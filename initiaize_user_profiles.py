import config
import asyncio
from redis_client import RedisClient
from web3client import VenusClient
from analyzer import Analyzer


class Run:
    def __init__(self):
        self.redis = RedisClient()
        self.client = VenusClient(config.ALCHEMY_BSC_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
        self.analyzer = Analyzer(self.client, self.redis)

    async def _load_cache_(self):
        coins_info = await self.redis.get_markets()
        self.analyzer.set_vtoken_cache(coins_info)

    async def main(self):
        await self._load_cache_()
        user_addresses = list(await self.redis.get_user_wallets())
        print(f"本次扫描共 {len(user_addresses)} 个钱包地址..")

        prices = await self.client.get_oracle_price(list(await self.redis.get_all_currencies()))
        step = 500
        for i in range(0, len(user_addresses), step):
            user_profiles = await self.analyzer.get_user_snapshot(user_addresses[i:i + step])
            for user_address, user_profile in user_profiles.items():

                if not user_profile:
                    await self.redis.remove_user_profile(user_address)
                    continue

                hf = self.analyzer.calculate_hf(user_profile, prices)
                print(user_address, hf)
                if hf <= 1.3:
                    await self.redis.save_or_update_user_health_factor({user_address: hf})
                else:
                    await self.redis.remove_user_health_factor_by_wallet_address(user_address)

                await self.redis.update_user_profile(user_address, user_profile)

    def __call__(self):
        asyncio.run(self.main())


if __name__ == "__main__":
    Run()()