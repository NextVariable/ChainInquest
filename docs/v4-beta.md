# ChainInquest V4 Beta

V4 reuses the V3 execution path and the single-address Skill. It adds historical candidate discovery, a bounded natural-language planner, an evidence-led report and closed-beta access controls. The scope is read-only and restricted to curated project contracts.

## Run locally

From the repository root, run `python3 web/server.py --port 8000`. Open `http://127.0.0.1:8000`, then enter a project name, contract addresses, the HTTPS page that lists them, a goal and a time scope. Preview the readable plan, confirm attribution and launch. The raw manifest stays available under "Advanced" for existing users. The report separates facts, investigation candidates and unknowns, shows coverage per evidence type, and offers JSON and self-contained HTML downloads. JSON can be checked independently with `python3 scripts/verify_evidence.py REPORT.json`.

```sh
python3 skills/onchain-address-inspector/scripts/v3.py \
  --input examples/beta/aave.json \
  --objective "Review activity in the confirmed scope"
```

Use `scope: {"days":30}` for a rolling window or `scope: {"from_time":"2026-10-01T00:00:00Z","to_time":"2026-10-31T00:00:00Z"}` for a reproducible interval. Fixed time windows accept UTC offsets, require whole seconds, and cannot exceed 30 days. The tool retains partial reports and can resume the same investigation. Coverage and confirmation rules remain in the [V3 guide](v3.md).

## Historical provider boundary

| Source | Observed behavior | V4 policy |
| --- | --- | --- |
| Routescan public (Ethereum only) | Credential-free Etherscan-compatible API. `txlist`, `getLogs`, `getblockreward` and `getblocknobytime` were reachable without a key; `offset=1000` and a provider result window of 10,000 rows were verified. Base and Monad are not supported by this route. | Preferred for Ethereum long ranges when no operator snapshot is configured. Coverage is attested only when `getblockreward` matches the RPC header at the end block and every requested block range was paged to exhaustion. |
| Ethereum Blockscout public | Indexing status, block tip and address page returned HTTP 200 in direct probes. The adapter matched the reported index head with RPC but the bounded address query timed out on the selected sample. | Fallback only when Routescan is unavailable and no Etherscan key or operator snapshot is configured. The observed head never certifies historical completeness. |
| Base / Monad Testnet Blockscout public endpoints | Base returned 403. The probed Monad Testnet endpoint returned 404. | These public endpoints are outside the adapter allowlist. |
| Etherscan V2 | Registry lists Ethereum, Base and Monad Testnet. Base requires a paid plan in the observed registry. No API key was configured. | Use the existing keyed transaction candidate adapter if available. An exhausted page does not prove a complete range. Base 30-day coverage is credential-blocked and reported as unknown. |
| Dune | Documentation lists Ethereum and Base. Monad Testnet coverage and runnable query/credits were not verified. | No live integration is enabled. |

The keyed Etherscan adapter never attests coverage: its basis is `pagination_without_provider_watermark`. Resume state is reused only when the provider tag, address and block range all match. Deadline, page-budget and transport gaps belong to the run that produced them and are not replayed after a later successful resume.

Routescan coverage has two independent parts. Transactions are paged across the exact block range with a persisted queue (`CHAININQUEST_INDEX_MAX_PAGES`, default 60, maximum 200). Event discovery runs five bounded queries (emitter, Transfer/Approval in, Transfer/Approval out) and deduplicates by transaction hash and log index. Every candidate row and log is reconciled against RPC transaction, receipt, block and topic fields before it contributes to metrics. When a row omits `logIndex` or `transactionIndex`, the receipt is fetched to repair it; an unrepaired row becomes a gap. Attestation means the provider watermark matched the RPC end block and pagination was exhausted; it is not cryptographic proof that the provider omitted nothing. Base and Monad long ranges without a configured key or operator snapshot remain `unknown`, never zero.

Index paging, RPC `eth_getLogs` chunks and verified-log states are checkpointed after each answered page or chunk. Resuming a job reuses the saved state; receipts are re-verified on resume. A range that hits the page or deadline budget ends as `partial` with explicit gaps.

Public Blockscout requests use a process-wide 2 rps schedule, a 15-second bounded cache for small public responses, at most six address pages and up to two attempts for eligible HTTP failures. RPC rechecks included transaction and event identity. Public index metadata or a matching head cannot prove that rows were never omitted. The existing RPC limits, Etherscan bounds and investigation timeout still apply. See [Blockscout API reference](https://github.com/blockscout/blockscout-api-v2-swagger/blob/main/swagger.yaml), [Etherscan supported chains](https://docs.etherscan.io/supported-chains), and [Dune API coverage](https://dune.com/apis-and-connectors).

For offline or privately provisioned history, set `CHAININQUEST_HISTORY_DIR` to a trusted directory. For long ranges it takes precedence over public data and uses a per-address file named `CHAINID-lowercase_address.json`. Required fields:

- `schema_version: 1`, `chain_id`, `address`, and credential-free HTTPS `source`.
- `watermark` with `start_block`, `indexed_through`, `start_hash`, and `end_hash` for the exact requested interval.
- `transactions` with `block`, `hash` and `claims` containing from, to, value, blockHash, timeStamp, and receipt status `txreceipt_status` or `isError`.
- Optional raw `logs` and `log_scope: "emitter_or_erc20_participant_v1"` attest the corresponding emitter and ERC-20 participant scope. Without those fields, events use the bounded RPC path.

Provision this directory from an independently verified export that exhausts pagination. Schema validation, endpoint hashes, a payload digest, row reconciliation and scope checks detect inconsistencies. The stated ability to cover a range comes from the operator's attestation, not cryptographic proof of absence. A mismatched watermark, duplicate row, unavailable receipt, missing comparison attribute or invalid log leaves a gap. The missing data cannot be replaced by assumptions. The snapshot is capped at 8 MiB, 500 transactions and 2,000 logs. Synthetic tests validate known-positive and known-empty 30-day windows as well as corrupt metadata.

## Planner and evidence

Without model configuration, the objective is mapped deterministically to activity, flows or overview and an explicitly requested 7/30-day window. The same step also derives a reviewable plan: focus areas, ordered bounded tools (scope, identity, transactions, events, corroboration, analysis, report), page/event/receipt budgets, and constraints such as manifest-only membership and "missing data is not zero activity". Code validates the proposed fields and the resulting plan before anyone can execute it. Model planning is opt-in through both `OPENAI_API_KEY` and `CHAININQUEST_PLANNER_MODEL`. Only the objective is sent to the provider. Avoid putting secrets or sensitive context in it. The model proposes a strict goal/window object with no tool access, URL changes or membership authority; it cannot change the deterministic focus, checks or budget. The preview requires confirmation, expires after five minutes and is single-use. Failure falls back to the local policy with a recorded category. Estimated cost is recorded only if rates are configured through `CHAININQUEST_MODEL_INPUT_USD_PER_M` and `CHAININQUEST_MODEL_OUTPUT_USD_PER_M`. Token usage and latency are recorded when available. The tests exercise the model transport with mocked responses. No paid model call was made for acceptance. [Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs)

A job may make at most one bounded supplementary attempt after a retryable failure (`index_transport`, `index_http_error`, `rpc_transport`, `rpc_http_error`, `timeout`) when the remaining chain budget is at least 15 seconds. A successful retry replaces the earlier issue; a failed retry remains an explicit gap. Deterministic conclusions separate established facts, labeled inferences, unknowns and evidence gaps, and withhold counts when observed activity is zero but coverage is incomplete. The optional model never supplies evidence, attribution or final counts.

## Closed-beta deployment gate

The Python service remains loopback-only. Its local mode is for a trusted single operator. Before inviting any external tester, configure an HTTPS origin and per-user token digests through `CHAININQUEST_BETA_ORIGIN` and `CHAININQUEST_BETA_TOKEN_HASHES`. Example configuration shape:

```text
CHAININQUEST_BETA_ORIGIN=https://beta.example.org
CHAININQUEST_BETA_TOKEN_HASHES={"tester":"<64 lowercase SHA-256 hex digits>"}
```

Generate a separate unpredictable token for each tester using a cryptographic random generator. Store only its SHA-256 digest in server configuration and deliver the token privately. In closed-beta mode, job creation, preview, resume, reports and exports require authenticated access. Each account has its own filesystem namespace. Job IDs are not bearer credentials. API budgets limit GET and POST per authenticated principal. HTTP concurrency, active investigation concurrency, pending approvals, inputs, storage and investigation attempts are bounded. Static assets and the sample contain no private data.

Deploy behind a managed TLS reverse proxy with the exact configured Host, no public listener on the backend port, a private service account and state volume, edge request-size and rate limits, and access logs that omit Authorization headers and request bodies. Do not establish forwarding or weaken proxy safeguards to work around a local failure. Verify TLS and origin checks, isolation, rotation and revocation, abort and restart behavior, and resource limits in the actual deployment before inviting users. Local security tests cover unauthorized access, cross-user reads and resumes, cross-origin writes, replayed approvals, quotas and HTML escaping. No hosted deployment was provisioned or independently security-reviewed in this sprint.

### Closed-beta runbook (prepared, not executed)

This runbook is documentation only. No host, DNS record, certificate or credential was created. Hosting requires explicit owner approval.

1. Install Python 3.10 or newer, then `python3 -m pip install -r requirements.txt -r skills/onchain-address-inspector/requirements-abi.txt`.
2. Generate one random token per tester locally, store only its SHA-256 digest, and deliver the token out of band. Never paste a token into a manifest, URL, report or commit.
3. Export `CHAININQUEST_BETA_ORIGIN` and `CHAININQUEST_BETA_TOKEN_HASHES`, keep the backend bound to loopback (`python3 web/server.py --port 8080 --store /var/lib/chaininquest`), and run it under a dedicated service account with a private state volume.
4. Terminate TLS at a managed reverse proxy, preserve the configured Host header, enforce request-size and rate limits, and scrub `Authorization` and request bodies from access logs.
5. Before inviting testers, verify: unauthenticated preview/job/report/export requests are rejected; one tester cannot read or resume another tester's job; cross-origin writes are rejected; approvals expire and cannot be replayed; restart preserves completed jobs and resume state; storage and concurrency limits hold; token rotation revokes the old digest.

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHAININQUEST_BETA_ORIGIN` | Exact HTTPS origin allowed for browser writes | unset (loopback only) |
| `CHAININQUEST_BETA_TOKEN_HASHES` | JSON map of account to SHA-256 token digest | unset (no accounts) |
| `CHAININQUEST_ETHEREUM_RPC`, `CHAININQUEST_BASE_RPC`, `CHAININQUEST_MONAD_RPC` | Chain RPC overrides on the allowlist | built-in public endpoints |
| `CHAININQUEST_INDEX_MAX_PAGES` | Page cap for historical index paging (1–200) | 60 |
| `CHAININQUEST_HISTORY_STATE` | Default resume-state path for the CLI | unset |
| `CHAININQUEST_PLANNER_MODEL`, `OPENAI_API_KEY` | Optional planner model; both required | unset (deterministic) |
| `CHAININQUEST_MODEL_INPUT_USD_PER_M`, `CHAININQUEST_MODEL_OUTPUT_USD_PER_M` | Optional cost rates for recorded estimates | unset |

All chain access is read-only. The service never signs or broadcasts transactions, and the RPC allowlist rejects unapproved hosts.

## Live acceptance and reproduction

The five manifests in [examples/beta](../examples/beta) pin Ethereum block 26468359 and point to curators' primary deployment sources. PublicNode and dRPC returned the same block hash, direct transaction rows, status, raw value and event identity/data for the tested filters. Circle USDC produced 12 transactions and 171 events. Aave, ENS, Lido and Uniswap Factory had zero matching rows in that fixed block after both sources finished the bounded queries. These are short positive and empty historical windows only.

Additional mined samples were independently sourced from Blockscout and individually checked against RPC transaction hash, raw value and receipt status. Aave at 26468358 returned 1 transaction and 2 events. ENS at 26468155, Lido at 26468273, and Uniswap at 26436643 initially received HTTP 403 from the original RPC. Resuming the saved jobs on dRPC returned 1/2, 1/2 and 1/1 transactions/events respectively. These checks do not establish cross-source event exhaustiveness for those additional activity samples. V3's earlier multi-chain measurements remain documented separately.

On Base Mainnet, the Circle USDC and CCTP contracts completed blocks 60650237–60650238 on dRPC and PublicNode. Both returned one direct transaction and eight events, with matching end block hash, transaction hash, status, raw wei and event block/hash/index/topics/data. To rerun, keep only the Base deployment from `examples/circle-multichain.json`, set the two explicit block heights, repeat with `CHAININQUEST_BASE_RPC=https://base.drpc.org` and `CHAININQUEST_BASE_RPC=https://base-rpc.publicnode.com`, and run the evidence verifier on both outputs. These observations exercise the same chain-scoped metrics across networks over short windows.

The no-model natural-language CLI run selected `flows`, verified two Circle Monad Testnet contracts and completed blocks 81761138–81761141. A separate local Web API run previewed and confirmed the plan, queried the same contracts on QuickNode at blocks 81762695–81762698, and retrieved both report formats. Both used the existing RPC investigation. The model-backed path remains tested through mocks only.

A local Chrome run exercised natural-language entry, plan preview, contract confirmation, progress, and the Aave result. The original Ethereum node returned HTTP 403; the report showed unknown activity. Reopening the same job from persisted state and resuming on dRPC completed the original one-block scope, matching the zero-activity reference. The evidence verifier passed on the recovered report.

The V4 RC report UI was exercised again in the same local browser flow with the Circle Monad example over recent blocks 81798886–81798889. The run completed with attested coverage, rendered the chain card, coverage status, metrics and the facts/inferences/unknowns sections, and showed no error banner. That window contained no activity, so the rendered zero is a proven zero, not an unknown placeholder.

The real 30-day Aave investigation resolved Ethereum blocks 26253076–26468402 for October 23 to November 22, 2026 UTC. It reported partial coverage. The public index timed out without an attested range, and dRPC event searches returned HTTP errors and exhausted the query deadline. There were no fully answered event ranges. This record predates the Routescan route and remains a valid example of a provider failure being shown as partial rather than zero.

## Real 30-day coverage acceptance

Three Ethereum contracts were investigated over rolling 30-day windows through the credential-free Routescan route with dRPC receipts. Each result is a single-address report with attested transaction and event coverage:

| Contract | Window (UTC) | Transactions | Verified events | Coverage |
| --- | --- | --- | --- | --- |
| Compound Timelock `0x6d903f…c33925` | 2026-10-23 07:33 – 2026-11-22 07:33 | 2 (2 success) | 40 | complete, attested |
| Compound Governor Bravo `0xc0da02…b66529` | 2026-10-23 07:33 – 2026-11-22 07:33 | 0 | 0 | complete, attested proven zero |
| Uniswap V3 Factory `0x1f9843…f984` | 2026-10-23 07:33 – 2026-11-22 07:33 | 18 (11 success, 7 failed) | 617 | complete, attested |

The Uniswap run used watermark block 26469288 (timestamp 1795334039) verified against the RPC header, one transaction page covering the full range, all five log queries, and a complete receipt pass with zero gaps after repairing provider rows that returned `logIndex`/`transactionIndex` as `"0x"`. Timelock and Bravo reconciled every indexed row against RPC identity, value, block and receipt fields. Bravo proves that a complete zero-activity window is distinguishable from an unknown window.

Reproduce one window (replace the address and dates as needed):

```sh
python3 skills/onchain-address-inspector/scripts/query.py \
  --address 0x1F98431c8aD98523631AE4a59f267346ea31F984 \
  --chain-id 1 --rpc https://ethereum.drpc.org --investigate --days 30 \
  --total-timeout 600 --history-state /tmp/uniswap-30d.json
```

Base and Monad 30-day windows remain credential-blocked: Base needs a paid Etherscan V2 plan and no operator snapshot was provisioned. Those runs must report `unknown` with `index_key_required`, not zero. High-activity 30-day contracts can still end `partial` when receipt verification exhausts the per-request budget; the report preserves that status and its gaps.

Reproduce the five reference comparisons from the repository root:

```sh
mkdir -p artifacts
for project in aave circle ens lido uniswap; do
  python3 skills/onchain-address-inspector/scripts/v3.py --input "examples/beta/$project.json" > "artifacts/v4-real-$project.json" || exit
done
python3 scripts/reconcile_beta.py artifacts examples/beta/ethereum-26468359-drpc.json
```

The saved [reference evidence](../examples/beta/ethereum-26468359-drpc.json) contains public dRPC data for block 26468359. It records raw values and event payloads to make the comparison repeatable even when a public API is limited. The RPC rerun still depends on the configured provider. Other raw probe outputs remain ignored under `artifacts/`. Fixture, public RPC, public index, authenticated index and model transport checks are distinct. Ethereum 30-day coverage has three attested live runs; Base and Monad 30-day coverage remain credential-blocked and are release gates, not completed acceptance.

## V4.1 closed beta productization

V4.1 does not add a new investigation engine. It removes the manifest-first entry barrier, makes coverage and recovery visible in the page, records structured tester feedback, and hardens state handling.

### Guided entry

The web page now asks for a project name, one to eight contract addresses, the HTTPS page that lists them, a goal and a scope (7 days, 30 days, 1–32 recent blocks or an explicit range under 32 blocks). The browser builds the manifest locally; the server still validates membership, attribution and limits. The plan preview renders the resolved ranges, attribution, ordered checks, rules and budget. Confirmation is single-use and expires after five minutes. The raw manifest remains available in the advanced section.

A run with no model objective now also shows the deterministic plan steps. The planner remains optional and cannot change membership, scope or budget.

### Feedback capture

After a report is ready, the page asks four closed questions (goal achieved, report understood, missing data noticed, reuse intent) plus an optional 500-character comment. Feedback is written atomically as `feedback.json` inside the job directory, so it follows the same account isolation and retention rules as the report. The server records elapsed seconds from job timestamps and the observed coverage counts, not client claims.

Summarize a store with:

```sh
python3 scripts/beta_feedback.py --store artifacts/web-investigations
```

### Retention

Finished jobs are pruned manually. The default is a dry run:

```sh
python3 scripts/beta_ops.py --store artifacts/web-investigations --days 30
python3 scripts/beta_ops.py --store artifacts/web-investigations --days 30 --apply
```

Running jobs and symlinks are skipped. Each candidate must be a direct, real child of the store or account directory, must have a readable state file with a valid age timestamp, and must not be `running` or `queued`. Deletion re-checks the state and takes the job's `.run.lock` immediately before removal, so a job that starts after planning is skipped as well. Malformed state, wrong directory names and paths outside the store are never deleted. There is no scheduler; pruning is an operator action.

### Deployment checks

`GET /healthz` returns `{"status":"ok"}` without authentication so a reverse proxy can probe liveness. When `CHAININQUEST_BETA_ORIGIN` is set, responses include `Strict-Transport-Security` and `X-Frame-Options: DENY` in addition to the existing CSP, nosniff, no-referrer and no-store headers. The service still terminates plain HTTP on loopback only; TLS is the proxy's job.

| Control | Implemented behavior |
| --- | --- |
| Access | Optional bearer tokens compared as SHA-256 digests in constant time; one namespace per account |
| Isolation | Job IDs are not credentials; cross-account reads, resumes and exports return 404 |
| Rate limits | 8 POST/min and 45 GET/10s per principal; 16 sockets; one active run; previews capped at 64 |
| Storage | 32 jobs per account, 32 MiB per result, 128 KiB per request |
| Run budget | 120s default (15–240s), three attempts, 8 contracts, 60 index pages |
| Redaction | RPC and index URLs omitted from reports; Authorization and bodies never logged; state files 0600, directories 0700 |
| Restart | Completed jobs and checkpoints persist; the page detects a stalled run and offers resume |

### Release hardening (V4.1 review)

The pre-release audit added failure-path checks without changing the investigation engine:

- Job state is validated against the expected schema on read. A truncated or type-corrupted `state.json` now raises `invalid_job` instead of a raw `KeyError` or `TypeError`, and the page reports it as a missing or invalid investigation.
- Etherscan resume state is reused only when the provider tag, address, block range, page counter, row shape, gap shape and `done` flag all validate. Any mismatch restarts that range from page one rather than trusting malformed rows.
- Pruning now re-validates the path, age, status and run lock at deletion time, as described under Retention.
- The plan preview and HTML report label contract attribution as declared by the requester. "Confirmed" means the requester cited a source; it is not independent verification of ownership. The report says so beside the attribution table.
- Feedback is reset when a new investigation is followed, so a previous job's thank-you state is not shown for another job.
- An incomplete range still renders as `Unknown (0 observed)`, never as a proven zero.

### Closed-beta acceptance checklist

Run these against the exact build being invited. Status reflects the local review; production items remain blocked until the owner approves a host.

| # | Check | Status |
| --- | --- | --- |
| 1 | `python3 -m unittest discover -s tests` passes | pass (154 tests, 1 skipped) |
| 2 | `ruff check .`, `ruff format --check .`, `node --check web/app.js`, `python3 scripts/check_package.py` pass | pass |
| 3 | Unauthenticated preview, job, result and export requests are rejected in closed-beta mode | pass (local test) |
| 4 | One account cannot read, resume or export another account's job | pass (local test) |
| 5 | Cross-origin POST, mismatched Host and replayed or expired approvals are rejected | pass (local test) |
| 6 | Pruning leaves running, queued, fresh, locked, malformed and symlinked entries untouched | pass (unit tests) |
| 7 | A corrupted state or resume file fails explicitly and recovers by restarting the affected range | pass (unit tests) |
| 8 | Incomplete coverage never renders as zero activity in JSON, HTML or the page | pass (unit tests) |
| 9 | Guided Circle example completes in a real browser with exports and feedback | pass (local, see below) |
| 10 | TLS, exact Host header, log scrubbing and rate limits at the proxy | blocked (no host) |
| 11 | Token issue, rotation and revocation with real testers | blocked (no testers) |
| 12 | Base and long Ethereum ranges use an approved snapshot or paid index plan | blocked (no credential) |

The single local browser run and the internal feedback entry are engineering checks. One internal run is not a user metric, and the sample must not be presented as real tester adoption.

### Release notes and tag plan (prepared, not published)

Draft tag: `v4.1.0-beta.1`, created only after PR #1 and PR #5 are merged into `main` and the post-merge CI run is green. Do not tag the feature branch.

Release notes draft:

- Guided project entry with contract list, declares source, goal and scope; manifest stays under Advanced.
- Reviewable plan preview with single-use, 5-minute approval and no-membership model planner.
- Readable report and HTML export separating established facts, labeled inferences, unknowns and evidence gaps.
- Structured post-report feedback stored per job with server-computed elapsed time and coverage.
- Safe manual retention tooling that skips running, locked, symlinked and malformed entries.
- Hardened state and resume validation; malformed files fail explicitly instead of crashing a request.
- Declared attribution is labelled as requester-provided, not independently verified ownership.
- Known limits: Base and long Ethereum ranges remain `unknown` without a paid index plan or operator snapshot; deployment, TLS and token operations have only been validated locally.

### Blocking items before any public invite

1. No host, DNS record, certificate, credential or public listener was created; deployment still needs explicit owner approval.
2. TLS must terminate at a managed proxy that preserves the exact Host header and enforces request-size and rate limits. The Python server is not a TLS server.
3. Token distribution, rotation and revocation have only been tested locally, not with real testers.
4. Base and long Ethereum ranges still need an operator snapshot or paid index plan; otherwise they correctly report `unknown`.
5. The store is single-process. Do not run multiple server workers against the same directory.
6. Retention and feedback summaries are manual commands, not scheduled jobs.
7. Feedback is per-job JSON, not an analytics product; there is no dashboard.
8. No independent security review of a live deployment has been performed.
