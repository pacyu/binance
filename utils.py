import requests
from decimal import Decimal

def get_realtime_price(symbol):
    """
    从币安 API 获取实时价格，不消耗 NodeReal 额度
    """
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"
    try:
        price = float(requests.get(url).json()['price'])
        return price
    except Exception:
        return None

def get_realtime_prices():
    """
    从币安 API 获取实时价格，不消耗 NodeReal 额度
    """
    url = f"https://api.binance.com/api/v3/ticker/price"
    try:
        prices = requests.get(url).json()
        return prices
    except Exception:
        return None

def get_binance_symbols() -> list:
    url = "https://api.binance.com/api/v3/exchangeInfo"
    try:
        response = requests.get(url)
        data = response.json()

        # 过滤出所有以 USDT 结算的币种名称
        symbols = [s['baseAsset'] for s in data['symbols'] if s['quoteAsset'] == 'USDT']

        print(f"✅ 成功从币安获取了 {len(symbols)} 个代币名称")
        return sorted(list(set(symbols)))
    except Exception as e:
        print(f"❌ 获取失败: {e}")
        return []


def usd_to_wei(optimal_usd, oracle_price_mantissa, token_decimals) -> int:
    """
    optimal_usd: 你的算法算出的最优美金数 (float/decimal)
    oracle_price_mantissa: 从 Oracle 拿到的原始 BigInt 价格
    token_decimals: 该代币的精度 (如 BTCB 是 18)
    """
    # 1. 获取 1 个代币对应的真实美金价格
    # Venus Oracle 价格公式: Price = (Mantissa) / 10^(36 - decimals)
    scaling_factor = Decimal(10) ** (36 - token_decimals)
    token_price_usd = Decimal(oracle_price_mantissa) / scaling_factor

    # 2. 计算需要还多少个代币 (人类单位)
    repay_token_units = Decimal(str(optimal_usd)) / token_price_usd

    # 3. 转换为 Wei (整数)
    repay_amount_wei = int(repay_token_units * (Decimal(10) ** token_decimals))

    return repay_amount_wei

def price_to_wei(price_str: str) -> int:
    # 将字符串转为 Decimal 以保持精度，然后乘以 10^8
    price_decimal = Decimal(price_str)
    # 统一转换为和 Chainlink 相同的 8 位精度整数
    return int(price_decimal * Decimal(10 ** 8))


if __name__ == '__main__':
#     print(get_binance_symbols())
    print(get_realtime_prices())