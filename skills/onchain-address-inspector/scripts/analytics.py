"""Deterministic sample metrics over validated transaction and event evidence."""

from collections import Counter
from datetime import date, timedelta

from evidence import metrics


def analyze(address, txs, events, start_time, end_time, coverage):
    tx_coverage = coverage["transactions"]
    observed = bool(txs) or tx_coverage["complete"] or tx_coverage.get("observed", False)
    totals = metrics(txs, address) if observed else None
    daily = {}
    if start_time and end_time:
        day = date.fromisoformat(start_time[:10])
        last = date.fromisoformat(end_time[:10])
        while day <= last:
            daily[day.isoformat()] = Counter()
            day += timedelta(days=1)

    def increment(timestamp, field):
        daily.setdefault(timestamp[:10], Counter())[field] += 1

    per_address = Counter()
    categories = Counter()
    for tx in txs:
        increment(tx["timestamp"], "direct_transactions")
        increment(tx["timestamp"], tx["status"])
        peers = {tx["from"], tx["to"]} - {None, address}
        per_address.update(peers)
        if tx["status"] == "success" and int(tx["value_wei"]):
            categories["successful_nonzero_native_value"] += 1
            increment(tx["timestamp"], "successful_nonzero_native_value")
        categories[tx["interaction_at_block_end"]] += 1
        if tx["to"] is None:
            categories["contract_creation_attempts"] += 1
        elif tx["has_input_data"] is True:
            categories["nonempty_calldata"] += 1
    counts = Counter()
    for event in events:
        if event.get("token"):
            kind = event["token"]["kind"]
            counts[kind] += 1
            increment(event["timestamp"], kind)
    fields = (
        "direct_transactions",
        "success",
        "failed",
        "unknown",
        "successful_nonzero_native_value",
        "Transfer",
        "Approval",
    )
    return {
        "transactions": totals,
        "counterparties": {
            "distinct_addresses": len(per_address),
            "first_observed_in_window": len(per_address),
            "one_transaction": sum(n == 1 for n in per_address.values()),
            "multiple_transactions": sum(n > 1 for n in per_address.values()),
        }
        if observed
        else None,
        "transaction_categories": dict(categories) if observed else None,
        "token_event_counts": {kind: counts[kind] for kind in ("Transfer", "Approval")}
        if events or coverage["events"]["complete"]
        else None,
        "token_event_counts_scope": "complete"
        if coverage["events"]["complete"]
        else "observed_sample",
        "daily_scope": {
            "status": "complete"
            if tx_coverage["complete"] and coverage["events"]["complete"]
            else "partial",
            "null_means_unobserved": True,
        },
        "daily_utc": [
            {
                "date": day,
                **{
                    name: counter.get(name, 0)
                    if (
                        name in counter
                        or coverage[
                            "events" if name in {"Transfer", "Approval"} else "transactions"
                        ]["complete"]
                    )
                    else None
                    for name in fields
                },
            }
            for day, counter in sorted(daily.items())
        ],
    }
