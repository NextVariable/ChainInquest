import http.client
import importlib.util
import json
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
                finally:
                    app.shutdown()
                    worker.join()
