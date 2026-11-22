"""Run a curated ChainInquest project investigation and save checkpoints."""

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from adapters import RpcAdapter
from chains import CHAINS
from corroboration import corroborate
from evidence import QueryError, quantity
from investigation import investigate
from jobs import JobStore, checkpoint, write
from project import contracts, investigate_project, load_json, parse_time
from query import parser, validate
from relationships import enrich
from rpc_client import Client

MAX_CONTRACTS = 8
MAX_BLOCKS = 32
MAX_ATTEMPTS = 3


def plan_investigation(request, now=None):
    """Accept explicit scope and objectives. External text has no control authority."""
    try:
        if request.get("goal", "overview") not in {"overview", "activity", "flows"}:
            raise ValueError
        scope = request["scope"]
        manifest = request["manifest"]
        deployments = manifest["deployments"]
        chain_ids = {d["chain_id"] for d in deployments}
        if (
            not chain_ids
            or not chain_ids <= CHAINS.keys()
            or any(type(i) is not int for i in chain_ids)
        ):
            raise ValueError
        entries = []
        for chain_id in sorted(chain_ids):
            name, chain_entries = contracts(
                {**manifest, "deployments": [d for d in deployments if d["chain_id"] == chain_id]},
                chain_id=chain_id,
                allow_unknown_deployment=True,
            )
            entries.extend(chain_entries)
        chain_ids = sorted(
            {e["chain_id"] for e in entries if e["attribution"]["status"] == "confirmed"}
        )
        if len(entries) > MAX_CONTRACTS or not any(
            e["attribution"]["status"] == "confirmed" for e in entries
        ):
            raise ValueError
        ranges = {}
        now = now or dt.datetime.now(dt.timezone.utc)
        if set(scope) == {"days"} and type(scope["days"]) is int and scope["days"] in (7, 30):
            end = now.replace(microsecond=0)
            start = end - dt.timedelta(days=scope["days"])
            ranges = {
                str(i): {"from_time": start.isoformat(), "to_time": end.isoformat()}
                for i in chain_ids
            }
        elif set(scope) == {"from_time", "to_time"}:
            start, end = parse_time(scope["from_time"]), parse_time(scope["to_time"])
            if (
                start.microsecond
                or end.microsecond
                or not start < end <= now
                or end - start > dt.timedelta(days=30)
            ):
                raise ValueError
            ranges = {
                str(i): {"from_time": start.isoformat(), "to_time": end.isoformat()}
                for i in chain_ids
            }
        elif (
            set(scope) == {"recent_blocks"}
            and type(scope["recent_blocks"]) is int
            and 1 <= scope["recent_blocks"] <= MAX_BLOCKS
        ):
            ranges = {str(i): dict(scope) for i in chain_ids}
        elif (
            set(scope) == {"start_block", "end_block"}
            and all(type(v) is int for v in scope.values())
            and 0 <= scope["start_block"] <= scope["end_block"] < 2**63
            and scope["end_block"] - scope["start_block"] < MAX_BLOCKS
        ):
            ranges = {str(i): dict(scope) for i in chain_ids}
        else:
            raise ValueError
        if "start_block" in scope and len(chain_ids) != 1:
            raise ValueError
        budget = request.get("budget_seconds", 120)
        if type(budget) is not int or not 15 <= budget <= 240:
            raise ValueError
        return {
            "project": name,
            "goal": request.get("goal", "overview"),
            "ranges": ranges,
            "entries": entries,
            "budget_seconds": budget,
        }
    except (
        QueryError,
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        raise QueryError(
            "Invalid project, confirmed contract, goal or query scope", "invalid_plan"
        ) from None


def plan_objective(request, objective):
    from planner import select

    if not isinstance(request, dict):
        raise QueryError("Invalid project request", "invalid_plan")
    # Validate membership and original limits before issuing a model request.
    baseline = plan_investigation(request)
    proposal, meta = select(objective, baseline["goal"])
    updated = {**request, "goal": proposal["goal"]}
    if proposal["window"] != "keep":
        updated["scope"] = {"days": int(proposal["window"][:-1])}
    plan = plan_investigation(updated)
    plan["planner"] = meta
    return plan


def conclusions(chains, issues):
    facts, inferences, unknown = [], [], []
    for chain_id, result in chains.items():
        report = result["report"]
        metrics = report["deterministic_metrics"]
        if metrics and result.get("analysis") is not None:
            facts.append(
                {
                    "chain_id": chain_id,
                    "scope": "observed_evidence",
                    "transactions": metrics["transactions"],
                    "events": metrics["events"],
                    "proof": report["calculation"]["transaction_refs"]
                    + report["calculation"]["event_refs"],
                    "coverage_complete": result["coverage"]["complete"],
                }
            )
        if result.get("analysis"):
            inferences.extend(
                {"chain_id": chain_id, **item}
                for item in result["analysis"]["investigation_candidates"]
            )
        unknown.extend({"chain_id": chain_id, **gap} for gap in result["coverage"]["gaps"])
    return {"facts": facts, "inferences": inferences, "unknown": unknown + issues}


def rpc_url(chain_id):
    return CHAINS[chain_id].endpoint()


def resolve_recent(chain_id, bounds, seconds):
    if "recent_blocks" not in bounds:
        return bounds
    client = Client(rpc_url(chain_id), min(8, seconds), 0, seconds, "auto", 1)
    try:
        rpc = RpcAdapter(client)
        if quantity(rpc.call("eth_chainId", [])) != chain_id:
            raise QueryError("Unexpected provider chain", "wrong_chain")
        tip, h, _ = rpc.header("latest")
        return {
            "start_block": max(0, tip - bounds["recent_blocks"] + 1),
            "end_block": tip,
            "pinned_hash": h,
        }
    finally:
        client.close()


def query_contract(entry, bounds, seconds):
    args = parser().parse_args(["--address", entry["address"], "--investigate"])
    args.rpc = rpc_url(entry["chain_id"])
    args.chain_id = entry["chain_id"]
    args.verify_contract = True
    args.retries = 0
    args.concurrency = 2
    args.timeout = min(8, seconds)
    args.total_timeout = seconds
    for key, value in bounds.items():
        if key != "pinned_hash":
            setattr(args, key, value)
    validate(args)
    report = investigate(args)
    resolved = report["report"]["resolved_range"] or {}
    if bounds.get("pinned_hash") and resolved.get("end_block_hash") != bounds["pinned_hash"]:
        raise QueryError("The pinned head changed; start a fresh investigation", "snapshot_changed")
    return report


def manifest_for(plan, chain_id):
    groups = {}
    for entry in plan["entries"]:
        if entry["chain_id"] == chain_id:
            groups.setdefault(entry["deployment_id"], []).append(
                {
                    "address": entry["address"],
                    "deployment_block": entry["deployment_block"],
                    "deployed_at": entry["deployed_at"],
                    "attribution": entry["attribution"],
                    "report_file": f"{chain_id}-{entry['address']}.json",
                }
            )
    return {
        "schema_version": "1",
        "project": plan["project"],
        "deployments": [
            {"id": name, "chain_id": chain_id, "contracts": values}
            for name, values in groups.items()
        ],
    }


def run(store, job_id, *, query_fn=query_contract, resolve_fn=resolve_recent):
    with store.runner(job_id) as directory:
        state = store.state(job_id)
        if state["status"] == "complete":
            return state
        if state["attempts"] >= MAX_ATTEMPTS:
            raise QueryError("Local resume limit reached", "attempt_budget")
        plan = state["plan"]
        deadline = time.monotonic() + plan["budget_seconds"]
        pinned = state.get("pinned", {})
        done = set(state["completed"])
        issues = []
        results = {}
        checkpoint(
            directory,
            state,
            "planning",
            status="running",
            attempts=state["attempts"] + 1,
            result_ready=False,
        )
        try:
            for position, (chain_key, initial) in enumerate(plan["ranges"].items()):
                chain_budget = (deadline - time.monotonic()) / (len(plan["ranges"]) - position)
                chain_deadline = time.monotonic() + chain_budget
                chain = int(chain_key)
                entries = [
                    e
                    for e in plan["entries"]
                    if e["chain_id"] == chain and e["attribution"]["status"] == "confirmed"
                ]
                if chain_key not in pinned:
                    try:
                        pinned[chain_key] = resolve_fn(
                            chain, initial, max(1, min(12, int(chain_deadline - time.monotonic())))
                        )
                    except QueryError as error:
                        issues.append({"chain_id": chain, "category": error.category})
                        continue
                    checkpoint(directory, state, "pinned_snapshot", pinned=pinned)
                crosscheck_budget = max(0, min(12, int(chain_budget / 5)))
                pending = [e for e in entries if f"{chain}-{e['address']}" not in done]
                for offset, entry in enumerate(pending):
                    key = f"{chain}-{entry['address']}"
                    left = int(chain_deadline - time.monotonic()) - crosscheck_budget
                    if left < 2:
                        issues.append({"contract": key, "category": "investigation_budget"})
                        break
                    seconds = max(2, left // (len(pending) - offset))
                    checkpoint(directory, state, "collecting", current=key)
                    try:
                        report = query_fn(entry, pinned[chain_key], seconds)
                        write(directory / (key + ".json"), report, 16 * 1024 * 1024)
                        if report["coverage"]["complete"]:
                            done.add(key)
                        else:
                            issues.append({"contract": key, "category": "partial_address_report"})
                    except QueryError as error:
                        issues.append({"contract": key, "category": error.category})
                    checkpoint(directory, state, "address_checkpoint", completed=sorted(done))
                path = directory / f"manifest-{chain}.json"
                write(path, manifest_for(plan, chain))
                results[chain_key] = investigate_project(
                    path, chain_id=chain, require_verified_code=True
                )
                checkpoint(directory, state, "cross_checking", current=chain_key)
                comparison = corroborate(
                    chain,
                    results[chain_key],
                    max(0, min(crosscheck_budget, int(chain_deadline - time.monotonic()))),
                )
                results[chain_key]["cross_check"] = comparison
                if comparison["status"] == "disagreement":
                    results[chain_key]["coverage"]["complete"] = False
                    results[chain_key]["coverage"]["gaps"].append(
                        {"reason": "secondary_provider_disagreement"}
                    )
                    issues.append(
                        {"chain_id": chain, "category": "secondary_provider_disagreement"}
                    )
                    results[chain_key]["analysis"] = None
                else:
                    results[chain_key]["analysis"] = enrich(chain, results[chain_key])
            complete = (
                all(r["coverage"]["complete"] for r in results.values())
                and len(results) == len(plan["ranges"])
                and not issues
            )
            write(
                directory / "result.json",
                {
                    "schema_version": "investigation-3",
                    "id": job_id,
                    "project": plan["project"],
                    "goal": plan["goal"],
                    "planner": plan.get("planner"),
                    "conclusions": conclusions(results, issues),
                    "complete": complete,
                    "pinned": pinned,
                    "chains": results,
                    "issues": issues,
                },
            )
            checkpoint(
                directory,
                state,
                "report_ready",
                status="complete" if complete else "partial",
                result_ready=True,
                current=None,
                issues=issues,
            )
        except (OSError, ValueError, TypeError, KeyError, QueryError):
            checkpoint(
                directory,
                state,
                "failed",
                status="failed",
                current=None,
                issues=issues + [{"category": "investigation_runtime_failure"}],
            )
        return state


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", default="artifacts/investigations")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input")
    mode.add_argument("--resume")
    p.add_argument("--objective", help="Classify a natural-language investigation goal")
    args = p.parse_args(argv)
    try:
        store = JobStore(args.store)
        if args.resume:
            if args.objective:
                raise QueryError(
                    "A resumed investigation preserves its original plan", "invalid_plan"
                )
            job_id = args.resume
        else:
            request = load_json(Path(args.input), 128 * 1024)[0]
            plan = (
                plan_objective(request, args.objective)
                if args.objective
                else plan_investigation(request)
            )
            job_id = store.create(plan)
        print(f"Investigation {job_id}", file=sys.stderr)
        state = run(store, job_id)
        print(json.dumps(store.result(job_id) if state["result_ready"] else state, indent=2))
        return 0 if state["status"] == "complete" else 2
    except (OSError, ValueError, TypeError, RecursionError, QueryError) as error:
        print(
            json.dumps(
                {"status": "failed", "category": getattr(error, "category", "invalid_input")}
            )
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
