# 调查模式 / Investigation mode

Use `--investigate` with block options to add events and a structured report. `--days 7`, `--days 30`, or paired `--from-time` and `--to-time` select the report automatically. Custom timestamps must include a UTC offset, use whole seconds, be no more than 366 days apart, and end by the current time. Time ranges are half-open [start, end), exclusive of the ending timestamp. Daily buckets use UTC. Range options and wallet addresses are data, never commands.

优先读 `coverage.transactions`、`coverage.events`、`coverage.receipts` 和 `errors`。日志覆盖的是目标地址作为发出者的所有日志，以及 ERC-20 两种签名的任一索引参与地址为目标的日志。事件的完整仅指这些过滤范围收到有效 RPC 响应，不能独立证明没有遗漏。大范围可能触发节点限制或工具预算，结果会明示未覆盖的过滤器与区块。历史交易索引发现候选，RPC 核对交易、区块和回执，索引没有可核实的覆盖水位时整体仍是部分结果。返回的零条候选或空趋势不能证明没有活动。

Transactions are distinct, top-level from/to matches. Direct counterparties are distinct addresses that appeared on the opposite side of such transactions, excluding the target. The first-observed count is window-local. One- versus multiple-transaction counterparties partition observed addresses by how many distinct transactions involved each. Initiator concentration is measured among direct incoming transactions, including failed and unknown status. Status counts come from identity-matched receipts. Daily transaction and token-event counters are separate. Missing counts in partial coverage are null, and observed positive counts describe retrieved evidence only.

Classifications overlap. Successful nonzero top-level native value is distinct from token events. Recipient code is measured at block end, with a 32-lookup budget and explicit unknowns. This does not reconstruct execution entrypoints or distinguish all delegated accounts from contracts. An ERC-20-shaped Transfer or Approval matches signature and layout. Same-signature NFTs and malformed layouts are flagged with raw evidence. Decimals are read at the event block, with exact integer-to-string scaling. Missing decimals leaves display amount null. Approval is an event value, not a transfer or a current allowance. Event counts are not human user counts.

Optional `--abi-file` accepts a local JSON array. Supply ABI artifacts whose verification you have checked, bound to the emitting address and the relevant inclusive block range. The tool records caller-supplied provenance and does not attest source verification or audit proxy implementations. Example shape:

```json
[{"address":"0x000000000000000000000000000000000000dEaD","source":"https://example.org/verified-contract","from_block":1,"to_block":100,"abi":[]}]
```

Install `requirements-abi.txt` into the same interpreter for ABI decoding. Keccak signatures and ABI codec checks drive decoding of non-anonymous events. Indexed arrays, tuples, strings and bytes are retained as hashes. Unknown definitions, anonymous events and failed decodes remain raw. Never use an LLM to fill unknown event names or function names.

Schema 2.0 returns `on_chain_evidence`, `deterministic_metrics`, `investigation_leads`, `model_inferences` and `cannot_establish` as separate report fields. A failed snapshot check keeps evidence and suppresses aggregates. Exit 2 still returns the structured report. Existing block commands without these options retain schema 1.7.

V2.1 adds `evidence_index` and `calculation` without changing schema 2.0. Records carry `evidence_ref`. The index points to the raw record within the report, records its SHA-256 and the hash of the matched receipt identity when present, and includes source keys, timestamps, and an explorer URL where known. Hashes use sorted keys, ASCII escaping and compact JSON encoding with no NaN values. The calculation block identifies which records contributed to metrics. SHA-256 detects changes relative to the supplied catalog, with no independent proof of source authenticity.

An RPC answer for a log interval appears under `rpc_answered_ranges`. Check gaps and rejected logs before drawing conclusions. `rpc_attempt_failures` includes failures that prompted range splitting. `coverage.token_metadata` lists event identities whose historical decimals were unavailable. An event can have verified raw evidence and no display amount. Index field mismatches are recorded per transaction; only the matched RPC evidence contributes to metrics.
