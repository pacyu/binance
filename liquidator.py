import asyncio
import config
from logging import Logger
from analyzer import Analyzer
from hexbytes import HexBytes
from typing import Tuple
import web3.exceptions
from redis_client import RedisClient
from web3client import VenusClient
from utils import usd_to_wei, calc_slippage


class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, analyzer: Analyzer, logger: Logger = None):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = analyzer
        self.incentive_rate = 1100000000000000000
        self._execution_lock = None
        self._vtoken_cache = None
        self._cooldown_cache = {}

    def set_vtoken_cache(self, vtoken_cache):
        self._vtoken_cache = vtoken_cache

    def set_execution_lock(self, lock):
        self._execution_lock = lock

    async def _handle_helper(self, user_address: str, user_profile: dict, assets: dict, prices: dict,
                             health_factor: float, oracle_tx_hash: str = None):
        self.Log.info(f"正在处理用户: {user_address}")
        error, liquidity, shortfall = assets[user_address]

        if shortfall > 0:
            liquidation_report = await self.is_liquidation(user_address, user_profile, prices, self.incentive_rate)
            if liquidation_report['is_profitable']:
                status = await self.execute_liquidation(user_address, liquidation_report, oracle_tx_hash)
                self.Log.info(f"用户: {user_address} | 清算结果状态: {status}")
            else:
                self.Log.info(f"用户: {user_address} 不值得清算! | 用户资产: {user_profile} | 健康度: {health_factor}")
        else:
            self.Log.info(f"用户无法被清算! 健康度: {health_factor} | 账户流动性:{liquidity} | 账户缺口: {shortfall}")

        await self._db.update_user_profile(f"user_profile:{user_address}", user_profile)
        await self._db.update_user_hf_in_order("high_risk_queue", {user_address: health_factor})

    async def handle_multi_liquidation(self, user_address_list, prices):
        risky_reports = await self.analyzer.analyze_users(user_address_list, prices)
        assets = await self._client.get_user_liquidity(user_address_list)
        for risky_report in risky_reports:
            user_address = risky_report['user_address']
            hf = risky_report['health_factor']
            user_profile = risky_report['user_profile']
            await self._handle_helper(user_address, user_profile, assets, prices, hf)

    async def handle_liquidation(self, report, oracle_tx_hash: str = None):
        user_address = report['user_address']

        if await self._db.should_skip(f"liquidator:skip:{user_address}"):
            return

        user_profile = await self.analyzer.get_user_snapshot([user_address])

        if not user_profile:
            return

        if not user_profile[user_address]:
            await self._db.remove_user_profile(f"user_profile:{user_address}")
            return

        prices = await self._client.get_oracle_price(list(user_profile[user_address].keys()))
        hf = self.analyzer.calculate_hf(user_profile[user_address], prices)
        assets = await self._client.get_user_liquidity([user_address])
        await self._handle_helper(user_address, user_profile[user_address], assets, prices, hf, oracle_tx_hash)

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

    @staticmethod
    def calc_received_collateral(repay_amount_wei, best_debt, best_collateral, prices, incentive_rate):
        price_debt = prices[best_debt['v_addr']]
        price_collateral = prices[best_collateral['v_addr']]

        numerator = repay_amount_wei * price_debt * incentive_rate
        denominator = price_collateral * 10**18
        received_collateral_wei = numerator // denominator
        return int(received_collateral_wei)

    async def calc_transform_collateral_to_usdt_best_path(self,
                                                     debt_wbnb_pair_address: str,
                                                     collateral_underlying_address: str,
                                                     collateral_amount: int) -> Tuple:
        """

        Args:
            debt_wbnb_pair_address: 交易对地址
            collateral_underlying_address: 抵押代币地址
            collateral_amount: 抵押品数量

        Returns: 最优路径，将抵押品换为 USDT，最多可以得到多少 USDT
        """
        if collateral_underlying_address == config.USDT_UNDER_ADDRESS:
            return [], collateral_amount

        best_path = []
        max_amount = 0
        pairs = await self._db.get_pairs(f"pair:{collateral_underlying_address}")
        if config.USDT_UNDER_ADDRESS in pairs:
            pair_addr = pairs[config.USDT_UNDER_ADDRESS]
            if pair_addr != debt_wbnb_pair_address:
                path = [self._client.to_checksum_address(collateral_underlying_address),
                        self._client.to_checksum_address(config.USDT_UNDER_ADDRESS)]
                try:
                    amounts = await self._client.get_amounts_out(collateral_amount, path)
                    if max_amount < amounts[-1]:
                        max_amount = amounts[-1]
                        best_path = path
                except web3.exceptions.ContractLogicError:
                    pass
        for node, pair_address in pairs.items():
            if pair_address != debt_wbnb_pair_address:
                if await self._db.exist_pair(f"pair:{node}", config.USDT_UNDER_ADDRESS):
                    if await self._db.get_pair(f"pair:{node}", config.USDT_UNDER_ADDRESS) != debt_wbnb_pair_address:
                        path = [self._client.to_checksum_address(collateral_underlying_address),
                                self._client.to_checksum_address(node),
                                self._client.to_checksum_address(config.USDT_UNDER_ADDRESS)]
                        try:
                            amounts = await self._client.get_amounts_out(collateral_amount, path)
                            if max_amount < amounts[-1]:
                                max_amount = amounts[-1]
                                best_path = path
                        except web3.exceptions.ContractLogicError:
                            continue

        return best_path, max_amount

    async def calc_repay_flash_loan_best_path(self,
                                              debt_wbnb_pair_address: str,
                                              collateral_underlying_address: str,
                                              debt_underlying_address: str,
                                              repay_amount: int) -> Tuple:
        """
        从闪电贷借出债务数量，帮负债人偿还后，会得到抵押品，然后我需要还闪电贷池子的债务。
        该方法的逻辑：当我要还掉借来的闪电贷时，根据我最少支付多少抵押品去还闪电贷，找到最优路径，并且绕过池子的重入锁。
        Args:
            debt_wbnb_pair_address: 交易对地址
            collateral_underlying_address: 抵押代币地址
            debt_underlying_address: 债务代币地址
            repay_amount: 偿还的债务数量（也是我从闪电贷借的债务数量）

        Returns: 最优路径，最少要支付多少抵押品
        """
        best_path = []
        min_pay_redeem_amount = float('inf')
        pairs = await self._db.get_pairs(f"pair:{collateral_underlying_address}")

        if debt_underlying_address in pairs:
            pair_addr = pairs[debt_underlying_address]
            if pair_addr != debt_wbnb_pair_address:
                path = [self._client.to_checksum_address(collateral_underlying_address),
                        self._client.to_checksum_address(debt_underlying_address)]
                try:
                    amounts = await self._client.get_amounts_in(repay_amount, path)
                    if 0 < amounts[0]:
                        min_pay_redeem_amount = amounts[0]
                        best_path = path
                except web3.exceptions.ContractLogicError:
                    pass

        for node, pair_address in pairs.items():
            if pair_address != debt_wbnb_pair_address:
                if await self._db.exist_pair(f"pair:{node}", debt_underlying_address):
                    if await self._db.get_pair(f"pair:{node}", debt_underlying_address) != debt_wbnb_pair_address:
                        path = [self._client.to_checksum_address(collateral_underlying_address),
                                self._client.to_checksum_address(node),
                                self._client.to_checksum_address(debt_underlying_address)]
                        try:
                            amounts = await self._client.get_amounts_in(repay_amount, path)
                            if 0 < amounts[0] < min_pay_redeem_amount:
                                min_pay_redeem_amount = amounts[0]
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

            current_price = prices[v_addr]
            value = abs(amount) * current_price

            if amount < 0:  # 债务
                debts.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value,
                    "amount": abs(amount),
                    "underlying_decimal": token['underlying_decimal'],
                })
            elif amount > 0:  # 抵押品
                collaterals.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value,
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
        debt_amount_limited = int(best_debt['amount'] * config.CLOSE_FACTOR)

        # 2. 抵押品限制：不能超过抵押品能赔付的上限 (假设奖励 10%)
        collateral_amount_limited = int(best_collateral['amount'] * (incentive_rate // 10**18))

        repay_amount = min(debt_amount_limited, collateral_amount_limited)

        return repay_amount

    async def is_liquidation(self, user_addr: str, user_profile: dict, prices: dict, incentive_rate) -> dict:
        best_debt, best_collateral = self.find_best_liquidation_asset(user_profile, prices)
        if not best_debt or not best_collateral:
            return {"is_profitable": False, "best_debt": {}, "best_collateral": {}, "repay_amount": 0}

        repay_amount_wei = self.get_optimal_repay(best_debt, best_collateral, incentive_rate)
        repay_usd = repay_amount_wei * prices[best_debt['v_addr']] / (10 ** (18 + best_debt['underlying_decimal']))

        min_profit_wei = usd_to_wei(config.MIN_PROFIT_TOLERANCE,
                                    prices[config.USDT_ADDRESS],
                                    18)



        if best_debt['underlying_address'] != config.WBNB_UNDER_ADDRESS:
            pair_address = await self._db.get_pair(f"pair:{best_debt['underlying_address']}",
                                                   config.WBNB_UNDER_ADDRESS)
        else:
            pair_address = await self._db.get_pair(f"pair:{best_debt['underlying_address']}",
                                                   config.USDT_UNDER_ADDRESS)

        # 1. 预估 gas 成本
        gas_price_wei = config.GAS_PRICE
        estimated_gas = 1000000  # 清算交易通常消耗较多 gas
        gas_cost_bnb = (gas_price_wei * estimated_gas) / 1e18
        bnb_price = prices.get(config.BNB_ADDRESS, config.BNB_PRICE_DEFAULT)
        gas_cost_usd = gas_cost_bnb * bnb_price / 1e18

        # 2. 我能得到的总抵押品数量
        received_collateral_amount_wei = self.calc_received_collateral(
            repay_amount_wei, best_debt, best_collateral, prices, incentive_rate)

        # 3. 计算还掉闪电贷的最优路径与债务 + 滑点 + swap手续费（0.25%）成本
        best_path, pay_collateral_amount_wei = await self.calc_repay_flash_loan_best_path(
            pair_address,
            best_collateral['underlying_address'],
            best_debt['underlying_address'],
            repay_amount_wei)

        # 4. 剩余抵押品数量 = 总抵押品数量 - 用于支付偿还闪电贷的抵押品数量
        rest_collateral_amount_wei = received_collateral_amount_wei - pay_collateral_amount_wei
        if rest_collateral_amount_wei <= 0:
            return {"is_profitable": False, "best_debt": {}, "best_collateral": {}, "repay_amount": 0}

        # 5. 计算将剩余的抵押品换为 USDT 这样的稳定币的最优路径，以及换成 USDT 后，我最多能得到多少 USDT
        path, gross_profit_amount = await self.calc_transform_collateral_to_usdt_best_path(
            pair_address,
            best_collateral['underlying_address'],
            rest_collateral_amount_wei)

        # 6. 毛利润
        gross_profit_usd = ((gross_profit_amount * prices[config.USDT_ADDRESS]) / 10 ** 36)

        # 7. 净利润
        net_profit = gross_profit_usd - gas_cost_usd

        if net_profit < config.MIN_PROFIT_TOLERANCE:
            await self._db.mark_as_non_liquidable(f"liquidator:skip:{user_addr}",
                                                  config.COOLDOWN_TTL_HOUR,
                                                  f"low_profit: {net_profit} USD")
        else:
            self.Log.info(f"--- ⚖️ 用户 {user_addr} 清算决策报告 ---\n"
                          f"🔹 待清算金额:  ${repay_usd} USD\n"
                          f"💰 理论毛利:    ${gross_profit_usd} USD\n"
                          f"⛽ Gas 成本:   ${gas_cost_usd} USD (约 {gas_cost_bnb:.8f} BNB)\n"
                          f"💴 预计收益:    ${net_profit} USD\n"
                          f"----------------------")

        liquidation_report = {
            "is_profitable": net_profit >= config.MIN_PROFIT_TOLERANCE,  # 利润大于 1 刀
            "repay_amount": repay_amount_wei,
            "pair_address": pair_address,
            "best_debt": best_debt,
            "best_collateral": best_collateral,
            "best_path": best_path,
            "pay_collateral_amount": pay_collateral_amount_wei,
            "min_profit": min_profit_wei,
            "net_profit": net_profit,
        }
        return liquidation_report

    async def execute_liquidation(self, user_address: str, liquidation_report: dict,
                                  oracle_tx_hash: str = None) -> bool:
        pair_address = liquidation_report['pair_address']
        repay_amount_wei = liquidation_report['repay_amount']
        path = liquidation_report['best_path']
        debt = liquidation_report['best_debt']
        collateral = liquidation_report['best_collateral']
        pay_collateral_amount_wei = liquidation_report['pay_collateral_amount']
        min_profit_wei = liquidation_report['min_profit']
        net_profit = liquidation_report['net_profit']

        self.Log.info(f"用户 {user_address}, 负债: {debt['symbol']}, 抵押品: {collateral['symbol']}")

        try:
            self._client.simulate_liquidation_tx(
                pair_address,
                user_address,
                repay_amount_wei,
                debt['v_addr'],
                collateral['v_addr'],
                path,
                pay_collateral_amount_wei,
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
                pay_collateral_amount_wei,
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
