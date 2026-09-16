"""Material physics figures: perovskite structure, strain, polarisation rotation.

These illustrate the structural origin of the electro-optic response that the
instrument measures. The crystallographic drawings are schematic in the sense
that atom radii are chosen for legibility, but the displacements, axes, phase
sequence and free-energy landscapes are drawn from the accepted physics rather
than invented. See docs/physics/06-material.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, Rectangle, FancyBboxPatch
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _figstyle import (apply_style, INK, MUTED, GRID, PANEL, LASER, BLUE, VIOLET,
                       GREEN, AMBER, CYAN, PINK, SLATE, title)

OUT = Path(__file__).resolve().parents[1] / "assets" / "figures"
OUT.mkdir(parents=True, exist_ok=True)

BA = "#3b82f6"     # A site
TI = "#ef4444"     # B site
OX = "#0ea5e9"     # oxygen


# --------------------------------------------------------------- projection --
def iso(x, y, z, sx=0.50, sy=0.28):
    """A simple axonometric projection: x to the right, y back, z up."""
    return x + sx * y, z + sy * y


def draw_cell(ax, ox, oy, a=1.0, c=1.0, shift=0.0, scale=1.0,
              show_octahedron=True, labels=False, dim=False):
    """Draw one perovskite cell. `shift` is the B-site displacement along z."""
    al = 0.30 if dim else 1.0
    corners = [(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)]

    def P(x, y, z):
        px, py = iso(x * a, y * a, z * c)
        return ox + scale * px, oy + scale * py

    # cell edges
    for p in corners:
        for axis in range(3):
            q = list(p)
            if q[axis] == 0:
                q[axis] = 1
                x1, y1 = P(*p); x2, y2 = P(*q)
                ax.plot([x1, x2], [y1, y2], color="#aab2c0", lw=1.0,
                        alpha=al, zorder=2)

    # oxygen at face centres
    faces = [(0.5, 0.5, 0), (0.5, 0.5, 1), (0.5, 0, 0.5),
             (0.5, 1, 0.5), (0, 0.5, 0.5), (1, 0.5, 0.5)]
    if show_octahedron:
        opts = [P(*f) for f in faces]
        # octahedron edges
        for i in range(len(faces)):
            for j in range(i + 1, len(faces)):
                d = np.linalg.norm(np.array(faces[i]) - np.array(faces[j]))
                if abs(d - 0.7071) < 0.05:
                    ax.plot([opts[i][0], opts[j][0]], [opts[i][1], opts[j][1]],
                            color=OX, lw=1.0, alpha=0.35 * al, zorder=3)
        for (fx, fy) in opts:
            ax.add_patch(Circle((fx, fy), 0.07 * scale, facecolor=OX,
                                edgecolor="white", lw=0.8, alpha=al, zorder=5))

    # A site at the corners
    for p in corners:
        px, py = P(*p)
        ax.add_patch(Circle((px, py), 0.11 * scale, facecolor=BA,
                            edgecolor="white", lw=0.9, alpha=al, zorder=6))

    # B site at the body centre, displaced by `shift` along z
    bx, by = P(0.5, 0.5, 0.5 + shift)
    ax.add_patch(Circle((bx, by), 0.095 * scale, facecolor=TI,
                        edgecolor="white", lw=0.9, alpha=al, zorder=7))
    if abs(shift) > 1e-6:
        cx, cy = P(0.5, 0.5, 0.5)
        ax.add_patch(Circle((cx, cy), 0.030 * scale, facecolor="none",
                            edgecolor=TI, lw=0.9, ls=(0, (1.5, 1.5)),
                            alpha=al, zorder=6))
        ax.annotate("", xy=(bx, by), xytext=(cx, cy),
                    arrowprops=dict(arrowstyle="-|>", color=TI, lw=1.6), zorder=8)
    if labels:
        px, py = P(0, 0, 0)
        ax.text(px - 0.16 * scale, py - 0.12 * scale, "Ba", color=BA,
                fontsize=8.6, fontweight="bold", ha="right")
        ax.text(bx + 0.14 * scale, by + 0.02 * scale, "Ti", color=TI,
                fontsize=8.6, fontweight="bold")
        fx, fy = P(0.5, 0, 0.5)
        ax.annotate("O", xy=(fx, fy), xytext=(fx + 0.02 * scale, fy - 0.42 * scale),
                    color=OX, fontsize=8.6, fontweight="bold", ha="center",
                    arrowprops=dict(arrowstyle="-", color=OX, lw=0.8))
        ox_, oy_ = P(0.5, 0.5, 1)
        ax.text(ox_ - 1.05 * scale, oy_ + 0.30 * scale, "O$_6$ octahedron",
                color=OX, fontsize=8.0, ha="left")
    return P


# ------------------------------------------- figure A: perovskite structure --
def figure_perovskite():
    fig = plt.figure(figsize=(13.0, 5.4))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.0, 1.0, 1.25], wspace=0.06,
                          left=0.02, right=0.985, top=0.80, bottom=0.10)

    # --- cubic, paraelectric ---
    ax = fig.add_subplot(gs[0, 0]); ax.set_aspect("equal"); ax.axis("off")
    draw_cell(ax, 0.55, 0.45, a=1.0, c=1.0, shift=0.0, scale=1.05, labels=True)
    ax.set_xlim(-0.55, 2.35); ax.set_ylim(-0.45, 2.42)
    ax.text(0.5, 1.02, "cubic  $Pm\\bar{3}m$", transform=ax.transAxes,
            ha="center", fontsize=11, fontweight="bold", color=INK)
    ax.text(0.5, 0.95, "above $T_c$, paraelectric", transform=ax.transAxes,
            ha="center", fontsize=8.6, color=MUTED)
    ax.text(0.5, -0.02, "Ti sits at the centre of its oxygen\noctahedron. "
            "Inversion symmetry holds,\nso every $r_{ij}$ must vanish.",
            transform=ax.transAxes, ha="center", va="top", fontsize=8.4,
            color=MUTED, linespacing=1.5)

    # --- tetragonal, ferroelectric ---
    ax2 = fig.add_subplot(gs[0, 1]); ax2.set_aspect("equal"); ax2.axis("off")
    draw_cell(ax2, 0.55, 0.40, a=1.0, c=1.10, shift=0.16, scale=1.05, labels=False)
    ax2.annotate('Ti off-centre\n(exaggerated)', xy=(1.13, 1.02), xytext=(-0.28, 1.88),
                 fontsize=8.2, color=TI, linespacing=1.4, ha='left',
                 arrowprops=dict(arrowstyle='-|>', color=TI, lw=1.0,
                                 connectionstyle='arc3,rad=-0.25'))
    ax2.annotate('', xy=(2.02, 1.72), xytext=(2.02, 1.30),
                 arrowprops=dict(arrowstyle='-|>', color=TI, lw=2.4))
    ax2.text(2.10, 1.51, '$P_s$', color=TI, fontsize=11, va='center',
             fontweight='bold')
    ax2.set_xlim(-0.45, 2.35); ax2.set_ylim(-0.45, 2.42)
    ax2.annotate("", xy=(1.72, 1.58), xytext=(1.72, 0.40),
                 arrowprops=dict(arrowstyle="<|-|>", color=SLATE, lw=1.1))
    ax2.text(1.78, 0.99, "$c$", color=SLATE, fontsize=10, va="center")
    ax2.annotate("", xy=(1.60, 0.16), xytext=(0.55, 0.16),
                 arrowprops=dict(arrowstyle="<|-|>", color=SLATE, lw=1.1))
    ax2.text(1.07, 0.00, "$a$", color=SLATE, fontsize=10, ha="center")
    ax2.text(0.5, 1.02, "tetragonal  $P4mm$", transform=ax2.transAxes,
             ha="center", fontsize=11, fontweight="bold", color=INK)
    ax2.text(0.5, 0.95, "room temperature, ferroelectric",
             transform=ax2.transAxes, ha="center", fontsize=8.6, color=MUTED)
    ax2.text(0.5, -0.02, "Ti moves off centre along $c$ and the\ncell elongates, "
             "$c/a \\approx 1.01$. Inversion is\nbroken, so $r_{13}$, $r_{33}$ "
             "and $r_{42}$ survive.",
             transform=ax2.transAxes, ha="center", va="top", fontsize=8.4,
             color=MUTED, linespacing=1.5)

    # --- the six variants ---
    ax3 = fig.add_subplot(gs[0, 2]); ax3.set_aspect("equal"); ax3.axis("off")
    ax3.set_xlim(-1.45, 1.45); ax3.set_ylim(-1.35, 1.55)
    for (dx, dy, dz) in [(1, 0, 0), (-1, 0, 0), (0, 1, 0),
                         (0, -1, 0), (0, 0, 1), (0, 0, -1)]:
        x, y = iso(dx, dy, dz)
        col = AMBER if dz != 0 else GREEN
        ax3.annotate("", xy=(x, y), xytext=(0, 0),
                     arrowprops=dict(arrowstyle="-|>", color=col, lw=2.4))
    ax3.add_patch(Circle((0, 0), 0.075, facecolor=INK, edgecolor="white",
                         lw=1.0, zorder=6))
    ax3.text(1.06, 0.06, "$[100]$", fontsize=8.4, color=GREEN)
    ax3.text(-1.42, 0.06, "$[\\bar{1}00]$", fontsize=8.4, color=GREEN)
    ax3.text(0.54, 0.34, "$[010]$", fontsize=8.4, color=GREEN)
    ax3.text(-0.98, -0.34, "$[0\\bar{1}0]$", fontsize=8.4, color=GREEN)
    ax3.text(0.07, 1.06, "$[001]$", fontsize=8.4, color=AMBER)
    ax3.text(0.07, -1.16, "$[00\\bar{1}]$", fontsize=8.4, color=AMBER)
    ax3.text(0.5, 1.02, "six polar variants", transform=ax3.transAxes,
             ha="center", fontsize=11, fontweight="bold", color=INK)
    ax3.text(0.5, 0.95, "the $\\langle 001\\rangle$ directions of the parent cube",
             transform=ax3.transAxes, ha="center", fontsize=8.6, color=MUTED)
    ax3.text(0.5, -0.02,
             "In a film grown on a substrate, an out-of-plane variant is a\n"
             "$c$ domain (amber) and an in-plane variant is an $a$ domain (green).\n"
             "Only $a$ domains give a first-order signal in this geometry.",
             transform=ax3.transAxes, ha="center", va="top", fontsize=8.4,
             color=MUTED, linespacing=1.5)

    fig.suptitle("Perovskite BaTiO$_3$: the structure that makes the effect possible",
                 x=0.02, ha="left", fontsize=13.5, fontweight="bold", y=0.975)
    fig.text(0.02, 0.905,
             "The linear electro-optic effect exists only because the cubic cell "
             "distorts. Cooling through the Curie point moves the Ti ion off centre, "
             "breaking inversion\nsymmetry and giving the unit cell a permanent dipole. "
             "Everything this instrument measures follows from that displacement.",
             ha="left", va="top", fontsize=8.7, color=MUTED, linespacing=1.55)
    fig.savefig(OUT / "perovskite_structure.png")
    plt.close(fig)


# ------------------------------------------- figure B: polarisation rotation --
def _landau_energy(theta_deg, anisotropy=1.0):
    """Free energy against the direction of P, in the (100)-(001) plane.

    A minimal uniaxial anisotropy model: wells at 0 and 90 degrees with a
    barrier whose height is set by `anisotropy`. Reducing the anisotropy
    flattens the valley that connects the two variants, which is what an
    epitaxial strain or a buffer layer does in practice.
    """
    th = np.deg2rad(theta_deg)
    return anisotropy * np.sin(2.0 * th) ** 2


def figure_polarisation_rotation():
    fig = plt.figure(figsize=(13.2, 6.1))
    gs = fig.add_gridspec(1, 3, width_ratios=[1.12, 1.0, 1.0], wspace=0.30,
                          left=0.055, right=0.985, top=0.672, bottom=0.125)

    th = np.linspace(0, 90, 601)

    # --- the energy valley ---
    ax = fig.add_subplot(gs[0, 0])
    for k, (aniso, col, lab, lw) in enumerate([
            (1.00, SLATE, "stiff: a deep barrier", 1.6),
            (0.45, BLUE, "softened by strain", 1.8),
            (0.12, LASER, "flat valley: rotation is nearly free", 2.6)]):
        ax.plot(th, _landau_energy(th, aniso), color=col, lw=lw, label=lab)
    ax.scatter([0, 90], [0, 0], color=AMBER, s=70, zorder=6,
               edgecolors="white", linewidths=1.2)
    ax.text(2, -0.085, "$c$ variant\n$P\\parallel[001]$", fontsize=8.4,
            color=AMBER, ha="left", va="top", linespacing=1.4)
    ax.text(88, -0.085, "$a$ variant\n$P\\parallel[100]$", fontsize=8.4,
            color=AMBER, ha="right", va="top", linespacing=1.4)
    ax.annotate("", xy=(78, 0.075), xytext=(12, 0.075),
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=1.8))
    ax.text(45, 0.115, "rotation path", ha="center", fontsize=9,
            color=LASER, fontweight="bold")
    ax.set_xlabel("direction of $P$ in the $(010)$ plane,  $\\theta$  (degrees)")
    ax.set_ylabel("free energy  (arbitrary)")
    ax.set_xlim(-3, 93); ax.set_ylim(-0.30, 1.22)
    ax.set_xticks([0, 30, 45, 60, 90])
    ax.legend(loc="upper right", fontsize=8.4)
    ax.set_title("Rotating $P$ costs energy, but how much is tunable",
                 loc="left", pad=30)
    ax.text(0.0, 1.075,
            "flatten the valley and the transverse susceptibility diverges,\n"
            "which is exactly what epitaxial strain is used to do",
            transform=ax.transAxes, fontsize=8.5, color=MUTED, linespacing=1.45)

    # --- transverse susceptibility ---
    ax2 = fig.add_subplot(gs[0, 1])
    aniso = np.linspace(0.04, 1.0, 400)
    chi_perp = 1.0 / aniso
    ax2.plot(aniso, chi_perp, color=LASER, lw=2.4)
    ax2.fill_between(aniso, 0, chi_perp, color=LASER, alpha=0.08)
    for a0, col, lab in [(1.00, SLATE, "stiff"), (0.45, BLUE, "softened"),
                         (0.12, LASER, "flat")]:
        ax2.scatter([a0], [1.0 / a0], color=col, s=62, zorder=6,
                    edgecolors="white", linewidths=1.2)
        ax2.annotate(lab, xy=(a0, 1.0 / a0),
                     xytext=(a0 - 0.09, 1.0 / a0 + 3.4), fontsize=8.4, color=col,
                     ha="center",
                     arrowprops=dict(arrowstyle="-", color=col, lw=0.7))
    ax2.set_xlabel("curvature of the valley  (arbitrary)")
    ax2.set_ylabel("$\\chi_\\perp \\propto$ 1 / curvature")
    ax2.set_xlim(0, 1.05); ax2.set_ylim(0, 27)
    ax2.invert_xaxis()
    ax2.set_title("A flat valley is a soft transverse mode", loc="left", pad=30)
    ax2.text(0.0, 1.075,
             "$\\chi_\\perp = (\\partial^2 F/\\partial P_\\perp^2)^{-1}$: the softer the\n"
             "valley, the larger the response to a transverse field",
             transform=ax2.transAxes, fontsize=8.5, color=MUTED, linespacing=1.45)

    # --- the consequence for r42 ---
    ax3 = fig.add_subplot(gs[0, 2])
    ratio = chi_perp / 1.0
    ax3.plot(aniso, ratio, color=VIOLET, lw=2.4)
    ax3.fill_between(aniso, 0, ratio, color=VIOLET, alpha=0.08)
    ax3.set_xlabel("curvature of the valley  (arbitrary)")
    ax3.set_ylabel("$r_{42}/r_{33} \\simeq \\chi_{11}/\\chi_{33}$")
    ax3.set_xlim(0, 1.05); ax3.set_ylim(0, 27)
    ax3.invert_xaxis()
    ax3.axhline(20, color=MUTED, lw=1.0, ls=(0, (3, 3)))
    ax3.text(0.98, 17.6, "measured BaTiO$_3$ sits here:\n$r_{42}\\sim10^3$ pm/V against\n$r_{33}\\sim10$ to $10^2$ pm/V",
             fontsize=8.2, color=MUTED, ha="left", va="top", linespacing=1.5)
    ax3.set_title("which is why $r_{42}$ is the large one", loc="left", pad=30)
    ax3.text(0.0, 1.075,
             "$r_{ijk}\\simeq 2g_{ijkl}P_l\\varepsilon_0\\chi_{kk}$, so the shear\n"
             "coefficient inherits the transverse susceptibility",
             transform=ax3.transAxes, fontsize=8.5, color=MUTED, linespacing=1.45)

    fig.suptitle("Polarisation rotation: why the shear coefficient is enormous",
                 x=0.055, ha="left", fontsize=13.5, fontweight="bold", y=0.982)
    fig.text(0.055, 0.925,
             "In a ferroelectric the electro-optic tensor is the quadratic "
             "polarisation-optic coupling evaluated at the spontaneous polarisation, "
             "so each coefficient carries a\nfactor of the susceptibility along the "
             "field direction. A field applied across the polar axis rotates $P$ rather "
             "than lengthening it, and when the valley\nconnecting two variants is "
             "flat that rotation is nearly free. This is the mechanism a buffer layer "
             "is engineered to exploit.",
             ha="left", va="top", fontsize=8.7, color=MUTED, linespacing=1.55)
    fig.savefig(OUT / "polarisation_rotation.png")
    plt.close(fig)


# ------------------------------------ figure C: misfit strain phase diagram --
def figure_strain_phase_diagram():
    """Schematic misfit-strain phase diagram for an epitaxial BaTiO3 film.

    The topology follows the thermodynamic treatments of epitaxial perovskite
    films: compressive misfit stabilises the out-of-plane polarised c phase,
    tensile misfit stabilises the in-plane polarised aa phase, and a bridging
    region of lower symmetry separates them. Boundary positions here are
    illustrative, not computed from a specific coefficient set.
    """
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.5),
                                  gridspec_kw={"width_ratios": [1.25, 1.0]})

    u = np.linspace(-1.6, 1.6, 400)          # misfit strain, per cent
    t_para = 130 + 220 * np.abs(u) ** 1.1     # paraelectric boundary
    ax.fill_between(u, 0, t_para, where=(u < -0.12), color=AMBER, alpha=0.16)
    ax.fill_between(u, 0, t_para, where=(u > 0.12), color=GREEN, alpha=0.16)
    ax.fill_between(u, 0, t_para, where=(np.abs(u) <= 0.12), color=LASER, alpha=0.20)
    ax.fill_between(u, t_para, 620, color="#eef1f6")
    ax.plot(u, t_para, color=SLATE, lw=2.0)
    ax.axvline(-0.12, color=SLATE, lw=1.2, ls=(0, (4, 3)))
    ax.axvline(0.12, color=SLATE, lw=1.2, ls=(0, (4, 3)))

    ax.text(-1.50, 205, "$c$ phase", fontsize=11, color="#a16207", fontweight="bold")
    ax.text(-1.50, 172, "$P$ out of plane\ninvisible to this\ninstrument",
            fontsize=8.3, color="#a16207", linespacing=1.45, va="top")
    ax.text(0.62, 205, "$aa$ phase", fontsize=11, color="#15803d", fontweight="bold")
    ax.text(0.62, 172, "$P$ in plane\nthis is what the\nbeam can see",
            fontsize=8.3, color="#15803d", linespacing=1.45, va="top")
    ax.annotate("bridging $r$ / monoclinic\n$P$ rotates continuously",
                xy=(0.0, 95), xytext=(0.30, 268), fontsize=8.8, color=LASER,
                fontweight="bold", ha="left", linespacing=1.5,
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=1.2,
                                connectionstyle="arc3,rad=0.25"))
    ax.text(1.28, 520, "paraelectric", fontsize=9.6, color=MUTED, ha="right")

    ax.annotate("unbuffered film", xy=(-0.72, 40), xytext=(-1.52, 545),
                fontsize=8.8, color=INK,
                arrowprops=dict(arrowstyle="-|>", color=INK, lw=1.2,
                                connectionstyle="arc3,rad=-0.2"))
    ax.scatter([-0.72], [40], color=INK, s=70, zorder=7,
               edgecolors="white", linewidths=1.2)
    ax.annotate("with a buffer layer", xy=(-0.02, 40), xytext=(0.52, 545),
                fontsize=8.8, color=LASER, fontweight="bold",
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=1.4,
                                connectionstyle="arc3,rad=0.2"))
    ax.scatter([-0.02], [40], color=LASER, s=80, zorder=7,
               edgecolors="white", linewidths=1.2)
    ax.annotate("", xy=(-0.06, 40), xytext=(-0.68, 40),
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=2.2))

    ax.set_xlabel("misfit strain  (per cent).  compressive $\\leftarrow$   "
                  "$\\rightarrow$ tensile")
    ax.set_ylabel("temperature  (arbitrary, $^\\circ$C-like)")
    ax.set_xlim(-1.6, 1.6); ax.set_ylim(0, 620)
    title(ax, "The substrate chooses the phase",
          "schematic topology: boundary positions are illustrative, "
          "not computed")

    # --- what the buffer layer does, as a sequence ---
    ax2.axis("off")
    ax2.set_xlim(0, 10); ax2.set_ylim(0, 10)
    ax2.text(0.0, 9.7, "The rotation path a buffer layer opens",
             fontsize=11.5, fontweight="bold", color=INK)
    ax2.text(0.0, 9.05,
             "Yu and co-workers insert a GdScO$_3$ buffer between the substrate\n"
             "and the film. Tuning the misfit walks the film along a continuous\n"
             "rotation path rather than jumping between variants:",
             fontsize=8.7, color=MUTED, va="top", linespacing=1.55)

    stages = [
        ("out-of-plane\ntetragonal-like", 0.0, AMBER, "$P\\parallel[001]$"),
        ("rhombohedral-like\n(bridging)", 45.0, LASER, "$P$ tilted"),
        ("in-plane\ntetragonal-like", 90.0, GREEN, "$P\\parallel[100]$"),
    ]
    for k, (name, ang, col, sub) in enumerate(stages):
        cx = 1.6 + k * 3.35
        cy = 5.1
        ax2.add_patch(Circle((cx, cy), 1.05, facecolor="white",
                             edgecolor=col, lw=1.8, zorder=3))
        a = np.deg2rad(90.0 - ang)
        ax2.annotate("", xy=(cx + 0.72 * np.cos(a), cy + 0.72 * np.sin(a)),
                     xytext=(cx - 0.72 * np.cos(a), cy - 0.72 * np.sin(a)),
                     arrowprops=dict(arrowstyle="-|>", color=col, lw=2.6), zorder=5)
        ax2.text(cx, cy + 1.60, name, ha="center", fontsize=8.8, color=col,
                 fontweight="bold", linespacing=1.4)
        ax2.text(cx, cy - 1.55, sub, ha="center", fontsize=8.6, color=MUTED)
        if k < len(stages) - 1:
            ax2.annotate("", xy=(cx + 2.25, cy), xytext=(cx + 1.15, cy),
                         arrowprops=dict(arrowstyle="-|>", color=SLATE, lw=1.6))
    ax2.text(5.0, 2.35,
             "Along that path the valley of the previous figure is flat, the\n"
             "transverse susceptibility is large, and the effective coefficient\n"
             "rises. The buffered films of that study reach 175 pm/V.",
             ha="center", fontsize=8.7, color=INK, va="top", linespacing=1.6)
    ax2.text(5.0, 0.75,
             "That number is theirs, for their films and their measurement\n"
             "geometry. It is quoted here as the motivation for mapping a\n"
             "strain- or composition-graded chip, not as a target.",
             ha="center", fontsize=8.0, color=MUTED, va="top", linespacing=1.55)
    fig.tight_layout(pad=1.8)
    fig.savefig(OUT / "strain_phase_diagram.png")
    plt.close(fig)


# ------------------------------------------ figure D: which domains are seen --
def figure_domain_visibility():
    """Why only an in-plane polar axis produces a first-order signal here.

    The argument is worked in docs/physics/01-electro-optics.md section 5.3.
    For a c domain the in-plane field drives only the shear rows of the 4mm
    tensor, and those do not alter the cross-section the light samples at
    normal incidence.
    """
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.8, 5.4),
                                  gridspec_kw={"width_ratios": [1.15, 1.0]})
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_xlim(0, 12); ax.set_ylim(0, 9.4)

    # substrate and film
    ax.add_patch(Rectangle((0.7, 1.05), 10.6, 1.35, facecolor="#e8eaf0",
                           edgecolor="#c3cad6", lw=1.2))
    ax.text(10.9, 1.72, "substrate", ha="right", va="center",
            fontsize=8.8, color=MUTED)

    # alternating a and c domains, filling the film layer
    widths = [1.9, 1.5, 2.1, 1.4, 1.9, 1.8]
    kinds = ["c", "a", "c", "a", "c", "a"]
    x = 0.7
    for w, kind in zip(widths, kinds):
        col = AMBER if kind == "c" else GREEN
        ax.add_patch(Rectangle((x, 2.40), w, 2.20, facecolor=col, alpha=0.13,
                               edgecolor=col, lw=1.1))
        cx = x + w / 2
        if kind == "c":
            ax.annotate("", xy=(cx, 4.25), xytext=(cx, 3.15),
                        arrowprops=dict(arrowstyle="-|>", color=col, lw=2.3))
        else:
            ax.annotate("", xy=(cx + 0.42, 3.70), xytext=(cx - 0.42, 3.70),
                        arrowprops=dict(arrowstyle="-|>", color=col, lw=2.3))
        ax.text(cx, 2.62, kind, ha="center", fontsize=10, color=col,
                fontweight="bold", style="italic")
        x += w
    ax.text(0.72, 2.16, "film: a mosaic of $a$ and $c$ domains",
            fontsize=8.8, color=INK, va="top")

    # electrodes sitting on the film surface
    for ex in (2.55, 6.10):
        ax.add_patch(Rectangle((ex, 4.60), 1.75, 0.40, facecolor="#f5b544",
                               edgecolor="#a16207", lw=1.1, zorder=4))
        ax.text(ex + 0.875, 4.80, "Au", ha="center", va="center",
                fontsize=7.6, color="#7c2d12", zorder=5)
    ax.annotate("", xy=(6.05, 3.95), xytext=(4.35, 3.95),
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=2.2), zorder=6)
    ax.text(4.55, 4.20, "$E$", ha="center", fontsize=11, color=LASER,
            fontweight="bold", zorder=6)
    ax.annotate("", xy=(6.05, 5.35), xytext=(4.30, 5.35),
                arrowprops=dict(arrowstyle="<|-|>", color=MUTED, lw=1.0))
    ax.text(5.18, 5.52, "$\\approx$7 $\\mu$m gap", ha="center", fontsize=8.2,
            color=MUTED)

    # the beam, down the centre of the gap
    for lw, al in [(13, 0.06), (7, 0.10), (3.2, 0.9)]:
        ax.plot([5.18, 5.18], [8.55, 0.95], color=LASER, lw=lw, alpha=al, zorder=2)
    ax.annotate("", xy=(5.18, 0.62), xytext=(5.18, 1.02),
                arrowprops=dict(arrowstyle="-|>", color=LASER, lw=2.0), zorder=3)
    ax.text(5.45, 8.30, "1550 nm beam, normal incidence", fontsize=8.6, color=LASER)

    ax.text(0.7, 0.30,
            "The beam samples whatever domains sit in the gap, and the measured\n"
            "response is their weighted sum. The $a$ domains carry it.",
            fontsize=8.5, color=MUTED, va="top", linespacing=1.5)
    title(ax, "A film is a mosaic, and the beam averages over it", None)

    # --- the visibility table ---
    ax2.axis("off"); ax2.set_xlim(0, 10); ax2.set_ylim(0, 10)
    ax2.text(0.0, 9.8, "What each domain contributes", fontsize=11.5,
             fontweight="bold", color=INK)
    rows = [
        ("$c$ domain", "$P\\parallel$ surface normal", AMBER,
         "An in-plane field drives only the $r_{42}$ shear rows,\n"
         "which tilt the indicatrix out of the plane. The\n"
         "cross-section the light samples is unchanged, so\n"
         "there is no first-order signal at normal incidence."),
        ("$a$ domain, $P\\parallel E$", "polar axis along the field", GREEN,
         "Drives $r_{13}$ and $r_{33}$, changes the diagonal\n"
         "components, and so modulates the retardance with\n"
         "the eigenaxes fixed. Peaks at 45$^\\circ$ to the field."),
        ("$a$ domain, $P\\perp E$", "polar axis across the field", CYAN,
         "Drives $r_{42}$ through Voigt component 5, which is\n"
         "off-diagonal within the cross-section, so it rotates\n"
         "the eigenaxes. Peaks along the field. This is the\n"
         "large coefficient."),
    ]
    y = 8.9
    for name, sub, col, body in rows:
        ax2.add_patch(FancyBboxPatch((0.0, y - 2.32), 9.9, 2.18,
                                     boxstyle="round,pad=0,rounding_size=0.14",
                                     facecolor="white", edgecolor=col, lw=1.3))
        ax2.add_patch(Rectangle((0.0, y - 2.32), 0.16, 2.18, facecolor=col,
                                edgecolor="none"))
        ax2.text(0.45, y - 0.55, name, fontsize=9.8, color=col, fontweight="bold")
        ax2.text(3.75, y - 0.55, sub, fontsize=8.3, color=MUTED)
        ax2.text(0.45, y - 0.95, body, fontsize=8.2, color=INK, va="top",
                 linespacing=1.55)
        y -= 2.55
    ax2.text(0.0, 0.85,
             "Poling aligns the $a$ domains so their contributions add rather\n"
             "than cancel. That is the whole reason the measurement holds a\n"
             "DC bias throughout a pixel.",
             fontsize=8.5, color=MUTED, va="top", linespacing=1.55)
    fig.tight_layout(pad=1.8)
    fig.savefig(OUT / "domain_visibility.png")
    plt.close(fig)


# ------------------------------ figure E: from susceptibility to coefficient --
def figure_eo_from_susceptibility():
    """The chain that connects the free-energy landscape to r42."""
    fig, ax = plt.subplots(figsize=(13.0, 4.2))
    ax.set_xlim(0, 100); ax.set_ylim(0, 30)
    ax.axis("off"); ax.set_aspect("auto")

    steps = [
        ("epitaxial strain\nand composition", "set by the substrate,\n"
         "the buffer layer and\nthe local stoichiometry", BLUE),
        ("energetic competition\nbetween polar variants", "how flat is the valley\n"
         "connecting $[001]$ and\n$[100]$?", CYAN),
        ("transverse\nsusceptibility $\\chi_{11}$", "a flat valley means $P$\n"
         "rotates almost freely,\nso $\\chi_{11}$ is large", VIOLET),
        ("shear coefficient\n$r_{42}$", "$r_{42}\\simeq 2g_{44}P_3"
         "\\varepsilon_0\\chi_{11}$,\nso it inherits $\\chi_{11}$ directly", LASER),
        ("what this bench\nmeasures", "a rotation of tens of\nmicroradians at most,\npixel by pixel", GREEN),
    ]
    w, gap = 16.0, 4.6
    x = 1.5
    for k, (head, body, col) in enumerate(steps):
        ax.add_patch(FancyBboxPatch((x, 7.0), w, 15.0,
                                    boxstyle="round,pad=0,rounding_size=0.8",
                                    facecolor="white", edgecolor=col, lw=1.6))
        ax.add_patch(Rectangle((x, 20.6), w, 1.4, facecolor=col,
                               edgecolor="none"))
        ax.text(x + w / 2, 18.4, head, ha="center", va="top", fontsize=9.6,
                color=col, fontweight="bold", linespacing=1.4)
        ax.text(x + w / 2, 13.6, body, ha="center", va="top", fontsize=8.2,
                color=INK, linespacing=1.6)
        if k < len(steps) - 1:
            ax.annotate("", xy=(x + w + gap - 0.7, 14.5), xytext=(x + w + 0.7, 14.5),
                        arrowprops=dict(arrowstyle="-|>", color=SLATE, lw=2.0))
        x += w + gap

    ax.text(1.5, 27.6, "From the substrate to a measured rotation",
            fontsize=13.5, fontweight="bold", color=INK)
    ax.text(1.5, 25.2,
            "The chain that makes a strain- or composition-graded chip worth "
            "mapping. Each link is a factor the film's own growth fixes, and the "
            "last one is what the instrument reads.",
            fontsize=8.8, color=MUTED, va="top")
    ax.text(1.5, 4.6,
            "The same chain read backwards is the warning: because $r_{42}$ "
            "inherits a susceptibility, it depends on strain, on temperature, on "
            "domain population and on poling history. A measured coefficient\n"
            "belongs to one pixel in one state, which is why this repository "
            "reports a normalised rotation as the comparable quantity and gates "
            "the absolute coefficient behind measured geometry.",
            fontsize=8.4, color=MUTED, va="top", linespacing=1.6)
    fig.tight_layout(pad=0.8)
    fig.savefig(OUT / "eo_from_susceptibility.png")
    plt.close(fig)


def main():
    apply_style()
    figure_perovskite()
    figure_polarisation_rotation()
    figure_strain_phase_diagram()
    figure_domain_visibility()
    figure_eo_from_susceptibility()
    for name in ("perovskite_structure", "polarisation_rotation",
                 "strain_phase_diagram", "domain_visibility",
                 "eo_from_susceptibility"):
        p = OUT / f"{name}.png"
        print(f"  wrote {p.name}  ({p.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
