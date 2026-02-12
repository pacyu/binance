from redis.asyncio import Redis

class RedisClient:
    def __init__(self, host='localhost', port=6379, db=0):
        self._db = Redis(host=host, port=port, db=db, decode_responses=True)

    async def save_user_wallet(self, address: str):
        name = "wallet:address"
        await self._db.sadd(name, address)

    async def save_user_wallets(self, address_list: list):
        name = "wallet:address"
        await self._db.sadd(name, *address_list)
        print(f"保存成功！库内地址数: {await self._db.scard(name)}")

    async def exist_user_wallet(self, wallet_address: str):
        name = "wallet:address"
        return await self._db.sismember(name, wallet_address)

    async def get_user_wallets(self):
        name = "wallet:address"
        return await self._db.smembers(name)

    async def save_or_update_user_health_factor(self, item: dict):
        name = "wallet:health_factor"
        await self._db.zadd(name, mapping=item)

    async def get_user_health_factor_by_score(self, x: float, y: float, withscores: bool=False):
        name = "wallet:health_factor"
        return await self._db.zrangebyscore(name, x, y, withscores=withscores)

    async def remove_user_health_factor_by_wallet_address(self, user_address: str):
        name = "wallet:health_factor"
        await self._db.zrem(name, user_address)

    async def remove_user_health_factor_by_score(self, x: float, y: float) -> bool:
        name = "wallet:health_factor"
        return await self._db.zremrangebyscore(name, x, y) > 0

    async def should_skip(self, user_address: str):
        name = f"liquidator:skip:{user_address}"
        return await self._db.exists(name)

    async def mark_as_non_liquidable(self, user_address: str, ttl: int=600, value: str=""):
        name = f"liquidator:skip:{user_address}"
        await self._db.set(name, value, ex=ttl, nx=True)

    async def update_user_asset_map(self, v_address: str, user_address: str):
        name = f'asset:users:{v_address}'
        await self._db.sadd(name, user_address)

    async def get_holder_by_currency(self, v_address: str):
        name = f'asset:users:{v_address}'
        return await self._db.smembers(name)

    async def update_user_profile(self, user_address: str, user_profile: dict):
        name = f"user_profile:{user_address}"
        await self._db.hset(name, mapping=user_profile)

    async def exist_user_profile(self, user_address: str):
        name = f"user_profile:{user_address}"
        return await self._db.exists(name)

    async def get_user_profile(self, user_address: str):
        name = f"user_profile:{user_address}"
        return await self._db.hgetall(name)

    async def get_all_user_addresses(self):
        match = "user_profile:*"
        cursor = 0
        keys = []
        while True:
            cursor, batch = await self._db.scan(cursor=cursor, match=match, count=200)
            keys.extend(batch)
            if cursor == 0:
                break
        return [key.replace('user_profile:', '') for key in keys]

    async def get_user_profiles(self, user_address_list: list):
        pipe = await self._db.pipeline()
        for user_address in user_address_list:
            name = f"user_profile:{user_address}"
            await pipe.hgetall(name)
        profiles = await pipe.execute()
        return dict(zip(user_address_list, profiles))

    async def remove_user_profile(self, user_address: str):
        name = f"user_profile:{user_address}"
        await self._db.delete(name)

    async def delete_asset_from_user_profile(self, user_address: str, asset_address: str):
        name = f"user_profile:{user_address}"
        await self._db.hdel(name, asset_address)

    async def update_exchange_rate(self, v_address: str, value: int):
        name = f"rate:{v_address}"
        await self._db.set(name, value)

    async def get_exchange_rate(self, v_address: str):
        name = f"rate:{v_address}"
        return await self._db.get(name)

    async def update_pair(self, underlying_address: str, key: str, value: str):
        name = f"pair:{underlying_address}"
        await self._db.hset(name, key, value)

    async def get_pair(self, underlying_address: str, key: str):
        name = f"pair:{underlying_address}"
        return await self._db.hget(name, key)

    async def get_pairs(self, underlying_address: str):
        name = f"pair:{underlying_address}"
        return await self._db.hgetall(name)

    async def get_all_pairs(self):
        match = "pair:*"
        cursor = 0
        keys = []
        while True:
            cursor, batch = await self._db.scan(cursor=cursor, match=match, count=200)
            keys.extend(batch)
            if cursor == 0:
                break
        pipe = await self._db.pipeline()
        for key in keys:
            await pipe.hgetall(key)
        results = await pipe.execute()
        return {key.replace('pair:', ''): value for key, value in zip(keys, results)}

    async def exist_pair(self, underlying_address: str, key: str) -> bool:
        name = f"pair:{underlying_address}"
        return await self._db.hexists(name, key)

    async def remove_pair(self, underlying_address: str):
        name = f"pair:{underlying_address}"
        await self._db.delete(name)

    async def update_currency_symbol_map(self, symbol: str, item: dict):
        name = f'currency:symbol:{symbol}'
        await self._db.hset(name, mapping=item)

    async def update_currency_address_map(self, address: str, item: dict):
        name = f'currency:address:{address}'
        await self._db.hset(name, mapping=item)

    async def update_currency_map(self, item: dict):
        name = "currency:map:address:symbol"
        await self._db.hset(name, mapping=item)

    async def update_symbol_map(self, item: dict):
        name = "currency:map:symbol:address"
        await self._db.hset(name, mapping=item)

    async def get_currency_by_symbol(self, symbol: str) -> dict:
        name = f"currency:symbol:{symbol}"
        return await self._db.hgetall(name)

    async def get_symbol_by_address(self, address: str) -> dict:
        name = f"currency:address:{address}"
        return await self._db.hgetall(name)

    async def get_v_address_by_symbol(self, symbol: str):
        name = "currency:map:symbol:address"
        return await self._db.hget(name, symbol)

    async def get_all_symbols(self):
        name = "currency:map:address:symbol"
        return await self._db.hvals(name)

    async def get_all_currencies(self):
        name = "currency:map:symbol:address"
        return await self._db.hvals(name)

    async def get_currencies_by_symbols(self, symbols: list):
        name = "currency:map:symbol:address"
        return await self._db.hmget(name, symbols)

    async def get_symbols_by_currencies(self, currencies: list):
        name = "currency:map:address:symbol"
        return await self._db.hmget(name, currencies)

    async def get_markets(self):
        match = "currency:address:*"
        cursor = 0
        keys = []
        while True:
            cursor, batch = await self._db.scan(cursor=cursor, match=match, count=200)
            keys.extend(batch)
            if cursor == 0:
                break
        pipe = await self._db.pipeline()
        for key in keys:
            await pipe.hgetall(key)
        results = await pipe.execute()
        return {key.replace('currency:address:', ''): value for key, value in zip(keys, results)}

    async def save_or_update_digest_mapping(self, digest: str, item: dict):
        name = f"currency:digest:{digest}"
        await self._db.hset(name, mapping=item)

    async def get_digest_mapping(self, digest: str):
        name = f"currency:digest:{digest}"
        return await self._db.hgetall(name)

    async def get_all_digests(self) -> dict:
        match = "currency:digest:*"
        cursor = 0
        keys = []
        while True:
            cursor, batch = await self._db.scan(cursor=cursor, match=match, count=200)
            keys.extend(batch)
            if cursor == 0:
                break
        pipe = await self._db.pipeline()
        for key in keys:
            await pipe.hgetall(key)
        results = await pipe.execute()
        return {key.replace('currency:digest:', ''): value for key, value in zip(keys, results)}

    async def save_or_update_binance_price(self, item: dict):
        name = f"binance:price"
        await self._db.hset(name, mapping=item)

    async def get_binance_price(self, key: str):
        name = f"binance:price"
        return await self._db.hget(name, key)

    async def get_binance_prices(self):
        name = f"binance:price"
        return await self._db.hgetall(name)
