import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from citations import digest
from event_decoder import decimal_amount, log_fields, token_event
from project import investigate_project
from test_investigation import STAMP, TOKEN, A, B, H, Ledger, args, investigate, iso

FIXTURES = Path(__file__).parent / "fixtures"


class RealFixtureTests(unittest.TestCase):
    def test_public_logs_and_offline_integer_precision(self):
        fixture = json.loads((FIXTURES / "monad_testnet_usdc.json").read_text())
        for case in fixture["cases"]:
            event = log_fields(case["raw_log"])
            parsed = token_event(event)
            self.assertEqual(
                (parsed["kind"], parsed["amount_raw"]), (case["kind"], case["amount_raw"])
            )
            self.assertEqual(event["transaction_hash"], case["receipt"]["transactionHash"])
            self.assertEqual(event["block_hash"], case["receipt"]["blockHash"])
            self.assertIsNone(decimal_amount(parsed["amount_raw"], None))
        self.assertEqual(decimal_amount(fixture["cases"][0]["amount_raw"], 6), "0.100000")


class ProjectTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.manifest = {
            "schema_version": "1",
            "project": "Fixture",
            "deployments": [
                {
                    "id": "testnet",
                    "chain_id": 10143,
                    "contracts": [
                        self.contract(A, "a.json"),
                        self.contract(B, "b.json"),
                        self.contract(TOKEN, "must-not-open.json", "unconfirmed"),
                    ],
                }
            ],
        }
        self.address_report("a.json", A)
        self.address_report("b.json", B)

    def contract(self, address, file, status="confirmed"):
        return {
            "address": address,
            "report_file": file,
            "deployment_block": 1,
            "deployed_at": iso(STAMP + 1),
            "attribution": {"status": status, "sources": ["https://example.org/project"]},
        }

    def address_report(self, file, address, ledger=None):
        parsed = args("--start-block", "1", "--end-block", "3")
        parsed.address = address
        result = investigate(parsed, ledger or Ledger())
        self.put(file, result)
        return result

    def put(self, file, value):
        (self.root / file).write_text(json.dumps(value))

    def run_project(self):
        self.put("project.json", self.manifest)
        return investigate_project(self.root / "project.json")

    def test_cli_deduplicates_and_preserves_attribution_boundary(self):
        self.put("project.json", self.manifest)
        package = Path(__file__).parents[1] / "skills/onchain-address-inspector/scripts/query.py"
        run = subprocess.run(
            [sys.executable, str(package), "--project-file", str(self.root / "project.json")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        report = json.loads(run.stdout)["report"]
        (self.root / "result.json").write_text(run.stdout)
        verifier = Path(__file__).parents[1] / "scripts/verify_evidence.py"
        checked = subprocess.check_output(
            [sys.executable, str(verifier), str(self.root / "result.json")], text=True
        )
        self.assertEqual(json.loads(checked)["transactions"], 1)
        evidence = report["on_chain_evidence"]
        metrics = report["deterministic_metrics"]
        self.assertEqual(metrics["transactions"], 1)
        self.assertEqual(metrics["events"], 3)
        self.assertEqual(
            metrics["successful_native_value_wei"], {"in": "0", "out": "0", "internal": str(10**18)}
        )
        tx = evidence["transactions"][0]
        self.assertEqual(tx["hash"], H)
        self.assertEqual(tx["observed_for"], sorted([A, B]))
        self.assertEqual(
            report["evidence_index"][tx["evidence_ref"]]["raw_sha256"],
            digest(tx["raw_transaction"]),
        )
        self.assertEqual(
            report["deployments"][0]["contracts"][-1]["exclusion"], "attribution_not_confirmed"
        )

    def test_mixed_snapshots_and_path_escape_remain_partial(self):
        for fault in ("snapshot", "path"):
            with self.subTest(fault=fault):
                if fault == "snapshot":
                    b = json.loads((self.root / "b.json").read_text())
                    b["report"]["resolved_range"]["end_block_hash"] = "0x" + "ab" * 32
                    self.put("b.json", b)
                else:
                    self.manifest["deployments"][0]["contracts"][1]["report_file"] = (
                        "../escape.json"
                    )
                result = self.run_project()
                self.assertFalse(result["coverage"]["complete"])
                self.assertEqual(result["report"]["deterministic_metrics"]["transactions"], 1)

    def test_receipt_conflict_excluded(self):
        chain = Ledger()
        chain.status = "0x0"
        self.address_report("b.json", B, chain)
        result = self.run_project()
        self.assertFalse(result["coverage"]["complete"])
        self.assertIn(
            "cross_report_conflict", [gap["reason"] for gap in result["coverage"]["gaps"]]
        )
        self.assertEqual(result["report"]["deterministic_metrics"]["transactions"], 0)

    def test_before_deployment_and_confirmed_source_requirement(self):
        entry = self.manifest["deployments"][0]["contracts"][0]
        entry["deployment_block"] = 3
        result = self.run_project()
        self.assertEqual(
            result["report"]["deployments"][0]["contracts"][0]["metrics"]["transactions"], 0
        )
        self.assertEqual(
            result["report"]["deterministic_metrics"]["successful_native_value_wei"]["out"],
            str(10**18),
        )
        entry["attribution"]["sources"] = []
        result = self.run_project()
        self.assertEqual(result["errors"][0]["category"], "invalid_project_manifest")
        self.assertIsNone(result["report"]["deterministic_metrics"])

    def test_input_digest_mismatch_rejects_report(self):
        b = json.loads((self.root / "b.json").read_text())
        b["report"]["on_chain_evidence"]["transactions"][0]["raw_transaction"]["value"] = "0x0"
        self.put("b.json", b)
        result = self.run_project()
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(result["report"]["deployments"][0]["contracts"][1]["included"], False)
