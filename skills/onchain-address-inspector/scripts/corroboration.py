"""Optional bounded checks against an operator-configured second RPC endpoint."""

import os

from adapters import RpcAdapter
from chains import CHAINS
from event_decoder import log_fields
from evidence import QueryError, apply_receipt_status, quantity
from rpc_client import Client


def corroborate(chain_id, result, seconds):
    secondary = os.environ.get(CHAINS[chain_id].env + "_SECONDARY")
    report = result["report"]
    bounds = report["resolved_range"]
    if not secondary or secondary.rstrip("/") == CHAINS[chain_id].endpoint().rstrip("/"):
        return {"status": "not_run", "reason": "distinct_secondary_endpoint_required"}
    if not bounds or seconds < 2:
        return {"status": "not_run", "reason": "snapshot_or_budget_unavailable"}
    evidence = report["on_chain_evidence"]
    samples = [(row, False) for row in evidence["transactions"][:2]]
    samples += [(row, True) for row in evidence["events"][:2]]
    client = Client(secondary, min(seconds, 5), 0, seconds, "auto", 1)
    checks = []
    try:
        rpc = RpcAdapter(client)
        if quantity(rpc.call("eth_chainId", [])) != chain_id:
            raise QueryError("Unexpected secondary chain", "wrong_chain")
        if rpc.header(bounds["end_block"])[1] != bounds["end_block_hash"]:
            return {"status": "disagreement", "reason": "ending_block_hash_mismatch", "checks": []}
        for row, is_event in samples:
            trial = {
                "hash": row["transaction_hash"] if is_event else row["hash"],
                "block_hash": row["block_hash"],
                "block": row["block"],
            }
            check = {"evidence_ref": row["evidence_ref"]}
            try:
                raw = rpc.call("eth_getTransactionReceipt", [trial["hash"]])
                apply_receipt_status(raw, trial)
                if is_event:
                    event = log_fields(row["raw_log"])
                    agrees = (
                        trial["status"] == "success"
                        and sum(log_fields(log) == event for log in raw["logs"]) == 1
                    )
                elif row["status"] == "unknown":
                    checks.append(
                        {**check, "status": "unavailable", "reason": "primary_status_unknown"}
                    )
                    continue
                else:
                    agrees = trial["status"] == row["status"]
                checks.append({**check, "status": "match" if agrees else "disagreement"})
            except (QueryError, ValueError, KeyError, TypeError) as error:
                status = (
                    "disagreement"
                    if getattr(error, "category", None) == "receipt_mismatch"
                    else "unavailable"
                )
                checks.append(
                    {
                        **check,
                        "status": status,
                        "reason": getattr(error, "category", "invalid_secondary_response"),
                    }
                )
        status = (
            "matched_sample"
            if checks and all(c["status"] == "match" for c in checks)
            else "partial"
        )
        if any(c["status"] == "disagreement" for c in checks):
            status = "disagreement"
        return {"status": status, "ending_block_matched": True, "checks": checks}
    except QueryError as error:
        return {"status": "unavailable", "reason": error.category, "checks": checks}
    finally:
        client.close()
