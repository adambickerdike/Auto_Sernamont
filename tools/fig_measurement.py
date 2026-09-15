"""Idealised figures for the measurement model.

The Malus slope, the complex analyser response, the angular harmonics, the
reference shapes of the loop classifier, and the poling kinetics. These are
computed from the same models the analysis code uses. Figures built from real
acquired data live in fig_measured_hysteresis.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import (apply_style, INK, MUTED, GRID, LASER, BLUE, VIOLET,
                       GREEN, AMBER, CYAN, PINK, SLATE, title)

OUT = Path(__file__).resolve().parents[1] / "assets" / "figures"
OUT.mkdir(parents=True, exist_ok=True)
RNG = np.random.default_rng(20260915)


# ----------------------------------------------- figure 4: the Malus slope --
def figure_malus_slope():
    psi = np.linspace(-95, 95, 1001)
    i_floor, i0 = 0.012, 1.0
    inten = i_floor + i0 * np.sin(np.deg2rad(psi)) ** 2
    slope = i0 * np.sin(np.deg2rad(2 * psi))

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.2, 4.5))

    ax.plot(psi, inten, color=BLUE, lw=2.4)
    ax.fill_between(psi, 0, inten, color=BLUE, alpha=0.07)
    ax.axhline(i_floor, color=MUTED, lw=0.9, ls=(0, (3, 3)))
    ax.text(-93, i_floor + 0.03, "leakage floor  $I_\\mathrm{floor}$",
            fontsize=8.4, color=MUTED)
    for off, col, lab in [(0, GREEN, "null"), (45, BLUE, "$+45\\degree$"),
                          (-45, AMBER, "$-45\\degree$")]:
        y = i_floor + i0 * np.sin(np.deg2rad(off)) ** 2
        ax.scatter([off], [y], color=col, s=52, zorder=6, edgecolors="white",
                   linewidths=1.1, marker="X" if off == 0 else "o")
        ax.annotate(lab, xy=(off, y), xytext=(off, y + 0.12), ha="center",
                    fontsize=9, color=col, fontweight="bold")
    # the three readings the triplet takes
    ax.annotate("", xy=(-45, 0.5), xytext=(45, 0.5),
                arrowprops=dict(arrowstyle="<|-|>", color=SLATE, lw=1.0))
    ax.text(0, 0.545, "the triplet: null, $+45\\degree$, $-45\\degree$",
            ha="center", fontsize=8.6, color=SLATE)
    ax.set_xlabel("analyser offset from the null,  $\\psi$  (degrees)")
    ax.set_ylabel("transmitted intensity  (normalised)")
    ax.set_xlim(-95, 95); ax.set_ylim(-0.02, 1.16)
    ax.set_xticks([-90, -45, 0, 45, 90])
    title(ax, "Malus transmission about the null",
          "$I(\\psi) = I_\\mathrm{floor} + I_0\\,\\sin^2\\psi$")

    ax2.axhline(0, color=GRID, lw=1.0)
    ax2.plot(psi, slope, color=LASER, lw=2.4)
    ax2.fill_between(psi, 0, slope, where=(np.abs(psi) <= 45), color=LASER, alpha=0.08)
    for off, col in [(45, BLUE), (-45, AMBER)]:
        ax2.scatter([off], [i0 * np.sin(np.deg2rad(2 * off))], color=col, s=52,
                    zorder=6, edgecolors="white", linewidths=1.1)
        ax2.axvline(off, color=col, lw=1.0, ls=(0, (2, 3)))
    ax2.scatter([0], [0], color=GREEN, s=52, marker="X", zorder=6,
                edgecolors="white", linewidths=1.1)
    ax2.axvline(0, color=GREEN, lw=1.1, ls=(0, (2, 3)))
    ax2.annotate("maximum sensitivity\nto a polarisation rotation",
                 xy=(45, 1.0), xytext=(8, 0.62), fontsize=8.6, color=BLUE,
                 linespacing=1.5,
                 arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=1.1,
                                 connectionstyle="arc3,rad=0.25"))
    ax2.annotate("equal magnitude,\nopposite sign",
                 xy=(-45, -1.0), xytext=(-88, -0.55), fontsize=8.6, color=AMBER,
                 linespacing=1.5,
                 arrowprops=dict(arrowstyle="-|>", color=AMBER, lw=1.1,
                                 connectionstyle="arc3,rad=0.25"))
    ax2.set_xlabel("analyser offset from the null,  $\\psi$  (degrees)")
    ax2.set_ylabel("$dI/d\\psi$  (normalised)")
    ax2.set_xlim(-95, 95); ax2.set_ylim(-1.35, 1.35)
    ax2.set_xticks([-90, -45, 0, 45, 90])
    title(ax2, "The slope is what the lock-in measures",
          "$dI/d\\psi = I_0\\,\\sin 2\\psi$, maximal at $\\pm45\\degree$ and zero at the null")
    fig.tight_layout(pad=1.6)
    fig.savefig(OUT / "malus_slope.png")
    plt.close(fig)


# --------------------------------------- figure 5: complex analyser response --
def figure_analyser_response():
    """Z(psi) = P + E1 sin(2 psi) + E2 cos(2 psi), fitted from four probes."""
    psi = np.linspace(-95, 275, 1200)
    P = 0.18 * np.exp(1j * np.deg2rad(115.0))      # analyser-independent term
    E1 = 1.00 * np.exp(1j * np.deg2rad(8.0))       # the electro-optic component
    E2 = 0.12 * np.exp(1j * np.deg2rad(8.0))       # small null-placement error
    Z = P + E1 * np.sin(np.deg2rad(2 * psi)) + E2 * np.cos(np.deg2rad(2 * psi))

    probes = np.array([0.0, 45.0, 90.0, -45.0])
    Zp = P + E1 * np.sin(np.deg2rad(2 * probes)) + E2 * np.cos(np.deg2rad(2 * probes))
    Zp = Zp + (RNG.normal(0, 0.012, 4) + 1j * RNG.normal(0, 0.012, 4))

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.4, 4.6))

    ax.axhline(0, color=GRID, lw=1.0)
    ax.plot(psi, Z.real, color=BLUE, lw=2.2, label="in phase,  $X$")
    ax.plot(psi, Z.imag, color=LASER, lw=2.2, ls=(0, (5, 2.5)),
            label="quadrature,  $Y$")
    ax.plot(psi, np.abs(Z), color=SLATE, lw=1.3, alpha=0.75, label="magnitude,  $|Z|$")
    ax.axhline(abs(P), color=VIOLET, lw=1.1, ls=(0, (2, 3)))
    ax.text(250, abs(P) + 0.05, "$|P|$", color=VIOLET, fontsize=9, ha="right")
    for pr, col, lab in zip(probes, [GREEN, BLUE, PINK, AMBER],
                            ["null", "$+45\\degree$", "bright\n$+90\\degree$", "$-45\\degree$"]):
        ax.axvline(pr, color=col, lw=0.9, ls=(0, (2, 3)))
        ax.text(pr, 1.36, lab, ha="center", fontsize=8.2, color=col,
                fontweight="bold", linespacing=1.3)
    ax.scatter(probes, Zp.real, color=BLUE, s=44, zorder=7, edgecolors="white", linewidths=1.0)
    ax.scatter(probes, Zp.imag, color=LASER, s=44, zorder=7, edgecolors="white", linewidths=1.0)
    ax.set_xlabel("analyser offset from the null,  $\\psi$  (degrees)")
    ax.set_ylabel("lock-in response  (arb.)")
    ax.set_xlim(-95, 275); ax.set_ylim(-1.35, 1.65)
    ax.set_xticks([-45, 0, 45, 90, 135, 180, 225])
    ax.legend(loc="lower right", ncols=1)
    title(ax, "The complex analyser response",
          "$Z(\\psi) = P + E_1\\sin 2\\psi + E_2\\cos 2\\psi$, linear in the complex unknowns")

    # phasor view
    ax2.axhline(0, color=GRID, lw=1.0); ax2.axvline(0, color=GRID, lw=1.0)
    ax2.plot(Z.real, Z.imag, color=SLATE, lw=1.2, alpha=0.6)
    ax2.annotate("", xy=(P.real, P.imag), xytext=(0, 0),
                 arrowprops=dict(arrowstyle="-|>", color=VIOLET, lw=2.0))
    ax2.text(P.real * 1.15, P.imag * 1.15, "$P$", color=VIOLET, fontsize=11,
             fontweight="bold")
    ax2.annotate("", xy=(P.real + E1.real, P.imag + E1.imag),
                 xytext=(P.real, P.imag),
                 arrowprops=dict(arrowstyle="-|>", color=LASER, lw=2.0))
    ax2.text(P.real + E1.real * 0.55, P.imag + E1.imag * 0.55 - 0.16, "$E_1$",
             color=LASER, fontsize=11, fontweight="bold")
    for pr, zz, col, lab in zip(probes, Zp, [GREEN, BLUE, PINK, AMBER],
                                ["null", "$+45\\degree$", "$+90\\degree$", "$-45\\degree$"]):
        ax2.scatter([zz.real], [zz.imag], color=col, s=64, zorder=7,
                    edgecolors="white", linewidths=1.1)
        ax2.text(zz.real, zz.imag + 0.10, lab, ha="center", fontsize=8.4,
                 color=col, fontweight="bold")
    ax2.set_xlabel("in phase,  $X$")
    ax2.set_ylabel("quadrature,  $Y$")
    ax2.set_aspect("equal")
    ax2.set_xlim(-1.45, 1.45); ax2.set_ylim(-1.0, 1.0)
    title(ax2, "The same thing as a phasor",
          "the four balanced probes fix $P$, $E_1$ and $E_2$ in closed form")
    fig.tight_layout(pad=1.6)
    fig.savefig(OUT / "analyser_response.png")
    plt.close(fig)


# --------------------------------------------- figure 6: angular harmonics --
def figure_angular_harmonics():
    th = np.linspace(0, 180, 721)
    signed = np.cos(np.deg2rad(2 * (th - 81.8688)))
    mag = np.abs(signed)
    grid = (81.8688 + 22.5 * np.arange(-4, 5)) % 180.0
    grid_signed = np.cos(np.deg2rad(2 * (grid - 81.8688)))

    fig = plt.figure(figsize=(12.6, 5.1))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.35, 1.0, 1.0], wspace=0.30,
                          left=0.06, right=0.98, top=0.735, bottom=0.115)

    ax = fig.add_subplot(gs[0, 0])
    ax.axhline(0, color=GRID, lw=1.0)
    ax.plot(th, signed, color=LASER, lw=2.4, label="signed response  $\\propto\\cos 2\\theta_i$")
    ax.plot(th, mag, color=BLUE, lw=1.8, ls=(0, (5, 2.5)),
            label="its magnitude  $\\propto|\\cos 2\\theta_i|$")
    ax.scatter(grid, grid_signed, color=LASER, s=44, zorder=6,
               edgecolors="white", linewidths=1.0)
    ax.axvline(81.8688, color=VIOLET, lw=1.1, ls=(0, (2, 3)))
    ax.text(81.8688 + 2, -0.92, "production grid centre\n$\\theta_i$ = 81.87$\\degree$",
            fontsize=8.2, color=VIOLET, linespacing=1.4)
    ax.set_xlabel("incident polarisation,  $\\theta_i$  (degrees)")
    ax.set_ylabel("normalised response")
    ax.set_xlim(0, 180); ax.set_ylim(-1.3, 1.3)
    ax.set_xticks([0, 45, 90, 135, 180])
    ax.legend(loc="upper right", fontsize=8.4)
    title(ax, "Signed response reverses every 90$\\degree$",
          "nine measured points, 22.5$\\degree$ apart in $\\theta_i$")

    axp = fig.add_subplot(gs[0, 1], projection="polar")
    axp.plot(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([mag, mag]), color=BLUE, lw=2.2)
    axp.fill(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([mag, mag]), color=BLUE, alpha=0.10)
    axp.scatter(np.deg2rad(np.concatenate([grid, grid + 180])),
                np.abs(np.concatenate([grid_signed, grid_signed])),
                color=BLUE, s=26, zorder=6)
    axp.set_rticks([0.5, 1.0]); axp.set_yticklabels([])
    axp.set_title("magnitude: four lobes\n(the $4\\theta_i$ diagnostic)",
                  fontsize=9.8, pad=20, linespacing=1.4)
    axp.grid(color=GRID)

    axs = fig.add_subplot(gs[0, 2], projection="polar")
    pos = np.clip(signed, 0, None)
    neg = np.clip(-signed, 0, None)
    axs.fill(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([pos, neg]), color=LASER, alpha=0.18)
    axs.plot(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([pos, neg]), color=LASER, lw=2.0)
    axs.fill(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([neg, pos]), color=SLATE, alpha=0.12)
    axs.plot(np.deg2rad(np.concatenate([th, th + 180])),
             np.concatenate([neg, pos]), color=SLATE, lw=1.6, ls=(0, (4, 2.5)))
    axs.set_rticks([0.5, 1.0]); axs.set_yticklabels([])
    axs.set_title("signed: two lobes each sign\n(the $2\\theta_i$ fit)",
                  fontsize=9.8, pad=20, linespacing=1.4)
    axs.grid(color=GRID)

    fig.suptitle("Why the signed fit uses harmonic 2 and the magnitude harmonic 4",
                 x=0.045, ha="left", fontsize=13.5, fontweight="bold", y=0.978)
    fig.text(0.045, 0.905,
             "Fitting the signed complex response at harmonic 4 destroys the sign reversal and "
             "produces spuriously low $R^2$.\nThe code fits the signed response at harmonic 2 "
             "(DEFAULT_ANGULAR_HARMONIC = 2) and runs a separate $4\\theta_i$ fit on the magnitude.",
             ha="left", fontsize=8.6, color=MUTED, linespacing=1.5)
    fig.savefig(OUT / "angular_harmonics.png")
    plt.close(fig)


# ------------------------------------------ figure 7: butterfly vs S-loop ---
def figure_loop_taxonomy():
    v = np.linspace(-40, 40, 600)

    def loop(vc_p, vc_m, w, sat=1.0, lin=0.0, frozen=0.0):
        """A plain hysteresis pair: each branch is a shifted tanh."""
        return (sat * np.tanh((v - vc_m) / w) + lin * v + frozen,
                sat * np.tanh((v - vc_p) / w) + lin * v + frozen)

    def pinched(bias=12.0, half=4.0, w=2.5):
        """Two sub-populations with opposite internal bias.

        The loop is closed at zero field and open on either side, which is the
        signature of defect pinning or internal-bias (defect-dipole) pairs.
        """
        down = 0.5 * (np.tanh((v - (bias - half)) / w)
                      + np.tanh((v + (bias + half)) / w))
        up = 0.5 * (np.tanh((v - (bias + half)) / w)
                    + np.tanh((v + (bias - half)) / w))
        return down, up

    cases = [
        ("ferroelectric_square", loop(8, -8, 1.6),
         "uniform, well-switching\nmean squareness $\\geq$ 0.7", GREEN),
        ("ferroelectric_slanted", loop(9, -9, 6.0),
         "broad coercive-field\ndistribution (squareness 0.3 to 0.7)", CYAN),
        ("ferroelectric_rounded", loop(10, -10, 14.0),
         "strong disorder,\ngraded switching (squareness < 0.3)", BLUE),
        ("pinched", pinched(),
         "constricted at zero field: defect pinning,\ninternal-bias pairs, AFE-like", VIOLET),
        ("linear_no_hysteresis", loop(0.4, -0.4, 0.8, sat=0.05, lin=0.022),
         "paraelectric-like reversible response\n(relative opening < 0.15)", AMBER),
        ("ferroelectric_square *imprinted", loop(16, 2, 2.2),
         "shifted off zero: a built-in internal\nbias field", PINK),
    ]

    fig, axes = plt.subplots(2, 3, figsize=(12.2, 6.8))
    for ax, (name, (down, up), note, col) in zip(axes.ravel(), cases):
        ax.axhline(0, color=GRID, lw=0.9); ax.axvline(0, color=GRID, lw=0.9)
        ax.plot(v, down, color=LASER, lw=2.1)
        ax.plot(v, up, color=GREEN, lw=2.1)
        ax.set_xlim(-42, 42); ax.set_ylim(-1.45, 1.45)
        ax.set_xticks([-40, -20, 0, 20, 40]); ax.set_yticks([])
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.text(0.5, 1.10, name, transform=ax.transAxes, ha="center",
                fontsize=9.8, color=col, fontweight="bold", family="monospace")
        ax.text(0.5, -0.36, note, transform=ax.transAxes, ha="center", va="top",
                fontsize=8.4, color=MUTED, linespacing=1.5)
        ax.set_xlabel("$V_\\mathrm{dc}$ (V)", fontsize=8.6, labelpad=2)
    fig.suptitle("Loop taxonomy: what classify_loop() reports, and what it means",
                 x=0.045, ha="left", fontsize=13.5, fontweight="bold", y=0.982)
    fig.text(0.045, 0.912,
             "Deterministic, threshold-documented categories. Every threshold is a module "
             "constant in pockels_hysteresis_analysis.py and is overridable per call.\n"
             "Red = descending branch, green = ascending. Modifiers (marked *) such as imprinted, "
             "partially_frozen, leaky, drifting_loop and saturation_asymmetric\nare reported "
             "alongside the primary type.",
             ha="left", va="top", fontsize=8.6, color=MUTED, linespacing=1.55)
    fig.subplots_adjust(left=0.045, right=0.985, top=0.775, bottom=0.095,
                        hspace=0.78, wspace=0.16)
    fig.savefig(OUT / "loop_taxonomy.png")
    plt.close(fig)


# --------------------------------------------- figure 9: poling kinetics ----
def figure_poling_kinetics():
    t = np.arange(0, 185, 10.0)
    tau, beta, m0, minf = 38.0, 0.62, 0.22, 1.0
    m = minf - (minf - m0) * np.exp(-((t / tau) ** beta))
    m_noisy = m + RNG.normal(0, 0.012, t.size)
    t_fine = np.linspace(0, 185, 600)
    m_fine = minf - (minf - m0) * np.exp(-((t_fine / tau) ** beta))

    step = np.abs(np.diff(m_noisy)) / np.maximum(np.abs(m_noisy[:-1]), 1e-9) * 100.0
    quiet = step < 2.0
    stop_idx = None
    run = 0
    for i, q in enumerate(quiet):
        run = run + 1 if q else 0
        if run >= 3 and t[i + 1] >= 60.0:
            stop_idx = i + 1
            break

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.0, 4.4),
                                  gridspec_kw={"width_ratios": [1.25, 1.0]})

    ax.plot(t_fine, m_fine, color=SLATE, lw=1.4, alpha=0.8,
            label="stretched-exponential fit")
    ax.plot(t, m_noisy, "o", color=LASER, ms=5, label="lock-in samples (every 10 s)")
    ax.axvline(60, color=MUTED, lw=1.0, ls=(0, (3, 3)))
    ax.text(61, 0.17, "60 s floor", fontsize=8.4, color=MUTED)
    ax.axvline(180, color=MUTED, lw=1.0, ls=(0, (3, 3)))
    ax.text(178, 0.17, "180 s cap", fontsize=8.4, color=MUTED, ha="right")
    if stop_idx is not None:
        ax.axvline(t[stop_idx], color=GREEN, lw=1.6)
        ax.scatter([t[stop_idx]], [m_noisy[stop_idx]], color=GREEN, s=70, zorder=7,
                   edgecolors="white", linewidths=1.2)
        ax.annotate(f"adaptive stop at {t[stop_idx]:.0f} s\n"
                    "(3 consecutive intervals < 2 %)",
                    xy=(t[stop_idx], m_noisy[stop_idx]), xytext=(t[stop_idx] + 16, 0.55),
                    fontsize=8.6, color=GREEN, linespacing=1.5,
                    arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=1.1,
                                    connectionstyle="arc3,rad=-0.2"))
    ax.set_xlabel("poling time  (s)")
    ax.set_ylabel("lock-in magnitude  $|M|$  (normalised)")
    ax.set_xlim(-4, 190); ax.set_ylim(0.1, 1.12)
    ax.legend(loc="lower right")
    title(ax, "Adaptive poling stops at the plateau",
          "$M(t) = M_\\infty - (M_\\infty - M_0)\\,\\exp[-(t/\\tau)^\\beta]$")

    for b, col in [(1.0, GRID), (0.8, CYAN), (0.62, LASER), (0.45, VIOLET)]:
        y = 1 - (1 - 0.0) * np.exp(-((t_fine / tau) ** b))
        ax2.plot(t_fine, y, color=col, lw=2.2 if b == 0.62 else 1.5,
                 label=f"$\\beta$ = {b:.2f}" + ("  (this pixel)" if b == 0.62 else ""))
    ax2.set_xlabel("poling time  (s)")
    ax2.set_ylabel("switched fraction")
    ax2.set_xlim(0, 190); ax2.set_ylim(0, 1.05)
    ax2.legend(loc="lower right")
    title(ax2, "$\\beta$ measures the disorder",
          "$\\beta$ = 1 is a single time constant; $\\beta<1$ means a distribution "
          "of\nswitching times, i.e. a spread of domain-wall pinning energies")
    fig.tight_layout(pad=1.7)
    fig.savefig(OUT / "poling_kinetics.png")
    plt.close(fig)
    return tau, beta


def main():
    apply_style()
    figure_malus_slope()
    figure_analyser_response()
    figure_angular_harmonics()
    figure_loop_taxonomy()
    tau, beta = figure_poling_kinetics()
    for name in ("malus_slope", "analyser_response", "angular_harmonics",
                 "loop_taxonomy", "poling_kinetics"):
        p = OUT / f"{name}.png"
        print(f"  wrote {p.name}  ({p.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
