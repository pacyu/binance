import os
import json
import time
import asyncio
import config
import websockets
from logger import Logger
from dotenv import load_dotenv
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from redis_client import RedisClient
from web3.exceptions import LogTopicError
from websockets.exceptions import ConnectionClosedError
from utils import price_to_wei, get_realtime_prices

class Run:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        self._db = RedisClient()
        self._client = VenusClient(config.ANKR_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR, private_key, bloxroute_api_key)
        self.Log = Logger()()

        self._vtoken_cache = {}
        self._binance_price = {}

        self.analyzer = Analyzer(self._client, self._db, self.Log)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)
        self._event = self._client.get_event()
        self._task_queue = asyncio.PriorityQueue(maxsize=2000)

        self._prior_counter = {'user_event': 0, 'price_update': 0}

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

    def __call__(self):
        asyncio.run(self.main())

    async def listen_worker(self):
        while True:
            prior, counter, task = await self._task_queue.get()

            try:
                if task["type"] == "user_event":
                    asyncio.create_task(self._handle_user_event(task))

                elif task["type"] == "price_update":
                    asyncio.create_task(self._handle_price_event(task))

            except Exception as e:
                self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 任务: {task}")

            finally:
                self._task_queue.task_done()

    async def _handle_user_event(self, task):
        user_addr = task["u_addr"]
        await self._process_and_analyze(user_addr)

    async def _handle_price_event(self, task):
        vtoken_addr = task["v_addr"]
        await self._check_opportunity(vtoken_addr)

    async def _check_opportunity(self, vtoken_addr):
        user_address_list = list(await self._db.read_by_name(f'asset:users:{vtoken_addr}'))
        
        if not user_address_list:
            return

        async def _process_user(u_addr):
            risky_report = await self.analyzer.analyze_user(u_addr, self._binance_price)
            if risky_report['is_liquidatable']:
                await self.engine.handle_liquidation(risky_report)

        batch_size = 20
        for i in range(0, len(user_address_list), batch_size):
            batch = user_address_list[i: i + batch_size]
            await asyncio.gather(*(_process_user(addr) for addr in batch))

    async def _process_and_analyze(self, user_addr):
        risky_report = await self.analyzer.analyze_user(user_addr.lower(), self._binance_price)
        if risky_report['is_liquidatable']:
            await self.engine.handle_liquidation(risky_report)

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
                return -1, user_addr
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
                return -1, user_addr
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
                return -1, user_addr
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
                return -1, user_addr
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
                return -1, user_addr
            market_addr = decoded['args']['market']
            collateral_balance = decoded['args']['collateralBalance'] / 1e18
            borrow_balance = decoded['args']['borrowBalance'] / 1e18
            exchange_rate = decoded['args']['exchangeRate'] / 1e18
            self.Log.info(f"🔥 检测到用户抵押事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
                          f" | 市场地址: {market_addr} | 抵押品数量: {collateral_balance}"
                          f" | 借款数量: {borrow_balance} | 抵押品汇率: {exchange_rate}"
                          f" | transactionHash: {log['transactionHash']}")
            return 1, user_addr

    async def poll_risk_check(self):
        while True:
            user_address_list = await self._db.get_user_hf_by_score('high_risk_queue', 0, float('inf'))
            for user_addr in user_address_list:
                risky_report = await self.analyzer.analyze_user(user_addr, self._binance_price)
                if risky_report['is_liquidatable']:
                    await self.engine.handle_liquidation(risky_report)
            await self._db.remove_user_hf_by_score('high_risk_queue', 1.3, float('inf'))
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
                                prior, user_addr = self._process_events_log(log)
                                if user_addr in config.BLACKLIST:
                                    continue
                                try:
                                    await self._task_queue.put((prior, self._prior_counter['user_event'], {
                                        "type": "user_event",
                                        "u_addr": user_addr,
                                        "ts": time.time()
                                    }))
                                    self._prior_counter['user_event'] += 1
                                except asyncio.QueueFull:
                                    self.Log.warning("任务队列达到上线！")

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
                        if abs(fluctuation) >= config.PRICE_VOLATILITY_THRESHOLD:
                            self.Log.info(f"💴 代币: {data['s']} | 价格: {data['p']} | 价格涨幅度: {fluctuation * 100}%")
                            try:
                                await self._task_queue.put((2, self._prior_counter['price_update'], {
                                    "type": "price_update",
                                    "v_addr": vtoken_addr,
                                    "ts": time.time()
                                }))
                                self._prior_counter['price_update'] += 1
                            except asyncio.QueueFull:
                                self.Log.warning("任务队列达到上限！")

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

    async def main(self):
        await self._load_vtoken_cache_()
        await self._init_price_()

        await asyncio.gather(
            self.poll_risk_check(),
            self.listen_user_events(),
            self.listen_binance_price_updates(),
            self.listen_worker(),
        )

if __name__ == '__main__':
    run = Run()
    run()