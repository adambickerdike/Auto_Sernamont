"""Figures 10-12: the chip grid, the switching-matrix circuit, and the time budget."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrowPatch, Circle

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import (apply_style, INK, MUTED, GRID, PANEL, LASER, BLUE, VIOLET,
                       GREEN, AMBER, CYAN, PINK, SLATE, title)

OUT = Path(__file__).resolve().parents[1] / "assets" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pockels"))
from arduino_switch_matrix import PIXEL_TO_ELECTRICAL_SWITCH  # noqa: E402

DEFAULT_PIXELS = set()
for chunk in "1-6,11-16,21-26,31-37,41-48,51-100".split(","):
    if "-" in chunk:
        a, b = chunk.split("-")
        DEFAULT_PIXELS.update(range(int(a), int(b) + 1))
    else:
        DEFAULT_PIXELS.add(int(chunk))


# ------------------------------------------------- figure 10: pixel grid ----
def figure_pixel_grid():
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.2, 5.9))

    # ---- left: the internal numbering the software uses ----
    for pix in range(1, 101):
        idx = pix - 1
        row, col = idx // 10, idx % 10          # 0-based, as stored in the CSVs
        x, y = col, 9 - row
        selected = pix in DEFAULT_PIXELS
        ax.add_patch(Rectangle((x - 0.46, y - 0.46), 0.92, 0.92,
                               facecolor="#dbeafe" if selected else "#f1f3f6",
                               edgecolor="#c3cad6", lw=0.8))
        ax.text(x, y + 0.08, f"{pix}", ha="center", va="center", fontsize=8.2,
                color=INK if selected else MUTED, fontweight="bold")
        ax.text(x, y - 0.26, f"{row},{col}", ha="center", va="center",
                fontsize=6.2, color=MUTED, family="monospace")
    ax.set_xlim(-0.75, 9.75); ax.set_ylim(-0.75, 9.75)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.annotate("", xy=(9.6, 9.75), xytext=(-0.55, 9.75),
                arrowprops=dict(arrowstyle="-|>", color=SLATE, lw=1.2))
    ax.text(4.5, 10.05, "col 0 \u2192 9   (stage $x$)", ha="center",
            fontsize=8.6, color=SLATE)
    ax.annotate("", xy=(-0.75, -0.55), xytext=(-0.75, 9.6),
                arrowprops=dict(arrowstyle="-|>", color=SLATE, lw=1.2))
    ax.text(-1.05, 4.5, "row 0 \u2192 9   (stage $y$)", va="center", rotation=90,
            ha="center", fontsize=8.6, color=SLATE)
    title(ax, "Internal numbering",
          "pixel = row$\\times$10 + col + 1, both 0-based.  "
          "Shaded = the 83-pixel default set.")

    # ---- right: the display frame, and the electrode-switch map ----
    order = np.zeros((10, 10))
    for pix, sw in PIXEL_TO_ELECTRICAL_SWITCH.items():
        idx = pix - 1
        row, col = idx // 10, idx % 10
        disp_col = 10 - col          # what the GUI draws, 1..10
        order[row, disp_col - 1] = sw
    im = ax2.imshow(order, cmap="viridis", vmin=1, vmax=100)
    for row in range(10):
        for dcol in range(10):
            pix = row * 10 + (10 - (dcol + 1)) + 1
            sw = int(order[row, dcol])
            ax2.text(dcol, row - 0.14, f"{pix}", ha="center", va="center",
                     fontsize=7.6, color="white", fontweight="bold")
            ax2.text(dcol, row + 0.24, f"E{sw}", ha="center", va="center",
                     fontsize=6.0, color="#e5e7ebcc", family="monospace")
    ax2.set_xticks(range(10), [str(i + 1) for i in range(10)])
    ax2.set_yticks(range(10), [str(i + 1) for i in range(10)])
    ax2.set_xlabel("display column  (1 = right-most stage column)")
    ax2.set_ylabel("display row")
    ax2.grid(False)
    cb = fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.03)
    cb.set_label("electrical switch channel", fontsize=9)
    cb.outline.set_visible(False)
    title(ax2, "Display frame and electrode routing",
          "the GUI draws display_col = 10 \u2212 col so the map matches the camera view;\n"
          "E$n$ is the switch-matrix channel that energises that pixel")
    fig.tight_layout(pad=1.8)
    fig.savefig(OUT / "pixel_grid.png")
    plt.close(fig)


# --------------------------------------- figure 11: switch-matrix circuit ---
def _box(ax, x, y, w, h, label, sub=None, fc=PANEL, ec="#c3cad6", fs=9.2,
         lc=INK, radius=0.06):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                                boxstyle=f"round,pad=0.0,rounding_size={radius}",
                                facecolor=fc, edgecolor=ec, lw=1.2, zorder=3))
    ax.text(x + w / 2, y + h / 2 + (0.055 * h if sub else 0), label,
            ha="center", va="center", fontsize=fs, color=lc,
            fontweight="bold", zorder=4)
    if sub:
        ax.text(x + w / 2, y + h / 2 - 0.20 * h, sub, ha="center", va="center",
                fontsize=7.4, color=MUTED, zorder=4, linespacing=1.35)


def _wire(ax, pts, color=SLATE, lw=1.4, arrow=True, ls="-", zorder=2):
    pts = np.asarray(pts, dtype=float)
    ax.plot(pts[:, 0], pts[:, 1], color=color, lw=lw, ls=ls, zorder=zorder,
            solid_capstyle="round")
    if arrow:
        ax.annotate("", xy=pts[-1], xytext=pts[-2],
                    arrowprops=dict(arrowstyle="-|>", color=color, lw=lw), zorder=zorder)


def figure_switch_matrix_circuit():
    fig, ax = plt.subplots(figsize=(13.0, 7.4))
    ax.set_xlim(0, 100); ax.set_ylim(0, 60)
    ax.set_aspect("equal"); ax.axis("off")

    # ---------------- control plane ----------------
    _box(ax, 2, 40, 17, 11, "Arduino Nano", "USB serial, 9600 baud\nCH340 bridge",
         fc="#e8f0fe", ec="#9fc0f5")
    ax.text(10.5, 51.6, "CONTROL PLANE", ha="center", fontsize=8.4,
            color=BLUE, fontweight="bold")

    pins = [("D11  DATA", 49.0, BLUE), ("D13  CLK", 46.6, BLUE),
            ("D10  LAT", 44.2, VIOLET), ("D9  BLANK", 41.8, LASER)]
    for lab, y, col in pins:
        _wire(ax, [(19, y), (26, y)], color=col, lw=1.5)
        ax.text(22.5, y + 0.85, lab, ha="center", fontsize=7.0, color=col,
                family="monospace")

    # the shift-register chain
    for k in range(7):
        x = 26 + k * 9.6
        _box(ax, x, 40.5, 8.4, 10, f"TLC59282", f"#{k+1}", fc="#f3f0ff",
             ec="#c4b5fd", fs=7.6)
        if k < 6:
            _wire(ax, [(x + 8.4, 45.5), (x + 9.6, 45.5)], color=VIOLET, lw=1.4)
            ax.text(x + 9.0, 46.3, "SOUT", ha="center", fontsize=5.6,
                    color=VIOLET, family="monospace")
    ax.text(60, 51.9, "7 \u00d7 TLC59282 cascaded  \u2192  112 shift-register bits",
            ha="center", fontsize=8.6, color=VIOLET, fontweight="bold")
    ax.text(60, 50.0, "bits 0\u201399: one per channel     bits 100\u2013109: one per group "
                      "of ten     bits 110\u2013111: spare",
            ha="center", fontsize=7.4, color=MUTED)

    # outputs down to the relays
    for k in range(7):
        x = 26 + k * 9.6 + 4.2
        _wire(ax, [(x, 40.5), (x, 36.5)], color=VIOLET, lw=1.0, arrow=False)
    _wire(ax, [(26, 36.5), (93.4, 36.5)], color=VIOLET, lw=1.0, arrow=False)
    _wire(ax, [(46.5, 36.5), (46.5, 34.0)], color=VIOLET, lw=1.0, arrow=False)

    # ---------------- relay plane ----------------
    ax.text(10.5, 33.0, "SWITCH PLANE", ha="center", fontsize=8.4,
            color=GREEN, fontweight="bold")
    _box(ax, 26, 23.5, 41, 10.5, "", fc="#f0fdf4", ec="#bbf7d0")
    ax.text(46.5, 31.9, "100 \u00d7 AQV258AX PhotoMOS relay",
            ha="center", fontsize=9.2, color=GREEN, fontweight="bold")
    ax.text(46.5, 30.0, "optically isolated: no contact bounce, galvanic isolation,\n"
                        "very low off-state leakage",
            ha="center", fontsize=7.3, color=MUTED, linespacing=1.4)
    for k in range(8):
        x = 29.5 + k * 5.4
        ax.add_patch(Circle((x, 26.6), 0.95, facecolor="white",
                            edgecolor=GREEN if k == 4 else "#bbf7d0", lw=1.4, zorder=5))
        if k == 4:
            ax.add_patch(Circle((x, 26.6), 0.42, facecolor=GREEN, edgecolor="none", zorder=6))
    ax.text(46.5, 24.4, "exclusive: exactly one relay closed at a time; "
                        "\u201c0\u201d opens them all", ha="center", fontsize=7.2,
            color=MUTED, style="italic")
    _wire(ax, [(51.1, 25.6), (51.1, 19.5)], color=GREEN, lw=1.6, arrow=False)
    ax.text(52.4, 21.8, "only the selected\nchannel is connected", fontsize=7.3,
            color=GREEN, linespacing=1.4)

    # ---------------- signal plane ----------------
    ax.text(10.5, 18.4, "SIGNAL PLANE", ha="center", fontsize=8.4,
            color=LASER, fontweight="bold")
    _box(ax, 2, 10.5, 17, 6.4, "SMU4201", "DC  0 \u2026 \u00b140 V\n1 mA compliance",
         fc="#fef2f2", ec="#fecaca", fs=8.6)
    _box(ax, 2, 2.0, 17, 6.4, "TGF3162 CH1", "AC  1\u20139 Vpp @ 30 kHz",
         fc="#fef2f2", ec="#fecaca", fs=8.6)
    _box(ax, 25, 5.6, 13, 7.4, "bias tee", "sums DC + AC onto\none conductor",
         fc="#fff7ed", ec="#fed7aa", fs=8.6)
    _wire(ax, [(19, 13.7), (22, 13.7), (22, 10.6), (25, 10.6)], color=LASER, lw=1.5)
    _wire(ax, [(19, 5.2), (22, 5.2), (22, 8.0), (25, 8.0)], color=LASER, lw=1.5)
    _wire(ax, [(38, 9.3), (46, 9.3)], color=LASER, lw=1.8)
    ax.text(42, 10.2, "common drive bus", ha="center", fontsize=7.4, color=LASER)

    _box(ax, 46, 4.2, 20, 11.0, "selected pixel", "one electrode pair\n"
         "\u22487 \u00b5m in-plane gap", fc="#fff1f2", ec="#fbcfe8", fs=9.0)
    _wire(ax, [(51.1, 19.5), (56.0, 19.5), (56.0, 15.2)], color=GREEN, lw=1.6, arrow=True)
    _wire(ax, [(56, 4.2), (56, 1.5), (30, 1.5)], color=SLATE, lw=1.4, arrow=False)
    ax.text(43, 0.6, "ground return", ha="center", fontsize=7.4, color=SLATE)

    _box(ax, 72, 4.2, 22, 11.0, "the other 99 pairs", "open-circuit:\nno stray path is energised",
         fc="#f8fafc", ec="#e2e8f0", fs=9.0, lc=MUTED)

    # ---------------- protocol box ----------------
    ax.add_patch(FancyBboxPatch((70.5, 18.0), 23.5, 15.5,
                                boxstyle="round,pad=0.0,rounding_size=0.5",
                                facecolor="white", edgecolor="#d7dbe2", lw=1.1, zorder=3))
    ax.text(72.0, 32.1, "Serial protocol", fontsize=8.8, color=INK,
            fontweight="bold", zorder=4)
    ax.text(72.0, 30.2,
            "1 \u2026 100    logical pixel\n"
            "E1 \u2026 E100  raw switch channel\n"
            "0          all channels OFF\n\n"
            "Automation sends the raw form:\n"
            "Python already holds the map.\n"
            "Firmware replies with one status\n"
            "line, verified before continuing.",
            fontsize=6.9, color=MUTED, va="top", family="monospace",
            linespacing=1.5, zorder=4)

    ax.text(2, 56.6, "The 100-channel switching matrix", fontsize=14,
            fontweight="bold", color=INK)
    ax.text(2, 54.2,
            "One measurement chain, one hundred electrode pairs.  Four Arduino pins shift 112 bits "
            "into a chain of constant-current drivers,\nwhich energise exactly one optically isolated "
            "relay \u2014 and the SMU and function generator reach that pixel and no other.",
            fontsize=8.6, color=MUTED, linespacing=1.5)
    fig.tight_layout(pad=0.6)
    fig.savefig(OUT / "switch_matrix_circuit.png")
    plt.close(fig)


# ------------------------------------------- figure 12: timing breakdown ----
def figure_timing():
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.6),
                                  gridspec_kw={"width_ratios": [1.0, 1.0]})

    # one production pixel
    steps = [("stage move + hill-climb alignment", 30, BLUE),
             ("Arduino routing + SMU ramp", 5, AMBER),
             ("adaptive poling", 75, LASER),
             ("9 HWP moves + null checks", 60, VIOLET),
             ("27 lock-in acquisitions", 200, GREEN),
             ("teardown", 5, SLATE)]
    left = 0.0
    for lab, dur, col in steps:
        ax.barh(0, dur, left=left, height=0.52, color=col, edgecolor="white", lw=1.2)
        if dur > 20:
            ax.text(left + dur / 2, 0, f"{dur} s", ha="center", va="center",
                    fontsize=8.4, color="white", fontweight="bold")
        left += dur
    ax.set_xlim(0, left * 1.02); ax.set_ylim(-1.5, 0.9)
    ax.set_yticks([])
    ax.set_xlabel("seconds")
    ax.grid(axis="y", visible=False)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in steps]
    ax.legend(handles, [s[0] for s in steps], loc="lower center",
              bbox_to_anchor=(0.5, -0.02), ncols=2, fontsize=8.2)
    title(ax, f"One production pixel  \u2248 {left/60:.1f} min",
          "the calibration pixel adds the forced 2-D null at every HWP: \u2248 8 min")

    # the campaign
    tasks = ["one lock-in point", "one production pixel", "calibration pixel",
             "one 45-point loop", "83-pixel chip map", "100 hysteresis loops"]
    hours = [12 / 3600, 4.75 / 60, 8 / 60, 29 / 60, 6.5, 49.0]
    cols = [GREEN, BLUE, CYAN, VIOLET, AMBER, LASER]
    y = np.arange(len(tasks))
    ax2.barh(y, hours, color=cols, height=0.6, edgecolor="white", lw=1.0)
    for yi, h in zip(y, hours):
        lab = (f"{h*3600:.0f} s" if h < 1 / 60 else
               f"{h*60:.0f} min" if h < 1 else f"{h:.1f} h")
        ax2.text(h * 1.12, yi, lab, va="center", fontsize=8.6, color=INK)
    ax2.set_yticks(y, tasks, fontsize=9)
    ax2.set_xscale("log")
    ax2.set_xlim(2e-3, 200)
    ax2.set_xlabel("hours  (log scale)")
    ax2.grid(axis="y", visible=False)
    ax2.invert_yaxis()
    title(ax2, "Why the fast map exists",
          "the naive plan \u2014 100 px $\\times$ 7 HWP $\\times$ 5 V $\\times$ 18 analyser angles "
          "\u2014 is\n63 000 lock-in points, hundreds of hours")
    fig.tight_layout(pad=1.8)
    fig.savefig(OUT / "timing_breakdown.png")
    plt.close(fig)


def main():
    apply_style()
    figure_pixel_grid()
    figure_switch_matrix_circuit()
    figure_timing()
    for name in ("pixel_grid", "switch_matrix_circuit", "timing_breakdown"):
        p = OUT / f"{name}.png"
        print(f"  wrote {p.name}  ({p.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
