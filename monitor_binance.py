import json
import config
import asyncio
import websockets
from logger import Logger
from redis_client import RedisClient
from websockets.exceptions import ConnectionClosedError
from utils import get_binance_symbols, price_to_wei, get_price_volatility_threshold


class MonitorBinance:
    def __init__(self):
        self._db = RedisClient()
        self.Log = Logger('price_update.log')()
        self._vtoken_cache = {}
        self._binance_price = {}

    async def _load_cache_(self):
        self._vtoken_cache = await self._db.get_markets()

    async def listen_binance_price_updates(self):
        streams = "/".join([f"{t.lower()}usdt@aggTrade" for t in get_binance_symbols()])
        while True:
            try:
                async with websockets.connect(
                        config.BINANCE_PRICE_WSS_URI + streams, ping_timeout=120, ping_interval=5,
                        close_timeout=5) as ws:
                    self.Log.info("成功订阅实时 binance 价格更新事件推送")
                    async for message in ws:
                        message = json.loads(message)
                        data = message['data']

                        symbol = data['s'].replace('USDT', '').lower()
                        if symbol == 'btc':
                            symbol = 'btcb'

                        vtoken_addr = await self._db.get_v_address_by_symbol(symbol)
                        token_config = self._vtoken_cache[vtoken_addr]
                        last_price = self._binance_price.get(vtoken_addr, 0)
                        current_price = price_to_wei(data['p'], int(token_config['underlying_decimal']))
                        fluctuation = 1 - last_price / current_price

                        # 减少日志量
                        if abs(fluctuation) > get_price_volatility_threshold(current_price):
                            self.Log.info(
                                f"💴 代币: {data['s']} | 价格: {data['p']} | 价格涨跌: {fluctuation * 100:.4f}%")
                            # TODO: 触发批量扫描，扫描在这个价格波动下的高风险用户钱包，
                            #  等到预言机上的价格和该价格相等时，进行清算处理，
                            #  这里可以只标记这些钱包地址以及价格，然后另外再写一个模块监听预言机价格变动

                        await self._db.save_or_update_binance_price({vtoken_addr: current_price})

                        self._binance_price[vtoken_addr] = current_price

                        if vtoken_addr == config.BNB_ADDRESS:
                            ex_rate = int(await self._db.get_exchange_rate(vtoken_addr))
                            # WBNB
                            self._binance_price['0x6bca74586218db34cdb402295796b79663d816e9'] = self._binance_price[
                                vtoken_addr]
                            await self._db.save_or_update_binance_price(
                                {'0x6bca74586218db34cdb402295796b79663d816e9': self._binance_price[
                                    '0x6bca74586218db34cdb402295796b79663d816e9']})
                            # asBNB
                            self._binance_price['0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] = self._binance_price[
                                                                                                    vtoken_addr] * ex_rate
                            await self._db.save_or_update_binance_price(
                                {'0xcc1db43a06d97f736c7b045aedd03c6707c09bdf': self._binance_price[
                                                                                   '0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] * ex_rate})

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听价格-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                retry_delay = min(2 ** config.RETRY_DELAY_PRICE, 30)
                await asyncio.sleep(retry_delay)
                if retry_delay < 30:
                    config.RETRY_DELAY_PRICE += 1
                else:
                    config.RETRY_DELAY_PRICE = 0

    async def main(self):
        await self._load_cache_()


    def __call__(self):
        asyncio.run(self.main())


if __name__ == "__main__":
    monitor = MonitorBinance()
    monitor()
