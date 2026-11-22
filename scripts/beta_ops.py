#!/usr/bin/env python3
"""Inspect and prune closed-beta investigation data under a web store."""

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

JOB = re.compile(r"[0-9a-f]{32}")


def stores(root):
    accounts = root / "accounts"
    if accounts.is_dir():
        return [path for path in accounts.iterdir() if path.is_dir() and not path.is_symlink()]
    return [root]


def state_of(directory):
    try:
        path = directory / "state.json"
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def updated_at(state):
    try:
        return dt.datetime.fromisoformat(state["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None


def plan(root, days):
    now = dt.datetime.now(dt.timezone.utc)
    candidates = []
    reports = 0
    for base in stores(root):
        for directory in base.iterdir():
            if not JOB.fullmatch(directory.name) or not directory.is_dir():
                continue
            if directory.is_symlink():
                continue
            state = state_of(directory)
            if state is None:
                continue
            if state.get("result_ready"):
                reports += 1
            stamp = updated_at(state)
            if stamp is None:
                continue
            age = (now - stamp).total_seconds() / 86400
            if age < days or state.get("status") == "running":
                continue
            candidates.append(
                {"path": str(directory), "age_days": round(age, 1), "status": state.get("status")}
            )
    return {"reports": reports, "candidates": candidates}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="artifacts/web-investigations")
    parser.add_argument("--days", type=int, default=30, help="retention window for finished jobs")
    parser.add_argument(
        "--apply", action="store_true", help="delete candidates; dry run by default"
    )
    args = parser.parse_args()
    root = Path(args.store)
    if not root.is_dir():
        raise SystemExit(f"store not found: {root}")
    if not 1 <= args.days <= 3650:
        raise SystemExit("--days must be between 1 and 3650")
    outcome = plan(root, args.days)
    deleted = 0
    if args.apply:
        for item in outcome["candidates"]:
            shutil.rmtree(item["path"])
            deleted += 1
    outcome.update({"days": args.days, "applied": args.apply, "deleted": deleted})
    print(json.dumps(outcome, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
