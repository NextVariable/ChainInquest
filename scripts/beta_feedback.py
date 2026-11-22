#!/usr/bin/env python3
"""Summarize structured closed-beta feedback from a web investigation store."""

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path

JOB = re.compile(r"[0-9a-f]{32}")
QUESTIONS = ("completed", "understood", "incomplete", "reuse")


def store_roots(root):
    accounts = root / "accounts"
    if accounts.is_dir():
        return [path for path in accounts.iterdir() if path.is_dir() and not path.is_symlink()]
    return [root]


def load(path, limit=1024 * 1024):
    try:
        if path.is_symlink() or path.stat().st_size > limit:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def rate(count, total):
    return round(100 * count / total, 1) if total else None


def summarize(root):
    reports = feedback = 0
    elapsed = []
    coverage = Counter()
    answers = {key: Counter() for key in QUESTIONS}
    for base in store_roots(root):
        for directory in base.iterdir():
            if (
                not JOB.fullmatch(directory.name)
                or not directory.is_dir()
                or directory.is_symlink()
            ):
                continue
            state = load(directory / "state.json")
            if state is None or not state.get("result_ready"):
                continue
            reports += 1
            document = load(directory / "feedback.json", 16 * 1024)
            if document is None:
                continue
            feedback += 1
            if isinstance(document.get("elapsed_seconds"), int):
                elapsed.append(document["elapsed_seconds"])
            observed = document.get("coverage") or {}
            if isinstance(observed.get("chains"), int) and observed["chains"]:
                coverage[
                    "complete" if observed.get("complete") == observed["chains"] else "partial"
                ] += 1
            for key, counter in answers.items():
                value = (document.get("answers") or {}).get(key)
                if isinstance(value, str):
                    counter[value] += 1
    return {
        "reports": reports,
        "feedback": feedback,
        "feedback_rate_percent": rate(feedback, reports),
        "completed_yes_percent": rate(answers["completed"]["yes"], feedback),
        "understood_yes_percent": rate(answers["understood"]["yes"], feedback),
        "incomplete_noticed_percent": rate(answers["incomplete"]["yes"], feedback),
        "reuse_yes_percent": rate(answers["reuse"]["yes"], feedback),
        "elapsed_seconds": {
            "median": statistics.median(elapsed) if elapsed else None,
            "min": min(elapsed) if elapsed else None,
            "max": max(elapsed) if elapsed else None,
        },
        "answers": {key: dict(counter) for key, counter in answers.items()},
        "coverage_reported": dict(coverage),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="artifacts/web-investigations")
    parser.add_argument("--output", help="write the summary JSON to this path instead of stdout")
    args = parser.parse_args()
    root = Path(args.store)
    if not root.is_dir():
        raise SystemExit(f"store not found: {root}")
    body = json.dumps(summarize(root), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(body + "\n", encoding="utf-8")
    else:
        print(body)


if __name__ == "__main__":
    main()
