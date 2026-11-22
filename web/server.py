"""Serve the single-operator ChainInquest demo over loopback."""

import argparse
import json
import logging
import re
import sys
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/onchain-address-inspector/scripts"))

from evidence import QueryError, invalid_constant, unique_object  # noqa: E402
from jobs import JobStore  # noqa: E402
from v3 import plan_investigation, run  # noqa: E402

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
JOB_ROUTE = re.compile(r"/api/jobs/([0-9a-f]{32})(?:/(result|resume))?")
MAX_INPUT = 128 * 1024


class Demo:
    def __init__(self, store, runner=run):
        self.store, self.runner = store, runner
        self.worker = threading.Lock()

    def start(self, *, plan=None, job_id=None):
        if not self.worker.acquire(blocking=False):
            raise QueryError("Wait until the current investigation ends", "demo_busy")
        try:
            if job_id is None:
                job_id = self.store.create(plan)
            else:
                self.store.directory(job_id)

            def work():
                try:
                    self.runner(self.store, job_id)
                except (OSError, ValueError, QueryError) as error:
                    logging.warning(
                        "Investigation runner exited: %s",
                        getattr(error, "category", "runtime_error"),
                    )
                finally:
                    self.worker.release()

            threading.Thread(target=work, daemon=True).start()
        except BaseException:
            self.worker.release()
            raise
        return job_id


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, demo, **kwargs):
        self.demo = demo
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send(
        self, payload, status=200, content_type="application/json; charset=utf-8", attachment=None
    ):
        body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, ensure_ascii=True).encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'",
        )
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def local_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def handle_error(self, error):
        code = getattr(error, "category", "invalid_request")
        status = {
            "invalid_job": 404,
            "not_ready": 409,
            "demo_busy": 409,
            "attempt_budget": 409,
            "storage_budget": 429,
        }.get(code, 400)
        self.send({"error": code}, status)

    def do_GET(self):
        if not self.local_host():
            return self.send({"error": "invalid_host"}, 403)
        route = self.path
        try:
            if route in STATIC:
                file, kind = STATIC[route]
                return self.send((ROOT / "web" / file).read_bytes(), content_type=kind)
            if route == "/api/examples/circle":
                return self.send((ROOT / "examples/circle-monad.json").read_bytes())
            match = JOB_ROUTE.fullmatch(route)
            if match and match[2] == "result":
                result = self.demo.store.result(match[1])
                return self.send(result, attachment=f"chaininquest-{match[1]}.json")
            if match and match[2] is None:
                return self.send(self.demo.store.state(match[1]))
            return self.send({"error": "not_found"}, 404)
        except (OSError, ValueError, RecursionError, QueryError) as error:
            return self.handle_error(error)

    def do_POST(self):
        if (
            not self.local_host()
            or self.headers.get("Origin") != "http://" + self.headers["Host"]
            or self.headers.get("X-ChainInquest") != "local"
        ):
            return self.send({"error": "same_origin_required"}, 403)
        try:
            if self.path == "/api/jobs":
                if self.headers.get_content_type() != "application/json" or self.headers.get(
                    "Transfer-Encoding"
                ):
                    raise ValueError
                size = int(self.headers["Content-Length"])
                if not 0 < size <= MAX_INPUT:
                    return self.send({"error": "input_budget"}, 413)
                payload = json.loads(
                    self.rfile.read(size),
                    object_pairs_hook=unique_object,
                    parse_constant=invalid_constant,
                )
                job_id = self.demo.start(plan=plan_investigation(payload))
            else:
                match = JOB_ROUTE.fullmatch(self.path)
                if not match or match[2] != "resume":
                    return self.send({"error": "not_found"}, 404)
                state = self.demo.store.state(match[1])
                if state["attempts"] >= 3 and state["status"] != "complete":
                    raise QueryError("Resume limit reached", "attempt_budget")
                job_id = self.demo.start(job_id=match[1])
            return self.send({"id": job_id}, 202)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, QueryError) as error:
            return self.handle_error(error)


def server(demo, port):
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, demo=demo))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--store", default="artifacts/web-investigations")
    args = p.parse_args()
    with server(Demo(JobStore(args.store)), args.port) as app:
        print(f"ChainInquest is listening on http://127.0.0.1:{app.server_address[1]}", flush=True)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
