import os
import json
import time
import config
import asyncio
import websockets
from dotenv import load_dotenv
from logger import Logger
from redis_client import RedisClient
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from web3.exceptions import TransactionNotFound
from websockets.exceptions import ConnectionClosedError


class MonitorMemPool:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        bloxroute_auth_header = os.getenv('BLOXROUTE_AUTH_HEADER')

        self.Log = Logger('mempool.log')()
        self._client = VenusClient(config.ANKR_RPC_URL2,
                                   config.VENUS_CORE_COMPTROLLER_ADDR,
                                   private_key,
                                   bloxroute_api_key,
                                   bloxroute_auth_header)
        self._db = RedisClient()
        self.analyzer = Analyzer(self._client, self._db)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)

        self._task_queue = asyncio.Queue(maxsize=1000)
        self._semaphore = asyncio.Semaphore(30)

        self._prior_counter = 0

        self._vtoken_cache = {}
        self._pre_onchain_price ={}

        self._execution_lock = asyncio.Lock()

        self.analyzer.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_execution_lock(self._execution_lock)

    async def _load_vtoken_cache_(self):
        all_vtokens = await self._db.get_markets('asset:v_addr')
        for item in all_vtokens:
            token = json.loads(item)
            self._vtoken_cache[token['address']] = token

    async def _handle_oracle_update(self, task):
        tx_hash = task["tx_hash"]
        await self._process_transaction(tx_hash)

    async def _process_user(self, u_addr, prices, oracle_tx_hash: str = None):
        async with self._semaphore:
            risky_report = await self.analyzer.analyze_user(u_addr, prices)
            if risky_report['is_liquidatable']:
                await self.engine.handle_liquidation(risky_report, oracle_tx_hash)

    async def _check_opportunity(self, vtoken_addr, prices, oracle_tx_hash: str = None):
        user_address_list = list(await self._db.read_by_name(f'asset:users:{vtoken_addr}'))

        if not user_address_list:
            return

        tasks = [
            self._process_user(addr, prices, oracle_tx_hash)
            for addr in user_address_list
        ]
        await asyncio.gather(*tasks)

    async def _process_transaction(self, tx_hash):
        try:
            tx = await self._client.get_transaction(tx_hash)
            oracle_address = tx['to']
            if oracle_address in config.ORACLE_WATCHLIST:
                self.Log.debug(">>>> 地址:", oracle_address)
                self.Log.debug(">>>> input:", tx['input'].hex())
            #     for v_addr in result:
            #         await self._check_opportunity(v_addr, self._pre_onchain_price, tx_hash)
        except TransactionNotFound:
            return

    async def listen_mempool(self):
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": ["newPendingTransactions"]
        }
        while True:
            try:
                async with websockets.connect(
                        config.BSC_WSS_URI, ping_timeout=120, ping_interval=5, close_timeout=5) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    msg = json.loads(await ws.recv())
                    self.Log.info(f"成功订阅 Mempool, SubID: {msg["result"]}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            tx_hash = data['params']['result']
                            try:
                                await self._task_queue.put((0, self._prior_counter, {
                                    "type": "oracle_update",
                                    "tx_hash": tx_hash,
                                    "ts": time.time()
                                }))
                                self._prior_counter += 1
                            except asyncio.QueueFull:
                                self.Log.warning("任务队列达到上限!")
                        except KeyError as e:
                            self.Log.error(f"消息错误: {e}")

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听公共池-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")

    async def listen_worker(self):
        while True:
            prior, counter, task = await self._task_queue.get()

            try:
                if task["type"] == "oracle_update":
                    await self._handle_oracle_update(task)

            except Exception as e:
                self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 任务: {task}")

            finally:
                self._task_queue.task_done()

    async def run(self):
        await self._load_vtoken_cache_()

        await asyncio.gather(
            self.listen_mempool(),
            self.listen_worker()
        )

    def __call__(self, *args, **kwargs):
        asyncio.run(self.run())


if __name__ == "__main__":
    monitor = MonitorMemPool()
    monitor()