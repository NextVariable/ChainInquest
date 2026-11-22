"""Serve the single-operator ChainInquest demo over loopback."""

import argparse
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "skills/onchain-address-inspector/scripts"))
sys.path.insert(0, str(ROOT / "web"))

from access import Access  # noqa: E402
from evidence import QueryError, invalid_constant, unique_object  # noqa: E402
from jobs import JobStore  # noqa: E402
from report_html import render  # noqa: E402
from v3 import plan_investigation, plan_objective, run  # noqa: E402

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
JOB_ROUTE = re.compile(r"/api/jobs/([0-9a-f]{32})(?:/(result|resume|html))?")
MAX_INPUT = 128 * 1024


class Demo:
    def __init__(self, store, runner=run, origin=None, credentials=None):
        self.store, self.runner = store, runner
        self.access = Access(store, origin, credentials)
        self.worker = threading.Lock()
        self.planner = threading.Lock()
        self.previews = {}

    def preview(self, user, payload):
        if not self.planner.acquire(blocking=False):
            raise QueryError("Plan worker is busy", "demo_busy")
        try:
            data = {**payload}
            objective = data.pop("objective", None)
            plan = (
                plan_investigation(data) if objective is None else plan_objective(data, objective)
            )
            token = secrets.token_urlsafe(24)
            now = time.monotonic()
            self.previews = {k: v for k, v in self.previews.items() if v[0] > now}
            if len(self.previews) >= 64:
                self.previews.pop(next(iter(self.previews)))
            self.previews[token] = (now + 300, user, plan)
            return {"approval_id": token, "plan": plan}
        finally:
            self.planner.release()

    def approved(self, user, token):
        if not isinstance(token, str) or len(token) > 64:
            raise QueryError("Review the investigation plan", "review_required")
        with self.planner:
            entry = self.previews.get(token)
            if not entry or entry[0] <= time.monotonic() or entry[1] != user:
                raise QueryError("Review the investigation plan", "review_required")
            result = self.start(plan=entry[2], store=self.access.store(user))
            del self.previews[token]
            return result

    def start(self, *, plan=None, job_id=None, store=None):
        store = store or self.store
        if not self.worker.acquire(blocking=False):
            raise QueryError("Wait until the current investigation ends", "demo_busy")
        try:
            if job_id is None:
                job_id = store.create(plan)
            else:
                store.directory(job_id)

            def work():
                try:
                    self.runner(store, job_id)
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
        if self.demo.access.origin:
            return self.headers.get("Host") == self.demo.access.origin.removeprefix("https://")
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
            "api_rate_limit": 429,
            "authentication_required": 401,
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
            if route == "/api/config":
                return self.send({"authentication_required": bool(self.demo.access.accounts)})
            user = self.demo.access.authenticate(self.headers.get("Authorization", ""))
            self.demo.access.limit(user, "GET")
            store = self.demo.access.store(user)
            match = JOB_ROUTE.fullmatch(route)
            if match and match[2] in {"result", "html"}:
                result = store.result(match[1])
                if match[2] == "html":
                    return self.send(
                        render(result),
                        content_type="text/html; charset=utf-8",
                        attachment=f"chaininquest-{match[1]}.html",
                    )
                return self.send(result, attachment=f"chaininquest-{match[1]}.json")
            if match and match[2] is None:
                return self.send(store.state(match[1]))
            return self.send({"error": "not_found"}, 404)
        except (OSError, ValueError, RecursionError, QueryError) as error:
            return self.handle_error(error)

    def do_POST(self):
        if (
            not self.local_host()
            or self.headers.get("Origin")
            != (self.demo.access.origin or "http://" + self.headers["Host"])
            or self.headers.get("X-ChainInquest") != "local"
        ):
            return self.send({"error": "same_origin_required"}, 403)
        try:
            user = self.demo.access.authenticate(self.headers.get("Authorization", ""))
            self.demo.access.limit(user, "POST")
            store = self.demo.access.store(user)
            if self.path in {"/api/jobs", "/api/plan"}:
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
                if not isinstance(payload, dict):
                    raise ValueError
                if self.path == "/api/plan":
                    return self.send(self.demo.preview(user, payload))
                if "approval_id" in payload or self.demo.access.accounts:
                    job_id = self.demo.approved(user, payload.get("approval_id"))
                else:
                    job_id = self.demo.start(plan=plan_investigation(payload), store=store)
            else:
                match = JOB_ROUTE.fullmatch(self.path)
                if not match or match[2] != "resume":
                    return self.send({"error": "not_found"}, 404)
                state = store.state(match[1])
                if state["attempts"] >= 3 and state["status"] != "complete":
                    raise QueryError("Resume limit reached", "attempt_budget")
                job_id = self.demo.start(job_id=match[1], store=store)
            return self.send({"id": job_id}, 202)
        except (OSError, ValueError, TypeError, KeyError, RecursionError, QueryError) as error:
            return self.handle_error(error)


class BoundedServer(ThreadingHTTPServer):
    slots = threading.BoundedSemaphore(16)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def server(demo, port):
    return BoundedServer(("127.0.0.1", port), partial(Handler, demo=demo))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--store", default="artifacts/web-investigations")
    args = p.parse_args()
    demo = Demo(
        JobStore(args.store),
        origin=os.environ.get("CHAININQUEST_BETA_ORIGIN"),
        credentials=os.environ.get("CHAININQUEST_BETA_TOKEN_HASHES"),
    )
    with server(demo, args.port) as app:
        print(f"ChainInquest is listening on http://127.0.0.1:{app.server_address[1]}", flush=True)
        try:
            app.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
