from redis.asyncio import Redis

class RedisClient:
    def __init__(self, host='localhost', port=6379, db=0):
        self._db = Redis(host=host, port=port, db=db, decode_responses=True)

    async def save_users(self, name, address_list):
        await self._db.sadd(name, *address_list)
        print(f"保存成功！库内地址数: {await self._db.scard(name)}")

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
        await self._db.set(name, value, ex=ttl, nx=True)

    async def update_cooldown_list(self, name, ttl=600):
        await self._db.set(name, '1', ex=ttl, nx=True)

    async def update_user_asset_map_list(self, name, user_address):
        await self._db.sadd(name, user_address)

    async def get_user_asset_map_list(self, name):
        return await self._db.smembers(name)

    async def update_user_profile(self, name, user_profile):
        await self._db.hset(name, mapping=user_profile)

    async def get_user_profile(self, name):
        return await self._db.hgetall(name)

    async def get_all_users(self, name):
        return await self._db.keys(name)

    async def exist_user_profile(self, name):
        return await self._db.exists(name)

    async def remove_user_profile(self, name):
        await self._db.delete(name)

    async def update_exchange_rate(self, name, value):
        await self._db.set(name, value)

    async def get_exchange_rate(self, name):
        return await self._db.get(name)

    async def update_pair(self, name, key, value):
        await self._db.hset(name, key, value)

    async def get_pair(self, name, key):
        return await self._db.hget(name, key)

    async def get_pairs(self, name):
        return await self._db.hgetall(name)

    async def exist_pair(self, name, key):
        return await self._db.hexists(name, key)

    async def remove_pair(self, name):
        await self._db.delete(name)

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

    async def update_oracle_source(self, name, key, value):
        await self._db.hset(name, key, value)

    async def exist_oracle_source(self, name):
        return await self._db.exists(name)

    async def get_oracle_source(self, name):
        return await self._db.hvals(name)

    async def update_last_block(self, name, value):
        await self._db.set(name, value)

    async def get_last_block(self, name):
        return await self._db.get(name)

    async def update_binance_price(self, name, key, value):
        await self._db.hset(name, key, value)

    async def get_binance_price(self, name, key):
        return await self._db.hget(name, key)

    async def get_binance_prices(self, name):
        return await self._db.hgetall(name)

    async def scan(self, cursor, match, count):
        return await self._db.scan(cursor, match, count)

    def scan_iter(self, match):
        return self._db.scan_iter(match)

    async def set(self, name, value):
        return await self._db.set(name, value)

    async def get(self, name):
        return await self._db.get(name)

    async def delete(self, name):
        return await self._db.delete(name)

    async def exist(self, name):
        return await self._db.exists(name)