import contextlib
import hashlib
import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "web"))
sys.path.insert(0, str(ROOT / "skills/onchain-address-inspector/scripts"))

from access import Access  # noqa: E402
from evidence import QueryError  # noqa: E402
from test_v3 import JobStore, plan_investigation, request  # noqa: E402

spec = importlib.util.spec_from_file_location("chaininquest_deploy_web", ROOT / "web/server.py")
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)

preflight_spec = importlib.util.spec_from_file_location(
    "chaininquest_preflight", ROOT / "deploy/preflight.py"
)
preflight = importlib.util.module_from_spec(preflight_spec)
preflight_spec.loader.exec_module(preflight)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store"
        self.store.mkdir()
        self.store.chmod(0o700)
        self.repository = self.root / "repository"
        self.repository.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def outcome(self, **overrides):
        values = {
            "store": str(self.store),
            "origin": "https://beta.example.com",
            "tokens": json.dumps({"alice": "a" * 64}),
            "repository": self.repository,
            "allow_root": True,
        }
        values.update(overrides)
        return preflight.run(**values)

    def test_valid_configuration_passes_without_echoing_digests(self):
        outcome = self.outcome()
        self.assertTrue(outcome["ok"], outcome["errors"])
        self.assertEqual(outcome["summary"]["users"], 1)
        self.assertEqual(outcome["summary"]["store_mode"], "0700")
        self.assertNotIn("a" * 64, json.dumps(outcome))

    def test_http_origin_is_rejected(self):
        self.assertFalse(self.outcome(origin="http://beta.example.com")["ok"])

    def test_origin_with_path_is_rejected(self):
        self.assertFalse(self.outcome(origin="https://beta.example.com/app")["ok"])

    def test_bad_digest_is_rejected(self):
        outcome = self.outcome(tokens=json.dumps({"alice": "not-a-digest"}))
        self.assertFalse(outcome["ok"])
        self.assertIn("SHA-256", " ".join(outcome["errors"]))

    def test_permissive_store_mode_is_rejected(self):
        self.store.chmod(0o755)
        outcome = self.outcome()
        self.assertFalse(outcome["ok"])
        self.assertIn("0700", " ".join(outcome["errors"]))

    def test_symlinked_store_is_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.store, target_is_directory=True)
        outcome = self.outcome(store=str(link))
        self.assertFalse(outcome["ok"])
        self.assertIn("symlink", " ".join(outcome["errors"]))

    def test_store_inside_repository_is_rejected(self):
        inside = self.repository / "data"
        inside.mkdir()
        inside.chmod(0o700)
        outcome = self.outcome(store=str(inside))
        self.assertFalse(outcome["ok"])
        self.assertIn("outside the application repository", " ".join(outcome["errors"]))

    def test_root_execution_requires_explicit_override(self):
        outcome = self.outcome(allow_root=False)
        if os.geteuid() == 0:
            self.assertFalse(outcome["ok"])
        else:
            self.assertTrue(outcome["ok"], outcome["errors"])


class TokenLifecycleTests(unittest.TestCase):
    def test_rotation_and_revocation_take_effect_for_new_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second, other = "a" * 40, "b" * 40, "c" * 40
            origin = "https://beta.example.com"
            access = Access(
                Path(tmp), origin, json.dumps({"alice": digest(first), "bob": digest(other)})
            )
            self.assertEqual(access.authenticate("Bearer " + first), "alice")

            rotated = Access(Path(tmp), origin, json.dumps({"alice": digest(second)}))
            with self.assertRaises(QueryError) as raised:
                rotated.authenticate("Bearer " + first)
            self.assertEqual(raised.exception.category, "authentication_required")
            self.assertEqual(rotated.authenticate("Bearer " + second), "alice")

            revoked = Access(Path(tmp), origin, json.dumps({"bob": digest(other)}))
            with self.assertRaises(QueryError) as raised:
                revoked.authenticate("Bearer " + first)
            self.assertEqual(raised.exception.category, "authentication_required")
            self.assertEqual(revoked.authenticate("Bearer " + other), "bob")


class RuntimeSafetyTests(unittest.TestCase):
    def test_job_lock_rejects_a_second_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            job = store.create(plan_investigation(request()))
            with store.runner(job):
                with self.assertRaises(QueryError) as raised:
                    with store.runner(job):
                        pass
                self.assertEqual(raised.exception.category, "job_running")
            with store.runner(job):
                pass

    def test_second_concurrent_investigation_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = threading.Event()
            store = JobStore(tmp)
            demo = web.Demo(store, runner=lambda s, job: gate.wait(10))
            job = demo.start(plan=plan_investigation(request()), store=store)
            self.assertEqual(len(job), 32)
            with self.assertRaises(QueryError) as raised:
                demo.start(plan=plan_investigation(request()), store=store)
            self.assertEqual(raised.exception.category, "demo_busy")
            gate.set()
            for _ in range(200):
                if not demo.worker.locked():
                    break
                time.sleep(0.01)
            self.assertFalse(demo.worker.locked())

    def test_request_logs_never_contain_credentials_or_bodies(self):
        token = "A" * 40
        origin = "https://beta.example.com"
        marker = "SECRET-BODY-7f3"
        with tempfile.TemporaryDirectory() as tmp:
            demo = web.Demo(
                JobStore(tmp),
                runner=lambda s, job: None,
                origin=origin,
                credentials=json.dumps({"alice": digest(token)}),
            )
            with web.server(demo, 0) as app:
                worker = threading.Thread(target=app.serve_forever, daemon=True)
                worker.start()
                port = app.server_address[1]

                def call(method, path, payload=None, **headers):
                    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    try:
                        client.request(method, path, payload, headers)
                        response = client.getresponse()
                        return response.status, response.read()
                    finally:
                        client.close()

                authorized = {
                    "Host": "beta.example.com",
                    "Origin": origin,
                    "X-ChainInquest": "local",
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                }
                stderr = io.StringIO()
                try:
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(call("GET", "/healthz", Host="beta.example.com")[0], 200)
                        self.assertEqual(call("GET", "/api/jobs/" + "0" * 32, **authorized)[0], 404)
                        self.assertEqual(
                            call("POST", "/api/plan", json.dumps({"marker": marker}), **authorized)[
                                0
                            ],
                            400,
                        )
                finally:
                    app.shutdown()
                    worker.join()
                logs = stderr.getvalue()
                self.assertIn("GET /healthz", logs)
                self.assertNotIn(token, logs)
                self.assertNotIn(digest(token), logs)
                self.assertNotIn(marker, logs)
                self.assertNotIn("Authorization", logs)


if __name__ == "__main__":
    unittest.main()
