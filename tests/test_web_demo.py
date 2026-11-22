import http.client
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from test_v3 import ContractQuery, JobStore, request, run

spec = importlib.util.spec_from_file_location(
    "chaininquest_web", Path(__file__).parents[1] / "web/server.py"
)
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)


class WebDemoTests(unittest.TestCase):
    def test_api_guards_and_exports_a_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            demo = web.Demo(store, runner=lambda s, job: run(s, job, query_fn=ContractQuery()))
            with web.server(demo, 0) as app:
                worker = threading.Thread(target=app.serve_forever, daemon=True)
                worker.start()
                port = app.server_address[1]

                def call(method, path, payload=None, **headers):
                    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    try:
                        client.request(method, path, payload, headers)
                        response = client.getresponse()
                        return response.status, dict(response.getheaders()), response.read()
                    finally:
                        client.close()

                try:
                    status, headers, _ = call("GET", "/")
                    self.assertEqual(status, 200)
                    self.assertIn("object-src 'none'", headers["Content-Security-Policy"])
                    status, _, page = call("GET", "/")
                    self.assertIn(b"Investigate a Web3 project", page)
                    self.assertIn(b'id="addresses"', page)
                    self.assertIn(b'id="source"', page)
                    self.assertEqual(call("GET", "/", Host="untrusted.example")[0], 403)
                    self.assertEqual(call("GET", "/api/jobs/../../state.json")[0], 404)
                    self.assertEqual(call("POST", "/api/jobs", json.dumps(request()))[0], 403)
                    authorized = {
                        "Origin": f"http://127.0.0.1:{port}",
                        "X-ChainInquest": "local",
                        "Content-Type": "application/json",
                    }
                    status, _, body = call("POST", "/api/jobs", json.dumps(request()), **authorized)
                    self.assertEqual(status, 202, body)
                    job = json.loads(body)["id"]
                    for _ in range(50):
                        status, _, body = call("GET", f"/api/jobs/{job}")
                        self.assertEqual(status, 200)
                        if json.loads(body)["result_ready"]:
                            break
                        time.sleep(0.02)
                    status, headers, body = call("GET", f"/api/jobs/{job}/result")
                    self.assertEqual(status, 200)
                    self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
                    report = json.loads(body)
                    self.assertTrue(report["complete"])
                    self.assertEqual(
                        report["chains"]["10143"]["report"]["deterministic_metrics"][
                            "transactions"
                        ],
                        1,
                    )
                    self.assertTrue(report["chains"]["10143"]["analysis"]["graph"]["facts"])
                    answers = {
                        "completed": "yes",
                        "understood": "partly",
                        "incomplete": "no",
                        "reuse": "yes",
                        "comment": "clear",
                    }
                    status, _, body = call(
                        "POST", f"/api/jobs/{job}/feedback", json.dumps(answers), **authorized
                    )
                    self.assertEqual(status, 201, body)
                    record = json.loads((Path(tmp) / job / "feedback.json").read_text())
                    self.assertEqual(record["answers"]["understood"], "partly")
                    self.assertGreaterEqual(record["elapsed_seconds"], 0)
                    self.assertEqual(record["coverage"]["complete"], 1)
                    status, _, _ = call(
                        "POST",
                        f"/api/jobs/{job}/feedback",
                        json.dumps({**answers, "completed": "maybe"}),
                        **authorized,
                    )
                    self.assertEqual(status, 400)
                    pending = store.create(web.plan_investigation(request()))
                    status, _, _ = call(
                        "POST", f"/api/jobs/{pending}/feedback", json.dumps(answers), **authorized
                    )
                    self.assertEqual(status, 409)
                    summary = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).parents[1] / "scripts/beta_feedback.py"),
                            "--store",
                            tmp,
                        ],
                        capture_output=True,
                        check=True,
                        text=True,
                    )
                    totals = json.loads(summary.stdout)
                    self.assertEqual(totals["feedback"], 1)
                    self.assertEqual(totals["completed_yes_percent"], 100.0)
                    self.assertEqual(totals["coverage_reported"]["complete"], 1)
                finally:
                    app.shutdown()
                    worker.join()

    def test_closed_beta_auth_review_isolation_and_html_export(self):
        import hashlib

        alice, bob = "a" * 40, "b" * 40
        digests = json.dumps(
            {k: hashlib.sha256(v.encode()).hexdigest() for k, v in (("alice", alice), ("bob", bob))}
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            demo = web.Demo(
                store,
                runner=lambda s, job: run(s, job, query_fn=ContractQuery()),
                origin="https://beta.example.org",
                credentials=digests,
            )
            with web.server(demo, 0) as app:
                worker = threading.Thread(target=app.serve_forever, daemon=True)
                worker.start()
                port = app.server_address[1]

                def call(method, path, token=None, value=None, origin="https://beta.example.org"):
                    client = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                    headers = {
                        "Host": "beta.example.org",
                        "Origin": origin,
                        "X-ChainInquest": "local",
                        "Content-Type": "application/json",
                    }
                    if token:
                        headers["Authorization"] = "Bearer " + token
                    try:
                        client.request(
                            method, path, json.dumps(value) if value is not None else None, headers
                        )
                        r = client.getresponse()
                        return r.status, dict(r.getheaders()), r.read()
                    finally:
                        client.close()

                try:
                    status, headers, raw = call("GET", "/healthz")
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(raw)["status"], "ok")
                    self.assertIn("Strict-Transport-Security", headers)
                    self.assertEqual(call("POST", "/api/plan", value=request())[0], 401)
                    args = {**request(), "objective": "show activity"}
                    args["manifest"]["project"] = "<script>alert(123)</script>"
                    self.assertEqual(
                        call("POST", "/api/plan", alice, args, "https://wrong.example")[0], 403
                    )
                    self.assertEqual(call("POST", "/api/jobs", alice, args)[0], 400)
                    status, _, raw = call("POST", "/api/plan", alice, args)
                    self.assertEqual(status, 200)
                    preview = json.loads(raw)
                    self.assertEqual(preview["plan"]["planner"]["mode"], "deterministic")
                    approval = {"approval_id": preview["approval_id"]}
                    self.assertEqual(call("POST", "/api/jobs", bob, approval)[0], 400)
                    status, _, raw = call("POST", "/api/jobs", alice, approval)
                    self.assertEqual(status, 202)
                    job = json.loads(raw)["id"]
                    self.assertEqual(call("POST", "/api/jobs", alice, approval)[0], 400)
                    path = f"/api/jobs/{job}"
                    self.assertEqual(call("GET", path, bob)[0], 404)
                    self.assertEqual(call("POST", path + "/resume", bob)[0], 404)
                    for _ in range(20):
                        status, _, raw = call("GET", path, alice)
                        if status == 200 and json.loads(raw)["result_ready"]:
                            break
                        time.sleep(0.03)
                    status, headers, raw = call("GET", path + "/html", alice)
                    self.assertEqual(status, 200)
                    self.assertIn(".html", headers["Content-Disposition"])
                    self.assertIn(b"&lt;script&gt;", raw)
                    self.assertNotIn(b"<script>", raw)
                    self.assertEqual(call("GET", path + "/html", bob)[0], 404)
                    for _ in range(7):
                        call("POST", "/api/jobs", bob, {})
                    self.assertEqual(call("POST", "/api/jobs", bob, {})[0], 429)
                finally:
                    app.shutdown()
                    worker.join()
