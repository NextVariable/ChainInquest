# 测试与验证 / Testing and validation

测试覆盖人工账本、整数金额、成功/失败/未知回执、自转、重复冲突、异常RPC响应、共享限速、超时、TLS、连接复用和独立安装入口。人工账本期望值从输入独立计算；真实并发与网络生命周期通过本机HTTP服务及curl进程验证。测试数据不代表真实链上活动。

Tests cover synthetic ledgers, integer amounts, success/failure/unknown receipts, self-transfers, duplicate conflicts, malformed RPC responses, shared rate limits, deadlines, TLS, connection reuse, and independent installation. Ledger expectations are computed from inputs independently; local HTTP servers and curl exercise actual concurrency and transport lifecycles. Fixtures do not establish live-chain activity.

```sh
python3 -m pip install -r requirements-dev.txt
python3 scripts/check_package.py
python3 -m ruff check .
python3 -m ruff format --check .
python3 -m coverage run -m unittest discover -s tests -v
python3 -m coverage combine
python3 -m coverage report
```

默认CI不访问公共RPC，不需要钱包或密钥。本机网络测试需要允许监听回环临时端口；权限错误不能记为功能通过。CI覆盖Python 3.10–3.14。扩展账本测试可设置`MONAD_TEST_SEEDS=10000`，样本数不等于链上查询次数。

Default CI uses no public RPC, wallet, or credentials. Local network tests require temporary loopback ports; permission failures do not count as functional passes. CI targets Python 3.10–3.14. Set `MONAD_TEST_SEEDS=10000` for expanded ledgers; sample counts are not live-query counts.

真实网络验证需单独指定地址、范围和预算，并检查JSON覆盖结果。保存个人查询结果时使用已忽略的`artifacts/`目录，公开前检查地址关联、路径与凭证。独立结果核对可运行`python3 scripts/verify_evidence.py artifacts/result.json`；它重新计算保存数据的统计，不等于独立节点或共识验证。旧查询不能证明当前公共服务可用。

Live validation requires a separate explicit address, range, budget, and inspection of JSON coverage. Save personal query outputs under ignored `artifacts/`; review address attribution, paths, and credentials before sharing. Run `python3 scripts/verify_evidence.py artifacts/result.json` for independent recalculation of saved statistics; this is not an independent-node or consensus check. Past queries do not prove current public-service availability.

## 调查模式记录 / Investigation verification

基线 75 项测试通过。调查模式另外覆盖时间窗口的闭开边界、保留旧入口、ERC-20 精确整数、自转日志去重、异常或冲突日志、回执缺失、重组后的指标抑制、索引分页与错误、可选 ABI、真实回环 HTTP 以及查询结果独立对账。用同一验证脚本可检查 1.7 或 2.0 的保存结果。

2026-11-22 的 Ankr 测试网抽样在区块 81745245–81745248 取得 4 笔顶层交易和 4 条核验事件，调查报告覆盖通过。这组事件没有 ERC-20 形状样本。扩大现网代币日志抽样时公共节点返回 RPC 错误，真实 Transfer 和 Approval 仍需另行验证。Foundation 的 7 天查询在 45 秒预算内未完成时间边界解析，输出结构化覆盖缺口。Etherscan 链列表公开接口确认列出 Monad Testnet 10143，带凭证的 txlist 查询尚未实测。保存的现场 JSON 位于被忽略的 `artifacts/`，不随仓库发布。

开发与可选 ABI 依赖在独立环境完成 `pip check` 和 `pip-audit`。初次审计只命中临时环境所带的旧 pip，升级至 26.3 后再次扫描未报告已知漏洞。仓库 MIT 许可证与 Skill 随包副本一致。可选 eth-abi、eth-utils 和 eth-hash 使用各自的 MIT 许可证，PyCryptodome 分发元数据列出 BSD 和公有领域条款。依赖以安装方式使用。

短时间窗实测使用 Ankr 与系统 curl。固定时间窗 `2026-11-22T03:29:55+00:00` 至 `2026-11-22T03:29:57+00:00` 解析到区块 81745243–81745248，6 笔顶层交易与 6 条事件，退出 0，保存输出通过独立对账。先前的显式 Python HTTPS 测试因本机 CA 校验失败，保留 TLS 校验并改用系统 curl 后完成。

本轮最终在 Python 3.14.3 上通过 94 项测试，总覆盖率 90.75%（含分支）。Ruff、打包检查和 Python 3.10 语法检查通过。其余 Python 版本由 CI 矩阵覆盖，本地没有运行该矩阵。

## V2.1 现场与回归记录 / Live and replay status

2026-11-22，使用公开 USDC 测试网合约。Foundation 与 QuickNode 对以下样本返回一致的事件发出者、区块哈希、交易哈希、日志位置、主题和数据。正常 CLI 分别按合约发出者与事件第二参与地址查询，每个目标事件命中一次。四份报告均通过独立证据与整数对账。

| 样本 | 区块 | RPC 原始金额 | 历史显示精度 |
| --- | ---: | --- | --- |
| [Transfer](https://testnet.monadscan.com/tx/0xa8d8dba135a663fdf4cfb2afc50954b389283f386cbcfa4c075f325ad4655186) | 29237812 | 100000 | 不可用 |
| [Approval](https://testnet.monadscan.com/tx/0xad1036effaffa31e5b11cb9bcce26ae10291f2e6d409ffef8992c7d3887d7336) | 19198003 | 2²⁵⁶−1 | 不可用 |

两个公共节点对这些旧块的 `eth_call decimals()` 返回 `-32602`，对 latest 返回 6。最新状态不会被用于补历史显示金额。原始事件被固定为 `tests/fixtures/monad_testnet_usdc.json`，默认 CI 离线回放并测试精确整数。重复、冲突排除和已知小数精度转换由本机测试覆盖。Ankr 对这些旧样本未返回可用回执。现场覆盖不外推至其他代币、区块或节点。

| Foundation 日志查询 | 时间窗口 | 耗时 | 观察结果 |
| --- | --- | ---: | --- |
| 10,000 块起始跨度 | 7 天，约 191 万块 | 99.5 秒 | 76 次 `-32602`，未应答有效区间 |
| 10,000 块起始跨度 | 30 天，约 832 万块 | 98.4 秒 | 70 次 `-32602`，未应答有效区间 |
| 100 块起始跨度 | 7 天，约 191 万块 | 86.5 秒 | 每个过滤器应答 1,300 块，退出 2 |
| 100 块起始跨度 | 30 天，约 832 万块 | 86.5 秒 | 三个过滤器分别应答 1,100、1,000、1,000 块，退出 2 |

独立跨度探测中 100 和 101 块成功，1,000 与 10,000 块明确返回 `block range too large`。工具对精确匹配的 Foundation 测试网 URL 使用保守的 100 块起始跨度。上述对照使用不同实时窗口和预算，记录错误与实际应答范围，不比较加速倍数。优化后保存的结果仍在总预算内返回部分范围，并完整报告未查询区间。短窗口已有链上证据，长窗口无已返回事件不能解释为无事件。

Etherscan V2 链列表列出 10143。此次环境缺少 API key，匿名 `txlist` 实测返回 `Missing/Invalid API Key`。授权查询以及与 RPC 的真实逐笔对账为阻塞项。对索引金额、区块哈希、地址、时间戳和回执状态的差异处理通过模拟回归验证。长窗口输出 `index_key_required`。本机调试原始输出位于已忽略的 `artifacts/`，CI 继续使用固定证据和人工账本。

V2.2 优先接入可声明历史覆盖水位的索引服务，并以至少一组已知有交易和一组已知为空的窗口做带凭证验收。另使用能查询历史状态的节点验证上述旧块的 `decimals()`，为相同地址分阶段的合约归属及代理升级引入版本化证据。

最终冻结版本在 Python 3.14.3 上通过 100 项测试，总覆盖率 89.72%（含分支），通过现有 85% 门槛。Ruff 和打包检查通过，新用例由原有 CI 自动发现。

## V4 RC 历史覆盖 / V4 RC historical coverage

新增回归集中在 `tests/test_history_provider.py` 与 `tests/test_report_html.py`：索引分页水位与断点续跑、页面窗口拆分、缺失水位不得计为零活动、索引日志回执修复、RPC `eth_getLogs` 已答区间跳过，以及 HTML 转义、缺口展示和“未知而非零”的渲染。合并前加固补齐了边界用例：`status=0` 携带错误消息的空数组必须被拒绝、仅显式“无记录”消息可视为合法空页、提供方 `blockHash` 与 RPC 不符时不得 attest、同高度不同区块哈希触发重组重置并重新抓取、重复交易/日志数据冲突时抑制 `complete`、每次运行独立的分页预算可在断点续跑后收敛到与一次性查询相同的指标，以及状态文件权限为 `0600` 且不写入凭证。完整套件在本机 Python 3.13.9 上通过 139 项测试（跳过 1 项），Ruff 检查通过。

覆盖声明只表示“提供方报告了结束区块，且其持有的每个请求范围都已翻完”，不构成独立链上缺失证明；交易与日志仍逐条经 RPC 区块头和回执核对，核对失败或任何范围未耗尽都不会标记 `complete`。

2026-11-22 用免密 Routescan（以太坊）加 dRPC 回执完成三组真实 30 天验收，范围均为 `2026-10-23T07:33Z` 至 `2026-11-22T07:33Z`：

- Compound Timelock：2 笔顶层交易、40 条核验事件，交易与事件覆盖均为 `complete` 且带水位 attestation。
- Compound Governor Bravo：0 笔交易、0 条事件，`complete` 且有水位 attestation，证明“完整零活动”可与“未知”区分。
- Uniswap V3 Factory：18 笔顶层交易（11 成功、7 失败）、617 条核验事件；修复了 Routescan 返回 `logIndex`/`transactionIndex` 为 `"0x"` 的行后，回执全部完整，缺口为 0。

三组结果的原始 JSON 仅保存在被忽略的 `artifacts/` 或临时目录，不随仓库发布。Base 与 Monad 的 30 天验收仍被凭证阻塞：没有付费 Etherscan V2 计划或运营方快照，运行必须输出 `index_key_required` 并标记未知，不能计为零。高活跃合约在固定预算下仍可能以 `partial` 结束，这是可接受的诚实状态，不是验收失败。
