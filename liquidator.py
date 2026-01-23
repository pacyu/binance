import json
import config
from logging import Logger
from analyzer import Analyzer
from binance.redis_client import RedisClient
from binance.web3client import VenusClient
from utils import usd_to_wei

class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, logger: Logger):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = Analyzer(self._client, self._db, self.Log)
        self.incentive_rate = 1.1
        self._vtoken_cache = {}
        self._load_vtoken_cache()

    def _load_vtoken_cache(self):
        all_vtokens = self._db.get_markets('asset:v_addr')
        for item in all_vtokens:
            token = json.loads(item)
            self._vtoken_cache[token['address']] = token

    async def handle_liquidation(self, report):
        user_addr = report['user_address']
        user_profile = await self.analyzer.get_user_snapshot(user_addr)
        prices = await self._client.get_oracle_price(list(user_profile.keys()))
        hf = self.analyzer.calculate_hf(user_profile, prices)
        if 0.92 <= hf <= 0.97:
            liq = self.is_liquidation(user_profile, prices, self.incentive_rate)
            if liq['is_profitable']:
                self.execute_liquidation(
                    user_addr, liq['repay_amount'], liq['best_debt_symbol'], liq['best_collateral_symbol'], prices)
        self._db.update_user_profile(f"user_profile:{user_addr}", user_profile)
        self._db.update_user_hf_in_order("high_risk_queue", {user_addr: hf})

    def find_best_liquidation_asset(self, user_profile, prices):
        """
        目标：
        1. 找到价值最大的债务 (Repay Debt) -> 提高单笔清算收益，节省 Gas。
        2. 找到 CF 因子最低或价值最大的抵押品 (Seize Collateral) -> 拿走价值最稳或对自己最有利的资产。
        """
        debts = []
        collaterals = []

        for v_addr, balance in user_profile.items():
            token = self._vtoken_cache.get(v_addr)
            if not token:
                continue

            price = prices[v_addr] / token['oracle_precision']
            value_usd = abs(balance) * price

            if balance < 0:  # 债务
                debts.append({"v_addr": v_addr, "symbol": token['symbol'], "value": value_usd})
            elif balance > 0:  # 抵押品
                depth = token['liquidity']['dex_depth_score']
                impact = (value_usd / depth) if depth > 0 else 1.0

                collaterals.append({
                    "v_addr": v_addr,
                    "symbol": token['symbol'],
                    "value": value_usd,
                    "cf": token['cf'],
                    "dex_depth_score": depth,
                    "slippage_risk_impact": impact
                })

        # 排序：债务按价值从大到小
        best_debt = sorted(debts, key=lambda x: x['value'], reverse=True)[0]

        # 首先排除滑点过高的那些抵押品
        collaterals = [c for c in collaterals if c['slippage_risk_impact'] < config.MAX_SLIPPAGE_TOLERANCE]
        if not collaterals:
            return {}, {}

        # 排序：抵押品优先选安全范围内的（即避免跟大鳄竞争，同时保证滑点风险不会超出预期）
        best_collateral = sorted(collaterals, key=lambda x: x['dex_depth_score'])[0]

        return best_debt, best_collateral

    def is_liquidation(self, user_profile, prices, incentive_rate=1.1):
        # 1. 当前 Gas 价格 (BSC 约 1-3 gwei)
        gas_price_wei = self._client.get_gas_price()
        estimated_gas = 800000  # 清算交易通常消耗较多 gas
        gas_cost_bnb = (gas_price_wei * estimated_gas) / 1e18

        bnb_price = self._client.get_oracle_price([config.BNB_VTOKEN_ADDRESS])
        gas_cost_usd = gas_cost_bnb * bnb_price[config.BNB_VTOKEN_ADDRESS] / 1e18

        best_debt, best_collateral = self.find_best_liquidation_asset(user_profile, prices)
        if not best_debt or not best_collateral:
            return {"is_profitable": False}

        repay_amount_usd = self.get_optimal_repay_amount(best_debt['value'], best_collateral['value'], incentive_rate)

        # 滑点计算
        gross_reward_usd = repay_amount_usd * incentive_rate
        slippage_risk_impact = best_collateral['slippage_risk_impact']
        slippage_loss_usd = gross_reward_usd * slippage_risk_impact

        # 2. 计算收益
        gross_profit_usd = repay_amount_usd * (incentive_rate - 1)
        net_profit = gross_profit_usd - gas_cost_usd - slippage_loss_usd

        self.Log.info(f"--- ⚖️ 清算决策报告 ---")
        self.Log.info(f"🔹 待清算金额:  ${repay_amount_usd:.2f} USD")
        self.Log.info(f"💰 理论毛利:    ${gross_profit_usd:.2f} USD")
        self.Log.info(f"⛽ Gas 成本:   ${gas_cost_usd:.2f} USDT (约 {gas_cost_bnb:.8f} BNB)")
        self.Log.info(f"📉 预估滑点损耗: ${slippage_loss_usd:.2f} USD")
        self.Log.info(f"💴 预计收益:    ${net_profit:.2f} USD")
        self.Log.info(f"----------------------")

        return {
            "is_profitable": net_profit >= 2.0 and slippage_risk_impact < config.MAX_SLIPPAGE_TOLERANCE, # 利润大于 2 刀且滑点风险小于0.02才做
            "best_debt": best_debt,
            "best_collateral": best_collateral,
            "repay_amount": repay_amount_usd
        }

    @staticmethod
    def get_optimal_repay_amount(best_debt_val, best_collateral_val, incentive_rate):
        # 1. 协议限制：只能还 50%
        limit_by_protocol = best_debt_val * 0.5

        # 2. 抵押品限制：不能超过抵押品能赔付的上限 (假设奖励 10%)
        limit_by_collateral = best_collateral_val / incentive_rate

        # 3. 策略限制：比如账户里只有 50 USDT，或者不想在山寨币上冒险
        my_max_fund = 50.0

        # 4. 取最小值
        repay_usd = min(limit_by_protocol, limit_by_collateral, my_max_fund)

        return repay_usd

    def execute_liquidation(self, user_address, repay_amount, vtoken_debt, vtoken_collateral, prices):
        try:
            token = self._vtoken_cache[vtoken_debt['v_addr']]
            self.Log.info(f"代偿额度: {repay_amount}, 负债代币价格: {prices[vtoken_debt['v_addr']]}, {token}")
            repay_amount_wei = usd_to_wei(
                repay_amount,
                prices[vtoken_debt['v_addr']],
                token['oracle_precision'])

            try:
                result = self._client.simulate_liquidation_tx(
                    user_address,
                    repay_amount_wei,
                    True if vtoken_debt['symbol'] == 'BNB' else False,
                    vtoken_debt['v_addr'],
                    vtoken_collateral['v_addr'])
                if not result:
                    self.Log.error(f"模拟清算失败")
                    return
            except Exception as e:
                self.Log.error(f"模拟清算 Revert: {e}")
                return

            tx_hash =  self._client.send_liquidation_tx(
                user_address,
                repay_amount_wei,
                True if vtoken_debt['symbol'] == 'BNB' else False,
                vtoken_debt['v_addr'],
                vtoken_collateral['v_addr'])

            self.Log.info(f"🚀 清算交易已发出！Hash: {tx_hash.hex()}")

            receipt = self._client.wait_for_transaction_receipt(tx_hash)
            if receipt['status'] == 1:
                self.Log.info("✅ 清算成功！")
            else:
                self.Log.info("🛑 交易回滚 (Reverted)")

        except Exception as e:
            self.Log.error(f"⚠️ 执行异常: {e}")
