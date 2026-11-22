"""Bounded local investigation records with atomic checkpoints and an exclusive runner."""

import fcntl
import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path

from evidence import QueryError
from project import load_json
from query import utc

MAX_JOBS = 32
MAX_RESULT_BYTES = 32 * 1024 * 1024


def write(path, value, limit=MAX_RESULT_BYTES):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    if len(data) > limit:
        raise QueryError("Investigation checkpoint exceeds storage budget", "storage_budget")
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class JobStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def directory(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise QueryError("Invalid investigation ID", "invalid_job")
        directory = self.root / job_id
        if not directory.is_dir() or directory.is_symlink():
            raise QueryError("Investigation not found", "invalid_job")
        return directory

    def create(self, plan):
        with (self.root / ".create.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            if sum(p.is_dir() for p in self.root.iterdir()) >= MAX_JOBS:
                raise QueryError("Local investigation quota reached", "storage_budget")
            job_id = uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir(mode=0o700)
            write(
                directory / "state.json",
                {
                    "id": job_id,
                    "status": "queued",
                    "plan": plan,
                    "created_at": utc(),
                    "completed": [],
                    "logs": [],
                    "attempts": 0,
                    "result_ready": False,
                },
            )
        return job_id

    def state(self, job_id):
        value = load_json(self.directory(job_id) / "state.json", 1024 * 1024)[0]
        if not isinstance(value, dict) or value.get("id") != job_id:
            raise QueryError("Investigation state file is malformed", "invalid_job")
        for key, kind in (
            ("status", str),
            ("plan", dict),
            ("created_at", str),
            ("completed", list),
            ("logs", list),
            ("attempts", int),
            ("result_ready", bool),
        ):
            if type(value.get(key)) is not kind:
                raise QueryError("Investigation state file is malformed", "invalid_job")
        return value

    def result(self, job_id):
        directory = self.directory(job_id)
        if not self.state(job_id)["result_ready"]:
            raise QueryError("Investigation result is not ready", "not_ready")
        value = load_json(directory / "result.json", MAX_RESULT_BYTES)[0]
        if not isinstance(value, dict):
            raise QueryError("Investigation result file is malformed", "invalid_job")
        return value

    @contextmanager
    def runner(self, job_id):
        directory = self.directory(job_id)
        with (directory / ".run.lock").open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise QueryError("Investigation is already running", "job_running") from None
            yield directory


def checkpoint(directory, state, phase, **fields):
    state.update(fields)
    state["phase"] = phase
    state["updated_at"] = utc()
    state["logs"] = (state["logs"] + [{"phase": phase, "at": state["updated_at"], **fields}])[-80:]
    write(directory / "state.json", state, 1024 * 1024)
