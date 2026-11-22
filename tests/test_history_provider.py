import os
import sys
import unittest
import urllib.parse
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from adapters import RpcAdapter
from evidence import QueryError
from history import ROUTESCAN_PAGE, RoutescanIndex
from investigation import finalize_coverage, indexed_transactions
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


class FakeRpc:
    def __init__(self, end, receipts=None):
        self.end = end
        self.receipts = receipts or {}
        self.calls = []

    def header(self, block):
        return block_hash(block), block_hash(block - 1), STAMP + block

    def call(self, method, params):
        self.calls.append((method, params))
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
        return {"status": "1" if rows else "0", "message": "OK", "result": rows}


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
        self.assertEqual({row[1] for row in rows}, {1, 6000} | {n for n in range(1, ROUTESCAN_PAGE + 1)})

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
        evidence, gaps = rpc.logs(
            1, 200, [{"address": A}], state={}, save=saved.append
        )
        self.assertEqual(evidence, [])
        self.assertEqual(gaps, [])
        self.assertEqual(len(saved[-1]["answered"]), 2)
        before = len(client.calls)
        evidence, gaps = rpc.logs(
            1, 200, [{"address": A}], state=saved[-1], save=saved.append
        )
        self.assertEqual(evidence, [])
        self.assertEqual(len(client.calls), before)


if __name__ == "__main__":
    unittest.main()
