import abi
import json
import config
import asyncio
import websockets
from logger import Logger
from analyzer import Analyzer
from web3client import VenusClient
from redis_client import RedisClient
from utils import get_realtime_prices, price_to_wei
from websockets_proxy import Proxy, proxy_connect

# proxy = Proxy.from_url("http://127.0.0.1:7890")

async def test_analyzer():
    client = VenusClient(config.NODEREAL_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
    redis_client = RedisClient()
    analyzer = Analyzer(client, redis_client, Logger()())
    # hash = client.get_topic_hash('MarketedEntered(address,address,uint256,uint256,uint256)')
    price = {}
    for item in get_realtime_prices():
        if item['symbol'].endswith('USDT'):
            symbol = item['symbol'].replace('USDT', '')
            token = redis_client.get_vtoken('venus:assets:symbol', symbol)
            if token:
                token = json.loads(token)
                price[token['address']] = price_to_wei(item['price'])
    token = json.loads(redis_client.get_vtoken('venus:assets:symbol', 'USDT'))
    price[token['address']] = 1
    report = await analyzer.analyze_user('0x76edb2236c9b58e45ab0b4bc5c462f6f1e52827d', price)
    print(report)

if __name__ == "__main__":
    asyncio.run(test_analyzer())
