oracle_abi = [{
    "constant":True,
    "inputs":[],
    "name":"oracle",
    "outputs":[{"name":"","type":"address"}],
    "type":"function"
}]
comptroller = [{
    "constant": True,
    "inputs": [{"name": "account","type": "address"}],
    "name": "getAssetsIn",
    "outputs": [{"name": "","type": "address[]"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
},{
    "inputs": [{"name": "", "type": "address"}],
    "name": "markets",
    "outputs": [
        {"name": "isListed", "type": "bool"},
        {"name": "collateralFactorMantissa", "type": "uint256"},  # 质押率 (通常是 18 位精度)
        {"name": "isVenus", "type": "bool"}
    ],
    "stateMutability": "view",
    "type": "function"
},{
    "constant": True,
    "inputs": [],
    "name": "getAllMarkets",
    "outputs": [{"internalType": "contract VToken[]", "name": "", "type": "address[]"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
},{
    "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
    "name": "getAccountLiquidity",
    "outputs": [
        {"internalType": "uint256", "name": "", "type": "uint256"},
        {"internalType": "uint256", "name": "", "type": "uint256"},
        {"internalType": "uint256", "name": "", "type": "uint256"}
    ],
    "stateMutability": "view",
    "type": "function"
}]
# incentive_mantissa_abi = [{
#     "constant":True,
#     "inputs":[],
#     "name":"liquidationIncentiveMantissa",
#     "outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
#     "stateMutability":"view",
#     "type":"function"
# }]
exchange_rate_abi = [{
    "constant":True,
    "inputs":[],
    "name":"exchangeRateStored",
    "outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
    "payable":False,
    "stateMutability":"view",
    "type":"function"
}]
vtoken = [{
    "inputs": [{"internalType": "address", "name": "account", "type": "address"}],
    "name": "getAccountSnapshot",
    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"},
                {"internalType": "uint256", "name": "", "type": "uint256"},
                {"internalType": "uint256", "name": "", "type": "uint256"},
                {"internalType": "uint256", "name": "", "type": "uint256"}],
    "stateMutability": "view",
    "type": "function"
},{
    "name": "symbol",
    "inputs": [],
    "outputs": [{"type": "string"}],
    "stateMutability": "view",
    "type": "function"
},{
    "constant": True,
    "inputs": [],
    "name": "underlying",
    "outputs": [{"internalType": "address", "name": "", "type": "address"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
}]
venus_oracle = [{
    "constant": True,
    "inputs": [{"name": "vToken", "type": "address"}],
    "name": "getUnderlyingPrice",
    "outputs": [{"name": "", "type": "uint256"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
},{
    "constant": True,
    "inputs": [],
    "name": "isPriceOracle",
    "outputs": [{"name": "", "type": "bool"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
}]
event_abi = [
    # {
    #     "anonymous": False,
    #     "inputs": [
    #         {"indexed": True, "internalType": "address", "name": "minter", "type": "address"},
    #         {"indexed": False, "internalType": "uint256", "name": "mintAmount", "type": "uint256"},
    #         {"indexed": False, "internalType": "uint256", "name": "mintTokens", "type": "uint256"}
    #     ],
    #     "name": "Mint",
    #     "type": "event"
    # },
    {
    "anonymous": False,
    "inputs": [
        {"indexed": False, "internalType": "address", "name": "borrower", "type": "address"},
        {"indexed": False, "internalType": "uint256", "name": "borrowAmount", "type": "uint256"},
        {"indexed": False, "internalType": "uint256", "name": "accountBorrows", "type": "uint256"},
        {"indexed": False, "internalType": "uint256", "name": "totalBorrows", "type": "uint256"}
    ],
    "name": "Borrow",
    "type": "event"
},{
    "anonymous": False,
    'inputs': [
        {'type': 'address', 'name': 'payer', 'indexed': False}, # 实际支付还款的地址
        {'type': 'address', 'name': 'borrower', 'indexed': False}, # 债务被偿还的借款人地址
        {'type': 'uint256', 'name': 'repayAmount', 'indexed': False}, # 偿还的底层资产数量
        {'type': 'uint256', 'name': 'accountBorrowsNew', 'indexed': False}, # 借款人新的借款余额
        {'type': 'uint256', 'name': 'totalBorrowsNew', 'indexed': False}, # 该市场新的总借款余额
    ],
    'name': 'RepayBorrow',
    'type': 'event'
},{
    "anonymous": False,
    'inputs': [
        {'type': 'address', 'name': 'liquidator', 'indexed': False},
        {'type': 'address', 'name': 'borrower', 'indexed': False},
        {'type': 'uint256', 'name': 'repayAmount', 'indexed': False},
        {'type': 'address', 'name': 'vTokenCollateral', 'indexed': False},
        {'type': 'uint256', 'name': 'seizeTokens', 'indexed': False},
    ],
    'name': 'LiquidateBorrow',
    'type': 'event'
},{
    "anonymous": False,
    'inputs': [
        {'type': 'address', 'name': 'redeemer', 'indexed': False},
        {'type': 'uint256', 'name': 'redeemAmount', 'indexed': False}, # 取回的底层资产数量
        {'type': 'uint256', 'name': 'redeemTokens', 'indexed': False}, # 销毁的vToken数量
    ],
    'name': 'Redeem',
    'type': 'event'
},{
    "anonymous": False,
    'inputs': [
        {'type': 'address', 'name': 'user', 'indexed': False}, # 实际进入市场的用户账户
        {'type': 'address', 'name': 'market', 'indexed': False}, # 进入的vToken市场地址
        {'type': 'uint256', 'name': 'collateralBalance', 'indexed': False}, # 抵押品余额
        {'type': 'uint256', 'name': 'borrowBalance', 'indexed': False}, # 借款余额
        {'type': 'uint256', 'name': 'exchangeRate', 'indexed': False}, # 汇率
    ],
    'name': 'MarketEntered',
    'type': 'event'
}]
erc20_abi = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "_owner", "type": "address"},
            {"name": "_spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_spender", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    }
]