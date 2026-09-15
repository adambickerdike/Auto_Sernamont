"""Hysteresis figures built from real measured data.

These are not synthetic. Every point comes from a `dc_hysteresis.csv` acquired
on the bench, and the signed projection, the metrics and the classification are
computed by calling the production analysis module itself
(`pockels/pockels_hysteresis_analysis.py`), so the figures show exactly what
the pipeline reports.

The eight curated CSVs live in `assets/data/`. They are small and are committed
deliberately so these figures can be regenerated from the repository alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "pockels"))

from _figstyle import (apply_style, INK, MUTED, GRID, LASER, BLUE, VIOLET,
                       GREEN, AMBER, CYAN, PINK, SLATE, title)
import pockels_hysteresis_analysis as hyst

DATA = ROOT / "assets" / "data"
OUT = ROOT / "assets" / "figures"
OUT.mkdir(parents=True, exist_ok=True)


def load(label: str):
    """Load one curated CSV and run the production analysis over it."""
    matches = sorted(DATA.glob(f"{label}__*.csv"))
    if not matches:
        raise FileNotFoundError(f"no curated CSV for {label!r} in {DATA}")
    path = matches[0]
    data = hyst.load_dc_hysteresis_csv(str(path))
    result = hyst.analyse_loop_file(str(path))
    phi = float(result["phi_ref_deg"])
    S, Q = hyst.signed_projection(data["X"], data["Y"], phi)
    good = (data["tripped"] == 0)
    return dict(path=path, data=data, result=result, phi=phi, S=S, Q=Q, good=good,
                V=data["V"], R=data["R"], branch=data["branch"],
                metrics=result.get("metrics") or {},
                classification=result.get("classification") or {})


def branch_masks(rec):
    """Split into descending and ascending, the way the analysis does."""
    b = np.array([str(x) for x in rec["branch"]])
    down = np.array([s.startswith("down") or s == "sat" for s in b])
    up = np.array([s.startswith("up") for s in b])
    virgin = np.array([s.startswith("virgin") for s in b])
    return down & rec["good"], up & rec["good"], virgin & rec["good"]


def _m(rec, key, scale=1.0):
    v = rec["metrics"].get(key)
    return float(v) * scale if isinstance(v, (int, float)) and np.isfinite(v) else float("nan")


# ------------------------------------------- figure: butterfly and S loop ---
def figure_measured_loop(label="ferroelectric_rounded_strong"):
    rec = load(label)
    dn, up, vg = branch_masks(rec)
    V, R, S = rec["V"], rec["R"] * 1e6, rec["S"] * 1e6
    vmax = float(np.nanmax(np.abs(V)))

    run = rec["path"].name.split("__")[1].replace(".csv", "")
    cls = rec["classification"].get("primary", "?")
    mods = rec["classification"].get("modifiers") or []

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(12.6, 5.0))

    for mask, col, lab in ((dn, LASER, "descending branch"), (up, GREEN, "ascending branch"),
                           (vg, VIOLET, "virgin branch")):
        if mask.any():
            order = np.argsort(np.arange(len(V))[mask])
            ax.plot(V[mask][order], R[mask][order], "-o", color=col, ms=4.4, lw=1.7, label=lab)
    for vc, col in ((_m(rec, "v_c_minus_V"), LASER), (_m(rec, "v_c_plus_V"), GREEN)):
        if np.isfinite(vc):
            ax.axvline(vc, color=col, lw=0.9, ls=(0, (2, 3)))
    ax.set_xlabel("DC bias,  $V_\\mathrm{dc}$  (V)")
    ax.set_ylabel("lock-in magnitude  $|R|$  ($\\mu$V)")
    ax.set_xlim(-vmax * 1.08, vmax * 1.08)
    ax.legend(loc="upper center", ncols=2, bbox_to_anchor=(0.5, 0.99))
    ax.annotate("the magnitude collapses towards zero\nwhere the phasor reverses, close to\n"
                "the coercive voltages",
                xy=(_m(rec, "v_c_minus_V"), float(np.nanmin(R[dn])) if dn.any() else 0.0),
                xytext=(vmax * 0.06, float(np.nanmax(R)) * 0.30),
                fontsize=8.4, color=MUTED, linespacing=1.5, ha="left",
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.0,
                                connectionstyle="arc3,rad=0.28"))
    vlimit = rec["result"]["meta"].get("DC_HYST_VMAX", "?")
    title(ax, "Measured lock-in magnitude: the butterfly",
          f"real data, {run}, {len(V)} acquired points, "
          f"$\\pm${vlimit} V trajectory")
    ax.text(0.5, -0.215,
            "Acquired July 2026 on a $\\pm$50 V trajectory, before the "
            "$\\pm$40 V ceiling became the production limit.",
            transform=ax.transAxes, ha="center", va="top", fontsize=7.8, color=MUTED)

    ax2.axhline(0, color=GRID, lw=1.0)
    ax2.axvline(0, color=GRID, lw=1.0)
    for mask, col, lab in ((dn, LASER, "descending branch"), (up, GREEN, "ascending branch"),
                           (vg, VIOLET, "virgin branch")):
        if mask.any():
            order = np.argsort(np.arange(len(V))[mask])
            ax2.plot(V[mask][order], S[mask][order], "-o", color=col, ms=4.4, lw=1.7, label=lab)
    for vc, col, lab in ((_m(rec, "v_c_minus_V"), LASER, "$V_c^-$"),
                         (_m(rec, "v_c_plus_V"), GREEN, "$V_c^+$")):
        if np.isfinite(vc):
            ax2.scatter([vc], [0], color=col, s=62, zorder=7, edgecolors="white", linewidths=1.2)
            ax2.text(vc, float(np.nanmax(np.abs(S))) * 0.14, lab, ha="center",
                     fontsize=11, color=col, fontweight="bold")
    ax2.set_xlabel("DC bias,  $V_\\mathrm{dc}$  (V)")
    ax2.set_ylabel("signed response  $S(V)$  ($\\mu$V)")
    ax2.set_xlim(-vmax * 1.08, vmax * 1.08)
    ax2.legend(loc="lower right")

    stats = (f"classification   {cls}" + (f"  ({', '.join(mods)})" if mods else "") + "\n"
             f"$V_c^+$              {_m(rec,'v_c_plus_V'):+7.2f} V\n"
             f"$V_c^-$              {_m(rec,'v_c_minus_V'):+7.2f} V\n"
             f"loop width       {_m(rec,'loop_width_V'):7.2f} V\n"
             f"imprint          {_m(rec,'imprint_V'):+7.2f} V\n"
             f"switchable       {_m(rec,'switchable_V',1e6):7.2f} $\\mu$V\n"
             f"squareness$^+$       {_m(rec,'squareness_pos'):7.2f}\n"
             f"quadrature frac. {_m(rec,'quadrature_fraction'):7.2f}")
    ax2.text(0.025, 0.975, stats, transform=ax2.transAxes, ha="left", va="top",
             fontsize=7.6, color=INK, family="monospace", linespacing=1.6,
             bbox=dict(boxstyle="round,pad=0.5", facecolor="#f7f8fa",
                       edgecolor="#dfe3ea", linewidth=0.8))
    title(ax2, "The same data projected onto the saturation axis",
          f"$S = X\\cos\\phi_\\mathrm{{ref}} + Y\\sin\\phi_\\mathrm{{ref}}$ "
          f"with $\\phi_\\mathrm{{ref}}$ = {rec['phi']:.1f}$\\degree$, "
          "computed by the production analysis module")
    fig.tight_layout(pad=1.8)
    fig.savefig(OUT / "hysteresis_measured.png")
    plt.close(fig)
    return rec


# ------------------------------------------------ figure: the real gallery --
def figure_measured_gallery():
    picks = [
        ("ferroelectric_rounded_strong", "the strongest loop on this chip"),
        ("ferroelectric_slanted", "broader switching-field distribution"),
        ("pinched", "constricted: defect pinning or internal-bias pairs"),
        ("imprinted", "shifted off zero by a built-in bias field"),
        ("linear_no_hysteresis", "reversible, no switching in this range"),
        ("no_response", "no measurable electro-optic response"),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(12.8, 7.4))
    for ax, (label, note) in zip(axes.ravel(), picks):
        rec = load(label)
        dn, up, vg = branch_masks(rec)
        V, S = rec["V"], rec["S"] * 1e6
        vmax = float(np.nanmax(np.abs(V)))
        span = float(np.nanmax(np.abs(S))) or 1.0
        ax.axhline(0, color=GRID, lw=0.9); ax.axvline(0, color=GRID, lw=0.9)
        for mask, col in ((dn, LASER), (up, GREEN), (vg, VIOLET)):
            if mask.any():
                order = np.argsort(np.arange(len(V))[mask])
                ax.plot(V[mask][order], S[mask][order], "-o", color=col, ms=3.2, lw=1.5)
        ax.set_xlim(-vmax * 1.08, vmax * 1.08)
        ax.set_ylim(-span * 1.55, span * 1.55)
        ax.set_xlabel("$V_\\mathrm{dc}$ (V)", fontsize=8.6, labelpad=2)
        ax.set_ylabel("$S$ ($\\mu$V)", fontsize=8.6, labelpad=2)
        cls = rec["classification"].get("primary", "?")
        mods = rec["classification"].get("modifiers") or []
        shown = mods[:2]
        mod_text = ("*" + ", ".join(shown)
                    + (f" +{len(mods) - len(shown)}" if len(mods) > len(shown) else "")
                    ) if mods else ""
        run = rec["path"].name.split("__")[1].replace(".csv", "")
        ax.text(0.5, 1.255, cls, transform=ax.transAxes, ha="center", fontsize=9.4,
                color=INK, fontweight="bold", family="monospace")
        ax.text(0.5, 1.145, mod_text, transform=ax.transAxes, ha="center", fontsize=7.6,
                color=PINK, family="monospace")
        ax.text(0.5, 1.045, run.replace("_", " "), transform=ax.transAxes, ha="center",
                fontsize=7.4, color=MUTED, family="monospace")
        ax.text(0.5, -0.33, note, transform=ax.transAxes, ha="center", va="top",
                fontsize=8.3, color=MUTED, linespacing=1.45)
        sw = _m(rec, "switchable_V", 1e6)
        w = _m(rec, "loop_width_V")
        imp = _m(rec, "imprint_V")
        ax.text(0.035, 0.965,
                f"switchable {sw:.1f} $\\mu$V\nwidth {w:.1f} V\nimprint {imp:+.1f} V",
                transform=ax.transAxes, ha="left", va="top", fontsize=7.2,
                color=MUTED, family="monospace", linespacing=1.5,
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                          edgecolor="#e4e7ec", linewidth=0.7, alpha=0.9))
    fig.suptitle("Measured loops, classified by the production analysis",
                 x=0.045, ha="left", fontsize=13.5, fontweight="bold", y=0.985)
    fig.text(0.045, 0.918,
             "Every panel is real acquired data, projected and classified by "
             "pockels/pockels_hysteresis_analysis.py. Red is the descending branch, "
             "green the ascending.\nThe source CSVs are committed in assets/data/ so "
             "these panels can be regenerated from the repository alone.",
             ha="left", va="top", fontsize=8.6, color=MUTED, linespacing=1.55)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.775, bottom=0.10,
                        hspace=0.92, wspace=0.32)
    fig.savefig(OUT / "hysteresis_gallery_measured.png")
    plt.close(fig)


def main():
    apply_style()
    rec = figure_measured_loop()
    figure_measured_gallery()
    print(f"  source: {rec['path'].name}")
    print(f"  class:  {rec['classification'].get('primary')} "
          f"{rec['classification'].get('modifiers')}")
    print(f"  Vc+ {_m(rec,'v_c_plus_V'):+.2f} V   Vc- {_m(rec,'v_c_minus_V'):+.2f} V   "
          f"width {_m(rec,'loop_width_V'):.2f} V   switchable {_m(rec,'switchable_V',1e6):.1f} uV")
    for name in ("hysteresis_measured", "hysteresis_gallery_measured"):
        p = OUT / f"{name}.png"
        print(f"  wrote {p.name} ({p.stat().st_size/1024:.0f} kB)")


if __name__ == "__main__":
    main()
