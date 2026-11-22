"""Create chain-scoped facts and deterministic investigation leads from validated rows."""

from collections import Counter, defaultdict
from datetime import timedelta

from chains import CHAINS
from project import deployed, parse_time


def enrich(chain_id, result):
    report = result["report"]
    evidence = report["on_chain_evidence"]
    txs, events = evidence["transactions"], evidence["events"]
    catalog = report["evidence_index"]
    contracts = {
        c["address"]: c
        for dep in report["deployments"]
        for c in dep["contracts"]
        if c["attribution"]["status"] == "confirmed"
    }
    nodes, facts, attribution, candidates = {}, [], [], []
    daily = defaultdict(Counter)
    peers, metadata = {}, {}
    project = f"project:{chain_id}"
    nodes[project] = {"type": "Project", "label": report["target"]}
    for dep in report["deployments"]:
        deployment = f"deployment:{chain_id}:{dep['id']}"
        nodes[deployment] = {"type": "Deployment", "id": dep["id"], "chain_id": chain_id}
        attribution.append({"from": project, "to": deployment, "relation": "curated_deployment"})
        for c in dep["contracts"]:
            if c["included"]:
                contract = f"address:{chain_id}:{c['address']}"
                nodes[contract] = {
                    "type": "Contract",
                    "address": c["address"],
                    "chain_id": chain_id,
                }
                attribution.append(
                    {
                        "from": deployment,
                        "to": contract,
                        "relation": "curator_attribution",
                        "sources": c["attribution"]["sources"],
                        "bytecode_check": c["coverage"]["contract_identity"],
                    }
                )

    def endpoint(value):
        key = f"address:{chain_id}:{value}"
        nodes.setdefault(key, {"type": "Address", "address": value, "chain_id": chain_id})
        return key

    def edge(src, dst, relation, ref, **fields):
        if ref in catalog:
            nodes.setdefault(ref, {"type": "Evidence", "reference": ref})
            facts.append(
                {"from": src, "to": dst, "relation": relation, "evidence_ref": ref, **fields}
            )

    for tx in txs:
        ref = tx["evidence_ref"]
        key = f"transaction:{chain_id}:{tx['hash']}"
        nodes[key] = {"type": "Transaction", "hash": tx["hash"], "status": tx["status"]}
        sender, recipient = tx["from"], tx["to"]
        sender_node = endpoint(sender)
        recipient_node = endpoint(recipient) if recipient else None
        edge(sender_node, key, "top_level_sender", ref)
        if recipient_node:
            edge(key, recipient_node, "top_level_recipient", ref)
        edge(key, ref, "supported_by", ref)
        stamp = parse_time(tx["timestamp"])
        day = stamp.date().isoformat()
        daily[day]["transactions"] += 1
        for address in {sender, recipient} - {None}:
            if address not in contracts or not deployed(contracts[address], tx["block"], stamp):
                peers.setdefault(address, []).append(ref)
        if recipient_node and tx["status"] == "success" and int(tx["value_wei"]) > 0:
            edge(
                sender_node,
                recipient_node,
                "successful_top_level_native_value",
                ref,
                asset=CHAINS[chain_id].symbol,
                amount_raw=tx["value_wei"],
            )

    for event in events:
        ref = event["evidence_ref"]
        key = f"event:{ref}"
        tx = f"transaction:{chain_id}:{event['transaction_hash']}"
        nodes.setdefault(tx, {"type": "Transaction", "hash": event["transaction_hash"]})
        nodes[key] = {"type": "Event", "block": event["block"], "log_index": event["log_index"]}
        edge(tx, key, "receipt_contains_log", ref)
        edge(key, endpoint(event["contract"]), "emitted_by", ref)
        edge(key, ref, "supported_by", ref)
        token = event.get("token")
        if not token:
            continue
        daily[event["timestamp"][:10]][token["kind"]] += 1
        if token["kind"] == "Transfer":
            edge(
                endpoint(token["from"]),
                endpoint(token["to"]),
                "erc20_shaped_transfer_log",
                ref,
                asset=event["contract"],
                amount_raw=token["amount_raw"],
                decimals=token["decimals"],
            )

    peers = {
        address: {"first_observed_in_report": refs[0], "transactions": len(refs)}
        for address, refs in peers.items()
    }
    coverage = result["coverage"]
    bounds = report["resolved_range"] or {}
    start = bounds.get("from_time_inclusive")
    end = bounds.get("to_time_exclusive")
    if start and end:
        d = parse_time(start).date()
        stop = (parse_time(end) - timedelta(seconds=1)).date()
        while d <= stop:
            daily[d.isoformat()]
            d += timedelta(days=1)
    for day, values in sorted(daily.items()):
        metadata[day] = {
            key: values[key] if key in values or coverage["complete"] else None
            for key in ("transactions", "Transfer", "Approval")
        }
    totals = report["deterministic_metrics"]
    if totals and totals["direct_initiators"]:
        top = totals["direct_initiators"][0]
        if top["count"] >= 3 and top["count"] * 2 >= top["denominator"]:
            refs = [
                t["evidence_ref"]
                for t in txs
                if t["from"] == top["address"]
                and t["to"] in contracts
                and deployed(contracts[t["to"]], t["block"], parse_time(t["timestamp"]))
            ]
            if len(refs) == top["count"]:
                candidates.append(
                    {
                        "type": "direct_initiator_concentration",
                        "observed": top,
                        "coverage_complete": coverage["complete"],
                        "evidence_refs": refs,
                    }
                )
    if coverage["complete"] and start and end:
        beginning, ending = parse_time(start), parse_time(end)
        complete_days = [
            day
            for day in daily
            if beginning <= parse_time(day + "T00:00:00Z")
            and parse_time(day + "T00:00:00Z") + timedelta(days=1) <= ending
        ]
        ordered = sorted(complete_days)
        for previous, current in zip(ordered, ordered[1:]):
            a, b = daily[previous]["transactions"], daily[current]["transactions"]
            if b >= 5 and b >= 2 * a:
                candidates.append(
                    {
                        "type": "day_over_day_transaction_increase",
                        "observed": {previous: a, current: b},
                        "coverage_complete": True,
                        "evidence_refs": [
                            t["evidence_ref"]
                            for t in txs
                            if t["timestamp"][:10] in {previous, current}
                        ],
                    }
                )
    return {
        "chain_id": chain_id,
        "coverage_complete": coverage["complete"],
        "daily_utc": metadata,
        "observed_counterparties": len(peers),
        "first_observed_in_window": len(peers),
        "repeated_counterparties": sum(p["transactions"] > 1 for p in peers.values()),
        "counterparty_evidence": peers,
        "graph": {
            "nodes": nodes,
            "facts": facts,
            "curator_attribution": attribution,
            "inferred_relationships": [],
        },
        "investigation_candidates": candidates,
    }
