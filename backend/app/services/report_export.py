"""Render a FeedbackReport as Markdown or PDF.

Both formats are produced from ONE intermediate document (build_document), so they always contain the same
information. Candidate-supplied text (answers, quotes, names) is escaped for Markdown and sanitised for the PDF;
nothing is written to disk by this module: the bytes are returned to the caller.
"""

import os
import re
import unicodedata
from datetime import datetime
from typing import Optional

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.schemas import FeedbackReport

MODE_LABELS = {"question_bank": "Question Bank", "cv_driven": "CV Driven", "hybrid": "Hybrid"}
SECURITY_LABELS = {
    "clean": "No suspected attempt detected",
    "suspected": "Suspected attempt detected",
    "failed": "Check failed",
    "unknown": "Unknown",
}
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _dt(value: Optional[datetime]) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "Not recorded"


def _score(value, scale: int) -> str:
    return f"{value} / {scale}" if value is not None else "Unavailable"


# ------------------------------------------------------------------ document model ----

def build_document(r: FeedbackReport) -> list[tuple]:
    """
    The report as a flat list of blocks, in reading order:
    ("title", text) ("h2", text) ("h3", text) ("facts", [(label, value)]) ("para", text) ("note", text)
    ("bullets", [text]) ("quote", label, text)
    """
    d: list[tuple] = [("title", "Interview Feedback Report")]
    if r.is_partial:
        d.append(("h2", r.partial_label or "Partial Interview \u2014 Abandoned"))
        d.append(("para", "The candidate ended this interview before finishing it. This report shows only what was saved: no full-interview "
                          "score or hiring recommendation is produced from incomplete evidence, and unevaluated answers are marked Not Evaluated."))
    d.append(("facts", [
        ("Candidate", r.candidate_name),
        ("Interview ID", str(r.interview_id)),
        ("Job role", r.job_title),
        ("Interview mode", MODE_LABELS.get(r.mode, r.mode)),
        (("Abandoned at" if r.is_partial else "Completed"), _dt(r.abandoned_at if r.is_partial else r.completed_at)),
        ("Report generated", _dt(r.generated_at)),
        ("Candidate consent", (f"{r.consent.status.title()} on {_dt(r.consent.recorded_at)}" if r.consent.status else "No decision recorded")),
    ]))

    d.append(("h2", "Overall Result (AI)"))
    if r.is_partial:
        d.append(("facts", [
            ("Overall score", "Not generated (interview abandoned)"),
            ("Preliminary AI recommendation", r.ai_recommendation or "Not generated (interview abandoned)"),
            ("Answered questions", f"{r.answered_count} answered; {r.main_questions_asked} main question(s) asked"),
            ("Evaluation coverage", r.evaluation_coverage),
        ]))
        if r.ai_recommendation_note:
            d.append(("note", r.ai_recommendation_note))
    else:
        d.append(("facts", [
            ("Overall score", _score(r.overall_score, 10)),
            ("AI recommendation (advisory)", r.ai_recommendation or "Unavailable"),
            ("AI confidence", r.ai_confidence or "Unavailable"),
            ("Evaluation coverage", r.evaluation_coverage),
        ]))
        d.append(("h3", "Overall assessment"))
        d.append(("para", r.overall_assessment) if r.overall_assessment else ("note", "No AI assessment text was stored for this interview."))

    d.append(("h2", "Strengths"))
    if r.strengths:
        d.append(("bullets", [_point_text(p) for p in r.strengths]))
    else:
        d.append(("note", r.strengths_note or "None recorded."))

    d.append(("h2", "Knowledge Gaps and Areas for Improvement"))
    if r.gaps:
        d.append(("bullets", [_point_text(p) for p in r.gaps]))
    else:
        d.append(("note", r.gaps_note or "None recorded."))

    d.append(("h2", "Assessment Dimensions"))
    d.append(("para", "Each dimension is scored 1-5. The average is taken over the answers that have a 4D evaluation; every "
                      "score below is backed by the evaluator's justification and, where one was stored, a verbatim quote from the answer."))
    for dim in r.dimensions:
        average = f"{dim.average:.1f} / 5" if dim.average is not None else "Unavailable"
        d.append(("h3", f"{dim.label}: {average} ({dim.evaluated_answers} evaluated answer{'s' if dim.evaluated_answers != 1 else ''})"))
        if not dim.evidence:
            d.append(("note", dim.note or "No evidence available."))
        for e in dim.evidence:
            d.append(("para", f"{e.question}: scored {e.score} / 5. {e.justification}"))
            d.append(("quote", "Evidence from the candidate's answer", e.quote) if e.quote else ("note", e.quote_note or "No verbatim quote available."))

    d.append(("h2", "Suggested Areas for the Next Interview Round"))
    if r.next_round_areas:
        d.append(("bullets", [_point_text(p) for p in r.next_round_areas]))
    else:
        d.append(("note", r.next_round_note or "None recorded."))

    d.append(("h2", "Human Review (Final Decision)"))
    d.append(("para", "The AI recommendation above is advisory. The decision below is the interviewer's, stored separately; "
                      "an override never replaces the original AI recommendation."))
    h = r.human_review
    if h.present:
        d.append(("facts", [
            ("Final human decision", h.decision or "Unavailable"),
            ("How it was reached", h.decision_type or "Unavailable"),
            ("AI recommendation at review time", f"{h.ai_recommendation_at_review or 'Unavailable'} (confidence: {h.ai_confidence_at_review or 'Unavailable'})"),
            ("Interviewer", f"{h.interviewer_name or 'Unavailable'} (ID: {h.interviewer_id or 'Unavailable'})"),
            ("Reviewed", _dt(h.reviewed_at)),
        ]))
        if h.override_reason:
            d.append(("quote", "Interviewer feedback (override reason)", h.override_reason))
        elif h.note:
            d.append(("note", h.note))
    else:
        d.append(("note", h.note or "No human review has been recorded."))

    d.append(("h2", "Interview Transcript and Evaluations"))
    for qa in r.transcript:
        d.append(("h3", qa.label + (" (follow-up)" if qa.is_follow_up else "")))
        d.append(("quote", "Question", qa.question))
        d.append(("quote", "Candidate answer", qa.answer) if qa.answer else ("note", "No answer was submitted."))
        d.append(("facts", [("Evaluation score", _score(qa.score, 10) if qa.score is not None else "Not Evaluated")]))
        if qa.dimensions:
            d.append(("bullets", [
                f"{x.label}: {x.score} / 5. {x.justification}" + (f' Evidence: "{x.quote}"' if x.quote else " (no verbatim quote stored)")
                for x in qa.dimensions
            ]))
        if qa.follow_up_reason or qa.missing_information:
            d.append(("para", f"Follow-up trigger: {qa.follow_up_reason or 'n/a'}. Noted gap: {qa.missing_information or 'none'}."))
        if qa.note:
            d.append(("note", qa.note))

    d.append(("h2", "Prompt-Injection Check"))
    d.append(("facts", [("Status", SECURITY_LABELS.get(r.security.status, r.security.status))]))
    d.append(("para", r.security.summary))
    if r.security.findings:
        d.append(("bullets", [
            f"{f.source}: {'; '.join(f.categories)}." + (f' Flagged text: "{f.excerpt}"' if f.excerpt else "")
            for f in r.security.findings
        ]))
    d.append(("note", "This check is a heuristic and does not change any score or recommendation. This report is for interviewers only. "
                      "It is generated from stored interview data and contains no credentials, LLM usage costs or internal logs."))
    return d


def _point_text(point) -> str:
    text = point.text
    if point.source:
        text += f" [{point.source}]"
    if point.quote:
        text += f' Evidence: "{point.quote}"'
    return text


# ----------------------------------------------------------------------- filename ----

def safe_filename(report: FeedbackReport, extension: str) -> str:
    """interview-<id>-<candidate-slug>.<ext>: ASCII letters, digits and hyphens only (no path characters)."""
    folded = unicodedata.normalize("NFKD", report.candidate_name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:40] or "candidate"
    return f"interview-{int(report.interview_id)}-{slug}.{extension}"


# ----------------------------------------------------------------------- markdown ----

def _md_escape(text) -> str:
    text = _CONTROL_CHARS.sub("", str(text)).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"([\\`*_{}\[\]()#+!|<>~&])", lambda m: "&amp;" if m.group(1) == "&" else "\\" + m.group(1), text)


def _md_line(text) -> str:
    return re.sub(r"\s+", " ", _md_escape(text)).strip()


def render_markdown(report: FeedbackReport) -> str:
    out: list[str] = []
    for block in build_document(report):
        kind = block[0]
        if kind == "title":
            out.append(f"# {block[1]}\n")
        elif kind == "h2":
            out.append(f"\n## {block[1]}\n")
        elif kind == "h3":
            out.append(f"\n### {_md_line(block[1])}\n")
        elif kind == "facts":
            out.extend(f"- **{label}:** {_md_line(value)}" for label, value in block[1])
            out.append("")
        elif kind == "para":
            out.append(_md_escape(block[1]).strip() + "\n")
        elif kind == "note":
            out.append(f"*{_md_line(block[1])}*\n")
        elif kind == "bullets":
            out.extend(f"- {_md_line(item)}" for item in block[1])
            out.append("")
        elif kind == "quote":
            out.append(f"**{block[1]}:**\n")
            out.extend(("> " + line).rstrip() for line in _md_escape(block[2]).split("\n"))
            out.append("")
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------------------- pdf ----

_FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", None),
]


def find_unicode_font() -> Optional[tuple]:
    """(regular, bold) TrueType font paths, from REPORT_FONT_REGULAR/REPORT_FONT_BOLD or a known system font."""
    env_regular = os.environ.get("REPORT_FONT_REGULAR")
    if env_regular and os.path.exists(env_regular):
        env_bold = os.environ.get("REPORT_FONT_BOLD")
        return env_regular, env_bold if env_bold and os.path.exists(env_bold) else env_regular
    for regular, bold in _FONT_CANDIDATES:
        if os.path.exists(regular):
            return regular, bold if bold and os.path.exists(bold) else regular
    return None


class _ReportPDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font(self.report_font, "", 8)
        self.set_text_color(110, 110, 110)
        self.cell(0, 8, f"Page {self.page_no()} / {{nb}}", align="C")


def render_pdf(report: FeedbackReport) -> bytes:
    pdf = _ReportPDF(orientation="P", unit="mm", format="A4")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(f"Interview Feedback Report - interview {report.interview_id}")
    pdf.set_creator("Technical Interviewer")
    pdf.alias_nb_pages()

    font = find_unicode_font()
    if font:
        pdf.add_font("Report", "", font[0])
        pdf.add_font("Report", "B", font[1])
        pdf.report_font = "Report"
        clean = lambda text: _CONTROL_CHARS.sub("", str(text))  # noqa: E731
    else:  # core fonts only cover Latin-1: anything else is replaced rather than crashing
        pdf.report_font = "Helvetica"
        clean = lambda text: _CONTROL_CHARS.sub("", str(text)).encode("latin-1", "replace").decode("latin-1")  # noqa: E731
    pdf.add_page()

    def need_space(height: float) -> None:
        if pdf.get_y() + height > pdf.h - pdf.b_margin:
            pdf.add_page()

    def paragraph(text, size=10, bold=False, indent=0.0, color=(30, 30, 30), line=5.4, gap=1.5):
        pdf.set_font(pdf.report_font, "B" if bold else "", size)
        pdf.set_text_color(*color)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(pdf.epw - indent, line, clean(text), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(gap)

    for block in build_document(report):
        kind = block[0]
        if kind == "title":
            pdf.set_font(pdf.report_font, "B", 20)
            pdf.set_text_color(25, 35, 90)
            pdf.cell(0, 11, clean(block[1]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(2)
        elif kind == "h2":
            need_space(30)
            pdf.ln(3)
            paragraph(block[1], size=13, bold=True, color=(25, 35, 90), line=7, gap=0.5)
            pdf.set_draw_color(160, 170, 210)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(2.5)
        elif kind == "h3":
            need_space(24)
            paragraph(block[1], size=11, bold=True, color=(40, 40, 40), line=6, gap=1)
        elif kind == "facts":
            for label, value in block[1]:
                need_space(8)
                pdf.set_x(pdf.l_margin)
                pdf.set_font(pdf.report_font, "B", 10)
                pdf.set_text_color(30, 30, 30)
                pdf.write(5.6, clean(f"{label}: "))
                pdf.set_font(pdf.report_font, "", 10)
                pdf.write(5.6, clean(value))
                pdf.ln(5.6)
            pdf.ln(1.5)
        elif kind == "para":
            paragraph(block[1])
        elif kind == "note":
            paragraph(block[1], color=(100, 100, 100))
        elif kind == "bullets":
            for item in block[1]:
                need_space(10)
                pdf.set_font(pdf.report_font, "", 10)
                pdf.set_text_color(30, 30, 30)
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(pdf.epw - 3, 5.4, clean("-  " + item), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(0.8)
            pdf.ln(1)
        elif kind == "quote":
            need_space(14)
            paragraph(block[1], size=9, bold=True, color=(90, 90, 120), line=5, gap=0.3)
            paragraph(block[2], indent=5, color=(55, 55, 55), gap=2.5)

    return bytes(pdf.output())
