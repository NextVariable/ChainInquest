#!/usr/bin/env python3
"""Validate a ChainInquest store archive before it is extracted.

Requires the checksum file, verifies every member, and rejects absolute paths,
parent traversal, symlinks, hardlinks, duplicate names, special files and any
member outside the store root. Prints JSON and exits non-zero on any issue.
"""

import argparse
import hashlib
import json
import pathlib
import sys
import tarfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def check_checksum(archive, checksum):
    if not checksum.is_file():
        return "checksum file is required"
    fields = checksum.read_text(encoding="utf-8").strip().split()
    if not fields or len(fields[0]) != 64:
        return "checksum file is malformed"
    if digest(archive) != fields[0].lower():
        return "checksum mismatch"
    return None


def check_members(archive, root):
    errors = []
    seen = set()
    try:
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            if not members:
                return ["archive is empty"]
            for item in members:
                name = item.name
                parts = pathlib.PurePosixPath(name).parts
                if name.startswith("/") or not parts:
                    errors.append(f"absolute path: {name}")
                    continue
                if ".." in parts:
                    errors.append(f"parent traversal: {name}")
                    continue
                if parts[0] != root:
                    errors.append(f"outside store root: {name}")
                    continue
                if name in seen:
                    errors.append(f"duplicate member: {name}")
                seen.add(name)
                if item.issym():
                    errors.append(f"symlink: {name}")
                elif item.islnk():
                    errors.append(f"hardlink: {name}")
                elif not (item.isfile() or item.isdir()):
                    errors.append(f"special file: {name}")
    except (tarfile.TarError, OSError, EOFError) as error:
        return [f"unreadable archive: {error}"]
    if not any(item.name == root and item.isdir() for item in members):
        errors.append(f"missing store root directory: {root}")
    return errors


def run(archive, root, checksum):
    archive = pathlib.Path(archive)
    if not archive.is_file():
        return {"ok": False, "errors": ["archive not found"]}
    if not root or "/" in root or root in {".", ".."}:
        return {"ok": False, "errors": ["invalid store root name"]}
    error = check_checksum(archive, pathlib.Path(checksum))
    if error:
        return {"ok": False, "errors": [error]}
    errors = check_members(archive, root)
    return {"ok": not errors, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive")
    parser.add_argument("--root", required=True)
    parser.add_argument("--checksum", required=True)
    args = parser.parse_args()
    outcome = run(args.archive, args.root, args.checksum)
    print(json.dumps(outcome, indent=2))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
