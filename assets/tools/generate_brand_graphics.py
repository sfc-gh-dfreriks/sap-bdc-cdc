#!/usr/bin/env python3
"""
Brand graphics for the collateral: cover hero and the capture cycle.

    python3 assets/tools/generate_brand_graphics.py

Writes assets/diagrams/B1_hero.png and B2_cycle.png. The analytical diagrams
(D1-D10) are unchanged; these two exist only to give the covers and decks a
visual anchor in the Snowflake palette. No figure appears in them that is not
already stated in the collateral text.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt                                     # noqa: E402
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Circle, Wedge  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "diagrams"

SF_BLUE  = "#29B5E8"
MIDNIGHT = "#11567F"
DEEP     = "#0B3D5C"
TEAL     = "#158158"
ORANGE   = "#FF9F36"
PALE     = "#E8F6FD"
WHITE    = "#FFFFFF"
GREY     = "#5B6B7A"

plt.rcParams["font.family"] = ["Arial", "Helvetica", "DejaVu Sans"]


def _card(ax, x, y, w, h, title, sub, fill, fg=WHITE, size=15, sub_size=10.5):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=2.2",
                                fc=fill, ec="none"))
    ax.text(x + w / 2, y + h * 0.60, title, ha="center", va="center", color=fg,
            fontsize=size, fontweight="bold")
    ax.text(x + w / 2, y + h * 0.28, sub, ha="center", va="center", color=fg,
            fontsize=sub_size, alpha=0.92)


def _arrow(ax, x1, y1, x2, y2, color=SF_BLUE, lw=3.2, rad=0.0):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle="-|>", mutation_scale=22,
                                 color=color, lw=lw, connectionstyle=f"arc3,rad={rad}"))


def hero(path: Path) -> None:
    """SAP share -> two signals -> one engine -> Gold, on a transparent background."""
    fig = plt.figure(figsize=(12, 4.6), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 120); ax.set_ylim(0, 46); ax.axis("off")
    _card(ax, 2, 15, 22, 16, "SAP BDC", "catalog-linked share", WHITE, fg=DEEP)
    _card(ax, 36, 27, 26, 13, "Standard products", "__OPERATION_TYPE · __TIMESTAMP", SF_BLUE,
          size=13, sub_size=8.5)
    _card(ax, 36, 6, 26, 13, "Custom products", "replication timestamp", TEAL, size=13,
          sub_size=9.5)
    _card(ax, 74, 13, 22, 20, "Dynamic tables", "REFRESH_MODE =\nINCREMENTAL", ORANGE, fg=DEEP,
          size=14, sub_size=10)
    _card(ax, 104, 15, 15, 16, "Gold", "minutes,\nnot hours", WHITE, fg=DEEP, size=14,
          sub_size=10)
    _arrow(ax, 24.5, 26, 35.5, 33, WHITE); _arrow(ax, 24.5, 20, 35.5, 13, WHITE)
    _arrow(ax, 62.5, 33, 73.5, 27, WHITE); _arrow(ax, 62.5, 13, 73.5, 19, WHITE)
    _arrow(ax, 96.5, 23, 103.5, 23, WHITE)
    ax.text(49, 43.4, "TWO SIGNALS", ha="center", color=WHITE, fontsize=10, fontweight="bold",
            alpha=0.85)
    ax.text(85, 36.2, "ONE ENGINE", ha="center", color=WHITE, fontsize=10, fontweight="bold",
            alpha=0.85)
    fig.savefig(path, transparent=True)
    plt.close(fig)


def cycle(path: Path) -> None:
    """The capture cycle: poll, refresh, reconcile, validate."""
    fig = plt.figure(figsize=(7, 7), dpi=200)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(-10, 10); ax.set_ylim(-10, 10); ax.axis("off")
    steps = [("Poll", "free watermark check\n0 MB scanned", SF_BLUE),
             ("Refresh", "incremental dynamic\ntables, NO_DATA <1 s", MIDNIGHT),
             ("Reconcile", "row counts, source\nvs Silver, daily", TEAL),
             ("Validate", "metadata and declared\nkeys before trust", ORANGE)]
    import math
    for i, (t, s, c) in enumerate(steps):
        a0 = 90 - i * 90
        ax.add_patch(Wedge((0, 0), 9.6, a0 - 87, a0 - 3, width=5.4, fc=c, ec="none"))
        a = math.radians(a0 - 45)
        x, y = 6.9 * math.cos(a), 6.9 * math.sin(a)
        fg = DEEP if c == ORANGE else WHITE
        ax.text(x, y + 0.75, t, ha="center", va="center", fontsize=17, fontweight="bold", color=fg)
        ax.text(x, y - 0.75, s, ha="center", va="center", fontsize=9, color=fg)
    ax.add_patch(Circle((0, 0), 3.6, fc=PALE, ec="none"))
    ax.text(0, 0.45, "CAPTURE", ha="center", va="center", fontsize=12, fontweight="bold",
            color=MIDNIGHT)
    ax.text(0, -0.6, "CYCLE", ha="center", va="center", fontsize=12, fontweight="bold",
            color=MIDNIGHT)
    fig.savefig(path, transparent=True)
    plt.close(fig)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    hero(OUT / "B1_hero.png")
    cycle(OUT / "B2_cycle.png")
    print("wrote", OUT / "B1_hero.png", OUT / "B2_cycle.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
