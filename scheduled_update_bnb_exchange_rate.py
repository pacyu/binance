import config
import asyncio
from web3client import VenusClient
from redis_client import RedisClient

redis = RedisClient()
client = VenusClient(config.ALCHEMY_BSC_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)

async def main():
    bnb_er = client.get_exchange_rate(config.BNB_ADDRESS)
    await redis.update_exchange_rate(f'rate:{config.BNB_ADDRESS}', bnb_er)

if __name__ == '__main__':
    asyncio.run(main())
