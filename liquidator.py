import asyncio
import config
from logging import Logger
from analyzer import Analyzer
from hexbytes import HexBytes
from typing import Tuple
import web3.exceptions
from redis_client import RedisClient
from web3client import VenusClient
from utils import usd_to_wei, calc_slippage


class Liquidator:
    def __init__(self, client: VenusClient, db: RedisClient, analyzer: Analyzer, logger: Logger = None):
        self._client = client
        self._db = db
        self.Log = logger
        self.analyzer = analyzer
        self.incentive_mantissa = config.INCENTIVE_MANTISSA
        self._execution_lock = None
        self._vtoken_cache = None
        self._cooldown_cache = {}
        self._pair_graph_ = {}

    def set_vtoken_cache(self, vtoken_cache):
        self._vtoken_cache = vtoken_cache

    def set_execution_lock(self, lock):
        self._execution_lock = lock

    def set_graph_cache(self, graph_cache):
        self._pair_graph_ = graph_cache

    async def _handle_helper(self, user_address: str, user_profile: dict, assets: dict, prices: dict,
                             health_factor: float, oracle_tx_hash: str = None):
        self.Log.info(f"正在处理用户: {user_address} | 健康度: {health_factor} ")
        error, liquidity, shortfall = assets[user_address]
        liquidation_report = await self.is_liquidation(user_address, user_profile, prices)
        if liquidation_report['is_profitable']:
            status = await self.execute_liquidation(user_address, liquidation_report, oracle_tx_hash)
            self.Log.info(
                f"用户: {user_address}| 账户流动性:{liquidity} | 账户缺口: {shortfall} | 清算结果状态: {status}")
        else:
            self.Log.info(f"用户: {user_address} 不值得清算! | 账户流动性:{liquidity} | 账户缺口: {shortfall}")

    async def handle_multi_liquidation(self, user_address_list, prices):
        risky_reports = await self.analyzer.analyze_users(user_address_list, prices)
        assets = await self._client.get_user_liquidity(user_address_list)
        tasks = []
        for risky_report in risky_reports:
            user_address = risky_report['user_address']
            hf = risky_report['health_factor']
            user_profile = risky_report['user_profile']
            error, liquidity, shortfall = assets[user_address]

            if hf < 1.02 or shortfall > config.SHORTFALL_THRESHOLD:
                tasks.append(self._handle_helper(user_address, user_profile, assets, prices, hf))
        await asyncio.gather(*tasks)

    async def handle_liquidation(self, report, prices, oracle_tx_hash: str = None):
        user_address = report['user_address']
        user_profile = report['user_profile']
        hf = report['health_factor']

        if await self._db.should_skip(user_address):
            return

        assets = await self._client.get_user_liquidity([user_address])
        error, liquidity, shortfall = assets[user_address]

        if hf < 1.02 or shortfall > config.SHORTFALL_THRESHOLD:
            await self._handle_helper(user_address, user_profile, assets, prices, hf, oracle_tx_hash)

    async def get_slippage(self, pair_addr, v_address, amount):
        reserves, token0, token1 = await self._client.get_reserves(pair_addr)
        if v_address.lower() == token0.lower():
            r0, r1 = reserves[0], reserves[1]
            slippage = calc_slippage(amount, r0, r1)
        elif v_address.lower() == token1.lower():
            r1, r0 = reserves[1], reserves[0]
            slippage = calc_slippage(amount, r1, r0)
        else:
            return 1.0
        return slippage

    @staticmethod
    def calc_received_collateral(repay_amount_wei, best_debt, best_collateral, prices, incentive_mantissa):
        price_debt = prices[best_debt['v_addr']]
        price_collateral = prices[best_collateral['v_addr']]

        numerator = repay_amount_wei * price_debt * incentive_mantissa
        denominator = price_collateral * config.WAD
        received_collateral_wei = numerator // denominator
        return int(received_collateral_wei)

    async def calc_transform_collateral_to_usdt_best_path(self,
                                                          debt_wbnb_pair_address: str,
                                                          collateral_underlying_address: str,
                                                          collateral_amount: int) -> Tuple:
        """

        Args:
            debt_wbnb_pair_address: 交易对地址
            collateral_underlying_address: 抵押代币地址
            collateral_amount: 抵押品数量

        Returns: 最优路径，将抵押品换为 USDT，最多可以得到多少 USDT
        """
        if collateral_underlying_address == config.USDT_UNDER_ADDRESS:
            return [], collateral_amount

        best_path = []
        max_amount = 0
        pairs = self._pair_graph_[collateral_underlying_address]
        if config.USDT_UNDER_ADDRESS in pairs:
            pair_addr = pairs[config.USDT_UNDER_ADDRESS]
            if pair_addr != debt_wbnb_pair_address:
                path = [self._client.to_checksum_address(collateral_underlying_address),
                        self._client.to_checksum_address(config.USDT_UNDER_ADDRESS)]
                try:
                    amounts = await self._client.get_amounts_out(collateral_amount, path)
                    if max_amount < amounts[-1]:
                        max_amount = amounts[-1]
                        best_path = path
                except web3.exceptions.ContractLogicError:
                    pass
        for node, pair_address in pairs.items():
            if pair_address != debt_wbnb_pair_address:
                if config.USDT_UNDER_ADDRESS in self._pair_graph_[node]:
                    if self._pair_graph_[node][config.USDT_UNDER_ADDRESS] != debt_wbnb_pair_address:
                        path = [self._client.to_checksum_address(collateral_underlying_address),
                                self._client.to_checksum_address(node),
                                self._client.to_checksum_address(config.USDT_UNDER_ADDRESS)]
                        try:
                            amounts = await self._client.get_amounts_out(collateral_amount, path)
                            if max_amount < amounts[-1]:
                                max_amount = amounts[-1]
                                best_path = path
                        except web3.exceptions.ContractLogicError:
                            continue

        return best_path, max_amount

    async def calc_repay_flash_loan_best_path(self,
                                              debt_wbnb_pair_address: str,
                                              collateral_underlying_address: str,
                                              debt_underlying_address: str,
                                              repay_amount: int) -> Tuple:
        """
        从闪电贷借出债务数量，帮负债人偿还后，会得到抵押品，然后我需要还闪电贷池子的债务。
        该方法的逻辑：当我要还掉借来的闪电贷时，根据我最少支付多少抵押品去还闪电贷，找到最优路径，并且绕过池子的重入锁。
        Args:
            debt_wbnb_pair_address: 交易对地址
            collateral_underlying_address: 抵押代币地址
            debt_underlying_address: 债务代币地址
            repay_amount: 偿还的债务数量（也是我从闪电贷借的债务数量）

        Returns: 最优路径，最少要支付多少抵押品
        """
        best_path = []
        min_pay_redeem_amount = float('inf')
        pairs = self._pair_graph_[collateral_underlying_address]

        if debt_underlying_address in pairs:
            pair_addr = pairs[debt_underlying_address]
            if pair_addr != debt_wbnb_pair_address:
                path = [self._client.to_checksum_address(collateral_underlying_address),
                        self._client.to_checksum_address(debt_underlying_address)]
                try:
                    amounts = await self._client.get_amounts_in(repay_amount, path)
                    if 0 < amounts[0]:
                        min_pay_redeem_amount = amounts[0]
                        best_path = path
                except web3.exceptions.ContractLogicError:
                    pass

        for node, pair_address in pairs.items():
            if pair_address != debt_wbnb_pair_address:
                if debt_underlying_address in self._pair_graph_[node]:
                    if self._pair_graph_[node][debt_underlying_address] != debt_wbnb_pair_address:
                        path = [self._client.to_checksum_address(collateral_underlying_address),
                                self._client.to_checksum_address(node),
                                self._client.to_checksum_address(debt_underlying_address)]
                        try:
                            amounts = await self._client.get_amounts_in(repay_amount, path)
                            if 0 < amounts[0] < min_pay_redeem_amount:
                                min_pay_redeem_amount = amounts[0]
                                best_path = path
                        except web3.exceptions.ContractLogicError:
                            continue

        return best_path, min_pay_redeem_amount

    def find_best_liquidation_asset(self, user_profile, prices):
        """
        目标：
        1. 找到价值最大的债务 (Repay Debt) -> 提高单笔清算收益，节省 Gas。
        2. 找到 CF 因子最低或价值最大的抵押品 (Seize Collateral) -> 拿走价值最稳或对自己最有利的资产。
        """
        debts = []
        collaterals = []

        for v_addr, amount in user_profile.items():
            token = self._vtoken_cache.get(v_addr)
            if not token:
                return {}, {}

            amount = int(amount)
            current_price = prices[v_addr]
            value = abs(amount) * current_price

            if amount < 0:  # 债务
                debts.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value,
                    "amount": abs(amount),
                    "underlying_decimal": int(token['underlying_decimal']),
                })
            elif amount > 0:  # 抵押品
                collaterals.append({
                    "v_addr": v_addr,
                    "underlying_address": token['underlying_address'],
                    "symbol": token['symbol'],
                    "value": value,
                    "amount": amount,
                    "cf": float(token['cf']),
                    "underlying_decimal": int(token['underlying_decimal']),
                })

        # 排序：债务按价值从大到小
        best_debt = max(debts, key=lambda x: x['value'], default={})

        # 排序：抵押品优先选抵押因子大的
        best_collateral = max(collaterals, key=lambda x: x['cf'], default={})

        return best_debt, best_collateral

    @staticmethod
    def get_optimal_repay_amount(best_debt, best_collateral, prices, incentive_mantissa):
        debt_price = prices[best_debt['v_addr']]

        # 1. 协议限制：只能还 50%
        debt_value_limited = (best_debt['value'] * config.CLOSE_FACTOR) // config.WAD

        # 2. 抵押品限制：不能超过抵押品能赔付的上限
        collateral_value_limited = (best_collateral['value'] * config.WAD) // incentive_mantissa

        repay_value = min(debt_value_limited, collateral_value_limited)
        return repay_value // debt_price

    async def is_liquidation(self, user_addr: str, user_profile: dict, prices: dict) -> dict:
        best_debt, best_collateral = self.find_best_liquidation_asset(user_profile, prices)

        if not best_debt or not best_collateral:
            return {"is_profitable": False, "best_debt": {}, "best_collateral": {}, "repay_amount": 0}

        repay_amount_wei = self.get_optimal_repay_amount(best_debt, best_collateral, prices, self.incentive_mantissa)

        numerator = repay_amount_wei * prices[best_debt['v_addr']]
        denominator = 10 ** 36
        repay_usd = numerator / denominator

        debt_underlying_address = best_debt['underlying_address']
        collateral_underlying_address = best_collateral['underlying_address']

        price_collateral = prices[best_collateral['v_addr']]
        min_profit_wei = usd_to_wei(config.MIN_PROFIT_TOLERANCE,
                                    price_collateral,
                                    18)

        if debt_underlying_address != config.WBNB_UNDER_ADDRESS:
            pair_address = self._pair_graph_[debt_underlying_address][config.WBNB_UNDER_ADDRESS]
        else:
            pair_address = self._pair_graph_[debt_underlying_address][config.USDT_UNDER_ADDRESS]

        # 1. 预估 gas 成本
        gas_price_wei = config.GAS_PRICE
        estimated_gas = 1000000  # 清算交易通常消耗较多 gas
        bnb_price = prices.get(config.BNB_ADDRESS, config.BNB_PRICE_DEFAULT)
        gas_cost_usd = (gas_price_wei * estimated_gas * bnb_price) / 10 ** 36

        # 2. 我能得到的总抵押品数量
        received_collateral_amount_wei = self.calc_received_collateral(
            repay_amount_wei, best_debt, best_collateral, prices, self.incentive_mantissa)

        # 3. 计算还掉闪电贷的最优路径与债务 + 滑点 + swap手续费（0.25%）成本
        best_path, pay_collateral_amount_wei = await self.calc_repay_flash_loan_best_path(
            pair_address,
            collateral_underlying_address,
            debt_underlying_address,
            repay_amount_wei)

        # 4. 剩余抵押品数量 = 总抵押品数量 - 用于支付偿还闪电贷的抵押品数量
        rest_collateral_amount_wei = received_collateral_amount_wei - pay_collateral_amount_wei
        if rest_collateral_amount_wei <= 0:
            return {"is_profitable": False, "best_debt": {}, "best_collateral": {}, "repay_amount": 0}

        # 5. 计算将剩余的抵押品换为 USDT 这样的稳定币的最优路径，以及换成 USDT 后，我最多能得到多少 USDT
        # path, gross_profit_amount = await self.calc_transform_collateral_to_usdt_best_path(
        #     pair_address,
        #     collateral_underlying_address,
        #     rest_collateral_amount_wei)

        # 6. 毛利润
        gross_profit_usd = (rest_collateral_amount_wei * price_collateral) / 10 ** 36

        # 7. 净利润
        net_profit = gross_profit_usd - gas_cost_usd

        self.Log.info(f"用户 {user_addr} | 负债代币: {best_debt['symbol']} | 抵押代币: {best_collateral['symbol']}")
        self.Log.info(f"用户资产: {user_profile}")
        self.Log.info(
            f"代偿数量: {repay_amount_wei} | 负债人负债代币总数量: {best_debt['amount']} | 价格: {prices[best_debt['v_addr']]}")
        self.Log.info(f"能得到的总抵押品数量: {received_collateral_amount_wei}")
        self.Log.info(f"支付抵押品数量: {pay_collateral_amount_wei}")
        self.Log.info(f"剩余抵押品数量: {rest_collateral_amount_wei}")
        # self.Log.info(f"将剩余抵押品换为 USDT 能得到的数量: {gross_profit_amount}")

        if net_profit < config.MIN_PROFIT_TOLERANCE:
            await self._db.mark_as_non_liquidable(f"liquidator:skip:{user_addr}",
                                                  config.COOLDOWN_TTL_HOUR,
                                                  f"low_profit: {net_profit} USD")
        else:
            self.Log.info(f"--- ⚖️ 用户 {user_addr} 清算决策报告 ---")
            self.Log.info(f"🔹 待清算金额:  ${repay_usd} USD")
            self.Log.info(f"💰 理论毛利:    ${gross_profit_usd} USD")
            self.Log.info(f"⛽ Gas 成本:   ${gas_cost_usd} USD")
            self.Log.info(f"💴 预计收益:    ${net_profit} USD")
            self.Log.info(f"----------------------")

        liquidation_report = {
            "is_profitable": net_profit >= config.MIN_PROFIT_TOLERANCE,  # 利润大于 1 刀
            "repay_amount": repay_amount_wei,
            "pair_address": pair_address,
            "best_debt": best_debt,
            "best_collateral": best_collateral,
            "best_path": best_path,
            "pay_collateral_amount": pay_collateral_amount_wei,
            "min_profit": min_profit_wei,
            "net_profit": net_profit,
        }
        return liquidation_report

    async def execute_liquidation(self, user_address: str, liquidation_report: dict, oracle_tx_hash: str = None) -> bool:
        pair_address = liquidation_report['pair_address']
        repay_amount_wei = liquidation_report['repay_amount']
        path = liquidation_report['best_path']
        debt = liquidation_report['best_debt']
        collateral = liquidation_report['best_collateral']
        pay_collateral_amount_wei = liquidation_report['pay_collateral_amount']
        min_profit_wei = liquidation_report['min_profit']
        net_profit = liquidation_report['net_profit']

        try:
            self._client.simulate_liquidation_tx(
                pair_address,
                user_address,
                repay_amount_wei,
                debt['v_addr'],
                collateral['v_addr'],
                path,
                pay_collateral_amount_wei,
                min_profit_wei,
                debt['underlying_address'],
                collateral['underlying_address'],
            )
        except Exception as e:
            self.Log.error(f"⚠️ [模拟失败]: {e}")
            return False

        async with self._execution_lock:
            signed_tx = self._client.create_alpha_liquidation_tx(
                pair_address,
                user_address,
                repay_amount_wei,
                debt['v_addr'],
                collateral['v_addr'],
                path,
                pay_collateral_amount_wei,
                min_profit_wei,
                debt['underlying_address'],
                collateral['underlying_address'],
            )
            if net_profit > 500:
                if oracle_tx_hash:
                    await self._client.send_bundle_to_bloxroute(oracle_tx_hash, signed_tx.rawTransaction.hex())
                else:
                    try:
                        response = await self._client.send_private_transaction(signed_tx)
                        if "result" in response:
                            tx_hash = HexBytes(response["result"])
                        else:
                            self.Log.error(f"❌ 私有广播失败: {response}")
                            return False
                    except Exception as e:
                        self.Log.error(f"❌ 私有通道请求异常: {e}")
                        return False
            else:
                tx_hash = await self._client.send_raw_transaction(signed_tx.rawTransaction)
            self.Log.info(f"🚀 清算交易已发出！Hash: {tx_hash.hex()}")

        return await asyncio.create_task(self.check_receipt_status(tx_hash, user_address))

    async def check_receipt_status(self, tx_hash, user_address) -> bool:
        # 这里的 wait_for_transaction_receipt 建议设置 timeout
        receipt = await self._client.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt['status'] == 1:
            # 计算实际 Gas 消耗（BNB）
            gas_used = receipt['gasUsed']
            gas_price = receipt['effectiveGasPrice']
            actual_cost = (gas_used * gas_price) / 1e18
            self.Log.info(f"✅ 清算成功! 用户: {user_address} | 消耗 Gas: {actual_cost} BNB")
            return True
        else:
            self.Log.error(f"❌ 交易被回滚 (Reverted): {user_address} | Hash: {tx_hash.hex()}")
            return False
