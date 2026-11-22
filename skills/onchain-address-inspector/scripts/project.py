"""Aggregate saved, comparable address investigations for a curated project manifest."""

import datetime as dt
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from adapters import ADDRESS, HASH
from citations import digest, evidence_catalog
from event_decoder import log_fields, token_event
from evidence import QueryError, apply_receipt_status, invalid_constant, unique_object
from query import CHAIN_ID, utc

MAX_CONTRACTS = 32
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 64 * 1024 * 1024
RANGE_KEYS = (
    "start_block",
    "end_block",
    "from_time_inclusive",
    "to_time_exclusive",
    "end_block_hash",
)


def load_json(path, limit):
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input exceeds byte budget")
    return json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant), data


def parse_time(value):
    result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() is None:
        raise ValueError("Missing timezone")
    return result.astimezone(dt.timezone.utc)


def source_url(value):
    if not isinstance(value, str) or not re.fullmatch(r"https://[^\s@?#]{1,500}", value):
        raise ValueError("Expected a credential-free HTTPS evidence URL")
    if not urlsplit(value).hostname:
        raise ValueError("Missing evidence URL host")
    return value


def contracts(manifest, *, chain_id=CHAIN_ID, allow_unknown_deployment=False):
    if type(manifest["schema_version"]) is not str or manifest["schema_version"] != "1":
        raise ValueError("Unsupported project manifest")
    name = manifest["project"]
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("Invalid project label")
    entries, seen = [], set()
    deployments = manifest["deployments"]
    if not isinstance(deployments, list) or not 1 <= len(deployments) <= MAX_CONTRACTS:
        raise ValueError("Invalid deployment count")
    deployment_ids = set()
    for dep in deployments:
        deployment = dep["id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", deployment) or deployment in deployment_ids:
            raise ValueError("Invalid or repeated deployment id")
        deployment_ids.add(deployment)
        chain = dep["chain_id"]
        if type(chain) is not int or chain != chain_id:
            raise ValueError("Unexpected chain for aggregation")
        if not isinstance(dep["contracts"], list) or not dep["contracts"]:
            raise ValueError("Empty contract list")
        for entry in dep["contracts"]:
            address = entry["address"].lower()
            if not ADDRESS.fullmatch(address) or address in seen or len(seen) >= MAX_CONTRACTS:
                raise ValueError("Invalid or duplicate contract")
            seen.add(address)
            attribution = entry["attribution"]
            status = attribution["status"]
            if status not in {"confirmed", "unconfirmed", "disputed"}:
                raise ValueError("Unknown attribution status")
            block = entry.get("deployment_block")
            timestamp = entry.get("deployed_at")
            if block is not None and (type(block) is not int or block < 0):
                raise ValueError("Invalid deployment block")
            deployed = parse_time(timestamp).isoformat() if timestamp is not None else None
            sources = attribution.get("sources", [])
            if not isinstance(sources, list) or len(sources) > 16:
                raise ValueError("Invalid attribution sources")
            sources = [source_url(url) for url in sources]
            if status == "confirmed" and (
                not sources
                or ((block is None or deployed is None) and not allow_unknown_deployment)
            ):
                raise ValueError("Confirmed contract requires sources, deployment block and time")
            entries.append(
                {
                    "deployment_id": deployment,
                    "chain_id": chain,
                    "address": address,
                    "deployment_block": block,
                    "deployed_at": deployed,
                    "attribution": {"status": status, "sources": sources},
                    "report_file": entry.get("report_file"),
                }
            )
    return name, entries


def report_inputs(result, address, chain_id=CHAIN_ID):
    report = result["report"]
    source = report["data_sources"]
    evidence = report["on_chain_evidence"]
    resolved = report["resolved_range"]
    if (
        result["schema_version"] != "2.0"
        or report["target"] != address
        or source["chain_id"] != chain_id
        or evidence["snapshot_checked"] is not True
        or not HASH.fullmatch(resolved["end_block_hash"])
    ):
        raise ValueError("Ineligible address report")
    first, last = resolved["start_block"], resolved["end_block"]
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise ValueError("Invalid resolved bounds")
    start_time, end_time = resolved["from_time_inclusive"], resolved["to_time_exclusive"]
    if (start_time is None) != (end_time is None) or (
        start_time is not None and parse_time(start_time) >= parse_time(end_time)
    ):
        raise ValueError("Invalid resolved time bounds")
    return report, tuple(resolved[key] for key in RANGE_KEYS)


def collect(report, contract, bounds):
    address = contract["address"]
    evidence = report["on_chain_evidence"]
    inputs = report["calculation"]
    catalog = report["evidence_index"]
    selected = {}
    for group, refs in (
        ("transactions", inputs["transaction_refs"]),
        ("events", inputs["event_refs"]),
    ):
        if not isinstance(refs, list) or len(refs) != len(set(refs)):
            raise ValueError("Invalid calculation references")
        rows = evidence[group]
        refs = set(refs)
        output = []
        for i, row in enumerate(rows):
            ref = row["evidence_ref"]
            if ref not in refs:
                continue
            refs.remove(ref)
            raw = row["raw_transaction" if group == "transactions" else "raw_log"]
            if catalog[ref]["pointer"] != f"/on_chain_evidence/{group}/{i}" or catalog[ref][
                "raw_sha256"
            ] != digest(raw):
                raise ValueError("Evidence reference does not match raw record")
            block, timestamp = row["block"], parse_time(row["timestamp"])
            if type(block) is not int or not bounds[0] <= block <= bounds[1]:
                raise ValueError("Evidence outside block bounds")
            if bounds[2] and not parse_time(bounds[2]) <= timestamp < parse_time(bounds[3]):
                raise ValueError("Evidence outside time bounds")
            if not deployed(contract, block, timestamp):
                continue
            if group == "transactions":
                if address not in (row["from"], row["to"]) or row["status"] not in {
                    "success",
                    "failed",
                    "unknown",
                }:
                    raise ValueError("Transaction scope or status mismatch")
                if (
                    raw["hash"].lower() != row["hash"]
                    or raw["blockHash"].lower() != row["block_hash"]
                    or raw["from"].lower() != row["from"]
                    or (raw["to"].lower() if raw["to"] else None) != row["to"]
                    or int(raw["blockNumber"], 16) != block
                    or int(raw["value"], 16) != int(row["value_wei"])
                    or not HASH.fullmatch(row["hash"])
                    or not HASH.fullmatch(row["block_hash"])
                ):
                    raise ValueError("Transaction evidence mismatch")
            else:
                event = log_fields(raw)
                if any(row[key] != value for key, value in event.items()):
                    raise ValueError("Event evidence mismatch")
                try:
                    token = token_event(event)
                except QueryError:
                    token = None
                if (
                    token is None
                    and row["token"] is not None
                    or token
                    and any(row["token"][key] != value for key, value in token.items())
                ):
                    raise ValueError("Event decoding mismatch")
                if address != event["contract"] and (
                    token is None
                    or address not in [token.get(k) for k in ("from", "to", "owner", "spender")]
                ):
                    # Participant-only malformed logs can be reviewed in the source report.
                    continue
            receipt = row.get("receipt_evidence")
            receipt_hash = digest(receipt) if receipt is not None else None
            if catalog[ref]["receipt_sha256"] != receipt_hash:
                raise ValueError("Receipt evidence hash mismatch")
            status = row["status"] if group == "transactions" else "success"
            if status != "unknown":
                check = {
                    "hash": row["hash" if group == "transactions" else "transaction_hash"],
                    "block_hash": row["block_hash"],
                    "block": block,
                }
                try:
                    apply_receipt_status(receipt, check)
                except QueryError:
                    raise ValueError("Receipt evidence mismatch") from None
                if check["status"] != status:
                    raise ValueError("Receipt status mismatch")
            output.append(row)
        if refs:
            raise ValueError("Unresolved evidence references")
        selected[group] = output
    return selected


def merge(rows, key, raw_key, group, gaps):
    accepted, excluded = {}, set()
    for address, row in rows:
        identity = key(row)
        if identity in excluded:
            continue
        old = accepted.get(identity)
        if old is None:
            accepted[identity] = {**row, "observed_for": [address]}
        else:
            old_cmp, new_cmp = old[raw_key], row[raw_key]
            if group == "events":
                old_cmp, new_cmp = log_fields(old_cmp), log_fields(new_cmp)
            if (
                old_cmp != new_cmp
                or old["timestamp"] != row["timestamp"]
                or (group == "transactions" and old["status"] != row["status"])
            ):
                del accepted[identity]
                excluded.add(identity)
                gaps.append(
                    {"reason": "cross_report_conflict", "group": group, "identity": identity}
                )
            else:
                old["observed_for"] = sorted(set(old["observed_for"]) | {address})
    return list(accepted.values())


def deployed(contract, block, timestamp):
    return (contract["deployment_block"] is None or block >= contract["deployment_block"]) and (
        contract["deployed_at"] is None or timestamp >= parse_time(contract["deployed_at"])
    )


def metrics(txs, events, included):
    counts = Counter(t["status"] for t in txs)
    assets = Counter()
    initiators = Counter()

    def active(address, tx):
        if address not in included:
            return False
        entry = included[address]
        return deployed(entry, tx["block"], parse_time(tx["timestamp"]))

    for tx in txs:
        sender, recipient = active(tx["from"], tx), active(tx["to"], tx)
        if recipient:
            initiators[tx["from"]] += 1
        if tx["status"] == "success":
            key = "internal" if sender and recipient else "out" if sender else "in"
            assets[key] += int(tx["value_wei"])
    kinds = Counter(e["token"]["kind"] for e in events if e["token"])
    return {
        "transactions": len(txs),
        "status": {status: counts[status] for status in ("success", "failed", "unknown")},
        "events": len(events),
        "token_event_counts": {kind: kinds[kind] for kind in ("Transfer", "Approval")},
        "successful_native_value_wei": {k: str(assets[k]) for k in ("in", "out", "internal")},
        "direct_initiators": [
            {"address": address, "count": count, "denominator": sum(initiators.values())}
            for address, count in sorted(initiators.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def investigate_project(manifest_path, *, chain_id=CHAIN_ID, require_verified_code=False):
    base = Path(manifest_path).resolve()
    gaps, errors, summaries = [], [], []
    txs, events = [], []
    bounds, name = None, None
    included, perimeter = set(), {}
    loaded_bytes = 0
    started = utc()
    provenance = {
        "chain_id": chain_id,
        "method": "saved_address_reports",
        "query_started_at": started,
        "reports": {},
    }
    try:
        manifest, raw_manifest = load_json(base, 256 * 1024)
        provenance["manifest_sha256"] = hashlib.sha256(raw_manifest).hexdigest()
        name, entries = contracts(
            manifest, chain_id=chain_id, allow_unknown_deployment=require_verified_code
        )
        perimeter = {
            entry["address"]: entry
            for entry in entries
            if entry["attribution"]["status"] == "confirmed"
        }
        for entry in entries:
            summary = {k: v for k, v in entry.items() if k != "report_file"}
            summaries.append(summary)
            address = entry["address"]
            if entry["attribution"]["status"] != "confirmed":
                summary["included"] = False
                summary["exclusion"] = "attribution_not_confirmed"
                continue
            summary["included"] = False
            try:
                path = (base.parent / entry["report_file"]).resolve()
                if not path.is_relative_to(base.parent) or path == base:
                    raise ValueError("Report path must stay under the manifest directory")
                result, raw = load_json(path, min(MAX_REPORT_BYTES, MAX_INPUT_BYTES - loaded_bytes))
                loaded_bytes += len(raw)
                report, current = report_inputs(result, address, chain_id)
                if bounds is not None and bounds != current:
                    raise QueryError(
                        "Use identical resolved bounds and ending block hash", "incomparable_report"
                    )
                if (
                    require_verified_code
                    and report["coverage"].get("contract_identity", {}).get("bytecode_present")
                    is not True
                ):
                    raise QueryError(
                        "Contract bytecode unavailable at pinned snapshot",
                        "contract_identity_unverified",
                    )
                selected = collect(report, entry, current)
                complete = result["coverage"]["complete"] is True
                summary.update(
                    {
                        "included": True,
                        "coverage": result["coverage"],
                        "metrics": metrics(
                            selected["transactions"], selected["events"], {address: entry}
                        ),
                    }
                )
                included.add(address)
                bounds = current
                for group, target in (("transactions", txs), ("events", events)):
                    target.extend((address, row) for row in selected[group])
                provenance["reports"][address] = {
                    "file_sha256": hashlib.sha256(raw).hexdigest(),
                    "requested_range": report["requested_range"],
                    "data_sources": report["data_sources"],
                }
                if not complete:
                    gaps.append({"address": address, "reason": "partial_address_report"})
            except QueryError as error:
                gaps.append({"address": address, "reason": error.category})
            except (
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                OverflowError,
                RecursionError,
            ):
                gaps.append({"address": address, "reason": "invalid_or_missing_address_report"})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, QueryError):
        errors.append(
            {"category": "invalid_project_manifest", "error": "Unable to validate project manifest"}
        )
    txs = merge(txs, lambda t: t["hash"], "raw_transaction", "transactions", gaps)
    events = merge(events, lambda e: (e["block"], e["log_index"]), "raw_log", "events", gaps)
    txs.sort(key=lambda t: (t["block"], t["hash"]))
    events.sort(key=lambda e: (e["block"], e["log_index"]))
    evidence = {"transactions": txs, "events": events}
    catalog = evidence_catalog(chain_id, evidence, provenance)
    for row in txs + events:
        catalog[row["evidence_ref"]]["sources"] = row["observed_for"]
    if not included:
        gaps.append({"reason": "no_eligible_address_reports"})
    coverage = {"complete": bool(included) and not gaps and not errors, "gaps": gaps}
    provenance["query_completed_at"] = utc()
    return {
        "schema_version": "project-2.1",
        "coverage": coverage,
        "errors": errors,
        "report": {
            "target": name,
            "deployments": [
                {
                    "id": deployment,
                    "chain_id": chain_id,
                    "contracts": [c for c in summaries if c["deployment_id"] == deployment],
                }
                for deployment in sorted({c["deployment_id"] for c in summaries})
            ],
            "resolved_range": dict(zip(RANGE_KEYS, bounds)) if bounds else None,
            "data_sources": provenance,
            "coverage": coverage,
            "on_chain_evidence": evidence,
            "evidence_index": catalog,
            "deterministic_metrics": metrics(txs, events, perimeter) if included else None,
            "calculation": {
                "method": "chaininquest-project-v2.1",
                "perimeter": sorted(perimeter),
                "time_zone": "UTC",
                "transaction_refs": [t["evidence_ref"] for t in txs],
                "event_refs": [e["evidence_ref"] for e in events],
            },
            "cannot_establish": [
                "Completeness of project membership, legal ownership, human users or commercial adoption",
                "Independent canonicality or provenance of saved reports",
            ],
        },
    }
