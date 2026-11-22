import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from corroboration import corroborate
from evidence import QueryError
from jobs import JobStore
from test_investigation import A, B, Ledger, args, investigate
from v3 import plan_investigation, run


def request():
    return {
        "goal": "activity",
        "scope": {"start_block": 1, "end_block": 3},
        "manifest": {
            "schema_version": "1",
            "project": "Verified fixture",
            "deployments": [
                {
                    "id": "fixture",
                    "chain_id": 10143,
                    "contracts": [
                        {
                            "address": address,
                            "attribution": {
                                "status": "confirmed",
                                "sources": ["https://example.org/verified"],
                            },
                        }
                        for address in (A, B)
                    ],
                }
            ],
        },
    }


@dataclasses.dataclass
class ContractQuery:
    unavailable: str = ""
    calls: list = dataclasses.field(default_factory=list)

    def __call__(self, entry, bounds, seconds):
        self.calls.append(entry["address"])
        if self.unavailable == entry["address"]:
            raise QueryError("fixture unavailable", "timeout")
        config = args(
            "--start-block", str(bounds["start_block"]), "--end-block", str(bounds["end_block"])
        )
        config.address = entry["address"]
        config.verify_contract = True
        config.chain_id = entry["chain_id"]
        ledger = Ledger()
        call = ledger.call

        def reply(method, params):
            if method == "eth_getCode":
                return "0x6000"
            if method == "eth_chainId":
                return hex(entry["chain_id"])
            return call(method, params)

        ledger.call = reply
        return investigate(config, ledger)


class InvestigationJobTests(unittest.TestCase):
    def test_multicontract_flow_and_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            job = store.create(plan_investigation(request()))
            probe = ContractQuery(B)
            state = run(store, job, query_fn=probe)
            self.assertEqual(state["status"], "partial")
            self.assertEqual([e["category"] for e in state["issues"]], ["timeout"])
            probe.unavailable = ""
            result = run(store, job, query_fn=probe)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(probe.calls.count(A), 1)
            self.assertEqual(probe.calls.count(B), 2)
            project = store.result(job)["chains"]["10143"]["report"]
            self.assertEqual(project["deterministic_metrics"]["transactions"], 1)
            self.assertEqual(project["deterministic_metrics"]["events"], 3)
            analysis = store.result(job)["chains"]["10143"]["analysis"]
            facts = analysis["graph"]["facts"]
            self.assertTrue(
                any(edge["relation"] == "successful_top_level_native_value" for edge in facts)
            )
            self.assertEqual(
                sum(edge["relation"] == "erc20_shaped_transfer_log" for edge in facts), 1
            )
            self.assertTrue(all(e["evidence_ref"] in project["evidence_index"] for e in facts))
            self.assertEqual(analysis["graph"]["inferred_relationships"], [])

            self.assertTrue(
                project["deployments"][0]["contracts"][0]["coverage"]["contract_identity"][
                    "bytecode_present"
                ]
            )

    def test_unverified_bytecode_and_invalid_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            plan = plan_investigation(request())
            job = store.create(plan)

            def absent(entry, bounds, seconds):
                config = args("--start-block", "1", "--end-block", "3")
                config.address = entry["address"]
                config.verify_contract = True
                return investigate(config, Ledger())

            self.assertEqual(run(store, job, query_fn=absent)["status"], "partial")
            self.assertIsNone(
                store.result(job)["chains"]["10143"]["report"]["deterministic_metrics"]
            )
        with self.assertRaises(QueryError):
            plan_investigation({**request(), "scope": {"recent_blocks": 100000}})

    def test_chain_specific_metrics_and_evidence(self):
        payload = request()
        payload["scope"] = {"recent_blocks": 3}
        first = payload["manifest"]["deployments"][0]
        for chain_id in (1, 8453):
            payload["manifest"]["deployments"].append({**first, "chain_id": chain_id})
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            job = store.create(plan_investigation(payload))
            state = run(
                store,
                job,
                query_fn=ContractQuery(),
                resolve_fn=lambda *args: {"start_block": 1, "end_block": 3},
            )
            self.assertEqual(state["status"], "complete")
            results = store.result(job)["chains"]
            for cid, host in (
                ("1", "etherscan.io"),
                ("8453", "basescan.org"),
                ("10143", "testnet.monadscan.com"),
            ):
                report = results[cid]["report"]
                self.assertEqual(report["deterministic_metrics"]["transactions"], 1)
                tx = report["on_chain_evidence"]["transactions"][0]
                self.assertTrue(tx["evidence_ref"].startswith(f"tx:{cid}:"))
                self.assertTrue(
                    report["evidence_index"][tx["evidence_ref"]]["explorer_url"].startswith(
                        f"https://{host}/"
                    )
                )
                if cid != "10143":
                    self.assertIn("value_native", tx)
                    self.assertNotIn("value_mon", tx)

    def test_secondary_receipt_disagreement_suppresses_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            job = store.create(plan_investigation(request()))
            ledger = Ledger()
            ledger.close = lambda: None
            with (
                patch.dict(
                    os.environ, {"MONAD_TESTNET_RPC_SECONDARY": "https://secondary.example.org"}
                ),
                patch("corroboration.Client", return_value=ledger),
            ):
                result = run(store, job, query_fn=ContractQuery())
                self.assertEqual(result["status"], "complete")
                chain = store.result(job)["chains"]["10143"]
                self.assertEqual(chain["cross_check"]["status"], "matched_sample")
                self.assertTrue(chain["analysis"]["graph"]["facts"])
                ledger.status = "0x0"
                suspect = store.create(plan_investigation(request()))
                run(store, suspect, query_fn=ContractQuery())
                disputed = store.result(suspect)["chains"]["10143"]
                self.assertEqual(disputed["cross_check"]["status"], "disagreement")
                self.assertFalse(disputed["coverage"]["complete"])
                self.assertIsNone(disputed["analysis"])
                self.assertEqual(
                    corroborate(10143, chain, 1)["reason"], "snapshot_or_budget_unavailable"
                )
