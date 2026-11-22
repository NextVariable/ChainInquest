"""Compare saved one-block Beta reports with a separate RPC's explicit evidence."""

import argparse
import json
from pathlib import Path

from verify_evidence import verify


def compare(root, independent):
    summaries = {}
    for name, reference in independent.items():
        path = root / f"v4-real-{name}.json"
        result = json.loads(path.read_text())
        verify(path)
        chain = result["chains"]["1"]
        report = chain["report"]
        evidence = report["on_chain_evidence"]
        block_matches = report["resolved_range"]["end_block_hash"] == reference["hash"]
        txs = {
            tx["hash"]: [
                hex(int(tx["value_wei"])),
                {"success": "0x1", "failed": "0x0", "unknown": None}[tx["status"]],
                tx["block_hash"],
            ]
            for tx in evidence["transactions"]
        }
        logs = {
            hex(log["log_index"]): [
                log["transaction_hash"],
                log["data"],
                log["block_hash"],
                log["topics"],
            ]
            for log in evidence["events"]
        }
        summaries[name] = {
            "block_matches": block_matches,
            "transactions": len(txs),
            "events": len(logs),
            "transaction_rows_match": txs == reference["transactions"],
            "event_rows_match": logs == reference["events"],
            "independent_errors": reference["errors"],
            "report_complete": chain["coverage"]["complete"],
        }
    return summaries


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("reports", type=Path)
    p.add_argument("reference", type=Path)
    args = p.parse_args()
    results = compare(args.reports, json.loads(args.reference.read_text()))
    print(json.dumps(results, indent=2))
    return (
        0
        if results
        and all(
            row["block_matches"]
            and row["transaction_rows_match"]
            and row["event_rows_match"]
            and not row["independent_errors"]
            and row["report_complete"]
            for row in results.values()
        )
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
