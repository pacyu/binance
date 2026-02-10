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
        markets = await self._db.get_markets()
        u_address_list = list(map(lambda x: x['underlying_address'], markets.values()))
        for u_addr in u_address_list:
            for v_addr in u_address_list:
                if u_addr != v_addr:
                    pair_address = self._client.get_pair(u_addr, v_addr)
                    if pair_address != '0x0000000000000000000000000000000000000000':
                        await self._db.update_pair(f"pair:{u_addr}", v_addr, pair_address)

    async def update_oracle_sources(self):
        proxy_abi = [
            {
                "inputs": [],
                "name": "description",
                "outputs": [{"internalType": "string", "name": "", "type": "string"}],
                "stateMutability": "view",
                "type": "function",
            },
            {
                "inputs": [],
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "type": "function",
            },
            {
                "inputs": [],
                "name": "aggregator",
                "outputs": [{"internalType": "address", "name": "", "type": "address"}],
                "stateMutability": "view",
                "type": "function"
            },
        ]

        digest_abi = [
            {
                "inputs": [],
                "name": "latestConfigDetails",
                "outputs": [{"name": "configCount", "type": "uint32"},
                            {"name": "blockNumber", "type": "uint32"},
                            {"name": "configDigest", "type": "bytes32"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        for proxy_addr in list(config.PROXY_ADDRESSES):
            proxy_contract = await self._client.get_async_contract(proxy_addr, proxy_abi)
            decimals = await proxy_contract.functions.decimals().call()
            desc = await proxy_contract.functions.description().call()
            agg_addr = await proxy_contract.functions.aggregator().call()

            agg_contract = await self._client.get_async_contract(agg_addr, digest_abi)
            _, _, digest = await agg_contract.functions.latestConfigDetails().call()

            symbol = desc.split(' / ')[0].lower()

            v_address = await self._db.get_v_address_by_symbol(symbol)
            if v_address:
                item = {
                    "aggregator_address": agg_addr,
                    "proxy_address": proxy_addr,
                    "symbol": symbol,
                    "v_address": v_address,
                    "decimals": decimals
                }
                print(f"✅ Digest: {digest.hex()} -> {desc} -> proxy_addr: {proxy_addr} -> Aggregator: {agg_addr}")
                await self._db.save_or_update_digest_mapping(digest.hex(), item)


if __name__ == '__main__':
    from web3client import VenusClient
    from redis_client import RedisClient
    import asyncio

    r = RedisClient()
    client = VenusClient(config.ALCHEMY_BSC_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)
    manager = DataManager(client, r)

    asyncio.run(manager.update_oracle_sources())
