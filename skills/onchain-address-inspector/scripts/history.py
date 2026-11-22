"""Bounded historical candidates and explicit operator-supplied coverage attestations."""

import http.client
import json
import os
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
ROUTESCAN = {1: "mainnet"}
ROUTESCAN_HOST = "api.routescan.io"
ROUTESCAN_PAGE = 1000
ROUTESCAN_WINDOW = 10_000
INDEX_PAGE_BUDGET = 60
MAX_INDEX_LOGS = 2000
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
APPROVAL = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
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

    def discover(self, address, start, end, state=None, save=None):
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

    def discover(self, address, start, end, state=None, save=None):
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


def _index_state(name, chain_id, address, start, end):
    return {
        "schema_version": 1,
        "provider": name,
        "chain_id": chain_id,
        "address": address,
        "start_block": start,
        "end_block": end,
        "queue": [[start, end]],
        "current_page": 1,
        "rows": [],
        "gaps": [],
        "requests": 0,
        "done": False,
        "watermark": None,
    }


def _resume_state(state, name, chain_id, address, start, end):
    fresh = _index_state(name, chain_id, address, start, end)
    valid = (
        isinstance(state, dict)
        and state.get("schema_version") == 1
        and state.get("provider") == name
        and state.get("chain_id") == chain_id
        and state.get("address") == address
        and state.get("start_block") == start
        and state.get("end_block") == end
        and isinstance(state.get("queue"), list)
        and isinstance(state.get("rows"), list)
        and isinstance(state.get("gaps"), list)
        and all(
            isinstance(item, list) and len(item) == 2 and all(type(value) is int for value in item)
            for item in state["queue"]
        )
        and type(state.get("current_page", 1)) is int
        and state.get("current_page", 1) >= 1
    )
    if valid:
        return state
    if isinstance(state, dict):
        state.clear()
        state.update(fresh)
        return state
    return fresh


class RoutescanIndex:
    """Credential-free Etherscan-compatible history on Routescan.

    Coverage is attested only when the provider reports the exact end block
    (timestamp reconciled with RPC) and every block range is fully paged.
    That is a provider watermark, not cryptographic proof of absence.
    """

    name = "routescan_etherscan_compatible"
    logs = None

    def __init__(self, chain_id, client, rpc, fetch=get_json):
        if chain_id not in ROUTESCAN:
            raise QueryError(
                "Routescan history is not available for this network", "index_not_supported"
            )
        self.chain_id, self.client, self.rpc, self.fetch = chain_id, client, rpc, fetch
        self.coverage = {"basis": "unverified", "attested": False, "gaps": []}
        self.log_coverage = {"basis": "unverified", "attested": False, "gaps": []}

    def read(self, **params):
        query = urlencode([(key, str(value)) for key, value in params.items() if value is not None])
        return self.fetch(
            ROUTESCAN_HOST,
            f"/v2/network/{ROUTESCAN[self.chain_id]}/evm/{self.chain_id}/etherscan/api?{query}",
            self.client,
            attempts=3,
        )

    @staticmethod
    def result(payload, empty):
        status = str(payload.get("status"))
        message = str(payload.get("message") or "")
        result = payload.get("result")
        if status == "1" and isinstance(result, list):
            return result
        low = message.lower()
        if "not supported" in low:
            raise QueryError("History index does not serve this network", "index_not_supported")
        if "result window is too large" in low:
            raise QueryError("Index pagination window exceeded", "index_page_window")
        if "rate limit" in low or "too many requests" in low:
            raise QueryError("History index rate limit", "index_rate_limited")
        # An empty array alone is not evidence of absence. Etherscan-compatible
        # providers use status 0 with an explicit no-record message; anything
        # else is a rejected response, never a proven zero.
        if status == "0" and isinstance(result, list) and not result:
            if any(
                text in low
                for text in ("no transactions found", "no logs found", "no records found")
            ):
                return []
        raise QueryError(f"{empty} index response rejected", "index_response")

    def watermark(self, end, state, start=0):
        number, header_hash, seconds = self.rpc.header(end)
        # Re-read the provider watermark on every run. A cached watermark alone
        # would let a resumed report claim coverage that the provider no longer
        # reports, and would hide provider-side corrections.
        payload = self.read(module="block", action="getblockreward", blockno=end)
        row = payload.get("result")
        if payload.get("status") != "1" or not isinstance(row, dict):
            raise QueryError("Index cannot attest the range end", "index_head_unverified")
        try:
            block = int(row["blockNumber"])
            stamp = int(row["timeStamp"])
        except (KeyError, TypeError, ValueError):
            raise QueryError("Malformed index block watermark", "index_response") from None
        if block != end or stamp != seconds or number != end:
            raise QueryError("Index block watermark differs from RPC", "index_watermark_mismatch")
        provider_hash = row.get("blockHash")
        if provider_hash is not None:
            if not valid(HASH, provider_hash) or provider_hash.lower() != header_hash:
                raise QueryError("Index block hash differs from RPC", "index_watermark_mismatch")
        current = {
            "block": block,
            "timestamp": stamp,
            "block_hash": header_hash,
            "provider_block_hash": provider_hash.lower() if provider_hash else None,
            "verified": True,
        }
        cached = state.get("watermark")
        if isinstance(cached, dict) and cached.get("block") == end:
            if cached == current:
                return cached
            # Same height but a different provider/RPC view (reorg, provider
            # correction, or a stale file): discard range progress before
            # attesting anything from the cached rows.
            name = state.get("provider", self.name)
            chain_id = state.get("chain_id", self.chain_id)
            address = state.get("address")
            state.clear()
            state.update(_index_state(name, chain_id, address, start, end))
        state["watermark"] = current
        return current

    def discover(self, address, start, end, state=None, save=None):
        state = _resume_state(state, self.name, self.chain_id, address, start, end)
        transient = []
        try:
            # Validate the watermark first: a reorg resets range progress.
            self.watermark(end, state, start)
        except QueryError as error:
            transient.append({"reason": error.category, "from": start, "to": end})
        rows, gaps = state["rows"], state["gaps"]
        by_hash = {
            row[0]: (row[1], row[2])
            for row in rows
            if isinstance(row, list) and len(row) == 3 and isinstance(row[2], dict)
        }
        try:
            cap = int(os.environ.get("CHAININQUEST_INDEX_MAX_PAGES", INDEX_PAGE_BUDGET))
        except ValueError:
            cap = INDEX_PAGE_BUDGET
        cap = min(max(cap, 1), 200)
        run_requests = 0
        while state["queue"] and run_requests < cap:
            if self.client.remaining() <= 1:
                transient.append({"from": start, "to": end, "reason": "index_deadline"})
                break
            first, last = state["queue"][0]
            page = state.get("current_page", 1)
            state["requests"] += 1
            run_requests += 1
            try:
                items = self.result(
                    self.read(
                        module="account",
                        action="txlist",
                        address=address,
                        startblock=first,
                        endblock=last,
                        page=page,
                        offset=ROUTESCAN_PAGE,
                        sort="asc",
                    ),
                    "Transaction",
                )
            except QueryError as error:
                if error.category == "index_page_window" and last > first:
                    middle = (first + last) // 2
                    state["queue"][:1] = [[first, middle], [middle + 1, last]]
                    state["current_page"] = 1
                elif error.category in {"index_deadline", "index_rate_limited"}:
                    transient.append({"reason": error.category, "from": first, "to": last})
                    break
                else:
                    gaps.append({"reason": error.category, "from": first, "to": last})
                    state["queue"].pop(0)
                    state["current_page"] = 1
                if save:
                    save(state)
                continue
            for item in items:
                try:
                    hash_, block, claims = self.row(item, address, first, last)
                except (ValueError, TypeError, KeyError, AttributeError):
                    gaps.append({"reason": "index_row", "from": first, "to": last})
                    continue
                if hash_ in by_hash:
                    if by_hash[hash_] != (block, claims):
                        gaps.append(
                            {
                                "reason": "index_row_conflict",
                                "hash": hash_,
                                "from": first,
                                "to": last,
                            }
                        )
                    continue
                by_hash[hash_] = (block, claims)
                rows.append([hash_, block, claims])
            if len(items) < ROUTESCAN_PAGE:
                state["queue"].pop(0)
                state["current_page"] = 1
            elif page >= ROUTESCAN_WINDOW // ROUTESCAN_PAGE:
                if last > first:
                    middle = (first + last) // 2
                    state["queue"][:1] = [[first, middle], [middle + 1, last]]
                    state["current_page"] = 1
                else:
                    gaps.append({"reason": "index_page_window", "from": first, "to": last})
                    state["queue"].pop(0)
                    state["current_page"] = 1
            else:
                state["current_page"] = page + 1
            if save:
                save(state)
        if state["queue"] and run_requests >= cap:
            transient.append({"from": start, "to": end, "reason": "index_page_budget"})
        state["done"] = not state["queue"]
        if save:
            save(state)
        gaps_all = gaps + transient
        attested = bool(state.get("watermark")) and state["done"] and not gaps_all
        self.coverage = {
            "provider": self.name,
            "endpoint": "api.routescan.io (credential-free Etherscan-compatible API)",
            "basis": (
                "provider_block_watermark_and_pagination_exhausted"
                if attested
                else "partial_pagination_or_watermark"
            ),
            "attested": attested,
            "watermark": state.get("watermark"),
            "attestation": {
                "kind": "provider_watermark_and_exhaustive_pagination",
                "proves": (
                    "the provider reports the requested end block and returned every page it "
                    "holds for each requested range"
                ),
                "does_not_prove": (
                    "independent on-chain proof that the provider omitted no records; this is "
                    "not a consensus-level completeness proof"
                ),
                "watermark_fields": (
                    "block number and timestamp; provider block hash when supplied"
                ),
            },
            "pagination": {
                "page_size": ROUTESCAN_PAGE,
                "requests": state["requests"],
                "requests_this_run": run_requests,
                "rows": len(rows),
                "terminal": state["done"],
                "pending_ranges": len(state["queue"]),
                "provider_window": ROUTESCAN_WINDOW,
            },
            "coverage_scope": (
                "Provider txlist over the exact block range; every candidate is reconciled "
                "against RPC before it contributes to metrics"
            ),
            "gaps": gaps_all,
        }
        return [(row[0], row[1], row[2]) for row in rows], gaps_all

    @staticmethod
    def row(item, address, first, last):
        if not isinstance(item, dict):
            raise TypeError
        hash_, block, block_hash = item["hash"], item["blockNumber"], item["blockHash"]
        if not valid(HASH, hash_) or not valid(HASH, block_hash):
            raise ValueError
        block = int(block)
        if not first <= block <= last:
            raise ValueError
        sender, to = item["from"], item.get("to") or ""
        if not valid(ADDRESS, sender) or not valid(CLAIMS["to"], to):
            raise ValueError
        if address not in {sender.lower(), to.lower()}:
            raise ValueError
        claims = {
            key: item[key] for key, pattern in CLAIMS.items() if valid(pattern, item.get(key))
        }
        claims.update({"from": sender.lower(), "to": to.lower()})
        return hash_.lower(), block, claims

    def logs_queries(self, address, state):
        participant = "0x" + "0" * 24 + address[2:]
        wanted = [
            ("emitter", {"address": address}, 2),
            ("transfer_in", {"topic0": TRANSFER, "topic1": participant}, 0),
            ("approval_in", {"topic0": APPROVAL, "topic1": participant}, 0),
            ("transfer_out", {"topic0": TRANSFER, "topic2": participant}, 1),
            ("approval_out", {"topic0": APPROVAL, "topic2": participant}, 1),
        ]
        if not isinstance(state.get("queries"), list) or len(state["queries"]) != len(wanted):
            state["queries"] = [
                {"key": key, "params": params, "filter": index, "next_page": 1, "done": False}
                for key, params, index in wanted
            ]
        return state["queries"]

    def discover_logs(self, address, first, last, state=None, save=None):
        state = _resume_state(state, self.name + ":logs", self.chain_id, address, first, last)
        transient = []
        try:
            self.watermark(last, state, first)
        except QueryError as error:
            transient.append({"reason": error.category, "from": first, "to": last})
        rows, gaps = state["rows"], state["gaps"]
        by_key = {
            (row[0].get("transactionHash"), row[0].get("logIndex")): row[0]
            for row in rows
            if isinstance(row, list) and row and isinstance(row[0], dict)
        }
        queries = self.logs_queries(address, state)
        for query in queries:
            while not query["done"]:
                if len(rows) >= MAX_INDEX_LOGS:
                    query["done"] = True
                    # Persist the truncation: a resumed run must not treat a
                    # budget-stopped query as an exhausted one.
                    gaps.append(
                        {
                            "reason": "log_budget",
                            "from": first,
                            "to": last,
                            "filter": query["filter"],
                        }
                    )
                    break
                if self.client.remaining() <= 1:
                    transient.append({"reason": "index_deadline", "from": first, "to": last})
                    break
                try:
                    items = self.result(
                        self.read(
                            module="logs",
                            action="getLogs",
                            fromBlock=first,
                            toBlock=last,
                            page=query["next_page"],
                            offset=ROUTESCAN_PAGE,
                            sort="asc",
                            **query["params"],
                        ),
                        "Log",
                    )
                except QueryError as error:
                    if error.category in {"index_deadline", "index_rate_limited"}:
                        transient.append(
                            {
                                "reason": error.category,
                                "from": first,
                                "to": last,
                                "filter": query["filter"],
                            }
                        )
                    else:
                        gaps.append(
                            {
                                "reason": error.category,
                                "from": first,
                                "to": last,
                                "filter": query["filter"],
                            }
                        )
                        query["done"] = True
                    break
                for item in items:
                    try:
                        raw = self.log_row(item, address, query["filter"])
                    except (ValueError, TypeError, KeyError, AttributeError, QueryError):
                        gaps.append(
                            {
                                "reason": "index_log_row",
                                "from": first,
                                "to": last,
                                "filter": query["filter"],
                            }
                        )
                        continue
                    key = raw["transactionHash"], raw["logIndex"]
                    if key in by_key:
                        if by_key[key] != raw:
                            gaps.append(
                                {
                                    "reason": "index_log_conflict",
                                    "from": first,
                                    "to": last,
                                    "filter": query["filter"],
                                }
                            )
                        continue
                    by_key[key] = raw
                    rows.append([raw, query["filter"]])
                if len(items) < ROUTESCAN_PAGE:
                    query["done"] = True
                elif query["next_page"] >= ROUTESCAN_WINDOW // ROUTESCAN_PAGE:
                    query["done"] = True
                    gaps.append(
                        {
                            "reason": "index_log_page_window",
                            "from": first,
                            "to": last,
                            "filter": query["filter"],
                        }
                    )
                else:
                    query["next_page"] += 1
                if save:
                    save(state)
        done = all(query["done"] for query in queries)
        gaps_all = gaps + transient
        attested = bool(state.get("watermark")) and done and not gaps_all
        self.log_coverage = {
            "provider": self.name,
            "basis": (
                "provider_block_watermark_and_pagination_exhausted"
                if attested
                else "partial_index_log_scope"
            ),
            "attested": attested,
            "watermark": state.get("watermark"),
            "attestation": {
                "kind": "provider_watermark_and_exhaustive_pagination",
                "proves": (
                    "the provider reports the requested end block and returned every log page "
                    "it holds for each participation filter"
                ),
                "does_not_prove": (
                    "independent on-chain proof that the provider omitted no logs; receipts are "
                    "the independent check for each returned row"
                ),
            },
            "queries": {query["key"]: query["done"] for query in queries},
            "rows": len(rows),
            "gaps": gaps_all,
            "coverage_scope": (
                "Emitter logs plus Transfer/Approval participation, reconciled against RPC receipts"
            ),
        }
        if save:
            save(state)
        return [(row[0], first, last, row[1]) for row in rows], gaps_all

    def log_row(self, item, address, filter_index):
        if not isinstance(item, dict):
            raise TypeError
        if not variants(item.get("logIndex")) or not variants(item.get("transactionIndex")):
            item = self.repair_log(item, address)
        raw = {
            "address": item["address"],
            "topics": item["topics"],
            "data": item["data"],
            "blockNumber": item["blockNumber"],
            "blockHash": item["blockHash"],
            "transactionHash": item["transactionHash"],
            "transactionIndex": item["transactionIndex"],
            "logIndex": item["logIndex"],
            "removed": False,
        }
        if (
            not valid(ADDRESS, raw["address"])
            or not valid(HASH, raw["blockHash"])
            or not valid(HASH, raw["transactionHash"])
            or not isinstance(raw["topics"], list)
            or len(raw["topics"]) > 4
            or not all(valid(HASH, topic) for topic in raw["topics"])
            or not variants(raw["logIndex"])
            or not variants(raw["transactionIndex"])
            or not variants(raw["blockNumber"])
        ):
            raise ValueError
        if filter_index == 2 and raw["address"].lower() != address:
            raise ValueError
        return raw

    def repair_log(self, item, address):
        receipt = self.rpc.call("eth_getTransactionReceipt", [item.get("transactionHash")])
        logs = receipt.get("logs") if isinstance(receipt, dict) else None
        if not isinstance(logs, list):
            raise ValueError
        matches = [
            row
            for row in logs
            if isinstance(row, dict)
            and row.get("address", "").lower() == str(item.get("address", "")).lower()
            and row.get("topics") == item.get("topics")
            and row.get("data") == item.get("data")
            and row.get("blockHash") == item.get("blockHash")
        ]
        if len(matches) != 1:
            raise ValueError
        return {
            **item,
            "logIndex": matches[0].get("logIndex"),
            "transactionIndex": matches[0].get("transactionIndex"),
            "blockNumber": matches[0].get("blockNumber"),
        }


def variants(value):
    return isinstance(value, str) and re.fullmatch(r"(?:0x)?[0-9a-fA-F]{1,16}", value) is not None


def provider_for(chain_id, raw_client, rpc, key):
    """Select the strongest available history provider for the network."""
    snapshot_root = os.environ.get("CHAININQUEST_HISTORY_DIR")
    if snapshot_root:
        return SnapshotIndex(snapshot_root, chain_id, rpc)
    if chain_id in ROUTESCAN:
        return RoutescanIndex(chain_id, raw_client, rpc)
    if key:
        from adapters import EtherscanAdapter

        return EtherscanAdapter(key, raw_client, chain_id)
    if chain_id in BLOCKSCOUT:
        return BlockscoutIndex(chain_id, raw_client, rpc)
    return None
