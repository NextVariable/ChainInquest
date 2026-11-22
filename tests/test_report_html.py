import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))

from report_html import render


def result(complete, transactions, gaps=None, deployments=None):
    return {
        "id": "a" * 32,
        "project": "<script>alert(1)</script>",
        "goal": "activity",
        "complete": complete,
        "conclusions": {
            "facts": [],
            "inferences": [],
            "unknown": [],
            "evidence_gaps": gaps or [],
            "interpretation": {
                "note": "Missing data is not zero activity.",
                "coverage_complete": complete,
            },
        },
        "chains": {
            "1": {
                "coverage": {
                    "complete": complete,
                    "status": "complete" if complete else "partial",
                    "observed": True,
                    "gaps": gaps or [],
                    "transactions": {"complete": complete, "method": "routescan"},
                },
                "report": {
                    "resolved_range": {"start_block": 1, "end_block": 2},
                    "deterministic_metrics": {
                        "transactions": transactions,
                        "events": 0,
                        "status": {"success": transactions, "failed": 0},
                    },
                    "evidence_index": {},
                    "deployments": deployments or [],
                    "data_sources": {"rpc": "configured endpoint (URL omitted)"},
                },
                "analysis": {
                    "observed_counterparties": 0,
                    "daily_utc": {
                        "2026-01-01": {"transactions": None, "Transfer": None, "Approval": None}
                    },
                    "graph": {"facts": [], "curator_attribution": []},
                },
            }
        },
    }


class ReportHtmlTests(unittest.TestCase):
    def test_hostile_text_is_escaped(self):
        output = render(result(True, 1)).decode()
        self.assertNotIn("<script>alert(1)</script>", output)
        self.assertIn("&lt;script&gt;", output)

    def test_zero_with_incomplete_coverage_is_not_activity(self):
        gap = [{"reason": "index_transport"}]
        output = render(result(False, 0, gap)).decode()
        self.assertIn("Unknown (0 observed)", output)
        self.assertIn("index_transport", output)
        self.assertIn("Established facts", output)
        self.assertIn("Inferences", output)
        self.assertIn("Unknowns and evidence gaps", output)

    def test_complete_zero_is_a_proven_zero(self):
        output = render(result(True, 0)).decode()
        self.assertNotIn("Unknown (0 observed)", output)
        self.assertIn("<td>0</td>", output)

    def test_unobserved_days_render_as_unknown(self):
        output = render(result(False, 0)).decode()
        self.assertIn("2026-01-01", output)
        self.assertIn("<td>Unknown</td>", output)

    def test_declared_attribution_is_not_presented_as_verified_ownership(self):
        deployments = [
            {
                "id": "core",
                "contracts": [
                    {
                        "address": "0x" + "1" * 40,
                        "attribution": {
                            "status": "confirmed",
                            "sources": ["https://example.org/notice"],
                        },
                    }
                ],
            }
        ]
        output = render(result(True, 1, deployments=deployments)).decode()
        self.assertIn("Contracts and declared attribution", output)
        self.assertIn("0x" + "1" * 40, output)
        self.assertIn("https://example.org/notice", output)
        self.assertIn("not legal or organizational ownership", output)


if __name__ == "__main__":
    unittest.main()
