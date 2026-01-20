import json
import asyncio
import config
import websockets
from logger import Logger
from datetime import datetime
from web3client import VenusClient
from analyzer import Analyzer
from liquidator import Liquidator
from redis_client import RedisClient
from web3.exceptions import LogTopicError
from websockets.exceptions import ConnectionClosedError
from utils import price_to_wei, get_realtime_prices

class Run:
    def __init__(self):
        self._db = RedisClient()
        self._client = VenusClient(config.NODEREAL_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
        self.Log = Logger()()
        self.analyzer = Analyzer(self._client, self._db, self.Log)
        self.engine = Liquidator(self._client, self._db, self.Log)
        self._binance_price = {}
        self.event = self._client.get_event()

    def __call__(self):
        for item in get_realtime_prices():
            if item['symbol'].endswith('USDT'):
                symbol = item['symbol'].replace('USDT', '').lower()
                if symbol == 'btc':
                    symbol = 'btcb'
                vtoken_addr = self._db.get_vtoken('symbol_map', symbol)
                if vtoken_addr:
                    self._binance_price[vtoken_addr] = price_to_wei(item['price'])
        self._binance_price['0xfd5840cd36d94d7229439859c0112a4185bc0255'] = 1e18
        asyncio.run(self.main())

    async def _check_opportunity(self, vtoken_addr):
        user_address_list = self._db.read_by_name(f'asset:user:{vtoken_addr}')
        for user_addr in user_address_list:
            risky_report = await self.analyzer.analyze_user(user_addr, self._binance_price)
            if risky_report['is_liquidatable']:
                asyncio.create_task(self.engine.handle_liquidation(risky_report))

    def _process_events_log(self, log):
        vtoken_addr = log['address']
        topic = log['topics'][0]

        if topic == config.TOPICS['Borrow']:
            # token = self._db.get_vtoken('venus:assets:v_addr', vtoken_addr.lower())
            # if not token:
            #     token = self._client.get_vtoken(vtoken_addr.lower())
            #     self._db.update_venus_vtoken('venus:assets:symbol', token['symbol'], json.dumps(token))
            #     self._db.update_venus_vtoken('venus:assets:v_addr', vtoken_addr.lower(), json.dumps(token))

            borrow_event = self.event.Borrow()
            decoded = borrow_event.process_log(log)
            user_addr = decoded['args']['borrower']
            borrow_amount = decoded['args']['borrowAmount'] / 1e18
            account_borrows = decoded['args']['accountBorrows'] / 1e18
            total_borrows = decoded['args']['totalBorrows'] / 1e18
            self.Log.info(f"🔥 检测到用户借款事件! 合约地址: {vtoken_addr} | 借款人: {user_addr}"
                          f" | 借款金额: {borrow_amount} | 借款人总债务: {account_borrows}"
                          f" | 市场总债务: {total_borrows} | transactionHash: {log['transactionHash']}")

        # elif topic == config.TOPICS['Mint']:
        #     mint_event = self.event.Mint()
        #     decoded = mint_event.process_log(log)
        #     user_addr = decoded['args']['minter']
        #     mint_amount = decoded['args']['mintAmount']
        #     mint_tokens = decoded['args']['mintTokens']
        #     self.Log.debug(f"🔥 检测到用户存款事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
        #                   f" | 存入资产数量: {mint_amount} | 获得代币数量: {mint_tokens}")

        elif topic == config.TOPICS['Redeem']:
            redeem_event = self.event.Redeem()
            decoded = redeem_event.process_log(log)
            user_addr = decoded['args']['redeemer']
            redeem_amount = decoded['args']['redeemAmount'] / 1e18
            redeem_tokens = decoded['args']['redeemTokens'] / 1e18
            self.Log.info(f"🔥 检测到用户赎回事件! 合约地址: {vtoken_addr} | 赎回者: {user_addr}"
                           f" | 赎回资产数量: {redeem_amount} | 销毁vToken数量: {redeem_tokens}"
                           f" | transactionHash: {log['transactionHash']}")

        elif topic == config.TOPICS['RepayBorrow']:
            repay_borrow_event = self.event.RepayBorrow()
            decoded = repay_borrow_event.process_log(log)
            payer_addr = decoded['args']['payer']
            user_addr = decoded['args']['borrower']
            repay_amount = decoded['args']['repayAmount'] / 1e18
            account_borrows_new = decoded['args']['accountBorrowsNew'] / 1e18
            total_borrows_new = decoded['args']['totalBorrowsNew'] / 1e18
            self.Log.info(f"🔥 检测到用户还款事件! 合约地址: {vtoken_addr} | 还款人: {payer_addr}"
                           f" | 借款人: {user_addr} | 还款金额: {repay_amount}"
                           f" | 借款人新债务: {account_borrows_new}"
                           f" | 市场总债务新值: {total_borrows_new}"
                           f" | transactionHash: {log['transactionHash']}")

        elif topic == config.TOPICS['LiquidateBorrow']:
            liquidate_borrow_event = self.event.LiquidateBorrow()
            decoded = liquidate_borrow_event.process_log(log)
            liquidator_addr = decoded['args']['liquidator']
            user_addr = decoded['args']['borrower']
            repay_amount = decoded['args']['repayAmount'] / 1e18
            vtoken_collateral_addr = decoded['args']['vTokenCollateral']
            seize_tokens = decoded['args']['seizeTokens'] / 1e18
            self.Log.info(f"🔥 检测到用户清算事件! 合约地址: {vtoken_addr} | 清算者: {liquidator_addr}"
                          f" | 被清算的借款人: {user_addr} | 偿还的债务金额: {repay_amount}"
                          f" | 抵押品vToken地址: {vtoken_collateral_addr}"
                          f" | 清算者获得的抵押品vToken数量: {seize_tokens}"
                          f" | transactionHash: {log['transactionHash']}")

        else:
            market_entered_event = self.event.MarketEntered()
            decoded = market_entered_event.process_log(log)
            user_addr = decoded['args']['user']
            market_addr = decoded['args']['market']
            collateral_balance = decoded['args']['collateralBalance'] / 1e18
            borrow_balance = decoded['args']['borrowBalance'] / 1e18
            exchange_rate = decoded['args']['exchangeRate'] / 1e18
            self.Log.info(f"🔥 检测到用户抵押事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
                          f" | 市场地址: {market_addr} | 抵押品数量: {collateral_balance}"
                          f" | 借款数量: {borrow_balance} | 抵押品汇率: {exchange_rate}"
                          f" | transactionHash: {log['transactionHash']}")
        return user_addr

    async def _process_and_analyze(self, user_addr):
        risky_report = await self.analyzer.analyze_user(user_addr.lower(), self._binance_price)
        if risky_report['is_liquidatable']:
            asyncio.create_task(self.engine.handle_liquidation(risky_report))

    async def poll_risk_check(self):
        while True:
            user_address_list = self._db.get_user_hf_by_score(f'high_risk_queue', 0, 1.2)
            for user_addr in user_address_list:
                risky_report = await self.analyzer.analyze_user(user_addr, self._binance_price)
                if risky_report['is_liquidatable']:
                    asyncio.create_task(self.engine.handle_liquidation(risky_report))
                if risky_report['health_factor'] > 1.2 or risky_report['health_factor'] < 0.6:
                    self._db.remove_user_hf_from_high_risk('high_risk_queue', user_addr)
            await asyncio.sleep(3)

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
                async with websockets.connect(config.BSC_WSS_URI) as web3:
                    await web3.send(json.dumps(subscribe_msg))
                    msg = json.loads(await web3.recv())
                    self.Log.info(f"成功订阅全网 Borrow/Redeem/RepayBorrow/LiquidateBorrow/MarketEntered 事件, SubID: {msg['result']}")
                    while True:
                        try:
                            message = json.loads(await web3.recv())
                            if "params" in message and "result" in message["params"]:
                                log = message["params"]["result"]
                                user_addr = self._process_events_log(log)
                                asyncio.create_task(self._process_and_analyze(user_addr))
                        except LogTopicError as e:
                            self.Log.error(f"发生异常: {e}, 异常类型: {type(e)}, 日志: {log}")
            except (ConnectionClosedError, TimeoutError) as e:
                self.Log.error(f"监听事件-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                await asyncio.sleep(config.RETRY_DELAY)

    async def listen_binance_price_updates(self):
        streams = "/".join([f"{t.lower()}usdt@aggTrade" for t in self._db.get_all_symbols()])
        while True:
            try:
                async with websockets.connect(config.BINANCE_PRICE_WSS_URI + streams) as ws:
                    self.Log.info("成功订阅实时 binance 价格更新事件推送")
                    while True:
                        message = json.loads(await ws.recv())
                        data = message['data']
                        self.Log.debug(f"💴 代币: {data['s']} | 价格: {data['p']}"
                              f" | 更新时间: {datetime.fromtimestamp(float(data['E']) / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
                        symbol = data['s'].replace('USDT', '').lower()
                        if symbol == 'btc':
                            symbol = 'btcb'
                        vtoken_addr = self._db.get_vtoken('symbol_map', symbol)
                        self._binance_price[vtoken_addr] = price_to_wei(data['p'])

                        if vtoken_addr == '0xa07c5b74c9b40447a954e1466938b865b6bbea36':
                            # WBNB
                            self._binance_price['0x6bca74586218db34cdb402295796b79663d816e9'] = self._binance_price[
                                vtoken_addr] * 1e18
                            # asBNB
                            self._binance_price['0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] = self._binance_price[
                                vtoken_addr] * self._client.get_exchange_rate(vtoken_addr) * 1e18

                        asyncio.create_task(self._check_opportunity(vtoken_addr))
            except (ConnectionClosedError, TimeoutError) as e:
                self.Log.error(f"监听价格-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                await asyncio.sleep(config.RETRY_DELAY)

    async def main(self):
        await asyncio.gather(
            self.poll_risk_check(),
            self.listen_user_events(),
            self.listen_binance_price_updates(),
        )

if __name__ == '__main__':
    run = Run()
    run()