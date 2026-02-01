import os
import json
import time
import asyncio
import config
import websockets
from eth_abi import abi
from logger import Logger
from dotenv import load_dotenv
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from redis_client import RedisClient
from web3.exceptions import LogTopicError
from websockets.exceptions import ConnectionClosedError
from utils import price_to_wei, get_realtime_prices, get_price_volatility_threshold

class Run:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        bloxroute_auth_header = os.getenv('BLOXROUTE_AUTH_HEADER')
        self._db = RedisClient()
        self._client = VenusClient(config.ANKR_RPC_URL,
                                   config.VENUS_CORE_COMPTROLLER_ADDR,
                                   private_key,
                                   bloxroute_api_key,
                                   bloxroute_auth_header)
        self.Log = Logger()()

        self._vtoken_cache = {}
        self._binance_price = {}
        self._onchain_price = {}

        self.analyzer = Analyzer(self._client, self._db, self.Log)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)
        self._event = None
        self._task_queue = asyncio.PriorityQueue(maxsize=2000)
        self._pending_users = set()
        self._prior_counter = {'user_event': 0, 'price_update': 0, 'oracle_update': 0}

        self._execution_lock = asyncio.Lock()

        self.analyzer.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_execution_lock(self._execution_lock)

    async def _load_vtoken_cache_(self):
        all_vtokens = await self._db.get_markets('asset:v_addr')
        for item in all_vtokens:
            token = json.loads(item)
            self._vtoken_cache[token['address']] = token

    async def _init_price_(self):
        for item in get_realtime_prices():
            if item['symbol'].endswith('USDT'):
                symbol = item['symbol'].replace('USDT', '').lower()
                if symbol == 'btc':
                    symbol = 'btcb'
                vtoken_addr = await self._db.get_vtoken('asset:symbol_map', symbol)
                if vtoken_addr:
                    self._binance_price[vtoken_addr] = price_to_wei(item['price'])
        self._binance_price['0xfd5840cd36d94d7229439859c0112a4185bc0255'] = 1e18
        self._onchain_price = await self._client.get_oracle_price(await self._db.get_all_tokens())

    def __call__(self):
        asyncio.run(self.main())

    async def listen_worker(self):
        while True:
            prior, counter, task = await self._task_queue.get()

            try:
                if task["type"] == "user_event":
                    asyncio.create_task(self._handle_user_event(task))

                elif task["type"] == "price_update":
                    asyncio.create_task(self._handle_price_update(task))

                elif task["type"] == "oracle_update":
                    asyncio.create_task(self._handle_oracle_update(task))

            except Exception as e:
                self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 任务: {task}")

            finally:
                self._task_queue.task_done()

    async def _handle_user_event(self, task):
        user_addr = task["address"]
        await self._process_and_analyze(user_addr)
        self._pending_users.remove(user_addr)

    async def _handle_price_update(self, task):
        vtoken_addr = task["address"]
        await self._check_opportunity(vtoken_addr, self._binance_price)

    async def _handle_oracle_update(self, task):
        tx_hash = task["tx_hash"]
        await self._process_transaction(tx_hash)

    async def _process_user(self, u_addr, prices, oracle_tx_hash: str= None):
        risky_report = await self.analyzer.analyze_user(u_addr, prices)
        if risky_report['is_liquidatable']:
            await self.engine.handle_liquidation(risky_report, oracle_tx_hash)

    async def _check_opportunity(self, vtoken_addr, prices, oracle_tx_hash: str=None):
        user_address_list = list(await self._db.read_by_name(f'asset:users:{vtoken_addr}'))
        
        if not user_address_list:
            return

        batch_size = 20
        for i in range(0, len(user_address_list), batch_size):
            batch = user_address_list[i: i + batch_size]
            await asyncio.gather(*(self._process_user(addr, prices, oracle_tx_hash) for addr in batch))

    async def _process_and_analyze(self, user_addr):
        risky_report = await self.analyzer.analyze_user(user_addr.lower(), self._binance_price)
        self.Log.info(f"用户事件触发: {user_addr}")
        self.Log.info(f"分析报告: {risky_report}")
        if risky_report['is_liquidatable']:
            await self.engine.handle_liquidation(risky_report)

    async def _process_transaction(self, tx_hash):
        try:
            tx = await self._client.get_transaction(tx_hash)
            oracle_address = tx['to']
            if await self._db.exist_oracle_source(f"oracle:address:{oracle_address}"):
                result = await self._db.get_oracle_source(f"oracle:address:{oracle_address}")
                for v_addr in result:
                    await self._check_opportunity(v_addr, self._onchain_price, tx_hash)
        except Exception as e:
            self.Log.debug(f"未找到交易 hash: {e}")

    def _process_events_log(self, log):
        vtoken_addr = log['address']
        topic = log['topics'][0]

        if topic == config.TOPICS['Borrow']:
            # token = self._vtoken_cache[vtoken_addr.lower()]
            # if not token:
            #     token = self._client.get_vtoken(vtoken_addr)
            #     self._db.update_venus_vtoken('asset:symbol', token['symbol'].lower(), json.dumps(token))
            #     self._db.update_venus_vtoken('asset:v_addr', vtoken_addr.lower(), json.dumps(token))

            borrow_event = self._event.Borrow()
            decoded = borrow_event.process_log(log)
            user_addr = decoded['args']['borrower']
            if user_addr in config.BLACKLIST:
                return None
            borrow_amount = decoded['args']['borrowAmount'] / 1e18
            account_borrows = decoded['args']['accountBorrows'] / 1e18
            total_borrows = decoded['args']['totalBorrows'] / 1e18
            self.Log.info(f"🔥 检测到用户借款事件! 合约地址: {vtoken_addr} | 借款人: {user_addr}"
                          f" | 借款金额: {borrow_amount} | 借款人总债务: {account_borrows}"
                          f" | 市场总债务: {total_borrows} | transactionHash: {log['transactionHash']}")
            return 1, user_addr

        elif topic == config.TOPICS['Redeem']:
            redeem_event = self._event.Redeem()
            decoded = redeem_event.process_log(log)
            user_addr = decoded['args']['redeemer']
            if user_addr in config.BLACKLIST:
                return None
            redeem_amount = decoded['args']['redeemAmount'] / 1e18
            redeem_tokens = decoded['args']['redeemTokens'] / 1e18
            self.Log.info(f"🔥 检测到用户赎回事件! 合约地址: {vtoken_addr} | 赎回者: {user_addr}"
                           f" | 赎回资产数量: {redeem_amount} | 销毁vToken数量: {redeem_tokens}"
                           f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        elif topic == config.TOPICS['RepayBorrow']:
            repay_borrow_event = self._event.RepayBorrow()
            decoded = repay_borrow_event.process_log(log)
            payer_addr = decoded['args']['payer']
            user_addr = decoded['args']['borrower']
            if user_addr in config.BLACKLIST:
                return None
            repay_amount = decoded['args']['repayAmount'] / 1e18
            account_borrows_new = decoded['args']['accountBorrowsNew'] / 1e18
            total_borrows_new = decoded['args']['totalBorrowsNew'] / 1e18
            self.Log.info(f"🔥 检测到用户还款事件! 合约地址: {vtoken_addr} | 还款人: {payer_addr}"
                           f" | 借款人: {user_addr} | 还款金额: {repay_amount}"
                           f" | 借款人新债务: {account_borrows_new}"
                           f" | 市场总债务新值: {total_borrows_new}"
                           f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        elif topic == config.TOPICS['LiquidateBorrow']:
            liquidate_borrow_event = self._event.LiquidateBorrow()
            decoded = liquidate_borrow_event.process_log(log)
            liquidator_addr = decoded['args']['liquidator']
            user_addr = decoded['args']['borrower']
            if user_addr in config.BLACKLIST:
                return None
            repay_amount = decoded['args']['repayAmount'] / 1e18
            vtoken_collateral_addr = decoded['args']['vTokenCollateral']
            seize_tokens = decoded['args']['seizeTokens'] / 1e18
            self.Log.info(f"🔥 检测到用户清算事件! 合约地址: {vtoken_addr} | 清算者: {liquidator_addr}"
                          f" | 被清算的借款人: {user_addr} | 偿还的债务金额: {repay_amount}"
                          f" | 抵押品vToken地址: {vtoken_collateral_addr}"
                          f" | 清算者获得的抵押品vToken数量: {seize_tokens}"
                          f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        else:
            market_entered_event = self._event.MarketEntered()
            decoded = market_entered_event.process_log(log)
            user_addr = decoded['args']['user']
            if user_addr in config.BLACKLIST:
                return None
            market_addr = decoded['args']['market']
            self.Log.info(f"🔥 检测到用户抵押事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
                          f" | 市场地址: {market_addr} | transactionHash: {log['transactionHash']}")
            return 1, user_addr

    async def poll_risk_check(self):
        while True:
            await self._db.remove_user_hf_by_score('high_risk_queue', 1.3, float('inf'))

            user_address_list = await self._db.get_user_hf_by_score('high_risk_queue', 0, float('inf'))

            if not user_address_list:
                continue

            batch_size = 20
            for i in range(0, len(user_address_list), batch_size):
                batch = user_address_list[i: i + batch_size]
                await asyncio.gather(*(self._process_user(addr, self._binance_price) for addr in batch))

            await asyncio.sleep(config.POLL_DELAY)

    async def listen_user_events(self):
        subscribe_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_subscribe",
            "params": [
                "logs", {
                    "topics": [list(config.TOPICS.values())]
                }]
        }
        while True:
            try:
                async with websockets.connect(
                        config.BSC_WSS_URI, ping_timeout=120, ping_interval=5, close_timeout=5) as ws:
                    await ws.send(json.dumps(subscribe_msg))
                    msg = json.loads(await ws.recv())
                    self.Log.info(f"成功订阅全网 Borrow/Redeem/RepayBorrow/LiquidateBorrow/MarketEntered 事件, SubID: {msg['result']}")
                    while True:
                        try:
                            message = json.loads(await ws.recv())
                            if "params" in message and "result" in message["params"]:
                                log = message["params"]["result"]
                                process_result = self._process_events_log(log)
                                if not process_result:
                                    continue
                                prior, address = process_result

                                self._pending_users.add(address)

                                try:
                                    await self._task_queue.put((prior, self._prior_counter['user_event'], {
                                        "type": 'user_event',
                                        "address": address,
                                        "ts": time.time()
                                    }))
                                    self._prior_counter['user_event'] += 1
                                except asyncio.QueueFull:
                                    self.Log.warning("任务队列达到上线!")

                        except LogTopicError as e:
                            self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 日志: {log}")

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听事件-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                retry_delay = min(2 ** config.RETRY_DELAY_EVENT, 30)
                await asyncio.sleep(retry_delay)
                if retry_delay < 30:
                    config.RETRY_DELAY_EVENT += 1
                else:
                    config.RETRY_DELAY_EVENT = 0

    async def listen_binance_price_updates(self):
        streams = "/".join([f"{t.lower()}usdt@aggTrade" for t in await self._db.get_all_symbols()])
        while True:
            try:
                async with websockets.connect(
                        config.BINANCE_PRICE_WSS_URI + streams, ping_timeout=120, ping_interval=5, close_timeout=5) as ws:
                    self.Log.info("成功订阅实时 binance 价格更新事件推送")
                    while True:
                        message = json.loads(await ws.recv())
                        data = message['data']

                        symbol = data['s'].replace('USDT', '').lower()
                        if symbol == 'btc':
                            symbol = 'btcb'

                        vtoken_addr = await self._db.get_vtoken('asset:symbol_map', symbol)

                        last_price = self._binance_price.get(vtoken_addr, 0)
                        current_price = price_to_wei(data['p'])
                        fluctuation = 1 - last_price / current_price
                        if abs(fluctuation) >= get_price_volatility_threshold(last_price):
                            self.Log.info(f"💴 代币: {data['s']} | 价格: {data['p']} | 价格涨跌: {fluctuation * 100:.4f}%")
                            try:
                                await self._task_queue.put((2, self._prior_counter['price_update'], {
                                    "type": "price_update",
                                    "address": vtoken_addr,
                                    "ts": time.time()
                                }))
                                self._prior_counter['price_update'] += 1
                            except asyncio.QueueFull:
                                self.Log.warning("任务队列达到上限!")

                        self._binance_price[vtoken_addr] = current_price

                        if vtoken_addr == config.BNB_VTOKEN_ADDRESS:
                            ex_rate = int(await self._db.get_exchange_rate(f"rate:{vtoken_addr}"))
                            # WBNB
                            self._binance_price['0x6bca74586218db34cdb402295796b79663d816e9'] = self._binance_price[
                                vtoken_addr]
                            # asBNB
                            self._binance_price['0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] = self._binance_price[
                                vtoken_addr] * ex_rate

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听价格-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                retry_delay = min(2 ** config.RETRY_DELAY_PRICE, 30)
                await asyncio.sleep(retry_delay)
                if retry_delay < 30:
                    config.RETRY_DELAY_PRICE += 1
                else:
                    config.RETRY_DELAY_PRICE = 0

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
                    self.Log.info(f"成功订阅 Mempool... SubID: {msg["result"]}")
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            tx_hash = data['params']['result']
                            try:
                                await self._task_queue.put((0, self._prior_counter['oracle_update'], {
                                    "type": "oracle_update",
                                    "tx_hash": tx_hash,
                                    "ts": time.time()
                                }))
                                self._prior_counter['oracle_update'] += 1
                            except asyncio.QueueFull:
                                self.Log.warning("任务队列达到上限!")
                        except KeyError as e:
                            self.Log.error(f"消息错误: {e}")

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听公共池-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")

    async def main(self):
        self._event = await self._client.get_event()
        await self._load_vtoken_cache_()
        await self._init_price_()

        await asyncio.gather(
            self.poll_risk_check(),
            self.listen_user_events(),
            self.listen_binance_price_updates(),
            self.listen_mempool(),
            self.listen_worker(),
        )

if __name__ == '__main__':
    run = Run()
    run()