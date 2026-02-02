import asyncio
import config
from logging import Logger
from analyzer import Analyzer
from hexbytes import HexBytes
from typing import List
import web3.exceptions
from binance.redis_client import RedisClient
from binance.web3client import VenusClient
from utils import usd_to_wei, calc_slippage

class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, analyzer: Analyzer, logger: Logger=None):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = analyzer
        self.incentive_rate = 1.1
        self._execution_lock = None
        self._vtoken_cache = None
        self._cooldown_cache = {}

    def set_vtoken_cache(self, vtoken_cache):
        self._vtoken_cache = vtoken_cache

    def set_execution_lock(self, lock):
        self._execution_lock = lock

    async def handle_liquidation(self, report, oracle_tx_hash: str=None):
        user_addr = report['user_address']
        if await self._db.should_skip(f"liquidator:skip:{user_addr}"):
            return

        user_profile = await self.analyzer.get_user_snapshot(user_addr)
        if not user_profile:
            await self._db.remove_user_profile(f"user_profile:{user_addr}")
            return

        prices = await self._client.get_oracle_price(list(user_profile.keys()))
        hf = self.analyzer.calculate_hf(user_profile, prices)
        asset = await self._client.get_user_liquidity([user_addr])
        error, liquidity, shortfall = asset[user_addr]

        if shortfall > 0:
            liq = await self.is_liquidation(user_addr, user_profile, prices, self.incentive_rate)
            if liq['is_profitable']:
                status = await self.execute_liquidation(
                    liq['pair_address'],
                    user_addr,
                    liq['repay_amount'],
                    liq['best_path'],
                    liq['best_debt'],
                    liq['best_collateral'],
                    liq['pay_redeem_amount'],
                    liq['min_profit'],
                    liq['net_profit'],
                    oracle_tx_hash
                )
                self.Log.info(f"liquidation status: {status}")

        await self._db.update_user_profile(f"user_profile:{user_addr}", user_profile)
        await self._db.update_user_hf_in_order("high_risk_queue", {user_addr: hf})

    async def get_slippage(self, pair_addr, v_address, amount):
        reserves, token0, token1 = await self._client.get_reserves(pair_addr)
        if v_address.lower() == token0.lower():
            r0, r1 = reserves[0], reserves[1]
            slippage = calc_slippage(amount, r0, r1)
        elif v_address.lower() == token1.lower():
            r1, r0 = reserves[1], reserves[0]
            slippage = calc_slippage(amount, r1, r0)
        else:
            return 1.0
        return slippage

    async def calc_pay_redeem_amount(self, repay_amount: int, path: List[str]) -> int:
        amount_required = (repay_amount * 10000) // 9975 + 1
        amounts_in = await self._client.get_amounts_in(amount_required, path)
        pay_redeem_amount = int(amounts_in[0] * 1.01)
        return pay_redeem_amount

    async def calc_best_path(self, debt_wbnb_pair_address, collateral_underlying_address, debt_underlying_address, repay_amount):
        best_path = []
        min_pay_redeem_amount = float('inf')
        pairs = await self._db.get_pairs(f"pair:{collateral_underlying_address}")
        for node, pair_address in pairs.items():
            if node == debt_underlying_address:
                path = [self._client.to_checksum_address(collateral_underlying_address),
                        self._client.to_checksum_address(debt_underlying_address)]
                try:
                    pay_redeem_amount = await self.calc_pay_redeem_amount(repay_amount, path)
                    if 0 < pay_redeem_amount < min_pay_redeem_amount:
                        min_pay_redeem_amount = pay_redeem_amount
                        best_path = path
                        break
                except web3.exceptions.ContractLogicError:
                    continue
            if pair_address != debt_wbnb_pair_address:
                if await self._db.exist_pair(f"pair:{node}", debt_underlying_address):
                    if await self._db.get_pair(f"pair:{node}", debt_underlying_address) != debt_wbnb_pair_address:
                        path = [self._client.to_checksum_address(collateral_underlying_address),
                                self._client.to_checksum_address(node),
                                self._client.to_checksum_address(debt_underlying_address)]
                        try:
                            pay_redeem_amount = await self.calc_pay_redeem_amount(repay_amount, path)
                            if 0 < pay_redeem_amount < min_pay_redeem_amount:
                                min_pay_redeem_amount = pay_redeem_amount
                                best_path = path
                        except web3.exceptions.ContractLogicError:
                            continue

        return best_path, min_pay_redeem_amount

    def find_best_liquidation_asset(self, user_profile, prices):
        """
        目标：
        1. 找到价值最大的债务 (Repay Debt) -> 提高单笔清算收益，节省 Gas。
        2. 找到 CF 因子最低或价值最大的抵押品 (Seize Collateral) -> 拿走价值最稳或对自己最有利的资产。
        """
        debts = []
        collaterals = []

        for v_addr, amount in user_profile.items():
            token = self._vtoken_cache.get(v_addr)
            if not token:
                return {}, {}

            current_price = prices[v_addr] / token['oracle_precision']
            value_usd = abs(amount) * current_price

            if amount < 0: # 债务
                debts.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value_usd,
                    "amount": abs(amount),
                    "underlying_decimal": token['underlying_decimal'],
                })
            elif amount > 0: # 抵押品
                collaterals.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value_usd,
                    "amount": amount,
                    "cf": token['cf'],
                    "underlying_decimal": token['underlying_decimal'],
                })

        # 排序：债务按价值从大到小
        best_debt = max(debts, key=lambda x: x['value'], default={})

        # 排序：抵押品优先选抵押因子大的
        best_collateral = max(collaterals, key=lambda x: x['cf'], default={})

        return best_debt, best_collateral

    @staticmethod
    def get_optimal_repay(best_debt, best_collateral, incentive_rate):
        # 1. 协议限制：只能还 50%
        debt_amount_limited = best_debt['amount'] * config.CLOSE_FACTOR
        debt_value_limited = best_debt['value'] * config.CLOSE_FACTOR
        # 2. 抵押品限制：不能超过抵押品能赔付的上限 (假设奖励 10%)
        collateral_amount_limited = best_collateral['amount'] / incentive_rate
        collateral_value_limited = best_collateral['value'] / incentive_rate

        repay_amount = min(debt_amount_limited, collateral_amount_limited)
        repay_usd = min(debt_value_limited, collateral_value_limited)
        return repay_amount, repay_usd

    async def is_liquidation(self, user_addr, user_profile, prices, incentive_rate=1.1):
        best_debt, best_collateral = self.find_best_liquidation_asset(user_profile, prices)
        if not best_debt or not best_collateral:
            return {"is_profitable": False, "best_debt": {}, "best_collateral": {}, "repay_amount": 0}

        repay_amount, repay_usd = self.get_optimal_repay(best_debt, best_collateral, incentive_rate)

        min_profit_wei = usd_to_wei(config.MIN_PROFIT_TOLERANCE,
                                    prices[best_collateral['v_addr']],
                                    best_collateral['underlying_decimal'])

        repay_amount_wei = usd_to_wei(repay_amount,
                                      prices[best_debt['v_addr']],
                                      best_debt['underlying_decimal'])

        # 1. 预估 gas 成本
        gas_price_wei = config.GAS_PRICE
        estimated_gas = 1000000  # 清算交易通常消耗较多 gas
        gas_cost_bnb = (gas_price_wei * estimated_gas) / 1e18
        bnb_price = prices.get(config.BNB_VTOKEN_ADDRESS, config.BNB_PRICE_DEFAULT)
        gas_cost_usd = gas_cost_bnb * bnb_price / 1e18

        if best_debt['underlying_address'] != config.WBNB_VTOKEN_UNDER_ADDRESS:
            pair_address = await self._db.get_pair(f"pair:{best_debt['underlying_address']}",
                                                   config.WBNB_VTOKEN_UNDER_ADDRESS)
        else:
            pair_address = await self._db.get_pair(f"pair:{best_debt['underlying_address']}",
                                                   config.USDT_VTOKEN_UNDER_ADDRESS)
        # 2. 滑点 + swap手续费（0.25%）成本
        best_path, pay_redeem_amount_wei = await self.calc_best_path(
                                                        pair_address,
                                                        best_collateral['underlying_address'],
                                                        best_debt['underlying_address'],
                                                        repay_amount_wei)
        repay_cost_usd = (pay_redeem_amount_wei * prices[best_collateral['v_addr']]) / 10**(18 + best_collateral['underlying_decimal'])

        # 3. 毛利润
        gross_profit_usd = repay_usd * (incentive_rate - 1)

        # 4. 净利润
        net_profit = gross_profit_usd - repay_cost_usd - gas_cost_usd

        self.Log.info(f"--- ⚖️ 用户 {user_addr} 清算决策报告 ---")
        self.Log.info(f"🔹 待清算金额:  ${repay_usd} USD")
        self.Log.info(f"💰 理论毛利:    ${gross_profit_usd} USD")
        self.Log.info(f"⛽ Gas 成本:   ${gas_cost_usd} USD (约 {gas_cost_bnb:.8f} BNB)")
        self.Log.info(f"💴 预计收益:    ${net_profit} USD")
        self.Log.info(f"----------------------")

        if net_profit < config.MIN_PROFIT_TOLERANCE:
            await self._db.mark_as_non_liquidable(f"liquidator:skip:{user_addr}", config.COOLDOWN_TTL_DAY, "low_profit")

        return {
            "is_profitable": net_profit >= config.MIN_PROFIT_TOLERANCE, # 利润大于 1 刀
            "repay_amount": repay_amount_wei,
            "pair_address": pair_address,
            "best_debt": best_debt,
            "best_collateral": best_collateral,
            "best_path": best_path,
            "pay_redeem_amount": pay_redeem_amount_wei,
            "min_profit": min_profit_wei,
            "net_profit": net_profit,
        }

    async def execute_liquidation(self,
                                  pair_address,
                                  user_address,
                                  repay_amount_wei,
                                  path,
                                  debt,
                                  collateral,
                                  pay_redeem_amount_wei,
                                  min_profit_wei,
                                  net_profit,
                                  oracle_tx_hash: str = None) -> bool:

        self.Log.info(f"负债: {debt['symbol']}, 抵押品: {collateral['symbol']}")

        try:
            self._client.simulate_liquidation_tx(
                pair_address,
                user_address,
                repay_amount_wei,
                debt['v_addr'],
                collateral['v_addr'],
                path,
                pay_redeem_amount_wei,
                min_profit_wei,
                debt['underlying_address'],
                collateral['underlying_address'],
            )
        except Exception as e:
            self.Log.error(f"⚠️ [模拟失败]: {e}")
            return False

        async with self._execution_lock:
            signed_tx = self._client.create_alpha_liquidation_tx(
                pair_address,
                user_address,
                repay_amount_wei,
                debt['v_addr'],
                collateral['v_addr'],
                path,
                pay_redeem_amount_wei,
                min_profit_wei,
                debt['underlying_address'],
                collateral['underlying_address'],
            )
            if net_profit > 50:
                if oracle_tx_hash:
                    await self._client.send_bundle_to_bloxroute(oracle_tx_hash, signed_tx.rawTransaction.hex())
                else:
                    try:
                        response = await self._client.send_private_transaction(signed_tx)
                        if "result" in response:
                            tx_hash = HexBytes(response["result"])
                        else:
                            self.Log.error(f"❌ 私有广播失败: {response}")
                            return False
                    except Exception as e:
                        self.Log.error(f"❌ 私有通道请求异常: {e}")
                        return False
            else:
                tx_hash = await self._client.send_raw_transaction(signed_tx.rawTransaction)
            self.Log.info(f"🚀 清算交易已发出！Hash: {tx_hash.hex()}")

        return await asyncio.create_task(self.check_receipt_status(tx_hash, user_address))

    async def check_receipt_status(self, tx_hash, user_address) -> bool:
        # 这里的 wait_for_transaction_receipt 建议设置 timeout
        receipt = await self._client.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt['status'] == 1:
            # 计算实际 Gas 消耗（BNB）
            gas_used = receipt['gasUsed']
            gas_price = receipt['effectiveGasPrice']
            actual_cost = (gas_used * gas_price) / 1e18
            self.Log.info(f"✅ 清算成功! 用户: {user_address} | 消耗 Gas: {actual_cost} BNB")
            return True
        else:
            self.Log.error(f"❌ 交易被回滚 (Reverted): {user_address} | Hash: {tx_hash.hex()}")
            return False