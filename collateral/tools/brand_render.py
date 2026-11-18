"""
Snowflake-template renderers for the collateral decks.

The slide CONTENT stays in generate_collateral.py (SLIDES, EXEC_SLIDES); this
module only decides how it looks. Decks are built on the official Snowflake
January 2026 template, so colours, fonts, footers and cover art come from the
template rather than from code.

Layout rules this module enforces:
- every content slide gets a title AND a subtitle;
- no bullet-only slides: bullets become numbered cards, caveats and risk
  statements become coloured callouts, tables are native tables;
- a diagram slide carries its takeaway in the subtitle;
- Arial only, theme colours only, no shape outlines.
"""
from __future__ import annotations

import os
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData  # noqa: F401  (kept for future chart slides)
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

TEMPLATE = Path(os.environ.get(
    "SNOWFLAKE_PPTX_TEMPLATE",
    Path.home() / ".snowflake/cortex/skills/pptx/snowflake-template.pptx"))

# Theme colours of the January 2026 template (see the pptx skill).
DK1      = RGBColor(0x00, 0x00, 0x00)
WHITE    = RGBColor(0xFF, 0xFF, 0xFF)
SF_BLUE  = RGBColor(0x05, 0x8D, 0xC7)     # accent1
TEAL     = RGBColor(0x15, 0x81, 0x58)     # dk2
GREEN    = RGBColor(0x50, 0xB4, 0x32)     # accent2
ORANGE   = RGBColor(0xED, 0x56, 0x1B)     # accent3
LBLUE    = RGBColor(0x24, 0xCB, 0xE5)     # accent5
LT2      = RGBColor(0xF3, 0xF3, 0xF3)
GREY     = RGBColor(0x59, 0x59, 0x59)
PALE     = RGBColor(0xE6, 0xF4, 0xFA)

HERO_IMAGE = Path(__file__).resolve().parents[2] / "assets" / "diagrams" / "B1_hero.png"
L_CONTENT, L_COVER, L_CHAPTER, L_THANKS = 0, 13, 18, 27
X0, X1, Y0, Y1 = 0.40, 9.55, 1.28, 5.08          # content safe zone, inches

# One line under each title. Keyed on the slide title in generate_collateral.py;
# a missing key falls back to a generic line rather than an empty subtitle.
SUBTITLES = {
    "The challenge": "Zero-copy shares arrive without a change signal",
    "Why standard CDC is refused": "Streams, CHANGES() and change tracking are all blocked",
    "Two tracks, one engine": "Two kinds of signal, the same incremental engine",
    "Track 1 - Custom data products": "A replication timestamp the pipeline owner adds",
    "Track 2 - Standard data products": "Four metadata columns that SAP generates",
    "The headline risk: never trust a local copy": "Copies silently corrupt the metadata columns",
    "The engine: dynamic tables": "REFRESH_MODE = INCREMENTAL, stated explicitly, always",
    "Mechanism comparison": "What each capture mechanism can and cannot do",
    "Decision guide": "Defaults first, exception paths only when needed",
    "The readiness probe": "Assess a tenant before deploying anything",
    "Next steps and open asks to SAP": "What to do now, and what we need SAP to confirm",
    "The situation": "Why freshness on SAP BDC needs a capture pattern",
    "What it costs, measured": "Measured on live tenants, result cache disabled",
    "The recommendation": "Where to invest, in order",
    "What we do not yet know": "Open points, stated plainly",
    "Next steps, and what we are asking SAP": "Actions for us, and asks for SAP",
    "Two kinds of data product, two different jobs": "Same engine; who supplies the signal differs",
    "Standard data products - a procedure": "SAP generates the change columns: validate, then poll",
    "Custom data products - a specification": "What the pipeline owner must configure",
    "The risk on standard products: never trust a copy": "Copies silently corrupt the metadata columns",
    "The finding that changed our recommendation": "Deletion and blocking codes the Gold layer drops",
}

# Big-number slides. Every value and label is quoted from the deck's own text;
# _stats_for() drops any stat whose value does not occur in that deck, so a stat
# can never introduce a figure the deck does not already state.
STATS = {
    "deck": [
        ("0.00 MB", "watermark poll on a 108M-row object, vs 7,035.80 MB for a full read"),
        ("984 ms", "no-change refresh returns NO_DATA without reading source data"),
        ("4,314,292", "rows in the initial incremental load, in 18.3 s"),
        ("735,305", "of 735,305 rows held corrupted metadata in one local copy"),
    ],
    "exec-deck": [
        ("0.000 MB", "to detect no change on a standard product, in 2 ms"),
        ("610 ms", "refresh when nothing moved: NO_DATA"),
        ("4,314,292", "rows refreshed in 18.3 s on a custom product"),
        ("19.0%", "of purchase order items carry a deletion or blocking code"),
    ],
}

# Chapter dividers, inserted before the named slide (solution deck only).
CHAPTERS = {
    "deck": [("The challenge", "01", "The problem"),
             ("Two tracks, one engine", "02", "The pattern"),
             ("The engine: dynamic tables", "03", "Running it")],
    "exec-deck": [],
}


# ── low-level helpers ────────────────────────────────────────────────────────

def _ph(slide, idx):
    for p in slide.placeholders:
        if p.placeholder_format.idx == idx:
            return p
    return None


def _set_ph(slide, idx, text, size=None, bold=None, color=None):
    p = _ph(slide, idx)
    if p is None:
        return None
    tf = p.text_frame
    tf.text = text
    for para in tf.paragraphs:
        for r in para.runs:
            r.font.name = "Arial"
            if size:
                r.font.size = Pt(size)
            if bold is not None:
                r.font.bold = bold
            if color is not None:
                r.font.color.rgb = color
    return p


def _box(slide, x, y, w, h, lines, fill=None, color=DK1, size=11, bold=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, shape=MSO_SHAPE.RECTANGLE,
         margin=0.10):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    s.line.fill.background()
    s.shadow.inherit = False
    tf = s.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(0.05)
    for i, ln in enumerate([lines] if isinstance(lines, str) else lines):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        b, t, sz, c = bold, ln, size, color
        if isinstance(ln, tuple):           # (text, size, bold, colour)
            t, sz, b, c = ln
        run = para.add_run()
        run.text = t
        para.alignment = align
        f = run.font
        f.name, f.size, f.bold, f.color.rgb = "Arial", Pt(sz), b, c
    return s


def _title(slide, text, subtitle):
    n = len(text)
    size = 24 if n <= 30 else 21 if n <= 38 else 18 if n <= 48 else 16
    _set_ph(slide, 0, text.upper(), size=size)
    _set_ph(slide, 1, subtitle or "SAP BDC change data capture on Snowflake", size=14)


def _content(prs, title, subtitle):
    s = prs.slides.add_slide(prs.slide_layouts[L_CONTENT])
    _title(s, title, subtitle)
    return s


def _notes(slide, text):
    if text:
        slide.notes_slide.notes_text_frame.text = text


def _cards(slide, items, x, y, w, h, chip=SF_BLUE, cols=None, size=None, fill=LT2):
    """Numbered cards in a grid. Text is never truncated; size scales with density."""
    n = len(items)
    if not n:
        return
    cols = cols or (2 if n >= 4 else 1)
    rows = -(-n // cols)
    gap = 0.12
    cw = (w - gap * (cols - 1)) / cols
    ch = (h - gap * (rows - 1)) / rows
    longest = max(len(t) for t in items)
    if size is None:
        size = 14 if rows <= 2 and longest < 110 else 13 if rows <= 3 and longest < 120 else 12 if rows <= 3 else 11
        if longest > 150 and size > 11:
            size -= 1
    import re
    items = [re.sub(r"^\d+\.\s+", "", t) for t in items]
    for i, text in enumerate(items):
        r, c = divmod(i, cols)
        cx, cy = x + c * (cw + gap), y + r * (ch + gap)
        _box(slide, cx, cy, cw, ch, "", fill=fill)
        d = min(0.36, ch - 0.12)
        _box(slide, cx + 0.10, cy + (ch - d) / 2, d, d, str(i + 1), fill=chip, color=WHITE,
             size=11, bold=True, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
             shape=MSO_SHAPE.OVAL, margin=0)
        _box(slide, cx + 0.18 + d, cy, cw - 0.24 - d, ch, text, size=size,
             anchor=MSO_ANCHOR.MIDDLE, margin=0.06)


def _image_fit(slide, path, x, y, w, h):
    from PIL import Image
    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, phh = iw * scale, ih * scale
    slide.shapes.add_picture(str(path), Inches(x + (w - pw) / 2), Inches(y + (h - phh) / 2),
                             Inches(pw), Inches(phh))


# ── slide renderers ──────────────────────────────────────────────────────────

def _cover(prs, spec):
    s = prs.slides.add_slide(prs.slide_layouts[L_COVER])
    title = spec["title"].replace("\n", " ")
    parts = [p for p in spec.get("subtitle", "").split("\n") if p.strip()]
    _set_ph(s, 3, title.upper(), size=40 if len(title) <= 34 else 32 if len(title) <= 46 else 28)
    _set_ph(s, 0, parts[0] if parts else "")
    _set_ph(s, 2, "  |  ".join(parts[1:]) if len(parts) > 1 else "")
    return s


def _chapter(prs, number, label):
    s = prs.slides.add_slide(prs.slide_layouts[L_CHAPTER])
    _set_ph(s, 1, f"{number}\n{label.upper()}")
    return s


def _stats(prs, stats, title="At a glance", subtitle="Measured on live SAP BDC tenants"):
    s = _content(prs, title, subtitle)
    n = len(stats)
    gap = 0.18
    w = (X1 - X0 - gap * (n - 1)) / n
    colours = [SF_BLUE, TEAL, SF_BLUE, ORANGE]
    for i, (value, label) in enumerate(stats):
        x = X0 + i * (w + gap)
        _box(s, x, 1.55, w, 0.08, "", fill=colours[i % 4])
        size = 30 if len(value) <= 7 else 25 if len(value) <= 9 else 21
        _box(s, x, 1.75, w, 0.95, value, color=colours[i % 4], size=size, bold=True,
             anchor=MSO_ANCHOR.MIDDLE, margin=0.02)
        _box(s, x, 2.70, w, 0.85, label, size=12, color=DK1, margin=0.02)
    hero = HERO_IMAGE
    if hero.exists():
        _box(s, X0, 3.65, X1 - X0, 1.43, "", fill=RGBColor(0x0B, 0x3D, 0x5C))
        _image_fit(s, hero, X0 + 0.2, 3.70, X1 - X0 - 0.4, 1.33)
    return s


def _bullets(prs, spec):
    s = _content(prs, spec["title"], SUBTITLES.get(spec["title"]))
    _cards(s, spec["bullets"], X0, Y0 + 0.05, X1 - X0, Y1 - Y0 - 0.05)
    return s


def _bullets_detail(prs, spec):
    s = _content(prs, spec["title"], SUBTITLES.get(spec["title"]))
    _box(s, X0, Y0, X1 - X0, 0.78, spec["intro"], fill=PALE, size=11,
         anchor=MSO_ANCHOR.MIDDLE, margin=0.14)
    caveat = spec.get("caveat")
    bottom = Y1 - (0.62 if caveat else 0)
    _cards(s, spec["bullets"], X0, Y0 + 0.90, X1 - X0, bottom - Y0 - 0.98)
    if caveat:
        _box(s, X0, Y1 - 0.55, X1 - X0, 0.55, "CAVEAT   " + caveat,
             fill=RGBColor(0xFF, 0xE3, 0xC4), size=10.5, anchor=MSO_ANCHOR.MIDDLE, margin=0.14)
    return s


def _risk(prs, spec):
    s = _content(prs, spec["title"], SUBTITLES.get(spec["title"]))
    _box(s, X0, Y0, X1 - X0, 0.70, spec["risk_statement"], fill=ORANGE, color=WHITE, size=12,
         bold=True, anchor=MSO_ANCHOR.MIDDLE, margin=0.16)
    _cards(s, spec["bullets"], X0, Y0 + 0.82, X1 - X0, Y1 - Y0 - 1.55, chip=ORANGE)
    _box(s, X0, Y1 - 0.62, X1 - X0, 0.62, "RULE   " + spec["rule"], fill=TEAL, color=WHITE,
         size=11, bold=True, anchor=MSO_ANCHOR.MIDDLE, margin=0.16)
    return s


def _table(prs, spec):
    s = _content(prs, spec["title"], SUBTITLES.get(spec["title"]))
    headers, rows = spec["headers"], spec["rows"]
    nr, nc = len(rows) + 1, len(headers)
    h = min(Y1 - Y0, 0.42 * nr)
    t = s.shapes.add_table(nr, nc, Inches(X0), Inches(Y0), Inches(X1 - X0), Inches(h)).table
    for c, frac in enumerate(spec.get("col_widths") or [1 / nc] * nc):
        t.columns[c].width = Inches((X1 - X0) * frac)
    size = 11 if nr <= 7 else 10
    for r in range(nr):
        vals = headers if r == 0 else rows[r - 1]
        for c, v in enumerate(vals):
            cell = t.cell(r, c)
            cell.text = str(v)
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            run = cell.text_frame.paragraphs[0].runs[0] if cell.text_frame.paragraphs[0].runs else None
            cell.fill.solid()
            if r == 0:
                cell.fill.fore_color.rgb = SF_BLUE
            else:
                cell.fill.fore_color.rgb = LT2 if r % 2 else WHITE
            if run:
                run.font.name, run.font.size = "Arial", Pt(size)
                run.font.bold = (r == 0 or c == 0)
                run.font.color.rgb = WHITE if r == 0 else DK1
    return s


def _figure(prs, spec, assets: Path):
    cap = spec.get("caption", "").strip()
    sub = SUBTITLES.get(spec["title"]) if spec["title"] in SUBTITLES and len(cap) > 70 else None
    s = _content(prs, spec["title"], sub or cap.rstrip("."))
    if sub:
        _image_fit(s, assets / spec["file"], X0, Y0, X1 - X0, Y1 - Y0 - 0.42)
        _box(s, X0, Y1 - 0.38, X1 - X0, 0.38, cap, size=10, color=GREY, margin=0.02)
    else:
        _image_fit(s, assets / spec["file"], X0, Y0, X1 - X0, Y1 - Y0)
    return s


def _closing(prs, spec):
    s = _content(prs, spec["title"], SUBTITLES.get(spec["title"]))
    half = (X1 - X0 - 0.25) / 2
    _box(s, X0, Y0, half, 0.40, "NEXT STEPS", fill=SF_BLUE, color=WHITE, size=12, bold=True,
         anchor=MSO_ANCHOR.MIDDLE, margin=0.14)
    _cards(s, spec["next_steps"], X0, Y0 + 0.50, half, Y1 - Y0 - 0.50, cols=1)
    x2 = X0 + half + 0.25
    _box(s, x2, Y0, half, 0.40, "OPEN ASKS TO SAP", fill=TEAL, color=WHITE, size=12,
         bold=True, anchor=MSO_ANCHOR.MIDDLE, margin=0.14)
    asks = [a.split(") ", 1)[1] if a.startswith("(") and ") " in a else a
            for a in spec["open_asks"]]
    _cards(s, asks, x2, Y0 + 0.50, half, Y1 - Y0 - 0.50, chip=TEAL, cols=1)
    return s


def _thanks(prs, line):
    s = prs.slides.add_slide(prs.slide_layouts[L_THANKS])
    _set_ph(s, 1, line)
    return s


# ── public entry point ───────────────────────────────────────────────────────

def _stats_for(deck_key, slides):
    text = repr(slides)
    return [st for st in STATS.get(deck_key, []) if st[0] in text]


def gen_deck_branded(out_path: Path, slides: list, assets: Path, deck_key: str,
                     normalize=None) -> int:
    """Render a slide list on the Snowflake template. Returns the slide count."""
    prs = Presentation(str(TEMPLATE))
    ids = prs.slides._sldIdLst
    for sid in list(ids):                       # drop the template's sample slides
        prs.part.drop_rel(sid.rId)
        ids.remove(sid)

    chapters = {t: (n, lbl) for t, n, lbl in CHAPTERS.get(deck_key, [])}
    stats = _stats_for(deck_key, slides)
    stats_done = False
    seen_chapters = set()

    for spec in slides:
        kind, title = spec["type"], spec.get("title", "")
        if title in chapters and title not in seen_chapters and kind != "figure":
            seen_chapters.add(title)
            _chapter(prs, *chapters[title])
        if kind == "title":
            _cover(prs, spec)
        elif kind == "bullets":
            _bullets(prs, spec)
        elif kind == "bullets_detail":
            _bullets_detail(prs, spec)
        elif kind == "risk":
            _risk(prs, spec)
        elif kind == "table":
            _table(prs, spec)
        elif kind == "figure":
            _figure(prs, spec, assets)
        elif kind == "closing":
            _closing(prs, spec)
        else:
            raise SystemExit(f"unknown slide type {kind!r} on {title!r}")
        # Big numbers right after the first content slide, where the argument starts.
        if stats and not stats_done and kind in ("bullets", "bullets_detail"):
            _stats(prs, stats)
            stats_done = True

    _thanks(prs, "THANK YOU")
    prs.save(str(out_path))
    if normalize:
        normalize(out_path)
    return len(prs.slides)
