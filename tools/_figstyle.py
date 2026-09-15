"""Shared visual style for every generated figure.

One palette, one set of fonts, one background, so the whole documentation set
looks like it came from the same instrument.  Light background: it is legible
against both GitHub themes without needing two variants of every file.
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------- palette --
BG        = "#ffffff"
PANEL     = "#f7f8fa"
INK       = "#14161a"
MUTED     = "#6b7280"
GRID      = "#e4e7ec"

LASER     = "#e5484d"   # the 1550 nm beam / signal
BLUE      = "#2563eb"   # DC / transmission
VIOLET    = "#7c3aed"   # QWP, compensation, virgin branch
GREEN     = "#16a34a"   # up branch, "good"
AMBER     = "#f59e0b"   # pre-saturation, warnings
CYAN      = "#0891b2"   # secondary traces
PINK      = "#db2777"   # emphasis
SLATE     = "#475569"

SEQ = [BLUE, LASER, GREEN, VIOLET, AMBER, CYAN, PINK, SLATE]


def apply_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": BG,
        "savefig.facecolor": BG,
        "axes.facecolor": BG,
        "axes.edgecolor": "#c9cdd4",
        "axes.labelcolor": INK,
        "axes.titlecolor": INK,
        "axes.linewidth": 1.0,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.labelsize": 10,
        "axes.titlesize": 11,
        "axes.titleweight": "600",
        "legend.frameon": False,
        "legend.fontsize": 9,
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "figure.dpi": 160,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.25,
        "lines.linewidth": 1.8,
        "lines.solid_capstyle": "round",
        "mathtext.fontset": "dejavusans",
    })


def title(ax, text, subtitle=None):
    """Left-aligned title with an optional muted subtitle underneath."""
    ax.set_title(text, loc="left", pad=19 if subtitle else 8)
    if subtitle:
        ax.text(0.0, 1.018, subtitle, transform=ax.transAxes,
                ha="left", va="bottom", fontsize=8.5, color=MUTED)


def caption(fig, text, y=-0.02):
    fig.text(0.5, y, text, ha="center", va="top", fontsize=8.5, color=MUTED, wrap=True)
