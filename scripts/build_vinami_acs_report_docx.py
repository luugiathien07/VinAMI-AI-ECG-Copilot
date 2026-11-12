"""Regenerate the VinAMI ACS Vietnamese product and architecture report as DOCX."""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "report" / "VinAMI_ACS_MVP_Report.md"
OUTPUT = ROOT / "docs" / "report" / "VinAMI_ACS_MVP_Report.docx"
TEAL = "007F86"
INK = "18363A"
MUTED = "60767A"


def set_cell_shading(cell, fill: str) -> None:
    """Apply a solid fill to a table cell."""
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def set_repeat_header(row) -> None:
    """Repeat the table header on subsequent pages."""
    properties = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    properties.append(header)


def set_cell_margins(cell, top: int = 80, start: int = 90, bottom: int = 80, end: int = 90) -> None:
    """Set compact cell margins in twips."""
    properties = cell._tc.get_or_add_tcPr()
    margins = properties.first_child_found_in("w:tcMar")
    if margins is None:
        margins = OxmlElement("w:tcMar")
        properties.append(margins)
    for side, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        element = margins.find(qn(f"w:{side}"))
        if element is None:
            element = OxmlElement(f"w:{side}")
            margins.append(element)
        element.set(qn("w:w"), str(value))
        element.set(qn("w:type"), "dxa")


def add_page_number(paragraph) -> None:
    """Insert a dynamic PAGE field into a footer paragraph."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, end])


def add_inline(paragraph, text: str) -> None:
    """Render simple bold Markdown fragments into a Word paragraph."""
    for fragment in re.split(r"(\*\*.*?\*\*)", text):
        if not fragment:
            continue
        bold = fragment.startswith("**") and fragment.endswith("**")
        run = paragraph.add_run(fragment[2:-2] if bold else fragment)
        run.bold = bold


def configure_document(document: Document) -> None:
    """Configure A4 page settings, styles, header, footer, and captions."""
    section = document.sections[0]
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(1.8)
    section.left_margin = Cm(2.1)
    section.right_margin = Cm(2.0)

    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    for name, size, color in (("Heading 1", 15, TEAL), ("Heading 2", 12.5, TEAL), ("Heading 3", 11, INK)):
        style = document.styles[name]
        style.font.name = "Times New Roman"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string(color)
        style.paragraph_format.space_before = Pt(14)
        style.paragraph_format.space_after = Pt(7)
        style.paragraph_format.keep_with_next = True

    if "Report Caption" not in document.styles:
        caption = document.styles.add_style("Report Caption", WD_STYLE_TYPE.PARAGRAPH)
        caption.font.name = "Times New Roman"
        caption.font.size = Pt(9)
        caption.font.italic = True
        caption.font.color.rgb = RGBColor.from_string(MUTED)
        caption.paragraph_format.space_before = Pt(5)
        caption.paragraph_format.space_after = Pt(9)

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = header.add_run("VinAMI ACS Operating System | MVP Product and Architecture Report")
    run.font.name = "Times New Roman"
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(MUTED)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("Research and engineering MVP | Demonstration rule - not for clinical use | Trang ")
    run.font.name = "Times New Roman"
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor.from_string(MUTED)
    add_page_number(footer)


def add_cover(document: Document) -> None:
    """Create the cover and a table-of-contents placeholder."""
    for _ in range(7):
        document.add_paragraph()
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("VinAMI ACS\nOperating System")
    run.font.name = "Times New Roman"
    run.font.size = Pt(28)
    run.font.bold = True
    run.font.color.rgb = RGBColor.from_string(TEAL)
    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = subtitle.add_run("Báo Cáo Sản Phẩm Và Kiến Trúc MVP\nSprint 1 | 13/07/2026")
    run.font.name = "Times New Roman"
    run.font.size = Pt(15)
    run.font.color.rgb = RGBColor.from_string(INK)
    notice = document.add_paragraph()
    notice.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = notice.add_run("Research and engineering MVP\nDemonstration rule - not for clinical use.")
    run.font.name = "Times New Roman"
    run.font.size = Pt(11)
    run.font.bold = True
    run.font.color.rgb = RGBColor.from_string("9A6400")
    document.add_page_break()
    document.add_paragraph("Mục lục", style="Heading 1")
    document.add_paragraph("Mở tài liệu trong Microsoft Word và chọn Update Field để tạo mục lục tự động từ Heading 1-3.")
    document.add_page_break()


def add_table(document: Document, rows: list[list[str]]) -> None:
    """Create a compact portrait-A4 table with repeating headers."""
    if not rows:
        return
    column_count = max(len(row) for row in rows)
    table = document.add_table(rows=0, cols=column_count)
    table.style = "Table Grid"
    for row_index, values in enumerate(rows):
        cells = table.add_row().cells
        for column in range(column_count):
            set_cell_margins(cells[column])
            value = values[column] if column < len(values) else ""
            paragraph = cells[column].paragraphs[0]
            paragraph.paragraph_format.space_after = Pt(0)
            run = paragraph.add_run(value)
            run.font.name = "Times New Roman"
            run.font.size = Pt(8.1 if column_count > 4 else 8.8)
            if row_index == 0:
                run.bold = True
                run.font.color.rgb = RGBColor(255, 255, 255)
                set_cell_shading(cells[column], TEAL)
        if row_index == 0:
            set_repeat_header(table.rows[0])
    document.add_paragraph()


def convert_markdown(document: Document, markdown: str) -> None:
    """Convert the report's small Markdown subset to styled DOCX content."""
    lines = markdown.splitlines()
    index = 0
    first_title = True
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("|") and line.endswith("|"):
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                candidate = lines[index].strip()
                cells = [cell.strip() for cell in candidate.strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells):
                    rows.append(cells)
                index += 1
            add_table(document, rows)
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            if level == 1 and first_title:
                first_title = False
            else:
                if level == 1 or (level == 2 and re.match(r"^\d+\.\s", title)):
                    document.add_page_break()
                document.add_paragraph(title, style={1: "Heading 1", 2: "Heading 2", 3: "Heading 3"}[level])
            index += 1
            continue
        if line.startswith(">"):
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.left_indent = Cm(0.5)
            run = paragraph.add_run(line.lstrip("> "))
            run.italic = True
            run.font.color.rgb = RGBColor.from_string("70531A")
            index += 1
            continue
        if line.startswith("[Hình "):
            document.add_paragraph(line.strip("[]"), style="Report Caption")
            index += 1
            continue
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        numbered = re.match(r"^\d+\.\s+(.*)$", line)
        if bullet or numbered:
            paragraph = document.add_paragraph(style="List Bullet" if bullet else "List Number")
            add_inline(paragraph, (bullet or numbered).group(1))
            index += 1
            continue
        paragraph = document.add_paragraph()
        add_inline(paragraph, line)
        index += 1


def main() -> None:
    """Generate the DOCX report from the canonical Markdown source."""
    if not SOURCE.exists():
        raise SystemExit(f"Missing report source: {SOURCE}")
    document = Document()
    configure_document(document)
    add_cover(document)
    convert_markdown(document, SOURCE.read_text(encoding="utf-8"))
    document.core_properties.title = "VinAMI ACS Operating System - MVP Product and Architecture Report"
    document.core_properties.subject = "Research and engineering MVP architecture report"
    document.core_properties.author = "VinAMI ACS Operating System"
    document.save(OUTPUT)
    print(f"Created: {OUTPUT}")


if __name__ == "__main__":
    main()

