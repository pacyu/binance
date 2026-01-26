from redis.asyncio import Redis

class RedisClient:
    def __init__(self, host='localhost', port=6379, db=0):
        self._db = Redis(host=host, port=port, db=db, decode_responses=True)

    async def save_users(self, name, address_list):
        await self._db.sadd(name, *address_list)
        print(f"保存成功！库内地址数: {self._db.scard(name)}")

    async def update_user_hf_in_order(self, name, item):
        await self._db.zadd(name, item)

    async def get_user_hf_by_score(self, name, x, y, withscores=False):
        return await self._db.zrangebyscore(name, x, y, withscores=withscores)

    async def remove_user_hf_from_high_risk(self, name, user_address):
        await self._db.zrem(name, user_address)

    async def remove_user_hf_by_score(self, name, x, y):
        await self._db.zremrangebyscore(name, x, y)

    async def should_skip(self, name):
        return await self._db.exists(name)

    async def mark_as_non_liquidable(self, name, ttl=600, value=""):
        await self._db.setex(name, ttl, value)

    async def update_user_asset_map_list(self, name, user_address):
        await self._db.sadd(name, user_address)

    async def update_user_profile(self, name, user_profile):
        await self._db.hset(name, mapping=user_profile)

    async def get_user_profile(self, name):
        return await self._db.hgetall(name)

    async def exist_user_profile(self, name):
        return await self._db.exists(name)

    async def remove_user_profile(self, name):
        await self._db.delete(name)

    async def update_pair_pool(self, name, item):
        await self._db.hset(name, mapping=item)

    async def get_pair(self, name, key):
        return await self._db.hget(name, key)

    async def get_pair_pool(self, name):
        return await self._db.hgetall(name)

    async def update_venus_vtoken(self, name, key, value):
        await self._db.hset(name, key, value)

    async def update_token_to_symbol(self, name, item):
        await self._db.hset(name, mapping=item)

    async def read_by_name(self, name):
        return await self._db.smembers(name)

    async def get_all_symbols(self):
        return await self._db.hvals("asset:vtoken_map")

    async def get_all_tokens(self):
        return await self._db.hvals("asset:symbol_map")

    async def get_tokens_by_symbols(self, name, symbols):
        return await self._db.hmget(name, symbols)

    async def get_symbols_by_tokens(self, name, tokens):
        return await self._db.hmget(name, tokens)

    async def vtoken_exists(self, name, address):
        return await self._db.hexists(name, address)

    async def get_markets(self, name):
        return await self._db.hvals(name)

    async def get_vtoken(self, name, key):
        return await self._db.hget(name, key)

    async def get_symbol(self, name, key):
        return await self._db.hget(name, key)

    async def set(self, name, value):
        await self._db.set(name, value)
        print("保存成功！")

    async def get(self, name):
        return await self._db.get(name)


# if __name__ == "__main__":
#     controller = RedisClient()
#     controller.save_user("user_addresses", "0x86055C7ae3719B9ecD6e0fB85dF2CEaE7bfc409C")