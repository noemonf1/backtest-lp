"""Build docs/guide/GUIDE.docx from docs/guide/GUIDE.md with python-docx.

Markdown subset handled: #/##/### headings (numbered as written in the md),
paragraphs, `- ` bullets, `1. ` numbered items, pipe tables (bordered, header
row bold), fenced code blocks (monospace, shaded), images `![caption](path)`
(embedded at up to 16 cm, caption below in italics, numbered Figure n),
inline **bold**, *italic*, `code`. Page numbers in the footer. Title page.
At the end the file is reopened and paragraph / table / image counts printed.
"""
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

GUIDE_DIR = Path("/home/user/backtest-lp/docs/guide")
MD = GUIDE_DIR / "GUIDE.md"
OUT = GUIDE_DIR / "GUIDE.docx"

INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)")


def add_runs(par, text, base_bold=False, size=None):
    for tok in INLINE.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**"):
            r = par.add_run(tok[2:-2])
            r.bold = True
        elif tok.startswith("`") and tok.endswith("`"):
            r = par.add_run(tok[1:-1])
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(9)
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            r = par.add_run(tok[1:-1])
            r.italic = True
        else:
            r = par.add_run(tok)
            if base_bold:
                r.bold = True
        if size is not None:
            r.font.size = Pt(size)


def set_cell_borders(table):
    tbl = table._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "808080")
        borders.append(el)
    tblPr.append(borders)


def shade(par, fill="F2F2F2"):
    pPr = par._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    pPr.append(shd)


def add_page_number(section):
    footer = section.footer
    par = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    par.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = par.add_run()
    for tag, text in (("begin", None), (None, "PAGE"), ("end", None)):
        if tag:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), tag)
        else:
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = text
        run._r.append(el)


def main():
    lines = MD.read_text().splitlines()
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for s in doc.sections:
        s.left_margin = s.right_margin = Cm(2.0)
        s.top_margin = s.bottom_margin = Cm(2.0)
        add_page_number(s)

    fig_n = 0
    tab_n = 0
    i = 0
    first_h1 = True
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            code = []
            while j < len(lines) and not lines[j].startswith("```"):
                code.append(lines[j])
                j += 1
            par = doc.add_paragraph()
            shade(par)
            r = par.add_run("\n".join(code))
            r.font.name = "Consolas"
            r._element.rPr.rFonts.set(qn("w:eastAsia"), "Consolas")
            r.font.size = Pt(8.5)
            par.paragraph_format.space_after = Pt(6)
            i = j + 1
            continue
        m = re.match(r"^(#{1,4})\s+(.*)", ln)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level == 1 and first_h1:
                t = doc.add_paragraph()
                t.alignment = WD_ALIGN_PARAGRAPH.CENTER
                r = t.add_run(text)
                r.bold = True
                r.font.size = Pt(24)
                first_h1 = False
            else:
                h = doc.add_heading(level=min(level, 3))
                add_runs(h, text)
            i += 1
            continue
        m = re.match(r"^!\[(.*?)\]\((.*?)\)", ln)
        if m:
            cap, rel = m.group(1), m.group(2)
            path = (GUIDE_DIR / rel).resolve()
            if path.exists():
                fig_n += 1
                doc.add_picture(str(path), width=Cm(16))
                doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
                c = doc.add_paragraph()
                c.alignment = WD_ALIGN_PARAGRAPH.CENTER
                add_runs(c, f"Figure {fig_n}. {cap}", size=9)
                c.runs[0].italic = True
            else:
                print("MISSING IMAGE", rel, file=sys.stderr)
                doc.add_paragraph(f"[missing image {rel}]")
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.match(r"^:?-{2,}:?$", c) for c in cells):
                    rows.append(cells)
                i += 1
            ncol = max(len(r) for r in rows)
            tab_n += 1
            table = doc.add_table(rows=len(rows), cols=ncol)
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            set_cell_borders(table)
            for ri, row in enumerate(rows):
                for ci in range(ncol):
                    cell = table.cell(ri, ci)
                    cell.text = ""
                    p = cell.paragraphs[0]
                    add_runs(p, row[ci] if ci < len(row) else "", base_bold=(ri == 0), size=8.5)
            doc.add_paragraph()
            continue
        m = re.match(r"^\s*[-*]\s+(.*)", ln)
        if m:
            par = doc.add_paragraph(style="List Bullet")
            add_runs(par, m.group(1))
            i += 1
            continue
        m = re.match(r"^\s*\d+\.\s+(.*)", ln)
        if m:
            par = doc.add_paragraph(style="List Number")
            add_runs(par, m.group(1))
            i += 1
            continue
        if ln.strip() == "":
            i += 1
            continue
        if ln.strip() == "---":
            i += 1
            continue
        # paragraph: join consecutive non-empty, non-special lines
        buf = [ln]
        j = i + 1
        while j < len(lines) and lines[j].strip() and not re.match(r"^(#|\||```|!\[|\s*[-*]\s|\s*\d+\.\s|---)", lines[j]):
            buf.append(lines[j])
            j += 1
        par = doc.add_paragraph()
        add_runs(par, " ".join(b.strip() for b in buf))
        par.paragraph_format.space_after = Pt(6)
        i = j

    doc.save(OUT)
    d2 = Document(str(OUT))
    n_par = len(d2.paragraphs)
    n_tab = len(d2.tables)
    n_img = len(d2.inline_shapes)
    print(f"GUIDE.docx: paragraphs={n_par} tables={n_tab} images={n_img} bytes={OUT.stat().st_size}")


if __name__ == "__main__":
    main()
