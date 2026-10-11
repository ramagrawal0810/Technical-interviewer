"""A candidate's saved CV: association, reuse when scheduling, replacement uploads and what reaches question generation.

Real routers, real database (temp SQLite seeded with the real seed data, which attaches the synthetic CVs), real PDF extraction.
Only the OpenAI-backed generators are doubles; they record exactly what they were given.

Run from backend/:  venv\\Scripts\\python.exe -m unittest tests.test_saved_candidate_cv -v
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import seed_database
from app.db import models
from app.services.candidate_cv import attach_synthetic_cvs
from tests.test_adaptive_engine import BACKEND, DATA_DIR, FRONTEND, STRONG, VAGUE, EngineTestCase
from tests.test_synthetic_cvs import CV_DIR, CV_FILES

FULLSTACK = "Full Stack Engineer"


def manifest():
    with open(os.path.join(CV_DIR, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


class SavedCvTestCase(EngineTestCase):
    def candidates(self):
        return {c["email"]: c for c in self.client.get("/api/candidates").json()}

    def candidate_by_name(self, name):
        return next(c for c in self.client.get("/api/candidates").json() if c["name"] == name)

    def saved_text(self, candidate_id):
        with self.Session() as db:
            return db.get(models.Candidate, candidate_id).cv_text

    def schedule_for(self, candidate_id, role, mode, **extra):
        r = self.client.post("/api/interviews", json={"candidate_id": candidate_id, "job_description_id": self.job_ids[role], "mode": mode, **extra})
        self.assertEqual(r.status_code, 201, r.text)
        self.grant_consent(r.json()["id"])
        return r.json()

    def upload_to_interview(self, interview_id, path, save=False):
        with open(path, "rb") as f:
            return self.client.post(f"/api/interviews/{interview_id}/upload-cv", params={"save_to_candidate": str(save).lower()},
                                    files={"file": (os.path.basename(path), f.read(), "application/pdf")})

    def cv_path_for(self, name):
        entry = next(m for m in manifest() if m["candidate_name"] == name)
        return os.path.join(CV_DIR, entry["file"])

    def drive(self, interview_id, already_started=False):
        if already_started:
            iq = self.client.get(f"/api/interviews/{interview_id}/session").json()["pending"]["interview_question_id"]
        else:
            iq = self.start(interview_id)["current_question"]["id"]
        for _ in range(60):
            self.answer(interview_id, iq, "Hmm, it depends.")
            nxt = self.next_question(interview_id)
            if nxt["is_complete"]:
                return
            iq = nxt["id"]
        self.fail("interview never completed")


class AssociationTests(SavedCvTestCase):
    def test_seeding_links_every_cv_to_the_right_candidate_by_id(self):
        candidates = self.candidates()
        self.assertEqual(len(candidates), 10)
        for entry in manifest():
            candidate = candidates[entry["candidate_email"]]
            self.assertTrue(candidate["has_cv"], entry["file"])
            self.assertEqual(candidate["cv_file_name"], entry["file"])
            self.assertTrue(candidate["cv_updated_at"])
            text = self.saved_text(candidate["id"])
            self.assertEqual(text.splitlines()[0].strip(), candidate["name"])          # the CV text in THIS candidate's row is theirs
            self.assertIn(candidate["email"], text)
            others = [c["name"] for c in candidates.values() if c["id"] != candidate["id"]]
            self.assertFalse([n for n in others if n in text], "another candidate's name appears in this CV")

    def test_the_api_never_returns_the_cv_text_in_candidate_lists(self):
        for candidate in self.client.get("/api/candidates").json():
            self.assertNotIn("cv_text", candidate)

    def test_reseeding_is_idempotent_and_never_overwrites_a_replaced_cv(self):
        aarav = self.candidate_by_name("Aarav Sharma")
        with open(self.cv_path_for("Priya Patel"), "rb") as f:
            r = self.client.post(f"/api/candidates/{aarav['id']}/cv", files={"file": ("replacement.pdf", f.read(), "application/pdf")})
        self.assertEqual(r.status_code, 200)
        with self.Session() as db:
            seed_database.seed_into(db)
            db.commit()
            result = attach_synthetic_cvs(db)
        self.assertEqual((result["attached"], result["already_had_cv"]), (0, 10))
        self.assertEqual(self.candidate_by_name("Aarav Sharma")["cv_file_name"], "replacement.pdf")

    def test_a_cv_is_never_attached_to_a_candidate_whose_name_it_does_not_show(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in os.listdir(CV_DIR):
                shutil.copy(os.path.join(CV_DIR, name), tmp)
            entries = manifest()
            entries[0]["candidate_email"] = entries[1]["candidate_email"]            # Aarav's file pointed at Priya
            Path(tmp, "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
            with self.Session() as db:
                for c in db.query(models.Candidate):
                    c.cv_text = c.cv_file_name = c.cv_updated_at = None
                db.commit()
                result = attach_synthetic_cvs(db, Path(tmp))
                db.commit()
                priya = db.query(models.Candidate).filter_by(email=entries[1]["candidate_email"]).one()
        self.assertEqual(result["attached"], 9)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("CV is for 'Aarav Sharma', candidate is 'Priya Patel'", result["skipped"][0])
        self.assertIn("Priya Patel", priya.cv_text.splitlines()[0])                  # Priya got her own CV from her own entry, not Aarav's

    def test_a_missing_manifest_or_file_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(attach_synthetic_cvs(self.Session(), Path(tmp))["attached"], 0)
            entries = manifest()[:1]
            entries[0]["file"] = "does_not_exist.pdf"
            Path(tmp, "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
            with self.Session() as db:
                db.query(models.Candidate).update({"cv_text": None})
                db.commit()
                result = attach_synthetic_cvs(db, Path(tmp))
        self.assertEqual(result["attached"], 0)
        self.assertEqual(len(result["skipped"]), 1)


class SchedulingReuseTests(SavedCvTestCase):
    def test_cv_driven_scheduling_uses_the_saved_cv_without_any_upload(self):
        yuki = self.candidate_by_name("Yuki Tanaka")
        interview = self.schedule_for(yuki["id"], BACKEND, "cv_driven")               # no cv_text and no upload
        self.assertTrue(interview["cv_available"])
        self.assertEqual(interview["cv_text"], self.saved_text(yuki["id"]))
        self.assertEqual(interview["cv_text"].splitlines()[0].strip(), "Yuki Tanaka")
        with self.Session() as db:
            checks = [c.status for c in db.query(models.ContentSecurityCheck).filter_by(interview_id=interview["id"], source="cv")]
        self.assertEqual(checks, ["clean"])                                          # the reused CV is security-checked for this interview
        self.start(interview["id"])                                                  # and the interview starts straight away

    def test_hybrid_uses_the_same_saved_cv_with_the_role_question_bank(self):
        sofia = self.candidate_by_name("Sofia Garcia")
        interview = self.schedule_for(sofia["id"], FULLSTACK, "hybrid")
        self.assertEqual(interview["cv_text"], self.saved_text(sofia["id"]))
        self.start(interview["id"])
        self.assertEqual(self.cv_calls[-1]["operation"], "hybrid_question_generation")
        self.assertEqual(self.cv_calls[-1]["job_title"], FULLSTACK)
        self.assertEqual(self.cv_calls[-1]["cv_text"], self.saved_text(sofia["id"]))
        self.policy = lambda *a: STRONG
        self.drive(interview["id"], already_started=True)
        asked = {q.question: q for q in self.db_questions()}
        with self.Session() as db:
            used = [db.get(models.Question, iq.question_id) for iq in db.query(models.InterviewQuestion).filter_by(interview_id=interview["id"])]
        self.assertTrue(any(q.source_interview_id is None for q in used), "hybrid must include role question-bank questions")
        self.assertTrue(any(q.category == "hybrid_generated" for q in used), "hybrid must include CV-based questions")
        self.assertTrue(all(q.job_type == FULLSTACK for q in used))
        self.assertTrue(asked)

    def test_question_bank_mode_needs_no_cv_and_does_not_copy_one(self):
        aarav = self.candidate_by_name("Aarav Sharma")
        interview = self.schedule_for(aarav["id"], BACKEND, "question_bank")
        self.assertIsNone(interview["cv_text"])
        self.assertFalse(interview["cv_available"] and interview["cv_text"])
        self.start(interview["id"])
        self.assertEqual(self.cv_calls, [])

    def test_an_explicit_cv_in_the_scheduling_request_wins_over_the_saved_one(self):
        aarav = self.candidate_by_name("Aarav Sharma")
        interview = self.schedule_for(aarav["id"], BACKEND, "cv_driven", cv_text="Custom CV text for this interview only. " * 5)
        self.assertTrue(interview["cv_text"].startswith("Custom CV text"))
        self.assertEqual(self.saved_text(aarav["id"]).splitlines()[0].strip(), "Aarav Sharma")

    def test_the_interview_keeps_the_cv_it_was_scheduled_with_when_the_saved_cv_changes_later(self):
        aarav = self.candidate_by_name("Aarav Sharma")
        interview = self.schedule_for(aarav["id"], BACKEND, "cv_driven")
        before = interview["cv_text"]
        with open(self.cv_path_for("Priya Patel"), "rb") as f:
            self.client.post(f"/api/candidates/{aarav['id']}/cv", files={"file": ("new.pdf", f.read(), "application/pdf")})
        self.assertNotEqual(self.saved_text(aarav["id"]), before)
        self.assertEqual(self.client.get(f"/api/interviews/{interview['id']}").json()["cv_text"], before)
        self.start(interview["id"])
        self.assertEqual(self.cv_calls[-1]["cv_text"], before)

    def test_a_legacy_interview_without_a_cv_picks_up_the_saved_cv_when_started(self):
        marcus = self.candidate_by_name("Marcus Johnson")
        with self.Session() as db:
            iv = models.Interview(candidate_id=marcus["id"], job_description_id=self.job_ids[BACKEND], mode="cv_driven", status="not_started")
            db.add(iv)
            db.commit()
            interview_id = iv.id
        self.assertTrue(self.client.get(f"/api/interviews/{interview_id}").json()["cv_available"])
        self.grant_consent(interview_id)
        self.start(interview_id)
        self.assertEqual(self.cv_calls[-1]["cv_text"], self.saved_text(marcus["id"]))


class MissingCvTests(SavedCvTestCase):
    def candidate_without_cv(self):
        c = self.candidate_by_name("Adil Hassan")
        with self.Session() as db:
            row = db.get(models.Candidate, c["id"])
            row.cv_text = row.cv_file_name = row.cv_updated_at = None
            db.commit()
        return c

    def test_no_saved_cv_means_the_cv_modes_are_not_ready_until_one_is_uploaded(self):
        adil = self.candidate_without_cv()
        self.assertFalse(self.candidate_by_name("Adil Hassan")["has_cv"])
        for mode in ("cv_driven", "hybrid"):
            interview = self.schedule_for(adil["id"], FRONTEND, mode)
            self.assertFalse(interview["cv_available"])
            self.assertIsNone(interview["cv_text"])
            r = self.client.post(f"/api/interviews/{interview['id']}/start")
            self.assertEqual(r.status_code, 400)
            self.assertIn("require a CV", r.json()["detail"])
            self.assertEqual(self.upload_to_interview(interview["id"], self.cv_path_for("Adil Hassan")).status_code, 200)
            self.assertTrue(self.client.get(f"/api/interviews/{interview['id']}").json()["cv_available"])
            self.start(interview["id"])

    def test_question_bank_works_for_a_candidate_without_a_cv(self):
        adil = self.candidate_without_cv()
        interview = self.schedule_for(adil["id"], FRONTEND, "question_bank")
        self.start(interview["id"])

    def test_bad_uploads_are_rejected_and_change_nothing(self):
        adil = self.candidate_without_cv()
        interview = self.schedule_for(adil["id"], FRONTEND, "cv_driven")
        for name, data, ctype in (("cv.txt", b"text", "text/plain"), ("empty.pdf", b"", "application/pdf"), ("bad.pdf", b"%PDF-1.4 x", "application/pdf")):
            r = self.client.post(f"/api/interviews/{interview['id']}/upload-cv", files={"file": (name, data, ctype)})
            self.assertEqual(r.status_code, 400, name)
            r = self.client.post(f"/api/candidates/{adil['id']}/cv", files={"file": (name, data, ctype)})
            self.assertEqual(r.status_code, 400, name)
        self.assertFalse(self.candidate_by_name("Adil Hassan")["has_cv"])
        self.assertIsNone(self.client.get(f"/api/interviews/{interview['id']}").json()["cv_text"])
        self.assertEqual(self.client.post("/api/candidates/99999/cv", files={"file": ("a.pdf", b"x", "application/pdf")}).status_code, 404)


class ReplacementTests(SavedCvTestCase):
    def test_a_replacement_for_one_interview_changes_neither_the_saved_cv_nor_any_other_candidate(self):
        aarav, priya = self.candidate_by_name("Aarav Sharma"), self.candidate_by_name("Priya Patel")
        aarav_saved, priya_saved = self.saved_text(aarav["id"]), self.saved_text(priya["id"])
        first = self.schedule_for(aarav["id"], BACKEND, "cv_driven")
        second = self.schedule_for(aarav["id"], BACKEND, "hybrid")                     # a second interview for the same candidate
        other = self.schedule_for(priya["id"], FRONTEND, "cv_driven")

        replacement = self.cv_path_for("Yuki Tanaka")
        self.assertEqual(self.upload_to_interview(first["id"], replacement).status_code, 200)

        self.assertIn("Yuki Tanaka", self.client.get(f"/api/interviews/{first['id']}").json()["cv_text"])   # used for the intended interview
        self.assertEqual(self.client.get(f"/api/interviews/{second['id']}").json()["cv_text"], aarav_saved)  # not for her other interview
        self.assertEqual(self.client.get(f"/api/interviews/{other['id']}").json()["cv_text"], priya_saved)   # nor another candidate's
        self.assertEqual(self.saved_text(aarav["id"]), aarav_saved)                    # the saved CV is untouched
        self.assertEqual(self.saved_text(priya["id"]), priya_saved)

        self.start(first["id"])
        self.assertIn("Yuki Tanaka", self.cv_calls[-1]["cv_text"])

    def test_saving_a_replacement_to_the_candidate_is_explicit_and_affects_only_that_candidate(self):
        aarav, priya = self.candidate_by_name("Aarav Sharma"), self.candidate_by_name("Priya Patel")
        priya_saved = self.saved_text(priya["id"])
        interview = self.schedule_for(aarav["id"], BACKEND, "cv_driven")
        r = self.upload_to_interview(interview["id"], self.cv_path_for("Yuki Tanaka"), save=True)
        self.assertEqual((r.status_code, r.json()["saved_to_candidate"]), (200, True))
        self.assertIn("Yuki Tanaka", self.saved_text(aarav["id"]))
        self.assertEqual(self.saved_text(priya["id"]), priya_saved)
        later = self.schedule_for(aarav["id"], BACKEND, "cv_driven")
        self.assertIn("Yuki Tanaka", later["cv_text"])                                 # future interviews reuse the new saved CV

    def test_the_candidate_cv_endpoint_replaces_only_that_candidate(self):
        aarav, priya = self.candidate_by_name("Aarav Sharma"), self.candidate_by_name("Priya Patel")
        snapshot = {c["id"]: self.saved_text(c["id"]) for c in self.client.get("/api/candidates").json()}
        with open(self.cv_path_for("Yuki Tanaka"), "rb") as f:
            r = self.client.post(f"/api/candidates/{aarav['id']}/cv", files={"file": ("yuki.pdf", f.read(), "application/pdf")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cv_file_name"], "yuki.pdf")
        for cid, before in snapshot.items():
            if cid == aarav["id"]:
                self.assertNotEqual(self.saved_text(cid), before)
            else:
                self.assertEqual(self.saved_text(cid), before)
        self.assertEqual(self.candidate_by_name("Priya Patel")["cv_file_name"], priya["cv_file_name"])


class CvContextTests(SavedCvTestCase):
    def test_follow_ups_and_evaluations_use_the_interviews_own_candidates_cv(self):
        priya, yuki = self.candidate_by_name("Priya Patel"), self.candidate_by_name("Yuki Tanaka")
        a = self.schedule_for(priya["id"], FRONTEND, "hybrid")
        b = self.schedule_for(yuki["id"], BACKEND, "cv_driven")
        for interview, name, other in ((a, "Priya Patel", "Yuki Tanaka"), (b, "Yuki Tanaka", "Priya Patel")):
            self.eval_calls.clear()
            self.follow_up_calls.clear()
            self.policy = lambda *args: VAGUE
            self.drive(interview["id"])
            self.assertTrue(self.eval_calls and self.follow_up_calls)
            for call in self.eval_calls:
                self.assertIn(name, call["cv"])                                          # the full CV, not just the name or skills
                self.assertNotIn(other, call["cv"])
                self.assertIn("EXPERIENCE", call["cv"])
            for call in self.follow_up_calls:
                self.assertIn(name, call["cv_context"])
                self.assertNotIn(other, call["cv_context"])
                self.assertIn("PROJECTS", call["cv_context"])

    def test_generation_receives_the_full_cv_text_not_a_summary(self):
        marcus = self.candidate_by_name("Marcus Johnson")
        interview = self.schedule_for(marcus["id"], BACKEND, "cv_driven")
        self.start(interview["id"])
        sent = self.cv_calls[-1]["cv_text"]
        self.assertEqual(sent, self.saved_text(marcus["id"]))
        self.assertGreater(len(sent), 1000)
        self.assertNotEqual(sent, marcus["resume_summary"])

    def test_the_real_follow_up_and_evaluation_prompts_carry_the_cv_as_untrusted_data(self):
        from tests.test_adaptive_engine import messages_text
        from app.services.follow_up_generator import build_follow_up_messages
        cv = self.saved_text(self.candidate_by_name("Emma Thompson")["id"])
        messages = build_follow_up_messages("Q?", "A.", "w", "f", cv_context=cv, job_title=FRONTEND, follow_up_reason="vague",
                                            missing_information="m", follow_up_number=1)
        self.assertIn('<untrusted_data name="CANDIDATE_CV">', messages[1]["content"])
        self.assertIn("Emma Thompson", messages[1]["content"])
        self.assertNotIn("Emma Thompson", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
