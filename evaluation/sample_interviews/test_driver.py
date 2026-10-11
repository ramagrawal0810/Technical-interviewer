"""Tests for drive_interviews.py using an in-memory FAKE of the application API and a FAKE simulator.

No network, no OpenAI call, no real database and no money. They check the plan, consent handling, budget guard, resume, stop conditions
and labelling. They do not (and cannot) show how the real model or the real backend behaves; that is what the later supervised run is for.

Run from the repository root:   backend\\venv\\Scripts\\python.exe -m unittest discover -s evaluation/sample_interviews -p "test_driver.py" -v
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
import drive_interviews as di  # noqa: E402
from app.services.injection_detector import detect_prompt_injection  # noqa: E402

PERSONAS = json.load(open(di.PERSONAS_PATH, encoding="utf-8"))


class FakeApi:
    """Just enough of the backend's endpoints, with a cost counter that the usage endpoint reports."""

    def __init__(self, interviews, followups=(0, 0, 0, 0, 0), cost_per_paid_call=0.0, fail=None):
        self.interviews = {i["id"]: {**i, "steps": [], "cv_text": "CV: Python, Docker", "next": list(range(1, 6))} for i in interviews}
        self.followups, self.cost_per_call, self.fail = list(followups), cost_per_paid_call, fail or {}
        self.cost, self.posts, self.fail_counts = {i["id"]: 0.0 for i in interviews}, [], {}

    # -- plumbing
    def get(self, path):
        return self.route("GET", path, None)

    def post(self, path, body=None):
        self.posts.append(path)
        return self.route("POST", path, body or {})

    def paid(self, iid):
        self.cost[iid] += self.cost_per_call

    def maybe_fail(self, key):
        spec = self.fail.get(key)
        if spec:
            n = self.fail_counts[key] = self.fail_counts.get(key, 0) + 1
            if n <= spec["times"]:
                raise di.ApiError(spec["status"], spec["detail"])

    def route(self, method, path, body):
        m = re.match(r"/api/interviews/(\d+)(/.*)?$", path)
        if path == "/health":
            return {"status": "ok"}
        if path == "/api/data-handling":
            return {"notice_version": "2026-10-10"}
        iid = int(m.group(1)) if m else None
        sub = (m.group(2) or "") if m else ""
        iv = self.interviews.get(iid)
        if method == "GET" and sub == "":
            return {k: iv[k] for k in ("id", "candidate_name", "job_title", "mode", "status", "consent_status", "cv_text")}
        if sub == "/usage":
            return {"estimated_cost_usd": self.cost[iid]}
        if sub == "/report":
            return {"overall_score": 7, "ai_recommendation": "Hire", "ai_confidence": "High", "answered_count": len(iv["steps"]), "security": {"status": "clean"}}
        if sub == "/session":
            steps = iv["steps"]
            last = steps[-1] if steps else None
            action = "answer" if last["answer"] is None else "evaluate" if not last["evaluated"] else "next_question"
            if iv["status"] == "completed":
                action = "complete"
            return {"status": iv["status"], "next_action": action, "last_answer_id": last.get("answer_id") if last else None,
                    "pending": ({"interview_question_id": last["iq"], "question": last["question"], "main_question_number": last["main"],
                                 "follow_up_number": last["fu"]} if last and last["answer"] is None else None),
                    "steps": [{"question": s["question"], "answer": s["answer"], "main_question_number": s["main"]} for s in steps]}
        if sub == "/consent":
            iv["consent_status"] = "granted"
            return {"consent_status": "granted"}
        if sub == "/start":
            self.paid(iid)
            iv["status"] = "in_progress"
            self.add_step(iv, 1, 0)
            return {}
        if sub.endswith("/answer"):
            iq = int(sub.split("/")[2])
            step = next(s for s in iv["steps"] if s["iq"] == iq)
            if step["answer"] is None:
                step["answer"], step["answer_id"] = body["answer_text"], 1000 + iq
            return {"id": step["answer_id"]}
        if re.match(r"/api/answers/\d+/evaluate", path):
            self.maybe_fail("evaluate")
            iid = next(i for i, v in self.interviews.items() if any(s.get("answer_id") == int(path.split("/")[3]) for s in v["steps"]))
            step = next(s for s in self.interviews[iid]["steps"] if s["answer_id"] == int(path.split("/")[3]))
            if step["evaluated"]:
                raise di.ApiError(400, "Answer has already been evaluated")
            step["evaluated"] = True
            self.paid(iid)
            return {}
        if sub == "/next-question":
            self.paid(iid)
            last = iv["steps"][-1]
            if last["fu"] < self.followups[last["main"] - 1] and last["fu"] < 3:
                self.add_step(iv, last["main"], last["fu"] + 1)
                return {"is_complete": False}
            if last["main"] < 5:
                self.add_step(iv, last["main"] + 1, 0)
                return {"is_complete": False}
            return {"is_complete": True}
        if sub == "/complete":
            if iv["status"] == "completed":
                raise di.ApiError(400, "Interview has already been completed")
            self.paid(iid)
            iv["status"] = "completed"
            return {}
        raise AssertionError(f"fake API has no route for {method} {path}")

    def add_step(self, iv, main, fu):
        n = len(iv["steps"]) + 1
        iv["steps"].append({"iq": n, "question": f"Q{main}.{fu} for interview {iv['id']}?", "main": main, "fu": fu, "answer": None, "answer_id": None, "evaluated": False})


class FakeSim:
    def __init__(self, cost_per_answer=0.0):
        self.calls, self.cost, self.per = 0, 0.0, cost_per_answer

    def answer(self, persona, role, cv, question, history, main, fu):
        self.calls += 1
        self.cost += self.per
        return f"simulated answer {self.calls} to {question}"


def interview(iid, name, role, mode, consent=None, status="not_started"):
    return {"id": iid, "candidate_name": name, "job_title": role, "mode": mode, "status": status, "consent_status": consent}


def four():
    return [interview(2, "Priya Patel", "Backend Engineer", "cv_driven"), interview(3, "Sofia Garcia", "Backend Engineer", "hybrid"),
            interview(4, "Yuki Tanaka", "Backend Engineer", "cv_driven"), interview(5, "Lisa Anderson", "Frontend Engineer", "hybrid")]


class DriverTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lines = []

    def driver(self, api, sim=None, ids=(2,), consent=True, budget=7.0, max_steps=40):
        args = argparse.Namespace(interviews=list(ids), grant_consent=consent, budget=budget, max_steps=max_steps, execute=True)
        return di.Driver(api, PERSONAS, sim or FakeSim(), args, runs_dir=self.tmp, sleep=lambda s: None, out=self.lines.append)

    def transcript(self, iid):
        return json.load(open(os.path.join(self.tmp, f"interview_{iid}_SIMULATED.json"), encoding="utf-8"))


class DryRunTests(DriverTestCase):
    def test_dry_run_makes_no_post_and_shows_the_plan_and_consent_handling(self):
        api = FakeApi(four())
        rows = self.driver(api, ids=(2, 3, 4, 5)).plan()
        self.assertEqual(api.posts, [])                                              # read-only
        self.assertEqual(os.listdir(self.tmp), [])                                   # no transcript written either
        text = "\n".join(self.lines)
        self.assertIn("DRY RUN", text)
        self.assertEqual(text.count("WOULD record consent"), 4)
        self.assertIn("only with --grant-consent-for-simulated-candidates", text)
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(api.interviews[i]["consent_status"] is None for i in (2, 3, 4, 5)))

    def test_default_command_line_is_a_dry_run(self):
        args = di.parse([])
        self.assertFalse(args.execute)
        self.assertFalse(args.grant_consent)
        self.assertEqual((args.interviews, args.budget), ([2, 3, 4, 5], 7.0))

    def test_the_plan_refuses_a_candidate_that_does_not_match_the_persona(self):
        api = FakeApi([interview(2, "Someone Else", "Backend Engineer", "cv_driven")])
        with self.assertRaises(di.StopRun):
            self.driver(api).plan()


class ConsentTests(DriverTestCase):
    def test_without_the_flag_nothing_is_recorded_started_or_paid(self):
        api = FakeApi(four())
        code = self.driver(api, consent=False).run()
        self.assertEqual(code, 2)
        self.assertEqual(api.posts, [])
        self.assertIsNone(api.interviews[2]["consent_status"])
        self.assertIn("was not given", "\n".join(self.lines))

    def test_with_the_flag_consent_is_recorded_once_and_documented_in_the_transcript(self):
        api = FakeApi(four())
        self.assertEqual(self.driver(api).run(), 0)
        self.assertEqual(api.posts.count("/api/interviews/2/consent"), 1)
        consent = self.transcript(2)["consent"]
        self.assertIn("SIMULATED test candidate", consent["granted_by"])
        self.assertEqual(consent["notice_version"], "2026-10-10")

    def test_declined_consent_is_never_overridden(self):
        api = FakeApi([interview(2, "Priya Patel", "Backend Engineer", "cv_driven", consent="declined")])
        self.assertEqual(self.driver(api).run(), 2)
        self.assertEqual(api.posts, [])

    def test_existing_consent_is_not_recorded_again(self):
        api = FakeApi([interview(2, "Priya Patel", "Backend Engineer", "cv_driven", consent="granted")])
        self.driver(api, consent=False).run()
        self.assertNotIn("/api/interviews/2/consent", api.posts)


class RunTests(DriverTestCase):
    def test_a_full_run_goes_through_the_real_endpoints_in_order_and_labels_everything_simulated(self):
        api = FakeApi(four(), followups=(1, 0, 2, 0, 0))
        sim = FakeSim()
        self.assertEqual(self.driver(api, sim).run(), 0)
        iv = api.interviews[2]
        self.assertEqual(iv["status"], "completed")
        self.assertEqual(len(iv["steps"]), 8)                                         # 5 main + 3 follow-ups
        self.assertTrue(all(s["evaluated"] for s in iv["steps"]))                     # evaluation was never skipped
        self.assertEqual(api.posts[:2], ["/api/interviews/2/consent", "/api/interviews/2/start"])
        self.assertEqual(api.posts[-1], "/api/interviews/2/complete")
        t = self.transcript(2)
        self.assertTrue(t["SIMULATED"])
        self.assertIn("SIMULATED CANDIDATE", t["banner"])
        self.assertEqual(len(t["steps"]), 8)
        self.assertTrue(all(s["SIMULATED_ANSWER"] and "SIMULATED" in s["answered_by"] for s in t["steps"].values()))
        self.assertEqual(t["outcome"]["ai_recommendation"], "Hire")
        md = open(os.path.join(self.tmp, "interview_2_SIMULATED.md"), encoding="utf-8").read()
        self.assertIn("SIMULATED", md.splitlines()[0])
        self.assertEqual(md.count("**Answer (SIMULATED by"), 8)

    def test_a_completed_interview_is_skipped_and_an_abandoned_one_is_not_touched(self):
        api = FakeApi([interview(2, "Priya Patel", "Backend Engineer", "cv_driven", "granted", "completed")])
        self.driver(api).run()
        self.assertEqual(api.posts, [])
        api = FakeApi([interview(2, "Priya Patel", "Backend Engineer", "cv_driven", "granted", "abandoned")])
        self.assertEqual(self.driver(api).run(), 2)
        self.assertEqual(api.posts, [])


class BudgetAndResumeTests(DriverTestCase):
    def test_the_budget_guard_stops_before_the_limit_and_a_rerun_resumes_without_repeating_anything(self):
        api = FakeApi(four(), cost_per_paid_call=0.05)       # a real paid call costs at most ~$0.07, below the guard's $0.12 margin
        sim = FakeSim(cost_per_answer=0.0)
        # 5 mains need start + 5 x (evaluate + next) + complete = 12 paid calls = $0.60 > the $0.50 budget
        self.assertEqual(self.driver(api, sim, budget=0.5).run(), 2)
        self.assertLess(api.cost[2], 0.5)                                              # stopped BEFORE the budget was exceeded
        self.assertIn("budget", "\n".join(self.lines))
        self.assertNotEqual(api.interviews[2]["status"], "completed")
        answers_generated, posts_before = sim.calls, list(api.posts)
        answers_posted = [p for p in posts_before if p.endswith("/answer")]

        self.lines.clear()
        self.assertEqual(self.driver(api, sim, budget=50.0).run(), 0)                  # resume with a bigger budget
        self.assertEqual(api.interviews[2]["status"], "completed")
        all_answers = [p for p in api.posts if p.endswith("/answer")]
        self.assertEqual(len(all_answers), len(set(all_answers)), "an answer was submitted twice")
        self.assertTrue(set(answers_posted) <= set(all_answers))
        self.assertEqual(api.posts.count("/api/interviews/2/start"), 1)               # not started again
        self.assertEqual(api.posts.count("/api/interviews/2/consent"), 1)             # consent not repeated
        self.assertEqual(sim.calls, 5)                                                # exactly one simulated answer per question, none regenerated
        self.assertGreaterEqual(sim.calls, answers_generated)

    def test_simulator_cost_counts_toward_the_budget(self):
        api = FakeApi(four())
        sim = FakeSim(cost_per_answer=1.0)
        self.assertEqual(self.driver(api, sim, budget=2.0).run(), 2)
        self.assertLessEqual(sim.calls, 2)

    def test_a_crash_after_generating_an_answer_reuses_that_exact_answer(self):
        api = FakeApi(four())
        sim = FakeSim()
        d = self.driver(api, sim, max_steps=2)                                          # too few steps: stops mid-interview
        self.assertEqual(d.run(), 2)
        first = json.load(open(os.path.join(self.tmp, "interview_2_SIMULATED.json"), encoding="utf-8"))["steps"]
        self.driver(api, sim).run()
        again = self.transcript(2)["steps"]
        for key, step in first.items():
            self.assertEqual(again[key]["answer"], step["answer"])


class StopConditionTests(DriverTestCase):
    def test_out_of_credit_stops_immediately_without_retrying(self):
        api = FakeApi(four(), fail={"evaluate": {"times": 99, "status": 429, "detail": "You have no credits remaining. Add credits to continue."}})
        self.assertEqual(self.driver(api).run(), 2)
        self.assertEqual(sum(1 for p in api.posts if "/evaluate" in p), 1)             # one attempt, no retry loop
        self.assertIn("out of credit", "\n".join(self.lines))

    def test_three_consecutive_failures_stop_the_run(self):
        api = FakeApi(four(), fail={"evaluate": {"times": 99, "status": 500, "detail": "Evaluation failed: boom"}})
        self.assertEqual(self.driver(api).run(), 2)
        self.assertEqual(sum(1 for p in api.posts if "/evaluate" in p), 3)
        self.assertIn("3 consecutive API failures", "\n".join(self.lines))

    def test_a_transient_failure_is_retried_from_server_state_and_recovers(self):
        api = FakeApi(four(), fail={"evaluate": {"times": 1, "status": 500, "detail": "temporary"}})
        self.assertEqual(self.driver(api).run(), 0)
        self.assertEqual(api.interviews[2]["status"], "completed")

    def test_the_step_cap_stops_a_runaway_interview(self):
        api = FakeApi(four(), followups=(9, 9, 9, 9, 9))
        self.assertEqual(self.driver(api, max_steps=10).run(), 2)
        self.assertIn("step cap", "\n".join(self.lines))


class SimulatorTests(unittest.TestCase):
    def make(self):
        calls = []

        class Resp:
            usage = type("U", (), {"prompt_tokens": 1000, "completion_tokens": 100})()
            choices = [type("C", (), {"message": type("M", (), {"content": "  an answer  "})()})()]

        class Client:
            chat = type("Chat", (), {"completions": type("Comp", (), {"create": staticmethod(lambda **kw: (calls.append(kw), Resp())[1])})()})()

        sim = di.Simulator.__new__(di.Simulator)
        sim.client, sim.config, sim.cost, sim.calls = Client(), PERSONAS["simulator"], 0.0, 0
        return sim, calls

    def test_contradiction_instruction_is_only_added_on_the_configured_question(self):
        sim, calls = self.make()
        lisa = PERSONAS["interviews"]["5"]
        sim.answer(lisa, "Frontend Engineer", "CV", "Q?", [], 2, 0)
        sim.answer(lisa, "Frontend Engineer", "CV", "Q?", [], 3, 0)
        self.assertNotIn("never worked with", calls[0]["messages"][0]["content"])
        self.assertIn("never worked with", calls[1]["messages"][0]["content"])
        self.assertEqual(sim.calls, 2)
        self.assertGreater(sim.cost, 0)
        self.assertEqual(calls[0]["model"], "gpt-4o-mini")

    def test_answer_is_trimmed_and_cost_comes_from_reported_usage(self):
        sim, _ = self.make()
        self.assertEqual(sim.answer(PERSONAS["interviews"]["4"], "Backend Engineer", None, "Q?", [("q", "a")], 1, 0), "an answer")
        self.assertAlmostEqual(sim.cost, 1000 * 0.15 / 1e6 + 100 * 0.60 / 1e6, places=7)


class PersonaFileTests(unittest.TestCase):
    def test_four_personas_cover_a_range_and_match_the_scheduled_interviews(self):
        p = PERSONAS["interviews"]
        self.assertEqual(sorted(p), ["2", "3", "4", "5"])
        self.assertEqual([p[k]["candidate_name"] for k in sorted(p)], ["Priya Patel", "Sofia Garcia", "Yuki Tanaka", "Lisa Anderson"])
        self.assertEqual([p[k]["label"].split(" ")[0] for k in sorted(p)], ["weak", "medium", "strong", "mixed"])
        self.assertEqual(p["5"]["contradiction_on_main_question"], 3)

    def test_no_persona_text_contains_injection_style_instructions(self):
        for k, persona in PERSONAS["interviews"].items():
            for field in ("instructions", "contradiction_instruction"):
                if field in persona:
                    self.assertEqual(detect_prompt_injection(persona[field]).status, "clean", (k, field))
        banned = re.compile(r"ignore (all |any )?(previous|prior)|strong hire|10/10|5/5|system prompt", re.I)
        self.assertIsNone(banned.search(json.dumps(PERSONAS["interviews"])))


if __name__ == "__main__":
    unittest.main()
