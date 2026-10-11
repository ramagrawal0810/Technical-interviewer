"""The ten synthetic CVs, the role descriptions and the question bank, exercised through the real upload and interview flow.

The OpenAI-backed generators are doubles (see EngineTestCase); everything else - PDF text extraction, the upload endpoint, mode
handling, question assignment, follow-up limits and the prompts built from the CV - is the real code. What the doubles cannot show
is how relevant a *real* model's questions are; that needs the manual check listed in the final report.

Run from backend/:  venv\\Scripts\\python.exe -m unittest tests.test_synthetic_cvs -v
"""

import glob
import json
import os
import re
import unittest

import pypdf

from app.db import models
from app.services.cv_processor import extract_text_from_pdf, validate_cv_text
from tests.test_adaptive_engine import BACKEND, CONTRADICTION, DATA_DIR, FRONTEND, INCOMPLETE, VAGUE, EngineTestCase

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CV_DIR = os.path.join(ROOT, "backend", "sample_data", "synthetic_cvs")
JD_DIR = os.path.join(ROOT, "data", "job_descriptions")
FULLSTACK = "Full Stack Engineer"
CV_FILES = sorted(glob.glob(os.path.join(CV_DIR, "*.pdf")))
ROLE_OF = {"backend": BACKEND, "frontend": FRONTEND, "fullstack": FULLSTACK}


def role_for(path):
    return ROLE_OF[os.path.basename(path).split("_")[1]]


class SyntheticAssetsTests(unittest.TestCase):
    def test_there_are_exactly_ten_valid_text_pdfs(self):
        self.assertEqual(len(CV_FILES), 10)
        for path in CV_FILES:
            with open(path, "rb") as f:
                data = f.read()
            self.assertTrue(data.startswith(b"%PDF-"), path)
            text = extract_text_from_pdf(data)
            self.assertEqual(validate_cv_text(text), (True, ""), path)
            self.assertGreater(len(text), 1200, f"{path} is too thin to drive meaningful questions")
            for section in ("PROFESSIONAL SUMMARY", "TECHNICAL SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION"):
                self.assertIn(section, text, path)

    def test_the_profiles_cover_three_roles_and_a_range_of_experience(self):
        roles = [role_for(p) for p in CV_FILES]
        self.assertEqual({r: roles.count(r) for r in roles}, {BACKEND: 4, FRONTEND: 4, FULLSTACK: 2})
        levels = " ".join(os.path.basename(p) for p in CV_FILES)
        for level in ("junior", "mid", "senior"):
            self.assertIn(level, levels)

    def test_every_cv_is_marked_fictional_and_uses_only_reserved_contact_details(self):
        for path in CV_FILES:
            with open(path, "rb") as f:
                text = extract_text_from_pdf(f.read())
            self.assertIn("SYNTHETIC SAMPLE CV", text)
            for email in re.findall(r"[\w.+-]+@[\w.-]+", text):
                self.assertTrue(email.endswith("@example.com"), (path, email))
            for phone in re.findall(r"\b555-\d{4}\b", text):
                self.assertRegex(phone, r"555-01\d\d")
            self.assertNotRegex(text, r"https?://")

    def test_each_cv_belongs_to_exactly_one_seeded_candidate(self):
        """Name, email, years and skills printed in the PDF agree with data/candidates.json, one CV per candidate."""
        with open(os.path.join(DATA_DIR, "candidates.json"), encoding="utf-8") as f:
            candidates = json.load(f)
        with open(os.path.join(CV_DIR, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual([m["candidate_email"] for m in manifest], [c["email"] for c in candidates])
        self.assertEqual(len({m["file"] for m in manifest}), 10)
        for order, (entry, candidate) in enumerate(zip(manifest, candidates), 1):
            self.assertEqual((entry["seed_order"], entry["candidate_name"]), (order, candidate["name"]))
            self.assertTrue(entry["file"].startswith(f"{order:02d}_"))
            with open(os.path.join(CV_DIR, entry["file"]), "rb") as f:
                text = extract_text_from_pdf(f.read())
            self.assertEqual(text.splitlines()[0].strip(), candidate["name"])
            self.assertIn(candidate["email"], text)
            self.assertIn(f"{candidate['experience_years']} year", text)
            flat = text.lower()
            for skill in candidate["skills"].split(","):
                self.assertIn(skill.strip().lower(), flat, f"{candidate['name']}: {skill}")
        self.assertEqual(sorted(os.path.basename(p) for p in CV_FILES), sorted(m["file"] for m in manifest))

    def test_three_markdown_job_descriptions_match_the_seeded_roles(self):
        files = sorted(glob.glob(os.path.join(JD_DIR, "*.md")))
        self.assertEqual(len(files), 3)
        with open(os.path.join(DATA_DIR, "job_descriptions.json"), encoding="utf-8") as f:
            jobs = {j["title"]: j for j in json.load(f)}
        for path in files:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            title = text.splitlines()[0].lstrip("# ").strip()
            self.assertIn(title, jobs)
            self.assertIn(jobs[title]["description"], text)
            for skill in jobs[title]["required_skills"].split(","):
                self.assertIn(f"- {skill.strip()}", text)

    def test_the_question_bank_is_role_tagged_with_skill_area_and_difficulty(self):
        for role, name in ((BACKEND, "backend"), (FRONTEND, "frontend"), (FULLSTACK, "fullstack")):
            with open(os.path.join(DATA_DIR, "question_banks", f"{name}_questions.json"), encoding="utf-8") as f:
                questions = json.load(f)
            self.assertGreaterEqual(len(questions), 8, role)
            self.assertEqual({q["job_type"] for q in questions}, {role})
            self.assertTrue(all(q["category"].strip() and q["difficulty"].lower() in ("easy", "medium", "hard") for q in questions))
            self.assertEqual({q["difficulty"].lower() for q in questions}, {"easy", "medium", "hard"})


class SyntheticCvFlowTests(EngineTestCase):
    def upload(self, interview_id, path):
        with open(path, "rb") as f:
            return self.client.post(f"/api/interviews/{interview_id}/upload-cv",
                                    files={"file": (os.path.basename(path), f.read(), "application/pdf")})

    def scheduled_with_uploaded_cv(self, path, mode):
        role = role_for(path)
        if role not in self.job_ids:
            self.skipTest(f"role {role} is not seeded")
        interview_id = self.schedule(role, mode)
        r = self.upload(interview_id, path)
        self.assertEqual(r.status_code, 200, r.text)
        return interview_id, role

    def test_every_cv_uploads_through_the_real_endpoint_and_is_stored_and_checked(self):
        for path in CV_FILES:
            interview_id, role = self.scheduled_with_uploaded_cv(path, "cv_driven")
            body = self.client.get(f"/api/interviews/{interview_id}").json()
            with open(path, "rb") as f:
                expected_name = extract_text_from_pdf(f.read()).splitlines()[0].strip()
            self.assertIn(expected_name, body["cv_text"])
            self.assertEqual(body["status"], "not_started")
            with self.Session() as db:
                checks = [c.status for c in db.query(models.ContentSecurityCheck).filter_by(interview_id=interview_id, source="cv")]
            self.assertEqual(checks, ["clean"], path)

    def test_non_pdf_and_empty_uploads_are_rejected_without_changing_the_interview(self):
        interview_id = self.schedule(FRONTEND, "cv_driven")
        before = self.client.get(f"/api/interviews/{interview_id}").json()["cv_text"]      # the candidate's saved CV, copied at scheduling
        for name, data, ctype in (("cv.txt", b"plain text", "text/plain"), ("empty.pdf", b"", "application/pdf"),
                                  ("fake.pdf", b"%PDF-1.4 not really", "application/pdf")):
            r = self.client.post(f"/api/interviews/{interview_id}/upload-cv", files={"file": (name, data, ctype)})
            self.assertEqual(r.status_code, 400, name)
        self.assertEqual(self.client.get(f"/api/interviews/{interview_id}").json()["cv_text"], before)

    def test_cv_driven_mode_builds_questions_from_each_candidates_own_cv_and_role(self):
        for path in CV_FILES:
            before = len(self.cv_calls)
            interview_id, role = self.scheduled_with_uploaded_cv(path, "cv_driven")
            started = self.start(interview_id)
            self.assertEqual(len(self.cv_calls), before + 1)
            call = self.cv_calls[-1]
            self.assertEqual(call["job_title"], role)
            with open(path, "rb") as f:
                name = extract_text_from_pdf(f.read()).splitlines()[0].strip()
            self.assertIn(name, call["cv_text"])                                 # the generator saw THIS candidate's CV
            first = self.question(started["current_question"]["question_id"])
            self.assertEqual(first["job_type"], role)
            self.assertIn(f"[{role}] CV question", first["question"])

    def test_cv_driven_interviews_use_only_cv_questions_for_the_main_questions(self):
        path = CV_FILES[4]                                                        # Chen Wei, frontend
        interview_id, role = self.scheduled_with_uploaded_cv(path, "cv_driven")
        self.policy = lambda *a: self.default_strong
        asked = self.drive(interview_id)
        mains = [q for q in asked if not q["is_follow_up"]]
        self.assertEqual(len(mains), 5)
        self.assertTrue(all("CV question" in q["text"] and q["job_type"] == role for q in mains))

    def test_hybrid_mode_mixes_role_matched_bank_questions_with_cv_questions(self):
        for path in (CV_FILES[0], CV_FILES[4], CV_FILES[3]):                      # one per role
            interview_id, role = self.scheduled_with_uploaded_cv(path, "hybrid")
            self.policy = lambda *a: self.default_strong
            asked = self.drive(interview_id)
            mains = [q for q in asked if not q["is_follow_up"]]
            cv_based = [q for q in mains if "CV question" in q["text"]]
            bank = [q for q in mains if "CV question" not in q["text"]]
            self.assertEqual(len(mains), 5, path)
            self.assertTrue(cv_based and bank, f"{path}: hybrid must contain both kinds")
            self.assertTrue(all(q["job_type"] == role for q in mains), path)

    def test_question_bank_mode_ignores_the_cv_and_stays_in_role(self):
        interview_id = self.schedule(BACKEND, "question_bank")
        self.assertEqual(self.upload(interview_id, CV_FILES[0]).status_code, 400)   # CV upload is only for CV-based modes
        before = len(self.cv_calls)
        self.policy = lambda *a: self.default_strong
        asked = self.drive(interview_id)
        self.assertEqual(len(self.cv_calls), before)                               # no CV generation in bank-only mode
        self.assertTrue(all(q["job_type"] == BACKEND for q in asked))

    def test_vague_incomplete_and_contradicting_answers_follow_up_at_most_three_times(self):
        path = CV_FILES[1]                                                        # Priya Patel: Redux is on the CV
        for label, outcome in (("vague", VAGUE), ("incomplete", INCOMPLETE), ("contradicts_cv", CONTRADICTION)):
            interview_id, role = self.scheduled_with_uploaded_cv(path, "hybrid")
            self.follow_up_calls.clear()
            self.policy = lambda *a, outcome=outcome: outcome
            asked = self.drive(interview_id, answer_text="I have never used Redux." if label == "contradicts_cv" else "Hmm, it depends.")
            run, longest = 0, 0
            for q in asked:
                run = run + 1 if q["is_follow_up"] else 0
                longest = max(longest, run)
            self.assertGreater(longest, 0, label)
            self.assertLessEqual(longest, 3, label)
            self.assertEqual(len([q for q in asked if not q["is_follow_up"]]), 5, label)
            self.assertTrue(all(q["job_type"] == role for q in asked), label)
            self.assertTrue(all(q["reason"] in (None, label) for q in asked if q["is_follow_up"]), label)
            self.assertTrue(self.follow_up_calls)
            self.assertTrue(all("Redux" in (c.get("cv_context") or "") for c in self.follow_up_calls),
                            "follow-ups must be generated with the candidate's own CV as context")

    # ---- helpers ----
    default_strong = None

    def setUp(self):
        super().setUp()
        from tests.test_adaptive_engine import STRONG
        self.default_strong = STRONG

    def drive(self, interview_id, answer_text="This is my answer."):
        """Run an already-scheduled interview to the end through the API; returns the asked questions."""
        started = self.start(interview_id)
        asked = [{"iq_id": started["current_question"]["id"], "question_id": started["current_question"]["question_id"],
                  "is_follow_up": False, "reason": None}]
        for _ in range(60):
            self.answer(interview_id, asked[-1]["iq_id"], answer_text)
            nxt = self.next_question(interview_id)
            if nxt["is_complete"]:
                return self._describe(asked)
            asked.append({"iq_id": nxt["id"], "question_id": nxt["question_id"], "is_follow_up": nxt["is_follow_up"],
                          "reason": nxt["follow_up_reason"]})
        self.fail("interview never completed")


if __name__ == "__main__":
    unittest.main()
