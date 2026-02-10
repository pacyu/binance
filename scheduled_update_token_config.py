import config
import asyncio
from redis_client import RedisClient
from web3client import VenusClient
from multicall import Multicall, Call
from utils import get_binance_symbols


class Run:
    def __init__(self):
        self._db = RedisClient()
        self._client = VenusClient(config.ALCHEMY_BSC_RPC_URL, config.VENUS_CORE_COMPTROLLER_ADDR)

    async def update_token_config(self):
        symbols = get_binance_symbols()
        local_symbols_set = set(s.lower() for s in symbols)

        # 1. 获取所有市场
        comptroller_addr = config.VENUS_CORE_COMPTROLLER_ADDR
        all_markets = await self._client.get_all_markets()

        print(f"📡 发现 Venus 核心池共 {len(all_markets)} 个市场，开始扫描...")

        # 2. 第一轮 Multicall: 获取所有 vToken 的 symbol 和底层资产地址
        calls_v = []
        for addr in all_markets:
            calls_v.append(Call(addr, ['symbol()(string)'], [(f"v_sym_{addr}", lambda x: x)]))
            # 查询 Comptroller 获取抵押因子: markets(address) -> (isListed, collatFactor, isVenus...)
            calls_v.append(
                Call(comptroller_addr, ['markets(address)((bool,uint256,bool))', addr],
                     [(f"market_{addr}", lambda x: x)]))

            if addr.lower() != config.BNB_ADDRESS:
                calls_v.append(Call(addr, ['underlying()(address)'], [(f"und_addr_{addr}", lambda x: x)]))

        res_v = await Multicall(calls_v, _w3=self._client.get_w3()).coroutine()

        # 3. 第二轮 Multicall: 获取底层资产的 decimals 和真正的 symbol
        calls_u = []
        vtoken_to_underlying = {}

        for v_addr in all_markets:
            u_addr = res_v.get(f"und_addr_{v_addr}")
            v_sym = res_v.get(f"v_sym_{v_addr}")
            market_info = res_v.get(f"market_{v_addr}")  # (isListed, cf, isComp)

            cf = market_info[1] / 1e18 if market_info else 0  # 转换为 0.x 格式

            if v_addr.lower() == config.BNB_ADDRESS:
                vtoken_to_underlying[v_addr] = {"sym": "BNB", "dec": 18, "is_native": True, "v_sym": v_sym, "cf": cf}
                # calls_u.append(Call(v_addr, ['getCash()(uint256)'], [(f"cash_{v_addr}", lambda x: x)]))
            elif u_addr:
                calls_u.append(Call(u_addr, ['decimals()(uint8)'], [(f"dec_{v_addr}", lambda x: x)]))
                calls_u.append(Call(u_addr, ['symbol()(string)'], [(f"sym_{v_addr}", lambda x: x)]))
                # calls_u.append(Call(v_addr, ['getCash()(uint256)'], [(f"cash_{v_addr}", lambda x: x)]))
                vtoken_to_underlying[v_addr] = {"u_addr": u_addr, "is_native": False, "v_sym": v_sym, "cf": cf}

        res_u = await Multicall(calls_u, _w3=self._client.get_w3()).coroutine()

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
                "cf": info['cf'],  # 抵押因子
                "is_native": info['is_native'],
                "venus_supported": u_sym.lower() in local_symbols_set,
                "oracle_precision": 10 ** (36 - u_dec),
            }
            await self._db.update_currency_symbol_map(u_sym.lower(), token_dict)
            await self._db.update_currency_address_map(v_addr.lower(), token_dict)
            await self._db.update_currency_map({v_addr.lower(): u_sym.lower()})
            await self._db.update_symbol_map({u_sym.lower(): v_addr.lower()})


if __name__ == '__main__':
    run = Run()
    asyncio.run(run.update_token_config())
