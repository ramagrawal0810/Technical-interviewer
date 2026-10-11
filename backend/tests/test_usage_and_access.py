"""Interviewer passcode gate, LLM pricing, and LLM usage metrics/dashboard tests.

Isolated temp database; no OpenAI calls.
Run from backend/:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import logging
import unittest
from datetime import datetime, timedelta
from unittest import mock

from app.config import settings
from app.db import models
from app.services.llm_pricing import estimate_cost_usd, get_model_pricing
from app.services.usage_aggregator import compute_usage_metrics, operation_label
from tests.test_scheduling_flow import ApiTestCase

PASSCODE = "pz-9137-xq"
BASE_TIME = datetime(2026, 10, 6, 10, 0, 0)


class PasscodeTests(ApiTestCase):
    def verify(self, passcode):
        return self.client.post("/api/interviewer/verify-passcode", json={"passcode": passcode})

    def test_correct_passcode_is_accepted(self):
        with mock.patch.object(settings, "interviewer_passcode", PASSCODE):
            r = self.verify(PASSCODE)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"authorized": True})

    def test_wrong_passcodes_are_rejected(self):
        with mock.patch.object(settings, "interviewer_passcode", PASSCODE):
            for attempt in ("", "wrong", PASSCODE + "0", " " + PASSCODE, PASSCODE.upper(), PASSCODE[:-1]):
                r = self.verify(attempt)
                self.assertEqual(r.status_code, 401, repr(attempt))
                self.assertEqual(r.json()["detail"], "Invalid passcode.")

    def test_fails_closed_when_not_configured(self):
        with mock.patch.object(settings, "interviewer_passcode", ""):
            for attempt in ("", "0810", "anything"):
                self.assertEqual(self.verify(attempt).status_code, 503, repr(attempt))

    def test_passcode_is_never_echoed_or_logged(self):
        records = []

        class Collector(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Collector(level=logging.DEBUG)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            with mock.patch.object(settings, "interviewer_passcode", PASSCODE):
                ok = self.verify(PASSCODE)
                bad = self.verify("not-the-passcode-attempt")
        finally:
            root.removeHandler(handler)

        for response in (ok, bad):
            self.assertNotIn(PASSCODE, response.text)
        self.assertNotIn("not-the-passcode-attempt", bad.text)
        self.assertFalse([m for m in records if PASSCODE in m or "not-the-passcode-attempt" in m])

    def test_passcode_comes_from_environment_configuration_not_source(self):
        import inspect
        import app.api.interviewer_access as access
        self.assertNotIn("0810", inspect.getsource(access))
        self.assertTrue(hasattr(settings, "interviewer_passcode"))


class PricingTests(unittest.TestCase):
    def test_known_model_cost_uses_actual_token_counts(self):
        # (1000 * 0.15 + 500 * 0.60) / 1e6
        self.assertAlmostEqual(estimate_cost_usd("gpt-4o-mini", 1000, 500), 0.00045)
        self.assertAlmostEqual(estimate_cost_usd("gpt-4", 1000, 1000), 0.09)

    def test_dated_model_names_match_the_most_specific_price(self):
        self.assertEqual(get_model_pricing("gpt-4o-mini-2024-07-18"), get_model_pricing("gpt-4o-mini"))
        self.assertEqual(get_model_pricing("gpt-4o-2024-08-06"), get_model_pricing("gpt-4o"))
        self.assertEqual(get_model_pricing("gpt-4-turbo-preview"), get_model_pricing("gpt-4-turbo"))
        self.assertNotEqual(get_model_pricing("gpt-4o-mini"), get_model_pricing("gpt-4o"))
        self.assertEqual(get_model_pricing("GPT-4O-MINI"), get_model_pricing("gpt-4o-mini"))

    def test_unpriced_or_incomplete_data_gives_none_not_a_guess(self):
        self.assertIsNone(estimate_cost_usd("mystery-model", 100, 50))
        self.assertIsNone(estimate_cost_usd("tts-1", None, None))
        self.assertIsNone(estimate_cost_usd(None, 100, 50))
        self.assertIsNone(estimate_cost_usd("gpt-4o-mini", None, 50))
        self.assertIsNone(estimate_cost_usd("gpt-4o-mini", 100, None))

    def test_zero_tokens_cost_zero(self):
        self.assertEqual(estimate_cost_usd("gpt-4o-mini", 0, 0), 0.0)


class OperationLabelTests(unittest.TestCase):
    def test_labels_are_derived_from_operation(self):
        expected = {
            "evaluation": "Technical Evaluation",
            "adaptive_follow_up": "Adaptive Interview",
            "cv_question_generation": "CV Question Generation",
            "hybrid_question_generation": "Hybrid Question Generation",
            "recommendation_generation": "Recommendation",
            "tts": "Text-to-Speech",
            "stt": "Speech-to-Text",
        }
        for operation, label in expected.items():
            self.assertEqual(operation_label(operation), label)

    def test_unknown_operation_gets_readable_fallback(self):
        self.assertEqual(operation_label("some_new_operation"), "Some New Operation")

    def test_usage_log_has_no_agent_name_column(self):
        self.assertFalse(hasattr(models.LLMUsageLog, "agent_name"))


class UsageTestCase(ApiTestCase):
    def add_log(self, interview_id, operation="evaluation", model="gpt-4o-mini", prompt=1000, completion=500,
                latency=2000, status="success", minutes=0):
        total = None if prompt is None else prompt + (completion or 0)
        with self.Session() as db:
            db.add(models.LLMUsageLog(
                interview_id=interview_id, operation=operation, provider="openai", model=model,
                prompt_tokens=prompt, completion_tokens=completion, total_tokens=total,
                latency_ms=latency, status=status, created_at=BASE_TIME + timedelta(minutes=minutes),
            ))
            db.commit()


class InterviewUsageSummaryTests(UsageTestCase):
    def test_metrics_use_recorded_tokens_and_estimated_cost(self):
        iid = self.seed_completed_interview([(5, None)])
        self.add_log(iid, "evaluation", prompt=1000, completion=500, latency=2000)
        self.add_log(iid, "evaluation", prompt=2000, completion=1000, latency=4000)
        self.add_log(iid, "recommendation_generation", prompt=500, completion=100, latency=3000)
        self.add_log(iid, "evaluation", prompt=None, completion=None, latency=1000, status="error")
        self.add_log(iid, "evaluation", model="mystery-model", prompt=100, completion=50, latency=2000)
        self.add_log(iid, "tts", model="tts-1", prompt=None, completion=None, latency=500)

        r = self.client.get(f"/api/interviews/{iid}/usage")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()

        self.assertEqual(body["total_llm_calls"], 5)                       # audio excluded, as before
        self.assertEqual((body["success_calls"], body["error_calls"]), (4, 1))
        self.assertAlmostEqual(body["success_rate"], 0.8)
        self.assertAlmostEqual(body["average_latency_ms"], 2400.0)         # (2000+4000+3000+1000+2000)/5
        self.assertEqual(body["tokens"], {"prompt_tokens": 3600, "completion_tokens": 1650, "total_tokens": 5250})
        self.assertAlmostEqual(body["estimated_cost_usd"], 0.001485)       # 0.00045 + 0.0009 + 0.000135
        self.assertEqual(body["unpriced_calls"], 1)                        # mystery-model: not given an invented price

        # pre-existing fields are intact
        self.assertEqual(body["interview_id"], iid)
        self.assertEqual(body["by_operation"]["evaluation"]["calls"], 4)
        self.assertEqual(body["audio"]["tts_calls"], 1)

    def test_interview_without_usage_returns_zeroes_not_fake_values(self):
        iid = self.seed_completed_interview([(5, None)])
        body = self.client.get(f"/api/interviews/{iid}/usage").json()
        self.assertEqual(body["total_llm_calls"], 0)
        self.assertIsNone(body["success_rate"])
        self.assertIsNone(body["average_latency_ms"])
        self.assertEqual(body["estimated_cost_usd"], 0.0)
        self.assertEqual(body["tokens"]["total_tokens"], 0)

    def test_unknown_interview_is_404(self):
        self.assertEqual(self.client.get("/api/interviews/9999/usage").status_code, 404)

    def test_usage_is_scoped_to_the_interview(self):
        a = self.seed_completed_interview([(5, None)])
        b = self.seed_completed_interview([(5, None)])
        self.add_log(a, prompt=1000, completion=0)
        self.add_log(b, prompt=7000, completion=0)
        self.assertEqual(self.client.get(f"/api/interviews/{a}/usage").json()["tokens"]["total_tokens"], 1000)

    def test_metrics_ignore_audio_for_cost_and_calls(self):
        with self.Session() as db:
            logs = [
                models.LLMUsageLog(operation="stt", provider="openai", model="whisper-1", latency_ms=300, status="success"),
                models.LLMUsageLog(operation="tts", provider="openai", model="tts-1", latency_ms=300, status="success"),
            ]
            metrics = compute_usage_metrics(logs)
        self.assertEqual(metrics["total_llm_calls"], 0)
        self.assertEqual(metrics["estimated_cost_usd"], 0.0)
        self.assertIsNone(metrics["success_rate"])


class UsageDashboardTests(UsageTestCase):
    def setUp(self):
        super().setUp()
        self.iv_aarav = self.seed_completed_interview([(5, None)])
        self.iv_priya = self.schedule(candidate_id=self.priya_id).json()["id"]
        self.add_log(self.iv_aarav, "evaluation", prompt=1000, completion=500, latency=2000, minutes=1)
        self.add_log(self.iv_aarav, "recommendation_generation", prompt=500, completion=100, latency=3000, minutes=2)
        self.add_log(self.iv_aarav, "evaluation", prompt=None, completion=None, latency=1000, status="error", minutes=3)
        self.add_log(self.iv_priya, "adaptive_follow_up", prompt=2000, completion=1000, latency=4000, minutes=4)
        self.add_log(self.iv_priya, "tts", model="tts-1", prompt=None, completion=None, latency=500, minutes=5)
        self.add_log(None, "stt", model="whisper-1", prompt=None, completion=None, latency=500, minutes=6)

    def get(self, **params):
        r = self.client.get("/api/usage", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_unfiltered_summary_and_newest_first_rows(self):
        data = self.get()
        self.assertEqual(data["total_logs"], 6)
        self.assertEqual(data["summary"]["total_llm_calls"], 4)             # audio excluded from LLM metrics
        self.assertEqual(data["summary"]["success_calls"], 3)
        self.assertEqual(data["summary"]["total_tokens"], 1500 + 600 + 3000)
        # evaluation 1000/500 + recommendation 500/100 + follow-up 2000/1000 on gpt-4o-mini; the errored call has no tokens
        self.assertAlmostEqual(data["summary"]["estimated_cost_usd"], 0.00045 + 0.000135 + 0.0009)
        times = [row["created_at"] for row in data["logs"]]
        self.assertEqual(times, sorted(times, reverse=True))

    def test_rows_show_names_labels_and_prices_not_internal_agent_fields(self):
        rows = {row["operation"] + str(row["interview_id"]): row for row in self.get()["logs"]}
        evaluation = rows[f"evaluation{self.iv_aarav}"]
        self.assertEqual((evaluation["candidate_name"], evaluation["job_title"]), ("Aarav Sharma", "Backend Engineer"))
        self.assertEqual(evaluation["operation_label"], "Technical Evaluation")
        self.assertEqual(evaluation["model"], "gpt-4o-mini")
        self.assertNotIn("agent_name", evaluation)

        follow_up = rows[f"adaptive_follow_up{self.iv_priya}"]
        self.assertEqual((follow_up["candidate_name"], follow_up["operation_label"]), ("Priya Patel", "Adaptive Interview"))
        self.assertEqual((follow_up["prompt_tokens"], follow_up["completion_tokens"], follow_up["total_tokens"]), (2000, 1000, 3000))
        self.assertAlmostEqual(follow_up["estimated_cost_usd"], 0.0009)

        self.assertIsNone(rows[f"tts{self.iv_priya}"]["estimated_cost_usd"])   # audio is never priced
        orphan = rows["stt" + "None"]
        self.assertIsNone(orphan["candidate_name"])
        self.assertEqual(orphan["operation_label"], "Speech-to-Text")

        failed = [r for r in self.get()["logs"] if r["status"] == "error"][0]
        self.assertIsNone(failed["estimated_cost_usd"])

    def test_candidate_filter(self):
        data = self.get(candidate_id=self.priya_id)
        self.assertEqual({r["candidate_name"] for r in data["logs"]}, {"Priya Patel"})
        self.assertEqual(data["total_logs"], 2)
        self.assertEqual(data["summary"]["total_llm_calls"], 1)
        self.assertEqual(self.get(candidate_id=9999)["total_logs"], 0)

    def test_interview_filter(self):
        data = self.get(interview_id=self.iv_aarav)
        self.assertEqual({r["interview_id"] for r in data["logs"]}, {self.iv_aarav})
        self.assertEqual(data["summary"]["total_llm_calls"], 3)
        self.assertEqual(data["summary"]["error_calls"], 1)

    def test_operation_filter_changes_the_summary(self):
        data = self.get(operation="evaluation")
        self.assertEqual({r["operation"] for r in data["logs"]}, {"evaluation"})
        self.assertEqual(data["summary"]["total_llm_calls"], 2)
        self.assertEqual(data["summary"]["total_tokens"], 1500)
        empty = self.get(operation="does_not_exist")
        self.assertEqual((empty["total_logs"], empty["summary"]["total_llm_calls"]), (0, 0))
        self.assertIsNone(empty["summary"]["success_rate"])

    def test_status_filter(self):
        errors = self.get(status="error")
        self.assertEqual(errors["total_logs"], 1)
        self.assertEqual(errors["summary"]["success_rate"], 0.0)
        success = self.get(status="Success")                               # case-insensitive
        self.assertEqual({r["status"] for r in success["logs"]}, {"success"})
        self.assertEqual(self.client.get("/api/usage", params={"status": "bogus"}).status_code, 400)

    def test_filters_combine(self):
        data = self.get(candidate_id=self.aarav_id, operation="evaluation", status="success")
        self.assertEqual(data["total_logs"], 1)
        self.assertEqual(data["summary"]["total_tokens"], 1500)

    def test_limit_caps_rows_but_not_the_summary(self):
        data = self.get(limit=2)
        self.assertEqual(len(data["logs"]), 2)
        self.assertEqual(data["total_logs"], 6)
        self.assertEqual(data["summary"]["total_llm_calls"], 4)
        self.assertEqual(self.client.get("/api/usage", params={"limit": 0}).status_code, 422)

    def test_available_operations_ignore_active_filters(self):
        values = {o["value"]: o["label"] for o in self.get(candidate_id=self.priya_id, status="error")["available_operations"]}
        self.assertEqual(values["evaluation"], "Technical Evaluation")
        self.assertEqual(values["recommendation_generation"], "Recommendation")
        self.assertEqual(values["adaptive_follow_up"], "Adaptive Interview")
        self.assertIn("tts", values)

    def test_empty_database_returns_empty_dashboard(self):
        with self.Session() as db:
            db.query(models.LLMUsageLog).delete()
            db.commit()
        data = self.get()
        self.assertEqual((data["total_logs"], data["logs"], data["available_operations"]), (0, [], []))
        self.assertEqual(data["summary"]["total_llm_calls"], 0)
        self.assertEqual(data["summary"]["estimated_cost_usd"], 0.0)


if __name__ == "__main__":
    unittest.main()
