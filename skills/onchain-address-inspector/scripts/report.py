"""Build a machine-readable report without interpreting participant intent."""

from citations import evidence_catalog


def generate(target, requested, resolved, source, coverage, evidence, analytics):
    catalog = evidence_catalog(source.get("chain_id"), evidence, source)
    tx_usable = evidence["snapshot_checked"] and coverage["transactions"].get("usable", True)
    observations = []
    totals = analytics["transactions"]
    if totals and totals["top_caller"] and totals["top_caller"]["count"] > 1:
        observations.append(
            {
                "kind": "deterministic_observation",
                "phenomenon": "repeated_direct_initiator",
                "evidence": totals["top_caller"],
                "scope": "retrieved transactions",
                "evidence_refs": [
                    tx["evidence_ref"]
                    for tx in evidence["transactions"]
                    if tx["from"] == totals["top_caller"]["address"] and tx["to"] == target
                ],
            }
        )
    return {
        "target": target,
        "requested_range": requested,
        "resolved_range": resolved,
        "data_sources": source,
        "coverage": coverage,
        "on_chain_evidence": evidence,
        "deterministic_metrics": analytics,
        "evidence_index": catalog,
        "calculation": {
            "method": "chaininquest-address-v2.1",
            "time_zone": "UTC",
            "transaction_refs": [t["evidence_ref"] for t in evidence["transactions"]]
            if tx_usable
            else [],
            "event_refs": [e["evidence_ref"] for e in evidence["events"]]
            if evidence["snapshot_checked"]
            else [],
        },
        "investigation_leads": observations,
        "model_inferences": [],
        "cannot_establish": [
            "Human users, wallet ownership, organic adoption or manipulation",
            "First-ever wallet activity or the current allowance from an Approval log",
            "Complete asset flows, internal transfers or ERC-20 compliance from log shape alone",
            "Independent canonicality or index exhaustiveness",
        ],
    }
