"""Stable evidence identities and verifiable pointers within an exported report."""

import hashlib
import json

from evidence import EXPLORER


def digest(value):
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def evidence_catalog(chain_id, evidence, sources):
    catalog = {}
    for group, rows in evidence.items():
        if group not in {"transactions", "events", "unverified_logs"}:
            continue
        for position, row in enumerate(rows):
            if group == "transactions":
                identity = f"tx:{chain_id}:{row['block_hash']}:{row['hash']}"
                tx_hash = row["hash"]
                raw = row.get("raw_transaction")
                source_keys = ["rpc"]
                if "index_comparison" in row:
                    source_keys.append("transaction_index")
            elif group == "events":
                identity = f"log:{chain_id}:{row['block_hash']}:{row['log_index']}"
                tx_hash = row["transaction_hash"]
                raw = row["raw_log"]
                source_keys = ["rpc"]
            else:
                raw = row["raw_log"]
                identity = f"rejected:{digest(raw)}:{position}"
                tx_hash = None
                source_keys = ["rpc"]
            catalog[identity] = {
                "pointer": f"/on_chain_evidence/{group}/{position}",
                "raw_sha256": digest(raw) if raw is not None else None,
                "timestamp": row.get("timestamp"),
                "receipt_sha256": digest(row["receipt_evidence"])
                if "receipt_evidence" in row
                else None,
                "sources": [key for key in source_keys if key in sources],
                "explorer_url": EXPLORER + tx_hash if tx_hash else None,
            }
            row["evidence_ref"] = identity
    return catalog
