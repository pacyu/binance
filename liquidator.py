import json
from analyzer import Analyzer
from binance.logger import Logger
from binance.redis_client import RedisClient
from binance.web3client import VenusClient
from utils import get_realtime_price, usd_to_wei

class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, logger: Logger):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = Analyzer(self._client, self._db, self.Log)
        self.incentive_rate = 1.1
        self.prepare_environment()

    def prepare_environment(self):
        """
        初始化环境：包括无限授权和余额检查。
        """
        # 为了提速，我们可以并发检查，但顺序发送授权交易以避免 Nonce 冲突
        markets = self._db.get_markets('asset:v_addr')
        nonce = self._client.get_transaction_count()

        for market in markets:
            market = json.loads(market)
            if market['symbol'] == 'bnb':
                continue  # 原生 BNB 不需要 Approve

            try:
                tx_hash, new_nonce = self._client.ensure_unlimited_approval(
                    market['underlying_address'],
                    market['address'],
                    nonce
                )
                nonce = new_nonce  # 更新 Nonce 供下一个使用
                self.Log.info(f"⏳ 授权交易已发出 {market['symbol']}, Hash: {tx_hash.hex()}")
            except Exception as e:
                self.Log.error(f"授权失败: {e}")

    async def handle_liquidation(self, report):
        user_addr = report['user_address']
        user_profile = await self.analyzer.get_user_snapshot(user_addr)
        prices = await self._client.get_oracle_price(list(user_profile.keys()))
        hf = self.analyzer.calculate_hf(user_profile, prices)
        if 0.9 <= hf < 1.05:
            liq = self.is_liquidation(user_profile, prices, self.incentive_rate)
            if liq['is_profitable']:
                self.Log.info(liq)
                # self.execute_liquidation(
                #     user_addr, liq['repay_amount'], liq['best_debt_symbol'], liq['best_collateral_symbol'])
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
            token = json.loads(self._db.get_vtoken('asset:v_addr', v_addr))

            price = prices[v_addr] / token['oracle_precision']
            value_usd = abs(balance) * price

            if balance < 0:  # 债务
                debts.append({"symbol": token['symbol'], "value": value_usd})
            elif balance > 0:  # 抵押品
                collaterals.append({"symbol": token['symbol'], "value": value_usd, "cf": token['cf']})

        # 排序：债务按价值从大到小
        best_debt = sorted(debts, key=lambda x: x['value'], reverse=True)[0]
        # 排序：抵押品优先选价值大的（或者根据喜好选 CF 低的）
        best_collateral = sorted(collaterals, key=lambda x: x['value'], reverse=True)[0]

        return best_debt, best_collateral

    def is_liquidation(self, user_profile, prices, incentive_rate=1.1):
        # 1. 当前 Gas 价格 (BSC 约 1-3 gwei)
        gas_price_wei = self._client.get_gas_price()
        estimated_gas = 800000  # 清算交易通常消耗较多 gas
        gas_cost_bnb = (gas_price_wei * estimated_gas) / 1e18

        bnb_price = get_realtime_price('BNB')
        gas_cost_usd = gas_cost_bnb * bnb_price

        best_debt, best_collateral = self.find_best_liquidation_asset(user_profile, prices)

        repay_amount_usd = self.get_optimal_repay_amount(best_debt['value'], best_collateral['value'], incentive_rate)

        # 滑点计算
        gross_reward_usd = repay_amount_usd * incentive_rate
        slippage_loss_usd = gross_reward_usd * 0.003

        # 2. 计算收益
        gross_profit_usd = repay_amount_usd * (incentive_rate - 1)
        net_profit = gross_profit_usd - gas_cost_usd - slippage_loss_usd

        self.Log.info(f"--- ⚖️ 清算决策报告 ---")
        self.Log.info(f"🔹 待清算金额:  ${repay_amount_usd:.2f} USDT")
        self.Log.info(f"💰 理论毛利:    ${gross_profit_usd:.2f} USDT")
        self.Log.info(f"⛽ Gas 成本:   ${gas_cost_usd:.2f} USDT (约 {gas_cost_bnb:.8f} BNB)")
        self.Log.info(f"📉 预估滑点损耗: ${slippage_loss_usd:.2f} USDT")
        self.Log.info(f"💴 预计收益:    ${net_profit:.2f} USDT")
        self.Log.info(f"----------------------")

        return {
            "is_profitable": net_profit > 2.0,  # 利润大于 2 刀才做
            "best_debt_symbol": best_debt['symbol'],
            "best_collateral_symbol": best_collateral['symbol'],
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

    def execute_liquidation(self, user_address, repay_amount, prices, vtoken_debt_symbol, vtoken_collateral_symbol):
        try:
            token = json.loads(self._db.get_vtoken('asset:symbol', vtoken_debt_symbol))
            vtoken_debt_address = token['address']
            vtoken_collateral_address = self._db.get_vtoken('symbol_map', vtoken_collateral_symbol)
            repay_amount_wei = usd_to_wei(
                repay_amount,
                prices[vtoken_debt_address],
                token['oracle_precision'])

            tx_hash =  self._client.send_liquidation_tx(
                user_address,
                repay_amount_wei,
                True if vtoken_debt_symbol == 'BNB' else False,
                vtoken_debt_address,
                vtoken_collateral_address)

            self.Log.info(f"🚀 清算交易已发出！Hash: {tx_hash.hex()}")

            receipt = self._client.wait_for_transaction_receipt(tx_hash)
            if receipt['status'] == 1:
                self.Log.info("✅ 清算成功！")
            else:
                self.Log.info("🛑 交易回滚 (Reverted)")

        except Exception as e:
            print(f"⚠️ 执行异常: {e}")
