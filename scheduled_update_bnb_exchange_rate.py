import os
import config
import asyncio
from web3client import VenusClient
from redis_client import RedisClient

alchemy_rpc_api_key = os.getenv('ALCHEMY_RPC_API_KEY')
redis = RedisClient()
client = VenusClient(config.CHAINSTACK_RPC_URL % alchemy_rpc_api_key, config.VENUS_CORE_COMPTROLLER_ADDR)

async def main():
    bnb_er = client.get_exchange_rate(config.BNB_ADDRESS)
    await redis.update_exchange_rate(config.BNB_ADDRESS, bnb_er)

if __name__ == '__main__':
    asyncio.run(main())
