import json
import config
import asyncio
import websockets
from logger import Logger
from redis_client import RedisClient
from websockets.exceptions import ConnectionClosedError
from utils import price_to_wei


class MonitorBinance:
    def __init__(self):
        self._db = RedisClient()
        self.Log = Logger('price_update.log')()
        self._binance_price = {}

    async def listen_binance_price_updates(self):
        streams = "/".join([f"{t.lower()}usdt@aggTrade" for t in await self._db.get_all_symbols()])
        while True:
            try:
                async with websockets.connect(
                        config.BINANCE_PRICE_WSS_URI + streams, ping_timeout=120, ping_interval=5, close_timeout=5) as ws:
                    self.Log.info("成功订阅实时 binance 价格更新事件推送")
                    async for message in ws:
                        message = json.loads(message)
                        data = message['data']

                        symbol = data['s'].replace('USDT', '').lower()
                        if symbol == 'btc':
                            symbol = 'btcb'

                        vtoken_addr = await self._db.get_vtoken('asset:symbol_map', symbol)

                        last_price = self._binance_price.get(vtoken_addr, 0)
                        current_price = price_to_wei(data['p'])
                        fluctuation = 1 - last_price / current_price

                        self.Log.info(f"💴 代币: {data['s']} | 价格: {data['p']} | 价格涨跌: {fluctuation * 100:.4f}%")
                        await self._db.update_binance_price(f"binance_price", vtoken_addr, current_price)

                        self._binance_price[vtoken_addr] = current_price

                        if vtoken_addr == config.BNB_VTOKEN_ADDRESS:
                            ex_rate = int(await self._db.get_exchange_rate(f"rate:{vtoken_addr}"))
                            # WBNB
                            self._binance_price['0x6bca74586218db34cdb402295796b79663d816e9'] = self._binance_price[
                                vtoken_addr]
                            await self._db.update_binance_price(f"binance_price",
                                                                '0x6bca74586218db34cdb402295796b79663d816e9',
                                                                self._binance_price['0x6bca74586218db34cdb402295796b79663d816e9'])
                            # asBNB
                            self._binance_price['0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] = self._binance_price[
                                vtoken_addr] * ex_rate
                            await self._db.update_binance_price(f"binance_price",
                                                                '0xcc1db43a06d97f736c7b045aedd03c6707c09bdf',
                                                                self._binance_price['0xcc1db43a06d97f736c7b045aedd03c6707c09bdf'] * ex_rate)

            except (ConnectionClosedError, ConnectionResetError, TimeoutError) as e:
                self.Log.error(f"监听价格-发生异常: {e}, 异常类型: {type(e)}, 正在重新连接...")
                retry_delay = min(2 ** config.RETRY_DELAY_PRICE, 30)
                await asyncio.sleep(retry_delay)
                if retry_delay < 30:
                    config.RETRY_DELAY_PRICE += 1
                else:
                    config.RETRY_DELAY_PRICE = 0

    def run(self):
        asyncio.run(self.listen_binance_price_updates())


if __name__ == "__main__":
    monitor = MonitorBinance()
    monitor.run()