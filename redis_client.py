import redis

class RedisClient:
    def __init__(self, host='localhost', port=6379, db=0):
        self._db = redis.Redis(host=host, port=port, db=db, decode_responses=True)

    def save_users(self, name, address_list):
        self._db.sadd(name, *address_list)
        print(f"保存成功！库内地址数: {self._db.scard(name)}")

    def update_user_hf_in_order(self, name, item):
        self._db.zadd(name, item)

    def get_user_hf_by_score(self, name, x, y, withscores=False):
        return self._db.zrangebyscore(name, x, y, withscores=withscores)

    def remove_user_hf_from_high_risk(self, name, user_address):
        self._db.zrem(name, user_address)

    def remove_user_hf_by_score(self, name, x, y):
        self._db.zremrangebyscore(name, x, y)

    def update_user_asset_map_list(self, name, user_address):
        self._db.sadd(name, user_address)

    def update_user_profile(self, name, user_profile):
        self._db.hset(name, mapping=user_profile)

    def get_user_profile(self, name):
        return self._db.hgetall(name)

    def exist_user_profile(self, name):
        return self._db.exists(name)

    def remove_user_profile(self, name):
        self._db.delete(name)

    def update_pair_pool(self, name, item):
        self._db.hset(name, mapping=item)

    def get_pair(self, name, key):
        return self._db.hget(name, key)

    def get_pair_pool(self, name):
        return self._db.hgetall(name)

    def update_venus_vtoken(self, name, key, value):
        self._db.hset(name, key, value)

    def update_token_to_symbol(self, name, item):
        self._db.hset(name, mapping=item)

    def read_by_name(self, name):
        return self._db.smembers(name)

    def get_all_symbols(self):
        return self._db.hvals("asset:vtoken_map")

    def get_all_tokens(self):
        return self._db.hvals("asset:symbol_map")

    def get_tokens_by_symbols(self, name, symbols):
        return self._db.hmget(name, symbols)

    def get_symbols_by_tokens(self, name, tokens):
        return self._db.hmget(name, tokens)

    def vtoken_exists(self, name, address):
        return self._db.hexists(name, address)

    def get_markets(self, name):
        return self._db.hvals(name)

    def get_vtoken(self, name, key):
        return self._db.hget(name, key)

    def get_symbol(self, name, key):
        return self._db.hget(name, key)

    def set(self, name, value):
        self._db.set(name, value)
        print("保存成功！")

    def get(self, name):
        return self._db.get(name)


# if __name__ == "__main__":
#     controller = RedisClient()
#     controller.save_user("user_addresses", "0x86055C7ae3719B9ecD6e0fB85dF2CEaE7bfc409C")