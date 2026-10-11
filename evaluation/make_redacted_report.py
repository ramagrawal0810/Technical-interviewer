"""Create the public (redacted) copy of the client evaluation report for the repository.

The only change to the content is removal of the human reviewer's written override reason (private human-review evidence). Everything else, including the
status of all four requirements, is unchanged. The ORIGINAL Markdown, DOCX and PDF files are never modified (their hashes are checked before and after).

Output folder: evaluation/client_documents_public/
  FINAL_CLIENT_EVALUATION_REPORT_REDACTED.{md,docx,pdf}   redacted report
  EVIDENCE_INDEX.{docx,pdf}                               verbatim copies of the evidence index (it contains no reviewer text)

Run from the repository root:   backend\\venv\\Scripts\\python.exe evaluation\\make_redacted_report.py
(needs python-docx; set PYTHON_DOCX_PATH if it is installed outside the active environment)
"""

import hashlib
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_client_documents as mk  # noqa: E402

SRC_MD = os.path.join(HERE, "FINAL_CLIENT_EVALUATION_REPORT.md")
ORIG_DIR = os.path.join(HERE, "client_documents")
OUT = os.path.join(HERE, "client_documents_public")
# The reason text is NOT written in this script (the script itself is committed): it is read from the report's table cell at run time.
REASON_CELL = re.compile(r'\| Overrode the AI recommendation \| "([^"|]+)" \|')
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def redact(md):
    """Returns (redacted_text, reason_text). The reason is returned only so the caller can verify it is gone from every output."""
    out = md
    # 1. the quoted reason in the table cell
    found = REASON_CELL.findall(out)
    assert len(found) == 1, "override reason cell not found exactly once"
    reason = found[0]
    cell = f'"{reason}"'
    assert out.count(cell) == 1
    out = out.replace(cell, "[withheld: the reviewer's written reason is not included in this public copy]")
    # 2. narrative sentence that refers to the quote
    old = ("giving the reason quoted above (the reviewer's own words, including spelling, are reproduced exactly).")
    assert out.count(old) == 1, "narrative sentence not found exactly once"
    out = out.replace(old, "with a written reason that is withheld from this public copy.")
    # 3. note at the top
    marker = "## Status of the four requirements"
    assert out.count(marker) == 1
    note = ("> **Redacted public copy.** The human reviewer's written override reason has been removed from this version. "
            "Nothing else was changed: statuses, results and limitations are identical to the original internal report.\n\n")
    out = out.replace(marker, note + marker)
    assert reason not in out and not KEY_PATTERN.search(out), "redacted text still contains forbidden content"
    return out, reason


def docx_text(path):
    return re.sub(r"<[^>]+>", " ", zipfile.ZipFile(path).read("word/document.xml").decode("utf-8"))


def pdf_text(path):
    from pypdf import PdfReader
    return " ".join((p.extract_text() or "") for p in PdfReader(path).pages)


def main():
    originals = [SRC_MD] + [os.path.join(ORIG_DIR, f) for f in sorted(os.listdir(ORIG_DIR))]
    before = {p: sha(p) for p in originals}
    os.makedirs(OUT, exist_ok=True)

    redacted, reason = redact(open(SRC_MD, encoding="utf-8").read())
    FORBIDDEN = re.compile("|".join([re.escape(" ".join(reason.split())), KEY_PATTERN.pattern]), re.I)   # compared on whitespace-collapsed text
    stem = "FINAL_CLIENT_EVALUATION_REPORT_REDACTED"
    md_path = os.path.join(OUT, stem + ".md")
    open(md_path, "w", encoding="utf-8", newline="\n").write(redacted)

    blocks = mk.parse(redacted)
    title, subtitle = "Technical Interviewer - Final Evaluation Report (redacted public copy)", "Client submission document - human reviewer's written reason withheld"
    mk.build_docx(blocks, title, subtitle, os.path.join(OUT, stem + ".docx"))
    mk.build_pdf(blocks, title, subtitle, os.path.join(OUT, stem + ".pdf"))
    for name in ("EVIDENCE_INDEX.docx", "EVIDENCE_INDEX.pdf"):
        shutil.copy2(os.path.join(ORIG_DIR, name), os.path.join(OUT, name))

    # ---------- validation
    import validate_client_documents as v
    v.OUT = OUT
    plain = mk.plain(" ".join(
        (b[2] if b[0] == "h" else b[1] if b[0] in ("p", "quote") else " ".join(b[1]) if b[0] in ("olist", "code") else " ".join(c for r in b[1] for c in r))
        for b in blocks))
    pdf = v.check_pdf(stem, plain)
    n_tables = sum(1 for b in blocks if b[0] == "table")
    n_heads = sum(1 for b in blocks if b[0] == "h")
    docx = v.check_docx(stem, plain, n_tables, n_heads)
    norm = lambda t: " ".join(t.split())  # noqa: E731
    leaks = {name: len(FORBIDDEN.findall(norm(txt))) for name, txt in (
        ("md", redacted), ("docx", docx_text(os.path.join(OUT, stem + ".docx"))), ("pdf", pdf_text(os.path.join(OUT, stem + ".pdf"))),
        ("EVIDENCE_INDEX.docx", docx_text(os.path.join(OUT, "EVIDENCE_INDEX.docx"))), ("EVIDENCE_INDEX.pdf", pdf_text(os.path.join(OUT, "EVIDENCE_INDEX.pdf"))))}
    statuses_ok = all(s in redacted for s in ("**PARTIALLY VERIFIED**", "**VERIFIED within its stated scope**", "**NOT VERIFIED**"))
    unchanged = {os.path.relpath(p, HERE): sha(p) == h for p, h in before.items()}

    print(f"redacted PDF : {pdf['pages']} pages, word coverage {pdf['coverage']:.4f}, problems: {pdf['problems'] or 'none'}")
    print(f"redacted DOCX: {docx['headings']} headings, {docx['tables']} tables, word coverage {docx['coverage']:.4f}, problems: {docx['problems'] or 'none'}")
    print("forbidden-content hits per output (must all be 0):", leaks)
    print("requirement statuses still present in redacted text:", statuses_ok)
    print("original files unchanged:", all(unchanged.values()), unchanged)
    ok = (not pdf["problems"] and not docx["problems"] and not any(leaks.values()) and statuses_ok and all(unchanged.values()) and docx["coverage"] > 0.999 and pdf["coverage"] > 0.995)
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
