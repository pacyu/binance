# Venus High-Frequency Liquidation Engine (V-HFLE)

[![Platform](https://img.shields.io/badge/Network-BSC-F3BA2F?style=for-the-badge&logo=binance-smart-chain&logoColor=white)](https://bscscan.com)
[![Python](https://img.shields.io/badge/Python-3.14+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

**V-HFLE** 是一款针对 Venus Protocol (BSC) 开发的高性能、低延迟清算执行引擎。系统通过集成 bloXroute 与 NodeReal 节点，实现了对 Mempool 中预言机价格更新交易的实时捕捉，并结合本地计算引擎，在亚秒级内完成风险评估与清算决策。

---

## 🌟 项目核心优势

### 1. 极致的性能优化 (Performance)
* **从 20s 到 9ms**：通过将原本依赖链上查询（`getAccountLiquidity`）的逻辑转化为本地内存计算，成功将清算路径决策延迟压缩至 **9ms** 级别。
* **本地化缓存策略**：利用 Redis 高效存储全量用户画像与 vToken 静态属性（CF、ExchangeRate 等），极大地减少了 RPC 请求带来的 IO 延迟。

### 2. 智能风险识别引擎 (Risk Management)
* **Analyzer**: 实时计算用户健康因子（HF）与动态 Shortfall，支持对数万个地址进行毫秒级筛选。
* **Liquidator**: 具备全自动利润预估功能，自动对冲 Gas 成本与滑点，支持通过 Flash Swap 锁定清算利润。

### 3. 三维监控体系 (Monitoring)
* **Mempool 监听**：捕获 Oracle 价格更新，实现“抢跑”预判。
* **全量扫描**：对高风险账户（HF < 1.05）进行高频轮询，确保无遗漏。
* **事件驱动**：实时跟踪链上 `Mint`（已去除）, `Redeem`, `Borrow`, `Repay`, `MarketEntered` 事件，动态维护本地用户数据库。

---

## 🏗 技术架构

系统采用微服务化的异步架构设计，确保各模块解耦，互不干扰且高效并发：

* `monitor_mempool.py`: 核心引擎，负责监听公共池交易并触发即时清算。
* `monitor_risky_user.py`: 风险哨兵，执行高频的全量用户健康度普查。
* `monitor_user_event.py`: 状态同步器，通过监听协议日志保持本地数据与链上一致。
* `analyzer.py`: 算法层，负责复杂的精度换算与清算路径择优。
* `liquidator.py`: 执行层，负责交易构造、私有通道广播及收据追踪。

---

## 🛠 技术栈

* **核心语言**: Python 3.14 (全面采用 `Asyncio` 异步架构)
* **区块链交互**: Web3.py, Eth-abi, Websockets
* **基础设施**: Redis (高性能数据中心), bloXroute BDN (极速网关)
* **业务逻辑**: Venus Protocol (Compound V2 分叉协议) 深度解析

---

## 📊 开发者调试记录

在开发过程中，本项目成功解决了多个清算实战中的工业级难题：

### 1. 攻克 Error 03 (MARKET_NOT_FRESH)
深入研究 Venus 源代码，定位到清算失败主因是利息未及时更新。通过在清算交易中显式集成 `accrueInterest` 调用，显著提升了在高波动行情下的清算成功率。

### 2. 精准处理 L_F (LIQUIDATE_SEIZE_TOO_MUCH)
针对“资不抵债”的坏账粉尘账户，建立了预模拟校验机制。系统会自动计算应得抵押品与用户实际余额的差值，有效避免了无效 Gas 支出。



---

## 🚥 快速开始

### 环境变量配置 (.env)
```env
PRIVATE_KEY=your_private_key
NODEREAL_RPC_URL=your_nodereal_url
BLOXROUTE_API_KEY=your_api_key
BLOXROUTE_AUTH_HEADER=your_auth_header
REDIS_HOST=127.0.0.1
```
