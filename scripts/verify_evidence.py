"""Independently check saved output without importing the scanner or its metrics."""

import argparse
import collections
import decimal
import json
from pathlib import Path


def verify_investigation(result):
    report = result["report"]
    evidence = report["on_chain_evidence"]
    computed = report["deterministic_metrics"]
    txs = evidence["transactions"]
    events = evidence["events"]
    if not evidence["snapshot_checked"]:
        assert computed["transactions"] is None and computed["token_event_counts"] is None
        return {"schema_version": "2.0", "snapshot_checked": False}
    totals = computed["transactions"]
    if totals is not None:
        assert len({t["hash"] for t in txs}) == len(txs) == totals["direct_transactions"]
        assert all(
            totals[s] == len([t for t in txs if t["status"] == s])
            for s in ("success", "failed", "unknown")
        )
        target = report["target"]
        for direction, field in (("in", "to"), ("out", "from")):
            value = sum(
                int(t["raw_transaction"]["value"], 16)
                for t in txs
                if t[field] == target and t["status"] == "success"
            )
            assert totals["native_value_successful_top_level_only"][direction + "_wei"] == str(
                value
            )
        dates = {row["date"]: row for row in computed["daily_utc"]}
        daily = collections.Counter(t["timestamp"][:10] for t in txs)
        assert all(dates[day]["direct_transactions"] == count for day, count in daily.items())
        assert sum(row["direct_transactions"] or 0 for row in dates.values()) == len(txs)
    assert len({(e["block_hash"], e["log_index"]) for e in events}) == len(events)
    tokens = [e for e in events if e["token"]]
    for event in tokens:
        token = event["token"]
        amount = int(event["raw_log"]["data"], 16)
        assert token["amount_raw"] == str(amount)
        d = token["decimals"]
        if d is None:
            assert token["amount_display"] is None
        else:
            with decimal.localcontext() as context:
                context.prec = 350
                expected = decimal.Decimal(amount).scaleb(-d)
                assert token["amount_display"] == format(expected, f".{d}f")
    if computed["token_event_counts"] is not None:
        assert all(
            computed["token_event_counts"][k] == sum(t["token"]["kind"] == k for t in tokens)
            for k in ("Transfer", "Approval")
        )
    return {
        "schema_version": "2.0",
        "transactions": len(txs),
        "events": len(events),
        "token_events": len(tokens),
        "complete": result["coverage"]["complete"],
    }


def verify(path):
    result = json.loads(path.read_text())
    if result.get("schema_version") == "2.0":
        return verify_investigation(result)
    txs = result["transactions"]
    metrics = result["metrics"]
    if metrics is None:
        assert not result["coverage"]["complete"]
        return {"file": path.name, "complete": False, "metrics": None, "records": len(txs)}
    address = result["address"]
    assert len({tx["hash"] for tx in txs}) == len(txs)
    assert metrics["direct_transactions"] == len(txs)
    status_counts = collections.Counter(tx["status"] for tx in txs)
    for status in ("success", "failed", "unknown"):
        assert metrics[status] == status_counts[status]
    sums = {}
    with decimal.localcontext() as context:
        context.prec = 120
        for direction, field in (("in", "to"), ("out", "from")):
            amount = sum(
                int(tx["value_wei"])
                for tx in txs
                if tx[field] == address and tx["status"] == "success"
            )
            values = metrics["native_value_successful_top_level_only"]
            assert values[direction + "_wei"] == str(amount)
            assert values[direction + "_mon"] == format(
                decimal.Decimal(amount) / decimal.Decimal(10**18), ".18f"
            )
            sums[direction + "_wei"] = str(amount)
    incoming = [tx for tx in txs if tx["to"] == address]
    callers = collections.Counter(tx["from"] for tx in incoming)
    assert metrics["direct_to_transactions"] == len(incoming)
    assert metrics["distinct_direct_initiators"] == len(callers)
    assert [(rank["address"], rank["count"]) for rank in metrics["caller_ranking"]] == sorted(
        callers.items(), key=lambda item: (-item[1], item[0])
    )
    for rank in metrics["caller_ranking"]:
        assert rank["share_numerator"] == callers[rank["address"]]
        assert rank["share_denominator"] == len(incoming)
    assert metrics["repeated_direct_initiators"] == sum(count >= 2 for count in callers.values())
    assert metrics["distinct_counterparties"] == len(
        {peer for tx in txs for peer in (tx["from"], tx["to"]) if peer and peer != address}
    )
    assert metrics["direct_to_with_input_data"]["transactions"] == sum(
        tx["has_input_data"] is True for tx in incoming
    )
    assert metrics["direct_to_without_input_data"] == sum(
        tx["has_input_data"] is False for tx in incoming
    )
    assert metrics["direct_to_unknown_input_data"] == sum(
        tx["has_input_data"] is None for tx in incoming
    )
    coverage = result["coverage"]
    if coverage["complete"]:
        assert result["chain_id"] == 10143
        assert coverage["scan_complete"] and coverage["chain_consistent"]
        assert not any(
            coverage[key] for key in ("failed_blocks", "missing_receipts", "data_anomalies")
        )
        assert not result["errors"] and status_counts["unknown"] == 0
        bounds = result["requested_range"]["resolved"]
        assert coverage["successful_blocks"] == list(
            range(bounds["start_block"], bounds["end_block"] + 1)
        )
    return {
        "file": path.name,
        "complete": coverage["complete"],
        "records": len(txs),
        "status": dict(status_counts),
        "successful_value": sums,
        "elapsed_seconds": result["elapsed_seconds"],
        "performance": result.get("performance"),
        "verification": "Output ledger recomputation; not an independent node or consensus proof.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    print(json.dumps(verify(parser.parse_args().file), ensure_ascii=False, indent=2))
