# ChainInquest V4 Beta

V4 reuses the V3 execution path and the single-address Skill. It adds historical candidate discovery, a bounded natural-language planner, an evidence-led report and closed-beta access controls. The scope is read-only and restricted to curated project contracts.

## Run locally

From the repository root, run `python3 web/server.py --port 8000`. Open `http://127.0.0.1:8000`, load the Circle sample or paste a project request, enter an optional objective, preview the plan, confirm attribution and launch. The report separates facts, investigation candidates and unknowns. HTML is an inert attachment and includes the evidence catalog and raw records. JSON can be checked independently with `python3 scripts/verify_evidence.py REPORT.json`.

```sh
python3 skills/onchain-address-inspector/scripts/v3.py \
  --input examples/beta/aave.json \
  --objective "Review activity in the confirmed scope"
```

Use `scope: {"days":30}` for a rolling window or `scope: {"from_time":"2026-10-01T00:00:00Z","to_time":"2026-10-31T00:00:00Z"}` for a reproducible interval. Fixed time windows accept UTC offsets, require whole seconds, and cannot exceed 30 days. The tool retains partial reports and can resume the same investigation. Coverage and confirmation rules remain in the [V3 guide](v3.md).

## Historical provider boundary

| Source | Observed behavior | V4 policy |
| --- | --- | --- |
| Ethereum Blockscout public | Indexing status, block tip and address page returned HTTP 200 in direct probes. The adapter matched the reported index head with RPC but the bounded address query timed out on the selected sample. | Opt-in automatically for Ethereum long ranges when no Etherscan key or operator snapshot is configured. The observed head never certifies historical completeness. |
| Base / Monad Testnet Blockscout public endpoints | Base returned 403. The probed Monad Testnet endpoint returned 404. | These public endpoints are outside the adapter allowlist. |
| Etherscan V2 | Registry lists Ethereum, Base and Monad Testnet. Base requires a paid plan in the observed registry. No API key was configured. | Use the existing keyed transaction candidate adapter if available. An exhausted page does not prove a complete range. |
| Dune | Documentation lists Ethereum and Base. Monad Testnet coverage and runnable query/credits were not verified. | No live integration is enabled. |

Public Blockscout requests use a process-wide 2 rps schedule, a 15-second bounded cache for small public responses, at most six address pages and up to two attempts for eligible HTTP failures. RPC rechecks included transaction and event identity. Public index metadata or a matching head cannot prove that rows were never omitted. The existing RPC limits, Etherscan bounds and investigation timeout still apply. See [Blockscout API reference](https://github.com/blockscout/blockscout-api-v2-swagger/blob/main/swagger.yaml), [Etherscan supported chains](https://docs.etherscan.io/supported-chains), and [Dune API coverage](https://dune.com/apis-and-connectors).

For offline or privately provisioned history, set `CHAININQUEST_HISTORY_DIR` to a trusted directory. For long ranges it takes precedence over public data and uses a per-address file named `CHAINID-lowercase_address.json`. Required fields:

- `schema_version: 1`, `chain_id`, `address`, and credential-free HTTPS `source`.
- `watermark` with `start_block`, `indexed_through`, `start_hash`, and `end_hash` for the exact requested interval.
- `transactions` with `block`, `hash` and `claims` containing from, to, value, blockHash, timeStamp, and receipt status `txreceipt_status` or `isError`.
- Optional raw `logs` and `log_scope: "emitter_or_erc20_participant_v1"` attest the corresponding emitter and ERC-20 participant scope. Without those fields, events use the bounded RPC path.

Provision this directory from an independently verified export that exhausts pagination. Schema validation, endpoint hashes, a payload digest, row reconciliation and scope checks detect inconsistencies. The stated ability to cover a range comes from the operator's attestation, not cryptographic proof of absence. A mismatched watermark, duplicate row, unavailable receipt, missing comparison attribute or invalid log leaves a gap. The missing data cannot be replaced by assumptions. The snapshot is capped at 8 MiB, 500 transactions and 2,000 logs. Synthetic tests validate known-positive and known-empty 30-day windows as well as corrupt metadata.

## Planner and evidence

Without model configuration, the objective is mapped deterministically to activity, flows or overview and an explicitly requested 7/30-day window, then validated and run by the investigation engine. Model planning is opt-in through both `OPENAI_API_KEY` and `CHAININQUEST_PLANNER_MODEL`. Only the objective is sent to the provider. Avoid putting secrets or sensitive context in it. The model proposes a strict goal/window object with no tool access, URL changes or membership authority. Code validates the proposed fields and the resulting deterministic plan before anyone can execute it. The preview requires confirmation, expires after five minutes and is single-use. Failure falls back to the local policy with a recorded category. Estimated cost is recorded only if rates are configured through `CHAININQUEST_MODEL_INPUT_USD_PER_M` and `CHAININQUEST_MODEL_OUTPUT_USD_PER_M`. Token usage and latency are recorded when available. The tests exercise the model transport with mocked responses. No paid model call was made for acceptance. [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

## Closed-beta deployment gate

The Python service remains loopback-only. Its local mode is for a trusted single operator. Before inviting any external tester, configure an HTTPS origin and per-user token digests through `CHAININQUEST_BETA_ORIGIN` and `CHAININQUEST_BETA_TOKEN_HASHES`. Example configuration shape:

```text
CHAININQUEST_BETA_ORIGIN=https://beta.example.org
CHAININQUEST_BETA_TOKEN_HASHES={"tester":"<64 lowercase SHA-256 hex digits>"}
```

Generate a separate unpredictable token for each tester using a cryptographic random generator. Store only its SHA-256 digest in server configuration and deliver the token privately. In closed-beta mode, job creation, preview, resume, reports and exports require authenticated access. Each account has its own filesystem namespace. Job IDs are not bearer credentials. API budgets limit GET and POST per authenticated principal. HTTP concurrency, active investigation concurrency, pending approvals, inputs, storage and investigation attempts are bounded. Static assets and the sample contain no private data.

Deploy behind a managed TLS reverse proxy with the exact configured Host, no public listener on the backend port, a private service account and state volume, edge request-size and rate limits, and access logs that omit Authorization headers and request bodies. Do not establish forwarding or weaken proxy safeguards to work around a local failure. Verify TLS and origin checks, isolation, rotation and revocation, abort and restart behavior, and resource limits in the actual deployment before inviting users. Local security tests cover unauthorized access, cross-user reads and resumes, cross-origin writes, replayed approvals, quotas and HTML escaping. No hosted deployment was provisioned or independently security-reviewed in this sprint.

## Live acceptance and reproduction

The five manifests in [examples/beta](../examples/beta) pin Ethereum block 26468359 and point to curators' primary deployment sources. PublicNode and dRPC returned the same block hash, direct transaction rows, status, raw value and event identity/data for the tested filters. Circle USDC produced 12 transactions and 171 events. Aave, ENS, Lido and Uniswap Factory had zero matching rows in that fixed block after both sources finished the bounded queries. These are short positive and empty historical windows only.

Additional mined samples were independently sourced from Blockscout and individually checked against RPC transaction hash, raw value and receipt status. Aave at 26468358 returned 1 transaction and 2 events. ENS at 26468155, Lido at 26468273, and Uniswap at 26436643 initially received HTTP 403 from the original RPC. Resuming the saved jobs on dRPC returned 1/2, 1/2 and 1/1 transactions/events respectively. These checks do not establish cross-source event exhaustiveness for those additional activity samples. V3's earlier multi-chain measurements remain documented separately.

On Base Mainnet, the Circle USDC and CCTP contracts completed blocks 60650237–60650238 on dRPC and PublicNode. Both returned one direct transaction and eight events, with matching end block hash, transaction hash, status, raw wei and event block/hash/index/topics/data. To rerun, keep only the Base deployment from `examples/circle-multichain.json`, set the two explicit block heights, repeat with `CHAININQUEST_BASE_RPC=https://base.drpc.org` and `CHAININQUEST_BASE_RPC=https://base-rpc.publicnode.com`, and run the evidence verifier on both outputs. These observations exercise the same chain-scoped metrics across networks over short windows.

The no-model natural-language CLI run selected `flows`, verified two Circle Monad Testnet contracts and completed blocks 81761138–81761141. A separate local Web API run previewed and confirmed the plan, queried the same contracts on QuickNode at blocks 81762695–81762698, and retrieved both report formats. Both used the existing RPC investigation. The model-backed path remains tested through mocks only.

A local Chrome run exercised natural-language entry, plan preview, contract confirmation, progress, and the Aave result. The original Ethereum node returned HTTP 403; the report showed unknown activity. Reopening the same job from persisted state and resuming on dRPC completed the original one-block scope, matching the zero-activity reference. The evidence verifier passed on the recovered report.

The real 30-day Aave investigation resolved Ethereum blocks 26253076–26468402 for October 23 to November 22, 2026 UTC. It reported partial coverage. The public index timed out without an attested range, and dRPC event searches returned HTTP errors and exhausted the query deadline. There were no fully answered event ranges. This is an observed provider failure, distinct from the successful offline positive/empty 30-day fixtures.

Reproduce the five reference comparisons from the repository root:

```sh
mkdir -p artifacts
for project in aave circle ens lido uniswap; do
  python3 skills/onchain-address-inspector/scripts/v3.py --input "examples/beta/$project.json" > "artifacts/v4-real-$project.json" || exit
done
python3 scripts/reconcile_beta.py artifacts examples/beta/ethereum-26468359-drpc.json
```

The saved [reference evidence](../examples/beta/ethereum-26468359-drpc.json) contains public dRPC data for block 26468359. It records raw values and event payloads to make the comparison repeatable even when a public API is limited. The RPC rerun still depends on the configured provider. Other raw probe outputs remain ignored under `artifacts/`. Fixture, public RPC, public index, authenticated index and model transport checks are distinct. Full 30-day provider acceptance remains a release blocker.
