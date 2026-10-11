"""Check the generated client documents against their Markdown sources. Read-only; no network.

For each PDF: opens without error, no blank pages, every word of the source is present, no text runs past the page margins (approximate, from
text positions), page count. For each DOCX: zip integrity, every word present, heading/table counts equal the source, every table fits the
page width, page-number fields present. A DOCX cannot be paginated without Word or LibreOffice, so its page count and visual layout are NOT checked.

Run from the repository root:   backend\\venv\\Scripts\\python.exe evaluation\\validate_client_documents.py
"""

import os
import re
import sys
import zipfile
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "client_documents")
if os.environ.get("PYTHON_DOCX_PATH"):
    sys.path.insert(0, os.environ["PYTHON_DOCX_PATH"])
sys.path.insert(0, HERE)
import make_client_documents as mk  # noqa: E402


def tokens(text):
    return Counter(re.findall(r"\w+", text.lower()))


def source_info(stem):
    md = open(os.path.join(HERE, f"{stem}.md"), encoding="utf-8").read()
    blocks = mk.parse(md)
    text = []
    for b in blocks:
        if b[0] == "h":
            text.append(b[2])
        elif b[0] in ("p", "quote"):
            text.append(b[1])
        elif b[0] == "olist":
            text += b[1]
        elif b[0] == "code":
            text += b[1]
        elif b[0] == "table":
            text += [c for row in b[1] for c in row]
    plain = mk.plain(" ".join(text))
    return blocks, plain, sum(1 for b in blocks if b[0] == "table"), sum(1 for b in blocks if b[0] == "h")


def coverage(src_tokens, doc_tokens):
    """Share of source word occurrences found in the document, and the missing words. A word split by a hard wrap counts as found if its pieces exist."""
    missing = Counter()
    for w, n in src_tokens.items():
        if doc_tokens.get(w, 0) < n:
            missing[w] = n - doc_tokens.get(w, 0)
    total = sum(src_tokens.values())
    return 1 - sum(missing.values()) / total, missing


def check_pdf_exact(stem, plain):
    """Exact check with PyMuPDF (word boxes, per-page text) when it is available."""
    try:
        if os.environ.get("PYMUPDF_PATH"):
            sys.path.insert(0, os.environ["PYMUPDF_PATH"])
        import pymupdf
    except ImportError:
        return None
    doc = pymupdf.open(os.path.join(OUT, f"{stem}.pdf"))
    problems, words_all, extents = [], [], []
    for n, page in enumerate(doc, 1):
        words = page.get_text("words")
        if len(words) < 20:
            problems.append(f"page {n} looks blank ({len(words)} words)")
            continue
        x0, x1 = min(w[0] for w in words), max(w[2] for w in words)
        extents.append((x0, x1))
        if x0 < 40 or x1 > page.rect.width - 40:
            problems.append(f"page {n}: text reaches x {x0:.0f}..{x1:.0f} of {page.rect.width:.0f} pt (outside the margins)")
        words_all += [w[4] for w in words]
    glued = "".join(words_all).lower()
    cov, missing = coverage(tokens(plain), tokens(" ".join(words_all)))
    # a long token (file path) split by a hard wrap is still present if its characters appear contiguously
    still_missing = {w: n for w, n in missing.items() if w not in glued}
    return {"pages": len(doc), "coverage": 1 - sum(still_missing.values()) / sum(tokens(plain).values()), "missing": still_missing, "problems": problems,
            "footer_numbers": all(f"Page {i} of {len(doc)}" in " ".join(p.get_text().split()) for i, p in enumerate(doc, 1)),
            "x_range": (min(e[0] for e in extents), max(e[1] for e in extents)), "method": "PyMuPDF word boxes (exact)"}


def check_pdf(stem, plain):
    exact = check_pdf_exact(stem, plain)
    if exact:
        return exact
    from pypdf import PdfReader
    path = os.path.join(OUT, f"{stem}.pdf")
    reader = PdfReader(path)
    problems, texts, overflow = [], [], 0
    for n, page in enumerate(reader.pages, 1):
        extents = []

        def visit(text, cm, tm, font_dict, font_size, extents=extents):
            if text.strip():
                x = tm[4] * cm[0] + cm[4]
                extents.append((x, x + 0.5 * (font_size or 8) * len(text)))
        text = page.extract_text(visitor_text=visit) or ""
        texts.append(text)
        width = float(page.mediabox.width)
        if len(text.strip()) < 60:
            problems.append(f"page {n} looks blank ({len(text.strip())} characters)")
        if extents and (min(e[0] for e in extents) < 30 or max(e[1] for e in extents) > width - 20):
            overflow += 1
            problems.append(f"page {n}: text may reach the page edge (x {min(e[0] for e in extents):.0f}..{max(e[1] for e in extents):.0f} of {width:.0f} pt)")
    cov, missing = coverage(tokens(plain), tokens(" ".join(texts)))
    problems = [p for p in problems if "reach the page edge" not in p] + [p + " [approximate estimate; install pymupdf for an exact check]" for p in problems if "reach the page edge" in p]
    return {"pages": len(reader.pages), "coverage": cov, "missing": missing, "problems": problems, "method": "pypdf (approximate)",
            "footer_numbers": all(f"Page {n} of {len(reader.pages)}" in re.sub(r"\s+", " ", t) for n, t in enumerate(texts, 1))}


def check_docx(stem, plain, n_tables, n_headings):
    from docx import Document
    path = os.path.join(OUT, f"{stem}.docx")
    problems = []
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            problems.append(f"corrupt zip member {bad}")
        footer_xml = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("word/footer"))
    doc = Document(path)
    texts = [p.text for p in doc.paragraphs] + [c.text for t in doc.tables for row in t.rows for c in row.cells]
    cov, missing = coverage(tokens(plain), tokens(" ".join(texts)))
    heads = sum(1 for p in doc.paragraphs if p.style.name.startswith("Heading") or p.style.name == "Title")
    usable = doc.sections[0].page_width - doc.sections[0].left_margin - doc.sections[0].right_margin
    widest = 0
    for t in doc.tables:
        w = sum(c.width for c in t.rows[0].cells)
        widest = max(widest, w / usable)
    if len(doc.tables) != n_tables:
        problems.append(f"{len(doc.tables)} tables, source has {n_tables}")
    if heads != n_headings:
        problems.append(f"{heads} headings/title, source has {n_headings}")
    if widest > 1.02:
        problems.append(f"a table is wider than the page ({widest:.2f}x)")
    if "PAGE" not in footer_xml or "NUMPAGES" not in footer_xml:
        problems.append("page-number fields missing from the footer")
    empties = sum(1 for a, b in zip(doc.paragraphs, doc.paragraphs[1:]) if not a.text.strip() and not b.text.strip() and not a.runs and not b.runs)
    return {"tables": len(doc.tables), "headings": heads, "coverage": cov, "missing": missing, "problems": problems, "widest_table_vs_page": round(widest, 3), "consecutive_empty_paragraphs": empties}


def main():
    ok = True
    for stem in mk.SOURCES:
        blocks, plain, n_tables, n_heads = source_info(stem)
        pdf, docx = check_pdf(stem, plain), check_docx(stem, plain, n_tables, n_heads)
        print(f"\n== {stem} (source: {n_heads} headings, {n_tables} tables)")
        print(f"  PDF : [{pdf['method']}] {pdf['pages']} pages | word coverage {pdf['coverage']:.4f} | page numbers in footer: {pdf['footer_numbers']} | problems: {pdf['problems'] or 'none'}")
        if pdf["missing"]:
            print("        words not found (first 12):", dict(list(pdf["missing"].items())[:12]))
        print(f"  DOCX: {docx['headings']} headings, {docx['tables']} tables | word coverage {docx['coverage']:.4f} | widest table = {docx['widest_table_vs_page']}x text width | problems: {docx['problems'] or 'none'}")
        if docx["missing"]:
            print("        words not found (first 12):", dict(list(docx["missing"].items())[:12]))
        ok &= not pdf["problems"] and not docx["problems"] and pdf["coverage"] > 0.995 and docx["coverage"] > 0.999 and pdf["footer_numbers"]
    print("\nOVERALL:", "PASS" if ok else "ISSUES FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
