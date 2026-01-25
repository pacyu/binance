import asyncio
import config
from logging import Logger
from analyzer import Analyzer
from hexbytes import HexBytes
from binance.redis_client import RedisClient
from binance.web3client import VenusClient
from utils import usd_to_wei, calc_slippage

class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, logger: Logger, analyzer: Analyzer):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = analyzer
        self.incentive_rate = 1.1
        self._execution_lock = None
        self._vtoken_cache = None

    def set_vtoken_cache(self, vtoken_cache):
        self._vtoken_cache = vtoken_cache

    def set_execution_lock(self, lock):
        self._execution_lock = lock

    async def handle_liquidation(self, report):
        user_addr = report['user_address']
        user_profile = await self.analyzer.get_user_snapshot(user_addr)
        if not user_profile:
            self._db.remove_user_profile(f"user_profile:{user_addr}")
            return
        prices = await self._client.get_oracle_price(list(user_profile.keys()))
        hf = self.analyzer.calculate_hf(user_profile, prices)
        if 0.6 < hf < 1.0:
            liq = await self.is_liquidation(user_addr, user_profile, prices, self.incentive_rate)
            if liq['is_profitable']:
                await self.execute_liquidation(
                    user_addr, liq['repay_amount'], liq['best_debt'], liq['best_collateral'], liq['net_profit'], prices)

        self._db.update_user_profile(f"user_profile:{user_addr}", user_profile)
        self._db.update_user_hf_in_order("high_risk_queue", {user_addr: hf})

    def get_slippage(self, pair_addr, v_address, amount):
        reserves, token0, token1 = self._client.get_reserves(pair_addr)
        if v_address.lower() == token0.lower():
            r0, r1 = reserves[0], reserves[1]
            slippage, amount_out = calc_slippage(amount, r0, r1)
        elif v_address.lower() == token1.lower():
            r1, r0 = reserves[1], reserves[0]
            slippage, amount_out = calc_slippage(amount, r1, r0)
        else:
            return 1.0, 0
        return slippage, amount_out

    def calc_dex_best_slippage(self, amount: int, v_address: str):
        """
        兑换为 token->USDT 或 token->WBNB->USDT
        """
        slippage = 0
        pair_addr = self._client.get_pair(v_address, config.USDT_VTOKEN_UNDER_ADDRESS)
        if pair_addr == '0x0000000000000000000000000000000000000000':
            for v_addr, s_addr in [(v_address, config.WBNB_VTOKEN_UNDER_ADDRESS),
                                  (config.WBNB_VTOKEN_UNDER_ADDRESS, config.USDT_VTOKEN_UNDER_ADDRESS)]:
                pair_addr = self._client.get_pair(v_addr, s_addr)
                slip, amount_out = self.get_slippage(pair_addr, v_address, amount)
                slippage += slip
            return slippage, amount_out
        slippage, amount_out = self.get_slippage(pair_addr, v_address, amount)
        return slippage, amount_out

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
                continue

            price = prices[v_addr] / token['oracle_precision']
            value_usd = abs(amount) * price

            if amount < 0: # 债务
                debts.append({"v_addr": v_addr, "symbol": token['symbol'], "value": value_usd, "amount": abs(amount)})
            elif amount > 0: # 抵押品
                collaterals.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value_usd,
                    "amount": abs(amount),
                    "cf": token['cf'],
                })

        # 排序：债务按价值从大到小
        best_debt = sorted(debts, key=lambda x: x['value'], reverse=True)[0]

        # 排序：抵押品优先选抵押因子大的
        best_collateral = sorted(collaterals, key=lambda x: x['cf'], reverse=True)[0]

        return best_debt, best_collateral

    @staticmethod
    def get_optimal_repay(best_debt, best_collateral, incentive_rate):
        # 1. 协议限制：只能还 50%
        debt_amount_limited = best_debt['amount'] * 0.5
        debt_value_limited = best_debt['value'] * 0.5
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

        # 1. 预估 gas 成本
        gas_price_wei = self._client.get_gas_price()
        estimated_gas = 1000000  # 清算交易通常消耗较多 gas
        gas_cost_bnb = (gas_price_wei * estimated_gas) / 1e18
        bnb_price = await self._client.get_oracle_price([config.BNB_VTOKEN_ADDRESS])
        gas_cost_usd = gas_cost_bnb * bnb_price[config.BNB_VTOKEN_ADDRESS] / 1e18

        # 2. 闪电贷成本=本金+手续费
        flash_loan_fee_rate = 0.9975
        flash_cost_usd = repay_usd / flash_loan_fee_rate

        # 3. 获得的抵押品总价值
        gross_reward_amount = repay_amount * incentive_rate

        # 4. 滑点
        slippage, gross_usd = self.calc_dex_best_slippage(gross_reward_amount, best_collateral['underlying_address'])
        slippage_loss_usd = gross_usd * slippage

        # 5. 毛利润
        gross_profit_usd = repay_usd * (incentive_rate - 1)

        # 6. 净利润
        net_profit = gross_usd - slippage_loss_usd - flash_cost_usd - gas_cost_usd

        self.Log.info(f"--- ⚖️ 用户 {user_addr} 清算决策报告 ---")
        # self.Log.info(f"👜 资产情况: {user_profile}")
        # self.Log.info(f"{best_debt}, {best_collateral}")
        self.Log.info(f"🔹 待清算金额:  ${repay_usd:.2f} USD")
        self.Log.info(f"💰 理论毛利:    ${gross_profit_usd:.2f} USD")
        self.Log.info(f"⛽ Gas 成本:   ${gas_cost_usd:.2f} USD (约 {gas_cost_bnb:.8f} BNB)")
        self.Log.info(f"📉 预估滑点损耗: ${slippage_loss_usd:.2f} USD")
        self.Log.info(f"💴 预计收益:    ${net_profit:.2f} USD")
        self.Log.info(f"----------------------")

        return {
            "is_profitable": net_profit >= 2.0 and slippage < config.MAX_SLIPPAGE_TOLERANCE, # 利润大于 2 刀且滑点风险小于0.02才做
            "best_debt": best_debt,
            "best_collateral": best_collateral,
            "repay_amount": repay_amount,
            "net_profit": net_profit
        }

    async def execute_liquidation(self, user_address, repay_amount, vtoken_debt, vtoken_collateral, net_profit, prices):
        try:
            debt_token = self._vtoken_cache[vtoken_debt['v_addr']]
            collateral_token = self._vtoken_cache[vtoken_collateral['v_addr']]
            self.Log.info(f"🎯 代偿数量: {repay_amount},"
                          f" 负债代币: {vtoken_debt['symbol'].upper()},"
                          f" 价格: {prices[vtoken_debt['v_addr']] / debt_token['oracle_precision']}")
            self.Log.info(f"🥩 抵押品代币: {vtoken_collateral['symbol'].upper()},"
                          f" 价格: {prices[vtoken_collateral['v_addr']] / collateral_token['oracle_precision']}")

            min_profit_wei = usd_to_wei(2.0, prices[vtoken_collateral['v_addr']], collateral_token['underlying_decimal'])

            repay_amount_wei = usd_to_wei(
                repay_amount,
                prices[vtoken_debt['v_addr']],
                debt_token['underlying_decimal'])

            pair_address = self._client.get_pair(debt_token['underlying_address'], collateral_token['underlying_address'])
            self.Log.info(f"负债token地址: {debt_token['underlying_address']}, 抵押品地址: {collateral_token['underlying_address']}")
            self.Log.info(f"交易对地址: {pair_address}")
            try:
                self._client.simulate_liquidation_tx(
                    pair_address,
                    user_address,
                    repay_amount_wei,
                    vtoken_debt['v_addr'],
                    vtoken_collateral['v_addr'],
                    min_profit_wei
                )
            except Exception as e:
                self.Log.error(f"⚠️ [模拟失败]: {e}")
                return

            async with self._execution_lock:
                signed_tx = self._client.send_alpha_liquidation_tx(
                    pair_address,
                    user_address,
                    repay_amount_wei,
                    vtoken_debt['v_addr'],
                    vtoken_collateral['v_addr'],
                    min_profit_wei
                )
                if net_profit > 50:
                    try:
                        response = self._client.send_private_transaction(signed_tx)
                        if "result" in response:
                            tx_hash = HexBytes(response["result"])
                        else:
                            self.Log.error(f"❌ 私有广播失败: {response}")
                            return
                    except Exception as e:
                        self.Log.error(f"❌ 私有通道请求异常: {e}")
                        return
                else:
                    tx_hash = self._client.send_raw_transaction(signed_tx)

                self.Log.info(f"🚀 清算交易已发出！Hash: {tx_hash.hex()}")
            asyncio.create_task(self.check_receipt_status(tx_hash, user_address))

        except Exception as e:
            self.Log.error(f"⚠️ [执行异常]: {e}")

    async def check_receipt_status(self, tx_hash, user_address):
        try:
            # 这里的 wait_for_transaction_receipt 建议设置 timeout
            receipt = await self._client.wait_for_transaction_receipt(tx_hash, timeout=60)

            if receipt['status'] == 1:
                # 计算实际 Gas 消耗（BNB）
                gas_used = receipt['gasUsed']
                gas_price = receipt['effectiveGasPrice']
                actual_cost = (gas_used * gas_price) / 1e18
                self.Log.info(f"✅ 清算成功! 用户: {user_address} | 消耗 Gas: {actual_cost:.6f} BNB")
            else:
                self.Log.error(f"❌ 交易被回滚 (Reverted): {user_address} | Hash: {tx_hash.hex()}")

        except Exception as e:
            self.Log.error(f"⚠️ 等待回执超时或出错: {e}")