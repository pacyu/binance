from redis_client import RedisClient
from web3client import VenusClient


class Analyzer:
    def __init__(self, client: VenusClient, db: RedisClient):
        self._client = client
        self._db = db
        self._vtoken_cache = None

    def set_vtoken_cache(self, vtoken_cache):
        self._vtoken_cache = vtoken_cache

    def get_vtoken_cache(self):
        return self._vtoken_cache

    def calculate_hf(self, user_profile: dict, prices: dict) -> float:
        total_collateral_usd = 0
        total_debt_usd = 0

        for v_addr, amount in user_profile.items():
            price = prices.get(v_addr)
            if not price:
                break

            amount = float(amount)
            token = self._vtoken_cache[v_addr]
            current_price = price / token['oracle_precision']
            usd_price = amount * current_price
            if amount > 0:
                total_collateral_usd += usd_price * token['cf']
            else:
                total_debt_usd += abs(usd_price)

        if total_debt_usd > 0:
            hf = total_collateral_usd / total_debt_usd
            return hf
        return float('inf')

    async def analyze_user(self, user_address: str, prices: dict) -> dict:
        user_profile = await self._db.get_user_profile(f"user_profile:{user_address}")
        if not user_profile:
            user_profile = await self.get_user_snapshot([user_address])
            if not user_profile or not user_profile[user_address]:
                return {
                    "user_address": '',
                    "health_factor": 0,
                    "is_liquidatable": False,
                }
            user_profile = user_profile[user_address]
            await self._db.update_user_profile(f"user_profile:{user_address}", user_profile)

        hf = self.calculate_hf(user_profile, prices)
        if hf <= 1.3:
            await self._db.update_user_hf_in_order("high_risk_queue", {user_address: hf})
        report = {
            "user_address": user_address,
            "health_factor": hf,
            "is_liquidatable": hf < 1.05,
        }
        return report

    async def analyze_users(self, user_address_list: list, prices: dict) -> list:
        user_profiles = await self.get_user_snapshot(user_address_list)
        risky_reports = []
        for user_address, user_profile in user_profiles.items():

            if not user_profile:
                continue

            hf = self.calculate_hf(user_profile, prices)
            if hf < 1.3:
                await self._db.update_user_hf_in_order('high_risk_queue', {user_address: hf})

            await self._db.update_user_profile(f"user_profile:{user_address}", user_profile)

            report = {
                "user_address": user_address,
                "health_factor": hf,
                "is_liquidatable": hf < 1.05,
                "user_profile": user_profile,
            }
            risky_reports.append(report)
        return risky_reports

    async def get_user_snapshot(self, user_address_list: list) -> dict:
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
            await self._db.update_user_asset_map_list(f'asset:users:{v_addr}', user_addr)
        return user_profile
