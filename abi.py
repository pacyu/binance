oracle_abi = [
    {
        "constant": True,
        "inputs": [],
        "name": "oracle",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function"
    }
]

comptroller_abi = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "getAssetsIn",
        "outputs": [{"name": "", "type": "address[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "", "type": "address"}],
        "name": "markets",
        "outputs": [
            {"name": "isListed", "type": "bool"},
            {"name": "collateralFactorMantissa", "type": "uint256"},  # 质押率 (通常是 18 位精度)
            {"name": "isVenus", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getAllMarkets",
        "outputs": [{"internalType": "contract VToken[]", "name": "", "type": "address[]"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }]

incentive_mantissa_abi = [{
    "constant": True,
    "inputs": [],
    "name": "liquidationIncentiveMantissa",
    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
    "stateMutability": "view",
    "type": "function"
}]

exchange_rate_abi = [{
    "constant": True,
    "inputs": [],
    "name": "exchangeRateStored",
    "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
    "payable": False,
    "stateMutability": "view",
    "type": "function"
}]

liquidate_borrow_abi = [
    {
        "name": "liquidateBorrow",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "borrower",
                "type": "address"
            },
            {
                "name": "repayAmount",
                "type": "uint256"
            },
            {
                "name": "cTokenCollateral",
                "type": "address"
            }
        ],
        "outputs": [
            {
                "name": "",
                "type": "uint256"
            }
        ]
    },
    {
        "name": "liquidateBorrow",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "borrower",
                "type": "address"
            },
            {
                "name": "cTokenCollateral",
                "type": "address"
            }
        ],
        "outputs": [
            {
                "name": "",
                "type": "uint256"
            }
        ]
    }
]

event_abi = [
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
    },
    {
        "anonymous": False,
        'inputs': [
            {'type': 'address', 'name': 'payer', 'indexed': False},  # 实际支付还款的地址
            {'type': 'address', 'name': 'borrower', 'indexed': False},  # 债务被偿还的借款人地址
            {'type': 'uint256', 'name': 'repayAmount', 'indexed': False},  # 偿还的底层资产数量
            {'type': 'uint256', 'name': 'accountBorrowsNew', 'indexed': False},  # 借款人新的借款余额
            {'type': 'uint256', 'name': 'totalBorrowsNew', 'indexed': False},  # 该市场新的总借款余额
        ],
        'name': 'RepayBorrow',
        'type': 'event'
    },
    {
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
    },
    {
        "anonymous": False,
        'inputs': [
            {'type': 'address', 'name': 'redeemer', 'indexed': False},
            {'type': 'uint256', 'name': 'redeemAmount', 'indexed': False},  # 取回的底层资产数量
            {'type': 'uint256', 'name': 'redeemTokens', 'indexed': False},  # 销毁的vToken数量
        ],
        'name': 'Redeem',
        'type': 'event'
    },
    {
        "anonymous": False,
        'inputs': [
            {'type': 'address', 'name': 'redeemer', 'indexed': True},
            {'type': 'uint256', 'name': 'redeemAmount', 'indexed': False},  # 取回的底层资产数量
            {'type': 'uint256', 'name': 'redeemTokens', 'indexed': False},  # 销毁的vToken数量
        ],
        'name': 'Redeem',
        'type': 'event'
    },
    {
        "anonymous": False,
        'inputs': [
            {'type': 'address', 'name': 'account', 'indexed': True},  # 实际进入市场的用户账户
            {'type': 'address', 'name': 'vToken', 'indexed': True},  # 进入的vToken市场地址
        ],
        'name': 'MarketEntered',
        'type': 'event'
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "internalType": "int256", "name": "current", "type": "int256"},
            {"indexed": False, "internalType": "uint256", "name": "roundId", "type": "uint256"},
            {"indexed": False, "internalType": "uint256", "name": "updatedAt", "type": "uint256"}
        ],
        "name": "AnswerUpdated",
        "type": "event"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "uint32", "name": "aggregatorRoundId", "type": "uint32"},
            {"indexed": False, "internalType": "int192", "name": "answer", "type": "int192"},
            {"indexed": False, "internalType": "address", "name": "transmitter", "type": "address"},
            {"indexed": False, "internalType": "uint32", "name": "observationsTimestamp", "type": "uint32"},
            {"indexed": False, "internalType": "int192[]", "name": "observations", "type": "int192[]"},
            {"indexed": False, "internalType": "bytes", "name": "observers", "type": "bytes"},
            {"indexed": False, "internalType": "int192", "name": "juelsPerFeeCoin", "type": "int192"},
            {"indexed": False, "internalType": "bytes32", "name": "configDigest", "type": "bytes32"},
            {"indexed": False, "internalType": "uint40", "name": "epochAndRound", "type": "uint40"}
        ],
        "name": "NewTransmission",
        "type": "event"
    }
]

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
        "name": "underlying",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "address"
            }
        ]
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "getCash",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]

pair_abi = [
    {
        "constant": True,
        "inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}],
        "name": "getPair",
        "outputs": [{"name": "", "type": "address"}],
        "type": "function"
    }
]

reserves_abi = [
    {
        "constant": True,
        "inputs": [],
        "name": "getReserves",
        "outputs": [
            {"name": "_reserve0", "type": "uint112"},
            {"name": "_reserve1", "type": "uint112"},
            {"name": "_blockTimestampLast", "type": "uint32"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "token1",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function",
    },
]

router_abi = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsIn",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"}
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

proxy_abi = [
    {
        "inputs": [],
        "name": "description",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "aggregator",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
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
    },
]
contract_abi = [
    {
        "type": "constructor",
        "inputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "receive",
        "stateMutability": "payable"
    },
    {
        "type": "function",
        "name": "Owner",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "address",
                "internalType": "address"
            }
        ],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "ROUTER",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "address",
                "internalType": "address"
            }
        ],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "VBNB",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "address",
                "internalType": "address"
            }
        ],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "WBNB",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "address",
                "internalType": "address"
            }
        ],
        "stateMutability": "view"
    },
    {
        "type": "function",
        "name": "execute",
        "inputs": [
            {
                "name": "pair",
                "type": "address",
                "internalType": "address"
            },
            {
                "name": "params",
                "type": "tuple",
                "internalType": "struct AlphaLiquidator.LiquidationParams",
                "components": [
                    {
                        "name": "borrower",
                        "type": "address",
                        "internalType": "address"
                    },
                    {
                        "name": "repayAmount",
                        "type": "uint256",
                        "internalType": "uint256"
                    },
                    {
                        "name": "vDebt",
                        "type": "address",
                        "internalType": "address"
                    },
                    {
                        "name": "vCollateral",
                        "type": "address",
                        "internalType": "address"
                    },
                    {
                        "name": "path",
                        "type": "address[]",
                        "internalType": "address[]"
                    },
                    {
                        "name": "maxInput",
                        "type": "uint256",
                        "internalType": "uint256"
                    },
                    {
                        "name": "minProfit",
                        "type": "uint256",
                        "internalType": "uint256"
                    },
                    {
                        "name": "dUnd",
                        "type": "address",
                        "internalType": "address"
                    },
                    {
                        "name": "cUnd",
                        "type": "address",
                        "internalType": "address"
                    }
                ]
            }
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "pancakeCall",
        "inputs": [
            {
                "name": "sender",
                "type": "address",
                "internalType": "address"
            },
            {
                "name": "a0",
                "type": "uint256",
                "internalType": "uint256"
            },
            {
                "name": "a1",
                "type": "uint256",
                "internalType": "uint256"
            },
            {
                "name": "data",
                "type": "bytes",
                "internalType": "bytes"
            }
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "rescue",
        "inputs": [
            {
                "name": "token",
                "type": "address",
                "internalType": "address"
            }
        ],
        "outputs": [],
        "stateMutability": "nonpayable"
    },
    {
        "type": "function",
        "name": "rescueBNB",
        "inputs": [],
        "outputs": [],
        "stateMutability": "nonpayable"
    }
]
