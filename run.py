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
            if 'USDT' in item['symbol']:
                symbol = item['symbol'].replace('USDT', '')
                token = self._db.get_vtoken('venus:assets:symbol', symbol)
                if token:
                    token = json.loads(token)
                    self._binance_price[token['address']] = price_to_wei(item['price'])
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
            if not self._db.vtoken_exists('venus:assets:v_addr', vtoken_addr.lower()):
                token = self._client.get_vtoken(vtoken_addr.lower())
                self._db.update_venus_vtoken('venus:assets:symbol', token['symbol'], json.dumps(token))
                self._db.update_venus_vtoken('venus:assets:v_addr', vtoken_addr.lower(), json.dumps(token))

            borrow_event = self.event.Borrow()
            decoded = borrow_event.process_log(log)
            user_addr = decoded['args']['borrower']
            borrow_amount = decoded['args']['borrowAmount']
            account_borrows = decoded['args']['accountBorrows']
            total_borrows = decoded['args']['totalBorrows']
            self.Log.info(f"🔥 检测到用户借款事件! 合约地址: {vtoken_addr} | 借款人: {user_addr}"
                          f" | 借款金额: {borrow_amount} | 借款人总债务: {account_borrows}"
                          f" | 市场总债务: {total_borrows}")

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
            redeem_amount = decoded['args']['redeemAmount']
            redeem_tokens = decoded['args']['redeemTokens']
            self.Log.debug(f"🔥 检测到用户赎回事件! 合约地址: {vtoken_addr} | 赎回者: {user_addr}"
                          f" | 赎回资产数量: {redeem_amount} | 销毁vToken数量: {redeem_tokens}")

        elif topic == config.TOPICS['RepayBorrow']:
            repay_borrow_event = self.event.RepayBorrow()
            decoded = repay_borrow_event.process_log(log)
            user_addr = decoded['args']['payer']
            borrower_addr = decoded['args']['borrower']
            repay_amount = decoded['args']['repayAmount']
            account_borrows_new = decoded['args']['accountBorrowsNew']
            total_borrows_new = decoded['args']['totalBorrowsNew']
            self.Log.debug(f"🔥 检测到用户还款事件! 合约地址: {vtoken_addr} | 还款人: {user_addr}"
                          f" | 借款人: {borrower_addr} | 还款金额: {repay_amount}"
                          f" | 借款人新债务: {account_borrows_new} | 市场总债务新值: {total_borrows_new}")

        elif topic == config.TOPICS['LiquidateBorrow']:
            liquidate_borrow_event = self.event.LiquidateBorrow()
            decoded = liquidate_borrow_event.process_log(log)
            user_addr = decoded['args']['liquidator']
            borrower_addr = decoded['args']['borrower']
            repay_amount = decoded['args']['repayAmount']
            vtoken_collateral_addr = decoded['args']['vTokenCollateral']
            seize_tokens = decoded['args']['seizeTokens']
            self.Log.debug(f"🔥 检测到用户清算事件! 合约地址: {vtoken_addr} | 清算者: {user_addr}"
                          f" | 被清算的借款人: {borrower_addr} | 偿还的债务金额: {repay_amount}"
                          f" | 抵押品vToken地址: {vtoken_collateral_addr}"
                          f" | 清算者获得的抵押品vToken数量: {seize_tokens}")

        else:
            market_entered_event = self.event.MarketEntered()
            decoded = market_entered_event.process_log(log)
            user_addr = decoded['args']['user']
            market_addr = decoded['args']['market']
            collateral_balance = decoded['args']['collateralBalance']
            borrow_balance = decoded['args']['borrowBalance']
            exchange_rate = decoded['args']['exchangeRate']
            self.Log.info(f"🔥 检测到用户抵押事件! 合约地址: {vtoken_addr} | 用户: {user_addr}"
                          f" | 市场地址: {market_addr} | 抵押品数量: {collateral_balance}"
                          f" | 借款数量: {borrow_balance} | 抵押品汇率: {exchange_rate}")
        return user_addr

    async def _process_and_analyze(self, user_addr):
        risky_report = await self.analyzer.analyze_user(user_addr.lower(), self._binance_price)
        if risky_report['is_liquidatable']:
            asyncio.create_task(self.engine.handle_liquidation(risky_report))

    async def poll_risk_check(self):
        while True:
            user_address_list = self._db.get_user_hf_by_score(f'high_risk_queue', 0, 1.1)
            for user_addr in user_address_list:
                risky_report = await self.analyzer.analyze_user(user_addr, self._binance_price)
                if risky_report['is_liquidatable']:
                    asyncio.create_task(self.engine.handle_liquidation(risky_report))
                if risky_report['health_factor'] > 1.1 or risky_report['health_factor'] < 0.6:
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
                        message = json.loads(await web3.recv())
                        if "params" in message and "result" in message["params"]:
                            log = message["params"]["result"]
                            user_addr = self._process_events_log(log)
                            asyncio.create_task(self._process_and_analyze(user_addr))
            except Exception as e:
                self.Log.info(f"发生异常: {e}, 正在重新连接...")

    async def listen_binance_price_updates(self):
        streams = "/".join([f"{t.lower()}usdt@aggTrade" for t in self._db.get_all_symbols()])
        while True:
            try:
                async with websockets.connect(config.BINANCE_PRICE_WSS_URI + streams) as ws:
                    self.Log.info("--- 等待实时 binance 价格更新事件推送 ---")
                    while True:
                        message = json.loads(await ws.recv())
                        data = message['data']
                        self.Log.debug(f"💴 代币: {data['s']} | 价格: {data['p']}"
                              f" | 更新时间: {datetime.fromtimestamp(float(data['E']) / 1000).strftime('%Y-%m-%d %H:%M:%S')}")
                        symbol = data['s'].replace('USDT', '')
                        vtoken_addr = self._db.get_vtoken('symbol_map', symbol)
                        self._binance_price[vtoken_addr] = price_to_wei(data['p'])
                        asyncio.create_task(self._check_opportunity(vtoken_addr))
            except Exception as e:
                self.Log.info(f"发生异常: {e}, 正在重新连接...")

    async def main(self):
        await asyncio.gather(
            self.poll_risk_check(),
            self.listen_user_events(),
            self.listen_binance_price_updates(),
        )

if __name__ == '__main__':
    run = Run()
    run()