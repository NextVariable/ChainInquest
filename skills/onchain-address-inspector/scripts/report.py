"""Build a machine-readable report without interpreting participant intent."""


def generate(target, requested, resolved, source, coverage, evidence, analytics):
    observations = []
    totals = analytics["transactions"]
    if totals and totals["top_caller"] and totals["top_caller"]["count"] > 1:
        observations.append(
            {
                "kind": "deterministic_observation",
                "phenomenon": "repeated_direct_initiator",
                "evidence": totals["top_caller"],
                "scope": "retrieved transactions",
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
        "investigation_leads": observations,
        "model_inferences": [],
        "cannot_establish": [
            "Human users, wallet ownership, organic adoption or manipulation",
            "First-ever wallet activity or the current allowance from an Approval log",
            "Complete asset flows, internal transfers or ERC-20 compliance from log shape alone",
            "Independent canonicality or index exhaustiveness",
        ],
    }
