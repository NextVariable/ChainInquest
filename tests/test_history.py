import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)
from adapters import RpcAdapter
from history import BlockscoutIndex, SnapshotIndex
from investigation import investigate
from test_investigation import STAMP, A, B, H, Ledger, args, block_hash, iso


class HistoryTests(unittest.TestCase):
    def test_known_positive_and_empty_thirty_day_operator_snapshot(self):
        start, end = 1, 30 * 86400
        for active in (True, False):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as folder:
                ledger = Ledger(tip=end + 10)
                address = A if active else "0x" + "44" * 20
                options = args("--from-time", iso(STAMP + 1), "--to-time", iso(STAMP + end + 1))
                options.address = address
                data = {
                    "schema_version": 1,
                    "chain_id": 10143,
                    "address": address,
                    "source": "https://example.org/operator-export",
                    "watermark": {
                        "start_block": start,
                        "indexed_through": end,
                        "start_hash": block_hash(start),
                        "end_hash": block_hash(end),
                    },
                    "transactions": [
                        {
                            "block": 2,
                            "hash": H,
                            "claims": {
                                "from": B,
                                "to": A,
                                "blockHash": block_hash(2),
                                "value": str(10**18),
                                "timeStamp": str(STAMP + 2),
                                "txreceipt_status": "1",
                            },
                        }
                    ]
                    if active
                    else [],
                    "logs": ledger.logs if active else [],
                    "log_scope": "emitter_or_erc20_participant_v1",
                }
                file = Path(folder) / f"10143-{address}.json"
                file.write_text(json.dumps(data))
                index = SnapshotIndex(folder, 10143, RpcAdapter(ledger))
                result = investigate(options, ledger, index, now=STAMP + end + 11)
                self.assertTrue(result["coverage"]["complete"], result["coverage"])
                self.assertEqual(
                    result["report"]["deterministic_metrics"]["transactions"][
                        "direct_transactions"
                    ],
                    int(active),
                )
                self.assertEqual(
                    len(result["report"]["on_chain_evidence"]["events"]), 3 if active else 0
                )
                self.assertFalse(any(method == "eth_getLogs" for method, _ in ledger.calls))
                data["watermark"]["end_hash"] = block_hash(end - 1)
                file.write_text(json.dumps(data))
                self.assertEqual(
                    index.discover(address, start, end)[1][0]["reason"], "index_watermark_mismatch"
                )

    def test_blockscout_head_is_not_attestation_and_cursor_must_advance(self):
        ledger = Ledger()
        calls = []

        def fetch(host, path, client):
            calls.append(path)
            if path.endswith("indexing-status"):
                return {"finished_indexing_blocks": True}
            if path.endswith("/blocks"):
                return {"items": [{"height": 3, "hash": block_hash(3)}]}
            tx = {
                "block_number": 2,
                "hash": H,
                "from": {"hash": B},
                "to": {"hash": A},
                "value": "1",
                "timestamp": iso(STAMP + 2),
                "status": "ok",
            }
            return {
                "items": [tx] if "?" not in path else [],
                "next_page_params": {"block_number": 2},
            }

        provider = BlockscoutIndex(1, ledger, RpcAdapter(ledger), fetch)
        rows, gaps = provider.discover(A, 1, 3)
        self.assertEqual(len(rows), 1)
        self.assertTrue(provider.coverage["head_matches_rpc"])
        self.assertFalse(provider.coverage["attested"])
        self.assertEqual(gaps[0]["reason"], "index_cursor")
        self.assertIn("?block_number=2", calls[-1])

    def test_public_transport_cache_retries_and_classifies_rejection(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from evidence import QueryError
        from history import get_json

        client = SimpleNamespace(remaining=lambda: 5, timeout=2)
        with (
            patch("history.http.client.HTTPSConnection") as connection,
            patch("history._NEXT_REQUEST", 0),
            patch("history.time.sleep"),
        ):
            first = SimpleNamespace(status=429, read=lambda limit: b"{}")
            second = SimpleNamespace(status=200, read=lambda limit: b'{"answer":1}')
            endpoint = connection.return_value
            endpoint.getresponse.side_effect = [first, second]
            self.assertEqual(get_json("example.org", "/test-new-key", client), {"answer": 1})
            self.assertEqual(get_json("example.org", "/test-new-key", client), {"answer": 1})
            self.assertEqual(endpoint.request.call_count, 2)
            endpoint.getresponse.side_effect = None
            endpoint.getresponse.return_value = SimpleNamespace(
                status=403, read=lambda limit: b"{}"
            )
            with self.assertRaises(QueryError) as error:
                get_json("example.org", "/forbidden", client)
            self.assertEqual(error.exception.category, "index_refused")

    def test_public_pagination_policy_positive_empty_and_partial(self):
        from copy import deepcopy

        source = Ledger()
        base = {
            "block_number": 2,
            "hash": H,
            "from": {"hash": B},
            "to": {"hash": A},
            "value": "1",
            "timestamp": iso(STAMP + 2),
            "status": "ok",
        }
        for name, entries, cursor, expected, reason in (
            ("positive", [base], None, 1, None),
            ("pending", [{"block_number": None, "status": None}, base], None, 1, None),
            ("empty", [], None, 0, None),
            ("newer", [{**base, "block_number": 3}], None, 0, None),
            ("older", [{**base, "block_number": 0}], None, 0, None),
            ("duplicate", [base, base], None, 1, "index_invalid_page"),
            ("wrong target", [{**base, "to": None}], None, 0, "index_invalid_page"),
            ("bad cursor field", [], {"url": "https://example.org"}, 0, "index_invalid_page"),
            ("empty cursor", [], {"block_number": None}, 0, "index_cursor"),
        ):
            with self.subTest(case=name):

                def fetch(host, path, client):
                    if path.endswith("indexing-status"):
                        return {}
                    if path.endswith("/blocks"):
                        return {"items": [{"height": 3, "hash": block_hash(3)}]}
                    return {"items": deepcopy(entries), "next_page_params": cursor}

                provider = BlockscoutIndex(1, source, RpcAdapter(source), fetch)
                rows, gaps = provider.discover(A, 1, 2)
                self.assertEqual(len(rows), expected)
                self.assertEqual(gaps[0]["reason"] if gaps else None, reason)
                self.assertFalse(provider.coverage["attested"])

    def test_snapshot_missing_wrong_scope_and_duplicate_are_not_attested(self):
        fixture = {
            "schema_version": 1,
            "chain_id": 10143,
            "address": A,
            "source": "https://example.org/source",
            "watermark": {
                "start_block": 1,
                "indexed_through": 3,
                "start_hash": block_hash(1),
                "end_hash": block_hash(3),
            },
            "transactions": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            provider = SnapshotIndex(folder, 10143, RpcAdapter(Ledger()))
            self.assertEqual(provider.discover(A, 1, 3)[1][0]["reason"], "index_snapshot_missing")
            f = Path(folder) / f"10143-{A}.json"
            row = {"block": 2, "hash": H, "claims": {"from": A, "to": B}}
            for change, reason in (
                ({"chain_id": 8453}, "index_invalid_snapshot"),
                ({"transactions": [row, row]}, "index_invalid_snapshot"),
                (
                    {"transactions": [{**row, "claims": {"from": B, "to": B}}]},
                    "index_invalid_snapshot",
                ),
                ({"logs": {}}, "index_invalid_snapshot"),
                (
                    {"watermark": {**fixture["watermark"], "indexed_through": 2}},
                    "index_watermark_gap",
                ),
            ):
                f.write_text(json.dumps({**fixture, **change}))
                self.assertEqual(provider.discover(A, 1, 3)[1][0]["reason"], reason)
                self.assertFalse(provider.coverage["attested"])
                self.assertIsNone(provider.logs)

    def test_curl_transport_caps_and_refusal(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        from evidence import QueryError
        from history import public_response

        with patch("history.subprocess.run") as curl:
            curl.return_value = SimpleNamespace(returncode=0, stdout=b"{}\n403")
            self.assertEqual(public_response("example.org", "/path", 1, "curl"), (403, b"{}"))
            self.assertIn("=https", curl.call_args.args[0])
            self.assertIn("--max-filesize", curl.call_args.args[0])
            curl.return_value = SimpleNamespace(returncode=28, stdout=b"")
            with self.assertRaises(QueryError):
                public_response("example.org", "/path", 1, "curl")
