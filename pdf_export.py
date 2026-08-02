# AI Roundtable — a local multi-model debate chatroom.
# Copyright (C) 2026 QZXP27
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or (at your
# option) any later version. Distributed WITHOUT ANY WARRANTY.
# See <https://www.gnu.org/licenses/agpl-3.0.html> for the full terms.
#
# Note the "Affero" part: if you run a modified version of this program and
# let other people use it over a network, you must offer them its source.
# Source: https://github.com/QZXP27/ai-roundtable

"""Render a debate session to PDF bytes, for the "Export as PDF" button.

Takes either a live DebateEngine or a StoredSession (engine.session_from_dict)
— anything exposing .topic / .exchanges_per_round / .participants / .messages.

Model output is markdown-ish, so body text goes through _render_body(), which
turns headings/lists/quotes into real layout instead of printing the raw
asterisks and hashes.
"""

import re
import time
from pathlib import Path

from fpdf import FPDF
from fpdf.errors import FPDFException

SUPPLEMENTAL = Path("/System/Library/Fonts/Supplemental")
SYSTEM_FONTS = Path("/System/Library/Fonts")

# Latin primary: real regular/bold/italic, so bold actually renders bold.
# All four styles must exist — fpdf2's markdown needs "BI" the moment a line
# contains both **bold** and italic, and errors out if it isn't registered.
LATIN_FONTS = {
    "": SUPPLEMENTAL / "Arial.ttf",
    "B": SUPPLEMENTAL / "Arial Bold.ttf",
    "I": SUPPLEMENTAL / "Arial Italic.ttf",
    "BI": SUPPLEMENTAL / "Arial Bold Italic.ttf",
}
# CJK fallback: Arial has no Chinese glyphs, STHeiti covers them (and has a
# genuine heavier weight for bold). There is no italic Chinese face, so the
# italic styles reuse the upright ones — otherwise italic CJK silently drops
# to missing glyphs.
CJK_FONTS = {
    "": SYSTEM_FONTS / "STHeiti Light.ttc",
    "B": SYSTEM_FONTS / "STHeiti Medium.ttc",
    "I": SYSTEM_FONTS / "STHeiti Light.ttc",
    "BI": SYSTEM_FONTS / "STHeiti Medium.ttc",
}
# Last-resort single font that at least covers both scripts.
UNIFONT = SUPPLEMENTAL / "Arial Unicode.ttf"

TEXT = (26, 32, 38)
MUTED = (120, 132, 145)
RULE = (214, 221, 228)
RULE_STRONG = (150, 162, 175)
PROVIDER_COLORS = {
    "claude": (183, 85, 42),
    "gemini": (47, 111, 224),
    "openai": (31, 143, 92),
    "deepseek": (112, 72, 196),
}
ACCENT = (47, 111, 224)

BODY_SIZE = 10.5
LINE_H = 5.4
TITLE_MAX = 70  # longer "topics" are really briefs; headline them, body the rest
LEFT = 18
RIGHT = 18


class DebatePDF(FPDF):
    """FPDF with a page-number footer; fpdf2 calls footer() on every page."""

    def footer(self):
        self.set_y(-12)
        self.set_font(self.body_font, "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, str(self.page_no()), align="C")


def _make_pdf() -> DebatePDF:
    pdf = DebatePDF()
    pdf.set_margins(LEFT, 16, RIGHT)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.body_font = "Helvetica"
    try:
        for style, path in LATIN_FONTS.items():
            pdf.add_font("body", style, str(path))
        pdf.body_font = "body"
        try:
            for style, path in CJK_FONTS.items():
                pdf.add_font("cjk", style, str(path))
            pdf.set_fallback_fonts(["cjk"])
        except (RuntimeError, FileNotFoundError, OSError):
            pass  # Latin-only is still readable; CJK would show as blanks.
    except (RuntimeError, FileNotFoundError, OSError):
        try:
            for style in ("", "B", "I", "BI"):
                pdf.add_font("body", style, str(UNIFONT))
            pdf.body_font = "body"
        except (RuntimeError, FileNotFoundError, OSError):
            pass  # core Helvetica: Latin only, but never crashes the export.
    pdf.add_page()
    pdf.set_text_color(*TEXT)
    pdf.set_font(pdf.body_font, "", BODY_SIZE)
    return pdf


# ---------- primitives ----------

def _chunked(text, n=60):
    """Break text into short pieces. Used only as a rescue path: fpdf2 can
    fail to lay out very long unbroken runs (CJK has no spaces to break on),
    and we would rather emit slightly odd wrapping than a failed export."""
    for para in text.split("\n"):
        if not para:
            yield ""
            continue
        for i in range(0, len(para), n):
            yield para[i:i + n]


def _cell(pdf, text, h=LINE_H, style="", size=BODY_SIZE, color=TEXT, markdown=True, indent=0):
    """One block of wrapped text. Left-aligned (fpdf2 justifies by default,
    which is what produced the huge inter-word gaps), cursor reset to the
    left margin so subsequent writes have room."""
    pdf.set_font(pdf.body_font, style, size)
    pdf.set_text_color(*color)
    if indent:
        pdf.set_left_margin(LEFT + indent)
        pdf.set_x(LEFT + indent)
    try:
        pdf.multi_cell(0, h, text, new_x="LMARGIN", new_y="NEXT", align="L", markdown=markdown)
    except FPDFException:
        # A failure can abort mid page-break and leave no page open, so put the
        # document back into a usable state before retrying.
        if pdf.page == 0:
            pdf.add_page()
        pdf.set_x(pdf.l_margin)
        for piece in _chunked(text):
            if pdf.page == 0:
                pdf.add_page()
            if not piece:
                pdf.ln(h * 0.5)
                continue
            try:
                pdf.multi_cell(0, h, piece, new_x="LMARGIN", new_y="NEXT", align="L", markdown=False)
            except FPDFException:
                pass  # unrenderable fragment; drop it rather than fail the export
    if indent:
        pdf.set_left_margin(LEFT)
        pdf.set_x(LEFT)


def _gap(pdf, mm=2.0):
    pdf.ln(mm)


def _rule(pdf, strong=False, gap_before=3.0, gap_after=3.0):
    _gap(pdf, gap_before)
    pdf.set_draw_color(*(RULE_STRONG if strong else RULE))
    pdf.set_line_width(0.4 if strong else 0.2)
    y = pdf.get_y()
    pdf.line(LEFT, y, pdf.w - RIGHT, y)
    _gap(pdf, gap_after)


def _keep_together(pdf, needed=26):
    """Start a new page rather than orphan a heading at the page bottom."""
    if pdf.get_y() > pdf.h - pdf.b_margin - needed:
        pdf.add_page()


# ---------- markdown-ish body rendering ----------

_BOLD_ONLY = re.compile(r"^\*\*(.+?)\*\*:?\s*$")
_ATX = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
# A lone *word* pair — fpdf2's markdown uses __italic__, so rewrite it rather
# than let the asterisks print literally.
_ITALIC = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")


def _inline(text: str) -> str:
    return _ITALIC.sub(r"__\1__", text)


def _render_body(pdf, text: str, indent=0):
    """Render one message's content, honouring simple markdown structure."""
    lines = (text or "").replace("\r\n", "\n").split("\n")
    pending_blank = False
    wrote_any = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            pending_blank = True
            continue
        if pending_blank and wrote_any:
            _gap(pdf, 1.8)  # collapse blank runs into one small spacer
        pending_blank = False

        atx = _ATX.match(line)
        bold_only = _BOLD_ONLY.match(line)
        if atx or bold_only:
            body = (atx.group(2) if atx else bold_only.group(1)).strip()
            _keep_together(pdf, 18)
            _gap(pdf, 1.5)
            _cell(pdf, body, h=5.6, style="B", size=BODY_SIZE + 1, indent=indent, markdown=False)
            _gap(pdf, 0.5)
            wrote_any = True
            continue

        quote = _QUOTE.match(line)
        if quote:
            _cell(pdf, _inline(quote.group(1)), color=MUTED, style="I", indent=indent + 5)
            wrote_any = True
            continue

        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if bullet or numbered:
            marker = "•" if bullet else f"{numbered.group(1)}."
            content = bullet.group(1) if bullet else numbered.group(2)
            _cell(pdf, f"{marker}  {_inline(content)}", indent=indent + 4)
            wrote_any = True
            continue

        _cell(pdf, _inline(line), indent=indent)
        wrote_any = True


# ---------- document ----------

def to_pdf_bytes(session) -> bytes:
    pdf = _make_pdf()
    provider_of = {p.name: p.provider for p in session.participants}

    # Title block. A "topic" can be a whole multi-paragraph brief, which makes
    # a terrible 18pt heading — so headline the first line and print the rest
    # as a normal-size Topic block below.
    topic = (session.topic or "").strip()
    first_line = next((l.strip() for l in topic.split("\n") if l.strip()), "AI Roundtable")
    headline = first_line if len(first_line) <= TITLE_MAX else first_line[:TITLE_MAX].rstrip() + "…"
    _cell(pdf, headline, h=8.5, style="B", size=18, markdown=False)
    _gap(pdf, 1)
    _cell(pdf, f"AI Roundtable · {time.strftime('%Y-%m-%d %H:%M')} · "
               f"{session.exchanges_per_round} exchanges/round",
          h=4.6, size=8.5, color=MUTED, markdown=False)
    _rule(pdf, strong=True, gap_before=3, gap_after=4)

    if topic and topic != headline:
        _cell(pdf, "Topic", h=6, style="B", size=12, markdown=False)
        _gap(pdf, 1.5)
        _render_body(pdf, topic)
        _rule(pdf, strong=True, gap_before=3, gap_after=4)

    # Participants
    if session.participants:
        _cell(pdf, "Participants", h=6, style="B", size=12, markdown=False)
        _gap(pdf, 1.5)
        for p in session.participants:
            color = PROVIDER_COLORS.get(p.provider, TEXT)
            _cell(pdf, p.name, h=5, style="B", size=BODY_SIZE, color=color, markdown=False)
            _cell(pdf, f"{p.provider} / {p.model}", h=4.4, size=8.5, color=MUTED, markdown=False)
            if p.persona:
                _cell(pdf, p.persona, h=4.8, style="I", size=9, color=MUTED, indent=3, markdown=False)
            _gap(pdf, 2)
        _rule(pdf, strong=True, gap_before=1, gap_after=4)

    current_round = 0
    first_turn_in_round = True
    for m in session.messages:
        if m.kind in ("turn", "moderator") and m.round != current_round:
            current_round = m.round
            _keep_together(pdf, 34)
            _gap(pdf, 2)
            _cell(pdf, f"Round {current_round}", h=7, style="B", size=14, markdown=False)
            _rule(pdf, gap_before=1.5, gap_after=4)
            first_turn_in_round = True

        if m.kind == "turn":
            if not first_turn_in_round:
                _rule(pdf, gap_before=3.5, gap_after=3.5)
            first_turn_in_round = False
            _keep_together(pdf, 26)
            color = PROVIDER_COLORS.get(provider_of.get(m.speaker, ""), TEXT)
            _cell(pdf, m.speaker, h=5.2, style="B", size=BODY_SIZE + 0.5, color=color, markdown=False)
            _gap(pdf, 1)
            _render_body(pdf, m.content)

        elif m.kind == "moderator":
            _gap(pdf, 3)
            _keep_together(pdf, 22)
            _cell(pdf, "Moderator", h=5, style="B", size=9.5, color=ACCENT, indent=4, markdown=False)
            _render_body(pdf, m.content, indent=4)
            _gap(pdf, 2)
            first_turn_in_round = False

        elif m.kind == "summary":
            _keep_together(pdf, 34)
            _rule(pdf, strong=True, gap_before=5, gap_after=3)
            _cell(pdf, f"Round {m.round} — Summary", h=6.4, style="B", size=12.5,
                  color=ACCENT, markdown=False)
            _gap(pdf, 2)
            _render_body(pdf, m.content)
            _rule(pdf, strong=True, gap_before=4, gap_after=4)
            first_turn_in_round = True

        elif m.kind == "verdict":
            pdf.add_page()
            _cell(pdf, "Final Verdict", h=8, style="B", size=16, markdown=False)
            _rule(pdf, strong=True, gap_before=2, gap_after=4)
            _render_body(pdf, m.content)

    return bytes(pdf.output())
