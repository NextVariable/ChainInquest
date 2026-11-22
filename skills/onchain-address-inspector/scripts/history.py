"""Bounded historical candidates and explicit operator-supplied coverage attestations."""

import http.client
import json
import re
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlencode

from adapters import ADDRESS, HASH
from citations import digest
from evidence import QueryError, invalid_constant, unique_object
from project import load_json, parse_time, source_url

MAX_RESPONSE = 8 * 1024 * 1024
BLOCKSCOUT = {1: "eth.blockscout.com"}
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_NEXT_REQUEST = 0.0
CLAIMS = {
    "from": ADDRESS,
    "to": re.compile(r"(?:0x[0-9a-fA-F]{40})?"),
    "value": re.compile(r"[0-9]{1,78}"),
    "blockHash": HASH,
    "timeStamp": re.compile(r"[0-9]{1,20}"),
    "txreceipt_status": re.compile(r"[01]"),
    "isError": re.compile(r"[01]"),
}


def get_json(host, path, client, *, attempts=2):
    """Fixed-host HTTPS transport. A daemon bounds slow DNS and body reads."""
    global _NEXT_REQUEST
    key = host, path
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 15:
            return cached[1]
    for attempt in range(attempts):
        with _LOCK:
            now = time.monotonic()
            wait = max(0, _NEXT_REQUEST - now)
            if wait >= client.remaining():
                raise QueryError("Index budget exhausted", "index_deadline")
            _NEXT_REQUEST = now + wait + 0.5
        if wait:
            time.sleep(wait)
        seconds = min(client.remaining(), client.timeout, 8)
        status, body = public_response(host, path, seconds, getattr(client, "transport", None))
        if status == 200:
            try:
                if len(body) > MAX_RESPONSE:
                    raise ValueError
                result = json.loads(
                    body, object_pairs_hook=unique_object, parse_constant=invalid_constant
                )
                if not isinstance(result, dict):
                    raise ValueError
            except (ValueError, RecursionError, QueryError):
                raise QueryError("Invalid index response", "index_response") from None
            # Cache small responses only. No authenticated data enters this cache.
            if len(body) <= 512 * 1024:
                with _LOCK:
                    _CACHE[key] = (time.monotonic(), result)
                    _CACHE.move_to_end(key)
                    while len(_CACHE) > 16:
                        _CACHE.popitem(last=False)
            return result
        category = {
            401: "index_key_required",
            403: "index_refused",
            404: "index_not_supported",
            429: "index_rate_limited",
        }.get(status, "index_transport")
        if attempt + 1 >= attempts or status not in {0, 429, 502, 503, 504}:
            raise QueryError("Public index request failed", category)
    raise AssertionError("Invalid index attempt budget")


def public_response(host, path, seconds, transport):
    if transport == "curl":
        try:
            r = subprocess.run(
                [
                    "curl",
                    "--disable",
                    "--silent",
                    "--show-error",
                    "--proto",
                    "=https",
                    "--max-time",
                    str(seconds),
                    "--max-filesize",
                    str(MAX_RESPONSE),
                    "--write-out",
                    "\n%{http_code}",
                    "https://" + host + path,
                ],
                capture_output=True,
                timeout=seconds,
                check=False,
            )
            if r.returncode == 28:
                raise TimeoutError
            body, sep, status = r.stdout.rpartition(b"\n")
            return (
                (int(status), body) if not r.returncode and sep and status.isdigit() else (0, b"")
            )
        except (TimeoutError, subprocess.TimeoutExpired):
            raise QueryError("Index transport timed out", "index_timeout") from None
        except OSError:
            return 0, b""
    outcome = []
    ready = threading.Event()

    def request():
        connection = http.client.HTTPSConnection(host, timeout=seconds)
        try:
            connection.request("GET", path, headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE + 1)
            outcome.append((response.status, body))
        except (OSError, http.client.HTTPException):
            outcome.append((0, b""))
        finally:
            connection.close()
            ready.set()

    threading.Thread(target=request, daemon=True).start()
    if not ready.wait(seconds):
        raise QueryError("Index transport timed out", "index_timeout")
    return outcome[0]


def number(value):
    if type(value) is not int or not 0 <= value < 2**63:
        raise ValueError("Invalid block")
    return value


def valid(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


class BlockscoutIndex:
    name = "blockscout_public_transactions"

    def __init__(self, chain_id, client, rpc, fetch=get_json):
        self.host = BLOCKSCOUT[chain_id]
        self.client, self.rpc, self.fetch = client, rpc, fetch
        self.coverage = {"basis": "observed_head_only", "attested": False}
        self.logs = None

    def read(self, path):
        return self.fetch(self.host, "/api/v2" + path, self.client)

    def discover(self, address, start, end):
        rows, gaps, seen, cursors = [], [], set(), set()
        try:
            status = self.read("/main-page/indexing-status")
            blocks = self.read("/blocks")["items"]
            top = blocks[0]
            height = number(top["height"])
            if not valid(HASH, top.get("hash")):
                raise ValueError
            verified = self.rpc.header(height)[1] == top["hash"].lower()
            self.coverage.update(
                {
                    "observed_index_head": height,
                    "head_hash": top["hash"].lower(),
                    "head_matches_rpc": verified,
                    "finished_indexing_blocks": status.get("finished_indexing_blocks") is True,
                }
            )
            if not verified or height < end:
                gaps.append({"reason": "index_head_unverified_or_behind", "from": start, "to": end})
            path, cursor, last = f"/addresses/{address}/transactions", {}, 2**63
            for _ in range(6):
                page = self.read(path + ("?" + urlencode(cursor) if cursor else ""))
                entries = page["items"]
                if not isinstance(entries, list) or len(entries) > 100:
                    raise ValueError
                for row in entries:
                    if row.get("block_number") is None and row.get("status") is None:
                        continue
                    block = number(row["block_number"])
                    h = row["hash"]
                    if not valid(HASH, h) or block > last or h.lower() in seen:
                        raise ValueError
                    h = h.lower()
                    seen.add(h)
                    last = block
                    if block < start:
                        return rows, gaps
                    if block > end:
                        continue
                    sender, to = row["from"]["hash"], row["to"]["hash"] if row["to"] else ""
                    value = row["value"]
                    if (
                        not valid(ADDRESS, sender)
                        or not valid(CLAIMS["to"], to)
                        or not valid(CLAIMS["value"], value)
                    ):
                        raise ValueError
                    if address not in {sender.lower(), to.lower()}:
                        raise ValueError
                    claims = {
                        "from": sender.lower(),
                        "to": to.lower(),
                        "value": value,
                        "timeStamp": str(int(parse_time(row["timestamp"]).timestamp())),
                    }
                    if row.get("status") in {"ok", "error"}:
                        claims["txreceipt_status"] = "1" if row["status"] == "ok" else "0"
                    rows.append((h, block, claims))
                cursor = page["next_page_params"]
                if cursor is None:
                    return rows, gaps
                if (
                    not isinstance(cursor, dict)
                    or not cursor
                    or not all(
                        k
                        in {
                            "index",
                            "value",
                            "hash",
                            "inserted_at",
                            "block_number",
                            "fee",
                            "items_count",
                        }
                        and (v is None or type(v) is int or isinstance(v, str) and len(v) <= 100)
                        for k, v in cursor.items()
                    )
                ):
                    raise ValueError
                cursor = {k: v for k, v in cursor.items() if v is not None}
                token = urlencode(sorted(cursor.items()))
                if not token or token in cursors:
                    raise QueryError("Index cursor did not advance", "index_cursor")
                cursors.add(token)
            gaps.append({"reason": "index_page_budget", "from": start, "to": end})
        except QueryError as error:
            gaps.append({"reason": error.category, "from": start, "to": end})
        except (KeyError, IndexError, ValueError, TypeError, AttributeError, RecursionError):
            gaps.append({"reason": "index_invalid_page", "from": start, "to": end})
        return rows, gaps


class SnapshotIndex:
    name = "operator_history_snapshot"

    def __init__(self, root, chain_id, rpc):
        self.root, self.chain_id, self.rpc = Path(root), chain_id, rpc
        self.coverage = {"basis": "operator_attestation", "attested": False}
        self.logs = None

    def discover(self, address, start, end):
        self.coverage = {"basis": "operator_attestation", "attested": False}
        self.logs = None
        try:
            doc, _ = load_json(self.root / f"{self.chain_id}-{address}.json", MAX_RESPONSE)
            if (
                doc["schema_version"] != 1
                or doc["chain_id"] != self.chain_id
                or doc["address"] != address
            ):
                raise ValueError
            source = source_url(doc["source"])
            watermark = doc["watermark"]
            if not (
                number(watermark["start_block"]) == start
                and number(watermark["indexed_through"]) == end
            ):
                raise QueryError(
                    "Snapshot does not cover the exact requested block range", "index_watermark_gap"
                )
            for block, field in ((start, "start_hash"), (end, "end_hash")):
                if self.rpc.header(block)[1] != watermark[field]:
                    raise QueryError(
                        "Snapshot watermark differs from RPC", "index_watermark_mismatch"
                    )
            tx = doc["transactions"]
            logs = doc.get("logs")
            if (
                not isinstance(tx, list)
                or len(tx) > 500
                or logs is not None
                and (not isinstance(logs, list) or len(logs) > 2000)
            ):
                raise ValueError
            rows, seen = [], set()
            for row in tx:
                block, h, claims = number(row["block"]), row["hash"], row["claims"]
                if (
                    not start <= block <= end
                    or not valid(HASH, h)
                    or h.lower() in seen
                    or not isinstance(claims, dict)
                ):
                    raise ValueError
                if not all(k in CLAIMS and valid(CLAIMS[k], v) for k, v in claims.items()):
                    raise ValueError
                if address not in {claims.get("from", "").lower(), claims.get("to", "").lower()}:
                    raise ValueError
                seen.add(h.lower())
                rows.append((h.lower(), block, claims))
            self.coverage = {
                "basis": "operator_attestation",
                "source": source,
                "attested": True,
                "watermark": {
                    k: watermark[k]
                    for k in ("start_block", "indexed_through", "start_hash", "end_hash")
                },
                "content_sha256": digest(doc),
                "transaction_scope": "top_level_direct_from_to",
            }
            if doc.get("log_scope") == "emitter_or_erc20_participant_v1":
                self.logs = logs
            return rows, []
        except QueryError as error:
            reason = error.category
        except FileNotFoundError:
            reason = "index_snapshot_missing"
        except (KeyError, ValueError, TypeError, OSError, AttributeError, RecursionError):
            reason = "index_invalid_snapshot"
        return [], [{"reason": reason, "from": start, "to": end}]
