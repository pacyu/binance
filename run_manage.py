import config
from manager import DataManager
from web3client import VenusClient
from redis_client import RedisClient
from analyzer import Analyzer
from utils import get_binance_symbols
from logger import Logger

def sync_users():
    days = 90  # 3个月
    blocks_per_day = 28800
    total_blocks_to_scan = days * blocks_per_day
    # last_block = redis_db.get('last_block')
    # print(last_block)
    latest_block = int(w3client.get_block_number())
    from_block = latest_block - total_blocks_to_scan
    step = 1000
    for _ in range(from_block, latest_block, step):
        manager.scan_user_address(start_block=_, end_block=_ + step)

def sync_users_profile():
    user_address_list = list(redis_db.read_by_name('user_address_tab'))
    for i in range(0, len(user_address_list), 600):
        manager.update_users_profile(user_address_list[i:i + 600])

def sync_token():
    my_local_symbols = get_binance_symbols()
    manager.generate_venus_config(my_local_symbols)


if __name__ == "__main__":
    w3client = VenusClient(config.ANKR_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
    redis_db = RedisClient()
    analyzer = Analyzer(w3client, redis_db, Logger()())
    manager = DataManager(w3client, redis_db, analyzer)

    sync_token()