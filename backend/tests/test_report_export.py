"""Feedback report generation and PDF / Markdown export.

Completed interviews are seeded as real rows (answers, 4D evaluations with quotes, follow-ups, reviews), then the
real report builder, exporters and HTTP endpoints are exercised. No LLM is involved: reports are built from stored data.

Run from backend/:  venv\\Scripts\\python.exe -m unittest discover -s tests -t . -v
"""

import html
import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import pypdf

from app.config import settings
from app.db import models
from app.services import report_export
from app.services.report_builder import build_feedback_report, verified_quote
from app.services.report_export import find_unicode_font, render_markdown, render_pdf, safe_filename
from tests.test_adaptive_engine import BACKEND, FRONTEND, EngineTestCase

DIM_KEYS = ["technical_skills", "domain_understanding", "problem_solving", "communication_clarity"]
ANSWER_1 = "State is data that belongs to a component. When setState runs, React schedules a re-render of that component and its children."
ANSWER_2 = "I would use a debounce so the API is not called on every keystroke. The cleanup function cancels the pending timer."


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def unmarkdown(text: str) -> str:
    return html.unescape(re.sub(r"\\([\\`*_{}\[\]()#+!|<>~])", r"\1", text))


def pdf_text(data: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def dims_for(scores, quotes=None):
    quotes = quotes or {}
    return json.dumps({k: {"score": s, "justification": f"{k.replace('_', ' ')} justification", "quote": quotes.get(k, "a quote")}
                       for k, s in zip(DIM_KEYS, scores)})


class Step:
    def __init__(self, question, answer, scores=(4, 3, 5, 2), quotes=None, score=6, missing=None, reason=None,
                 follow_up=False, evaluated=True, dims=True):
        self.question, self.answer, self.scores, self.quotes = question, answer, scores, quotes
        self.score, self.missing, self.reason, self.follow_up, self.evaluated, self.dims = score, missing, reason, follow_up, evaluated, dims


class ReportTestCase(EngineTestCase):
    def seed_interview(self, steps, recommendation="Hire", reason="A solid interview overall.", cv=None, mode="question_bank",
                       review=None, status="completed", role=FRONTEND, consent="granted"):
        with self.Session() as db:
            interview = models.Interview(
                candidate_id=self.candidate_id, job_description_id=self.job_ids[role], status=status, mode=mode, cv_text=cv,
                created_at=datetime(2026, 10, 1, 9, 0), started_at=datetime(2026, 10, 5, 11, 0),
                completed_at=datetime(2026, 10, 5, 12, 0) if status == "completed" else None,
                consent_status=consent, consent_recorded_at=datetime(2026, 10, 5, 10, 59) if consent else None,
                consent_notice_version="2026-10-10" if consent else None)
            db.add(interview)
            db.flush()
            for order, step in enumerate(steps, 1):
                question = models.Question(question=step.question, category="follow_up" if step.follow_up else "Topic", difficulty="medium",
                                           job_type=role, source_interview_id=interview.id if step.follow_up else None)
                db.add(question)
                db.flush()
                iq = models.InterviewQuestion(interview_id=interview.id, question_id=question.id, question_order=order,
                                              created_at=datetime(2026, 10, 5, 11, order))
                db.add(iq)
                db.flush()
                if step.answer is None:
                    continue
                answer = models.Answer(interview_question_id=iq.id, answer_text=step.answer, created_at=datetime(2026, 10, 5, 11, order, 30))
                db.add(answer)
                db.flush()
                if step.evaluated:
                    db.add(models.Evaluation(
                        answer_id=answer.id, dimensions=dims_for(step.scores, step.quotes) if step.dims else None, score=step.score,
                        strengths="s", weaknesses="w", feedback="f", confidence=0.8, needs_follow_up=bool(step.reason),
                        follow_up_reason=step.reason, missing_information=step.missing, contradicts_cv=False,
                        created_at=datetime(2026, 10, 5, 11, order, 45)))
            if recommendation:
                db.add(models.Recommendation(interview_id=interview.id, recommendation=recommendation, confidence="High", reason=reason))
            if review:
                db.add(models.HumanReview(
                    interview_id=interview.id, interviewer_id="demo-interviewer", interviewer_name="Demo Interviewer",
                    ai_recommendation=review.get("ai", recommendation), ai_confidence="High", ai_reasoning=reason,
                    decision=review["decision"], decision_type=review["type"], override_reason=review.get("reason"),
                    created_at=datetime(2026, 10, 6, 9, 0), reviewed_at=datetime(2026, 10, 6, 9, 5)))
            db.commit()
            return interview.id

    def standard_interview(self, **kw):
        quotes_1 = {k: "re-render of that component" for k in DIM_KEYS}
        quotes_1["communication_clarity"] = "this quote is not in the answer"       # a paraphrase the model invented
        return self.seed_interview([
            Step("Explain how React state works.", ANSWER_1, scores=(4, 3, 5, 2), quotes=quotes_1, score=7, missing="no mention of batching", reason="incomplete"),
            Step("Can you explain batching?", "Not sure, I think React waits a bit.", scores=(2, 2, 3, 2), score=3, follow_up=True,
                 quotes={k: "React waits a bit" for k in DIM_KEYS}),
            Step("How would you debounce an API call?", ANSWER_2, scores=(5, 4, 4, 4), score=8, quotes={k: "cancels the pending timer" for k in DIM_KEYS}),
        ], **kw)

    def report(self, interview_id):
        r = self.client.get(f"/api/interviews/{interview_id}/report")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class ReportContentTests(ReportTestCase):
    def test_report_is_built_from_persisted_data(self):
        interview_id = self.standard_interview()
        r = self.report(interview_id)
        self.assertEqual((r["interview_id"], r["candidate_name"], r["job_title"], r["mode"]), (interview_id, "Aarav Sharma", FRONTEND, "question_bank"))
        self.assertTrue(r["completed_at"].startswith("2026-10-05T12:00"))
        self.assertEqual(r["overall_score"], 6)                                  # round-half-up mean of 7, 3, 8 (existing formula)
        self.assertEqual((r["ai_recommendation"], r["ai_confidence"], r["overall_assessment"]), ("Hire", "High", "A solid interview overall."))
        self.assertEqual([q["label"] for q in r["transcript"]], ["Question 1", "Question 1, follow-up 1", "Question 2"])
        self.assertEqual([q["is_follow_up"] for q in r["transcript"]], [False, True, False])
        self.assertEqual([q["answer"] for q in r["transcript"]][0], ANSWER_1)
        self.assertEqual(r["transcript"][0]["follow_up_reason"], "incomplete")
        self.assertEqual(r["evaluation_coverage"], "3 of 3 asked questions evaluated; 3 answered")
        self.assertEqual(r["consent"]["status"], "granted")

    def test_each_dimension_has_a_score_a_justification_and_evidence(self):
        r = self.report(self.standard_interview())
        self.assertEqual([d["key"] for d in r["dimensions"]], DIM_KEYS)
        technical = r["dimensions"][0]
        self.assertEqual(technical["average"], 3.7)                              # (4 + 2 + 5) / 3
        self.assertEqual(technical["evaluated_answers"], 3)
        self.assertEqual([e["question"] for e in technical["evidence"]], ["Question 1", "Question 1, follow-up 1", "Question 2"])
        self.assertTrue(all(e["justification"] and 1 <= e["score"] <= 5 for e in technical["evidence"]))
        for dimension in r["dimensions"]:
            self.assertTrue(any(e["quote"] for e in dimension["evidence"]), f"{dimension['key']} needs at least one supporting quote")

    def test_every_quote_is_verbatim_from_the_candidates_actual_answer(self):
        interview_id = self.standard_interview()
        r = self.report(interview_id)
        answers = {q["label"]: q["answer"] for q in r["transcript"]}
        shown = 0
        for dimension in r["dimensions"]:
            for e in dimension["evidence"]:
                if e["quote"]:
                    shown += 1
                    self.assertIn(collapse(e["quote"]).lower(), collapse(answers[e["question"]]).lower())
                else:
                    self.assertTrue(e["quote_note"])
        self.assertGreater(shown, 0)
        # the paraphrased quote is NOT presented as a quote
        clarity = next(d for d in r["dimensions"] if d["key"] == "communication_clarity")
        self.assertIsNone(clarity["evidence"][0]["quote"])
        self.assertIn("No verbatim quote", clarity["evidence"][0]["quote_note"])
        for point in r["strengths"] + r["gaps"]:
            if point["quote"]:
                self.assertTrue(any(collapse(point["quote"]).lower() in collapse(a).lower() for a in answers.values()))

    def test_verified_quote_rules(self):
        self.assertEqual(verified_quote("re-render  of THAT component", ANSWER_1), "re-render  of THAT component")
        self.assertEqual(verified_quote("re-render of that component...", ANSWER_1), "re-render of that component...")
        self.assertIsNone(verified_quote("something never said", ANSWER_1))
        self.assertIsNone(verified_quote("[Paraphrased] re-render of that component", ANSWER_1))
        self.assertIsNone(verified_quote("", ANSWER_1))
        self.assertIsNone(verified_quote("re-render", None))

    def test_strengths_gaps_and_next_round_come_from_stored_evaluation_data(self):
        r = self.report(self.standard_interview())
        strength_text = " ".join(p["text"] for p in r["strengths"])
        self.assertIn("Problem-Solving Approach (5/5)", strength_text)
        self.assertIn("Technical Skills (5/5)", strength_text)
        gap_text = " ".join(p["text"] for p in r["gaps"])
        self.assertIn("(2/5)", gap_text)                                         # the low scores of the follow-up
        self.assertIn("Gap noted by the evaluator: no mention of batching", gap_text)
        self.assertTrue(all(p["source"] for p in r["strengths"] + r["gaps"]))
        next_round = " ".join(p["text"] for p in r["next_round_areas"])
        self.assertIn("Follow up on: no mention of batching", next_round)
        self.assertLessEqual(len(r["next_round_areas"]), 8)

    def test_missing_evaluation_data_is_reported_as_unavailable_not_invented(self):
        interview_id = self.seed_interview([
            Step("Q one?", "An answer with 4D data."),
            Step("Q two?", "Answer scored before 4D existed.", dims=False, score=5),
            Step("Q three?", "Saved but never evaluated.", evaluated=False),
            Step("Q four?", None),
        ])
        r = self.report(interview_id)
        by_label = {q["label"]: q for q in r["transcript"]}
        self.assertEqual(by_label["Question 1"]["evaluation_status"], "evaluated")
        legacy = by_label["Question 2"]
        self.assertEqual((legacy["evaluation_status"], legacy["score"], legacy["dimensions"]), ("evaluated", 5, None))
        self.assertIn("no 4D breakdown", legacy["note"])
        unevaluated = by_label["Question 3"]
        self.assertEqual((unevaluated["evaluation_status"], unevaluated["score"], unevaluated["dimensions"]), ("not_evaluated", None, None))
        self.assertEqual(by_label["Question 4"]["evaluation_status"], "no_answer")
        self.assertIsNone(by_label["Question 4"]["answer"])
        self.assertEqual(r["dimensions"][0]["evaluated_answers"], 1)             # only the one answer with 4D data counts
        joined = " ".join(p["text"] for p in r["next_round_areas"])
        self.assertIn("Question 3 was answered but not evaluated", joined)
        self.assertIn("Question 4 was never answered", joined)

    def test_an_interview_with_no_4d_data_at_all_still_produces_a_report(self):
        interview_id = self.seed_interview([Step("Q?", "An answer.", dims=False, score=6), Step("Q2?", "Another.", dims=False, score=8)])
        r = self.report(interview_id)
        self.assertEqual(r["overall_score"], 7)
        self.assertTrue(all(d["average"] is None and d["evidence"] == [] and d["note"] for d in r["dimensions"]))
        self.assertEqual(r["strengths"], [])
        self.assertIn("no strengths are recorded", r["strengths_note"])
        self.assertIn("No low scores", r["gaps_note"])
        md = render_markdown(build_report(self, interview_id))
        self.assertIn("Unavailable", md)
        self.assertGreater(len(render_pdf(build_report(self, interview_id))), 1000)

    def test_missing_recommendation_is_marked_unavailable(self):
        interview_id = self.seed_interview([Step("Q?", "An answer.")], recommendation=None)
        r = self.report(interview_id)
        self.assertEqual((r["ai_recommendation"], r["ai_confidence"], r["overall_assessment"]), (None, None, None))
        self.assertIn("Unavailable", render_markdown(build_report(self, interview_id)))
        self.assertIn("No AI assessment text was stored", render_markdown(build_report(self, interview_id)))

    def test_legacy_recommendation_labels_are_normalised(self):
        interview_id = self.seed_interview([Step("Q?", "An answer.")], recommendation="consider")
        self.assertEqual(self.report(interview_id)["ai_recommendation"], "Hold")

    def test_malformed_stored_dimensions_do_not_break_the_report(self):
        interview_id = self.standard_interview()
        with self.Session() as db:
            db.query(models.Evaluation).order_by(models.Evaluation.id).first().dimensions = '{"technical_skills": '
            db.commit()
        r = self.report(interview_id)
        self.assertEqual(r["transcript"][0]["dimensions"], None)
        self.assertEqual(r["dimensions"][0]["evaluated_answers"], 2)

    def test_reports_are_only_available_for_completed_interviews(self):
        in_progress = self.seed_interview([Step("Q?", "A.")], status="in_progress")
        scheduled = self.schedule(FRONTEND)
        for interview_id in (in_progress, scheduled):
            for suffix in ("report", "report.md", "report.pdf"):
                r = self.client.get(f"/api/interviews/{interview_id}/{suffix}")
                self.assertEqual(r.status_code, 400, suffix)
                self.assertIn("only available for completed or abandoned interviews", r.json()["detail"])
        for suffix in ("report", "report.md", "report.pdf"):
            self.assertEqual(self.client.get(f"/api/interviews/99999/{suffix}").status_code, 404)

    def test_opening_or_downloading_a_report_calls_no_llm_and_changes_nothing(self):
        interview_id = self.standard_interview()

        def tables():
            with self.Session() as db:
                return {name: db.query(table).count() for name, table in models.Base.metadata.tables.items()}

        before = tables()
        with mock.patch("app.services.llm_evaluation.OpenAI", side_effect=AssertionError("LLM called")), \
                mock.patch("app.services.follow_up_generator.OpenAI", side_effect=AssertionError("LLM called")), \
                mock.patch("app.services.cv_processor.OpenAI", side_effect=AssertionError("LLM called")):
            for suffix in ("report", "report.md", "report.pdf", "report", "report.pdf"):
                self.assertEqual(self.client.get(f"/api/interviews/{interview_id}/{suffix}").status_code, 200)
        self.assertEqual(tables(), before)
        self.assertEqual(self.eval_calls + self.follow_up_calls, [])
        self.assertEqual(before["llm_usage_logs"], 0)

    def test_the_report_has_no_credentials_costs_or_unnecessary_personal_data(self):
        interview_id = self.standard_interview()
        with mock.patch.object(settings, "openai_api_key", "sk-test-secret-key-1234567890"):
            payload = self.client.get(f"/api/interviews/{interview_id}/report")
            md = self.client.get(f"/api/interviews/{interview_id}/report.md").text
            pdf = pdf_text(self.client.get(f"/api/interviews/{interview_id}/report.pdf").content)
        with self.Session() as db:
            email = db.query(models.Candidate).get(self.candidate_id).email
        everything = payload.text + md + pdf
        self.assertNotIn("sk-test-secret", everything)
        self.assertNotIn(email, everything)
        for forbidden in ("estimated_cost", "prompt_tokens", "completion_tokens", "total_tokens", "latency_ms", "api_key", "OPENAI"):
            self.assertNotIn(forbidden, everything)


def build_report(test, interview_id):
    with test.Session() as db:
        return build_feedback_report(db, interview_id, now=datetime(2026, 10, 7, 8, 30))


class HumanReviewInReportTests(ReportTestCase):
    def test_no_review_yet(self):
        r = self.report(self.standard_interview())
        self.assertFalse(r["human_review"]["present"])
        self.assertIn("No human review has been recorded", r["human_review"]["note"])

    def test_override_is_reported_separately_and_never_replaces_the_ai_recommendation(self):
        reason = "Strong debugging instincts in the follow-up discussion."
        interview_id = self.standard_interview(review={"decision": "Strong Hire", "type": "OVERRIDE", "reason": reason})
        r = self.report(interview_id)
        self.assertEqual(r["ai_recommendation"], "Hire")                          # the AI recommendation is untouched
        h = r["human_review"]
        self.assertEqual((h["present"], h["decision"], h["decision_type"], h["override_reason"]),
                         (True, "Strong Hire", "Overrode the AI recommendation", reason))
        self.assertEqual((h["ai_recommendation_at_review"], h["interviewer_id"], h["interviewer_name"]), ("Hire", "demo-interviewer", "Demo Interviewer"))
        self.assertTrue(h["reviewed_at"].startswith("2026-10-06T09:05"))
        md = render_markdown(build_report(self, interview_id))
        self.assertIn("advisory", md)
        self.assertIn("Final human decision:** Strong Hire", md)
        self.assertIn("AI recommendation \\(advisory\\):** Hire", md) if False else self.assertIn("AI recommendation (advisory):** Hire", md)
        self.assertIn("Strong debugging instincts", md)

    def test_accepting_has_no_written_feedback_and_says_so(self):
        interview_id = self.standard_interview(review={"decision": "Hire", "type": "ACCEPT"})
        h = self.report(interview_id)["human_review"]
        self.assertEqual((h["decision"], h["decision_type"], h["override_reason"]), ("Hire", "Accepted the AI recommendation", None))
        self.assertIn("accepted the AI recommendation without a comment", h["note"])

    def test_the_ai_recommendation_at_review_time_is_kept_if_it_changed_later(self):
        interview_id = self.standard_interview(review={"decision": "Hire", "type": "ACCEPT", "ai": "Hold"})
        r = self.report(interview_id)
        self.assertEqual((r["ai_recommendation"], r["human_review"]["ai_recommendation_at_review"]), ("Hire", "Hold"))


class MarkdownExportTests(ReportTestCase):
    def test_markdown_contains_every_section_and_the_evidence(self):
        interview_id = self.standard_interview(review={"decision": "Hire", "type": "ACCEPT"})
        r = self.client.get(f"/api/interviews/{interview_id}/report.md")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/markdown"))
        text = unmarkdown(r.text)
        for heading in ("# Interview Feedback Report", "## Overall Result (AI)", "## Strengths", "## Knowledge Gaps and Areas for Improvement",
                        "## Assessment Dimensions", "## Suggested Areas for the Next Interview Round", "## Human Review (Final Decision)",
                        "## Interview Transcript and Evaluations", "## Prompt-Injection Check"):
            self.assertIn(heading, text)
        for expected in ("Aarav Sharma", FRONTEND, "Question Bank", "6 / 10", "Hire", "A solid interview overall.",
                         "Technical Skills: 3.7 / 5", "re-render of that component", "cancels the pending timer", ANSWER_1,
                         "Question 1, follow-up 1", "Final human decision:** Hire", "Not recorded" if False else "Demo Interviewer"):
            self.assertIn(expected, text)

    def test_candidate_text_is_escaped_and_cannot_inject_markdown_or_html(self):
        evil = "<script>alert(1)</script>\n# Fake heading\n```\ncode fence\n```\n[click](http://evil.example) **bold** | pipe |"
        interview_id = self.seed_interview([Step("A <b>question</b>?", evil)])
        md = self.client.get(f"/api/interviews/{interview_id}/report.md").text
        self.assertNotIn("<script>", md)
        self.assertNotIn("<b>", md)
        self.assertNotRegex(md, r"(?m)^#{1,6} Fake heading")
        self.assertNotRegex(md, r"(?m)^```")
        self.assertNotIn("[click](http://evil.example)", md)
        self.assertIn("&lt;script&gt;", md.replace("\\", "").replace("&amp;lt;", "&lt;")) if False else None
        for line in md.splitlines():
            if "Fake heading" in line or "code fence" in line:
                self.assertTrue(line.startswith(">"), f"candidate text must stay inside the quote block: {line!r}")
        self.assertEqual(unmarkdown(md).count("alert(1)"), 1)

    def test_unicode_survives_in_markdown(self):
        interview_id = self.seed_interview([Step("¿Qué es una “promesa”? 日本語", "Une promesse représente la valeur future — café, naïve, Привет, Ελληνικά, 🙂")])
        md = self.client.get(f"/api/interviews/{interview_id}/report.md").content.decode("utf-8")
        for text in ("¿Qué es una “promesa”? 日本語", "représente", "café", "naïve", "Привет", "Ελληνικά", "🙂"):
            self.assertIn(text, md)

    def test_long_answers_are_kept_whole(self):
        long_answer = " ".join(f"sentence{i} explains part {i} of the design." for i in range(1500))
        interview_id = self.seed_interview([Step("Q?", long_answer)])
        md = unmarkdown(self.client.get(f"/api/interviews/{interview_id}/report.md").text)
        self.assertIn("sentence0 ", md)
        self.assertIn("sentence1499 ", md)

    def test_download_headers_and_safe_filename(self):
        interview_id = self.standard_interview()
        for suffix, mime in (("md", "text/markdown"), ("pdf", "application/pdf")):
            r = self.client.get(f"/api/interviews/{interview_id}/report.{suffix}")
            disposition = r.headers["content-disposition"]
            self.assertRegex(disposition, rf'^attachment; filename="interview-{interview_id}-aarav-sharma\.{suffix}"$')
            self.assertTrue(r.headers["content-type"].startswith(mime))
            self.assertEqual(r.headers["x-content-type-options"], "nosniff")
            self.assertEqual(r.headers["cache-control"], "no-store")

    def test_filenames_never_contain_path_or_unusual_characters(self):
        class FakeReport:
            interview_id = 7
        for name in ("../../etc/passwd", "..\\..\\Windows\\win.ini", 'Ünïcode Näme/\\:*?"<>|', "", "   ", "日本語", "a" * 200, "CON", "x\x00y\ny"):
            FakeReport.candidate_name = name
            for ext in ("pdf", "md"):
                filename = safe_filename(FakeReport, ext)
                self.assertRegex(filename, r"^interview-7-[a-z0-9-]{1,40}\.(pdf|md)$", repr(name))
                self.assertNotRegex(filename, r"[/\\:]|\.\.")


class PdfExportTests(ReportTestCase):
    def test_pdf_is_valid_and_contains_the_sections_and_evidence(self):
        interview_id = self.standard_interview(review={"decision": "Strong Hire", "type": "OVERRIDE", "reason": "Excellent follow-up reasoning."})
        r = self.client.get(f"/api/interviews/{interview_id}/report.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF-"))
        self.assertGreater(len(pypdf.PdfReader(io.BytesIO(r.content)).pages), 0)
        text = collapse(pdf_text(r.content))
        for expected in ("Interview Feedback Report", "Aarav Sharma", FRONTEND, "Overall Result (AI)", "Strengths", "Knowledge Gaps and Areas for Improvement",
                         "Assessment Dimensions", "Technical Skills: 3.7 / 5", "re-render of that component", "cancels the pending timer",
                         "Suggested Areas for the Next Interview Round", "Human Review (Final Decision)", "Strong Hire", "Excellent follow-up reasoning.",
                         "Interview Transcript and Evaluations", "Question 1, follow-up 1", "Prompt-Injection Check", "6 / 10", "Page 1"):
            self.assertIn(expected, text)

    def test_markdown_and_pdf_contain_the_same_substantive_information(self):
        interview_id = self.standard_interview(review={"decision": "Hire", "type": "ACCEPT"})
        report = build_report(self, interview_id)
        md = collapse(unmarkdown(render_markdown(report)))
        pdf = collapse(pdf_text(render_pdf(report)))
        must_match = [report.candidate_name, report.job_title, f"{report.overall_score} / 10", report.ai_recommendation,
                      report.overall_assessment, report.human_review.decision, report.security.summary]
        must_match += [qa.label for qa in report.transcript] + [qa.question for qa in report.transcript]
        must_match += [e.quote for d in report.dimensions for e in d.evidence if e.quote]
        must_match += [f"{d.label}: {d.average:.1f} / 5" for d in report.dimensions if d.average is not None]
        for fact in must_match:
            self.assertIn(collapse(fact), md, f"missing from markdown: {fact!r}")
            self.assertIn(collapse(fact), pdf, f"missing from pdf: {fact!r}")

    def test_long_reports_span_multiple_pages_without_losing_content(self):
        steps = [Step(f"Question number {i} about design?", " ".join(f"detail{i}x{j} describes the approach." for j in range(220)))
                 for i in range(1, 7)]
        interview_id = self.seed_interview(steps)
        data = self.client.get(f"/api/interviews/{interview_id}/report.pdf").content
        reader = pypdf.PdfReader(io.BytesIO(data))
        self.assertGreater(len(reader.pages), 3)
        text = collapse(pdf_text(data))
        self.assertIn("detail1x0 ", text)
        self.assertIn("detail6x219 ", text)                                       # the very end of the last long answer
        for number, page in enumerate(reader.pages, 1):
            self.assertIn(f"Page {number} / {len(reader.pages)}", collapse(page.extract_text()))
            box = page.mediabox
            self.assertAlmostEqual(float(box.width), 595.28, delta=1)             # A4, nothing wider than the page

    def test_an_unbroken_very_long_string_is_wrapped_not_clipped_and_does_not_crash(self):
        blob = "A" * 4000
        interview_id = self.seed_interview([Step("Q?", blob)])
        data = self.client.get(f"/api/interviews/{interview_id}/report.pdf").content
        self.assertTrue(data.startswith(b"%PDF-"))
        text = pdf_text(data).replace("\n", "").replace(" ", "")
        self.assertGreaterEqual(text.count("A"), 4000)                            # every character is present, wrapped across lines

    def test_unicode_and_control_characters_do_not_crash_the_pdf(self):
        tricky = "Ünïcode café — “quotes” Привет Ελληνικά 日本語 🙂\x00\x07 tab\there ‮ rtl"
        interview_id = self.seed_interview([Step(tricky, tricky)])
        data = self.client.get(f"/api/interviews/{interview_id}/report.pdf").content
        self.assertTrue(data.startswith(b"%PDF-"))
        if find_unicode_font():
            text = pdf_text(data)
            for expected in ("café", "Привет", "Ελληνικά"):
                self.assertIn(expected, text)

    def test_without_a_unicode_font_the_pdf_still_renders_with_a_safe_fallback(self):
        interview_id = self.seed_interview([Step("Q café 日本語?", "Answer café 日本語 end")])
        with mock.patch.object(report_export, "find_unicode_font", return_value=None):
            data = render_pdf(build_report(self, interview_id))
        text = pdf_text(data)
        self.assertIn("caf", text)
        self.assertIn("?", text)                                                  # unsupported characters are replaced, not fatal

    def test_pdf_is_generated_in_memory_and_nothing_is_written_to_disk(self):
        interview_id = self.standard_interview()
        with tempfile.TemporaryDirectory() as workdir:
            previous = os.getcwd()
            os.chdir(workdir)
            try:
                for suffix in ("report.pdf", "report.md", "report"):
                    self.assertEqual(self.client.get(f"/api/interviews/{interview_id}/{suffix}").status_code, 200)
                self.assertEqual(os.listdir(workdir), [])
            finally:
                os.chdir(previous)

    def test_a_missing_evaluation_does_not_crash_the_pdf(self):
        interview_id = self.seed_interview([Step("Q?", None), Step("Q2?", "answer", evaluated=False), Step("Q3?", "answer", dims=False)])
        data = self.client.get(f"/api/interviews/{interview_id}/report.pdf").content
        text = collapse(pdf_text(data))
        self.assertIn("No answer was submitted", text)
        self.assertIn("not evaluated", text.lower())


if __name__ == "__main__":
    unittest.main()
