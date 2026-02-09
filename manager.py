import json
import config

class DataManager:
    def __init__(self, client, db):
        self._client = client
        self._db = db

    async def scan_user_address(self, start_block, end_block):
        print(f"正在扫描从 {start_block} 到 {end_block} 的链上用户借款日志...")
        user_address_list = self._client.fetch_user_address(start_block, end_block)
        await self._db.update_last_block('last_block', end_block)
        print(user_address_list)
        if user_address_list:
            await self._db.save_user_wallets("wallet_address", user_address_list)

    async def update_pair_address(self):
        markets = await self._db.get_markets('asset:v_addr')
        u_address_list = list(map(lambda x: json.loads(x)['underlying_address'], markets))
        for u_addr in u_address_list:
            for v_addr in u_address_list:
                if u_addr != v_addr:
                    pair_address = self._client.get_pair(u_addr, v_addr)
                    if pair_address != '0x0000000000000000000000000000000000000000':
                        await self._db.update_pair(f"pair:{u_addr}", v_addr, pair_address)

    async def update_oracle_sources(self):
        abi = [
            {"inputs":[{"internalType":"address","name":"asset","type":"address"},
                       {"internalType":"enum ResilientOracle.OracleRole","name":"role","type":"uint8"}],
             "name":"getOracle",
             "outputs":[{"internalType":"address","name":"oracle","type":"address"},{"internalType":"bool","name":"enabled","type":"bool"}],
             "stateMutability":"view",
             "type":"function"
             }
        ]
        chainlink_oracle_abi = [
            {
                "inputs": [{"internalType": "address", "name": "", "type": "address"}],
                "name": "tokenConfigs",
                "outputs": [
                    {"internalType": "address", "name": "feed", "type": "address"},
                    {"internalType": "uint256", "name": "maxStalePeriod", "type": "uint256"}
                ],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        aggregator_abi = [{"inputs": [], "name": "aggregator",
                           "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                           "stateMutability": "view", "type": "function"}]

        oracle_contract = self._client.get_contract(config.ORACLE_ADDRESS, abi)

        # 1. 获取所有 vToken
        all_markets = await self._client.get_all_markets()
        print(f"🔍 发现 {len(all_markets)} 个市场，正在检索价格源...")
        for v_token_addr in all_markets:
            token = json.loads(await self._db.get_vtoken("asset:v_addr", v_token_addr.lower()))
            symbol = token['symbol']

            # vBNB 的 underlying 处理 (Venus 内部 BNB 地址通常为 0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB)
            if symbol == 'bnb':
                underlying_addr = "0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB"  # BNB
            else:
                underlying_addr = self._client.to_checksum_address(token['underlying_address'])

            # 返回: (oracle_address, is_enabled)
            oracle_info = oracle_contract.functions.getOracle(underlying_addr, 0).call()
            sub_oracle_addr = oracle_info[0]

            if sub_oracle_addr == "0x0000000000000000000000000000000000000000":
                continue

            # try:
            plugin_contract = self._client.get_contract(sub_oracle_addr, chainlink_oracle_abi)
            config_data = plugin_contract.functions.tokenConfigs(underlying_addr).call()
            proxy_address = self._client.to_checksum_address(hex(config_data[1]))
            proxy_contract = self._client.get_contract(proxy_address, aggregator_abi)
            real_aggregator_address = proxy_contract.functions.aggregator().call()
            print(symbol, sub_oracle_addr, real_aggregator_address)
            await self._db.update_oracle_source(f"oracle:address:{real_aggregator_address}", symbol, v_token_addr.lower())
            # except Exception as e:
            #     print(e)
            #     continue

if __name__ == '__main__':
    from web3client import VenusClient
    from redis_client import RedisClient
    import asyncio

    r = RedisClient()
    client = VenusClient(config.ALCHEMY_BSC_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
    manager = DataManager(client, r)

    asyncio.run(manager.update_oracle_sources())

