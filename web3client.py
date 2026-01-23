import abi
import config
from web3 import Web3, HTTPProvider
from web3.contract import Contract
from eth_account import Account
from multicall import Call, Multicall
from hexbytes import HexBytes
from eth_typing import BlockNumber, ChecksumAddress
from web3.types import LogReceipt, Wei, TxReceipt, FilterParams
from web3.contract.contract import ContractEvents
from typing import List, Dict, Tuple, Optional

AccountSnapshot = Dict[str, Tuple[int, int, int, int]]

class VenusClient:
    def __init__(self, rpc_url: str, comptroller_addr: str, private_key: Optional[str]=None):
        self._w3 = Web3(HTTPProvider(rpc_url))
        self.comptroller_addr = comptroller_addr
        self.private_key = private_key

        if private_key:
            key = private_key if private_key.startswith("0x") else "0x" + private_key
            self.account = Account.from_key(key)
            self.account_address = self.account.address

        self.oracle_address = self.get_oracle_address()

    def get_w3(self) -> Web3:
        return self._w3

    def get_contract(self, address: str, _abi: List) -> Contract:
        """
        获取合约实例。

        :param address: 合约的链上地址
        :param _abi: 接口查询ABI
        :return: 合约实例 Contract
        """
        return self._w3.eth.contract(address=self.to_checksum_address(address), abi=_abi)

    def to_checksum_address(self, address: str) -> ChecksumAddress:
        return self._w3.to_checksum_address(address)

    def get_logs(self, filter_params: FilterParams) -> List[LogReceipt]:
        return self._w3.eth.get_logs(filter_params)

    def get_event(self) -> ContractEvents:
        contract = self._w3.eth.contract(abi=abi.event_abi)
        return contract.events

    def get_block_number(self) -> BlockNumber:
        return self._w3.eth.block_number

    def get_topic_hash(self, signature: str) -> str:
        return self._w3.keccak(text=signature).hex()

    def fetch_user_address(self, start_block: int, end_block: int) -> List[str]:
        logs = self.get_logs({
            "fromBlock": start_block,
            "toBlock": end_block,
            "address": self.to_checksum_address(config.VENUS_CORE_COMPTROLLER_ADDR),
            "topics": [config.TOPICS['MarketEnter']]
        })
        user_address_list = []
        for log in logs:
            user = "0x" + log['topics'][2].hex()[-40:]
            user_address_list.append(user)
        return user_address_list

    def get_gas_price(self) -> Wei:
        return self._w3.eth.gas_price

    def get_oracle_address(self) -> str:
        """
        从 Comptroller 合约动态获取当前预言机地址。

        :return: 42位以 0x 开头的地址字符串。
        """
        comp_contract = self.get_contract(self.comptroller_addr, abi.oracle_abi)
        oracle_addr = comp_contract.functions.oracle().call()
        return self.to_checksum_address(oracle_addr)

    async def get_account_snapshot(self, user_address_or_list: List[str])-> AccountSnapshot:
        """
        获取用户在 Venus 协议中的快照数据。

        :param user_address_or_list: 列表 (多个地址)
        :return: 字典 {vtoken_address: (error, vBal, bBal, exRate)}
        """
        signature = 'getAccountSnapshot(address)((uint256,uint256,uint256,uint256))'

        asset_calls = [
            Call(
                self.comptroller_addr,
                ['getAssetsIn(address)(address[])', user_address],
                [(user_address, lambda x: x)]
            ) for user_address in user_address_or_list]
        assets_map = await Multicall(asset_calls, _w3=self._w3).coroutine()

        calls = []
        for user_address, assets in assets_map.items():
            for vtoken_addr in assets:
                calls.append(
                    Call(vtoken_addr, [signature, user_address], [(f"{user_address}|{vtoken_addr}", lambda x: x)])
                )
        snapshots = await Multicall(calls, _w3=self._w3).coroutine()
        return snapshots

    def get_vsymbol(self, vtoken_address: str)-> str:
        contract = self.get_contract(vtoken_address, abi.vtoken)
        return contract.functions.symbol().call()

    def get_symbol(self, vtoken_address: str)-> str:
        contract = self.get_contract(vtoken_address, abi.vtoken)
        v_contract = self.get_contract(contract.functions.underlying().call(), abi.vtoken)
        return v_contract.functions.symbol().call()

    def get_all_markets(self) -> List[str]:
        contract = self.get_contract(config.VENUS_CORE_COMPTROLLER_ADDR, abi.comptroller)
        return contract.functions.getAllMarkets().call()

    def get_assets_in(self, user_address: str) -> List[str]:
        """
        获取用户的所有资产地址

        :param user_address: 用户地址
        :return: list 资产地址列表
        """
        comp_contract = self.get_contract(config.VENUS_CORE_COMPTROLLER_ADDR, abi.comptroller)
        return comp_contract.functions.getAssetsIn(self.to_checksum_address(user_address)).call()

    def get_exchange_rate(self, vtoken_address: str) -> float:
        contract = self.get_contract(vtoken_address, abi.exchange_rate_abi)
        return contract.functions.exchangeRateStored().call()

    def get_cash(self, v_address: str) -> float:
        v_contract = self.get_contract(v_address, abi.erc20_abi)
        return v_contract.functions.getCash().call()

    def get_pair(self, address: str) -> str:
        contract = self.get_contract(config.PANCAKE_FACTORY_ADDR, abi.pair_abi)
        return contract.functions.getPair(self.to_checksum_address(address), self.to_checksum_address(config.USDT_VTOKEN_ADDRESS)).call()

    def get_reserves(self, address: str):
        contract = self.get_contract(address, abi.reserves_abi)
        reserves = contract.functions.getReserves().call()
        token0 = contract.functions.token0().call()
        token1 = contract.functions.token1().call()
        return reserves, token0, token1

    def get_dex_depth_score(self, v_address: str) -> float:
        if v_address.lower() == config.USDT_VTOKEN_ADDRESS.lower():
            return 1e99

        pair_addr = self.get_pair(v_address)
        if pair_addr == '0x0000000000000000000000000000000000000000':
            return 0.0

        reserves, token0, token1 = self.get_reserves(pair_addr)
        if v_address.lower() == token0.lower():
            usdt_reserve = reserves[0]
        elif v_address.lower() == token1.lower():
            usdt_reserve = reserves[1]
        else:
            return 0.0

        return usdt_reserve / 1e18

    def get_vtoken(self, v_addr: str) -> dict:
        """
        获取vToken的底层基本信息

        :param v_addr: vToken 地址
        :return: 字典
        """
        # 1. 初始化合约
        comp_contract = self.get_contract(self.comptroller_addr, abi.comptroller)
        v_contract = self.get_contract(v_addr, abi.vtoken)

        # 2. 获取 vToken 信息
        v_symbol = v_contract.functions.symbol().call()

        # 3. 特殊处理原生代币 (BNB)
        if v_addr.lower() == "0xa07c5b74c9b40447a954e1466938b865b6bbea36":
            symbol = "BNB"
            underlying_decimal = 18
            is_native = True
        else:
            underlying_addr = v_contract.functions.underlying().call()
            u_contract = self.get_contract(underlying_addr, abi.erc20_abi)
            underlying_decimal = u_contract.functions.decimals().call()
            raw_symbol = u_contract.functions.symbol().call()
            symbol = raw_symbol.replace(" ", "")  # 某些代币符号带空格
            is_native = False

        # 4. 从 Comptroller 获取抵押因子 (CF)
        # markets 返回值是一个元组，通常 index 1 是 collateralFactorMantissa
        market_info = comp_contract.functions.markets(self.to_checksum_address(v_addr)).call()
        cf = market_info[1] / 1e18  # 转换为 0.825 这种格式

        # 5. 构建你的数据结构
        return {
            "symbol": symbol.lower(),
            "v_symbol": v_symbol,
            "address": v_addr.lower(),
            "underlying_decimal": underlying_decimal,
            "cf": cf,
            "is_native": is_native,
            "venus_supported": True,
            "oracle_precision": 10 ** (36 - underlying_decimal)
        }

    async def get_oracle_price(self, vtoken_or_list: List[str]) -> Dict[str, int]:
        """
        获取 vToken 对应底层资产的预言机价格。
        注意:
        Venus Oracle 返回的价格公式为: Value_in_USD = (Asset_Amount * Oracle_Price) / 10^{18}

        函数签名详细说明:
        :param vtoken_or_list: 列表 ["0x...", "0x..."]
        :return: 字典地址: { "vToken地址": uint256价格 }
        """
        signature = 'getUnderlyingPrice(address)(uint256)'

        calls = [
            Call(self.oracle_address, [signature, vtoken], [(vtoken, lambda x: x)])
            for vtoken in vtoken_or_list
        ]

        return await Multicall(calls, _w3=self._w3).coroutine()  # 返回 {vtoken_address: price}

    async def get_user_liquidity(self, user_address_list: List[str]) -> Dict[str, tuple]:
        """
        直接获取用户的清算缺口 (Shortfall)

        :param user_address_list: 列表 (多个地址)
        :return: { address: (error, liquidity, shortfall) }
        """
        signature = 'getAccountLiquidity(address)((uint256,uint256,uint256))'
        calls = [
            Call(self.comptroller_addr,[signature, user_address],[(user_address, lambda x: x)])
            for user_address in user_address_list
        ]
        return await Multicall(calls, _w3=self._w3).coroutine()

    # def get_liquidation_incentive(self) -> float:
    #     """
    #     获取清算奖励比例。
    #
    #     :return: float
    #     """
    #     contract = self.get_contract(self.comptroller_addr, abi.incentive_mantissa_abi)
    #     mantissa = contract.functions.liquidationIncentiveMantissa().call()
    #     return mantissa / 10 ** 18

    def wait_for_transaction_receipt(self, tx_hash) -> TxReceipt:
        return self._w3.eth.wait_for_transaction_receipt(tx_hash)

    def get_transaction_count(self) -> int:
        return self._w3.eth.get_transaction_count(self.to_checksum_address(self.account_address), 'pending')

    def send_liquidation_tx(self,
                            user_address: str,
                            amount: int,
                            is_native: bool,
                            vtoken_debt_address: str,
                            vtoken_collateral_address: str,
                            gas_multiplier: float=1.1) -> HexBytes:
        """
        提交清算交易到区块链。

        :param user_address: 被清算人的钱包地址
        :param amount: 你代为偿还的金额 (单位为 Wei)
        :param is_native: 用户欠的币种是否为本位币BNB
        :param vtoken_debt_address: 被清算人欠款的 vToken 合约地址 (如 vUSDT)
        :param vtoken_collateral_address: 你想拿走的抵押品 vToken 合约地址 (如 vBNB)
        :param gas_multiplier: 在当前网络 Gas 价格基础上的加价倍数
        :return: 交易哈希值 (Transaction Hash)
        """
        if not self.private_key:
            raise ValueError("Private key is required for sending transactions.")

        v_contract = self.get_contract(vtoken_debt_address, abi.vtoken)

        # 自动管理 Nonce
        nonce = self.get_transaction_count()

        tx_params = {}

        if is_native: # 如果是 BNB
            tx_params['value'] = amount
            lb = v_contract.functions.liquidateBorrow(
                user_address, vtoken_collateral_address
            )
        else:
            lb = v_contract.functions.liquidateBorrow(
                user_address, amount, vtoken_collateral_address
            )

        try:
            # 预估 Gas (防止浪费钱)
            gas_limit = lb.estimate_gas({'from': self.account_address})
        except Exception as e:
            raise Exception(f"Gas estimation failed: {e}")

        tx_params.update({
            'from': self.account_address,
            'nonce': nonce,
            'gas': int(gas_limit * 1.2),  # 20% 冗余
            'gasPrice': int(self.get_gas_price() * gas_multiplier),
            'chainId': 56
        })

        tx = lb.build_transaction(tx_params)

        signed_tx = self._w3.eth.account.sign_transaction(tx, self.private_key)
        return self._w3.eth.send_raw_transaction(signed_tx.rawTransaction)

    def simulate_liquidation_tx(self,
                                user_address: str,
                                amount: int,
                                is_native: bool,
                                vtoken_debt_address: str,
                                vtoken_collateral_address: str) -> bool:
        """
        模拟提交清算交易。

        :param user_address: 被清算人的钱包地址
        :param amount: 你代为偿还的金额 (单位为 Wei)
        :param is_native: 用户欠的币种是否为本位币BNB
        :param vtoken_debt_address: 被清算人欠款的 vToken 合约地址 (如 vUSDT)
        :param vtoken_collateral_address: 你想拿走的抵押品 vToken 合约地址 (如 vBNB)
        :return: bool
        """
        # 实例化债务代币的 vToken 合约
        # 如果债务是 BNB，调用 vBNB 合约；如果是其他，调用 vERC20 合约
        vtoken_contract = self.get_contract(vtoken_debt_address, abi.vtoken)

        # 准备调用参数
        # 注意：不同 vToken 的 liquidateBorrow 签名略有不同
        if is_native:
            # vBNB.liquidateBorrow(borrower, vTokenCollateral)
            # 注意：vBNB 清算需要发送 value，但 .call 同样可以模拟这个行为
            call_function = vtoken_contract.functions.liquidateBorrow(
                self.to_checksum_address(user_address),
                self.to_checksum_address(vtoken_collateral_address)
            )
            # 模拟时需要带上 value 字段
            res = call_function.call({
                'from': self.account_address,
                'value': amount
            })
        else:
            # vERC20.liquidateBorrow(borrower, repayAmount, vTokenCollateral)
            call_function = vtoken_contract.functions.liquidateBorrow(
                self.to_checksum_address(user_address),
                amount,
                self.to_checksum_address(vtoken_collateral_address)
            )
            res = call_function.call({'from': self.account_address})

        # Venus 的 liquidateBorrow 如果执行成功通常返回 0 (Error.NO_ERROR)
        # 如果返回非 0 值，说明逻辑错误（如：清算额度超过限制等）
        if res == 0:
            return True
        else:
            return False

    def ensure_unlimited_approval(self, underlying_addr, vtoken_addr, current_nonce):
        """
        检查并执行单笔授权
        """

        # 使用 ERC20 ABI 实例化底层代币
        token_contract = self.get_contract(underlying_addr, abi.erc20_abi)
        allowance = token_contract.functions.allowance(
            self.to_checksum_address(self.account_address), self.to_checksum_address(vtoken_addr)).call()

        # 如果授权额度小于 1 亿美金 (安全阈值)，则重新授权
        if allowance < 10 ** 8 * 10 ** 18:
            max_uint256 = 2 ** 256 - 1
            tx = (token_contract.functions.approve(self.to_checksum_address(vtoken_addr), max_uint256)
            .build_transaction({
                'from': self.account_address,
                'nonce': current_nonce,
                'gasPrice': self.get_gas_price()
            }))

            signed_tx = self._w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self._w3.eth.send_raw_transaction(signed_tx.rawTransaction)

            # 这里不使用 wait_for_receipt 阻塞，直接增加 nonce 发下一笔
            return tx_hash, allowance, current_nonce + 1
        # 否则就不需要授权，表示额度还够
        return None, allowance, current_nonce