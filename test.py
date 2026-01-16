import abi
import json
import config
import asyncio
import websockets
from web3client import VenusClient
from websockets_proxy import Proxy, proxy_connect
from web3 import AsyncWeb3
from web3.providers.persistent import WebSocketProvider
from web3.middleware import ExtraDataToPOAMiddleware

# proxy = Proxy.from_url("http://127.0.0.1:7890")

async def test_borrow_event():
    client = VenusClient(config.NODEREAL_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
    # hash = client.get_topic_hash('MarketedEntered(address,address,uint256,uint256,uint256)')
    hash = await client.get_account_snapshot(['0x523bd2676d1939005d8980864b2dcd5a29cf8a2e'])
    print(hash)

if __name__ == "__main__":
    asyncio.run(test_borrow_event())
