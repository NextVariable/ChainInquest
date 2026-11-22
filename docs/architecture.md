# 架构 / Architecture

安装包负责只读核查 Monad 测试网地址在有限区块范围内的顶层交易，输出覆盖说明、指标和可核对的证据。

The installable package inspects top-level address activity within a bounded Monad Testnet block range and returns coverage, metrics, and traceable evidence.

`skills/onchain-address-inspector/` 是完整安装单元。`SKILL.md` 提供发现信息与核心工作流，`scripts/` 包含运行代码，`references/` 按需解释参数和统计口径，`agents/` 提供 Codex 界面元数据。测试、维护工具和使用文档位于安装包外；个人查询记录不随仓库分发。

`skills/onchain-address-inspector/` is the complete installation unit. `SKILL.md` provides discovery metadata and the core workflow, `scripts/` contains runtime code, `references/` supplies on-demand parameter and metric definitions, and `agents/` supplies Codex interface metadata. Tests, maintenance tools, and usage documentation live outside the package. Personal query records are not distributed.

运行代码分为四个模块。`query.py` 负责参数、进度、扫描、去重、一致性检查和覆盖结论；`rpc_client.py` 负责只读方法、JSON-RPC信封、共享限速、截止与重试；`rpc_transport.py` 负责TLS、HTTP工作线程、连接复用与响应体上限；`evidence.py` 负责数据校验、回执状态和整数指标计算，不访问网络。全部模块随安装包分发，运行不依赖第三方Python包。

Four modules implement the original block scan and shared runtime. `query.py` handles arguments, progress, scanning, deduplication, consistency, and coverage. `rpc_client.py` handles read-only methods, JSON-RPC envelopes, shared rate limits, deadlines, and retries. `rpc_transport.py` handles TLS, HTTP workers, connection reuse, and response limits. `evidence.py` validates data and calculates receipt outcomes and integer metrics without network access. All modules ship together. Default execution uses the standard library, with an optional ABI codec described below.

测试使用独立计算期望值的人工账本、本机HTTP服务、curl进程及仓库外安装入口，分别验证指标、传输生命周期和完整命令行路径。

Tests use independently calculated ledger expectations, local HTTP services, curl processes, and an installation outside the checkout to validate metrics, transport lifecycles, and the complete CLI path.

详细参数见[使用说明](usage.md)，可复现检查见[测试说明](testing.md)。

See [usage](usage.md) for detailed parameters and [testing](testing.md) for reproducible checks.

## 调查模式 / Investigation mode

`query.py` dispatches to `investigation.py` for schema 2.0. The original schema 1.7 entry point, limits and default query are retained. Small time windows use the existing block scanner after conversion to block bounds. Larger windows use the index boundary described below.

| Boundary | Responsibility |
| --- | --- |
| `adapters.py` | Resolve time using exponential bracketing and binary search over headers, query bounded RPC log ranges and discover historical top-level candidates through Etherscan V2 |
| `evidence.py` and `event_decoder.py` | Validate identities and decode fixed ERC-20 layouts or events with a supplied ABI |
| `evidence_store.py` | Share bounded receipt evidence within a run and export raw transactions, raw logs and rejected records |
| `analytics.py` | Calculate UTC buckets, counts and classifications from retained evidence |
| `report.py` | Assemble source, coverage and limits with deterministic investigation leads |

The RPC allowlist, TLS and shared 5 rps scheduler are reused. An index request uses a fixed HTTPS host and a credential from the environment. Pagination is bounded to five pages of 100 and candidate transaction fields are checked against RPC data. Index coverage remains unverified even at a terminal page. Events use up to 120 log queries, spans up to 10,000 blocks per request, and a raw log budget of 2,000 rows or 8 MiB. Large windows can exceed these budgets. Receipt, block identity and closing snapshot checks verify returned evidence within a single RPC's view. They do not attest consensus or absence of silent upstream omissions.

Etherscan discovery uses the [V2 account transaction interface](https://docs.etherscan.io/api-reference/endpoint/txlist). Event layout uses [ERC-20](https://eips.ethereum.org/EIPS/eip-20) and the [Solidity ABI specification](https://docs.soliditylang.org/en/latest/abi-spec.html). The optional decoder uses eth-abi and eth-utils with the eth-hash PyCryptodome backend. Default execution retains its standard-library runtime. Evidence is written to stdout for the caller to retain. No service or local database is required.
