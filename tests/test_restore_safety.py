import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERIFY = ROOT / "deploy/verify_archive.py"
RESTORE = ROOT / "deploy/restore.sh"


def member(name, kind=tarfile.REGTYPE, linkname="", payload=b"data"):
    item = tarfile.TarInfo(name)
    item.type = kind
    item.linkname = linkname
    item.mode = 0o600
    item.size = len(payload) if kind == tarfile.REGTYPE else 0
    return item, payload if kind == tarfile.REGTYPE else None


def archive(path, items):
    with tarfile.open(path, "w:gz") as handle:
        for item, payload in items:
            handle.addfile(item, io.BytesIO(payload) if payload is not None else None)


def checksum(path):
    value = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{value}  {path.name}\n", encoding="utf-8")


def verify(path, root="store"):
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFY),
            str(path),
            "--root",
            root,
            "--checksum",
            str(path) + ".sha256",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode, json.loads(result.stdout)


def valid_items():
    return [
        member("store", tarfile.DIRTYPE),
        member("store/accounts", tarfile.DIRTYPE),
        member("store/accounts/state.json", payload=b'{"ok":true}'),
    ]


class ArchiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, items, checksum_file=True):
        path = self.root / "store.tar.gz"
        archive(path, items)
        if checksum_file:
            checksum(path)
        return path

    def assert_rejected(self, items, fragment):
        path = self.build(items)
        code, outcome = verify(path)
        self.assertEqual(code, 1, outcome)
        self.assertIn(fragment, " ".join(outcome["errors"]))

    def test_valid_archive_passes(self):
        code, outcome = verify(self.build(valid_items()))
        self.assertEqual(code, 0, outcome)

    def test_absolute_path_is_rejected(self):
        self.assert_rejected(valid_items() + [member("/etc/passwd", payload=b"x")], "absolute path")

    def test_parent_traversal_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("store/../escape", payload=b"x")], "parent traversal"
        )

    def test_member_outside_store_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("other/file", payload=b"x")], "outside store root"
        )

    def test_symlink_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("store/link", tarfile.SYMTYPE, "/etc/passwd")], "symlink"
        )

    def test_hardlink_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("store/hard", tarfile.LNKTYPE, "store/accounts/state.json")],
            "hardlink",
        )

    def test_special_file_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("store/fifo", tarfile.FIFOTYPE)], "special file"
        )

    def test_duplicate_member_is_rejected(self):
        self.assert_rejected(
            valid_items() + [member("store/accounts/state.json", payload=b"again")],
            "duplicate member",
        )

    def test_missing_checksum_file_is_rejected(self):
        path = self.build(valid_items(), checksum_file=False)
        code, outcome = verify(path)
        self.assertEqual(code, 1)
        self.assertIn("checksum file is required", outcome["errors"])

    def test_checksum_mismatch_is_rejected(self):
        path = self.build(valid_items())
        Path(str(path) + ".sha256").write_text("0" * 64 + "  store.tar.gz\n", encoding="utf-8")
        code, outcome = verify(path)
        self.assertEqual(code, 1)
        self.assertIn("checksum mismatch", outcome["errors"])

    def test_truncated_archive_is_rejected(self):
        path = self.build(valid_items())
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        checksum(path)
        code, outcome = verify(path)
        self.assertEqual(code, 1)
        self.assertIn("unreadable archive", " ".join(outcome["errors"]))


class RestoreScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = self.base / "store"
        self.store.mkdir()
        (self.store / "marker").write_text("old", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_restore(self, archive_path):
        env = {**os.environ, "STORE": str(self.store), "PYTHON": sys.executable}
        return subprocess.run(
            ["sh", str(RESTORE), str(archive_path)],
            capture_output=True,
            text=True,
            env=env,
        )

    def make_archive(self, items, checksum_file=True):
        path = self.base / "backup.tar.gz"
        archive(path, items)
        if checksum_file:
            checksum(path)
        return path

    def test_missing_checksum_keeps_the_store(self):
        path = self.make_archive(valid_items(), checksum_file=False)
        result = self.run_restore(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum file is required", result.stderr)
        self.assertEqual((self.store / "marker").read_text(encoding="utf-8"), "old")

    def test_malicious_archive_is_rejected_before_extraction(self):
        path = self.make_archive(valid_items() + [member("store/../escape", payload=b"pwned")])
        result = self.run_restore(path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive validation failed", result.stderr)
        self.assertFalse((self.base / "escape").exists())
        self.assertEqual((self.store / "marker").read_text(encoding="utf-8"), "old")

    def test_valid_archive_replaces_store_and_keeps_previous(self):
        path = self.make_archive(
            [
                member("store", tarfile.DIRTYPE),
                member("store/marker", payload=b"new"),
            ]
        )
        result = self.run_restore(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.store / "marker").read_text(encoding="utf-8"), "new")
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o700)
        previous = list(self.base.glob("store.pre-restore-*"))
        self.assertEqual(len(previous), 1)
        self.assertEqual((previous[0] / "marker").read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.base.glob(".chaininquest-restore.*")), [])

    @unittest.skipIf(os.geteuid() == 0, "root ignores directory write permissions")
    def test_activation_failure_keeps_previous_store(self):
        path = self.make_archive(
            [member("store", tarfile.DIRTYPE), member("store/marker", payload=b"new")]
        )
        self.base.chmod(0o500)
        try:
            result = self.run_restore(path)
        finally:
            self.base.chmod(0o700)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.store / "marker").read_text(encoding="utf-8"), "old")
        self.assertFalse(list(self.base.glob(".chaininquest-restore.*")))


if __name__ == "__main__":
    unittest.main()
