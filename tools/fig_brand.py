"""Brand assets: the repository banner and the square logo mark.

The mark is the measurement itself: a 1550 nm beam passing through a
polarisation ellipse and landing on a 10 x 10 electrode grid.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle, Rectangle
from matplotlib.collections import LineCollection

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import apply_style

OUT = Path(__file__).resolve().parents[1] / "assets" / "logo"
OUT.mkdir(parents=True, exist_ok=True)

INK_D = "#05070d"
PANEL_D = "#0d1220"
BEAM = "#ff4d5a"
BEAM_SOFT = "#ff8a94"
CYAN = "#22d3ee"
VIOLET = "#a78bfa"
PAPER = "#f4f6fb"
MUTED_D = "#8a94a8"


def _ellipse(ax, cx, cy, a, b, angle_deg, color, lw=3.0, alpha=1.0, zorder=5):
    t = np.linspace(0, 2 * np.pi, 400)
    x, y = a * np.cos(t), b * np.sin(t)
    th = np.deg2rad(angle_deg)
    xr = cx + x * np.cos(th) - y * np.sin(th)
    yr = cy + x * np.sin(th) + y * np.cos(th)
    ax.plot(xr, yr, color=color, lw=lw, alpha=alpha, zorder=zorder,
            solid_capstyle="round")


def _glow_line(ax, x, y0, y1, color, base_lw=3.0, zorder=3):
    for lw, al in [(base_lw * 5.0, 0.06), (base_lw * 3.0, 0.10),
                   (base_lw * 1.8, 0.20), (base_lw, 1.0)]:
        ax.plot([x, x], [y0, y1], color=color, lw=lw, alpha=al, zorder=zorder,
                solid_capstyle="round")


def draw_mark(ax, cx=0.0, cy=0.0, s=1.0, dark=True):
    """The square mark: beam -> ellipse -> chip grid."""
    beam = BEAM
    grid_c = CYAN if dark else "#0e7490"
    ell_c = VIOLET if dark else "#7c3aed"

    # beam through the whole mark
    _glow_line(ax, cx, cy + 1.02 * s, cy - 1.02 * s, beam, base_lw=3.4 * s)

    # the polarisation ellipse, centred
    _ellipse(ax, cx, cy + 0.30 * s, 0.52 * s, 0.24 * s, -22, ell_c, lw=3.6 * s)
    _ellipse(ax, cx, cy + 0.30 * s, 0.52 * s, 0.24 * s, -22, ell_c, lw=11 * s, alpha=0.10)

    # the chip: a 4 x 4 hint of the 10 x 10 pad array
    n, pitch, pad = 5, 0.255 * s, 0.092 * s
    x0 = cx - pitch * (n - 1) / 2
    y0 = cy - 0.44 * s - pitch * (n - 1) / 2 + 0.12 * s
    for i in range(n):
        for j in range(n):
            lit = (i, j) == (2, 2)
            ax.add_patch(Rectangle((x0 + j * pitch - pad / 2,
                                    y0 + i * pitch - pad / 2), pad, pad,
                                   facecolor=beam if lit else grid_c,
                                   edgecolor="none",
                                   alpha=1.0 if lit else 0.45, zorder=6))
    # the measured pixel glows
    ax.add_patch(Circle((x0 + 2 * pitch, y0 + 2 * pitch), 0.17 * s,
                        facecolor=beam, alpha=0.18, edgecolor="none", zorder=5))


def banner():
    apply_style()
    fig = plt.figure(figsize=(16, 4.4), dpi=120)
    fig.patch.set_facecolor(INK_D)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 16); ax.set_ylim(0, 4.4)
    ax.axis("off")
    ax.set_facecolor(INK_D)

    # soft vignette / gradient
    grad = np.linspace(0, 1, 512).reshape(1, -1)
    ax.imshow(grad, extent=[0, 16, 0, 4.4], aspect="auto", cmap="Blues_r",
              alpha=0.10, zorder=0)

    # faint Poincare-sphere wireframe on the right
    t = np.linspace(0, 2 * np.pi, 300)
    scx, scy, R = 13.3, 2.2, 1.55
    for lat in np.deg2rad([-55, -28, 0, 28, 55]):
        ax.plot(scx + R * np.cos(lat) * np.cos(t), scy + R * np.sin(lat) + 0 * t,
                color=CYAN, lw=0.7, alpha=0.16, zorder=1)
    for lat in np.deg2rad([-55, -28, 28, 55]):
        r = R * np.cos(lat)
        ax.plot(scx + r * np.cos(t), scy + R * np.sin(lat) + r * 0.34 * np.sin(t),
                color=CYAN, lw=0.7, alpha=0.20, zorder=1)
    for lon in np.linspace(0, np.pi, 7)[:-1]:
        ax.plot(scx + R * np.cos(lon) * np.cos(t), scy + R * np.sin(t),
                color=CYAN, lw=0.7, alpha=0.16, zorder=1)
    ax.plot(scx + R * np.cos(t), scy + R * 0.34 * np.sin(t), color=CYAN,
            lw=1.1, alpha=0.38, zorder=1)
    # a state and its modulation arc
    arc = np.linspace(-0.55, 0.55, 60)
    ax.plot(scx + R * np.cos(arc + 0.5), scy + R * 0.34 * np.sin(arc + 0.5) + 0.05,
            color=BEAM, lw=3.0, alpha=0.9, zorder=2)

    draw_mark(ax, cx=1.9, cy=2.2, s=1.42)

    ax.text(3.45, 2.78, "PockelsMap", fontsize=46, color=PAPER,
            fontweight="bold", va="center", ha="left", family="DejaVu Sans")
    ax.text(3.55, 1.82,
            "Automated Sénarmont polarimetry for electro-optic and\n"
            "ferroelectric mapping of BaTiO$_3$ thin films at 1550 nm",
            fontsize=14.5, color="#aeb8cc", va="center", ha="left", linespacing=1.55)
    ax.text(3.58, 0.72,
            "100 pixels   ·   9 incident polarisations   ·   ±40 V hysteresis"
            "   ·   microradian resolution",
            fontsize=11.5, color=CYAN, va="center", ha="left", alpha=0.85)

    fig.savefig(OUT / "banner.png", facecolor=INK_D, bbox_inches=None, pad_inches=0)
    plt.close(fig)


def mark(dark=True, name="logo.png", size=512):
    fig = plt.figure(figsize=(size / 120, size / 120), dpi=120)
    bg = INK_D if dark else "#ffffff"
    fig.patch.set_facecolor(bg)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-1.25, 1.25); ax.set_ylim(-1.25, 1.25)
    ax.set_aspect("equal"); ax.axis("off"); ax.set_facecolor(bg)
    ax.add_patch(FancyBboxPatch((-1.18, -1.18), 2.36, 2.36,
                                boxstyle="round,pad=0,rounding_size=0.34",
                                facecolor=PANEL_D if dark else "#f4f6fb",
                                edgecolor="#1b2740" if dark else "#dfe4ee",
                                lw=2.0, zorder=0))
    draw_mark(ax, 0.0, 0.17, s=0.95, dark=dark)
    fig.savefig(OUT / name, facecolor=bg, pad_inches=0)
    plt.close(fig)


def main():
    apply_style()
    banner()
    mark(dark=True, name="logo.png")
    mark(dark=False, name="logo-light.png")
    for f in sorted(OUT.glob("*.png")):
        print(f"  wrote assets/logo/{f.name}  ({f.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
