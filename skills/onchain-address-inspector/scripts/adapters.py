"""Read-only discovery sources. Indexed candidates need RPC corroboration."""

import concurrent.futures
import http.client
import json
import re
import ssl
import threading
from collections import deque
from contextlib import contextmanager
from urllib.parse import urlencode

from evidence import QueryError, invalid_constant, quantity, unique_object

HASH = re.compile(r"0x[0-9a-fA-F]{64}")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")
INDEX_PAGE = 100
INDEX_PAGES = 5
LOG_SPAN = 10_000
MAX_LOG_QUERIES = 120
MAX_LOGS = 2000
MAX_LOG_BYTES = 8 * 1024 * 1024


class RpcAdapter:
    def __init__(self, client):
        self.client = client
        self.headers = {}

    def call(self, method, params):
        return self.client.call(method, params)

    @contextmanager
    def reserve_snapshot_budget(self):
        deadline = getattr(self.client, "deadline", None)
        if deadline is not None:
            available = self.client.remaining()
            self.client.deadline -= min(5.0, available / 3)
        try:
            yield
        finally:
            if deadline is not None:
                self.client.deadline = deadline

    def header(self, block, *, fresh=False):
        if not fresh and block in self.headers:
            return self.headers[block]
        tag = hex(block) if isinstance(block, int) else block
        raw = self.call("eth_getBlockByNumber", [tag, False])
        if not isinstance(raw, dict) or not HASH.fullmatch(str(raw.get("hash"))):
            raise QueryError("Missing or malformed block header", "invalid_block")
        number = quantity(raw.get("number"))
        if isinstance(block, int) and number != block:
            raise QueryError("Header number mismatch", "invalid_block")
        header = number, raw["hash"].lower(), quantity(raw.get("timestamp"))
        if not fresh:
            self.headers[number] = header
        return header

    def lower_bound(self, timestamp, tip):
        # Bracket from the tip first so short recent windows do not touch
        # unrelated old history on a pruned provider.
        if self.header(tip)[2] < timestamp:
            return tip + 1
        hi = tip
        step = 1
        while True:
            probe = max(0, tip - step)
            if self.header(probe)[2] < timestamp:
                lo = probe + 1
                break
            hi = probe
            if probe == 0:
                lo = 0
                break
            step *= 2
        while lo < hi:
            mid = (lo + hi) // 2
            if self.header(mid)[2] < timestamp:
                lo = mid + 1
            else:
                hi = mid
        for n in (lo - 1, lo):
            if 0 <= n <= tip and (self.header(n)[2] < timestamp) != (n < lo):
                raise QueryError("Timestamp boundary could not be validated", "invalid_block")
        return lo

    def logs(self, start, end, filters):
        """Return raw evidence and uncompleted filter/block ranges."""
        evidence, gaps = [], []
        budget = MAX_LOG_QUERIES
        bytes_left = MAX_LOG_BYTES
        work = deque((start, end, i) for i in range(len(filters)))
        while work:
            a, b, i = work.popleft()
            if budget <= 0 or bytes_left <= 0 or len(evidence) >= MAX_LOGS:
                gaps.append({"from": a, "to": b, "filter": i, "reason": "log_budget"})
                continue
            if b - a + 1 > LOG_SPAN:
                work.append((a + LOG_SPAN, b, i))
                b = a + LOG_SPAN - 1
            budget -= 1
            try:
                rows = self.call(
                    "eth_getLogs", [{"fromBlock": hex(a), "toBlock": hex(b), **filters[i]}]
                )
                if not isinstance(rows, list):
                    raise QueryError("Expected array of logs", "invalid_response")
                for row in rows:
                    size = len(json.dumps(row, ensure_ascii=True))
                    if size > bytes_left or len(evidence) >= MAX_LOGS:
                        gaps.append({"from": a, "to": b, "filter": i, "reason": "log_budget"})
                        break
                    evidence.append((row, a, b, i))
                    bytes_left -= size
            except QueryError as error:
                if error.category in {"rpc_error", "invalid_response"} and a < b:
                    mid = (a + b) // 2
                    work.extend([(a, mid, i), (mid + 1, b, i)])
                else:
                    gaps.append({"from": a, "to": b, "filter": i, "reason": error.category})
                    if error.category in {
                        "deadline_exceeded",
                        "rate_limited",
                        "dns_failure",
                        "tls_failure",
                    }:
                        budget = 0
        return evidence, gaps


class EtherscanAdapter:
    """Bounded V2 normal-transaction discovery for Monad Testnet."""

    def __init__(self, key, client):
        self.key = key
        self.client = client

    def page(self, address, start, end, page):
        timeout = min(self.client.remaining(), self.client.timeout)
        params = urlencode(
            {
                "chainid": 10143,
                "module": "account",
                "action": "txlist",
                "address": address,
                "startblock": start,
                "endblock": end,
                "page": page,
                "offset": INDEX_PAGE,
                "sort": "asc",
                "apikey": self.key,
            }
        )
        connection = http.client.HTTPSConnection(
            "api.etherscan.io", timeout=timeout, context=ssl.create_default_context()
        )

        def fetch():
            try:
                connection.request("GET", "/v2/api?" + params)
                response = connection.getresponse()
                body = response.read(2**20 + 1)
                if response.status != 200 or len(body) > 2**20:
                    raise QueryError("Index HTTP failure or oversized page", "index_http_error")
                return json.loads(
                    body, object_pairs_hook=unique_object, parse_constant=invalid_constant
                )
            finally:
                connection.close()

        # Fixed single in-flight index call. Slow reads/DNS must not delay the CLI deadline.
        future = concurrent.futures.Future()

        def worker():
            try:
                future.set_result(fetch())
            except Exception as error:
                future.set_exception(error)

        threading.Thread(target=worker, daemon=True).start()
        try:
            payload = future.result(timeout=timeout)
        except (OSError, ValueError, RecursionError, http.client.HTTPException) as error:
            category = "index_timeout" if isinstance(error, TimeoutError) else "index_transport"
            raise QueryError("Index request failed", category) from None
        if not isinstance(payload, dict):
            raise QueryError("Malformed index response", "index_response")
        rows = payload.get("result")
        if (
            rows == []
            and payload.get("status") == "0"
            and payload.get("message") == "No transactions found"
        ):
            return []
        if payload.get("status") != "1" or not isinstance(rows, list) or len(rows) > INDEX_PAGE:
            raise QueryError("Index rejected query or returned malformed page", "index_response")
        return rows

    def discover(self, address, start, end):
        rows, gaps = [], []
        seen = set()
        last = start
        for page in range(1, INDEX_PAGES + 1):
            try:
                items = self.page(address, start, end, page)
                for item in items:
                    h = item.get("hash") if isinstance(item, dict) else None
                    block = item.get("blockNumber") if isinstance(item, dict) else None
                    if (
                        not isinstance(h, str)
                        or not HASH.fullmatch(h)
                        or not isinstance(block, str)
                        or not re.fullmatch(r"[0-9]{1,20}", block)
                    ):
                        raise QueryError("Invalid index row", "index_row")
                    h, block = h.lower(), int(block)
                    if not last <= block <= end or h in seen:
                        raise QueryError("Index order, range or pagination conflict", "index_row")
                    last = block
                    seen.add(h)
                    rows.append((h, block))
                if len(items) < INDEX_PAGE:
                    return rows, gaps
            except QueryError as error:
                return rows, [{"reason": error.category, "page": page}]
        return rows, [{"reason": "index_page_budget", "page": INDEX_PAGES + 1}]
