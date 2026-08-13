#!/usr/bin/env python3
"""Generate the private CensusChat interview-preparation manual.

The document is intentionally built from repository facts rather than from the
running model. It is a stable coaching artifact, not an eval result and not a
replacement for the reviewer-facing README.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "output" / "pdf" / "censuschat-interview-manual.pdf"
UI_SCREENSHOT = ROOT / "docs" / "assets" / "censuschat-reviewer-ui.png"

APP_SNAPSHOT = "606e35b"
VERIFIED_DATE = "2026-08-12"
OFFLINE_TESTS = "449"
DEPLOYED_URL = "https://censuschat.brianmar.com"

NAVY = colors.HexColor("#0F2440")
INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#5E6B78")
BLUE = colors.HexColor("#2563EB")
SNOW = colors.HexColor("#29B5E8")
PALE_BLUE = colors.HexColor("#EAF3FF")
PALE_CYAN = colors.HexColor("#EAFBFF")
PALE_GREEN = colors.HexColor("#EAF8F0")
GREEN = colors.HexColor("#16794A")
PALE_AMBER = colors.HexColor("#FFF6DE")
AMBER = colors.HexColor("#B26A00")
PALE_RED = colors.HexColor("#FDECEC")
RED = colors.HexColor("#B42318")
PAPER = colors.HexColor("#FFFFFF")
SOFT = colors.HexColor("#F5F7FA")
BORDER = colors.HexColor("#D8E0E8")

PAGE_W, PAGE_H = letter
LEFT = 0.62 * inch
RIGHT = 0.62 * inch
TOP = 0.58 * inch
BOTTOM = 0.58 * inch
CONTENT_W = PAGE_W - LEFT - RIGHT


BASE = getSampleStyleSheet()
STYLES = {
    "cover_eyebrow": ParagraphStyle(
        "cover_eyebrow",
        parent=BASE["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=SNOW,
        tracking=1.6,
        spaceAfter=10,
    ),
    "cover_title": ParagraphStyle(
        "cover_title",
        parent=BASE["Title"],
        fontName="Helvetica-Bold",
        fontSize=34,
        leading=37,
        textColor=PAPER,
        alignment=TA_LEFT,
        spaceAfter=14,
    ),
    "cover_subtitle": ParagraphStyle(
        "cover_subtitle",
        parent=BASE["Normal"],
        fontName="Helvetica",
        fontSize=13,
        leading=18,
        textColor=colors.HexColor("#D9E7F5"),
        spaceAfter=18,
    ),
    "section_no": ParagraphStyle(
        "section_no",
        parent=BASE["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=9,
        textColor=BLUE,
        tracking=1.8,
        spaceAfter=4,
    ),
    "section_title": ParagraphStyle(
        "section_title",
        parent=BASE["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=25,
        textColor=NAVY,
        spaceBefore=0,
        spaceAfter=5,
    ),
    "section_kicker": ParagraphStyle(
        "section_kicker",
        parent=BASE["Normal"],
        fontName="Helvetica",
        fontSize=9.2,
        leading=12.5,
        textColor=MUTED,
        spaceAfter=12,
    ),
    "h2": ParagraphStyle(
        "h2",
        parent=BASE["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=12.2,
        leading=14.5,
        textColor=NAVY,
        spaceBefore=8,
        spaceAfter=5,
    ),
    "h3": ParagraphStyle(
        "h3",
        parent=BASE["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=12,
        textColor=INK,
        spaceBefore=5,
        spaceAfter=3,
    ),
    "body": ParagraphStyle(
        "body",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=9.25,
        leading=12.6,
        textColor=INK,
        spaceAfter=6,
        allowWidows=0,
        allowOrphans=0,
    ),
    "body_small": ParagraphStyle(
        "body_small",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=8.2,
        leading=10.8,
        textColor=INK,
        spaceAfter=4,
    ),
    "bullet": ParagraphStyle(
        "bullet",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=12.1,
        leftIndent=12,
        firstLineIndent=-8,
        bulletIndent=0,
        textColor=INK,
        spaceAfter=4,
    ),
    "bullet_small": ParagraphStyle(
        "bullet_small",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=8.1,
        leading=10.5,
        leftIndent=11,
        firstLineIndent=-7,
        textColor=INK,
        spaceAfter=2.6,
    ),
    "callout_label": ParagraphStyle(
        "callout_label",
        parent=BASE["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.2,
        leading=8.5,
        tracking=1.0,
        textColor=NAVY,
    ),
    "callout_body": ParagraphStyle(
        "callout_body",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=8.8,
        leading=11.5,
        textColor=INK,
    ),
    "source": ParagraphStyle(
        "source",
        parent=BASE["Normal"],
        fontName="Courier",
        fontSize=6.8,
        leading=9,
        textColor=MUTED,
        spaceBefore=4,
    ),
    "caption": ParagraphStyle(
        "caption",
        parent=BASE["Normal"],
        fontName="Helvetica-Oblique",
        fontSize=7.4,
        leading=9.5,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceBefore=4,
    ),
    "table_head": ParagraphStyle(
        "table_head",
        parent=BASE["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.2,
        leading=9,
        textColor=PAPER,
    ),
    "table_cell": ParagraphStyle(
        "table_cell",
        parent=BASE["Normal"],
        fontName="Helvetica",
        fontSize=7.6,
        leading=10.1,
        textColor=INK,
    ),
    "table_cell_bold": ParagraphStyle(
        "table_cell_bold",
        parent=BASE["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.7,
        leading=10.2,
        textColor=NAVY,
    ),
    "quote": ParagraphStyle(
        "quote",
        parent=BASE["BodyText"],
        fontName="Helvetica-Bold",
        fontSize=12.5,
        leading=17,
        textColor=NAVY,
        leftIndent=12,
        rightIndent=12,
        spaceAfter=8,
    ),
    "question": ParagraphStyle(
        "question",
        parent=BASE["Heading3"],
        fontName="Helvetica-Bold",
        fontSize=9.4,
        leading=11.4,
        textColor=NAVY,
        spaceAfter=2,
    ),
    "answer": ParagraphStyle(
        "answer",
        parent=BASE["BodyText"],
        fontName="Helvetica",
        fontSize=8.35,
        leading=10.8,
        textColor=INK,
        spaceAfter=6,
    ),
}


def P(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def bullet(text: str, small: bool = False) -> Paragraph:
    return P(f"- {text}", "bullet_small" if small else "bullet")


def source_note(*paths: str) -> Paragraph:
    return P("SOURCE PATHS  " + "  |  ".join(paths), "source")


def section_header(number: str, title: str, kicker: str) -> list[Flowable]:
    return [
        P(f"SECTION {number}", "section_no"),
        P(title, "section_title"),
        P(kicker, "section_kicker"),
    ]


def callout(label: str, text: str, kind: str = "say") -> Table:
    palette = {
        "say": (PALE_BLUE, BLUE),
        "why": (PALE_GREEN, GREEN),
        "warn": (PALE_AMBER, AMBER),
        "risk": (PALE_RED, RED),
    }
    background, accent = palette[kind]
    table = Table(
        [[P(label.upper(), "callout_label"), P(text, "callout_body")]],
        colWidths=[0.92 * inch, CONTENT_W - 0.92 * inch],
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.Color(accent.red, accent.green, accent.blue, alpha=0.35)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 9),
                ("RIGHTPADDING", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def data_table(
    headers: list[str],
    rows: list[list[str]],
    widths: list[float],
    compact: bool = False,
) -> Table:
    body_style = "body_small" if compact else "table_cell"
    data: list[list[Paragraph]] = [
        [P(header, "table_head") for header in headers]
    ]
    for row in rows:
        data.append(
            [
                P(cell, "table_cell_bold" if index == 0 else body_style)
                for index, cell in enumerate(row)
            ]
        )
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.45, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for row_number in range(1, len(data)):
        if row_number % 2 == 0:
            style.append(("BACKGROUND", (0, row_number), (-1, row_number), SOFT))
    table.setStyle(TableStyle(style))
    return table


class CoverHero(Flowable):
    def __init__(self, width: float, height: float = 9.35 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def draw(self) -> None:
        c = self.canv
        w, h = self.width, self.height
        c.setFillColor(NAVY)
        c.roundRect(0, 0, w, h, 18, fill=1, stroke=0)
        c.setFillColor(BLUE)
        c.circle(w - 60, h - 55, 112, fill=1, stroke=0)
        c.setFillColor(SNOW)
        c.circle(w - 16, h - 25, 54, fill=1, stroke=0)

        c.setFillColor(SNOW)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(30, h - 60, "PRIVATE INTERVIEW PREPARATION")

        title = "CensusChat"
        c.setFillColor(PAPER)
        c.setFont("Helvetica-Bold", 36)
        c.drawString(30, h - 125, title)
        c.setFont("Helvetica-Bold", 25)
        c.drawString(30, h - 160, "Engineering Interview Manual")

        c.setFillColor(colors.HexColor("#D8E7F5"))
        c.setFont("Helvetica", 12)
        lines = [
            "How to explain the system, defend the tradeoffs,",
            "and map the build to the Snowflake Applied AI rubric.",
        ]
        y = h - 205
        for line in lines:
            c.drawString(30, y, line)
            y -= 18

        c.setStrokeColor(colors.HexColor("#46647F"))
        c.setLineWidth(1)
        c.line(30, h - 270, w - 30, h - 270)

        cards = [
            ("01", "LEAD WITH", "Deterministic SQL trust boundary"),
            ("02", "TEACH", "Closed topology, open vocabulary"),
            ("03", "BE CANDID", "Grounding evidence is not a runtime guarantee"),
        ]
        card_y = h - 345
        for number, label, message in cards:
            c.setFillColor(colors.HexColor("#183754"))
            c.roundRect(30, card_y - 42, w - 60, 54, 8, fill=1, stroke=0)
            c.setFillColor(SNOW)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(44, card_y - 10, number)
            c.setFillColor(colors.HexColor("#A9C4DB"))
            c.setFont("Helvetica-Bold", 7)
            c.drawString(76, card_y - 7, label)
            c.setFillColor(PAPER)
            c.setFont("Helvetica", 10)
            c.drawString(76, card_y - 23, message)
            card_y -= 70

        c.setFillColor(colors.HexColor("#A9C4DB"))
        c.setFont("Helvetica", 8.5)
        c.drawString(30, 42, f"Application snapshot {APP_SNAPSHOT}  |  Verified {VERIFIED_DATE}")
        c.drawRightString(w - 30, 42, "Not for submission")


class ArchitectureDiagram(Flowable):
    def __init__(self, width: float = CONTENT_W, height: float = 3.9 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def _box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        title: str,
        subtitle: str,
        fill: colors.Color = PAPER,
        stroke: colors.Color = BORDER,
        title_color: colors.Color = NAVY,
    ) -> None:
        c = self.canv
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(1)
        c.roundRect(x, y, w, h, 7, fill=1, stroke=1)
        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(x + w / 2, y + h - 14, title)
        c.setFillColor(MUTED if fill == PAPER else title_color)
        c.setFont("Helvetica", 6.6)
        for index, line in enumerate(subtitle.split("\n")):
            c.drawCentredString(x + w / 2, y + h - 27 - 9 * index, line)

    def _arrow(self, x1: float, y1: float, x2: float, y2: float, color: colors.Color = MUTED) -> None:
        c = self.canv
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.2)
        c.line(x1, y1, x2, y2)
        angle = 4
        if abs(x2 - x1) >= abs(y2 - y1):
            direction = 1 if x2 > x1 else -1
            c.line(x2, y2, x2 - direction * angle, y2 + 2.5)
            c.line(x2, y2, x2 - direction * angle, y2 - 2.5)
        else:
            direction = 1 if y2 > y1 else -1
            c.line(x2, y2, x2 + 2.5, y2 - direction * angle)
            c.line(x2, y2, x2 - 2.5, y2 - direction * angle)

    def draw(self) -> None:
        c = self.canv
        w = self.width
        self._box(0, 208, 70, 46, "BROWSER", "one HTML file\nSSE client", PALE_BLUE, BLUE)
        self._box(88, 208, 72, 46, "FASTAPI", "/api/chat\nstream transport")
        self._box(184, 198, 96, 66, "AGENT LOOP", "Sonnet\n8 rounds / 2 retries\n50s soft watchdog", PALE_CYAN, SNOW)
        self._box(306, 208, 82, 46, "ANSWER", "normalized rows\nstreamed tokens", PALE_GREEN, GREEN)
        self._box(w - 106, 199, 106, 64, "EVIDENCE", "SQLite trace\nspans + final answer\nterminal status", SOFT, BORDER)

        self._arrow(70, 231, 88, 231)
        self._arrow(160, 231, 184, 231)
        self._arrow(280, 231, 306, 231)
        self._arrow(388, 231, w - 106, 231, GREEN)

        self._box(66, 145, 92, 42, "HAIKU GUARDRAIL", "scope classifier\nfails open", PALE_AMBER, AMBER)
        self._arrow(184, 210, 158, 175, AMBER)

        tool_y = 55
        tool_w = (w - 36) / 3
        self._box(8, tool_y, tool_w, 58, "search_census_variables", "local SQLite FTS5\nopen vocabulary", PALE_BLUE, BLUE)
        self._box(18 + tool_w, tool_y, tool_w, 58, "resolve_geography", "local SQLite snapshot\ndeterministic FIPS", SOFT, BORDER)
        self._box(28 + tool_w * 2, tool_y, tool_w, 58, "run_census_sql", "SQL GATE (sqlglot)\nSNOWFLAKE 2020 ACS", PALE_RED, RED, RED)

        self._arrow(218, 198, 8 + tool_w / 2, tool_y + 58, BLUE)
        self._arrow(232, 198, 18 + tool_w * 1.5, tool_y + 58, MUTED)
        self._arrow(246, 198, 28 + tool_w * 2.5, tool_y + 58, RED)

        c.setFillColor(MUTED)
        c.setFont("Helvetica-Oblique", 6.5)
        c.drawString(12, 41, "local discovery, no request-time network")
        c.drawRightString(w - 12, 41, "default-deny hard boundary")

        c.setStrokeColor(BORDER)
        c.setDash(3, 2)
        c.roundRect(0, 35, w, 94, 8, fill=0, stroke=1)
        c.setDash()
        c.setFillColor(MUTED)
        c.setFont("Helvetica-Bold", 6.3)
        c.drawString(8, 133, "THREE AGENT TOOLS")


class SequenceDiagram(Flowable):
    def __init__(self, width: float = CONTENT_W, height: float = 3.9 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def draw(self) -> None:
        c = self.canv
        lanes = [
            ("Browser", 4),
            ("FastAPI", 90),
            ("Agent", 176),
            ("Local tools", 262),
            ("Gate + SF", 348),
            ("Evidence", 434),
        ]
        top = self.height - 30
        bottom = 22
        for label, x in lanes:
            c.setFillColor(NAVY)
            c.roundRect(x, top, 76, 22, 5, fill=1, stroke=0)
            c.setFillColor(PAPER)
            c.setFont("Helvetica-Bold", 7)
            c.drawCentredString(x + 38, top + 8, label)
            c.setStrokeColor(BORDER)
            c.setDash(2, 2)
            c.line(x + 38, top, x + 38, bottom)
            c.setDash()

        events = [
            (0, 1, "1  POST /api/chat + SSE", 228, BLUE),
            (1, 2, "2  replay session history", 205, MUTED),
            (2, 2, "3  classify recent context", 182, AMBER),
            (2, 3, "4  search variables / geography", 159, MUTED),
            (2, 4, "5  proposed SELECT", 136, RED),
            (4, 4, "6  validate AST, then execute", 113, RED),
            (4, 2, "7  normalized QueryResult", 90, GREEN),
            (2, 0, "8  stream answer tokens", 67, BLUE),
            (2, 5, "9  persist spans, answer, status", 44, MUTED),
            (2, 0, "10  done or error", 21, BLUE),
        ]
        x_centers = [42, 128, 214, 300, 386, 472]
        for start, end, label, y, color in events:
            x1, x2 = x_centers[start], x_centers[end]
            if start == end:
                c.setStrokeColor(color)
                c.roundRect(x1 - 31, y - 4, 62, 16, 4, fill=0, stroke=1)
                c.setFillColor(color)
                c.setFont("Helvetica", 6.5)
                c.drawCentredString(x1, y + 1, label)
                continue
            c.setStrokeColor(color)
            c.setLineWidth(1)
            c.line(x1, y, x2, y)
            direction = 1 if x2 > x1 else -1
            c.line(x2, y, x2 - direction * 5, y + 2.5)
            c.line(x2, y, x2 - direction * 5, y - 2.5)
            c.setFillColor(INK)
            c.setFont("Helvetica", 6.5)
            c.drawCentredString((x1 + x2) / 2, y + 5, label)


class TestingStack(Flowable):
    def __init__(self, width: float = CONTENT_W, height: float = 2.0 * inch):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, available_width: float, available_height: float) -> tuple[float, float]:
        return min(self.width, available_width), self.height

    def draw(self) -> None:
        c = self.canv
        layers = [
            (0, 0, self.width, 30, NAVY, "OFFLINE TESTS", "deterministic, credential-free, every commit"),
            (26, 37, self.width - 52, 30, BLUE, "LIVE REGRESSION", "6 stable scenarios x 2 trials, manual paid gate"),
            (52, 74, self.width - 104, 30, SNOW, "CAPABILITY", "8 broader scenarios, tri-state, informational"),
            (78, 111, self.width - 156, 30, AMBER, "HUMAN SEMANTIC REVIEW", "prose quality until a judge is calibrated"),
        ]
        for x, y, w, h, color, title, subtitle in layers:
            c.setFillColor(color)
            c.roundRect(x, y, w, h, 6, fill=1, stroke=0)
            c.setFillColor(PAPER if color != AMBER else NAVY)
            c.setFont("Helvetica-Bold", 7.2)
            c.drawString(x + 9, y + 17, title)
            c.setFont("Helvetica", 6.6)
            c.drawRightString(x + w - 9, y + 9, subtitle)


def page_furniture(canvas, doc) -> None:
    canvas.saveState()
    if doc.page == 1:
        canvas.restoreState()
        return
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(LEFT, PAGE_H - 27, PAGE_W - RIGHT, PAGE_H - 27)
    canvas.setFont("Helvetica-Bold", 6.6)
    canvas.setFillColor(MUTED)
    canvas.drawString(LEFT, PAGE_H - 20, "CENSUSCHAT / INTERVIEW PLAYBOOK")
    canvas.setFont("Helvetica", 6.6)
    canvas.drawRightString(PAGE_W - RIGHT, PAGE_H - 20, "PRIVATE PREPARATION")

    canvas.line(LEFT, 27, PAGE_W - RIGHT, 27)
    canvas.setFont("Helvetica", 6.4)
    canvas.drawString(LEFT, 17, f"App snapshot {APP_SNAPSHOT}  |  Verified {VERIFIED_DATE}")
    canvas.drawCentredString(PAGE_W / 2, 17, "Do not overstate soft guarantees")
    canvas.drawRightString(PAGE_W - RIGHT, 17, f"{doc.page:02d}")
    canvas.restoreState()


def add_cover(story: list[Flowable]) -> None:
    story.append(CoverHero(CONTENT_W))
    story.append(PageBreak())


def add_opening(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "01",
            "The 90-second opening",
            "Start with the problem, the governing architecture, and one honest boundary.",
        )
    )
    story.append(
        callout(
            "Say this",
            "CensusChat is a production-minded chat agent for the 2020 ACS five-year estimates. It combines runtime discovery over thousands of Census variables with a small, explicit tool loop. The model can decide what to look up, but it cannot decide what SQL is safe: every request-time Snowflake query crosses a default-deny AST gate. The design optimizes for grounded answers, honest ambiguity, and observable failure under a 24-hour constraint.",
            "say",
        )
    )
    story.append(Spacer(1, 8))
    story.append(P("The story in three moves", "h2"))
    story.append(bullet("<b>Coverage without prompt bloat.</b> The schema topology is small and stable; the variable vocabulary is large and discovered at runtime through local FTS."))
    story.append(bullet("<b>Autonomy inside deterministic boundaries.</b> Sonnet selects among exactly three tools, while code enforces SQL safety, unresolved geography ambiguity, retry limits, and terminal stream behavior."))
    story.append(bullet("<b>Evidence with named limits.</b> Offline tests prove deterministic layers. Live evals exercise the real model and Snowflake stack. Semantic prose quality remains human-reviewed."))
    story.append(Spacer(1, 5))
    screenshot = Image(str(UI_SCREENSHOT), width=CONTENT_W, height=CONTENT_W * 480 / 853)
    story.append(screenshot)
    story.append(P("Current reviewer surface: Chat, How It Works, Evidence, Evals, plus a New Chat boundary for session isolation.", "caption"))
    story.append(Spacer(1, 5))
    story.append(
        callout(
            "Do not overclaim",
            "The strongest hard guarantee is SQL safety. Numeric answer grounding is a model instruction with selected offline evidence checks, not a serving-time validator for every final-answer number.",
            "warn",
        )
    )
    story.append(PageBreak())


def add_rubric(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "02",
            "Assignment rubric map",
            "Tie each rubric dimension to implementation evidence, then volunteer the relevant limitation.",
        )
    )
    rows = [
        [
            "LLM / AI Engineering",
            "Runtime variable and geography discovery; three-tool Sonnet loop; schema rules in context; deterministic ambiguity backstop; AST SQL gate.",
            "Final-answer numbers are not all runtime-validated. Prose quality has no calibrated model judge.",
            "Lead with the split between model reasoning and code enforcement.",
        ],
        [
            "Production Quality",
            "SSE streaming; graceful degraded mode; sanitized errors; bounded recovery; SQL statement timeout; durable Evidence traces; health endpoint.",
            "Single-instance SQLite, no rate limit or spend cap, cached Snowflake health, soft watchdog.",
            "Explain failure behavior before listing infrastructure.",
        ],
        [
            "Judgment Under Constraints",
            "2020-only allowlist; FTS instead of embeddings; no agent framework; three tools only; decennial and Langfuse cut deliberately.",
            "The interface arrived late in the original build. Some broader capability remains flaky or informational.",
            "Defend what was excluded as strongly as what was built.",
        ],
        [
            "Reflection and Self-Awareness",
            "The reflection names false-green tests, prompt defects, eval-calibration failures, production gaps, and a prioritized next-day plan.",
            "Do not turn reflection into self-criticism. Use incidents to show changed engineering judgment.",
            "Tell one concrete failure and the general lesson it produced.",
        ],
    ]
    story.append(
        data_table(
            ["Dimension", "Evidence", "Known limit", "Interview move"],
            rows,
            [1.12 * inch, 2.18 * inch, 2.02 * inch, 1.42 * inch],
            compact=True,
        )
    )
    story.append(Spacer(1, 10))
    story.append(
        callout(
            "Why it matters",
            "The assignment explicitly evaluates the ability to explain and defend open-ended technical decisions. A rubric answer should therefore have the form: choice, evidence, tradeoff, rejected alternative.",
            "why",
        )
    )
    story.append(Spacer(1, 8))
    story.append(P("A compact evidence ladder", "h2"))
    story.append(bullet("<b>Code:</b> the strongest evidence for deterministic guarantees."))
    story.append(bullet("<b>Offline tests:</b> repeatable proof that code-level contracts hold."))
    story.append(bullet("<b>Live evals:</b> evidence about the integrated model, prompt, tools, and database."))
    story.append(bullet("<b>Reflection:</b> evidence of judgment, not evidence that a behavior works."))
    story.append(source_note("docs/assignment.pdf pp. 2-5", "README.md", "docs/reflection.md"))
    story.append(PageBreak())


def add_architecture(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "03",
            "Architecture at a glance",
            "The model is an orchestrator inside a narrow action space, not the security boundary.",
        )
    )
    story.append(ArchitectureDiagram())
    story.append(Spacer(1, 4))
    story.append(P("The governing design insight", "h2"))
    story.append(
        P(
            "CensusChat separates <b>closed topology</b> from <b>open vocabulary</b>. Join rules, Census block-group key structure, rollup recipes, and statistical constraints are small enough to teach in the system prompt. Thousands of variable labels and geography records stay outside the prompt and are retrieved only when needed.",
            "body",
        )
    )
    story.append(
        callout(
            "Say this",
            "The prompt teaches how the database works, not everything the database contains. That keeps context small while preserving broad variable coverage.",
            "say",
        )
    )
    story.append(Spacer(1, 7))
    story.append(P("Why three tools is a feature", "h2"))
    story.append(bullet("<b>search_census_variables</b> translates user language into Census field identifiers using local SQLite FTS5."))
    story.append(bullet("<b>resolve_geography</b> maps a state or county name to deterministic FIPS candidates using local SQLite."))
    story.append(bullet("<b>run_census_sql</b> is the only request-time Snowflake code path, and every call crosses the SQL gate."))
    story.append(source_note("src/agent.py", "src/tools.py", "src/sqlgate.py", "src/model_config.py"))
    story.append(PageBreak())


def add_request_lifecycle(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "04",
            "One request, end to end",
            "Walk through the turn in time order. This is the clearest way to explain state, tools, safety, and observability together.",
        )
    )
    story.append(SequenceDiagram())
    story.append(Spacer(1, 2))
    story.append(P("What happens at each boundary", "h2"))
    story.append(bullet("The browser sends a client-generated <b>session_id</b> and message to FastAPI, which returns an SSE stream."))
    story.append(bullet("The agent replays the full user/assistant history from SQLite, then gives the classifier only the two most recent stored messages for context-aware routing."))
    story.append(bullet("Sonnet discovers variables and geography locally, proposes SQL, receives normalized query rows, and streams answer tokens."))
    story.append(bullet("Each tool call emits start/end events. Every turn terminates with <b>done</b> or <b>error</b>. A separate trace record stores spans, final answer, terminal status, and timing."))
    story.append(
        callout(
            "Why it matters",
            "The data flow makes the trust boundaries inspectable: user text becomes model context, never SQL string interpolation; proposed SQL becomes executable only after structural validation.",
            "why",
        )
    )
    story.append(Spacer(1, 6))
    story.append(
        callout(
            "Do not overclaim",
            "Full-history replay is simple and effective for a demo, but it can replay orphaned user turns after infrastructure failures. New Chat gives the user a fresh session boundary; a production fix would make turn persistence transactional or exclude failed turns from replay.",
            "warn",
        )
    )
    story.append(source_note("src/app.py", "src/agent.py", "src/sessions.py", "src/tracing.py"))
    story.append(PageBreak())


def add_data_model(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "05",
            "Data model and statistical correctness",
            "The database stores small geographic pieces. The app can combine some measures into county or state answers, but not all of them.",
        )
    )
    story.append(P("Think about the data in three steps", "h2"))
    story.append(
        callout(
            "STORE",
            "<b>1. Start with the smallest unit.</b><br/>Each source row describes one Census block group in the 2020 ACS five-year estimates. A block group has its own population count, household count, income statistics, and other measures.",
            "say",
        )
    )
    story.append(
        callout(
            "ADD",
            "<b>2. Add counts to answer larger-geography questions.</b><br/>Population is additive. <b>Harris County population = sum of its block-group population counts.</b> The same idea works for other counts when every row measures the same universe.",
            "why",
        )
    )
    story.append(
        callout(
            "STOP",
            "<b>3. Do not combine medians.</b><br/>Two block groups report median household incomes of $55k and $95k. Their average is $75k, but that does not make the county median $75k. The block groups may contain different numbers and distributions of households, so the true county median cannot be reconstructed from those two medians.",
            "warn",
        )
    )
    story.append(Spacer(1, 8))
    story.append(P("QUICK RULES", "h2"))
    rules = [
        ["Match the universe", "People, households, workers, and occupied housing units are different populations."],
        ["Missing is not zero", "NULL becomes 'not reported.' A verified income top-code displays as $250,000 or more."],
        ["Supported rollups", "The shipped source supports state and county answers, not city, ZIP, or metro boundaries."],
        ["One vintage", "Only the 2020 ACS five-year release is queryable, and the SQL table allowlist enforces it."],
    ]
    story.append(data_table(["Rule", "Plain-English meaning"], rules, [1.48 * inch, 5.26 * inch], compact=True))
    story.append(Spacer(1, 7))
    story.append(P("Why not compare 2015-2019 with 2016-2020?", "h2"))
    story.append(
        P(
            "Those five-year estimates share four of the same years, and the 2020 release uses changed block-group boundaries. A result might look like a trend while mostly comparing overlapping data. Supporting time comparisons would require vintage-aware retrieval and code that rejects invalid cross-vintage analysis.",
            "body_small",
        )
    )
    story.append(
        callout(
            "Say this",
            "The key question is not just whether SQL can aggregate rows. It is whether the statistic itself is valid to combine. Counts usually are; medians are not.",
            "say",
        )
    )
    story.append(source_note("docs/schema-notes.md", "docs/decisions.md D-003/D-005/D-008", "src/tools.py"))
    story.append(PageBreak())


def add_trust(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "06",
            "Trust boundaries and graceful failure",
            "Separate advisory model behavior from code-enforced invariants. This is the strongest system-design section.",
        )
    )
    rows = [
        ["System prompt", "Soft", "Quoting, aggregation, grounding, and answer-style instructions.", "The model may ignore or misapply it."],
        ["Haiku classifier", "Soft", "Fast-fail off-topic, adversarial, and inappropriate input.", "Fails open on timeout or error."],
        ["Ambiguity backstop", "Hard", "Blocks SQL when a geography resolved as ambiguous during the turn.", "Produces deterministic clarification."],
        ["SQL gate", "Hard", "Parses one SELECT, checks every table, rejects banned structures, injects LIMIT.", "Defaults to deny; rejected SQL never executes."],
        ["Snowflake session", "Hard", "25-second statement timeout and sanitized SQL only.", "Bounds database execution, not connection or model time."],
    ]
    story.append(data_table(["Layer", "Kind", "Role", "Failure behavior"], rows, [1.2 * inch, 0.58 * inch, 2.55 * inch, 2.41 * inch], compact=True))
    story.append(Spacer(1, 8))
    story.append(P("What the SQL gate checks", "h2"))
    story.append(bullet("sqlglot parses with the Snowflake dialect; regex is not the parser."))
    story.append(bullet("Exactly one statement, SELECT only, with CTEs allowed and DDL, DML, INTO, calls, session variables, and unverifiable table positions rejected."))
    story.append(bullet("Every referenced table must match the fully qualified allowlist. Explicit columns are required; COUNT(*) is the narrow exemption."))
    story.append(bullet("A LIMIT is injected when missing. User text is never interpolated into SQL."))
    story.append(
        callout(
            "Say this",
            "The classifier is allowed to fail open because it is not load-bearing. Availability stays high, while the SQL boundary still fails closed. At scale I would add defense in depth with a least-privilege Snowflake role and resource monitor.",
            "say",
        )
    )
    story.append(Spacer(1, 7))
    story.append(P("Bounds that prevent hanging or uncontrolled repair", "h2"))
    story.append(bullet("Two retries after SQL failure or zero rows; then deterministic honest failure."))
    story.append(bullet("Eight total tool-loop rounds regardless of success or failure."))
    story.append(bullet("A 50-second watchdog stops new rounds after the budget, but cannot cancel a call already in flight."))
    story.append(source_note("src/sqlgate.py", "src/guardrail.py", "src/agent.py", "src/contracts.py"))
    story.append(PageBreak())


def add_state_and_evidence(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "07",
            "State, streaming, and Evidence",
            "Explain these as three separate responsibilities, even though the UI brings them together.",
        )
    )
    rows = [
        ["Conversation state", "SQLite messages keyed by session_id", "Full user/assistant replay gives multi-turn continuity and survives reloads."],
        ["Transport state", "SSE ChatEvents", "Tokens, tool start/end, terminal done/error, and progress reach the browser incrementally."],
        ["Evidence state", "Separate SQLite trace store", "Guardrail, model, and tool spans plus final answer, terminal status, timing, and raw JSON."],
        ["Browser boundary", "localStorage session_id + New Chat", "New Chat starts a fresh replay context without deleting old Evidence history."],
    ]
    story.append(data_table(["Responsibility", "Mechanism", "Why"], rows, [1.45 * inch, 2.1 * inch, 3.19 * inch]))
    story.append(Spacer(1, 9))
    story.append(P("What to point out in the Evidence tab", "h2"))
    story.append(bullet("The guardrail verdict and latency show whether the turn took the fast-fail path."))
    story.append(bullet("Model spans expose stop reason, token counts, and time spent per round."))
    story.append(bullet("Tool spans show arguments, bounded result summaries, success/failure, and latency."))
    story.append(bullet("The terminal step pairs the final answer with done/error status and total timing."))
    story.append(
        callout(
            "Why it matters",
            "Observability is part of the reviewer story because it makes a stochastic system inspectable. It also revealed the recent session-replay bug: traces showed an older Washington County turn being replayed into a Harris County request.",
            "why",
        )
    )
    story.append(Spacer(1, 7))
    story.append(
        callout(
            "Do not overclaim",
            "Evidence is application-local tracing, not Langfuse. It has no cross-service search, alerting, retention policy, or replica-aware aggregation. Recording is deliberately fail-soft so a tracing bug cannot break chat.",
            "warn",
        )
    )
    story.append(source_note("src/sessions.py", "src/tracing.py", "static/index.html", "docs/decisions.md D-021/D-023/D-025"))
    story.append(PageBreak())


def add_evals(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "08",
            "Testing and evaluation strategy",
            "Use the right instrument for each layer. Do not ask mocked unit tests to certify model behavior.",
        )
    )
    story.append(TestingStack())
    story.append(Spacer(1, 8))
    rows = [
        ["Regression (6)", "DF-05, MT-01, AMB-01, UN-01, OT-01, INJ-02", "Stable, objectively scored behaviors. Two live trials must both pass when the manual workflow is run."],
        ["Capability (8)", "DF-01, CMP-01, AMB-02, PM-02, PM-03, AMB-03, UN-08, PM-08", "Broader or stochastic coverage. Pass, fail, or inconclusive, but non-blocking."],
    ]
    story.append(data_table(["Suite", "Scenarios", "Contract"], rows, [1.2 * inch, 2.55 * inch, 2.99 * inch], compact=True))
    story.append(Spacer(1, 7))
    story.append(P("What deterministic graders can prove", "h2"))
    story.append(bullet("Expected variable and geography appear in captured tool evidence."))
    story.append(bullet("Answer is nonblank or contains a stable reference value."))
    story.append(bullet("Refusal paths avoid tools and use refusal language; ambiguity paths ask and avoid SQL."))
    story.append(bullet("Protected median variables are not aggregated with SUM or AVG."))
    story.append(bullet("Four-digit answer figures are checked only against visible final-turn query-row cells; hidden rows become inconclusive rather than passing."))
    story.append(
        callout(
            "Do not overclaim",
            "The committed 14/14 artifact is from commit d44c1cc on 2026-08-06 and predates the current suite and tri-state contracts. The UI labels it legacy. It demonstrates that a real-stack run existed, not that the current branch is 14/14.",
            "warn",
        )
    )
    story.append(Spacer(1, 7))
    story.append(
        callout(
            "Say this",
            "I did not add an LLM judge because an uncalibrated judge would create a score, not evidence. Prose quality stays human-reviewed until a judge is compared with labeled examples and agreement is measured on held-out data.",
            "say",
        )
    )
    story.append(source_note("tests/", "evals/scenarios.py", "evals/run_evals.py", ".github/workflows/"))
    story.append(PageBreak())


def add_production(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "09",
            "Deployment and production quality",
            "The app is deployable and observable as a controlled demo. Production at customer scale would require a different state and cost-control layer.",
        )
    )
    rows = [
        ["Edge", "Native Caddy on EC2", "TLS, basic auth, SSE flushing; app port bound to loopback on the host."],
        ["Application", "Dockerized FastAPI/Uvicorn", "One stateless process around stateful local files; image includes frontend and committed eval artifacts."],
        ["Model", "Anthropic SDK", "Sonnet agent and Haiku classifier, pinned in one module."],
        ["Data", "Snowflake Marketplace share", "Only run_census_sql touches it at request time; local snapshot handles discovery."],
        ["Persistence", "Mounted SQLite files", "Sessions and traces survive restart and deploy, but do not support horizontal replicas."],
        ["CI", "GitHub Actions", "Credential-free offline tests on PR/main; protected, manually triggered paid regression workflow."],
    ]
    story.append(data_table(["Layer", "Choice", "Operational meaning"], rows, [1.05 * inch, 1.75 * inch, 3.94 * inch]))
    story.append(Spacer(1, 8))
    story.append(P("Verified deployment snapshot", "h2"))
    story.append(
        callout(
            "Evidence",
            f"{DEPLOYED_URL} and the local instance both returned status=ok, snapshot=ok, snowflake=ok on {VERIFIED_DATE}. This is a point-in-time check, not continuous availability evidence.",
            "why",
        )
    )
    story.append(Spacer(1, 8))
    story.append(P("What is still missing for production", "h2"))
    story.append(bullet("Rate limiting, per-session spend caps, abuse monitoring, and a least-privilege Snowflake role with a resource monitor."))
    story.append(bullet("Shared session/trace storage for multiple replicas, plus retention and deletion policies."))
    story.append(bullet("Per-call cancellation deadlines. The current watchdog checks only between rounds."))
    story.append(bullet("Live health or circuit breaking that can detect Snowflake failure after boot without violating the one-query-path invariant."))
    story.append(bullet("Central telemetry with search, alerting, and deployment correlation."))
    story.append(
        callout(
            "Say this",
            "I would ship this as a time-bounded reviewer demo. I would not call it multi-tenant production infrastructure until cost controls, shared state, cancellation, and centralized observability are added.",
            "say",
        )
    )
    story.append(source_note("Dockerfile", "docker-compose.yml", "deploy.sh", "src/health.py", ".github/workflows/"))
    story.append(PageBreak())


def add_walkthrough(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "10",
            "Five-minute reviewer walkthrough",
            "Use one coherent session. Show behavior first, then open one evidence surface to explain why it happened.",
        )
    )
    rows = [
        ["1", "Population of Harris County, Texas?", "Variable search, exact county resolution, safe state/county rollup, grounded fact.", "Point to tool order and final QueryResult evidence."],
        ["2", "What about households?", "Full-history replay carries Harris County into the follow-up without restatement.", "Show the second variable resolution and same geography."],
        ["3", "How many people live in Washington County?", "Thirty matches should trigger clarification, not a silent choice.", "No Snowflake SQL while ambiguity is unresolved."],
        ["4", "How many people will live in Texas in 2050?", "The ACS is historical measurement, not projection. Fast refusal should avoid SQL.", "Guardrail or answer path, zero database work."],
        ["5", "Open Evidence, then Evals", "Evidence explains one real turn. Evals explains test intent and result provenance.", "Call out legacy benchmark labeling before the reviewer does."],
    ]
    story.append(data_table(["Step", "Prompt/action", "What it demonstrates", "What to inspect"], rows, [0.48 * inch, 1.86 * inch, 2.6 * inch, 1.8 * inch], compact=True))
    story.append(Spacer(1, 9))
    story.append(P("Suggested narration", "h2"))
    story.append(bullet("Before the first prompt: 'I will show a fact, a context-dependent follow-up, ambiguity, and an unsupported request.'"))
    story.append(bullet("Before Evidence: 'The chat is the product surface; Evidence is the explanation surface.'"))
    story.append(bullet("Before Evals: 'The app does not pretend every behavior has the same measurement quality, so stable regression and broader capability are separate.'"))
    story.append(
        callout(
            "Do not overclaim",
            "A live model can vary. If a scenario behaves unexpectedly, use Evidence to localize the failure and discuss the appropriate fix. Do not rerun until it happens to look good.",
            "warn",
        )
    )
    story.append(Spacer(1, 7))
    story.append(P("Recovery line if the demo fails", "h2"))
    story.append(
        callout(
            "Say this",
            "This is exactly why the trace and eval layers exist. Let me show whether the failure was routing, discovery, SQL validation, execution, or answer generation, and then I will explain which layer should own the fix.",
            "say",
        )
    )
    story.append(source_note("README.md: Evaluating the running demo", "static/index.html", "evals/README.md"))
    story.append(PageBreak())


def add_judgment(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "11",
            "Judgment under the 24-hour constraint",
            "The strongest answer is not 'I built everything.' It is 'I knew which guarantees were worth encoding and which breadth to cut.'",
        )
    )
    rows = [
        ["FTS5 over embeddings", "Variable retrieval needed exact, local, inspectable matching. A probe showed a morphology/ranking problem, not missing semantic representation.", "Embedding dependency, index build, retrieval opacity, and tuning time."],
        ["Handwritten tool loop", "Exactly three tools and explicit SSE/tracing needs kept control flow small and legible.", "Framework abstraction and repair behavior that would be harder to inspect."],
        ["2020 only", "Older ACS release overlaps four years and uses changed CBG boundaries. Code-enforced scope prevents invalid comparisons.", "Broader but misleading vintage coverage."],
        ["SQL gate before feature breadth", "A single unsafe or structurally unverifiable query is a customer risk. Default-deny validation is deterministic and testable.", "Prompt-only SQL safety or a regex denylist."],
        ["SQLite sessions and traces", "Fast durable evidence for one demo instance with no external service dependency.", "Distributed state infrastructure that the assignment did not require."],
        ["No LLM judge", "Semantic scores are not trustworthy until calibrated against human labels.", "A polished but unvalidated quality number."],
    ]
    story.append(data_table(["Decision", "Why it was rational", "Alternative rejected"], rows, [1.4 * inch, 3.15 * inch, 2.19 * inch], compact=True))
    story.append(Spacer(1, 8))
    story.append(P("Two incidents that changed the engineering approach", "h2"))
    story.append(bullet("<b>Prompt defect hidden by recovery.</b> Unquoted mixed-case Census identifiers failed live even while mocked tests passed. Successful self-repair initially made the systematic defect look like resilience. Lesson: recovery attempts are failure signals and real-stack evals test the product, not just the code."))
    story.append(bullet("<b>Uncalibrated grounding check produced false reds.</b> Variable IDs and missing division logic were misread as fabrication. Lesson: an eval check is a measuring instrument and needs known-good calibration plus mutation tests."))
    story.append(
        callout(
            "Say this",
            "My best judgment was encoding high-cost mistakes as deterministic boundaries: unsafe SQL, unresolved geography, invalid median aggregation, bounded repair, and honest terminal behavior. I cut breadth where it would weaken those guarantees.",
            "say",
        )
    )
    story.append(source_note("docs/reflection.md", "docs/decisions.md", "docs/01-architecture.md"))
    story.append(PageBreak())


def qa_block(question: str, answer: str) -> list[Flowable]:
    return [P(question, "question"), P(answer, "answer")]


def add_questions_one(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "12",
            "Likely interview questions, part I",
            "Keep each answer under a minute. Name the tradeoff and the rejected alternative.",
        )
    )
    questions = [
        (
            "Why does the classifier fail open?",
            "Because it is an availability and UX layer, not the trust boundary. If Haiku is unavailable, a legitimate Census request should still proceed. SQL safety remains fail-closed below it, and off-topic content still cannot escape the three-tool action space. The tradeoff is that an outage may allow irrelevant turns to reach Sonnet and cost more.",
        ),
        (
            "Why FTS5 instead of embeddings?",
            "The retrieval target is a structured vocabulary of Census labels and IDs, and the observed failures were token morphology and ranking. FTS5 is local, deterministic, cheap, and inspectable. I would add embeddings only after an error analysis showed semantic recall failures that lexical retrieval could not solve.",
        ),
        (
            "Why did you avoid LangChain or LangGraph?",
            "The loop has three tools, one model vendor, eight bounded rounds, and custom streaming and trace events. A handwritten Anthropic loop makes every transition and failure rule visible. A framework would become attractive when orchestration complexity, provider portability, or durable workflow resumption exceeded this small control surface.",
        ),
        (
            "Why only the 2020 ACS five-year vintage?",
            "The older release overlaps four of five years and uses different block-group boundaries. Adding it would create plausible but invalid trend comparisons. The current allowlist makes cross-vintage SQL impossible. If multi-vintage access became necessary, I would add vintage-aware retrieval and a gate rule rejecting mixed-vintage statements.",
        ),
        (
            "Why can counts aggregate but medians cannot?",
            "Counts are additive across non-overlapping block groups. A median is an order statistic and cannot be reconstructed from subgroup medians alone. Where aggregate numerator and denominator variables exist, the system may compute a true mean and explicitly state that substitution.",
        ),
        (
            "Why exactly three tools?",
            "Each tool owns one distinct external effect: discover variables, resolve geography, execute data SQL. That is enough for the assignment while keeping the action space auditable. Adding a tool should require a new responsibility that cannot fit an existing boundary, not merely convenience.",
        ),
        (
            "Why is the 50-second watchdog soft?",
            "It is checked between agent rounds, so it can stop new tool work after the budget is observed, but it cannot interrupt an Anthropic or Snowflake call already in flight. That leaves headroom under the assignment's 60-second target without creating a guarantee. A production version would add per-call cancellation and a request-wide deadline.",
        ),
    ]
    for question, answer in questions:
        story.extend(qa_block(question, answer))
    story.append(
        callout(
            "Interview pattern",
            "Answer architecture questions as: requirement, choice, why it fits this system, tradeoff, threshold for revisiting the choice.",
            "why",
        )
    )
    story.append(PageBreak())


def add_questions_two(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "13",
            "Likely interview questions, part II",
            "These answers distinguish demonstrated behavior from unfinished production work.",
        )
    )
    questions = [
        (
            "How do you prove that answers are grounded?",
            "Today I can prove selected parts: expected tools ran, expected IDs were resolved, and visible answer figures match captured final-turn query-row cells in the eval harness. I cannot claim every final-answer number is runtime-validated. The strongest next step is to retain complete per-turn query evidence, buffer the final answer, and validate structured numeric claims before release.",
        ),
        (
            "Why have an inconclusive eval outcome?",
            "It prevents missing evidence from becoming a false pass. Bounded trace summaries may hide later query rows, so some grounding checks cannot honestly decide. Inconclusive stays non-passing and in the denominator. It is informational in capability reporting and blocking in regression trials.",
        ),
        (
            "Why fourteen scenarios, and why not more?",
            "The current bottleneck is grader quality, not case count. Six stable regression scenarios protect objectively measurable behavior; eight capability scenarios expose broader or stochastic behavior without creating merge roulette. I would expand only with cases tied to observed failures and checks that have been proven to fail correctly.",
        ),
        (
            "What breaks first under real traffic?",
            "Cost exposure and state topology. There is no rate limit or spend cap, and SQLite sessions and traces are local to one instance. Next come cancellation, stale boot-time health, and observability. I would add limits and shared state before adding replicas because replication without shared state would make behavior less coherent.",
        ),
        (
            "How did you use AI coding tools?",
            "AI produced much of the implementation and independent review agents found real defects. My responsibility was dataset recon, architecture, constraints, acceptance criteria, live verification, and deciding which suggestions to reject. The key lesson was that code review agents still missed a prompt-plus-database integration defect, so I added real-stack eval evidence rather than assuming more review would solve it.",
        ),
        (
            "What would you build next?",
            "First, serving-time numeric provenance. Second, transactional session turns so failed requests cannot remain as orphaned instructions. Third, per-call cancellation. Fourth, rate limits, spend caps, shared state, and centralized telemetry. Fifth, a genuinely independent decennial source for conflicting-answer cases.",
        ),
    ]
    for question, answer in questions:
        story.extend(qa_block(question, answer))
    story.append(
        callout(
            "Do not overclaim",
            "Do not say 'production ready' without a qualifier. Say 'production-minded reviewer demo' and immediately name the scale, cost, and observability work required for customer traffic.",
            "warn",
        )
    )
    story.append(PageBreak())


def add_cheat_sheet(story: list[Flowable]) -> None:
    story.extend(
        section_header(
            "14",
            "Interview cheat sheet",
            "Read this page immediately before the interview. It is the compressed version of the entire manual.",
        )
    )
    left = [
        P("FIVE CLAIMS TO LEAD WITH", "h2"),
        bullet("The AST SQL gate is the hard trust boundary.", small=True),
        bullet("Closed topology plus open runtime vocabulary avoids schema prompt bloat.", small=True),
        bullet("Exactly three tools keep the action space narrow and auditable.", small=True),
        bullet("Ambiguity, retries, and terminal behavior have code backstops.", small=True),
        bullet("Tests, live evals, and human review measure different layers.", small=True),
        Spacer(1, 5),
        P("KEY NUMBERS", "h2"),
        bullet("2020 ACS five-year estimates, covering 2016-2020.", small=True),
        bullet("3 tools: variable search, geography resolution, SQL execution.", small=True),
        bullet("2 recovery retries, 8 total rounds, 50-second soft watchdog.", small=True),
        bullet("25-second Snowflake statement timeout, 200-row injected limit.", small=True),
        bullet("6 regression + 8 capability scenarios.", small=True),
        bullet(f"{OFFLINE_TESTS} offline tests at the documented snapshot.", small=True),
    ]
    right = [
        P("FIVE CLAIMS NOT TO MAKE", "h2"),
        bullet("'Every answer number is runtime-grounded.'", small=True),
        bullet("'The 14/14 artifact proves the current branch.'", small=True),
        bullet("'The watchdog guarantees the 60-second requirement.'", small=True),
        bullet("'Evidence is production observability.'", small=True),
        bullet("'The app is horizontally scalable or cost bounded.'", small=True),
        Spacer(1, 5),
        P("NEXT FIVE IMPROVEMENTS", "h2"),
        bullet("Validate structured numeric claims before final answer release.", small=True),
        bullet("Make turn persistence transactional; exclude failed orphan turns.", small=True),
        bullet("Add per-call deadlines and cancellation.", small=True),
        bullet("Add rate limits, spend caps, shared state, and central telemetry.", small=True),
        bullet("Add an independent decennial source for conflict handling.", small=True),
    ]
    two_col = Table([[left, right]], colWidths=[CONTENT_W / 2 - 6, CONTENT_W / 2 - 6], hAlign="LEFT")
    two_col.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, 0), PALE_BLUE),
                ("BACKGROUND", (1, 0), (1, 0), PALE_AMBER),
                ("BOX", (0, 0), (0, 0), 0.6, BLUE),
                ("BOX", (1, 0), (1, 0), 0.6, AMBER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(two_col)
    story.append(Spacer(1, 9))
    story.append(P("File map for live discussion", "h2"))
    story.append(
        data_table(
            ["Question", "Open"],
            [
                ["How does the loop work?", "src/agent.py"],
                ["Where is the hard boundary?", "src/sqlgate.py"],
                ["How are variables and geographies found?", "src/tools.py + src/snapshot.py"],
                ["How is context stored?", "src/sessions.py"],
                ["How is a turn explained?", "src/tracing.py + Evidence tab"],
                ["What is actually evaluated?", "evals/scenarios.py + evals/README.md"],
                ["What changed and why?", "docs/decisions.md + docs/reflection.md"],
            ],
            [2.4 * inch, 4.34 * inch],
            compact=True,
        )
    )
    story.append(Spacer(1, 8))
    story.append(
        callout(
            "Last line",
            "The design principle is simple: let the model reason where uncertainty is useful, and move high-cost mistakes into deterministic, observable boundaries.",
            "say",
        )
    )


def build_manual(output_path: Path = OUTPUT_PATH) -> None:
    if not UI_SCREENSHOT.exists():
        raise FileNotFoundError(f"UI screenshot not found: {UI_SCREENSHOT}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=LEFT,
        rightMargin=RIGHT,
        topMargin=TOP,
        bottomMargin=BOTTOM,
        title="CensusChat Interview Manual",
        author="Brian Mar - private interview preparation",
        subject="Snowflake Applied AI candidate homework interview playbook",
        creator="CensusChat ReportLab generator",
        pageCompression=1,
    )
    story: list[Flowable] = []
    add_cover(story)
    add_opening(story)
    add_rubric(story)
    add_architecture(story)
    add_request_lifecycle(story)
    add_data_model(story)
    add_trust(story)
    add_state_and_evidence(story)
    add_evals(story)
    add_production(story)
    add_walkthrough(story)
    add_judgment(story)
    add_questions_one(story)
    add_questions_two(story)
    add_cheat_sheet(story)

    doc.build(story, onFirstPage=page_furniture, onLaterPages=page_furniture)


if __name__ == "__main__":
    build_manual()
    print(OUTPUT_PATH)
