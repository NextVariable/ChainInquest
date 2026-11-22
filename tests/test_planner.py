import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/onchain-address-inspector/scripts")
)

from evidence import QueryError
from jobs import JobStore
from planner import model_response, select
from test_v3 import ContractQuery, request
from v3 import plan_investigation, plan_objective, run


def response(result):
    return {
        "status": "completed",
        "usage": {"input_tokens": 30, "output_tokens": 10},
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": json.dumps(result)}]}
        ],
    }


class PlannerTests(unittest.TestCase):
    def test_natural_language_plan_calls_validated_tools_without_model(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "", "CHAININQUEST_PLANNER_MODEL": ""}):
            plan = plan_objective(request(), "查看过去 30 天的资金流向")
            self.assertEqual(plan["goal"], "flows")
            self.assertIn("from_time", plan["ranges"]["10143"])
            self.assertFalse(plan["planner"]["model_called"])
            plan = plan_objective(request(), "查看资金流向")
        with tempfile.TemporaryDirectory() as tmp:
            store = JobStore(tmp)
            job = store.create(plan)
            query = ContractQuery()
            result = run(store, job, query_fn=query)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(len(query.calls), 2)
            saved = store.result(job)
            self.assertEqual(saved["goal"], "flows")
            self.assertEqual(saved["conclusions"]["facts"][0]["transactions"], 1)
            proof = saved["conclusions"]["facts"][0]["proof"]
            self.assertTrue(
                set(proof) <= saved["chains"]["10143"]["report"]["evidence_index"].keys()
            )
        with self.assertRaises(QueryError):
            select("x" * 1501)

    def test_structured_model_and_restricted_authority(self):
        env = {
            "OPENAI_API_KEY": "example-key",
            "CHAININQUEST_PLANNER_MODEL": "example-model",
            "CHAININQUEST_MODEL_INPUT_USD_PER_M": "1",
            "CHAININQUEST_MODEL_OUTPUT_USD_PER_M": "2",
        }
        with patch.dict(os.environ, env):
            valid, meta = select(
                "last 7 days of activity",
                invoke=lambda *args: response({"goal": "activity", "window": "7d"}),
            )
            self.assertEqual(valid["window"], "7d")
            self.assertEqual(meta["mode"], "model_validated")
            self.assertEqual(meta["estimated_usd"], "0.00005")
            for data in (
                {"goal": "flows", "window": "30d"},
                {"goal": "flows", "window": "keep", "addresses": []},
                {"goal": "external", "window": "keep"},
            ):
                plan, meta = select("inspect activity", invoke=lambda *args: response(data))
                self.assertEqual(plan, {"goal": "activity", "window": "keep"})
                self.assertEqual(meta["reason"], "model_output_rejected")
            _, meta = select("activity", invoke=lambda *args: {"transport_error": "model_http_429"})
            self.assertEqual(meta["reason"], "model_http_429")

    def test_model_transport_sends_only_bounded_schema_and_objective(self):
        with patch("planner.http.client.HTTPSConnection") as transport:
            endpoint = transport.return_value
            body = endpoint.getresponse.return_value
            body.status = 200
            body.read.return_value = json.dumps(
                response({"goal": "flows", "window": "keep"})
            ).encode()
            result = model_response("inspect flows", "example-model", "example-key")
            self.assertEqual(result["status"], "completed")
            sent = endpoint.request.call_args.args
            self.assertEqual(sent[:2], ("POST", "/v1/responses"))
            parsed = json.loads(sent[2])
            self.assertFalse(parsed["store"])
            self.assertEqual(parsed["input"], "inspect flows")
            self.assertNotIn("tools", parsed)
            body.status = 429
            self.assertEqual(
                model_response("flows", "example-model", "example-key"),
                {"transport_error": "model_http_429"},
            )

    def test_fixed_time_scope_is_reproducible_and_bounded(self):
        now = dt.datetime(2026, 1, 31, tzinfo=dt.timezone.utc)
        start = "2026-01-01T08:00:00+08:00"
        end = "2026-01-31T00:00:00Z"
        req = {**request(), "scope": {"from_time": start, "to_time": end}}
        plan = plan_investigation(req, now)
        self.assertEqual(plan["ranges"]["10143"]["from_time"], "2026-01-01T00:00:00+00:00")
        req["scope"]["from_time"] = "2025-12-31T00:00:00Z"
        with self.assertRaises(QueryError):
            plan_investigation(req, now)
