#!/usr/bin/env python3
"""Generate the standard-versus-custom CDC diagrams.

Why this exists. D1 to D7 in assets/diagrams were committed as PNGs with no
source, so a wording change means editing a binary. Everything added here ships
with its generator, and matches the existing files' geometry - 1729 px wide at
200 dpi, which is 8.645 in - so old and new sit together in the same documents
without a visible seam.

    python3 assets/tools/generate_diagrams.py            # all
    python3 assets/tools/generate_diagrams.py standard   # one

Deterministic: matplotlib writes no timestamp into a PNG, and nothing here is
random, so re-running produces byte-identical files and an empty git diff.

Output:
    assets/diagrams/D8_standard_track.png    the procedure - columns already exist
    assets/diagrams/D9_custom_track.png      the specification - flags to apply
    assets/diagrams/D10_delineation.png      the two side by side
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                        # noqa: E402
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "diagrams"

# Repo palette, shared with collateral/tools/generate_collateral.py.
NAVY  = "#1F3A6B"
BLUE  = "#2C5282"
SLATE = "#4A5568"
MID   = "#718096"
RULE  = "#CBD5E0"
LIGHT = "#EBF4FF"
ALT   = "#F7F9FC"
WHITE = "#FFFFFF"

# Status colours. Used consistently across all three diagrams so a reader who
# learns the key once can read every picture: green is present and usable, amber
# is present but not being used, red is absent and must be asked for.
OK_FILL,   OK_EDGE   = "#E6F4EA", "#1E7E34"
WARN_FILL, WARN_EDGE = "#FFF6E5", "#B7791F"
GAP_FILL,  GAP_EDGE  = "#FDECEA", "#C53030"
OFF_FILL,  OFF_EDGE  = "#F1F3F5", "#A0AEC0"

W_IN, DPI = 8.645, 200          # 1729 px wide, matching D1-D7


def _fig(h_in: float):
    fig, ax = plt.subplots(figsize=(W_IN, h_in), dpi=DPI)
    # Use the whole canvas. Cropping with bbox_inches="tight" on save would make
    # every diagram a different width depending on its content, and they have to
    # sit beside D1-D7 (all exactly 1729 px) in the same documents.
    fig.subplots_adjust(left=0.015, right=0.985, top=0.975, bottom=0.02)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    fig.patch.set_facecolor(WHITE)
    return fig, ax


def _box(ax, x, y, w, h, title, lines=(), fill=WHITE, edge=RULE,
         title_color=NAVY, title_size=8.2, body_size=6.9, lw=1.0, radius=1.6):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw, edgecolor=edge, facecolor=fill, zorder=2))
    n_title = title.count("\n") + 1
    ty = y + h - 2.8 if lines else y + h / 2
    ax.text(x + w / 2, ty, title, ha="center",
            va="center" if not lines else "top",
            fontsize=title_size, fontweight="bold", color=title_color, zorder=3)
    first = ty - 4.0 - (n_title - 1) * 3.6
    for i, ln in enumerate(lines):
        ax.text(x + w / 2, first - i * 3.5, ln, ha="center", va="top",
                fontsize=body_size, color=SLATE, zorder=3)


def _arrow(ax, x1, y1, x2, y2, label=None, color=BLUE, label_size=6.4,
           label_dy=1.8, style="-|>"):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=11,
        linewidth=1.25, color=color, zorder=4,
        shrinkA=0, shrinkB=0))
    if label:
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + label_dy, label, ha="center",
                va="bottom", fontsize=label_size, color=color,
                fontweight="bold", zorder=5)


def _title(ax, text, sub=None):
    ax.text(0, 99, text, ha="left", va="top", fontsize=11,
            fontweight="bold", color=NAVY)
    if sub:
        import textwrap
        ax.text(0, 92.5, "\n".join(textwrap.wrap(sub, 104)),
                ha="left", va="top", fontsize=7.6, color=MID, linespacing=1.35)


def _footnote(ax, text, y=1.5):
    ax.text(0, y, text, ha="left", va="bottom", fontsize=6.4, color=MID,
            style="italic")


def _key(ax, items, x=100, y=97):
    """Status key, right-aligned at the top."""
    for i, (label, fill, edge) in enumerate(items):
        yy = y - i * 5.0
        ax.add_patch(FancyBboxPatch(
            (x - 26, yy - 2.6), 2.4, 2.4,
            boxstyle="round,pad=0,rounding_size=0.5",
            linewidth=0.9, edgecolor=edge, facecolor=fill, zorder=3))
        ax.text(x - 22.6, yy - 1.4, label, ha="left", va="center",
                fontsize=6.2, color=SLATE, zorder=3)


# ---------------------------------------------------------------------------
# D8 - standard data products: the procedure
# ---------------------------------------------------------------------------

def standard(path: Path) -> None:
    fig, ax = _fig(4.35)
    _title(ax,
           "CDC on a STANDARD SAP BDC data product - the procedure",
           "SAP generates the change columns. Nothing is configured in the "
           "source system; the work is to validate them and poll them cheaply.")

    y, h = 46, 26
    _box(ax, 0, y, 19, h, "SAP BDC\nstandard data product",
         ["catalog-linked", "zero-copy, read-only", "Iceberg on SAP catalog"],
         fill=LIGHT, edge=BLUE)

    _box(ax, 22.5, y, 22, h, "Four columns SAP ships",
         ['__OPERATION_TYPE', '__TIMESTAMP  (TEXT)',
          'load_type_<hash>', 'run_id_<hash>  - unused'],
         fill=ALT, edge=RULE)

    _box(ax, 48, y, 20, h, "1. Validate  (gate)",
         ["SP_VALIDATE_STANDARD", "reject if op cardinality > 12,",
          "if no timestamp parses, or if", "op equals timestamp"],
         fill=WARN_FILL, edge=WARN_EDGE)

    _box(ax, 71.5, y, 28.5, h, "2. Poll, then 3. Refresh",
         ['MAX("__TIMESTAMP") as TEXT', "0.000 MB / 2 ms - free",
          "advanced?  ALTER DYNAMIC", "TABLE ... REFRESH"],
         fill=OK_FILL, edge=OK_EDGE)

    _arrow(ax, 19, y + h / 2, 22.5, y + h / 2)
    _arrow(ax, 44.5, y + h / 2, 48, y + h / 2)
    _arrow(ax, 68, y + h / 2, 71.5, y + h / 2)

    # Gold layer below
    _box(ax, 22.5, 14, 77.5, 21, "Gold layer - dynamic tables, REFRESH_MODE = INCREMENTAL",
         ["Pin REFRESH_MODE explicitly. Left on AUTO, Snowflake picks FULL on a "
          "non-trivial query, and FULL cascades:",
          "change tracking is unsupported on a FULL dynamic table, so every "
          "object downstream is forced to FULL too.",
          "Measured on the reference share: INCREMENTAL in 5,241 ms, then "
          "NO_DATA in 610 ms when nothing had changed."],
         fill=WHITE, edge=BLUE)
    _arrow(ax, 85.5, y, 85.5, 35, label=None)

    _footnote(ax,
              "Compare the watermark AS TEXT. The format is fixed-width, so text "
              "order is chronological order; parsing it first costs 0.310 MB "
              "instead of 0.000 MB and buys nothing.")
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# D9 - custom data products: the specification
# ---------------------------------------------------------------------------

def custom(path: Path) -> None:
    fig, ax = _fig(4.55)
    _title(ax,
           "CDC on a CUSTOM SAP BDC data product - the flags to apply",
           "No connector metadata is generated. Two of the four capabilities are "
           "already present in the share; one is present but discarded, and one "
           "must be asked for.")
    _key(ax, [("present and usable", OK_FILL, OK_EDGE),
              ("present, being discarded", WARN_FILL, WARN_EDGE),
              ("absent - ask SAP", GAP_FILL, GAP_EDGE)])

    _box(ax, 0, 41, 17.5, 27, "SAP source\nvia SLT",
         ["extension fields", "configured per", "data product"],
         fill=LIGHT, edge=BLUE)

    # the four flags
    fx, fw, fh = 21, 37, 6.4
    flags = [
        ("1. Change timestamp  ZZLASTCHGDATE",
         "TIMESTAMP_NTZ, NOT NULL - 0 nulls / 114M rows", OK_FILL, OK_EDGE),
        ("2. Deletion / blocking codes",
         "populated on 19.0% of PO items - dropped at Gold", WARN_FILL, WARN_EDGE),
        ("3. Replication operation code",
         "insert / update / physical delete - ASK FOR THIS", GAP_FILL, GAP_EDGE),
        ("4. Load-phase flag",
         "optional - inferable from the timestamp spread", OFF_FILL, OFF_EDGE),
    ]
    for i, (t, s, fill, edge) in enumerate(flags):
        yy = 63.5 - i * (fh + 1.5)
        ax.add_patch(FancyBboxPatch(
            (fx, yy), fw, fh, boxstyle="round,pad=0,rounding_size=1.1",
            linewidth=1.0, edgecolor=edge, facecolor=fill, zorder=2))
        ax.text(fx + 1.6, yy + fh - 2.2, t, ha="left", va="center",
                fontsize=7.0, fontweight="bold", color=NAVY, zorder=3)
        ax.text(fx + 1.6, yy + 2.1, s, ha="left", va="center",
                fontsize=6.3, color=SLATE, zorder=3)

    ax.text(fx, 74.6, "What the share must carry", ha="left", va="bottom",
            fontsize=7.6, fontweight="bold", color=BLUE)
    _arrow(ax, 17.5, 54.5, fx, 54.5)

    _box(ax, 62, 41, 38, 27, "Same engine as the standard track",
         ['poll MAX(ZZLASTCHGDATE) - 0.00 MB / 47-79 ms',
          "refresh the dynamic table only when it moves",
          "0.0000% pruning on a 108,615,680-row object",
          "4,314,292 rows in 18.3 s, then NO_DATA in 984 ms"],
         fill=OK_FILL, edge=OK_EDGE)
    _arrow(ax, fx + fw, 54.5, 62, 54.5)

    _box(ax, 0, 12, 100, 24,
         "The one action that needs nothing from SAP",
         ["Items 1 and 2 are already in the share. Surfacing the deletion and "
          "blocking codes from Silver to Gold is free, and on the reference",
          "account it governs 19.0% of purchase order items and 42.2% of "
          "reported USD ordered value - 29.33 bn of 69.50 bn.",
          "Only item 3 is a request to the pipeline owner, and the standard "
          "track has not been shown to satisfy it either."],
         fill=WARN_FILL, edge=WARN_EDGE)

    _footnote(ax,
              "A row that simply stops being carried is unrecoverable by any "
              "watermark. Either an operation code is emitted, or deletion must "
              "be soft - that is the whole of the gap.")
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)


# ---------------------------------------------------------------------------
# D10 - the delineation, side by side
# ---------------------------------------------------------------------------

def delineation(path: Path) -> None:
    fig, ax = _fig(4.9)
    _title(ax,
           "Standard versus custom data products - what differs, and what does not",
           "The engine is identical. What differs is who supplies the change "
           "signal, and whether a delete can be represented at all.")

    colw = 46
    for x, head, tint in ((4, "STANDARD data product", LIGHT),
                          (54, "CUSTOM data product", ALT)):
        ax.add_patch(FancyBboxPatch(
            (x, 8), colw, 76, boxstyle="round,pad=0,rounding_size=1.6",
            linewidth=1.1, edgecolor=BLUE, facecolor=tint, zorder=1))
        ax.text(x + colw / 2, 80.5, head, ha="center", va="center",
                fontsize=8.6, fontweight="bold", color=NAVY, zorder=3)

    ax.text(4 + colw / 2, 75.5, "a PROCEDURE - the columns already exist",
            ha="center", fontsize=6.8, color=BLUE, style="italic", zorder=3)
    ax.text(54 + colw / 2, 75.5, "a SPECIFICATION - the flags must be applied",
            ha="center", fontsize=6.8, color=BLUE, style="italic", zorder=3)

    rows = [
        ("Who supplies the signal",
         "SAP, automatically", "the pipeline owner, by choice"),
        ("Change timestamp",
         '__TIMESTAMP - TEXT, comma separator,\ncasts silently to NULL',
         "ZZLASTCHGDATE - TIMESTAMP_NTZ,\n0 nulls across 114M rows"),
        ("Operation code",
         "__OPERATION_TYPE exists;\nonly L and A ever observed",
         "none generated; business deletion\ncodes present but discarded"),
        ("Can represent a delete",
         "in principle - no delete code seen",
         "not physically; logically, yes,\nvia the deletion codes"),
        ("Predicate pruning",
         "not demonstrated\n(single micropartition)",
         "0.0000% of a full read\non 108,615,680 rows"),
        ("Free no-change poll",
         "0.000 MB / 2 ms\n(only if compared as text)", "0.00 MB / 47-79 ms"),
        ("Engine",
         "dynamic tables, REFRESH_MODE = INCREMENTAL - identical on both", ""),
    ]
    y = 70
    for i, (label, a, b) in enumerate(rows):
        span = (b == "")
        ax.text(50, y, label, ha="center", va="center", fontsize=6.9,
                fontweight="bold", color=NAVY, zorder=5,
                bbox=dict(boxstyle="round,pad=0.28", facecolor=WHITE,
                          edgecolor=RULE, linewidth=0.7))
        if span:
            ax.text(50, y - 5.4, a, ha="center", va="center", fontsize=6.5,
                    color=SLATE, zorder=3)
        else:
            ax.text(4 + colw / 2, y - 5.4, a, ha="center", va="center",
                    fontsize=6.5, color=SLATE, zorder=3)
            ax.text(54 + colw / 2, y - 5.4, b, ha="center", va="center",
                    fontsize=6.5, color=SLATE, zorder=3)
        if i < len(rows) - 1:
            ax.plot([6, 98], [y - 9.4, y - 9.4], color=RULE, linewidth=0.6,
                    zorder=1)
        y -= 8.9

    _footnote(ax,
              "Prefer a standard product where one exists - only it carries an "
              "operation column. But product type is a weaker determinant of CDC "
              "quality than configuration and validation are.")
    fig.savefig(path, dpi=DPI, facecolor=WHITE)
    plt.close(fig)


TARGETS = {
    "standard":    ("D8_standard_track.png", standard),
    "custom":      ("D9_custom_track.png",   custom),
    "delineation": ("D10_delineation.png",   delineation),
}


def main(argv=None) -> int:
    args = (argv or sys.argv)[1:] or list(TARGETS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in args:
        if name not in TARGETS:
            print(f"Unknown diagram: {name}. Choose from: {', '.join(TARGETS)}")
            continue
        filename, fn = TARGETS[name]
        out = OUT_DIR / filename
        fn(out)
        from PIL import Image
        w, h = Image.open(out).size
        print(f"  {filename:<26} {w}x{h}  {out.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
