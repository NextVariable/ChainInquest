#!/usr/bin/env python3
"""Validate a ChainInquest closed-beta deployment before starting the service.

Reads configuration from the environment by default. Never prints token digests,
tokens or file contents. Exits non-zero when a blocking check fails.
"""

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

USER = re.compile(r"[a-z][a-z0-9_-]{0,31}")
DIGEST = re.compile(r"[0-9a-f]{64}")


def check_origin(value):
    if not isinstance(value, str) or not value:
        return "CHAININQUEST_BETA_ORIGIN is required for closed beta"
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path
        or url.query
        or url.fragment
        or value != "https://" + url.netloc
    ):
        return "CHAININQUEST_BETA_ORIGIN must be an exact https origin without path or query"
    return None


def check_tokens(value):
    """Return (user_count, error) without echoing any digest."""
    if not isinstance(value, str) or not value:
        return 0, "CHAININQUEST_BETA_TOKEN_HASHES is required for closed beta"
    try:
        records = json.loads(value)
    except ValueError:
        return 0, "CHAININQUEST_BETA_TOKEN_HASHES must be a JSON object"
    if not isinstance(records, dict) or not 1 <= len(records) <= 16:
        return 0, "CHAININQUEST_BETA_TOKEN_HASHES must contain 1 to 16 users"
    if len(set(records.values())) != len(records):
        return 0, "token digests must be unique per user"
    for user, digest in records.items():
        if not USER.fullmatch(user):
            return 0, "user names must match [a-z][a-z0-9_-]{0,31}"
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            return 0, "every user needs a lowercase SHA-256 token digest"
    return len(records), None


def check_store(path, repository):
    errors, warnings = [], []
    root = Path(path)
    if not path:
        return ["--store is required"], warnings, "missing"
    if not root.is_absolute():
        warnings.append("store path is relative; use an absolute path in production")
    if root.is_symlink():
        return ["store must not be a symlink"], warnings, "symlink"
    if not root.is_dir():
        return ["store directory does not exist"], warnings, "missing"
    mode = stat.S_IMODE(root.stat().st_mode)
    if mode & 0o077:
        errors.append(f"store mode is {mode:04o}; require 0700")
    if not os.access(root, os.W_OK | os.X_OK):
        errors.append("store is not writable by the current service account")
    try:
        if repository.resolve() in root.resolve().parents or root.resolve() == repository.resolve():
            errors.append("store must live outside the application repository")
    except OSError:
        pass
    accounts = root / "accounts"
    if accounts.is_symlink():
        errors.append("store accounts directory must not be a symlink")
    elif accounts.is_dir() and stat.S_IMODE(accounts.stat().st_mode) & 0o077:
        errors.append("store accounts directory must use mode 0700")
    return errors, warnings, f"{mode:04o}"


def check_privileges(allow_root):
    if os.geteuid() == 0 and not allow_root:
        return ["run the service as a dedicated non-root account, not root"]
    return []


def run(store, origin, tokens, repository, allow_root=False):
    errors, warnings = [], []
    for item in (check_origin(origin), check_privileges(allow_root)):
        if isinstance(item, list):
            errors.extend(item)
        elif item:
            errors.append(item)
    users, token_error = check_tokens(tokens)
    if token_error:
        errors.append(token_error)
    store_errors, store_warnings, store_mode = check_store(store, repository)
    errors.extend(store_errors)
    warnings.extend(store_warnings)
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "origin": origin or "",
            "users": users,
            "store": store or "",
            "store_mode": store_mode,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default=os.environ.get("CHAININQUEST_STORE", ""))
    parser.add_argument("--origin", default=os.environ.get("CHAININQUEST_BETA_ORIGIN", ""))
    parser.add_argument("--tokens", default=os.environ.get("CHAININQUEST_BETA_TOKEN_HASHES", ""))
    parser.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--allow-root", action="store_true")
    args = parser.parse_args()
    outcome = run(
        args.store,
        args.origin,
        args.tokens,
        Path(args.repository),
        allow_root=args.allow_root,
    )
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
