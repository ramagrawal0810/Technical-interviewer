# Archive of historical build documents

These 21 Markdown files were written during the build (2026-10-06 and 2026-10-07) and were moved here unchanged from the repository root (and `backend/`) during the documentation cleanup.
They are kept as history, **not** as current documentation: several describe earlier states (for example "design phase, no implementation yet" or "ready for testing"), and the maintained
documents in [`docs/`](../README.md) supersede them. Nothing in the code, tests or scripts references them. Integrity: `legacy_reports/MANIFEST.sha256` lists the SHA-256 of each file, verified identical after the move.
The previous root `README.md` is preserved as `README_before_docs_pass.md`. The descriptions in the table come from each file's title and opening lines, not from a full re-read.

| Archived file | What it was | Superseded by |
|---|---|---|
| `legacy_reports/AUDIT_LLM_API_CALLS.md` | Audit of every OpenAI call before usage logging | [FEATURES_AND_CODE_MAP.md](../FEATURES_AND_CODE_MAP.md) (LLM usage logging), [ARCHITECTURE.md](../ARCHITECTURE.md) |
| `legacy_reports/LLM_USAGE_LOGGING_IMPLEMENTATION.md`, `FINAL_LLM_USAGE_LOGGING_REPORT.md`, `FINAL_IMPLEMENTATION_SUMMARY.md`, `COMPLETION_SUMMARY.md`, `IMPLEMENTATION_AUDIT_TRAIL.md`, `IMPLEMENTATION_COMPLETE_TESTING_GUIDE.md`, `TESTING_AND_VERIFICATION.md` | Progress, summary and test-guide notes for the usage-logging work | same as above |
| `legacy_reports/EVALUATION_SYSTEM_AUDIT.md` | Pre-implementation audit of the four-dimension evaluation | [ARCHITECTURE.md](../ARCHITECTURE.md) Flow 4 |
| `legacy_reports/FOUR_DIMENSIONAL_EVALUATION_IMPLEMENTATION.md`, `IMPLEMENTATION_QUICK_REFERENCE.md`, `RUNTIME_VERIFICATION_REPORT.md` | Implementation, quick reference and runtime check of the four-dimension evaluation | [FEATURES_AND_CODE_MAP.md](../FEATURES_AND_CODE_MAP.md) |
| `legacy_reports/HUMAN_REVIEW_SYSTEM_AUDIT.md`, `HUMAN_REVIEW_IMPLEMENTATION_REPORT.md` | Design and implementation of accept/override review | [ARCHITECTURE.md](../ARCHITECTURE.md) Flow 6 |
| `legacy_reports/INTERVIEWER_PANEL_AUDIT.md`, `INTERVIEWER_PANEL_IMPLEMENTATION_REPORT.md` | Design and implementation of the interviewer panel | [FEATURES_AND_CODE_MAP.md](../FEATURES_AND_CODE_MAP.md) |
| `legacy_reports/COMPREHENSIVE_IMPLEMENTATION_REPORT.md` | Broad end-to-end implementation report | [ARCHITECTURE.md](../ARCHITECTURE.md) |
| `legacy_reports/BUG_FIX_REPORT.md`, `REVIEW_DATA_500_FIX_REPORT.md`, `RUNTIME_ISSUES_FIX_REPORT.md`, `PHASE5_BUG_ANALYSIS.md` | Root-cause notes for specific bugs (a NameError in the evaluate endpoint, a review-data 500, runtime issues, CV questions appearing in Question Bank mode) | Addressed in later work; not individually re-verified during this cleanup |
