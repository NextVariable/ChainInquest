"""Opt-in investigation orchestration, separate from the legacy JSON schema."""

import datetime as dt
import json
import os
import time
from copy import copy
from pathlib import Path

from adapters import ADDRESS, HASH, RpcAdapter
from analytics import analyze
from chains import CHAINS
from event_decoder import (
    BYTES,
    TOPICS,
    AbiDecoder,
    decimal_amount,
    log_fields,
    token_event,
)
from evidence import QueryError, apply_receipt_status, parse_block, quantity, receipt_identity
from evidence_store import EvidenceStore
from query import (
    CHAIN_ID,
    DEFAULT_BLOCKS,
    MAX_BLOCKS,
    MAX_RECEIPTS,
    Progress,
    rpc_profile,
    scan,
    utc,
    validate,
)
from report import generate
from rpc_client import Client

UTC = dt.timezone.utc


def timestamp(value):
    return dt.datetime.fromtimestamp(value, UTC).isoformat()


def history_saver(path):
    """Load and atomically persist per-address history progress."""
    if not path:
        return None, None
    document = None
    try:
        with open(path, encoding="utf-8") as handle:
            candidate = json.load(handle)
        if isinstance(candidate, dict):
            document = candidate
    except (OSError, ValueError, RecursionError):
        document = None
    failed = False

    def save(value):
        nonlocal failed
        if failed:
            return
        try:
            body = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            if len(body) > 16 * 1024 * 1024:
                raise ValueError
            target = Path(path)
            temporary = target.with_suffix(target.suffix + ".tmp")
            temporary.write_text(body, encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        except (OSError, ValueError, RecursionError):
            failed = True

    return document, save


def stream_status(part):
    if part.get("complete"):
        return "complete"
    gaps = part.get("gaps") or []
    if part.get("method") in {None, "none_configured"}:
        return "unknown"
    if part.get("observed") or part.get("status") == "partial":
        return "partial"
    return "failed" if gaps else "unknown"


def finalize_coverage(coverage):
    for name in ("transactions", "events", "receipts"):
        coverage[name]["status"] = stream_status(coverage[name])
    states = {coverage[name]["status"] for name in ("transactions", "events", "receipts")}
    if coverage["complete"]:
        coverage["status"] = "complete"
    elif states <= {"failed"}:
        coverage["status"] = "failed"
    elif "partial" in states or "complete" in states:
        coverage["status"] = "partial"
    else:
        coverage["status"] = "unknown"


def time_window(args, now):
    if args.days is None and args.from_time is None and args.to_time is None:
        return None
    if any(x is not None for x in (args.start_block, args.end_block, args.recent_blocks)):
        raise QueryError("Time and block ranges are mutually exclusive", "invalid_range")
    if args.days:
        if args.from_time is not None or args.to_time is not None:
            raise QueryError("--days cannot be combined with timestamps", "invalid_range")
        return now - args.days * 86400, now
    try:
        if not (args.from_time and args.to_time):
            raise ValueError
        times = [
            dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
            for t in (args.from_time, args.to_time)
        ]
        if any(t.tzinfo is None or t.microsecond for t in times):
            raise ValueError
        a, b = (int(t.timestamp()) for t in times)
        if not 0 <= a < b <= now or b - a > 366 * 86400:
            raise ValueError
        return a, b
    except (ValueError, OverflowError, OSError):
        raise QueryError(
            "Provide a UTC-offset time window of at most 366 days, ending by now", "invalid_range"
        ) from None


def get_transaction(rpc, h, n, address):
    raw = rpc.call("eth_getTransactionByHash", [h])
    if not isinstance(raw, dict) or str(raw.get("hash")).lower() != h:
        raise QueryError("Indexed transaction identity not corroborated", "index_mismatch")
    _, block_hash, seconds = rpc.header(n)
    found, _ = parse_block(
        {
            "number": hex(n),
            "hash": block_hash,
            "parentHash": "0x" + "0" * 64,  # parse_block only validates its format here.
            "timestamp": hex(seconds),
            "transactions": [raw],
        },
        n,
        address,
        raw=True,
    )
    if not found:
        raise QueryError("Indexed transaction does not match target", "index_mismatch")
    return found[0]


def legacy_transactions(args, rpc, start, end):
    options = copy(args)
    options.start_block, options.end_block, options.recent_blocks = start, end, None
    options.progress = False
    # Reuse the legacy scanner without transferring ownership of the shared client.
    out = scan(options, rpc)
    anchor = rpc.header(end)
    if not any(
        h["block"] == end and h["hash"] == anchor[1] for h in out["coverage"]["block_headers"]
    ):
        out["coverage"]["chain_consistent"] = False
    coverage = out["coverage"]
    gaps = [{"reason": "block_fetch", "block": n} for n in coverage["failed_blocks"]] + coverage[
        "data_anomalies"
    ]
    if not coverage["chain_consistent"]:
        gaps.append({"reason": "chain_consistency"})
    # Until corroboration succeeds, retain the raw transactions only as evidence.
    return out["transactions"], {
        "method": "rpc_block_scan",
        "complete": coverage["scan_complete"] and not gaps,
        "observed": bool(coverage["successful_blocks"]) and coverage["chain_consistent"],
        "usable": coverage["chain_consistent"],
        "gaps": gaps,
        "errors": out["errors"],
    }


def indexed_transactions(rpc, index, address, start, end, state=None, save=None):
    if index is None:
        return [], {
            "method": "none_configured",
            "status": "unknown",
            "complete": False,
            "observed": False,
            "gaps": [{"reason": "index_key_required", "from": start, "to": end}],
        }
    candidates, gaps = index.discover(address, start, end, state=state, save=save)
    attestation = getattr(index, "coverage", {})
    attested = attestation.get("attested") is True
    txs = []
    for h, n, claims in candidates:
        try:
            tx = get_transaction(rpc, h, n, address)
            tx["index_comparison"] = {"claims": claims}
            txs.append(tx)
        except QueryError as error:
            gaps.append({"hash": h, "block": n, "reason": error.category})
    # Never infer that an empty page means no activity. Zero is observable only
    # when the provider watermark and pagination are both attested.
    return txs, {
        "method": getattr(index, "name", "etherscan_v2_txlist"),
        "complete": attested and not gaps,
        "observed": attested or bool(txs),
        "index_coverage": attestation,
        "pagination_and_corroboration_complete": not gaps,
        "gaps": gaps
        + (
            []
            if attested
            else [{"reason": "index_coverage_not_attested", "from": start, "to": end}]
        ),
    }


def compare_index_claims(tx, coverage):
    comparison = tx["index_comparison"]
    claims = comparison["claims"]
    actual = {
        "from": tx["from"],
        "to": tx["to"] or "",
        "value": tx["value_wei"],
        "blockHash": tx["block_hash"],
        "timeStamp": str(int(dt.datetime.fromisoformat(tx["timestamp"]).timestamp())),
    }
    if tx["status"] != "unknown":
        actual["txreceipt_status"] = "1" if tx["status"] == "success" else "0"
        actual["isError"] = "0" if tx["status"] == "success" else "1"
    mismatches = [
        key for key, value in actual.items() if key in claims and claims[key].lower() != value
    ]
    missing = [
        key for key in ("from", "to", "value", "blockHash", "timeStamp") if key not in claims
    ]
    comparison.update(
        {
            "mismatches": mismatches,
            "unverified_fields": missing,
            "receipt_status_claimed": bool({"txreceipt_status", "isError"} & claims.keys()),
        }
    )
    # Only the RPC-derived record contributes to sample metrics.
    if mismatches or missing:
        coverage["pagination_and_corroboration_complete"] = False
        coverage["complete"] = False
        coverage["gaps"].append(
            {
                "reason": "index_claim_mismatch" if mismatches else "index_claim_unverified",
                "hash": tx["hash"],
                "fields": mismatches or missing,
            }
        )


def check_event(rpc, cache, event):
    _, h, seconds = rpc.header(event["block"])
    if h != event["block_hash"]:
        raise QueryError("Log block hash mismatch", "log_mismatch")
    transaction = {"hash": event["transaction_hash"], "block_hash": h, "block": event["block"]}
    raw_receipt = cache.receipt(rpc, transaction["hash"])
    apply_receipt_status(raw_receipt, transaction)
    logs = raw_receipt.get("logs")
    if (
        transaction["status"] != "success"
        or quantity(raw_receipt.get("transactionIndex")) != event["transaction_index"]
        or not isinstance(logs, list)
    ):
        raise QueryError("Receipt cannot corroborate log", "log_mismatch")
    matches = [
        row
        for row in logs
        if isinstance(row, dict) and row.get("logIndex") == hex(event["log_index"])
    ]
    if len(matches) != 1 or log_fields(matches[0]) != event:
        raise QueryError("Log is missing or conflicts with receipt", "log_mismatch")
    return timestamp(seconds), receipt_identity(raw_receipt)


def token_metadata(rpc, token, block):
    raw = rpc.call("eth_call", [{"to": token, "data": "0x313ce567"}, hex(block)])
    if not isinstance(raw, str) or not HASH.fullmatch(raw) or int(raw, 16) > 255:
        raise QueryError("Invalid decimals() result", "decimals_unavailable")
    return int(raw, 16)


def get_events(
    rpc,
    cache,
    address,
    first,
    last,
    abi,
    window,
    history=None,
    log_state=None,
    save_logs=None,
):
    participant = "0x" + "0" * 24 + address[2:]
    filters = [
        {"topics": [list(TOPICS), participant]},
        {"topics": [list(TOPICS), None, participant]},
        {"address": address},
    ]
    supplied = getattr(history, "logs", None)
    index_logs = getattr(history, "log_coverage", None) if history is not None else None
    if supplied is not None:
        rows, gaps = [(row, first, last, 3) for row in supplied], []
        filters.append({"operator_scope": "emitter_or_erc20_participant_v1"})
    elif history is not None and hasattr(history, "discover_logs"):
        rows, gaps = history.discover_logs(address, first, last, state=log_state, save=save_logs)
        index_logs = history.log_coverage
    else:
        rows, gaps = rpc.logs(first, last, filters, state=log_state, save=save_logs)
    accepted, rejected, conflicts = {}, [], set()
    decimals = {}
    for raw, a, b, i in rows:
        try:
            event = log_fields(raw)
            topics = event["topics"]
            if (
                not a <= event["block"] <= b
                or (i == 2 and event["contract"] != address)
                or (
                    i == 3
                    and event["contract"] != address
                    and not (topics and topics[0] in TOPICS and participant in topics[1:3])
                    or i < 2
                    and (
                        len(topics) <= i + 1
                        or topics[0] not in TOPICS
                        or topics[i + 1] != participant
                    )
                )
            ):
                raise QueryError("Log falls outside requested filter", "log_mismatch")
        except QueryError as error:
            rejected.append({"reason": error.category, "raw_log": raw, "filter": i})
            gaps.append({"reason": error.category, "from": a, "to": b, "filter": i})
            continue
        key = event["block"], event["log_index"]
        if key in conflicts:
            rejected.append({"reason": "conflicting_log", "raw_log": raw})
            continue
        if key in accepted:
            prior, sources, prior_event = accepted[key]
            if prior_event != event or i in sources:
                rejected.extend(
                    {"reason": "conflicting_or_duplicate_log", "raw_log": item}
                    for item in (prior, raw)
                )
                gaps.append({"reason": "conflicting_or_duplicate_log", "block": key[0]})
                conflicts.add(key)
                del accepted[key]
            else:
                sources.add(i)
            continue
        accepted[key] = raw, {i}, event
    events = []
    for _, (raw, sources, event) in sorted(accepted.items()):
        try:
            stamp, matched_receipt = check_event(rpc, cache, event)
            if window and not window[0] <= rpc.header(event["block"])[2] < window[1]:
                raise QueryError("Log timestamp falls outside requested window", "time_mismatch")
        except QueryError as error:
            rejected.append({"reason": error.category, "raw_log": raw})
            gaps.append({"reason": error.category, "block": event["block"]})
            continue
        entry = {
            **event,
            "raw_log": raw,
            "receipt_evidence": matched_receipt,
            "timestamp": stamp,
            "filters": sorted(sources),
            "token": None,
            "abi": None,
            "issues": [],
        }
        try:
            entry["token"] = token_event(event)
        except QueryError as error:
            entry["issues"].append(error.category)
        token = entry["token"]
        if token:
            key = event["contract"], event["block"]
            if key not in decimals:
                try:
                    decimals[key] = token_metadata(rpc, *key)
                except QueryError as error:
                    decimals[key] = error.detail()
            value = decimals[key]
            if isinstance(value, dict):
                entry["decimals_error"] = value
                value = None
            token.update(
                {
                    "decimals": value,
                    "amount_display": decimal_amount(token["amount_raw"], value),
                    "decimals_at_block": event["block"] if value is not None else None,
                }
            )
            if value is None:
                entry["issues"].append("decimals_unavailable")
        if abi:
            try:
                entry["abi"] = abi.decode(event)
            except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, RecursionError):
                entry["issues"].append("abi_decode_failed")
        events.append(entry)
    if supplied is not None:
        method = "operator_snapshot_and_rpc_receipts"
        basis = "operator-attested log scope, included logs reconciled against RPC"
        complete = not gaps
        attested = True
    elif history is not None and hasattr(history, "discover_logs"):
        method = "index_logs_and_rpc_receipts"
        basis = "provider log pagination plus RPC receipt reconciliation"
        attested = bool((index_logs or {}).get("attested"))
        complete = attested and not gaps
    else:
        method = "rpc_getLogs_and_receipts"
        basis = "RPC responses, no independent omission proof"
        attested = False
        complete = not gaps
    observed = bool(events) or complete or (attested and not gaps)
    return (
        events,
        rejected,
        {
            "method": method,
            "status": "complete"
            if complete
            else "partial"
            if (events or rows)
            else "failed"
            if gaps
            else "unknown",
            "complete": complete,
            "observed": observed,
            "attested": attested,
            "filters": filters,
            "gaps": gaps,
            "rpc_attempt_failures": rpc.log_failures,
            "rpc_answered_ranges": rpc.log_answered_ranges,
            "initial_log_span": rpc.log_span,
            "index_log_coverage": index_logs,
            "completeness_basis": basis,
        },
    )


def classify_interactions(rpc, txs):
    cache = {}
    for tx in txs:
        to = tx["to"]
        if to is None:
            tx["interaction_at_block_end"] = "creation_attempt"
            continue
        key = to, tx["block"]
        if key not in cache and len(cache) < 32:
            try:
                code = rpc.call("eth_getCode", [to, hex(tx["block"])])
                cache[key] = (
                    ("recipient_has_code" if code != "0x" else "recipient_no_code")
                    if isinstance(code, str) and BYTES.fullmatch(code)
                    else "unknown"
                )
            except QueryError:
                cache[key] = "unknown"
        tx["interaction_at_block_end"] = cache.get(key, "unknown")


def investigate(args, client=None, index=None, now=None):
    now = int(dt.datetime.now(UTC).timestamp()) if now is None else now
    chain_id = getattr(args, "chain_id", CHAIN_ID)
    chain = CHAINS[chain_id]
    address = args.address.lower() if ADDRESS.fullmatch(args.address) else None
    history, persist = history_saver(getattr(args, "history_state_path", None))
    if not isinstance(history, dict):
        history = {}
    history.update({"schema_version": 1, "chain_id": chain_id, "address": address})

    def save_transactions(state):
        history["transactions"] = state
        if persist:
            persist(history)

    def save_logs(state):
        history["logs_index"] = state
        if persist:
            persist(history)

    coverage = {
        name: {"complete": False, "gaps": []} for name in ("transactions", "events", "receipts")
    }
    coverage["complete"] = False
    source = {
        "rpc": "configured endpoint (URL omitted)",
        "network": chain.name,
        "native_symbol": chain.symbol,
        "rpc_profile": rpc_profile(args.rpc),
        "limits": {
            "timeout_seconds": args.timeout,
            "total_timeout_seconds": args.total_timeout,
            "retries": args.retries,
            "concurrency": args.concurrency,
        },
        "chain_id": None,
        "query_started_at": timestamp(now),
    }
    requested = {
        "days": args.days,
        "from_time": None,
        "to_time": None,
        "start_block": args.start_block,
        "end_block": args.end_block,
        "recent_blocks": args.recent_blocks,
    }
    resolved = None
    txs, events, rejected, errors = [], [], [], []
    cache = EvidenceStore()
    stable = False
    complete_day_range = (None, None)
    progress = Progress(args.progress)
    progress.start()
    raw_client = None
    try:
        window = time_window(args, now)
        validate(args)
        if window and args.days is None:
            requested["from_time"], requested["to_time"] = map(timestamp, window)
        abi = None
        if args.abi_file:
            try:
                abi = AbiDecoder.load(args.abi_file)
            except QueryError as error:
                errors.append(error.detail())
        raw_client = client or Client(
            args.rpc,
            args.timeout,
            args.retries,
            args.total_timeout,
            args.transport,
            args.concurrency,
        )
        rpc = RpcAdapter(raw_client)
        source["chain_id"] = quantity(rpc.call("eth_chainId", []))
        if source["chain_id"] != chain_id:
            raise QueryError(f"Expected chain ID {chain_id}", "wrong_chain")
        tip, tip_hash, tip_seconds = rpc.header("latest")
        time_gaps = []
        progress.update("range_resolution")
        if window:
            a, b = window
            if args.to_time is None and b > tip_seconds + 1:
                b = tip_seconds + 1
                window = (a, b)
            first, last = rpc.lower_bound(a, tip), rpc.lower_bound(b, tip) - 1
            genesis_time = rpc.header(0)[2] if first == 0 else a
            if a < genesis_time:
                time_gaps.append(
                    {
                        "reason": "before_genesis",
                        "from_time": timestamp(a),
                        "to_time": timestamp(min(genesis_time, b)),
                    }
                )
            if b > tip_seconds + 1:
                time_gaps.append(
                    {
                        "reason": "after_rpc_head",
                        "from_time": timestamp(max(a, tip_seconds + 1)),
                        "to_time": timestamp(b),
                    }
                )
            complete_day_range = timestamp(a), timestamp(b - 1)
        else:
            last = tip if args.end_block is None else args.end_block
            first = (
                args.start_block
                if args.start_block is not None
                else max(0, last - (args.recent_blocks or DEFAULT_BLOCKS) + 1)
            )
            if last > tip:
                raise QueryError("End block exceeds RPC head", "invalid_range")
        resolved = {
            "start_block": first if first <= last else None,
            "end_block": last if first <= last else None,
            "from_time_inclusive": timestamp(window[0]) if window else None,
            "to_time_exclusive": timestamp(window[1]) if window else None,
            "rpc_head": {"block": tip, "hash": tip_hash, "timestamp": timestamp(tip_seconds)},
        }
        if first <= last:
            anchor = rpc.header(last)
            resolved["end_block_hash"] = anchor[1]
            if getattr(args, "verify_contract", False):
                progress.update("contract_identity")
                code = rpc.call("eth_getCode", [address, hex(last)])
                if not isinstance(code, str) or not BYTES.fullmatch(code) or code == "0x":
                    raise QueryError(
                        "Contract bytecode unavailable at pinned snapshot",
                        "contract_identity_unverified",
                    )
                from citations import digest

                coverage["contract_identity"] = {
                    "bytecode_present": True,
                    "block": last,
                    "block_hash": anchor[1],
                    "bytecode_json_sha256": digest(code.lower()),
                }
            with rpc.reserve_snapshot_budget():
                progress.update("transactions")
                if last - first + 1 <= MAX_BLOCKS:
                    txs, coverage["transactions"] = legacy_transactions(args, rpc, first, last)
                else:
                    key = os.environ.get("ETHERSCAN_API_KEY")
                    if index is None:
                        from history import provider_for

                        index = provider_for(chain_id, raw_client, rpc, key)
                    source["transaction_index"] = getattr(index, "name", "none_configured")
                    txs, coverage["transactions"] = indexed_transactions(
                        rpc,
                        index,
                        address,
                        first,
                        last,
                        state=history.get("transactions"),
                        save=save_transactions,
                    )
                if window and any(
                    not window[0]
                    <= int(dt.datetime.fromisoformat(tx["timestamp"]).timestamp())
                    < window[1]
                    for tx in txs
                ):
                    coverage["transactions"]["complete"] = False
                    coverage["transactions"]["usable"] = False
                    coverage["transactions"]["observed"] = False
                    coverage["transactions"]["gaps"].append(
                        {"reason": "transaction_timestamp_mismatch"}
                    )
                txs.sort(key=lambda t: (t["block"], t["hash"]))
                if coverage["transactions"]["method"] != "rpc_block_scan":
                    for tx in txs[:MAX_RECEIPTS]:
                        try:
                            apply_receipt_status(cache.receipt(rpc, tx["hash"]), tx)
                        except QueryError as error:
                            tx["receipt_error"] = error.detail()
                        compare_index_claims(tx, coverage["transactions"])
                else:
                    failures = {
                        e["hash"]: e for e in coverage["transactions"]["errors"] if "hash" in e
                    }
                    for tx in txs:
                        if tx["status"] == "unknown" and tx["hash"] in failures:
                            tx["receipt_error"] = failures[tx["hash"]]
                progress.update("events")
                events, rejected, coverage["events"] = get_events(
                    rpc,
                    cache,
                    address,
                    first,
                    last,
                    abi,
                    window,
                    index,
                    log_state=history.get("logs_index"),
                    save_logs=save_logs,
                )
                if coverage["events"]["method"].startswith(("index_logs", "operator_snapshot")):
                    source["event_index"] = getattr(index, "name", "operator_history_snapshot")
                classify_interactions(rpc, txs)
            progress.update("snapshot")
            stable = rpc.header(last, fresh=True) == anchor and (
                not window or rpc.header(tip, fresh=True) == (tip, tip_hash, tip_seconds)
            )
        else:
            stable = rpc.header(tip, fresh=True) == (tip, tip_hash, tip_seconds)
            for name in ("transactions", "events"):
                coverage[name] = {"method": "empty_resolved_range", "complete": True, "gaps": []}
        if not stable:
            time_gaps.append({"reason": "snapshot_changed"})
        for name in ("events", "transactions"):
            coverage[name]["gaps"].extend(time_gaps)
            coverage[name]["complete"] &= not time_gaps
        unknown = [tx["hash"] for tx in txs if tx["status"] == "unknown"]
        coverage["receipts"] = {
            "method": "rpc_receipts" if txs else "not_needed",
            "complete": coverage["transactions"]["complete"] and not unknown,
            "observed": coverage["transactions"].get("observed", bool(txs)),
            "unknown_transaction_hashes": unknown,
            "gaps": list(time_gaps) + [{"reason": "receipt_unknown", "hash": h} for h in unknown],
        }
        if args.abi_file:
            coverage["abi"] = {
                "loaded": abi is not None,
                "decoded": sum(e["abi"] is not None for e in events),
                "decode_failed": sum("abi_decode_failed" in e["issues"] for e in events),
            }
        coverage["complete"] = not errors and all(
            coverage[k]["complete"] for k in ("transactions", "events", "receipts")
        )
    except (QueryError, ValueError, OverflowError, OSError) as error:
        stable = False
        errors.append(
            error.detail()
            if isinstance(error, QueryError)
            else {
                "category": "invalid_response",
                "error": "Could not validate query inputs or response",
            }
        )
        for name in ("transactions", "events", "receipts"):
            coverage[name]["complete"] = False
            coverage[name]["gaps"].append({"reason": errors[-1]["category"]})
    finally:
        progress.finish()
        source["query_completed_at"] = utc()
        source["elapsed_seconds"] = round(time.monotonic() - progress.started, 3)
        if isinstance(raw_client, Client):
            source["performance"] = raw_client.diagnostics()
            raw_client.close()
    if not stable:
        coverage["transactions"]["observed"] = False
        if "contract_identity" in coverage:
            coverage["contract_identity"]["bytecode_present"] = None
    analytics = analyze(
        address,
        txs if stable and coverage["transactions"].get("usable", True) else [],
        events if stable else [],
        *(complete_day_range if stable else (None, None)),
        coverage,
    )
    coverage["token_metadata"] = {
        "scope": "decoded ERC-20 events in the returned sample",
        "decoded_events": sum(e["token"] is not None for e in events),
        "historical_decimals_unavailable": [
            {"transaction_hash": e["transaction_hash"], "log_index": e["log_index"]}
            for e in events
            if e.get("decimals_error")
        ],
    }
    if chain_id != CHAIN_ID:
        for tx in txs:
            tx["value_native"] = tx.pop("value_mon")
            tx["explorer_url"] = chain.explorer + tx["hash"]
        if analytics["transactions"]:
            native = analytics["transactions"]["native_value_successful_top_level_only"]
            for direction in ("in", "out"):
                native[f"{direction}_native"] = native.pop(f"{direction}_mon")
    finalize_coverage(coverage)
    evidence = cache.export(txs, events, rejected, stable)
    return {
        "schema_version": "2.0",
        "coverage": coverage,
        "errors": errors,
        "report": generate(address, requested, resolved, source, coverage, evidence, analytics),
    }
