"""Convert the Markdown evaluation report and evidence index into formatted Word (.docx) and PDF files.

Content is converted verbatim: text, numbers, statuses and tables are not reworded or reordered. Only presentation is added (fonts, heading
styles, table shading, status colouring, page numbers). Makes no network or paid calls and does not touch the source Markdown files, the
database, results or archives.

Needs: python-docx (pip install python-docx) and fpdf2 (already in the backend requirements). If python-docx is installed somewhere other
than the active environment, set PYTHON_DOCX_PATH to that folder.

Run from the repository root:   backend\\venv\\Scripts\\python.exe evaluation\\make_client_documents.py
Output: evaluation/client_documents/{FINAL_CLIENT_EVALUATION_REPORT,EVIDENCE_INDEX}.{docx,pdf}
Check:  backend\\venv\\Scripts\\python.exe evaluation\\validate_client_documents.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "client_documents")
if os.environ.get("PYTHON_DOCX_PATH"):
    sys.path.insert(0, os.environ["PYTHON_DOCX_PATH"])

SOURCES = {"FINAL_CLIENT_EVALUATION_REPORT": ("Technical Interviewer - Final Evaluation Report", "Client submission document"),
           "EVIDENCE_INDEX": ("Technical Interviewer - Evidence Index", "Companion to the Final Evaluation Report")}
NAVY, GREY_FILL, CODE_FILL = (0x1F, 0x38, 0x64), "D9E2F3", "F2F2F2"
STATUS_COLOURS = [("NOT VERIFIED", (0xC0, 0x00, 0x00)), ("PARTIALLY VERIFIED", (0xB4, 0x5F, 0x06)), ("VERIFIED", (0x2E, 0x7D, 0x32))]


# ------------------------------------------------------------------ markdown subset parser
def parse(md):
    lines, i, blocks = md.splitlines(), 0, []
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1
        elif ln.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", code))
        elif re.match(r"^#{1,3} ", ln):
            level = len(ln) - len(ln.lstrip("#"))
            blocks.append(("h", level, ln[level + 1:].strip()))
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            blocks.append(("table", rows))
        elif re.match(r"^\d+\. ", ln):
            items = []
            while i < len(lines) and re.match(r"^\d+\. ", lines[i]):
                items.append(lines[i]); i += 1
            blocks.append(("olist", items))
        elif ln.startswith("> "):
            blocks.append(("quote", ln[2:].strip())); i += 1
        else:
            para = [ln.strip()]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#{1,3} |\||```|\d+\. |> )", lines[i]):
                para.append(lines[i].strip()); i += 1
            blocks.append(("p", " ".join(para)))
    return blocks


def spans(text):
    """Split inline markdown into (text, bold, code) segments."""
    out = []
    for part in re.split(r"(\*\*.+?\*\*|`.+?`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append((part[2:-2], True, False))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append((part[1:-1], False, True))
        else:
            out.append((part, False, False))
    return out


def plain(text):
    return "".join(t for t, _, _ in spans(text))


def status_colour(text):
    for key, rgb in STATUS_COLOURS:
        if key in text:
            return rgb
    return None


def col_weights(rows):
    n = max(len(r) for r in rows)
    weights = []
    for c in range(n):
        cells = [plain(r[c]) if c < len(r) else "" for r in rows]
        longest = max(len(x) for x in cells)
        longest_word = max((len(w) for x in cells for w in x.split()), default=0)
        # wide enough for the longest word (so 'recommendation' or 'question_bank' is not split), proportional to the text otherwise
        weights.append(max(min(max(longest, 6), 70) + 8, min(longest_word, 30) * 1.15 + 5))
    return weights


# ------------------------------------------------------------------ DOCX
def build_docx(blocks, title, subtitle, path):
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = Cm(1.9)
    sec.top_margin, sec.bottom_margin = Cm(2.0), Cm(2.0)
    usable_cm = 21.0 - 3.8
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), "Calibri")
    normal.paragraph_format.space_after, normal.paragraph_format.line_spacing = Pt(6), 1.12
    for name, size in (("Heading 1", 20), ("Heading 2", 15), ("Heading 3", 12)):
        st = doc.styles[name]
        st.font.name, st.font.size, st.font.bold = "Calibri", Pt(size), True
        st.font.color.rgb = RGBColor(*NAVY)
        st.element.rPr.rFonts.set(qn("w:ascii"), "Calibri"); st.element.rPr.rFonts.set(qn("w:hAnsi"), "Calibri")
        st.paragraph_format.space_before, st.paragraph_format.space_after, st.paragraph_format.keep_with_next = Pt(14 if name != "Heading 3" else 10), Pt(6), True

    def shade(cell, fill):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
        tcPr.append(shd)

    def add_runs(par, text, size=None, bold_all=False, colour=None):
        for t, bold, code in spans(text):
            run = par.add_run(t)
            run.bold = bold or bold_all
            if code:
                run.font.name, run.font.size = "Consolas", Pt((size or 10.5) - 1)
                run._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            elif size:
                run.font.size = Pt(size)
            if colour:
                run.font.color.rgb = RGBColor(*colour)

    def field(par, code):
        run = par.add_run()
        for kind, txt in (("begin", None), (None, code), ("end", None)):
            if kind:
                el = OxmlElement("w:fldChar"); el.set(qn("w:fldCharType"), kind)
            else:
                el = OxmlElement("w:instrText"); el.set(qn("xml:space"), "preserve"); el.text = txt
            run._r.append(el)

    # footer: page x of y
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fp.add_run(f"{title}   |   Page ").font.size = Pt(8.5)
    field(fp, "PAGE")
    fp.add_run(" of ").font.size = Pt(8.5)
    field(fp, "NUMPAGES")
    for r in fp.runs:
        r.font.size = Pt(8.5)

    first_h1 = True
    for b in blocks:
        kind = b[0]
        if kind == "h":
            level, text = b[1], b[2]
            if level == 1 and first_h1:
                first_h1 = False
                p = doc.add_paragraph(style="Title")
                p.add_run(title).font.color.rgb = RGBColor(*NAVY)
                p.runs[0].font.size, p.runs[0].font.name = Pt(24), "Calibri"
                sp = doc.add_paragraph(); r = sp.add_run(subtitle); r.italic = True; r.font.size = Pt(11)
                continue
            doc.add_heading(plain(text), level=level)
        elif kind == "p":
            add_runs(doc.add_paragraph(), b[1])
        elif kind == "quote":
            p = doc.add_paragraph(); p.paragraph_format.left_indent = Cm(0.8)
            add_runs(p, b[1])
        elif kind == "olist":
            for item in b[1]:
                p = doc.add_paragraph(style="List Number") if False else doc.add_paragraph()
                p.paragraph_format.left_indent, p.paragraph_format.first_line_indent = Cm(0.9), Cm(-0.6)
                add_runs(p, item)
        elif kind == "code":
            for ln in b[1]:
                p = doc.add_paragraph()
                p.paragraph_format.space_after, p.paragraph_format.left_indent = Pt(0), Cm(0.3)
                r = p.add_run(ln or " "); r.font.name, r.font.size = "Consolas", Pt(8)
                r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
                pPr = p._p.get_or_add_pPr(); shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), CODE_FILL); pPr.append(shd)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
        elif kind == "table":
            rows = b[1]
            ncol = max(len(r) for r in rows)
            size = 8.5 if ncol <= 5 else 7.5 if ncol <= 7 else 7
            weights = col_weights(rows)
            total = sum(weights)
            table = doc.add_table(rows=len(rows), cols=ncol)
            table.style, table.alignment, table.autofit = "Table Grid", WD_TABLE_ALIGNMENT.CENTER, False
            tblPr = table._tbl.tblPr
            layout = OxmlElement("w:tblLayout"); layout.set(qn("w:type"), "fixed"); tblPr.append(layout)
            for ri, row in enumerate(rows):
                tr = table.rows[ri]._tr
                trPr = tr.get_or_add_trPr()
                cs = OxmlElement("w:cantSplit"); trPr.append(cs)
                if ri == 0:
                    th = OxmlElement("w:tblHeader"); trPr.append(th)
                for ci in range(ncol):
                    cell = table.cell(ri, ci)
                    cell.width = Cm(usable_cm * weights[ci] / total)
                    text = row[ci] if ci < len(row) else ""
                    par = cell.paragraphs[0]
                    par.paragraph_format.space_after, par.paragraph_format.line_spacing = Pt(1), 1.0
                    add_runs(par, text, size=size, bold_all=(ri == 0), colour=status_colour(text) if ri else None)
                    if ri == 0:
                        shade(cell, GREY_FILL)
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
    doc.core_properties.title, doc.core_properties.subject = title, subtitle
    doc.save(path)


# ------------------------------------------------------------------ PDF
def build_pdf(blocks, title, subtitle, path):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    fonts = r"C:\Windows\Fonts"
    use_calibri = os.path.exists(os.path.join(fonts, "calibri.ttf"))

    class Pdf(FPDF):
        def footer(self):
            self.set_y(-12)
            self.set_font("Body", "", 8)
            self.set_text_color(90, 90, 90)
            self.cell(0, 6, f"{title}   |   Page {self.page_no()} of {{nb}}", align="C")

    pdf = Pdf(format="A4")
    pdf.alias_nb_pages()
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=True, margin=18)
    if use_calibri:
        pdf.add_font("Body", "", os.path.join(fonts, "calibri.ttf"))
        pdf.add_font("Body", "B", os.path.join(fonts, "calibrib.ttf"))
        pdf.add_font("Body", "I", os.path.join(fonts, "calibrii.ttf"))
        pdf.add_font("Body", "BI", os.path.join(fonts, "calibriz.ttf"))
    else:
        pdf.add_font("Body", "", os.path.join(fonts, "arial.ttf")); pdf.add_font("Body", "B", os.path.join(fonts, "arialbd.ttf"))
        pdf.add_font("Body", "I", os.path.join(fonts, "ariali.ttf")); pdf.add_font("Body", "BI", os.path.join(fonts, "arialbi.ttf"))
    pdf.add_font("Mono", "", os.path.join(fonts, "consola.ttf"))
    pdf.add_font("Mono", "B", os.path.join(fonts, "consolab.ttf"))
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def write_inline(text, size, lh):
        for t, bold, code in spans(text):
            if code:
                pdf.set_font("Mono", "", size - 1)
            else:
                pdf.set_font("Body", "B" if bold else "", size)
            pdf.write(lh, t)
        pdf.ln(lh)

    first_h1 = True
    for b in blocks:
        kind = b[0]
        pdf.set_x(pdf.l_margin)
        if kind == "h":
            level, text = b[1], b[2]
            if level == 1 and first_h1:
                first_h1 = False
                pdf.set_text_color(*NAVY); pdf.set_font("Body", "B", 22)
                pdf.multi_cell(width, 10, title, new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(90, 90, 90); pdf.set_font("Body", "I", 11)
                pdf.multi_cell(width, 6, subtitle, new_x="LMARGIN", new_y="NEXT")
                pdf.set_draw_color(*NAVY); pdf.set_line_width(0.6); pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.l_margin + width, pdf.get_y() + 2)
                pdf.ln(7); pdf.set_text_color(0, 0, 0)
                continue
            size, gap = {1: (17, 6), 2: (14, 5), 3: (11.5, 4)}[level]
            if pdf.get_y() > pdf.h - 45:      # keep a heading with the text that follows it
                pdf.add_page()
            pdf.ln(gap)
            pdf.set_text_color(*NAVY); pdf.set_font("Body", "B", size)
            pdf.multi_cell(width, size * 0.5, plain(text), new_x="LMARGIN", new_y="NEXT")
            if level == 2:
                pdf.set_draw_color(*NAVY); pdf.set_line_width(0.25); pdf.line(pdf.l_margin, pdf.get_y() + 0.5, pdf.l_margin + width, pdf.get_y() + 0.5)
            pdf.ln(2.5); pdf.set_text_color(0, 0, 0)
        elif kind in ("p", "quote"):
            if kind == "quote":
                pdf.set_x(pdf.l_margin + 5)
            write_inline(b[1], 10, 5.0)
            pdf.ln(1.8)
        elif kind == "olist":
            for item in b[1]:
                pdf.set_x(pdf.l_margin + 3)
                write_inline(item, 10, 5.0)
                pdf.ln(1)
            pdf.ln(1)
        elif kind == "code":
            pdf.set_font("Mono", "", 7.6)
            pdf.set_fill_color(242, 242, 242)
            for ln in b[1]:
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(width, 4.1, ln or " ", fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
        elif kind == "table":
            rows = b[1]
            ncol = max(len(r) for r in rows)
            size = 8.2 if ncol <= 5 else 7.4 if ncol <= 7 else 6.8
            weights = col_weights(rows)
            total = sum(weights)
            widths = [width * w / total for w in weights]
            pdf.set_font("Body", "", size)
            with pdf.table(col_widths=tuple(widths), width=width, text_align="LEFT", line_height=size * 0.46 + 0.6, repeat_headings=1,
                           headings_style=FontFace(emphasis="BOLD", fill_color=(217, 226, 243)), borders_layout="ALL", padding=1.2) as table:
                for ri, row in enumerate(rows):
                    r = table.row()
                    for ci in range(ncol):
                        text = plain(row[ci]) if ci < len(row) else ""
                        colour = status_colour(row[ci]) if (ri and ci < len(row)) else None
                        r.cell(text, style=FontFace(color=colour) if colour else None)
            pdf.ln(4)
    pdf.set_title(title)
    pdf.output(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    for stem, (title, subtitle) in SOURCES.items():
        src = os.path.join(HERE, f"{stem}.md")
        with open(src, encoding="utf-8") as f:
            blocks = parse(f.read())
        build_docx(blocks, title, subtitle, os.path.join(OUT, f"{stem}.docx"))
        build_pdf(blocks, title, subtitle, os.path.join(OUT, f"{stem}.pdf"))
        print("wrote", os.path.join(OUT, f"{stem}.docx"), "and .pdf")


if __name__ == "__main__":
    main()
