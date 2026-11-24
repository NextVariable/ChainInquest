# Codex-native acceptance

**Status: `CODEX_NATIVE_READY`.** Three real Codex host cases passed on 2026-11-24. The accepted product is the **Agent-Native On-chain Investigation Skill & Domain Harness**.

The host acceptance covers discovering the installed Skill, converting a natural-language objective into a bounded request, invoking the local investigation engine, and explaining returned evidence and gaps. It does **not** establish complete 30-day historical coverage.

## Tested setup

- Source revision: [`6f53e1dc96644faa154ecf92edf1293a5e769a43`](https://github.com/NextVariable/ChainInquest/commit/6f53e1dc96644faa154ecf92edf1293a5e769a43). The [baseline CI](https://github.com/NextVariable/ChainInquest/actions/runs/45047365893) ran separately.
- Host: Codex CLI `0.171.0-alpha.13` bundled with the desktop app, authenticated through ChatGPT. Each case used a separate real `codex exec --json` session. No account details are published.
- Installation: the full 27-file Skill package was copied to the investigation workspace's `.agents/skills/onchain-address-inspector`. Codex `skills/list` reported the workspace copy as enabled, and tool calls in all three sessions read that copy's `SKILL.md`.
- Runtime: Python 3.14.3. The host called the installed `scripts/query.py` or `scripts/v3.py`, read the resulting JSON, and produced a final report. Standalone CLI help checks were installation checks. No mocks or replayed reports counted as host acceptance.
- Model: the host handled planning. The optional separate model planner was disabled. No standalone model API key was needed.

## Host cases

| Case | Natural-language task and observed host action | Result |
| --- | --- | --- |
| A · Monad Testnet address | Inspect recent activity for the public Circle USDC contract and explain transaction count, receipt status, caller concentration, coverage, and evidence. Codex selected the installed address entry point and queried the public Monad Foundation RPC. | **PASS.** All 12 blocks, 82345526–82345537, were scanned. There were zero matching top-level transactions and no defined caller share. A separate live query of historical block 29237812 found one successful top-level transaction from one direct caller (1/1). |
| B · Compound Timelock project | Investigate the last 30 days, confirm scope and contract attribution, and produce a traceable report. Codex generated a project request from the supplied confirmed manifest, checked the contract address against Compound's official deployment documentation, and ran the installed project engine against dRPC and Routescan. | **PASS.** The rolling request produced an evidence-backed partial report. Codex retried the documented fixed dates as a new live investigation and reported the resulting coverage gaps and unknown aggregate. |
| C · Base data gaps | Investigate Circle USDC on Base for 30 days with a limited query budget and explain missing coverage. Codex invoked the installed project engine and read the structured failure output. | **PASS.** The first attempt failed TLS verification. A follow-up kept verification enabled, confirmed the Base chain ID, and timed out while resolving the block range. The host explained that transactions, events, and receipts were unknown and that an empty evidence set did not show zero activity or zero users. |

Case A's historical sample has a successful receipt (`0x1`) at block `0x1be2234`. The public Monad Foundation RPC returned both fields on a documentation recheck using `eth_getTransactionReceipt` with the same hash. The explorer returned HTTP 403 to the automated link check. The single observed caller does not establish concentration outside that block. [MonadScan transaction](https://testnet.monadscan.com/tx/0xa8d8dba135a663fdf4cfb2afc50954b389283f386cbcfa4c075f325ad4655186)

### Compound coverage and evidence

The investigation was scoped to Ethereum Mainnet Timelock `0x6d903f6003cca6255d85cca4d3b5e5146dc33925`. The deployment source establishes the contract in scope. RPC and index responses supply the activity evidence. [Compound V2 deployment documentation](https://docs.compound.finance/v2/)

| Window (UTC, end exclusive) | Coverage and findings |
| --- | --- |
| Rolling · 2026-10-25 08:17:42 to 2026-11-24 08:17:42 | **Partial.** The host reported 2 successful top-level transactions and 31 receipt-matched logs. Nine indexed logs were not fully checked, and the final six seconds exceeded the RPC coverage boundary. These counts describe observed evidence. |
| Fixed · 2026-10-23 07:33 to 2026-11-22 07:33 | **Partial.** A fresh network query retained 2 receipt-matched transactions and 5 receipt-matched logs at address level. The ending snapshot recheck timed out, so the project engine excluded that address report from aggregation and returned unknown metrics. Earlier published validation of these dates is a separate run. |

Example evidence reference from the rolling report:

```text
tx:1:0xd6247a91168e2cf55fc63c15faad6b25a3d0597301e0974f8e50627d0c0a4c00:0x659d49259e6998e7cb2cc46da24e3836ddb627be0b56fdef8ccf3a8c758da86b
```

The transaction hash lets reviewers check the block, sender, target, amount and receipt status. [Etherscan transaction](https://etherscan.io/tx/0x659d49259e6998e7cb2cc46da24e3836ddb627be0b56fdef8ccf3a8c758da86b)

## Publication and delivery scope

This public record contains a summary of host actions, public chain identifiers and documented outcomes. Raw conversations, tool transcripts, local filesystem paths, session and job identifiers, and private account or credential information are excluded from the repository.

Codex supplies natural-language interaction and task decisions. ChainInquest supplies read-only data queries, deterministic analysis, evidence validation, budget limits, persisted project state, and reporting. Task resumption is an engine capability. The fixed-window run in Case B used a separate request. Internal execution traces, inferred wallet ownership, and complete historical coverage are outside the claims supported by these runs.

The [local Web demo](v4-beta.md#run-locally) and [deployment templates](../deploy/README.md) are optional components. MCP servers, integration with standard ChatGPT conversations, and public deployment are outside the current delivery. See the [Codex quickstart](../README.en.md#codex-quickstart) to use the accepted workflow.

功能开发在 `CODEX_NATIVE_READY` 验收后冻结。当前交付范围为 Codex 原生 Skill 和调查 Harness，MCP、普通 ChatGPT 对话接入与公网部署均不在本次交付范围内。
