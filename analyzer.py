import json
from logging import Logger
from binance.redis_client import RedisClient
from binance.web3client import VenusClient


class Analyzer:
    def __init__(self, client: VenusClient, db: RedisClient, logger: Logger):
        self._client = client
        self._db = db
        self.Log = logger
        self._vtoken_cache = {}

    def _load_vtoken_cache(self):
        all_vtokens = self._db.get_markets('asset:v_addr')
        for item in all_vtokens:
            token = json.loads(item)
            self._vtoken_cache[token['address']] = token

    def calculate_hf(self, user_profile, prices):
        total_collateral_usd = 0
        total_debt_usd = 0
        # self.Log.info(f"用户资产: {user_profile}")
        # 从用户持仓细节中提取资产并乘以 prices 里的实时价
        for v_addr, amount in user_profile.items():
            if not prices.get(v_addr):
                break

            amount = float(amount)
            token = self._vtoken_cache[v_addr]
            current_price = prices[v_addr] / token['oracle_precision']
            usd_price = amount * current_price
            if amount > 0:
                total_collateral_usd += usd_price * token['cf']
            else:
                total_debt_usd += abs(usd_price)

        if total_debt_usd > 0:
            hf = total_collateral_usd / total_debt_usd
            # self.Log.info(f"用户健康度: {hf}")
            return hf
        # self.Log.info(f"用户健康度: inf")
        return float('inf')

    async def analyze_user(self, user_address, prices):
        user_profile = self._db.get_user_profile(f"user_profile:{user_address}")
        if not user_profile:
            user_profile = await self.get_user_snapshot(user_address)
            if not user_profile:
                return {
                    "user_address": '',
                    "health_factor": 0,
                    "is_liquidatable": False,
                }
            self._db.update_user_profile(f"user_profile:{user_address}", user_profile)

        hf = self.calculate_hf(user_profile, prices)
        if hf <= 1.3:
            self._db.update_user_hf_in_order("high_risk_queue", {user_address: hf})
        report = {
            "user_address": user_address,
            "health_factor": hf,
            "is_liquidatable": 0.9 <= hf < 1.105,
        }
        return report

    async def get_user_snapshot(self, user_address):
        results = await self._client.get_account_snapshot([user_address])
        user_profile = {}
        for addr, snapshot in results.items():
            user_addr, v_addr = addr.split('|')
            err, vtoken_bal, borrow_bal, exchange_rate = snapshot

            if err != 0:
                continue

            # 计算底层资产抵押数量 = vToken余额 * 兑换率 / 1e18
            collateral_underlying = (vtoken_bal * exchange_rate) / 1e18

            # 借款余额已经是底层资产单位了
            debt_underlying = borrow_bal

            # 计算净头寸 (带符号)
            # 这里假设只要有存款就视为抵押，实际上需要判断是否入库 (isListed)，但清算中通常直接取净值
            amount = (collateral_underlying - debt_underlying) / 1e18

            if abs(amount) > 1e-9:  # 过滤极小值
                user_profile[v_addr] = amount
            self._db.update_user_asset_map_list(f'asset:users:{v_addr}', user_address)
        return user_profile

    async def get_users_snapshot(self, user_address_list):
        results = await self._client.get_account_snapshot(user_address_list)
        user_profile = {}
        for addr, snapshot in results.items():
            user_addr, v_addr = addr.split('|')
            err, vtoken_bal, borrow_bal, exchange_rate = snapshot

            if err != 0:
                continue

            # 计算底层资产抵押数量 = vToken余额 * 兑换率 / 1e18
            collateral_underlying = (vtoken_bal * exchange_rate) / 1e18

            # 借款余额已经是底层资产单位了
            debt_underlying = borrow_bal

            # 计算净头寸 (带符号)
            # 这里假设只要有存款就视为抵押，实际上需要判断是否入库 (isListed)，但清算中通常直接取净值
            amount = (collateral_underlying - debt_underlying) / 1e18

            if abs(amount) > 1e-9:  # 过滤极小值
                if user_addr not in user_profile:
                    user_profile[user_addr] = {}
                else:
                    user_profile[user_addr][v_addr] = amount
            self._db.update_user_asset_map_list(f'asset:users:{v_addr}', user_addr)
        return user_profile

    async def analyze_liquidable_users(self, user_address_list):
        results = await self._client.get_user_liquidity(user_address_list)
        for user_address, (error, liquidity, shortfall) in results.items():
            shortfall_usd = shortfall / 10 ** 18
            liq_usd = liquidity / 10 ** 18

            if shortfall_usd > 0:
                self.Log.info(f"🚨 发现可清算目标: {user_address} | 欠费金额: ${shortfall_usd:.2f}")
            #     self._db.set(f"target:{user_address}", shortfall_usd)
            elif liq_usd < 500:  # 账户剩余额度不足 500 USD
                self.Log.info(f"⚠️ [高风险] 用户: {user_address} | 剩余额度: ${liq_usd:.2f}")
            #     self._db.set(f'user_profile:{user_address}', item)
            else:
                self.Log.info(f"✅ [安全] 用户: {user_address} | 剩余额度: ${liq_usd:.2f}")
            #     self._db.set(f"liquidity:{user_address}", liq_usd)
