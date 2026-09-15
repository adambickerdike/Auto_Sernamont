"""The repository hero graphic: the optical column, annotated with real physics.

A vertical beamline drawn in the project's dark brand style, with the computed
polarisation state beside each station and the Poincare trajectory alongside.
Deliberately an information graphic rather than a photoreal render: it teaches
the instrument at a glance.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Circle, Ellipse, Rectangle

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import apply_style
from _polarisation import (half_wave_plate, quarter_wave_plate, retarder,
                           linear_state, normalised_stokes, ellipse_parameters,
                           ellipse_trace)

OUT = Path(__file__).resolve().parents[1] / "assets"
OUT.mkdir(parents=True, exist_ok=True)

INK_D, PANEL_D = "#05070d", "#101728"
BEAM, BEAM2 = "#ff4d5a", "#ff8a94"
CYAN, VIOLET, GREEN, AMBER = "#22d3ee", "#a78bfa", "#34d399", "#fbbf24"
PAPER, MUTED_D = "#eef2fa", "#8a94a8"
FAINT = "#2a3550"

THETA_HWP, SAMPLE_RET, SAMPLE_AXIS = 15.0, 70.0, 0.0


def _states():
    j0 = linear_state(0.0)
    j1 = half_wave_plate(THETA_HWP) @ j0
    j2 = retarder(SAMPLE_RET, SAMPLE_AXIS) @ j1
    best, best_s3 = 0.0, 9e9
    for a in np.linspace(-90, 90, 36001):
        s3 = abs(normalised_stokes(quarter_wave_plate(a) @ j2)[2])
        if s3 < best_s3:
            best_s3, best = s3, a
    j3 = quarter_wave_plate(best) @ j2
    return j0, j1, j2, j3, best


def _mini_ellipse(ax, cx, cy, r, jones, color):
    ex, ey = ellipse_trace(jones, 240)
    ax.plot(cx + r * ex, cy + r * ey, color=color, lw=2.4, zorder=8,
            solid_capstyle="round")
    ax.add_patch(Circle((cx, cy), r * 1.5, facecolor=PANEL_D, edgecolor=FAINT,
                        lw=1.0, zorder=6))
    ax.plot(cx + r * ex, cy + r * ey, color=color, lw=2.4, zorder=8)


def hero():
    apply_style()
    j0, j1, j2, j3, qwp_angle = _states()

    fig = plt.figure(figsize=(16, 9), dpi=120)
    fig.patch.set_facecolor(INK_D)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 16); ax.set_ylim(0, 9)
    ax.axis("off"); ax.set_facecolor(INK_D)
    grad = np.linspace(0, 1, 512).reshape(1, -1)
    ax.imshow(grad, extent=[0, 16, 0, 9], aspect="auto", cmap="Blues_r",
              alpha=0.08, zorder=0)

    # ---------------- the optical column ----------------
    col_x = 5.35
    top, bot = 8.15, 0.75
    for lw, al in [(16, 0.05), (9, 0.08), (4.5, 0.16), (2.0, 1.0)]:
        ax.plot([col_x, col_x], [bot, top], color=BEAM, lw=lw, alpha=al, zorder=2)

    stations = [
        (7.80, "camera",        "CMOS alignment camera",      CYAN,   None),
        (7.15, "beamsplitter",  "non-polarising cube",        CYAN,   None),
        (6.45, "polariser 1",   "defines the polarisation frame", GREEN, j0),
        (5.72, "HWP  λ/2", f"motorised — sets $\\theta_i$ = {2*THETA_HWP:.0f}°", VIOLET, j1),
        (5.05, "lens",          "focus into the electrode gap", CYAN,  None),
        (4.18, "BTO chip",      "10 × 10 pairs, ≈7 µm gap", BEAM, j2),
        (3.30, "lens",          "collection",                  CYAN,   None),
        (2.62, "QWP  λ/4",  f"motorised — compensates at {qwp_angle:.1f}°", VIOLET, j3),
        (1.92, "polariser 2",   "analyser — null and ±45°", GREEN, None),
        (1.25, "lens",          "onto the detector",           CYAN,   None),
        (0.60, "detector",      "PDA30B2 → scope + lock-in", AMBER, None),
    ]

    for y, name, note, col, jones in stations:
        # the optic itself
        if "lens" in name:
            ax.add_patch(Ellipse((col_x, y), 1.00, 0.20, facecolor="#9fd8ff",
                                 alpha=0.30, edgecolor=CYAN, lw=1.2, zorder=7))
        elif "chip" in name:
            ax.add_patch(Rectangle((col_x - 0.80, y - 0.11), 1.60, 0.22,
                                   facecolor="#0e3a3a", edgecolor=BEAM, lw=1.4, zorder=7))
            for dx in (-0.22, 0.22):
                ax.add_patch(Rectangle((col_x + dx - 0.16, y + 0.06), 0.32, 0.10,
                                       facecolor=AMBER, edgecolor="none", zorder=8))
        elif name in ("camera", "detector"):
            ax.add_patch(FancyBboxPatch((col_x - 0.42, y - 0.20), 0.84, 0.40,
                                        boxstyle="round,pad=0,rounding_size=0.08",
                                        facecolor=PANEL_D, edgecolor=col, lw=1.4, zorder=7))
        elif "beamsplitter" in name:
            ax.add_patch(Rectangle((col_x - 0.36, y - 0.36), 0.72, 0.72,
                                   facecolor="#13324a", alpha=0.75, edgecolor=col,
                                   lw=1.3, zorder=7))
            ax.plot([col_x - 0.36, col_x + 0.36], [y - 0.36, y + 0.36],
                    color=col, lw=1.1, zorder=8)
        else:
            ax.add_patch(Rectangle((col_x - 0.70, y - 0.09), 1.40, 0.18,
                                   facecolor=col, alpha=0.30, edgecolor=col,
                                   lw=1.3, zorder=7))
        dy = -0.30 if "chip" in name else 0.0
        ax.text(col_x + 0.95, y + 0.07 + dy, name, fontsize=11, color=PAPER,
                va="center", fontweight="bold", zorder=9)
        ax.text(col_x + 0.95, y - 0.16 + dy, note, fontsize=8.4, color=MUTED_D,
                va="center", zorder=9)
        if jones is not None:
            _mini_ellipse(ax, col_x - 1.35, y, 0.30, jones, BEAM2)
            psi, chi, ratio = ellipse_parameters(jones)
            shape = "linear" if abs(chi) < 0.5 else "elliptical"
            ax.text(col_x - 1.35, y - 0.60, shape, fontsize=7.4, color=MUTED_D,
                    ha="center", zorder=9)

    ax.text(col_x - 1.35, 8.62, "polarisation\nstate", fontsize=8.6, color=CYAN,
            ha="center", va="center", linespacing=1.4, zorder=9)

    # laser arm entering the beamsplitter from the upper right
    lx = 8.55
    ax.plot([col_x + 0.36, lx], [7.15, 7.15], color=BEAM, lw=2.0, zorder=3)
    ax.plot([lx, lx], [7.15, 8.05], color=BEAM, lw=2.0, zorder=3)
    for mx, my, sgn in [(lx, 7.15, 1), (lx, 8.05, -1)]:
        ax.plot([mx - 0.15, mx + 0.15], [my - 0.15 * sgn, my + 0.15 * sgn],
                color="#cbd5e1", lw=2.8, zorder=8)
    ax.add_patch(FancyBboxPatch((lx + 0.28, 7.83), 1.85, 0.46,
                                boxstyle="round,pad=0,rounding_size=0.09",
                                facecolor=PANEL_D, edgecolor=BEAM, lw=1.4, zorder=7))
    ax.text(lx + 1.20, 8.06, "1550 nm laser", fontsize=9.2, color=PAPER,
            ha="center", va="center", fontweight="bold", zorder=8)
    ax.plot([lx + 0.28, lx + 0.05], [8.06, 8.06], color=BEAM, lw=2.0, zorder=3)

    # ---------------- electrical stack ----------------
    ax.text(9.95, 8.62, "electrical drive", fontsize=8.6, color=AMBER,
            ha="left", va="center", zorder=9)
    boxes = [("SMU4201", "DC poling and ±40 V hysteresis, 1 mA limit", 7.62),
             ("TGF3162", "30 kHz drive (CH1) + lock-in reference (CH2)", 6.86),
             ("bias tee", "sums DC and AC onto one conductor", 6.10),
             ("switch matrix", "100 channels, exclusive routing", 5.34)]
    for name, note, y in boxes:
        ax.add_patch(FancyBboxPatch((9.95, y - 0.30), 5.55, 0.62,
                                    boxstyle="round,pad=0,rounding_size=0.10",
                                    facecolor=PANEL_D, edgecolor=FAINT, lw=1.1, zorder=6))
        ax.text(10.20, y + 0.08, name, fontsize=10, color=PAPER, va="center",
                fontweight="bold", zorder=7)
        ax.text(10.20, y - 0.15, note, fontsize=8.2, color=MUTED_D, va="center", zorder=7)
        ax.plot([9.95, 9.72], [y, y], color=FAINT, lw=1.0, zorder=5)
    ax.plot([9.72, 9.72], [5.34, 7.62], color=FAINT, lw=1.0, zorder=5)
    ax.annotate("", xy=(6.35, 4.30), xytext=(9.60, 4.30),
                arrowprops=dict(arrowstyle="-|>", color=AMBER, lw=1.5, alpha=0.85),
                zorder=4)
    ax.plot([9.60, 9.60], [4.30, 5.34], color=AMBER, lw=1.5, alpha=0.85, zorder=4)
    ax.text(9.50, 4.52, "one selected electrode pair", fontsize=8.2, color=AMBER,
            ha="right", va="bottom", zorder=9)

    # ---------------- detection stack ----------------
    for name, note, y in [("Tektronix TBS", "DC transmission — nulls and the Malus slope", 1.95),
                          ("DSP7230 lock-in", "the AC response at 30 kHz, 200 µV full scale", 1.19)]:
        ax.add_patch(FancyBboxPatch((9.95, y - 0.30), 5.55, 0.62,
                                    boxstyle="round,pad=0,rounding_size=0.10",
                                    facecolor=PANEL_D, edgecolor=FAINT, lw=1.1, zorder=6))
        ax.text(10.20, y + 0.08, name, fontsize=10, color=PAPER, va="center",
                fontweight="bold", zorder=7)
        ax.text(10.20, y - 0.15, note, fontsize=8.2, color=MUTED_D, va="center", zorder=7)
    ax.plot([col_x + 0.45, 9.60], [0.60, 0.60], color=AMBER, lw=1.3, zorder=4, alpha=0.85)
    ax.plot([9.60, 9.60], [0.60, 1.95], color=AMBER, lw=1.3, zorder=4, alpha=0.85)
    for yy in (1.19, 1.95):
        ax.annotate("", xy=(9.95, yy), xytext=(9.60, yy),
                    arrowprops=dict(arrowstyle="-|>", color=AMBER, lw=1.3, alpha=0.85),
                    zorder=4)

    # ---------------- headline ----------------
    ax.text(0.55, 8.40, "PockelsMap", fontsize=34, color=PAPER, fontweight="bold",
            va="center", zorder=9)
    ax.text(0.55, 7.72,
            "Automated Sénarmont polarimetry\nfor BaTiO$_3$ thin films at 1550 nm",
            fontsize=11.5, color="#aeb8cc", va="center", linespacing=1.55, zorder=9)
    ax.text(0.55, 6.60,
            "A 1550 nm beam is polarised, its\ndirection set by a motorised half-wave\n"
            "plate, and focused into one electrode\ngap of a 10 × 10 array.  A voltage\n"
            "changes the film's refractive indices —\nthe Pockels effect — and rotates the\n"
            "polarisation by microradians.",
            fontsize=8.8, color=MUTED_D, va="top", linespacing=1.7, zorder=9)
    ax.text(0.55, 3.55,
            "A quarter-wave plate cancels the film's\nstatic birefringence so the analyser can\n"
            "extinguish the beam; sitting ±45° from\nthat null puts the measurement on the\n"
            "steepest part of the transmission curve,\nwhere a lock-in amplifier can pull the\n"
            "signal out of the noise.",
            fontsize=8.8, color=MUTED_D, va="top", linespacing=1.7, zorder=9)
    ax.plot([0.55, 3.95], [7.12, 7.12], color=FAINT, lw=1.2, zorder=9)

    fig.savefig(OUT / "hero.png", facecolor=INK_D, pad_inches=0)
    plt.close(fig)
    print(f"  wrote assets/hero.png  ({(OUT/'hero.png').stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    hero()
