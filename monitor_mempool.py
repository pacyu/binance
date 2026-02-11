import os
import json
import time
import config
import asyncio
import websockets
from eth_abi import abi
from dotenv import load_dotenv
from logger import Logger
from redis_client import RedisClient
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from web3.exceptions import TransactionNotFound
from utils import extract_payload, extract_method_id, parse_prices
from websockets.exceptions import ConnectionClosedError


class MonitorMemPool:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        bloxroute_auth_header = os.getenv('BLOXROUTE_AUTH_HEADER')

        self.Log = Logger('mempool.log')()
        self._client = VenusClient(config.QUICKNODE_RPC_URL,
                                   config.VENUS_CORE_COMPTROLLER_ADDR,
                                   private_key,
                                   bloxroute_api_key,
                                   bloxroute_auth_header)
        self._db = RedisClient()
        self.analyzer = Analyzer(self._client, self._db)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)

        self._task_queue = asyncio.Queue(maxsize=1000)
        self._semaphore = asyncio.Semaphore(60)

        self._prior_counter = 0

        self._process_func = {'6fadcf72': self._process_forward, 'b1dc65a4': self._process_transmit}
        self._vtoken_cache = {}
        self._pre_onchain_price ={}
        self._digests_mapping = {}

        self._execution_lock = asyncio.Lock()

        self.analyzer.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_execution_lock(self._execution_lock)

    async def _load_vtoken_cache_(self):
        self._vtoken_cache = await self._db.get_markets()

    async def _load__digests_map_cache_(self):
        self._digests_mapping = await self._db.get_all_digests()

    async def _init_price_(self):
        self._pre_onchain_price = await self._client.get_oracle_price(list(self._vtoken_cache.keys()))

    @staticmethod
    def _process_forward(data: str):
        data_bytes = bytes.fromhex(data)
        decoded = abi.decode(['address', 'bytes'], data_bytes)
        return decoded

    @staticmethod
    def _process_transmit(data: str):
        data_bytes = bytes.fromhex(data)
        decoded = abi.decode(
            ['bytes32[3]', 'bytes', 'bytes32[]', 'bytes32[]', 'bytes32'],
            data_bytes
        )
        return decoded

    async def _handle_oracle_update(self, task):
        tx_hash = task["tx_hash"]
        await self._process_transaction(tx_hash)

    async def _process_user(self, u_addr, prices, oracle_tx_hash: str = None):
        async with self._semaphore:
            risky_report = await self.analyzer.analyze_user(u_addr, prices)
            if risky_report['is_liquidatable']:
                self.Log.info(f"⚠️ 用户 {u_addr} 资产进入警戒线，立即触发清算!")
                await self.engine.handle_liquidation(risky_report, oracle_tx_hash)

    async def _check_opportunity(self, vtoken_addr, prices, oracle_tx_hash: str = None):
        user_address_list = list(await self._db.get_holder_by_currency(vtoken_addr))

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
            data = tx['input'].hex()
            method_id = extract_method_id(data)
            payload = extract_payload(data)
            if method_id in self._process_func:
                decoded = self._process_func[method_id](payload)
                if method_id == '6fadcf72':
                    decoded = self._process_transmit(decoded[1])

                digest = decoded[0][0].hex()
                if digest in self._digests_mapping:
                    digest_config = self._digests_mapping[digest]
                    # 解析价格
                    report = decoded[1]
                    prices = parse_prices(report)
                    final_price = sorted(prices)[len(prices) // 2]
                    decimals = digest_config['decimals']
                    price = final_price * (10 ** (18 - decimals)) # 将价格放缩为18位精度（wei)

                    symbol = digest_config['symbol']
                    vtoken_address = digest_config['v_address']
                    last_price = self._pre_onchain_price[vtoken_address]
                    self._pre_onchain_price[vtoken_address] = price
                    deviation = price - last_price / price
                    self.Log.info(f"🔍 发现代币 {symbol} 价格即将更新! 交易价格变化: {last_price} -> {price} | 波动偏差: {abs(deviation) * 100:.f}%")
                    await self._check_opportunity(vtoken_address, self._pre_onchain_price, tx_hash)
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
        await self._init_price_()
        await self._load_vtoken_cache_()
        await self._load__digests_map_cache_()

        await asyncio.gather(
            self.listen_mempool(),
            self.listen_worker()
        )

    def __call__(self, *args, **kwargs):
        asyncio.run(self.run())


if __name__ == "__main__":
    monitor = MonitorMemPool()
    monitor()