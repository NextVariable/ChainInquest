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
