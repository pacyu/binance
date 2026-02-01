import abi
import json
import config
import aiohttp
from web3 import Web3, HTTPProvider
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.contract import AsyncContract
from web3.contract.async_contract import AsyncContractEvents
from eth_account import Account
from multicall import Call, Multicall
from hexbytes import HexBytes
from eth_typing import BlockNumber, ChecksumAddress, HexStr
from web3.types import LogReceipt, Wei, TxReceipt, FilterParams, TxData, SignedTx
from typing import List, Dict, Tuple, Optional

AccountSnapshot = Dict[str, Tuple[int, int, int, int]]


class VenusClient:
    def __init__(self,
                 rpc_url: str,
                 comptroller_addr: str,
                 private_key: Optional[str] = None,
                 bloxroute_api_key: Optional[str] = None,
                 bloxroute_auth_header: Optional[str] = None, ):

        self._w3 = Web3(HTTPProvider(rpc_url))
        self._async_w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        self.comptroller_addr = comptroller_addr
        self.private_key = private_key
        self.bloxroute_api_key = bloxroute_api_key
        self.bloxroute_auth_header = bloxroute_auth_header

        if private_key:
            key = private_key if private_key.startswith("0x") else "0x" + private_key
            self.account = Account.from_key(key)
            self.account_address = self.account.address

        self.oracle_address = config.ORACLE_ADDRESS

    def get_w3(self) -> Web3:
        return self._w3

    def get_async_w3(self) -> AsyncWeb3:
        return self._async_w3

    async def get_contract(self, address: str, _abi: List) -> AsyncContract:
        """
        获取合约实例。

        :param address: 合约的链上地址
        :param _abi: 接口查询ABI
        :return: 合约实例 Contract
        """
        return self._async_w3.eth.contract(address=self.to_checksum_address(address), abi=_abi)

    def to_checksum_address(self, address: str) -> ChecksumAddress:
        return self._async_w3.to_checksum_address(address)

    async def get_logs(self, filter_params: FilterParams) -> List[LogReceipt]:
        return await self._async_w3.eth.get_logs(filter_params)

    async def get_event(self) -> AsyncContractEvents:
        contract = self._async_w3.eth.contract(abi=abi.event_abi)
        return contract.events

    async def get_block_number(self) -> BlockNumber:
        return await self._async_w3.eth.block_number

    async def keccak(self, signature: str) -> bytes:
        return self._async_w3.keccak(text=signature)

    async def fetch_user_address(self, start_block: int, end_block: int) -> List[str]:
        logs = await self.get_logs({
            "fromBlock": start_block,
            "toBlock": end_block,
            "address": self.to_checksum_address(config.VENUS_CORE_COMPTROLLER_ADDR),
            "topics": [config.TOPICS['MarketEntered']]
        })
        user_address_list = []
        for log in logs:
            user = "0x" + log['topics'][2].hex()[-40:]
            user_address_list.append(user)
        return user_address_list

    async def get_gas_price(self) -> Wei:
        return await self._async_w3.eth.gas_price

    async def get_oracle_address(self) -> str:
        """
        从 Comptroller 合约动态获取当前预言机地址。

        :return: 42位以 0x 开头的地址字符串。
        """
        comp_contract = await self.get_contract(self.comptroller_addr, abi.oracle_abi)
        return await comp_contract.functions.oracle().call()

    async def get_account_snapshot(self, user_address_or_list: List[str]) -> AccountSnapshot:
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

    async def symbol(self, vtoken_address: str) -> str:
        contract = await self.get_contract(vtoken_address, abi.vtoken)
        return await contract.functions.symbol().call()

    async def underlying(self, vtoken_address: str) -> str:
        contract = await self.get_contract(vtoken_address, abi.vtoken)
        return await contract.functions.underlying().call()

    async def get_all_markets(self) -> List[str]:
        contract = await self.get_contract(config.VENUS_CORE_COMPTROLLER_ADDR, abi.comptroller)
        return await contract.functions.getAllMarkets().call()

    async def get_amounts_in(self, amount: int, path: list):
        router_contract = await self.get_contract(config.ROUTER_ADDRESS, abi.router_abi)
        return await router_contract.functions.getAmountsIn(amount, path).call()

    async def get_assets_in(self, user_address: str) -> List[str]:
        """
        获取用户的所有资产地址

        :param user_address: 用户地址
        :return: list 资产地址列表
        """
        comp_contract = await self.get_contract(config.VENUS_CORE_COMPTROLLER_ADDR, abi.comptroller)
        return await comp_contract.functions.getAssetsIn(self.to_checksum_address(user_address)).call()

    async def get_liquidation_incentive(self, v_address: str) -> float:
        """
        获取清算奖励比例。

        :param v_address: 代币地址
        :return: float
        """
        contract = await self.get_contract(v_address, abi.incentive_mantissa_abi)
        mantissa = await contract.functions.liquidationIncentiveMantissa().call()
        return mantissa / 10 ** 18

    async def get_exchange_rate(self, vtoken_address: str) -> float:
        contract = await self.get_contract(vtoken_address, abi.exchange_rate_abi)
        return await contract.functions.exchangeRateStored().call()

    async def get_cash(self, v_address: str) -> float:
        v_contract = await self.get_contract(v_address, abi.erc20_abi)
        return await v_contract.functions.getCash().call()

    async def get_transaction(self, tx_hash: HexBytes) -> TxData:
        return await self._async_w3.eth.get_transaction(tx_hash)

    async def get_raw_transaction(self, tx_hash: HexBytes) -> HexBytes:
        return await self._async_w3.eth.get_raw_transaction(tx_hash)

    async def get_pair(self, address0: str, address1: str) -> str:
        contract = await self.get_contract(config.PANCAKE_FACTORY_ADDR, abi.pair_abi)
        return await contract.functions.getPair(self.to_checksum_address(address0),
                                          self.to_checksum_address(address1)).call()

    async def get_reserves(self, address: str) -> Tuple:
        contract = await self.get_contract(address, abi.reserves_abi)
        reserves = await contract.functions.getReserves().call()
        token0 = await contract.functions.token0().call()
        token1 = await contract.functions.token1().call()
        return reserves, token0, token1

    async def get_vtoken(self, v_addr: str) -> dict:
        """
        获取vToken的底层基本信息

        :param v_addr: vToken 地址
        :return: 字典
        """
        # 1. 初始化合约
        comp_contract = await self.get_contract(self.comptroller_addr, abi.comptroller)
        v_contract = await self.get_contract(v_addr, abi.vtoken)

        # 2. 获取 vToken 信息
        v_symbol = await v_contract.functions.symbol().call()
        underlying_addr = await v_contract.functions.underlying().call()

        # 3. 特殊处理原生代币 (BNB)
        if v_addr.lower() == "0xa07c5b74c9b40447a954e1466938b865b6bbea36":
            symbol = "BNB"
            underlying_decimal = 18
            is_native = True
        else:
            u_contract = await self.get_contract(underlying_addr, abi.erc20_abi)
            underlying_decimal = await u_contract.functions.decimals().call()
            raw_symbol = await u_contract.functions.symbol().call()
            symbol = raw_symbol.replace(" ", "")  # 某些代币符号带空格
            is_native = False

        # 4. 从 Comptroller 获取抵押因子 (CF)
        # markets 返回值是一个元组，通常 index 1 是 collateralFactorMantissa
        market_info = await comp_contract.functions.markets(self.to_checksum_address(v_addr)).call()
        cf = market_info[1] / 1e18  # 转换为 0.825 这种格式

        # 5. 构建你的数据结构
        return {
            "symbol": symbol.lower(),
            "v_symbol": v_symbol,
            "underlying_address": (config.WBNB_VTOKEN_UNDER_ADDRESS if is_native else underlying_addr.lower()),
            "address": v_addr.lower(),
            "underlying_decimal": underlying_decimal,
            "cf": cf,
            "is_native": is_native,
            "venus_supported": True,
            "oracle_precision": 10 ** (36 - underlying_decimal),
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
            Call(self.comptroller_addr, [signature, user_address], [(user_address, lambda x: x)])
            for user_address in user_address_list
        ]
        return await Multicall(calls, _w3=self._w3).coroutine()

    async def wait_for_transaction_receipt(self, tx_hash, timeout=60) -> TxReceipt:
        return await self._async_w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)

    async def get_transaction_count(self) -> int:
        return await self._async_w3.eth.get_transaction_count(self.to_checksum_address(self.account_address), 'pending')

    async def send_private_transaction(self, signed_tx):
        """
        通过 BloXroute 或类似服务发送私有交易
        """
        if not self.bloxroute_api_key:
            raise ValueError("BLOXROUTE API key is required for sending transactions.")

        # BloXroute 的 BSC 私有 RPC 地址
        private_rpc = "https://bsc.bloxroute.com/eth"

        # 构造请求头（需要你的 API Key）
        headers = {
            "Authorization": self.bloxroute_api_key,
            "Content-Type": "application/json"
        }

        payload = {
            "jsonrpc": "2.0",
            "method": "eth_sendRawTransaction",
            "params": [signed_tx.rawTransaction.hex()],
            "id": 1
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(private_rpc, headers=headers, json=payload) as resp:
                return await resp.json()

    async def send_bundle_to_bloxroute(self, oracle_tx: str, signed_tx: str) -> str:
        if not self.bloxroute_api_key or not self.bloxroute_auth_header:
            raise ValueError("BLOXROUTE API key or BLOXROUTE Authorization HEADER is required for sending a bundle.")

        txs = [oracle_tx.replace('0x', ''), signed_tx.replace('0x', '')]
        block_number = await self.get_block_number()
        params = {
            "transactions": txs,
            "block_number": hex(block_number + 1),
            "blockchain_network": "BSC-Mainnet",
            "mev_builders": "all"  # 向所有连接的验证者广播
        }

        message = json.dumps(params)
        signature = Account.sign_message(
            {"text": message},
            private_key=self.bloxroute_api_key
        ).signature.hex()

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "blxr_submit_bundle",
            "params": params
        }

        headers = {
            "Authorization": self.bloxroute_auth_header,
            "X-BLXR-Bundle-Hash": signature,
            "Content-Type": "application/json"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(config.BLOXROUTE_API, json=payload, headers=headers) as r:
                result = await r.json()
                if "error" in result:
                    raise Exception(f"Bundle 提交失败: {result['error']}")
                else:
                    return result['result'].get('bundle_hash')

    async def create_alpha_liquidation_tx(self,
                                          pair_address: str,
                                          borrower: str,
                                          amount: int,
                                          vdebt_address: str,
                                          vcollateral_address: str,
                                          path: List[str],
                                          pay_redeem_amount: int,
                                          min_profit: int,
                                          debt_underlying_address: str,
                                          collateral_underlying_address: str) -> SignedTx:
        """
        调用我的智能合约发送清算交易。

        :param pair_address: 交易对地址
        :param borrower: 被清算人的钱包地址
        :param amount: 代偿数量 (单位为 Wei)
        :param vdebt_address: 被清算人欠款的 vToken 合约地址 (如 vUSDT)
        :param vcollateral_address: 你想拿走的抵押品 vToken 合约地址 (如 vBNB)
        :param path: 最优路径
        :param pay_redeem_amount: 最小支付赎回数量 (单位为 Wei)
        :param min_profit: 要求的最低利润 (Wei)
        :param debt_underlying_address: 负债代币底层地址
        :param collateral_underlying_address: 抵押代币底层地址
        """
        if not self.private_key:
            raise ValueError("Private key is required for sending transactions.")

        alpha_contract = await self.get_contract(config.CONTRACT_ADDR, abi.contract_abi)

        nonce = await self.get_transaction_count()

        params = {
            "borrower": self.to_checksum_address(borrower),
            "repayAmount": amount,
            "vDebt": self.to_checksum_address(vdebt_address),
            "vCollateral": self.to_checksum_address(vcollateral_address),
            "path": path,
            "maxInput": pay_redeem_amount,
            "minProfit": min_profit,
            "dUnd": self.to_checksum_address(debt_underlying_address),
            "cUnd": self.to_checksum_address(collateral_underlying_address),
        }

        tx = await alpha_contract.functions.execute(
            self.to_checksum_address(pair_address),
            params
        ).build_transaction({
            'from': self.account_address,
            'nonce': nonce,
            'gas': 1000000,  # 新合约逻辑复杂，Gas Limit 建议给足
            'gasPrice': int(await self.get_gas_price() * 1.1),
            'chainId': 56
        })

        signed_tx = await self._async_w3.eth.account.sign_transaction(tx, self.private_key)
        return signed_tx

    async def simulate_liquidation_tx(self,
                                      pair_address,
                                      borrower: str,
                                      amount: int,
                                      vdebt_address: str,
                                      vcollateral_address: str,
                                      path: List[str],
                                      pay_redeem_amount: int,
                                      min_profit: int,
                                      debt_underlying_address: str,
                                      collateral_underlying_address: str) -> bool:
        """
        模拟发送清算交易。

        :param pair_address: 交易对地址
        :param borrower: 被清算人的钱包地址
        :param amount: 你代为偿还的金额 (单位为 Wei)
        :param vdebt_address: 被清算人欠款的 vToken 合约地址 (如 vUSDT)
        :param vcollateral_address: 你想拿走的抵押品 vToken 合约地址 (如 vBNB)
        :param path: 兑换路径
        :param pay_redeem_amount: 最小支付数量 (单位为 Wei)
        :param min_profit: 要求的最低利润 (Wei)
        :param debt_underlying_address: 负债代币底层地址
        :param collateral_underlying_address: 抵押代币底层地址
        :return: bool
        """
        if not self.private_key:
            raise ValueError("Private key is required for sending transactions.")
        
        alpha_contract = await self.get_contract(config.CONTRACT_ADDR, abi.contract_abi)

        params = {
            "borrower": self.to_checksum_address(borrower),
            "repayAmount": amount,
            "vDebt": self.to_checksum_address(vdebt_address),
            "vCollateral": self.to_checksum_address(vcollateral_address),
            "path": path,
            "maxInput": pay_redeem_amount,
            "minProfit": min_profit,
            "dUnd": self.to_checksum_address(debt_underlying_address),
            "cUnd": self.to_checksum_address(collateral_underlying_address),
        }

        await alpha_contract.functions.execute(
            self.to_checksum_address(pair_address),
            params
        ).call({'from': self.account_address})
        return True

    async def send_raw_transaction(self, transaction: HexStr):
        return await self._async_w3.eth.send_raw_transaction(transaction)

    async def send_liquidation_tx(self,
                                  user_address: str,
                                  amount: int,
                                  is_native: bool,
                                  vtoken_debt_address: str,
                                  vtoken_collateral_address: str,
                                  gas_multiplier: float = 1.1) -> HexBytes:
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

        v_contract = await self.get_contract(vtoken_debt_address, abi.vtoken)

        # 自动管理 Nonce
        nonce = await self.get_transaction_count()

        tx_params = {}

        if is_native:  # 如果是 BNB
            tx_params['value'] = amount
            lb = await v_contract.functions.liquidateBorrow(
                user_address, vtoken_collateral_address
            )
        else:
            lb = await v_contract.functions.liquidateBorrow(
                user_address, amount, vtoken_collateral_address
            )

        try:
            # 预估 Gas (防止浪费钱)
            gas_limit = await lb.estimate_gas({'from': self.account_address})
        except Exception as e:
            raise Exception(f"Gas estimation failed: {e}")

        tx_params.update({
            'from': self.account_address,
            'nonce': nonce,
            'gas': int(gas_limit * 1.2),  # 20% 冗余
            'gasPrice': int(await self.get_gas_price() * gas_multiplier),
            'chainId': 56
        })

        tx = await lb.build_transaction(tx_params)

        signed_tx = await self._async_w3.eth.account.sign_transaction(tx, self.private_key)
        return await self.send_raw_transaction(signed_tx.rawTransaction)

    async def ensure_unlimited_approval(self, underlying_addr, vtoken_addr, current_nonce):
        """
        检查并执行单笔授权
        """

        # 使用 ERC20 ABI 实例化底层代币
        token_contract = await self.get_contract(underlying_addr, abi.erc20_abi)
        allowance = await token_contract.functions.allowance(
            self.to_checksum_address(self.account_address), self.to_checksum_address(vtoken_addr)).call()

        # 如果授权额度小于 1 亿美金 (安全阈值)，则重新授权
        if allowance < 10 ** 8 * 10 ** 18:
            max_uint256 = 2 ** 256 - 1
            tx = await (await token_contract.functions.approve(self.to_checksum_address(vtoken_addr), max_uint256)
            .build_transaction({
                'from': self.account_address,
                'nonce': current_nonce,
                'gasPrice': self.get_gas_price()
            }))

            signed_tx = await self._async_w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = await self._async_w3.eth.send_raw_transaction(signed_tx.rawTransaction)

            # 这里不使用 wait_for_receipt 阻塞，直接增加 nonce 发下一笔
            return tx_hash, allowance, current_nonce + 1
        # 否则就不需要授权，表示额度还够
        return None, allowance, current_nonce
