import copy
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from adapters import EtherscanAdapter, RpcAdapter
from event_decoder import APPROVAL, TRANSFER, AbiDecoder, decimal_amount
from evidence import QueryError
from investigation import investigate
from test_query import A, B, H, q

STAMP = 1700000000
TOKEN = "0x" + "12" * 20
INDEX_SECRET = "fake-index-test-key"


def block_hash(n):
    return "0x" + f"{n + 100:064x}"


def word(value):
    return "0x" + f"{int(value, 16) if isinstance(value, str) else value:064x}"


def iso(n):
    return dt.datetime.fromtimestamp(n, dt.timezone.utc).isoformat()


class Ledger:
    def __init__(self, tip=3):
        self.tip = tip
        self.calls = []
        self.tx = {
            "hash": H,
            "blockNumber": "0x2",
            "blockHash": block_hash(2),
            "from": B,
            "to": A,
            "value": hex(10**18),
            "input": "0xaabb",
        }
        self.log = {
            "address": TOKEN,
            "blockNumber": "0x2",
            "blockHash": block_hash(2),
            "transactionHash": H,
            "transactionIndex": "0x0",
            "logIndex": "0x0",
            "topics": [TRANSFER, word(A), word(A)],
            "data": word(1234567),
            "removed": False,
        }
        self.approval = {
            **self.log,
            "logIndex": "0x1",
            "topics": [APPROVAL, word(B), word(A)],
            "data": word(2**256 - 1),
        }
        self.unknown = {**self.log, "address": A, "logIndex": "0x2", "topics": [], "data": "0x"}
        self.logs = [self.log, self.approval, self.unknown]
        self.status = "0x1"
        self.decimals = word(6)
        self.changed = False

    def call(self, name, params):
        self.calls.append((name, params))
        if name == "eth_chainId":
            return "0x279f"
        if name == "eth_blockNumber":
            return hex(self.tip)
        if name == "eth_getCode":
            return "0x"
        if name == "eth_getBlockByNumber":
            n = self.tip if params[0] == "latest" else int(params[0], 16)
            h = block_hash(n)
            if self.changed and n == self.tip and params[0] != "latest":
                h = block_hash(n + 100)
            return {
                "number": hex(n),
                "hash": h,
                "parentHash": block_hash(n - 1),
                "timestamp": hex(STAMP + n),
                "transactions": [self.tx] if n == 2 else [],
            }
        if name == "eth_getLogs":
            filt = params[0]
            result = []
            if not int(filt["fromBlock"], 16) <= 2 <= int(filt["toBlock"], 16):
                return []
            for row in self.logs:
                if "address" in filt and row["address"] != filt["address"]:
                    continue
                topics = filt.get("topics", [])
                if any(
                    allowed is not None
                    and (
                        len(row["topics"]) <= i
                        or row["topics"][i]
                        not in (allowed if isinstance(allowed, list) else [allowed])
                    )
                    for i, allowed in enumerate(topics)
                ):
                    continue
                result.append(copy.deepcopy(row))
            return result
        if name == "eth_getTransactionReceipt":
            if self.status is None:
                return None
            return {
                **{
                    k: self.log[k]
                    for k in ("transactionHash", "blockHash", "blockNumber", "transactionIndex")
                },
                "status": self.status,
                "logs": copy.deepcopy(self.logs),
            }
        if name == "eth_call":
            return self.decimals
        if name == "eth_getTransactionByHash":
            return self.tx
        raise AssertionError(name)


def args(*options):
    parsed = q.parser().parse_args(["--address", A, "--investigate", *options])
    return parsed


class InvestigationTests(unittest.TestCase):
    def test_report_evidence_and_independent_values(self):
        chain = Ledger()
        result = investigate(args("--start-block", "1", "--end-block", "3"), chain)
        self.assertTrue(result["coverage"]["complete"])
        report = result["report"]
        evidence = report["on_chain_evidence"]
        metrics = report["deterministic_metrics"]
        self.assertEqual(
            metrics["transactions"]["native_value_successful_top_level_only"]["in_wei"], str(10**18)
        )
        self.assertEqual(evidence["transactions"][0]["raw_transaction"], chain.tx)
        self.assertEqual(len(evidence["events"]), 3)
        transfer, approval, unknown = evidence["events"]
        self.assertEqual(transfer["filters"], [0, 1])
        self.assertEqual(transfer["raw_log"], chain.log)
        self.assertEqual(transfer["token"]["amount_display"], "1.234567")
        self.assertEqual(int(approval["token"]["amount_raw"]), int(chain.approval["data"], 16))
        self.assertEqual(
            sum(r["direct_transactions"] for r in metrics["daily_utc"]),
            len(evidence["transactions"]),
        )
        self.assertEqual(sum(r["Transfer"] for r in metrics["daily_utc"]), 1)
        self.assertEqual(metrics["counterparties"]["one_transaction"], 1)
        self.assertEqual(report["model_inferences"], [])
        self.assertIsNone(unknown["abi"])
        self.assertEqual(sum(name == "eth_call" for name, _ in chain.calls), 1)

    def test_half_open_time_bounds_and_missing_head_coverage(self):
        chain = Ledger()
        result = investigate(
            args("--from-time", iso(STAMP + 1), "--to-time", iso(STAMP + 3)), chain, now=STAMP + 9
        )
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["report"]["resolved_range"]["end_block"], 2)
        late = investigate(
            args("--from-time", iso(STAMP + 1), "--to-time", iso(STAMP + 7)),
            Ledger(),
            now=STAMP + 9,
        )
        self.assertFalse(late["coverage"]["complete"])
        self.assertIn("after_rpc_head", [g["reason"] for g in late["coverage"]["events"]["gaps"]])

    def test_rolling_windows_do_not_scan_all_blocks(self):
        for days in [7, 30]:
            chain = Ledger(tip=20_001)
            with patch.dict(os.environ, {"ETHERSCAN_API_KEY": ""}):
                result = investigate(args("--days", str(days)), chain, now=STAMP + chain.tip)
            self.assertFalse(result["coverage"]["complete"])
            self.assertEqual(
                result["coverage"]["transactions"]["gaps"][0]["reason"], "index_key_required"
            )
            self.assertFalse(
                any(
                    method == "eth_getBlockByNumber" and params[1] for method, params in chain.calls
                )
            )
            self.assertLess(len(chain.calls), 70)

    def test_index_candidates_use_rpc_and_remain_partial(self):
        adapter = EtherscanAdapter(INDEX_SECRET, None)
        chain = Ledger(tip=4000)
        claims = {
            "hash": H,
            "blockNumber": "2",
            "blockHash": chain.tx["blockHash"],
            "from": B,
            "to": A,
            "value": str(10**18),
            "timeStamp": str(STAMP + 2),
            "txreceipt_status": "1",
            "isError": "0",
        }
        with patch.object(adapter, "page", return_value=[claims]):
            result = investigate(
                args("--from-time", iso(STAMP), "--to-time", iso(STAMP + 3000)),
                chain,
                adapter,
                now=STAMP + 4000,
            )
        with patch.object(
            adapter, "page", return_value=[{**claims, "value": "999", "isError": "1"}]
        ):
            conflict = investigate(
                args("--from-time", iso(STAMP), "--to-time", iso(STAMP + 3000)),
                Ledger(tip=4000),
                adapter,
                now=STAMP + 4000,
            )
        self.assertIn(
            "index_claim_mismatch",
            [g["reason"] for g in conflict["coverage"]["transactions"]["gaps"]],
        )
        self.assertEqual(
            conflict["report"]["deterministic_metrics"]["transactions"][
                "native_value_successful_top_level_only"
            ]["in_wei"],
            str(10**18),
        )
        coverage = result["coverage"]["transactions"]
        self.assertTrue(coverage["pagination_and_corroboration_complete"])
        self.assertFalse(coverage["complete"])
        self.assertEqual(result["report"]["deterministic_metrics"]["transactions"]["success"], 1)
        self.assertNotIn(INDEX_SECRET, json.dumps(result))

    def test_index_invalid_row_and_page_budget(self):
        adapter = EtherscanAdapter(INDEX_SECRET, None)
        with patch.object(adapter, "page", return_value=[{"hash": H, "blockNumber": "2"}] * 2):
            rows, gaps = adapter.discover(A, 0, 3)
        self.assertEqual(rows, [(H, 2, {})])
        self.assertEqual(gaps[0]["reason"], "index_row")
        with (
            patch("adapters.INDEX_PAGE", 1),
            patch("adapters.INDEX_PAGES", 1),
            patch.object(
                adapter,
                "page",
                return_value=[{"hash": H, "blockNumber": "2"}],
            ),
        ):
            self.assertEqual(adapter.discover(A, 0, 3)[1][0]["reason"], "index_page_budget")

    def test_etherscan_resume_rejects_foreign_state_and_drops_stale_gaps(self):
        from types import SimpleNamespace

        foreign = {
            "schema_version": 1,
            "provider": "another_provider",
            "address": A,
            "start_block": 1,
            "end_block": 3,
            "next_page": 2,
            "rows": [[H, 2, {}]],
            "gaps": [{"reason": "index_deadline", "page": 2}],
            "done": True,
        }
        adapter = EtherscanAdapter(INDEX_SECRET, SimpleNamespace(timeout=2, remaining=lambda: 2))
        with patch.object(adapter, "page", return_value=[]):
            rows, _ = adapter.discover(A, 1, 3, state=foreign)
        self.assertEqual(rows, [])
        self.assertEqual(adapter.coverage["pagination"]["rows"], 0)

        saved = []
        blocked = EtherscanAdapter(INDEX_SECRET, SimpleNamespace(timeout=2, remaining=lambda: 0))
        with patch.object(blocked, "page", return_value=[]):
            _, gaps = blocked.discover(A, 1, 3, save=saved.append)
        self.assertEqual([gap["reason"] for gap in gaps], ["index_deadline"])
        self.assertEqual(saved[-1]["gaps"], [])

        resumed = EtherscanAdapter(INDEX_SECRET, SimpleNamespace(timeout=2, remaining=lambda: 2))
        with patch.object(resumed, "page", return_value=[]):
            _, gaps = resumed.discover(A, 1, 3, state=saved[-1])
        self.assertEqual(gaps, [])
        self.assertTrue(saved[-1]["done"])

    def test_index_page_timeout_is_reported_on_python_310(self):
        class Client:
            timeout = 1

            @staticmethod
            def remaining():
                return 1

        class LegacyTimeout(Exception):
            """Python 3.10 concurrent.futures.TimeoutError is not OSError."""

        reached = threading.Event()

        class NeverReady:
            def result(self, timeout=None):
                reached.wait(2)
                raise LegacyTimeout

            def set_result(self, value):
                pass

            def set_exception(self, error):
                pass

        class Connection:
            def __init__(self, *args, **kwargs):
                pass

            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                reached.set()
                raise OSError

            def close(self):
                pass

        adapter = EtherscanAdapter(INDEX_SECRET, Client())
        with (
            patch("adapters.concurrent.futures.TimeoutError", LegacyTimeout),
            patch("adapters.concurrent.futures.Future", NeverReady),
            patch("adapters.http.client.HTTPSConnection", Connection),
        ):
            with self.assertRaises(QueryError) as raised:
                adapter._page(A, 0, 3, 1)
        self.assertEqual(raised.exception.category, "index_timeout")

    def test_nonstandard_shape_and_optional_decimals(self):
        chain = Ledger()
        chain.log["topics"].append(word(4))
        chain.decimals = word(256)
        result = investigate(args("--start-block", "2", "--end-block", "2"), chain)
        self.assertTrue(result["coverage"]["complete"])
        transfer, approval, _ = result["report"]["on_chain_evidence"]["events"]
        self.assertEqual(transfer["issues"], ["non_erc20_layout"])
        self.assertIsNone(transfer["token"])
        self.assertEqual(approval["issues"], ["decimals_unavailable"])
        self.assertIsNone(approval["token"]["amount_display"])
        self.assertEqual(decimal_amount("123", 0), "123")
        self.assertEqual(decimal_amount("0", 255), "0." + "0" * 255)

    def test_missing_receipt_cannot_become_failed_or_validate_event(self):
        chain = Ledger()
        chain.status = None
        result = investigate(args("--start-block", "2", "--end-block", "2"), chain)
        m = result["report"]["deterministic_metrics"]["transactions"]
        self.assertEqual((m["unknown"], m["failed"]), (1, 0))
        self.assertFalse(result["coverage"]["events"]["complete"])
        self.assertEqual(len(result["report"]["on_chain_evidence"]["unverified_logs"]), 3)

    def test_snapshot_change_preserves_evidence_and_suppresses_metrics(self):
        chain = Ledger()
        original = chain.call

        def call(method, params):
            answer = original(method, params)
            if method == "eth_call":
                chain.changed = True
            return answer

        chain.call = call
        result = investigate(args(), chain)
        evidence = result["report"]["on_chain_evidence"]
        self.assertFalse(evidence["snapshot_checked"])
        self.assertEqual(len(evidence["transactions"]), 1)
        self.assertIsNone(result["report"]["deterministic_metrics"]["transactions"])

    def test_invalid_arguments_issue_no_rpc(self):
        for flags in [
            ("--days", "7", "--start-block", "1"),
            ("--to-time", "2025-01-01"),
            ("--from-time", iso(STAMP + 2), "--to-time", iso(STAMP + 1)),
        ]:
            chain = Ledger()
            self.assertEqual(
                investigate(args(*flags), chain)["errors"][0]["category"], "invalid_range"
            )
            self.assertEqual(chain.calls, [])

    def test_log_chunk_split_and_explicit_budget(self):
        chain = Ledger()

        def call(method, params):
            a, b = [int(params[0][n], 16) for n in ("fromBlock", "toBlock")]
            if b > a:
                raise QueryError("range too big", "rpc_error")
            return []

        with patch.object(chain, "call", side_effect=call):
            self.assertEqual(RpcAdapter(chain).logs(0, 3, [{}]), ([], []))
            with patch("adapters.MAX_LOG_QUERIES", 2):
                self.assertTrue(RpcAdapter(chain).logs(0, 3, [{}])[1])

    def test_cli_invalid_time_json(self):
        result = subprocess.run(
            [sys.executable, q.__file__, "--address", A, "--days", "7", "--from-time", "bad"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["schema_version"], "2.0")

    @unittest.skipUnless(importlib.util.find_spec("eth_abi"), "optional ABI extra not installed")
    def test_verified_artifact_roundtrip_and_indexed_hash(self):
        from eth_abi import encode
        from eth_utils import keccak

        self.assertEqual("0x" + keccak(text="Approval(address,address,uint256)").hex(), APPROVAL)
        topic = "0x" + keccak(text="Note(string,uint256)").hex()
        chain = Ledger()
        chain.unknown.update(
            topics=[topic, word(300)], data="0x" + encode(["uint256"], [2**255]).hex()
        )
        abi = [
            {
                "address": A,
                "source": "https://example.org/verified",
                "from_block": 1,
                "to_block": 3,
                "abi": [
                    {
                        "type": "event",
                        "name": "Note",
                        "anonymous": False,
                        "inputs": [
                            {"name": "text", "type": "string", "indexed": True},
                            {"name": "n", "type": "uint256", "indexed": False},
                        ],
                    }
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abi.json"
            path.write_text(json.dumps(abi))
            result = investigate(args("--abi-file", str(path)), chain)
            decoded = result["report"]["on_chain_evidence"]["events"][-1]["abi"]
            self.assertEqual(decoded["arguments"][0]["value"], {"indexed_hash": word(300)})
            self.assertEqual(decoded["arguments"][1]["value"], str(2**255))
            decoder = AbiDecoder.load(path)
            event = result["report"]["on_chain_evidence"]["events"][-1]
            self.assertIsNone(decoder.decode({**event, "block": 10}))
            with self.assertRaises(ValueError):
                decoder.decode({**event, "data": event["data"] + "00"})

    def test_filter_violation_and_conflicting_log_are_kept_raw(self):
        ledger = Ledger()
        original = ledger.call

        def call(method, params):
            rows = original(method, params)
            if method == "eth_getLogs" and rows and "topics" in params[0]:
                rows.append({**rows[0], "data": word(1)})
                rows.append({**rows[0], "removed": True})
            return rows

        ledger.call = call
        result = investigate(args(), ledger)
        self.assertFalse(result["coverage"]["events"]["complete"])
        rejected = result["report"]["on_chain_evidence"]["unverified_logs"]
        self.assertIn("conflicting_or_duplicate_log", {row["reason"] for row in rejected})
        self.assertIn("invalid_log", {row["reason"] for row in rejected})
        self.assertFalse(
            any(
                event["log_index"] == 0 for event in result["report"]["on_chain_evidence"]["events"]
            )
        )

    def test_cli_over_real_local_http_with_report(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        ledger = Ledger()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                result = ledger.call(request["method"], request["params"])
                data = json.dumps(
                    {"jsonrpc": "2.0", "id": request["id"], "result": result}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        q.__file__,
                        "--address",
                        A,
                        "--from-time",
                        iso(STAMP + 1),
                        "--to-time",
                        iso(STAMP + 3),
                        "--transport",
                        "http",
                        "--rpc",
                        f"http://127.0.0.1:{server.server_port}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            finally:
                server.shutdown()
                worker.join()
        self.assertEqual(result.returncode, 0, result.stdout)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.json"
            path.write_text(result.stdout)
            verifier = Path(__file__).resolve().parents[1] / "scripts/verify_evidence.py"
            checked = subprocess.check_output([sys.executable, str(verifier), str(path)], text=True)
        self.assertEqual(json.loads(checked)["token_events"], 2)

    def test_https_index_page_validation_and_redaction(self):
        from types import SimpleNamespace

        client = SimpleNamespace(timeout=2, remaining=lambda: 2)
        adapter = EtherscanAdapter(INDEX_SECRET, client)
        with patch("adapters.http.client.HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(
                {"status": "1", "result": [{"hash": H, "blockNumber": "2"}]}
            ).encode()
            self.assertEqual(adapter.discover(A, 1, 3), ([(H, 2, {})], []))
            request = connection.return_value.request.call_args.args
            self.assertEqual(request[0], "GET")
            self.assertIn("chainid=10143", request[1])
            response.read.return_value = json.dumps(
                {"status": "0", "result": INDEX_SECRET}
            ).encode()
            self.assertEqual(
                adapter.discover(A, 1, 3)[1], [{"reason": "index_response", "page": 1}]
            )
            response.read.return_value = (
                b'{"status":"0","message":"No transactions found","result":[]}'
            )
            self.assertEqual(adapter.discover(A, 1, 3), ([], []))
            response.status = 429
            self.assertEqual(
                adapter.discover(A, 1, 3)[1], [{"reason": "index_http_error", "page": 1}]
            )

    def test_bracketing_recent_window_avoids_pruned_genesis(self):
        ledger = Ledger(tip=100_000)
        original = ledger.call

        def call(method, params):
            if method == "eth_getBlockByNumber" and params[0] != "latest":
                if int(params[0], 16) < 99_990:
                    raise QueryError("pruned", "rpc_error")
            return original(method, params)

        ledger.call = call
        result = investigate(
            args("--from-time", iso(STAMP + 99_998), "--to-time", iso(STAMP + 100_000)),
            ledger,
            now=STAMP + 100_001,
        )
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(result["report"]["resolved_range"]["start_block"], 99_998)

    def test_invalid_inputs_do_not_echo_arbitrary_strings(self):
        options = q.parser().parse_args(["--address", INDEX_SECRET])
        self.assertNotIn(INDEX_SECRET, json.dumps(q.scan(options, Ledger())))
        options = args("--from-time", INDEX_SECRET, "--to-time", iso(STAMP))
        self.assertNotIn(INDEX_SECRET, json.dumps(investigate(options, Ledger())))

    def test_legacy_missing_end_block_cannot_pass_snapshot_check(self):
        ledger = Ledger()
        call = ledger.call

        def missing(method, params):
            if method == "eth_getBlockByNumber" and params == ["0x3", True]:
                return None
            return call(method, params)

        ledger.call = missing
        result = q.scan(args("--start-block", "2", "--end-block", "3"), ledger)
        self.assertFalse(result["coverage"]["chain_consistent"])
        self.assertIsNone(result["metrics"])
        self.assertIn("missing_end_block", [error["category"] for error in result["errors"]])

    def test_legacy_duplicate_unrelated_transaction_invalidates_block(self):
        ledger = Ledger()
        ledger.tx.update({"from": TOKEN, "to": B})
        call = ledger.call

        def duplicated(method, params):
            result = call(method, params)
            if method == "eth_getBlockByNumber" and params == ["0x2", True]:
                result["transactions"].append(copy.deepcopy(ledger.tx))
            return result

        ledger.call = duplicated
        result = q.scan(args("--start-block", "2", "--end-block", "2"), ledger)
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["coverage"]["failed_blocks"], [2])
