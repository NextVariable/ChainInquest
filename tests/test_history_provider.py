import json
import os
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from adapters import RpcAdapter
from event_decoder import TRANSFER
from evidence import QueryError
from history import ROUTESCAN_PAGE, RoutescanIndex
from investigation import finalize_coverage, history_saver, indexed_transactions
from test_investigation import A, block_hash

STAMP = 1700000000
OTHER = "0x" + "33" * 20
HASH = "0x" + "ab" * 32


def tx_row(n):
    return {
        "hash": "0x" + f"{n:064x}",
        "blockNumber": str(n),
        "blockHash": block_hash(n),
        "from": A,
        "to": OTHER,
        "value": "1",
        "timeStamp": str(STAMP + n),
        "txreceipt_status": "1",
        "isError": "0",
    }


def raw_tx(n):
    return {
        "hash": "0x" + f"{n:064x}",
        "blockNumber": hex(n),
        "blockHash": block_hash(n),
        "from": A,
        "to": OTHER,
        "value": "0x1",
        "input": "0x",
    }


class FakeRpc:
    def __init__(self, end, receipts=None, salt=0, txs=None):
        self.end = end
        self.receipts = receipts or {}
        self.salt = salt
        self.txs = txs or {}
        self.calls = []

    def header(self, block):
        return block, block_hash(block + self.salt), STAMP + block

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_getTransactionByHash":
            return self.txs.get(params[0])
        if method == "eth_getTransactionReceipt":
            return self.receipts.get(params[0])
        raise QueryError("unexpected", "invalid_response")


class FakeClient:
    def __init__(self):
        self.timeout = 10

    def remaining(self):
        return 100


class Board:
    """Programmable Etherscan-compatible index responses."""

    def __init__(self, pages, window_error=False):
        self.pages = pages
        self.window_error = window_error
        self.calls = []

    def __call__(self, host, path, client, attempts=3):
        params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
        self.calls.append(params)
        if params.get("action") == "getblockreward":
            return {
                "status": "1",
                "result": {
                    "blockNumber": params["blockno"],
                    "timeStamp": str(STAMP + int(params["blockno"])),
                },
            }
        if params.get("module") == "logs":
            return {"status": "0", "message": "No logs found", "result": []}
        first, last = int(params["startblock"]), int(params["endblock"])
        page = int(params["page"])
        if self.window_error and first == 1 and last == 10_000 and page == 2:
            return {"status": "0", "message": "Result window is too large", "result": None}
        rows = self.pages.get((first, last, page), [])
        return {
            "status": "1" if rows else "0",
            "message": "OK" if rows else "No transactions found",
            "result": rows,
        }


class RoutescanTests(unittest.TestCase):
    def index(self, board, end=10_000):
        return RoutescanIndex(1, FakeClient(), FakeRpc(end), fetch=board)

    def test_attested_pagination_and_resume(self):
        board = Board({(1, 10_000, 1): [tx_row(n) for n in range(1, ROUTESCAN_PAGE + 1)]})
        board.pages[(1, 10_000, 2)] = [tx_row(1001)]
        index = self.index(board)
        state = {}
        with patch.dict(os.environ, {"CHAININQUEST_INDEX_MAX_PAGES": "1"}):
            rows, gaps = index.discover(A, 1, 10_000, state=state)
        self.assertEqual(len(rows), ROUTESCAN_PAGE)
        self.assertIn("index_page_budget", [gap["reason"] for gap in gaps])
        self.assertFalse(index.coverage["attested"])
        self.assertEqual(state["current_page"], 2)
        rows, gaps = index.discover(A, 1, 10_000, state=state)
        self.assertEqual(len(rows), ROUTESCAN_PAGE + 1)
        self.assertEqual(gaps, [])
        self.assertTrue(index.coverage["attested"])
        self.assertEqual(index.coverage["watermark"]["block"], 10_000)

    def test_page_window_splits_range(self):
        first_page = [tx_row(n) for n in range(1, ROUTESCAN_PAGE + 1)]
        board = Board(
            {
                (1, 10_000, 1): first_page,
                (1, 5000, 1): [tx_row(1)],
                (5001, 10_000, 1): [tx_row(6000)],
            },
            window_error=True,
        )
        index = self.index(board)
        rows, gaps = index.discover(A, 1, 10_000, state={})
        self.assertEqual(gaps, [])
        self.assertTrue(index.coverage["attested"])
        self.assertEqual(
            {row[1] for row in rows}, {1, 6000} | {n for n in range(1, ROUTESCAN_PAGE + 1)}
        )

    def test_missing_watermark_never_yields_observed_zero(self):
        board = Board({(1, 10_000, 1): []})

        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {"status": "0", "message": "not indexed", "result": None}
            return board(host, path, client, attempts)

        index = RoutescanIndex(1, FakeClient(), FakeRpc(10_000), fetch=fetch)
        txs, transactions = indexed_transactions(
            FakeRpc(10_000), index, A, 1, 10_000, state={}, save=None
        )
        self.assertEqual(txs, [])
        self.assertFalse(transactions["observed"])
        coverage = {
            "complete": False,
            "transactions": transactions,
            "events": {"complete": True},
            "receipts": {"complete": True},
        }
        finalize_coverage(coverage)
        self.assertNotEqual(coverage["status"], "complete")

    def test_index_log_repairs_missing_indices_from_receipt(self):
        log = {
            "address": A,
            "blockNumber": "0x64",
            "blockHash": block_hash(100),
            "transactionHash": HASH,
            "transactionIndex": "0x",
            "logIndex": "0x",
            "topics": ["0x" + "11" * 32, "0x" + "22" * 32],
            "data": "0x",
        }
        receipt = {
            "logs": [
                {
                    **log,
                    "transactionIndex": "0x3",
                    "logIndex": "0x4",
                }
            ]
        }

        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {
                    "status": "1",
                    "result": {"blockNumber": "100", "timeStamp": str(STAMP + 100)},
                }
            if params.get("module") == "logs" and params.get("address"):
                return {"status": "1", "message": "OK", "result": [log]}
            return {"status": "0", "message": "No logs found", "result": []}

        index = RoutescanIndex(1, FakeClient(), FakeRpc(100, {HASH: receipt}), fetch=fetch)
        rows, gaps = index.discover_logs(A, 1, 100, state={})
        self.assertEqual(gaps, [])
        self.assertEqual(rows[0][0]["logIndex"], "0x4")
        self.assertEqual(rows[0][0]["transactionIndex"], "0x3")

    def test_rpc_logs_resume_skips_answered_chunks(self):
        class Client:
            url = "https://example.org"
            timeout = 10
            deadline = None

            def __init__(self):
                self.calls = []

            def remaining(self):
                return 100

            def call(self, method, params):
                self.calls.append((method, params))
                return []

        client = Client()
        rpc = RpcAdapter(client)
        rpc.log_span = 100
        saved = []
        evidence, gaps = rpc.logs(1, 200, [{"address": A}], state={}, save=saved.append)
        self.assertEqual(evidence, [])
        self.assertEqual(gaps, [])
        self.assertEqual(len(saved[-1]["answered"]), 2)
        before = len(client.calls)
        evidence, gaps = rpc.logs(1, 200, [{"address": A}], state=saved[-1], save=saved.append)
        self.assertEqual(evidence, [])
        self.assertEqual(len(client.calls), before)

    def test_empty_result_with_error_message_is_not_proven_zero(self):
        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {
                    "status": "1",
                    "result": {
                        "blockNumber": params["blockno"],
                        "timeStamp": str(STAMP + int(params["blockno"])),
                    },
                }
            return {"status": "0", "message": "Error! Invalid address format", "result": []}

        index = RoutescanIndex(1, FakeClient(), FakeRpc(100), fetch=fetch)
        rows, gaps = index.discover(A, 1, 100, state={})
        self.assertEqual(rows, [])
        self.assertIn("index_response", [gap["reason"] for gap in gaps])
        self.assertFalse(index.coverage["attested"])

    def test_explicit_no_record_message_is_a_legitimate_empty_page(self):
        board = Board({})
        index = self.index(board)
        rows, gaps = index.discover(A, 1, 10_000, state={})
        self.assertEqual(rows, [])
        self.assertEqual(gaps, [])
        self.assertTrue(index.coverage["attested"])
        self.assertTrue(index.coverage["pagination"]["terminal"])
        self.assertEqual(
            index.coverage["attestation"]["kind"], "provider_watermark_and_exhaustive_pagination"
        )

    def test_provider_block_hash_mismatch_blocks_attestation(self):
        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {
                    "status": "1",
                    "result": {
                        "blockNumber": params["blockno"],
                        "timeStamp": str(STAMP + int(params["blockno"])),
                        "blockHash": "0x" + "ff" * 32,
                    },
                }
            return {"status": "0", "message": "No transactions found", "result": []}

        index = RoutescanIndex(1, FakeClient(), FakeRpc(100), fetch=fetch)
        rows, gaps = index.discover(A, 1, 100, state={})
        self.assertEqual(rows, [])
        self.assertIn("index_watermark_mismatch", [gap["reason"] for gap in gaps])
        self.assertFalse(index.coverage["attested"])

    def test_reorg_discards_cached_rows_and_refetches(self):
        board = Board({(1, 10_000, 1): [tx_row(1)]})
        index = self.index(board)
        state = {}
        rows, gaps = index.discover(A, 1, 10_000, state=state)
        self.assertEqual((len(rows), gaps), (1, []))
        self.assertTrue(index.coverage["attested"])
        # Same height and timestamp, different header hash: the chain changed.
        board.pages[(1, 10_000, 1)] = [tx_row(2)]
        fresh = RoutescanIndex(1, FakeClient(), FakeRpc(10_000, salt=7), fetch=board)
        rows, gaps = fresh.discover(A, 1, 10_000, state=state)
        self.assertEqual(gaps, [])
        self.assertEqual([row[0] for row in rows], [tx_row(2)["hash"]])
        self.assertEqual(state["watermark"]["block_hash"], block_hash(10_007))
        self.assertEqual(state["requests"], 1)
        self.assertTrue(fresh.coverage["attested"])

    def test_resume_revalidates_provider_watermark(self):
        board = Board({(1, 10_000, 1): [tx_row(1)]})
        index = self.index(board)
        state = {}
        index.discover(A, 1, 10_000, state=state)
        self.assertTrue(index.coverage["attested"])

        def later(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {"status": "0", "message": "not indexed", "result": None}
            return {"status": "0", "message": "No transactions found", "result": []}

        stale = RoutescanIndex(1, FakeClient(), FakeRpc(10_000), fetch=later)
        rows, gaps = stale.discover(A, 1, 10_000, state=state)
        self.assertIn("index_head_unverified", [gap["reason"] for gap in gaps])
        self.assertFalse(stale.coverage["attested"])

    def test_conflicting_duplicate_transaction_rows_suppress_attestation(self):
        row = tx_row(5)
        board = Board({(1, 10_000, 1): [row, {**row, "value": "2"}]})
        index = self.index(board)
        rows, gaps = index.discover(A, 1, 10_000, state={})
        self.assertEqual(len(rows), 1)
        self.assertIn("index_row_conflict", [gap["reason"] for gap in gaps])
        self.assertFalse(index.coverage["attested"])

    def test_conflicting_duplicate_logs_suppress_attestation(self):
        log = {
            "address": A,
            "blockNumber": "0x64",
            "blockHash": block_hash(100),
            "transactionHash": HASH,
            "transactionIndex": "0x1",
            "logIndex": "0x1",
            "topics": [TRANSFER, "0x" + "22" * 32, "0x" + "33" * 32],
            "data": "0x01",
        }

        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {
                    "status": "1",
                    "result": {
                        "blockNumber": params["blockno"],
                        "timeStamp": str(STAMP + int(params["blockno"])),
                    },
                }
            if params.get("module") == "logs" and params.get("address"):
                return {"status": "1", "message": "OK", "result": [log, {**log, "data": "0x02"}]}
            return {"status": "0", "message": "No logs found", "result": []}

        index = RoutescanIndex(1, FakeClient(), FakeRpc(100), fetch=fetch)
        rows, gaps = index.discover_logs(A, 1, 100, state={})
        self.assertEqual(len(rows), 1)
        self.assertIn("index_log_conflict", [gap["reason"] for gap in gaps])
        self.assertFalse(index.log_coverage["attested"])

    def test_log_budget_truncation_stays_partial_after_resume(self):
        log = {
            "address": A,
            "blockNumber": "0x64",
            "blockHash": block_hash(100),
            "transactionHash": HASH,
            "transactionIndex": "0x1",
            "logIndex": "0x1",
            "topics": [TRANSFER, "0x" + "22" * 32, "0x" + "33" * 32],
            "data": "0x01",
        }

        def fetch(host, path, client, attempts=3):
            params = dict(urllib.parse.parse_qsl(path.split("?", 1)[1]))
            if params.get("action") == "getblockreward":
                return {
                    "status": "1",
                    "result": {
                        "blockNumber": params["blockno"],
                        "timeStamp": str(STAMP + int(params["blockno"])),
                    },
                }
            if params.get("module") == "logs" and params.get("address"):
                return {"status": "1", "message": "OK", "result": [log]}
            return {"status": "0", "message": "No logs found", "result": []}

        index = RoutescanIndex(1, FakeClient(), FakeRpc(100), fetch=fetch)
        state = {}
        with patch("history.MAX_INDEX_LOGS", 1):
            rows, gaps = index.discover_logs(A, 1, 100, state=state)
        self.assertEqual(len(rows), 1)
        self.assertIn("log_budget", [gap["reason"] for gap in gaps])
        self.assertFalse(index.log_coverage["attested"])
        rows, gaps = index.discover_logs(A, 1, 100, state=state)
        self.assertIn("log_budget", [gap["reason"] for gap in gaps])
        self.assertFalse(index.log_coverage["attested"])

    def test_capped_resume_matches_single_run_metrics(self):
        rows = [tx_row(n) for n in range(1, ROUTESCAN_PAGE + 1)] + [tx_row(1001)]
        pages = {
            (1, 10_000, 1): rows[:ROUTESCAN_PAGE],
            (1, 10_000, 2): rows[ROUTESCAN_PAGE:],
        }
        txs = {raw_tx(n)["hash"]: raw_tx(n) for n in [*range(1, ROUTESCAN_PAGE + 1), 1001]}

        def run(state, cap):
            rpc = FakeRpc(10_000, txs=txs)
            index = RoutescanIndex(1, FakeClient(), rpc, fetch=Board(pages))
            with patch.dict(os.environ, {"CHAININQUEST_INDEX_MAX_PAGES": str(cap)}):
                found, coverage = indexed_transactions(
                    rpc, index, A, 1, 10_000, state=state, save=None
                )
            return found, coverage, index, state

        partial, partial_coverage, _, state = run({}, 1)
        self.assertEqual(len(partial), ROUTESCAN_PAGE)
        self.assertFalse(partial_coverage["complete"])
        self.assertEqual(partial_coverage["index_coverage"]["pagination"]["requests_this_run"], 1)
        resumed, resumed_coverage, index, state = run(state, 1)
        self.assertEqual(len(resumed), ROUTESCAN_PAGE + 1)
        self.assertTrue(resumed_coverage["complete"])
        self.assertTrue(resumed_coverage["observed"])
        self.assertTrue(index.coverage["attested"])
        self.assertEqual(state["requests"], 2)
        one_shot, one_shot_coverage, _, _ = run({}, 200)
        self.assertTrue(one_shot_coverage["complete"])
        self.assertEqual(
            sorted(tx["hash"] for tx in resumed),
            sorted(tx["hash"] for tx in one_shot),
        )


class StateFileTests(unittest.TestCase):
    def test_atomic_save_sets_private_permissions_and_keeps_last_good(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "history-state.json"
            document, save = history_saver(str(path))
            self.assertIsNone(document)
            save({"schema_version": 1, "rows": []})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"schema_version": 1, "rows": []}
            )
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with patch("investigation.os.replace", side_effect=OSError):
                save({"schema_version": 1, "rows": [["changed"]]})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")), {"schema_version": 1, "rows": []}
            )
            self.assertFalse((Path(root) / "history-state.json.tmp").exists())

    def test_save_fsyncs_and_reports_failure_without_raising(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "history-state.json"
            _, save = history_saver(str(path))
            fsyncs = []
            with patch("investigation.os.fsync", side_effect=fsyncs.append):
                save({"schema_version": 1})
            self.assertEqual(len(fsyncs), 1)
            self.assertEqual(save.failures, 0)
            with patch("investigation.os.replace", side_effect=OSError("disk full")):
                save({"schema_version": 1, "rows": [["changed"]]})
            self.assertEqual(save.failures, 1)
            self.assertEqual(save.last_error, "OSError")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"schema_version": 1})
            self.assertFalse((Path(root) / "history-state.json.tmp").exists())
            save({"schema_version": 1, "rows": [["later"]]})
            self.assertEqual(save.failures, 1)

    def test_corrupt_state_file_recovers_and_stays_private(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "history-state.json"
            path.write_text("{not json", encoding="utf-8")
            document, save = history_saver(str(path))
            self.assertIsNone(document)
            save({"schema_version": 1})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"schema_version": 1})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_invalid_state_is_reset_and_credentials_are_not_persisted(self):
        index = RoutescanIndex(1, FakeClient(), FakeRpc(10_000), fetch=Board({}))
        state = {
            "schema_version": 1,
            "provider": "another_provider",
            "chain_id": 1,
            "address": A,
            "start_block": 1,
            "end_block": 10_000,
            "queue": [[1, 10_000]],
            "rows": [],
            "gaps": [],
            "requests": 0,
            "done": False,
            "watermark": None,
            "api_key": "SECRET",
        }
        saved = []
        index.discover(A, 1, 10_000, state=state, save=saved.append)
        self.assertNotIn("api_key", state)
        self.assertEqual(state["provider"], index.name)
        self.assertTrue(saved)
        self.assertNotIn("SECRET", json.dumps(saved))
        bad_queue = {
            "schema_version": 1,
            "provider": index.name,
            "chain_id": 1,
            "address": A,
            "start_block": 1,
            "end_block": 10_000,
            "queue": ["bogus"],
            "rows": [],
            "gaps": [],
            "requests": 0,
            "done": False,
            "watermark": None,
        }
        index.discover(A, 1, 10_000, state=bad_queue, save=None)
        self.assertTrue(bad_queue["done"])
        self.assertEqual(bad_queue["queue"], [])
        self.assertTrue(index.coverage["attested"])


if __name__ == "__main__":
    unittest.main()
