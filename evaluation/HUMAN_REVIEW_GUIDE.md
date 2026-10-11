# Human-agreement review: step by step

Goal (experiment D): compare the AI evaluator with an **independent human** on at least **five completed interviews**, scoring
every answer with the same rubric the AI uses. Nothing here is filled in for you; the analysis refuses to call the experiment
complete until real human scores exist.

> The original assignment text is not stored in the repository, so the sample size follows the wording I was given:
> "at least five sample interviews". If your assignment says five *reviewed records* instead, five interviews still satisfy it; if it
> allows fewer, edit `MIN_INTERVIEWS` in `human_agreement.py` and say so in the report.

## What exists today

The application database has **one** completed interview (id 2, Frontend role, Hybrid, 8 answers) and one abandoned one (excluded:
partial). So at least **four more completed interviews** are needed. Interview 2 also contains three deliberate prompt-injection
test answers (Q1 and its two follow-ups were the instruction-style attempts); you may score them like any other answer (a human should
give them 1 on every dimension), but they are not representative technical answers.

## Process

1. **Create four or more more completed interviews** in the app (Schedule, then take each as the candidate through all 5 main
   questions). Vary the quality on purpose, so the sample has strong, middling and weak answers, and ideally cover all three roles.
   Do not edit or delete any existing record.
2. **Export the sheets** (read-only on the database; safe to repeat, it keeps anything you already typed):
   `backend\venv\Scripts\python.exe evaluation\export_review_pack.py`
   Files appear in `evaluation/human_review/`.
3. **Pick the reviewer.** It should be someone who did not write the answers and, ideally, who has not seen the AI scores. Write their
   name in the `reviewer` column. Use one reviewer for the whole sample, or tell me if several people share it.
4. **Score blind.** Open ONLY `review_sheet_BLIND.csv` (no AI scores in it). Do not open the app's review page, the PDF/Markdown
   report, or `*_with_ai.csv` until you have finished scoring. For every row read the question and `candidate_answer`; for CV-based
   interviews the CV is the file named in `candidate_cv_file` (in `backend/sample_data/synthetic_cvs/`).
5. **Score each answer 1-5 on the four dimensions** (type whole numbers into the four `human_*` columns):
   - `human_technical_skills`: Does the candidate show solid technical knowledge and correct understanding?
   - `human_domain_understanding`: Does the candidate understand the domain and context of the question?
   - `human_problem_solving`: Does the candidate approach the problem systematically?
   - `human_communication_clarity`: Is the explanation clear and well structured?

   Scale: **1 = poor, 5 = excellent.** An answer with no technical substance (for example, only an instruction to the grader) is 1 on
   every dimension. Judge only what is written; ignore the candidate's name, background or anything not about the job. Write a short
   reason in `human_comment` when a score is not obvious.
6. **Give one overall recommendation per interview** in `interview_sheet_BLIND.csv`, after reading all of that interview's answers
   including follow-ups: `human_recommendation` must be exactly one of **Strong Hire, Hire, Hold, No Hire**, with a sentence in
   `human_rationale` and your name in `reviewer`.
7. **Run the analysis:** `backend\venv\Scripts\python.exe evaluation\human_agreement.py`
   It reports agreement per dimension, score differences, recommendation agreement, and lists every answer where the AI and you differ
   by 2 or more points.
8. **Explain the disagreements.** Open `review_sheet_with_ai.csv` (and `interview_sheet_with_ai.csv` for recommendations), look at the AI
   scores for the flagged rows, and write why the two differ in `disagreement_explanation` (for example: "AI credited an example the
   answer does not contain", or "I was stricter about missing trade-offs"). Run step 7 again; `evaluation/RESULTS.md` section D is
   rebuilt, and the status becomes COMPLETE only when at least five interviews are fully scored and have a recommendation.

## Rules that keep the result honest

- Do not change AI scores, and do not score after looking at them.
- Do not leave a row half-filled: a row counts only when all four dimension scores are present.
- Re-running the export never overwrites what you typed; `--force-new` does, so avoid it.
