#!/usr/bin/env python3
"""Inspect and prune closed-beta investigation data under a web store."""

import argparse
import datetime as dt
import fcntl
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
    path = directory / "state.json"
    try:
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def stamp_of(state):
    try:
        stamp = dt.datetime.fromisoformat(state["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone.utc)


def candidate(base, directory, days, now):
    """Return an aged, finished job that is a direct, real child of base."""
    if directory.is_symlink() or not directory.is_dir():
        return None
    try:
        if directory.resolve().parent != base.resolve():
            return None
    except OSError:
        return None
    state = state_of(directory)
    if state is None or state.get("status") in {"running", "queued"}:
        return None
    stamp = stamp_of(state)
    if stamp is None:
        return None
    age = (now - stamp).total_seconds() / 86400
    if age < days:
        return None
    return {
        "path": str(directory),
        "base": str(base),
        "age_days": round(age, 1),
        "status": state.get("status"),
    }


def lock(directory):
    handle = (directory / ".run.lock").open("a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def delete(item, days, now):
    """Re-check under the run lock immediately before deleting."""
    directory = Path(item["path"])
    base = Path(item["base"])
    if candidate(base, directory, days, now) is None:
        return False
    handle = lock(directory)
    if handle is None:
        return False
    try:
        if candidate(base, directory, days, now) is None:
            return False
        shutil.rmtree(directory)
        return True
    finally:
        handle.close()


def plan(root, days):
    now = dt.datetime.now(dt.timezone.utc)
    reports = 0
    items = []
    for base in stores(root):
        for directory in base.iterdir():
            if not JOB.fullmatch(directory.name):
                continue
            if directory.is_symlink() or not directory.is_dir():
                continue
            state = state_of(directory)
            if state is not None and state.get("result_ready"):
                reports += 1
            found = candidate(base, directory, days, now)
            if found:
                items.append(found)
    return {"reports": reports, "candidates": items}


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
    deleted, skipped = 0, []
    if args.apply:
        now = dt.datetime.now(dt.timezone.utc)
        for item in outcome["candidates"]:
            try:
                if delete(item, args.days, now):
                    deleted += 1
                else:
                    skipped.append(item["path"])
            except OSError as error:
                skipped.append(f"{item['path']}: {error.strerror or 'delete failed'}")
    outcome.update(
        {"days": args.days, "applied": args.apply, "deleted": deleted, "skipped": skipped}
    )
    print(json.dumps(outcome, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
