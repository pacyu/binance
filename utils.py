import requests
from decimal import Decimal

def get_realtime_price(symbol):
    """
    从币安 API 获取实时价格，不消耗 NodeReal 额度
    """
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}USDT"

    price = float(requests.get(url).json()['price'])
    return price

def get_realtime_prices():
    """
    从币安 API 获取实时价格，不消耗 NodeReal 额度
    """
    url = f"https://api.binance.com/api/v3/ticker/price"

    prices = requests.get(url).json()
    return prices

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
    price_decimal = Decimal(price_str)
    return int(price_decimal * Decimal(10 ** 18))

def calc_slippage(amount, r0, r1):
    price_before = r1 / r0
    amount_fee = amount * 9975
    numerator = amount_fee * r1
    denominator = r0 * 10000 + amount_fee
    amount_out = numerator / denominator
    price_after = amount_out / amount
    slippage = 1 - price_after/price_before
    return slippage

def max_liquidatable_amount(r0, r1, max_slippage, precision):
    low = 0
    high = r0 * 0.9
    while high - low > precision:
        mid = (low + high) / 2
        slippage, amount_out = calc_slippage(mid, r0, r1)
        if slippage > max_slippage:
            high = mid
        else:
            low = mid
    return low

def get_price_volatility_threshold(current_price: float) -> float:
    # 针对高价资产（BNB/BTC/ETH）
    if current_price >= 500:
        return 0.0001  # 万分之一 (0.01%)

    # 针对中价资产
    elif current_price >= 10:
        return 0.0003  # 万分之三 (0.03%)

    # 针对低价资产
    elif current_price >= 0.1:
        return 0.001  # 千分之一 (0.1%)

    # 针对极低价资产
    else:
        return 0.005