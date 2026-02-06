import json
import config
from multicall import Call, Multicall
from utils import get_binance_symbols

class DataManager:
    def __init__(self, client, db, analyzer):
        self._client = client
        self._db = db
        self.analyzer = analyzer

    async def scan_user_address(self, start_block, end_block):
        print(f"正在扫描从 {start_block} 到 {end_block} 的链上用户借款日志...")
        user_address_list = self._client.fetch_user_address(start_block, end_block)
        await self._db.update_last_block('last_block', end_block)
        print(user_address_list)
        if user_address_list:
            await self._db.save_user_wallets("wallet_address", user_address_list)

    async def update_users_profile(self, user_address_list):
        print('正在初始化用户画像...')
        user_profiles = await self.analyzer.get_users_snapshot(user_address_list)
        for user_address, user_profile in user_profiles.items():
            await self._db.save_user_wallet("wallet_address", user_address)
            if await self._db.exist_user_profile(f'user_profile:{user_address}'):
                continue

            if not user_profile:
                continue

            print(user_profile)
            prices = await self._client.get_oracle_price(list(user_profile.keys()))
            hf = self.analyzer.calculate_hf(user_profile, prices)
            if hf < 1.3:
                await self._db.update_user_hf_in_order('high_risk_queue', {user_address: hf})
            else:
                await self._db.remove_user_hf_from_high_risk('high_risk_queue', user_address)
            await self._db.update_user_profile(f"user_profile:{user_address}", user_profile)

    async def update_tokens_profile(self):
        symbols = get_binance_symbols()
        local_symbols_set = set(s.lower() for s in symbols)

        comptroller_abi = [{"inputs": [], "name": "getAllMarkets",
                            "outputs": [{"internalType": "contract VToken[]", "name": "", "type": "address[]"}],
                            "stateMutability": "view", "type": "function"}]

        # 1. 获取所有市场
        comptroller_addr = config.VENUS_CORE_COMPTROLLER_ADDR
        comptroller = self._client.get_contract(comptroller_addr, comptroller_abi)
        all_markets = comptroller.functions.getAllMarkets().call()

        print(f"📡 发现 Venus 核心池共 {len(all_markets)} 个市场，开始扫描...")

        # 2. 第一轮 Multicall: 获取所有 vToken 的 Symbol 和底层资产地址
        calls_v = []
        for addr in all_markets:
            calls_v.append(Call(addr, ['symbol()(string)'], [(f"v_sym_{addr}", lambda x: x)]))
            # 查询 Comptroller 获取抵押因子: markets(address) -> (isListed, collatFactor, isVenus...)
            calls_v.append(
                Call(comptroller_addr, ['markets(address)((bool,uint256,bool))', addr],
                     [(f"market_{addr}", lambda x: x)]))

            if addr.lower() != "0xa07c5b74c9b40447a954e1466938b865b6bbea36":
                calls_v.append(Call(addr, ['underlying()(address)'], [(f"und_addr_{addr}", lambda x: x)]))

        res_v = Multicall(calls_v, _w3=self._client.get_w3())()

        # 3. 第二轮 Multicall: 获取底层资产的 Decimals 和真正的 Symbol
        calls_u = []
        vtoken_to_underlying = {}

        for v_addr in all_markets:
            u_addr = res_v.get(f"und_addr_{v_addr}")
            v_sym = res_v.get(f"v_sym_{v_addr}")
            market_info = res_v.get(f"market_{v_addr}")  # (isListed, cf, isComp)

            cf = market_info[1] / 1e18 if market_info else 0  # 转换为 0.x 格式

            if v_addr.lower() == "0xa07c5b74c9b40447a954e1466938b865b6bbea36":
                vtoken_to_underlying[v_addr] = {"sym": "BNB", "dec": 18, "is_native": True, "v_sym": v_sym, "cf": cf}
                # calls_u.append(Call(v_addr, ['getCash()(uint256)'], [(f"cash_{v_addr}", lambda x: x)]))
            elif u_addr:
                calls_u.append(Call(u_addr, ['decimals()(uint8)'], [(f"dec_{v_addr}", lambda x: x)]))
                calls_u.append(Call(u_addr, ['symbol()(string)'], [(f"sym_{v_addr}", lambda x: x)]))
                # calls_u.append(Call(v_addr, ['getCash()(uint256)'], [(f"cash_{v_addr}", lambda x: x)]))
                vtoken_to_underlying[v_addr] = {"u_addr": u_addr, "is_native": False, "v_sym": v_sym, "cf": cf}

        res_u = Multicall(calls_u, _w3=self._client.get_w3())()

        # 4. 构建 JSON
        for v_addr, info in vtoken_to_underlying.items():
            u_sym = res_u.get(f"sym_{v_addr}", "BNB") if not info['is_native'] else "BNB"
            u_dec = res_u.get(f"dec_{v_addr}", 18) if not info['is_native'] else 18

            token_dict = {
                "symbol": u_sym.lower(),
                "v_symbol": info['v_sym'],
                "underlying_address": (config.WBNB_UNDER_ADDRESS if info['is_native'] else info["u_addr"].lower()),
                "address": v_addr.lower(),
                "underlying_decimal": u_dec,
                "cf": info['cf'],  # 新增：抵押因子 (如 0.8)
                "is_native": info['is_native'],
                "venus_supported": u_sym.lower() in local_symbols_set,
                "oracle_precision": 10 ** (36 - u_dec),
            }
            await self._db.update_venus_vtoken('asset:symbol', u_sym.lower(), json.dumps(token_dict))
            await self._db.update_venus_vtoken('asset:v_addr', v_addr.lower(), json.dumps(token_dict))
            await self._db.update_token_to_symbol('asset:vtoken_map', {v_addr.lower(): u_sym.lower()})
            await self._db.update_token_to_symbol('asset:symbol_map', {u_sym.lower(): v_addr.lower()})

    async def update_pair_address(self):
        markets = await self._db.get_markets('asset:v_addr')
        u_address_list = list(map(lambda x: json.loads(x)['underlying_address'], markets))
        for u_addr in u_address_list:
            for v_addr in u_address_list:
                if u_addr != v_addr:
                    pair_address = self._client.get_pair(u_addr, v_addr)
                    if pair_address != '0x0000000000000000000000000000000000000000':
                        await self._db.update_pair(f"pair:{u_addr}", v_addr, pair_address)

    async def update_exchange_rate(self):
        bnb_er = self._client.get_exchange_rate(config.BNB_ADDRESS)
        await self._db.update_exchange_rate(f'rate:{config.BNB_ADDRESS}', bnb_er)

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

        oracle_contract = await self._client.get_contract(config.ORACLE_ADDRESS, abi)

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
            oracle_info = await oracle_contract.functions.getOracle(underlying_addr, 0).call()
            sub_oracle_addr = oracle_info[0]
            print(symbol, "的 Sub-Route 地址:", sub_oracle_addr)

            if sub_oracle_addr == "0x0000000000000000000000000000000000000000":
                continue

            try:
                plugin_contract = await self._client.get_contract(sub_oracle_addr, chainlink_oracle_abi)
                config_data = await plugin_contract.functions.tokenConfigs(underlying_addr).call()
                proxy_address = self._client.to_checksum_address(hex(config_data[1]))


                # 穿透 Proxy
                proxy_contract = await self._client.get_contract(proxy_address, aggregator_abi)
                real_aggregator_address = await proxy_contract.functions.aggregator().call()
                print(symbol, real_aggregator_address)
                await self._db.update_oracle_source(f"oracle:address:{real_aggregator_address}", symbol, v_token_addr.lower())
            except Exception as e:
                print(e)
                continue

