"""Read-only discovery sources. Indexed candidates need RPC corroboration."""

import concurrent.futures
import http.client
import json
import os
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
CLAIMS = {
    "from": ADDRESS,
    "to": re.compile(r"(?:0x[0-9a-fA-F]{40})?"),
    "value": re.compile(r"[0-9]{1,78}"),
    "blockHash": HASH,
    "timeStamp": re.compile(r"[0-9]{1,20}"),
    "txreceipt_status": re.compile(r"[01]"),
    "isError": re.compile(r"[01]"),
}


class RpcAdapter:
    def __init__(self, client):
        self.client = client
        self.headers = {}
        self.log_failures = {}
        self.log_answered_ranges = []
        self.log_span = (
            100
            if getattr(client, "url", None) == "https://rpc-testnet.monadinfra.com"
            else LOG_SPAN
        )

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

    def logs(self, start, end, filters, state=None, save=None):
        """Return raw evidence and uncompleted filter/block ranges.

        When a state dict is supplied, answered chunks and their raw logs are
        persisted so a later run resumes instead of re-querying from scratch.
        """
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("start") != start
            or state.get("end") != end
            or state.get("filters") != len(filters)
        ):
            state = {
                "schema_version": 1,
                "provider": "rpc_getLogs",
                "start": start,
                "end": end,
                "filters": len(filters),
                "answered": [],
                "rows": [],
                "gaps": [],
                "requests": 0,
                "done": False,
            }
        answered = {tuple(item) for item in state["answered"] if isinstance(item, list)}
        evidence = [
            (row[0], row[1], row[2], row[3])
            for row in state["rows"]
            if isinstance(row, list) and len(row) == 4
        ]
        gaps = list(state["gaps"])
        budget = MAX_LOG_QUERIES
        bytes_left = MAX_LOG_BYTES - sum(
            len(json.dumps(row, ensure_ascii=True)) for row, _, _, _ in evidence
        )
        chunks = deque()
        for index in range(len(filters)):
            a = start
            while a <= end:
                b = min(end, a + self.log_span - 1)
                if (a, b, index) not in answered:
                    chunks.append((a, b, index))
                a = b + 1
        while chunks:
            a, b, i = chunks.popleft()
            if budget <= 0 or bytes_left <= 0 or len(evidence) >= MAX_LOGS:
                gaps.append({"from": a, "to": b, "filter": i, "reason": "log_budget"})
                continue
            budget -= 1
            state["requests"] += 1
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
                    state["rows"].append([row, a, b, i])
                    bytes_left -= size
                else:
                    self.log_answered_ranges.append({"from": a, "to": b, "filter": i})
                    state["answered"].append([a, b, i])
                    if save:
                        save(state)
            except QueryError as error:
                code = str(error) if re.fullmatch(r"RPC error code -?[0-9]+", str(error)) else None
                label = code or error.category
                self.log_failures[label] = self.log_failures.get(label, 0) + 1
                if error.category in {"rpc_error", "invalid_response"} and a < b:
                    mid = (a + b) // 2
                    chunks.extend([(a, mid, i), (mid + 1, b, i)])
                else:
                    gaps.append({"from": a, "to": b, "filter": i, "reason": error.category})
                    if error.category in {
                        "deadline_exceeded",
                        "rate_limited",
                        "dns_failure",
                        "tls_failure",
                    }:
                        budget = 0
        state["gaps"] = [gap for gap in gaps if gap not in state["gaps"]]
        state["done"] = not chunks and not state["gaps"]
        if save:
            save(state)
        return evidence, gaps


class EtherscanAdapter:
    """Bounded V2 normal-transaction discovery on the selected EVM network."""

    def __init__(self, key, client, chain_id=10143):
        self.key = key
        self.chain_id = chain_id
        self.client = client
        self.coverage = {"basis": "unverified", "attested": False, "gaps": []}

    def page(self, address, start, end, page):
        for attempt in range(3):
            try:
                return self._page(address, start, end, page)
            except QueryError as error:
                if error.category not in {"index_http_error", "index_transport"} or attempt == 2:
                    raise
                if hasattr(self.client, "pause"):
                    self.client.pause(0.5 * (attempt + 1))

    def _page(self, address, start, end, page):
        timeout = min(self.client.remaining(), self.client.timeout)
        params = urlencode(
            {
                "chainid": self.chain_id,
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
                    raise QueryError(f"Index HTTP failure {response.status}", "index_http_error")
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
        except (
            OSError,
            ValueError,
            RecursionError,
            http.client.HTTPException,
            concurrent.futures.TimeoutError,
        ) as error:
            timed_out = isinstance(error, (TimeoutError, concurrent.futures.TimeoutError))
            raise QueryError(
                "Index request failed", "index_timeout" if timed_out else "index_transport"
            ) from None
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

    def discover(self, address, start, end, state=None, save=None):
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or state.get("address") != address
            or state.get("start_block") != start
            or state.get("end_block") != end
        ):
            state = {
                "schema_version": 1,
                "provider": "etherscan_v2_txlist",
                "address": address,
                "start_block": start,
                "end_block": end,
                "next_page": 1,
                "rows": [],
                "gaps": [],
                "done": False,
            }
        rows = state["rows"]
        gaps = state["gaps"]
        seen = {row[0] for row in rows if isinstance(row, list) and len(row) == 3}
        last = max(
            (row[1] for row in rows if isinstance(row, list) and len(row) == 3), default=start
        )
        try:
            cap = int(os.environ.get("CHAININQUEST_INDEX_MAX_PAGES", INDEX_PAGES))
        except ValueError:
            cap = INDEX_PAGES
        cap = min(max(cap, 1), 200)
        page = state.get("next_page", 1)
        while page <= cap:
            if self.client is not None and self.client.remaining() <= 1:
                gaps.append({"reason": "index_deadline", "page": page})
                break
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
                    claims = {
                        key: item[key]
                        for key, pattern in CLAIMS.items()
                        if isinstance(item.get(key), str) and pattern.fullmatch(item[key])
                    }
                    rows.append([h, block, claims])
                if len(items) < INDEX_PAGE:
                    state["done"] = True
                    break
                page += 1
                state["next_page"] = page
                if save:
                    save(state)
            except QueryError as error:
                gaps.append({"reason": error.category, "page": page})
                break
        if page > cap and not state["done"]:
            gaps.append({"reason": "index_page_budget", "page": page})
        self.coverage = {
            "provider": "etherscan_v2_txlist",
            "basis": "pagination_without_provider_watermark",
            "attested": False,
            "pagination": {
                "page_size": INDEX_PAGE,
                "pages": page - 1,
                "rows": len(rows),
                "terminal": state["done"],
            },
            "coverage_scope": "Provider txlist; no indexed-through height attestation",
            "gaps": gaps,
        }
        if save:
            save(state)
        return [(row[0], row[1], row[2]) for row in rows], gaps
