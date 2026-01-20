import json
import config
from multicall import Call, Multicall

class DataManager:
    def __init__(self, client, db, analyzer):
        self._client = client
        self._db = db
        self.analyzer = analyzer

    def scan_user_address(self, start_block, end_block):
        print(f"正在扫描从 {start_block} 到 {end_block} 的链上用户借款日志...")
        user_address_list = self._client.fetch_user_address(start_block, end_block)
        self._db.set('last_block', end_block)
        self._db.save_users("user_address_tab", user_address_list)

    def update_users_profile(self, user_address_list):
        print('正在初始化用户画像...')
        user_profiles = self.analyzer.get_users_snapshot(user_address_list)
        for user_address, user_profile in user_profiles.items():
            if self._db.exist_user_profile(f'user_profile:{user_address}'):
                continue

            if not user_profile:
                continue

            print(user_profile)
            prices = self._client.get_oracle_price(list(user_profile.keys()))
            hf = self.analyzer.calculate_hf(user_profile, prices)
            if hf < 1.2:
                self._db.update_user_hf_in_order('high_risk_queue', {user_address: hf})
            else:
                self._db.remove_user_hf_from_high_risk('high_risk_queue', user_address)
            self._db.update_user_profile(f"user_profile:{user_address}", user_profile)

    def generate_venus_config(self, symbols):
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
            elif u_addr:
                calls_u.append(Call(u_addr, ['decimals()(uint8)'], [(f"dec_{v_addr}", lambda x: x)]))
                calls_u.append(Call(u_addr, ['symbol()(string)'], [(f"sym_{v_addr}", lambda x: x)]))
                vtoken_to_underlying[v_addr] = {"u_addr": u_addr, "is_native": False, "v_sym": v_sym, "cf": cf}

        res_u = Multicall(calls_u, _w3=self._client.get_w3())()

        # 4. 构建 JSON
        for v_addr, info in vtoken_to_underlying.items():
            u_sym = res_u.get(f"sym_{v_addr}", "BNB") if not info['is_native'] else "BNB"
            u_dec = res_u.get(f"dec_{v_addr}", 18) if not info['is_native'] else 18

            token_dict = {
                "symbol": u_sym.lower(),
                "v_symbol": info['v_sym'],
                "address": v_addr.lower(),
                "underlying_decimal": u_dec,
                "cf": info['cf'],  # 新增：抵押因子 (如 0.8)
                "is_native": info['is_native'],
                "venus_supported": u_sym.lower() in local_symbols_set,
                "oracle_precision": 10 ** (36 - u_dec)
            }
            self._db.update_venus_vtoken('venus:assets:symbol', u_sym.lower(), json.dumps(token_dict))
            self._db.update_venus_vtoken('venus:assets:v_addr', v_addr.lower(), json.dumps(token_dict))
            self._db.update_token_to_symbol('vtoken_map', {v_addr.lower(): u_sym.lower()})
            self._db.update_token_to_symbol('symbol_map', {u_sym.lower(): v_addr.lower()})



