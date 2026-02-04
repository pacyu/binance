import os
import json
import config
import asyncio
from logger import Logger
from dotenv import load_dotenv
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from redis_client import RedisClient


class MonitorUsers:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        bloxroute_auth_header = os.getenv('BLOXROUTE_AUTH_HEADER')

        self._db = RedisClient()
        self._client = VenusClient(config.ANKR_RPC_URL2,
                                   config.VENUS_CORE_COMPTROLLER_ADDR,
                                   private_key,
                                   bloxroute_api_key,
                                   bloxroute_auth_header)
        self.Log = Logger('scan_users.log')()

        self._vtoken_cache = {}

        self.analyzer = Analyzer(self._client, self._db)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)

        self._execution_lock = asyncio.Lock()

        self.analyzer.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_execution_lock(self._execution_lock)

    async def _load_vtoken_cache_(self):
        all_vtokens = await self._db.get_markets('asset:v_addr')
        for item in all_vtokens:
            token = json.loads(item)
            self._vtoken_cache[token['address']] = token

    async def _process_users(self, user_address_list, prices):
        try:
            await self.engine.handle_multi_liquidation(user_address_list, prices)
        except Exception as e:
            self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}")

    async def full_scan(self):
        user_address_list = list(await self._db.read_by_name('user_address_tab'))

        if not user_address_list:
            return

        prices = await self._client.get_oracle_price(list(self._vtoken_cache.keys()))

        batch_size = 100

        tasks = [
            self._process_users(user_address_list[i: i + batch_size], prices)
            for i in range(0, len(user_address_list), batch_size)
        ]

        await asyncio.gather(*tasks)

    async def run(self):
        await self._load_vtoken_cache_()

        self.Log.info(f"全量扫描任务开始，该任务每小时执行一次...")
        await self.full_scan()
        self.Log.info(f"全量扫描完成!")

    def __call__(self):
        asyncio.run(self.run())


if __name__ == '__main__':
    run = MonitorUsers()
    run()