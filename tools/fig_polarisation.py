"""Figures 1-3: the polarisation state everywhere in the instrument.

These are computed with the Jones/Stokes helpers in ``_polarisation.py``. They
are not artists' impressions.  The beamline is modelled as

    polariser(0 deg) -> HWP(theta_h) -> sample(delta_s, fast axis) -> QWP(gamma)
    -> analyser(a)

with the QWP angle solved numerically for the compensated (linear) output that
lets the analyser reach a true null.  See docs/physics/02-polarisation.md and
docs/physics/03-senarmont-readout.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch
from mpl_toolkits.mplot3d import proj3d

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import (apply_style, BG, INK, MUTED, GRID, LASER, BLUE, VIOLET,
                       GREEN, AMBER, CYAN, PINK, SLATE, title, caption)
from _polarisation import (polariser, half_wave_plate, quarter_wave_plate,
                           retarder, linear_state, normalised_stokes,
                           ellipse_parameters, ellipse_trace, retarder_axis,
                           rotate_about, retarder_arc)

OUT = Path(__file__).resolve().parents[1] / "assets" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- the model
THETA_HWP = 15.0        # raw HWP angle -> incident polarisation theta_i = 30 deg
THETA_I = 2.0 * THETA_HWP
SAMPLE_RETARDANCE = 70.0   # representative static birefringence of the film
SAMPLE_AXIS = 0.0          # film fast axis, in the polarisation frame


def solve_compensating_qwp(state_after_sample: np.ndarray) -> float:
    """QWP angle that turns the elliptical output back into linear light.

    Sweeps the QWP and returns the angle whose output has the smallest |S3|,
    i.e. the setting that puts the state back on the equator of the Poincare
    sphere where a linear analyser can extinguish it.
    """
    angles = np.linspace(-90.0, 90.0, 36001)
    best, best_s3 = 0.0, np.inf
    for a in angles:
        s3 = abs(normalised_stokes(quarter_wave_plate(a) @ state_after_sample)[2])
        if s3 < best_s3:
            best_s3, best = s3, a
    return float(best)


def build_beamline():
    """Return the ordered list of (label, jones, note) stations along the beam."""
    j0 = linear_state(0.0)                                   # after the polariser
    j1 = half_wave_plate(THETA_HWP) @ j0                     # after the HWP
    j2 = retarder(SAMPLE_RETARDANCE, SAMPLE_AXIS) @ j1       # after the BTO film
    gamma = solve_compensating_qwp(j2)
    j3 = quarter_wave_plate(gamma) @ j2                      # after the QWP
    psi3 = ellipse_parameters(j3)[0]
    a_null = psi3 + 90.0                                     # crossed analyser
    stations = [
        ("A", "After polariser 1", j0,
         "linear, azimuth 0\u00b0\nthis defines the polarisation frame"),
        ("B", f"After HWP ({THETA_HWP:.0f}\u00b0)", j1,
         f"still linear, azimuth\n$\\theta_i$ = {THETA_I:.0f}\u00b0 = 2 $\\times$ HWP angle"),
        ("C", "After the BTO film", j2,
         f"elliptical: the film adds\n{SAMPLE_RETARDANCE:.0f}\u00b0 of static retardance"),
        ("D", f"After QWP ({gamma:.1f}\u00b0)", j3,
         "linear again: the QWP has\nundone the film's ellipticity"),
    ]
    return stations, gamma, a_null


# ------------------------------------------------------ figure 1: ellipses --
def figure_ellipses():
    stations, gamma, a_null = build_beamline()
    j_null_in = stations[-1][2]

    fig = plt.figure(figsize=(13.2, 4.7))
    gs = fig.add_gridspec(1, 5, wspace=0.34, left=0.035, right=0.985, top=0.745, bottom=0.12)

    panels = list(stations) + [
        ("E", f"At the analyser (null, {a_null:.1f}\u00b0)", j_null_in,
         "the analyser is crossed with D:\nextinction, the operating null")]

    for k, (tag, name, j, note) in enumerate(panels):
        ax = fig.add_subplot(gs[0, k])
        ex, ey = ellipse_trace(j)
        psi, chi, ratio = ellipse_parameters(j)

        ax.axhline(0, color=GRID, lw=0.9)
        ax.axvline(0, color=GRID, lw=0.9)
        ax.plot(ex, ey, color=LASER if k < 4 else SLATE, lw=2.2, zorder=3)

        # rotation sense
        if abs(chi) > 0.5:
            i = 40
            ax.annotate("", xy=(ex[i + 8], ey[i + 8]), xytext=(ex[i], ey[i]),
                        arrowprops=dict(arrowstyle="-|>", color=LASER, lw=1.6))

        # major axis
        r = 1.15
        ax.plot([-r * np.cos(np.deg2rad(psi)), r * np.cos(np.deg2rad(psi))],
                [-r * np.sin(np.deg2rad(psi)), r * np.sin(np.deg2rad(psi))],
                color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=2)

        if k == 4:  # show the analyser transmission axis and the extinguished state
            aa = np.deg2rad(a_null)
            ax.plot([-1.3 * np.cos(aa), 1.3 * np.cos(aa)],
                    [-1.3 * np.sin(aa), 1.3 * np.sin(aa)],
                    color=GREEN, lw=2.0, zorder=4)
            ax.text(0.97, 0.955, "analyser\naxis", transform=ax.transAxes,
                    ha="right", va="top", fontsize=8, color=GREEN, linespacing=1.2)

        ax.set_xlim(-1.35, 1.35)
        ax.set_ylim(-1.35, 1.35)
        ax.set_aspect("equal")
        ax.set_xticks([]); ax.set_yticks([])
        ax.grid(False)
        for spine in ax.spines.values():
            spine.set_color("#d8dbe0")

        ax.text(0.5, 1.255, f"{tag}", transform=ax.transAxes, ha="center",
                fontsize=12, fontweight="bold", color=INK)
        ax.text(0.5, 1.125, name, transform=ax.transAxes, ha="center",
                fontsize=9.2, color=INK)
        shape = ("linear" if abs(chi) < 0.5
                 else "circular" if abs(abs(chi) - 45.0) < 0.5
                 else "elliptical")
        ax.text(0.035, 0.035,
                f"$\\psi$ {psi:+.1f}$\\degree$\n$\\chi$ {chi:+.1f}$\\degree$\n"
                f"b/a {ratio:.2f}",
                transform=ax.transAxes, ha="left", va="bottom",
                fontsize=8.4, color=INK, family="monospace", linespacing=1.45)
        ax.text(0.965, 0.035, shape, transform=ax.transAxes, ha="right",
                va="bottom", fontsize=8.6, color=LASER if k < 4 else SLATE,
                fontweight="bold")
        ax.text(0.5, -0.045, note, transform=ax.transAxes, ha="center",
                va="top", fontsize=8.0, color=MUTED, wrap=True, linespacing=1.4)

    fig.suptitle("Polarisation state at each station of the beamline",
                 x=0.035, ha="left", fontsize=14, fontweight="bold", y=0.975)
    fig.text(0.035, 0.895,
             "Computed from the Jones chain, not drawn by hand.  "
             "$\\psi$ = ellipse azimuth, $\\chi$ = ellipticity angle "
             "($\\chi$ = 0 linear, $\\chi$ = $\\pm$45$\\degree$ circular), b/a = axial ratio.",
             ha="left", fontsize=8.6, color=MUTED)
    fig.savefig(OUT / "polarisation_ellipses.png")
    plt.close(fig)
    return gamma, a_null


# ------------------------------------------------- sphere drawing helpers --
def _draw_sphere(ax):
    """A clean wireframe Poincare sphere.

    A translucent surface is deliberately avoided: matplotlib does not depth-sort
    surfaces against lines, so a surface would wash out the trajectory arcs.
    """
    t = np.linspace(0, 2 * np.pi, 361)
    # latitude circles
    for lat in np.deg2rad([-60, -30, 30, 60]):
        r = np.cos(lat)
        ax.plot(r * np.cos(t), r * np.sin(t), np.full_like(t, np.sin(lat)),
                color="#dfe3ea", lw=0.7, zorder=1)
    # meridians
    for lon in np.deg2rad(np.arange(0, 180, 30)):
        ax.plot(np.cos(lon) * np.cos(t), np.sin(lon) * np.cos(t), np.sin(t),
                color="#dfe3ea", lw=0.7, zorder=1)
    # equator, emphasised: this is where all linear states live
    ax.plot(np.cos(t), np.sin(t), 0 * t, color="#aab2c0", lw=1.5, zorder=2)
    # axes
    for vec, lab, off in [((1.0, 0, 0), "$S_1$ (H / V linear)", (0.16, -0.10, 0.20)),
                          ((0, 1.0, 0), "$S_2$ ($\\pm$45$\\degree$ linear)", (-0.05, 0.30, -0.06)),
                          ((0, 0, 1.0), "$S_3$ (circular)", (0.02, 0.02, 0.26))]:
        ax.plot([-vec[0] * 1.18, vec[0] * 1.18],
                [-vec[1] * 1.18, vec[1] * 1.18],
                [-vec[2] * 1.18, vec[2] * 1.18],
                color="#9aa3b2", lw=0.9, zorder=2)
        ax.text(vec[0] + off[0], vec[1] + off[1], vec[2] + off[2], lab,
                fontsize=8.4, color=MUTED, zorder=3)
    ax.set_xlim(-1.02, 1.02); ax.set_ylim(-1.02, 1.02); ax.set_zlim(-1.02, 1.02)
    ax.set_box_aspect((1, 1, 1))
    ax.set_axis_off()


# -------------------------------------------- figure 2: beamline on sphere --
def figure_poincare_beamline(gamma, a_null):
    j0 = linear_state(0.0)
    j1 = half_wave_plate(THETA_HWP) @ j0
    j2 = retarder(SAMPLE_RETARDANCE, SAMPLE_AXIS) @ j1
    j3 = quarter_wave_plate(gamma) @ j2

    p0, p1, p2, p3 = (normalised_stokes(j) for j in (j0, j1, j2, j3))

    arc_hwp = retarder_arc(THETA_HWP, 180.0, p0)
    arc_sample = retarder_arc(SAMPLE_AXIS, SAMPLE_RETARDANCE, p1)
    arc_qwp = retarder_arc(gamma, 90.0, p2)

    fig = plt.figure(figsize=(12.4, 5.15))
    ax = fig.add_subplot(1, 2, 1, projection="3d")
    _draw_sphere(ax)

    for arc, col, lab in [(arc_hwp, BLUE, "HWP: 180$\\degree$ rotation"),
                          (arc_sample, LASER, f"BTO film: {SAMPLE_RETARDANCE:.0f}$\\degree$"),
                          (arc_qwp, VIOLET, "QWP: 90$\\degree$ rotation")]:
        ax.plot(arc[:, 0], arc[:, 1], arc[:, 2], color=col, lw=2.6, label=lab, zorder=5)

    for p, tag, col in [(p0, "A", SLATE), (p1, "B", BLUE),
                        (p2, "C", LASER), (p3, "D", VIOLET)]:
        ax.scatter(*p, color=col, s=46, depthshade=False, zorder=8,
                   edgecolors="white", linewidths=1.0)
        ax.text(p[0] * 1.18, p[1] * 1.18, p[2] * 1.18, tag, fontsize=11,
                fontweight="bold", color=col)

    # the analyser null axis (antipode of the compensated state)
    ax.plot([-p3[0] * 1.3, p3[0] * 1.3], [-p3[1] * 1.3, p3[1] * 1.3],
            [-p3[2] * 1.3, p3[2] * 1.3], color=GREEN, lw=1.4, ls=(0, (5, 3)), zorder=6)
    ax.scatter(*(-p3), color=GREEN, s=46, depthshade=False, zorder=8,
               edgecolors="white", linewidths=1.0, marker="X")
    ax.text(-p3[0] * 1.30, -p3[1] * 1.30, -p3[2] * 1.30, "analyser\nnull axis",
            fontsize=8.2, color=GREEN)

    ax.view_init(elev=22, azim=38)
    ax.legend(loc="upper left", bbox_to_anchor=(-0.02, 0.98), fontsize=8.4)
    ax.text2D(0.0, 1.03, "Trajectory through the instrument",
              transform=ax.transAxes, fontsize=11, fontweight="bold", color=INK)

    # ---- right panel: what each element does, as a table of states ----
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.axis("off")
    rows = [
        ("A", "after polariser", p0, j0),
        ("B", f"after HWP ({THETA_HWP:.0f}$\\degree$)", p1, j1),
        ("C", "after BTO film", p2, j2),
        ("D", f"after QWP ({gamma:.1f}$\\degree$)", p3, j3),
    ]
    ax2.text(0.0, 0.985, "State at each station", fontsize=11,
             fontweight="bold", color=INK, transform=ax2.transAxes)
    ax2.text(0.0, 0.915,
             "Every retarder is a rigid rotation of the sphere about its own\n"
             "equatorial axis, through an angle equal to its retardance.  The\n"
             "QWP angle is solved so that the state lands back on the equator,\n"
             "where a linear analyser can extinguish it completely.",
             fontsize=8.6, color=MUTED, va="top", transform=ax2.transAxes)
    head_y = 0.700
    ax2.text(0.0, head_y, "pt", fontsize=8.6, color=MUTED, fontweight="bold",
             transform=ax2.transAxes)
    ax2.text(0.07, head_y, "station", fontsize=8.6, color=MUTED, fontweight="bold",
             transform=ax2.transAxes)
    ax2.text(0.44, head_y, "$(S_1, S_2, S_3)$", fontsize=8.6, color=MUTED,
             fontweight="bold", transform=ax2.transAxes)
    ax2.text(0.79, head_y, "$\\psi$ / $\\chi$", fontsize=8.6, color=MUTED,
             fontweight="bold", transform=ax2.transAxes)
    ax2.plot([0.0, 1.0], [head_y - 0.02, head_y - 0.02], color=GRID, lw=1.0,
             transform=ax2.transAxes, clip_on=False)
    for i, (tag, name, p, j) in enumerate(rows):
        y = head_y - 0.085 - i * 0.090
        psi, chi, _ = ellipse_parameters(j)
        col = {"A": SLATE, "B": BLUE, "C": LASER, "D": VIOLET}[tag]
        ax2.text(0.0, y, tag, fontsize=10, fontweight="bold", color=col,
                 transform=ax2.transAxes)
        ax2.text(0.07, y, name, fontsize=9, color=INK, transform=ax2.transAxes)
        ax2.text(0.44, y, f"({p[0]:+.2f}, {p[1]:+.2f}, {p[2]:+.2f})",
                 fontsize=8.6, color=INK, family="monospace",
                 transform=ax2.transAxes)
        ax2.text(0.79, y, f"{psi:+6.1f}$\\degree$ / {chi:+5.1f}$\\degree$",
                 fontsize=8.6, color=INK, family="monospace",
                 transform=ax2.transAxes)
    ax2.text(0.0, 0.255,
             "Reading the sphere\n"
             "  equator          linear polarisation\n"
             "  poles            circular polarisation\n"
             "  latitude 2$\\chi$      how elliptical the light is\n"
             "  longitude 2$\\psi$     the azimuth of the ellipse\n"
             "  antipodal pair   orthogonal states (crossed analyser)",
             fontsize=8.8, color=INK, va="top", family="monospace",
             transform=ax2.transAxes)
    fig.subplots_adjust(left=-0.03, right=0.985, top=0.99, bottom=0.0, wspace=-0.02)
    fig.savefig(OUT / "poincare_beamline.png")
    plt.close(fig)


# ------------------------------------------ figure 3: the EO modulation ----
def figure_poincare_modulation(gamma, a_null):
    """Why the readout sits 45 degrees from the null, shown on the sphere."""
    j1 = half_wave_plate(THETA_HWP) @ linear_state(0.0)
    j2 = retarder(SAMPLE_RETARDANCE, SAMPLE_AXIS) @ j1
    j3 = quarter_wave_plate(gamma) @ j2
    p3 = normalised_stokes(j3)

    gamma_ac = 14.0     # exaggerated for visibility; the real value is ~microradians

    def state_with_eo(g):
        j = retarder(SAMPLE_RETARDANCE + g, SAMPLE_AXIS) @ j1
        return normalised_stokes(quarter_wave_plate(gamma) @ j)

    swing = np.array([state_with_eo(g) for g in np.linspace(-gamma_ac, gamma_ac, 60)])

    fig = plt.figure(figsize=(12.8, 5.3))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.0, 1.12], wspace=0.06,
                          left=0.0, right=0.975, top=0.885, bottom=0.115)

    ax = fig.add_subplot(gs[0, 0], projection="3d")
    _draw_sphere(ax)

    # analyser axes: null (antipodal to the state) and the two slope points
    for off, col, lab, mk in [(0.0, GREEN, "null", "X"),
                              (+45.0, BLUE, "+45$\\degree$", "o"),
                              (-45.0, AMBER, "-45$\\degree$", "o")]:
        ang = np.deg2rad(2.0 * (a_null + off))
        a_vec = np.array([np.cos(ang), np.sin(ang), 0.0])
        ax.plot([0, a_vec[0]], [0, a_vec[1]], [0, 0], color=col, lw=1.7, zorder=4)
        ax.scatter(*a_vec, color=col, s=48, marker=mk, depthshade=False,
                   zorder=9, edgecolors="white", linewidths=1.0)
        ax.text(a_vec[0] * 1.26, a_vec[1] * 1.26, 0.10, lab, fontsize=9,
                color=col, fontweight="bold")

    ax.plot(swing[:, 0], swing[:, 1], swing[:, 2], color=LASER, lw=4.0, zorder=10)
    ax.scatter(*p3, color=VIOLET, s=58, depthshade=False, zorder=11,
               edgecolors="white", linewidths=1.0)
    ax.text(p3[0] * 0.92, p3[1] * 1.85, p3[2] - 0.46, "operating\nstate",
            fontsize=8.6, color=VIOLET, ha="center")

    ax.view_init(elev=24, azim=48)
    ax.text2D(0.02, 1.055, "The electro-optic modulation on the sphere",
              transform=ax.transAxes, fontsize=11.5, fontweight="bold", color=INK)
    ax.text2D(0.02, 0.985,
              "red arc: the state swept by the AC drive\n"
              f"(exaggerated to $\\pm${gamma_ac:.0f}$\\degree$; the real swing is microradians)",
              transform=ax.transAxes, fontsize=8.4, color=MUTED, linespacing=1.4)
    ax.text2D(0.02, 0.06,
              "The analyser measures the projection of the state\n"
              "onto its own axis.  At the null the two are antipodal,\n"
              "so the projection is second order in the swing.  At\n"
              "$\\pm$45$\\degree$ the axis is 90$\\degree$ away and the projection is\n"
              "first order, and of opposite sign on the two sides.",
              transform=ax.transAxes, fontsize=8.4, color=INK, linespacing=1.5)

    # ---- right panel: the Malus curve and its derivative ----
    ax2 = fig.add_subplot(gs[0, 1])
    offsets = np.linspace(-90, 90, 721)
    inten = 0.5 * (1.0 - np.cos(np.deg2rad(2.0 * offsets)))     # Malus about the null
    slope = np.gradient(inten, np.deg2rad(offsets))
    slope /= np.max(np.abs(slope))
    ax2.axhline(0, color=GRID, lw=1.0)
    ax2.plot(offsets, inten, color=BLUE, lw=2.4, label="transmission  $I(\\psi)$")
    ax2.plot(offsets, slope, color=LASER, lw=2.1, ls=(0, (5, 2.5)),
             label="slope  $dI/d\\psi$  (normalised)")
    for off, col in [(45, BLUE), (-45, AMBER)]:
        ax2.axvline(off, color=col, lw=1.0, ls=(0, (2, 3)))
        ax2.scatter([off], [0.5], color=col, s=46, zorder=6,
                    edgecolors="white", linewidths=1.0)
    ax2.axvline(0, color=GREEN, lw=1.2, ls=(0, (2, 3)))
    ax2.scatter([0], [0], color=GREEN, s=46, zorder=6, marker="X",
                edgecolors="white", linewidths=1.0)

    ax2.annotate("$+$45$\\degree$ and $-$45$\\degree$:\n$|dI/d\\psi|$ is maximal,\n"
                 "and the two slopes\nhave opposite sign",
                 xy=(45, 1.0), xytext=(56, 0.16), fontsize=8.6, color=BLUE,
                 linespacing=1.5, ha="left",
                 arrowprops=dict(arrowstyle="-|>", color=BLUE, lw=1.1,
                                 connectionstyle="arc3,rad=0.2"))
    ax2.annotate("null: $I$ and $dI/d\\psi$ both vanish.\n"
                 "The EO signal disappears here, so this\nreading measures the background alone.",
                 xy=(0, 0), xytext=(-88, -0.86), fontsize=8.6, color=GREEN,
                 linespacing=1.5, ha="left",
                 arrowprops=dict(arrowstyle="-|>", color=GREEN, lw=1.1,
                                 connectionstyle="arc3,rad=-0.22"))

    ax2.set_xlabel("analyser offset from the null,  $\\psi$  (degrees)")
    ax2.set_ylabel("normalised")
    ax2.set_xlim(-90, 90)
    ax2.set_ylim(-1.15, 1.30)
    ax2.set_xticks([-90, -45, 0, 45, 90])
    ax2.legend(loc="upper left", bbox_to_anchor=(0.005, 0.995))
    ax2.set_title("Why the readout sits at $\\pm$45$\\degree$", loc="left",
                  fontsize=11.5, pad=18)
    ax2.text(0.0, 1.022,
             "on the Poincar\u00e9 sphere the analyser axis is then 90$\\degree$ from the state",
             transform=ax2.transAxes, fontsize=8.4, color=MUTED)
    fig.savefig(OUT / "poincare_modulation.png")
    plt.close(fig)


def main():
    apply_style()
    gamma, a_null = figure_ellipses()
    figure_poincare_beamline(gamma, a_null)
    figure_poincare_modulation(gamma, a_null)
    print(f"beamline: theta_i = {THETA_I:.1f} deg, sample retardance = "
          f"{SAMPLE_RETARDANCE:.1f} deg, compensating QWP = {gamma:.3f} deg, "
          f"analyser null = {a_null:.3f} deg")
    for name in ("polarisation_ellipses", "poincare_beamline", "poincare_modulation"):
        p = OUT / f"{name}.png"
        print(f"  wrote {p.relative_to(OUT.parents[1])}  ({p.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
