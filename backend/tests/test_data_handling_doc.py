"""docs/DATA_HANDLING.md must match what the code does."""

import os
import re
import unittest

from app.api.human_review import DEMO_INTERVIEWER_ID, DEMO_INTERVIEWER_NAME
from app.config import settings
from app.db import models
from app.services.data_policy import get_data_handling_policy

DOC_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "DATA_HANDLING.md")


def doc_text():
    with open(DOC_PATH, encoding="utf-8") as f:
        return f.read()


class DataHandlingDocTests(unittest.TestCase):
    def test_the_document_exists(self):
        self.assertTrue(os.path.exists(DOC_PATH))

    def test_the_default_retention_period_matches_the_setting(self):
        self.assertIn(f"default **{settings.data_retention_days}** days", doc_text())
        self.assertEqual(get_data_handling_policy()["retention_days"], settings.data_retention_days)
        self.assertIn("DATA_RETENTION_DAYS", doc_text())

    def test_the_database_file_name_matches_the_code(self):
        self.assertIn(get_data_handling_policy()["database_file"], doc_text())

    def test_every_table_in_the_schema_is_documented(self):
        text = doc_text()
        for table in models.Base.metadata.tables:
            self.assertIn(f"`{table}`", text, f"table {table} is not described in DATA_HANDLING.md")

    def test_the_consent_columns_are_documented(self):
        for column in ("consent_status", "consent_recorded_at", "consent_notice_version"):
            self.assertIn(column, doc_text())
            self.assertIn(column, models.Interview.__table__.columns)

    def test_it_does_not_claim_unimplemented_protection(self):
        policy = get_data_handling_policy()
        self.assertFalse(policy["automatic_deletion_implemented"])
        self.assertFalse(policy["encryption_implemented"])
        text = doc_text()
        self.assertIn("Nothing is deleted automatically", text)
        self.assertIn("No encryption at rest is implemented", text)
        self.assertIn("No real authentication or authorization", text)
        for claim in (r"\bencrypted at rest\b", r"\bautomatically (deleted|expire[sd]?|purged)\b", r"\baccess[- ]controlled\b"):
            for match in re.finditer(claim, text, re.IGNORECASE):
                window = text[max(0, match.start() - 60):match.start()].lower()
                self.assertRegex(window, r"(not|no|never|isn't|nothing)", f"unqualified claim: {match.group(0)!r}")

    def test_the_demo_interviewer_identity_is_documented_as_fixed(self):
        self.assertIn(DEMO_INTERVIEWER_ID, doc_text())
        self.assertIn(DEMO_INTERVIEWER_NAME, doc_text())

    def test_the_documented_cleanup_options_exist(self):
        import retention_cleanup
        import inspect
        source = inspect.getsource(retention_cleanup)
        for option in ("--days", "--interview-id", "--execute"):
            self.assertIn(option, doc_text())
            self.assertIn(option, source)

    def test_the_documented_endpoint_and_audio_route_exist(self):
        from app.main import app
        paths = {route.path for route in app.routes}
        self.assertIn("/api/data-handling", paths)
        self.assertIn("/api/audio/speech-to-text", paths)
        self.assertIn("/api/audio/speech-to-text", doc_text())


if __name__ == "__main__":
    unittest.main()
