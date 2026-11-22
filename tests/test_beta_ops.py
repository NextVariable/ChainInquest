import datetime as dt
import fcntl
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import beta_ops

JOB = "a" * 32
NOW = dt.datetime.now(dt.timezone.utc)


def state(status="complete", days=40, **extra):
    stamp = (NOW - dt.timedelta(days=days)).isoformat()
    return {
        "id": JOB,
        "status": status,
        "plan": {},
        "created_at": stamp,
        "completed": [],
        "logs": [],
        "attempts": 1,
        "result_ready": status == "complete",
        "updated_at": stamp,
        **extra,
    }


class BetaOpsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, name, value, base=None):
        directory = (base or self.root) / name
        directory.mkdir(parents=True)
        if value is not None:
            (directory / "state.json").write_text(json.dumps(value), encoding="utf-8")
        return directory

    def test_aged_finished_job_is_deleted(self):
        directory = self.job(JOB, state())
        outcome = beta_ops.plan(self.root, 30)
        self.assertEqual(outcome["reports"], 1)
        self.assertEqual(len(outcome["candidates"]), 1)
        self.assertTrue(beta_ops.delete(outcome["candidates"][0], 30, NOW))
        self.assertFalse(directory.exists())

    def test_running_queued_fresh_and_malformed_jobs_are_kept(self):
        cases = {
            "1" * 32: state(status="running"),
            "2" * 32: state(status="queued"),
            "3" * 32: state(days=2),
            "4" * 32: None,
            "5" * 32: {"status": "complete"},
        }
        directories = [self.job(name, value) for name, value in cases.items()]
        self.assertEqual(beta_ops.plan(self.root, 30)["candidates"], [])
        for directory in directories:
            self.assertTrue(directory.exists())

    def test_symlinked_job_is_ignored_even_if_it_looks_aged(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "state.json").write_text(json.dumps(state()), encoding="utf-8")
        (self.root / JOB).symlink_to(outside, target_is_directory=True)
        outcome = beta_ops.plan(self.root, 30)
        self.assertEqual(outcome["candidates"], [])
        self.assertEqual(outcome["reports"], 0)

    def test_other_directories_are_ignored(self):
        notes = self.root / "notes"
        notes.mkdir()
        (notes / "state.json").write_text(json.dumps(state()), encoding="utf-8")
        self.assertEqual(beta_ops.plan(self.root, 30)["candidates"], [])

    def test_locked_job_is_not_deleted(self):
        directory = self.job(JOB, state())
        handle = (directory / ".run.lock").open("a")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            item = beta_ops.plan(self.root, 30)["candidates"][0]
            self.assertFalse(beta_ops.delete(item, 30, NOW))
            self.assertTrue(directory.exists())
        finally:
            handle.close()

    def test_delete_rechecks_state_before_removing(self):
        directory = self.job(JOB, state())
        item = beta_ops.plan(self.root, 30)["candidates"][0]
        (directory / "state.json").write_text(json.dumps(state(status="running")), encoding="utf-8")
        self.assertFalse(beta_ops.delete(item, 30, NOW))
        self.assertTrue(directory.exists())

    def test_account_store_is_scanned_without_leaving_its_base(self):
        base = self.root / "accounts" / "alice"
        directory = self.job(JOB, state(), base=base)
        outcome = beta_ops.plan(self.root, 30)
        self.assertEqual(len(outcome["candidates"]), 1)
        self.assertEqual(outcome["candidates"][0]["base"], str(base))
        self.assertTrue(beta_ops.delete(outcome["candidates"][0], 30, NOW))
        self.assertFalse(directory.exists())
        self.assertTrue(base.is_dir())


if __name__ == "__main__":
    unittest.main()
