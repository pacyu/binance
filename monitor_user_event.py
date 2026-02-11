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
from web3.exceptions import LogTopicError
from websockets.exceptions import ConnectionClosedError


class MonitorUserEvent:
    def __init__(self):
        load_dotenv()
        private_key = os.getenv('PRIVATE_KEY')
        bloxroute_api_key = os.getenv('BLOXROUTE_API_KEY')
        bloxroute_auth_header = os.getenv('BLOXROUTE_AUTH_HEADER')

        self.Log = Logger('user_event.log')()
        self._client = VenusClient(config.CHAINSTACK_RPC_URL,
                                   config.VENUS_CORE_COMPTROLLER_ADDR,
                                   private_key,
                                   bloxroute_api_key,
                                   bloxroute_auth_header)
        self._db = RedisClient()
        self.analyzer = Analyzer(self._client, self._db)
        self.engine = Liquidator(self._client, self._db, self.analyzer, self.Log)

        self._task_queue = asyncio.PriorityQueue(maxsize=1000)

        self._pending_users = set()
        self._event = None

        self._prior_counter = 0
        self._vtoken_cache = {}

        self._execution_lock = asyncio.Lock()
        self.engine.set_execution_lock(self._execution_lock)

    async def _load_vtoken_cache_(self):
        self._vtoken_cache = await self._db.get_markets()
        self.analyzer.set_vtoken_cache(self._vtoken_cache)
        self.engine.set_vtoken_cache(self._vtoken_cache)

    async def _process_and_analyze(self, user_address):
        await self._db.save_user_wallet(user_address)
        user_profiles = await self.analyzer.get_user_snapshot([user_address])

        for user_address, user_profile in user_profiles.items():
            if not user_profile:
                await self._db.remove_user_profile(user_address)
                continue

            prices = await self._client.get_oracle_price(list(user_profile.keys()))
            hf = self.analyzer.calculate_hf(user_profile, prices)
            if hf <= 1.3:
                await self._db.save_or_update_user_health_factor({user_address: hf})
            else:
                await self._db.remove_user_health_factor_by_wallet_address(user_address)

            await self._db.update_user_profile(user_address, user_profile)

    async def _handle_user_event(self, task):
        user_address = task["address"]
        await self._process_and_analyze(user_address)
        if user_address in self._pending_users:
            self._pending_users.remove(user_address)

    def _process_events_log(self, log):
        vtoken_addr = log['address']
        topic = log['topics'][0]

        if topic == config.TOPICS['Borrow']:
            borrow_event = self._event.Borrow()
            decoded = borrow_event.process_log(log)
            user_addr = decoded['args']['borrower']
            if user_addr in config.BLACKLIST:
                return None
            borrow_amount = decoded['args']['borrowAmount']
            account_borrows = decoded['args']['accountBorrows']
            total_borrows = decoded['args']['totalBorrows']
            self.Log.info(f"🔥 检测到用户借款事件! 合约地址: {vtoken_addr} | 借款人: {user_addr}"
                          f" | 借款数量: {borrow_amount} | 借款人总债务: {account_borrows}"
                          f" | 市场总债务: {total_borrows} | transactionHash: {log['transactionHash']}")
            return 1, user_addr

        elif topic == config.TOPICS['Redeem']:
            redeem_event = self._event.Redeem()
            decoded = redeem_event.process_log(log)
            user_addr = decoded['args']['redeemer']
            if user_addr in config.BLACKLIST:
                return None
            redeem_amount = decoded['args']['redeemAmount']
            redeem_tokens = decoded['args']['redeemTokens']
            self.Log.info(f"🔥 检测到用户赎回事件! 合约地址: {vtoken_addr} | 赎回者: {user_addr}"
                           f" | 赎回资产数量: {redeem_amount} | 销毁vToken数量: {redeem_tokens}"
                           f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        elif topic == config.TOPICS['RepayBorrow']:
            repay_borrow_event = self._event.RepayBorrow()
            decoded = repay_borrow_event.process_log(log)
            payer_addr = decoded['args']['payer']
            user_addr = decoded['args']['borrower']
            if payer_addr in config.BLACKLIST:
                return None
            repay_amount = decoded['args']['repayAmount']
            account_borrows_new = decoded['args']['accountBorrowsNew']
            total_borrows_new = decoded['args']['totalBorrowsNew']
            self.Log.info(f"🔥 检测到用户还款事件! 合约地址: {vtoken_addr} | 还款人: {payer_addr}"
                           f" | 借款人: {user_addr} | 还款数量: {repay_amount}"
                           f" | 借款人新债务: {account_borrows_new}"
                           f" | 市场总债务新值: {total_borrows_new}"
                           f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        elif topic == config.TOPICS['LiquidateBorrow']:
            liquidate_borrow_event = self._event.LiquidateBorrow()
            decoded = liquidate_borrow_event.process_log(log)
            liquidator_addr = decoded['args']['liquidator']
            user_addr = decoded['args']['borrower']
            if liquidator_addr in config.BLACKLIST:
                return None
            repay_amount = decoded['args']['repayAmount']
            vtoken_collateral_addr = decoded['args']['vTokenCollateral']
            seize_tokens = decoded['args']['seizeTokens']
            self.Log.info(f"🔥 检测到用户清算事件! 合约地址: {vtoken_addr} | 清算者: {liquidator_addr}"
                          f" | 被清算的借款人: {user_addr} | 偿还的债务数量: {repay_amount}"
                          f" | 抵押品vToken地址: {vtoken_collateral_addr}"
                          f" | 清算者获得的抵押品vToken数量: {seize_tokens}"
                          f" | transactionHash: {log['transactionHash']}")
            return 50, user_addr

        else:
            market_entered_event = self._event.MarketEntered()
            decoded = market_entered_event.process_log(log)
            user_addr = decoded['args']['account']
            if user_addr in config.BLACKLIST:
                return None
            v_addr = decoded['args']['vToken']
            self.Log.info(f"🔥 检测到用户抵押事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
                          f" | 市场地址: {v_addr} | transactionHash: {log['transactionHash']}")
            return 1, user_addr

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
                    async for message in ws:
                        try:
                            message = json.loads(message)
                            if "params" in message and "result" in message["params"]:
                                log = message["params"]["result"]
                                process_result = self._process_events_log(log)
                                if not process_result:
                                    continue
                                prior, address = process_result

                                if address in self._pending_users:
                                    continue

                                self._pending_users.add(address.lower())

                                try:
                                    await self._task_queue.put((prior, self._prior_counter, {
                                        "type": 'user_event',
                                        "address": address.lower(),
                                        "ts": time.time()
                                    }))
                                    self._prior_counter += 1
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

    async def listen_worker(self):
        while True:
            prior, counter, task = await self._task_queue.get()

            try:
                if task["type"] == "user_event":
                    await self._handle_user_event(task)

            except Exception as e:
                self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 任务: {task}")

            finally:
                self._task_queue.task_done()

    async def run(self):
        self._event = await self._client.get_event()
        await self._load_vtoken_cache_()

        await asyncio.gather(
            self.listen_user_events(),
            self.listen_worker()
        )

    def __call__(self, *args, **kwargs):
        asyncio.run(self.run())


if __name__ == "__main__":
    monitor = MonitorUserEvent()
    monitor()
