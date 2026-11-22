import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

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
        ledger = Ledger()
        call = ledger.call
        ledger.call = (
            lambda method, params: "0x6000" if method == "eth_getCode" else call(method, params)
        )
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
