#!/usr/bin/env python3
"""Chip-level hysteresis maps from per-pixel DC loop metrics (v2).

Scans a fast-map run directory for per-pixel hysteresis results
(``pockels_pixels/pixel_NNN/dc_hysteresis/**/dc_hysteresis.csv`` +
``dc_hysteresis_metrics.json``), computing missing/outdated metrics from the
raw CSVs on the fly, then writes:

- ``hysteresis_metrics_all_pixels.csv``  - one row per measured pair
- ``map_<metric>.png``                   - annotated 10x10 heatmaps of
      Vc+, Vc-, loop width, imprint (asymmetry about 0 V), remanent
      response S_rem+/-, switchable / frozen response, squareness,
      saturation asymmetry, loop area, leakage conductance, quadrature
      fraction - and coercive FIELDS (kV/cm) when ``--gap-um`` is given
- ``map_hysteresis_type.png``            - categorical loop-type map
      (square / slanted / rounded ferroelectric, pinched, linear,
      frozen, no-response, invalid) with counts
- ``map_loop_gallery.png``               - the actual normalized S(V)
      loop of every pair drawn at its chip position, border-colored by
      loop type - the chip's "hysteresis fingerprint" at a glance

Usage:
    python make_hysteresis_maps.py <run_dir> [--gap-um 7 --alpha 0.94]
    python make_hysteresis_maps.py            # latest pockels_fast_map run

Display convention matches make_peak_hwp_angle_map.py:
    display_row = (pixel-1)//10 + 1,  display_col = 10 - (pixel-1)%10
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from pockels_hysteresis_analysis import (
    TYPE_COLORS,
    TYPE_SHORT_CODES,
    analyse_and_save,
    load_dc_hysteresis_csv,
    signed_projection,
)

GRID = 10

# (csv column, metrics path, scale, unit label, colormap, symmetric-about-0)
MAP_SPECS = [
    ("v_c_plus_V", ("metrics", "v_c_plus_V"), 1.0, "V", "viridis", False),
    ("v_c_minus_V", ("metrics", "v_c_minus_V"), 1.0, "V", "viridis", False),
    ("loop_width_V", ("metrics", "loop_width_V"), 1.0, "V", "magma", False),
    ("imprint_V", ("metrics", "imprint_V"), 1.0, "V", "coolwarm", True),
    ("s_rem_pos_uV", ("metrics", "s_rem_pos_V"), 1e6, "uV", "magma", False),
    ("s_rem_neg_uV", ("metrics", "s_rem_neg_V"), 1e6, "uV", "magma_r", False),
    ("switchable_uV", ("metrics", "switchable_V"), 1e6, "uV", "magma", False),
    ("frozen_uV", ("metrics", "frozen_V"), 1e6, "uV", "coolwarm", True),
    ("squareness_pos", ("metrics", "squareness_pos"), 1.0, "", "viridis", False),
    ("sat_asymmetry", ("metrics", "sat_asymmetry"), 1.0, "", "coolwarm", True),
    ("loop_area_V2", ("metrics", "loop_area_V2"), 1e6, "uV*V", "magma", False),
    ("leakage_nS", ("metrics", "leakage", "conductance_S"), 1e9, "nS", "magma", False),
    ("quadrature_fraction", ("metrics", "quadrature_fraction"), 1.0, "", "viridis", False),
    # v2 compositional extractables
    ("quad_eo_ratio", ("metrics", "quad_eo_ratio"), 1.0, "", "viridis", False),
    ("switching_sigma_V", ("metrics", "switching_sigma_V"), 1.0, "V", "magma", False),
    ("switching_skewness", ("metrics", "switching_skewness"), 1.0, "", "coolwarm", True),
    ("nucleation_asymmetry", ("metrics", "nucleation_asymmetry"), 1.0, "", "coolwarm", True),
    ("butterfly_contrast", ("metrics", "butterfly_min_over_sat"), 1.0, "", "viridis", False),
    ("transition_width_V", ("metrics", "transition_width_25_75_V"), 1.0, "V", "magma", False),
]

FIELD_SPECS = [
    ("e_c_plus_kV_cm", ("fields", "e_c_plus_kV_per_cm"), 1.0, "kV/cm", "viridis", False),
    ("e_c_minus_kV_cm", ("fields", "e_c_minus_kV_per_cm"), 1.0, "kV/cm", "viridis", False),
    ("e_width_kV_cm", ("fields", "e_width_kV_per_cm"), 1.0, "kV/cm", "magma", False),
    ("e_imprint_kV_cm", ("fields", "e_imprint_kV_per_cm"), 1.0, "kV/cm", "coolwarm", True),
]

TYPE_ORDER = [
    "ferroelectric_square",
    "ferroelectric_slanted",
    "ferroelectric_rounded",
    "pinched",
    "linear_no_hysteresis",
    "frozen_response",
    "partial_loop_unresolved",
    "no_response",
    "invalid_projection",
    "unclassified",
]

# TYPE_COLORS is imported from pockels_hysteresis_analysis (shared with GUI).


def find_latest_run(root: Path) -> Path | None:
    base = root / "pockels_fast_map"
    if not base.is_dir():
        return None
    runs = sorted((d for d in base.iterdir() if d.is_dir()),
                  key=lambda d: d.name)
    return runs[-1] if runs else None


def dig(obj, path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def collect_pixel_metrics(run_dir: Path, heal: bool = True,
                          reanalyse: bool = False,
                          gap_um: float | None = None,
                          alpha: float = 1.0) -> list[dict]:
    out = []
    for pixel_dir in sorted(run_dir.glob("pockels_pixels/pixel_*")):
        m = re.search(r"pixel_(\d+)", pixel_dir.name)
        if not m:
            continue
        pix = int(m.group(1))
        hyst_root = pixel_dir / "dc_hysteresis"
        if not hyst_root.is_dir():
            continue
        csvs = [p for p in hyst_root.rglob("dc_hysteresis.csv")
                if "recon_coarse" not in p.parts]
        for csv_path in csvs:
            jp = csv_path.with_name("dc_hysteresis_metrics.json")
            need = reanalyse or not jp.exists()
            if not need:
                try:
                    with open(jp) as f:
                        existing = json.load(f)
                    if "classification" not in existing:
                        need = True
                    if gap_um is not None and "fields" not in existing:
                        need = True
                except Exception:
                    need = True
            if need and heal:
                try:
                    print(f"[analyse] pixel {pix:03d}: {csv_path}")
                    analyse_and_save(str(csv_path), gap_um=gap_um,
                                     field_correction=alpha)
                except Exception as exc:
                    print(f"[analyse] pixel {pix:03d}: failed: {exc}")
        metric_files = [p for p in hyst_root.rglob("dc_hysteresis_metrics.json")
                        if "recon_coarse" not in p.parts]
        if not metric_files:
            continue
        metric_files.sort(key=lambda p: p.stat().st_mtime)
        try:
            with open(metric_files[-1]) as f:
                result = json.load(f)
        except Exception as exc:
            print(f"[WARN] pixel {pix:03d}: unreadable metrics: {exc}")
            continue
        result["_pixel"] = pix
        result["_metrics_path"] = str(metric_files[-1])
        out.append(result)
    return out


def pixel_display(pix: int) -> tuple[int, int]:
    return ((pix - 1) // GRID + 1, GRID - (pix - 1) % GRID)


def display_to_pixel(dr: int, dc: int) -> int:
    return (dr - 1) * GRID + 11 - dc


def write_combined_csv(results: list[dict], out_csv: Path) -> None:
    fields = ["pixel", "display_row", "display_col",
              "hysteresis_type", "type_code", "modifiers",
              "n_cycles_analysed", "phi_ref_deg"]
    fields += [spec[0] for spec in MAP_SPECS]
    fields += [spec[0] for spec in FIELD_SPECS]
    fields += ["loop_closure_V", "n_points", "n_tripped_excluded",
               "metrics_path"]
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for res in results:
            pix = res["_pixel"]
            dr, dc = pixel_display(pix)
            cls = res.get("classification") or {}
            row = {
                "pixel": pix,
                "display_row": dr,
                "display_col": dc,
                "hysteresis_type": cls.get("primary", ""),
                "type_code": cls.get("short_code", ""),
                "modifiers": ";".join(cls.get("modifiers", [])),
                "n_cycles_analysed": res.get("n_cycles_analysed"),
                "phi_ref_deg": res.get("phi_ref_deg"),
                "loop_closure_V": dig(res, ("metrics", "loop_closure_V")),
                "n_points": dig(res, ("metrics", "n_points")),
                "n_tripped_excluded": dig(res, ("metrics", "n_tripped_excluded")),
                "metrics_path": res.get("_metrics_path"),
            }
            for name, path, scale, _unit, _cmap, _sym in MAP_SPECS + FIELD_SPECS:
                value = dig(res, path)
                row[name] = (value * scale
                             if isinstance(value, (int, float)) and value is not None
                             and math.isfinite(float(value)) else "")
            writer.writerow(row)
    print(f"[SAVED] {out_csv}")


def draw_map(results: list[dict], spec, out_png: Path, run_name: str) -> bool:
    name, path, scale, unit, cmap, symmetric = spec
    grid = np.full((GRID, GRID), np.nan)
    for res in results:
        value = dig(res, path)
        if value is None or not isinstance(value, (int, float)):
            continue
        value = float(value) * scale
        if not math.isfinite(value):
            continue
        dr, dc = pixel_display(res["_pixel"])
        grid[dr - 1, dc - 1] = value
    if not np.any(np.isfinite(grid)):
        return False

    fig, ax = plt.subplots(figsize=(7.6, 6.4))
    if symmetric:
        vmax = np.nanmax(np.abs(grid))
        vmin = -vmax
    else:
        vmin = np.nanmin(grid)
        vmax = np.nanmax(grid)
    masked = np.ma.masked_invalid(grid)
    cmap_obj = plt.get_cmap(cmap if cmap in plt.colormaps() else "viridis").copy()
    cmap_obj.set_bad("0.85")
    im = ax.imshow(masked, origin="lower", cmap=cmap_obj, vmin=vmin, vmax=vmax)
    for r in range(GRID):
        for c in range(GRID):
            v = grid[r, c]
            if math.isfinite(v):
                txt = f"{v:.2f}" if abs(v) < 100 else f"{v:.0f}"
                ax.text(c, r, txt, ha="center", va="center", fontsize=6.5,
                        color="white" if cmap != "coolwarm" else "black")
    ax.set_xticks(range(GRID), [str(GRID - i) for i in range(GRID)])
    ax.set_yticks(range(GRID), [str(r + 1) for r in range(GRID)])
    ax.set_xlabel("display column")
    ax.set_ylabel("display row")
    label = name.replace("_", " ")
    ax.set_title(f"{label}" + (f" ({unit})" if unit else "") + f"\n{run_name}")
    fig.colorbar(im, ax=ax, shrink=0.85)
    fig.tight_layout()
    fig.savefig(out_png, dpi=180)
    plt.close(fig)
    return True


def draw_type_map(results: list[dict], out_png: Path, run_name: str) -> bool:
    """Categorical loop-type map with per-type counts."""
    from matplotlib.colors import ListedColormap

    idx_grid = np.full((GRID, GRID), np.nan)
    counts: dict[str, int] = {}
    for res in results:
        cls = res.get("classification") or {}
        primary = cls.get("primary", "unclassified")
        if primary not in TYPE_ORDER:
            primary = "unclassified"
        counts[primary] = counts.get(primary, 0) + 1
        dr, dc = pixel_display(res["_pixel"])
        idx_grid[dr - 1, dc - 1] = TYPE_ORDER.index(primary)
    if not np.any(np.isfinite(idx_grid)):
        return False

    cmap = ListedColormap([TYPE_COLORS[t] for t in TYPE_ORDER])
    cmap.set_bad("0.92")
    fig, ax = plt.subplots(figsize=(9.0, 6.8))
    ax.imshow(np.ma.masked_invalid(idx_grid), origin="lower", cmap=cmap,
              vmin=-0.5, vmax=len(TYPE_ORDER) - 0.5)
    for res in results:
        cls = res.get("classification") or {}
        dr, dc = pixel_display(res["_pixel"])
        code = cls.get("short_code", "?")
        if cls.get("modifiers"):
            code += "*"
        ax.text(dc - 1, dr - 1, code, ha="center", va="center",
                fontsize=8, fontweight="bold")
    ax.set_xticks(range(GRID), [str(GRID - i) for i in range(GRID)])
    ax.set_yticks(range(GRID), [str(r + 1) for r in range(GRID)])
    ax.set_xlabel("display column")
    ax.set_ylabel("display row")
    ax.set_title(f"hysteresis loop type (\"*\" = has modifiers)\n{run_name}")
    handles = [
        Patch(facecolor=TYPE_COLORS[t],
              label=f"{TYPE_SHORT_CODES.get(t, '?')} {t.replace('_', ' ')}"
                    f"  ({counts.get(t, 0)})")
        for t in TYPE_ORDER if counts.get(t, 0) > 0
    ]
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.02, 0.5),
              fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(out_png, dpi=180)
    plt.close(fig)
    return True


def draw_loop_gallery(results: list[dict], out_png: Path, run_name: str) -> bool:
    """Every pair's normalized S(V) loop at its chip position."""
    by_pixel = {res["_pixel"]: res for res in results}
    fig, axes = plt.subplots(GRID, GRID, figsize=(15.5, 15.5))
    drew_any = False
    for dr in range(1, GRID + 1):
        for dc in range(1, GRID + 1):
            ax = axes[GRID - dr, dc - 1]
            ax.set_xticks([])
            ax.set_yticks([])
            pix = display_to_pixel(dr, dc)
            res = by_pixel.get(pix)
            if res is None:
                ax.set_facecolor("0.94")
                ax.text(0.5, 0.5, str(pix), transform=ax.transAxes,
                        ha="center", va="center", fontsize=7, color="0.75")
                continue
            try:
                data = load_dc_hysteresis_csv(res["source_csv"])
                S, _Q = signed_projection(data["X"], data["Y"],
                                          res["phi_ref_deg"])
                V = data["V"]
                base = np.array([str(b).rstrip("0123456789")
                                 for b in data["branch"]])
                cyc = data["cycle"]
                last_cycle = int(dig(res, ("metrics", "cycle")) or np.max(cyc))
                good = (data["tripped"] == 0) & np.isfinite(V) & np.isfinite(S)
                vmax = float(np.nanmax(np.abs(V[good]))) or 1.0
                smax = float(np.nanmax(np.abs(S[good]))) or 1.0
                for bname, color in (("down", "tab:red"), ("up", "tab:green")):
                    # earlier cycles faded behind the settled last cycle so
                    # wake-up is visible chip-wide at a glance
                    for cycle_num in sorted(set(int(c) for c in cyc[good])):
                        mask = good & (base == bname) & (cyc == cycle_num)
                        if not np.any(mask):
                            continue
                        alpha = 1.0 if cycle_num == last_cycle else 0.3
                        lw = 1.0 if cycle_num == last_cycle else 0.7
                        ax.plot(V[mask] / vmax, S[mask] / smax,
                                color=color, lw=lw, alpha=alpha)
                ax.axhline(0, color="0.6", lw=0.4)
                ax.axvline(0, color="0.6", lw=0.4)
                ax.set_xlim(-1.05, 1.05)
                ax.set_ylim(-1.15, 1.15)
                cls = res.get("classification") or {}
                edge = TYPE_COLORS.get(cls.get("primary", "unclassified"),
                                       "0.5")
                for spine in ax.spines.values():
                    spine.set_color(edge)
                    spine.set_linewidth(2.2)
                ax.text(0.04, 0.96, str(pix), transform=ax.transAxes,
                        ha="left", va="top", fontsize=7, fontweight="bold")
                ax.text(0.96, 0.96, cls.get("short_code", "?"),
                        transform=ax.transAxes, ha="right", va="top",
                        fontsize=7, color=edge, fontweight="bold")
                drew_any = True
            except Exception as exc:
                ax.set_facecolor("mistyrose")
                ax.text(0.5, 0.5, f"{pix}\nerr", transform=ax.transAxes,
                        ha="center", va="center", fontsize=6)
                print(f"[gallery] pixel {pix:03d}: {exc}")
    handles = [Patch(facecolor="none", edgecolor=TYPE_COLORS[t], linewidth=2,
                     label=f"{TYPE_SHORT_CODES.get(t, '?')} "
                           f"{t.replace('_', ' ')}")
               for t in TYPE_ORDER]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8,
               frameon=False, bbox_to_anchor=(0.5, 0.005))
    fig.suptitle(f"signed hysteresis loops S(V), normalized per pair "
                 f"(x: V/Vmax, y: S/|S|max)\n{run_name}", fontsize=13)
    fig.tight_layout(rect=(0, 0.03, 1, 0.96))
    fig.savefig(out_png, dpi=170)
    plt.close(fig)
    return drew_any


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", nargs="?", default=None,
                    help="Fast-map run directory (default: latest under "
                         "pockels_fast_map/)")
    ap.add_argument("--out", default=None,
                    help="Output directory [default <run_dir>/hysteresis_maps]")
    ap.add_argument("--gap-um", type=float, default=None,
                    help="Measured electrode gap (um) - adds coercive-FIELD "
                         "maps, E = alpha*V/gap.")
    ap.add_argument("--alpha", type=float, default=1.0,
                    help="FEM field-correction factor [default 1.0].")
    ap.add_argument("--reanalyse", action="store_true",
                    help="Force re-running the loop analysis on every raw CSV.")
    ap.add_argument("--no-heal", dest="heal", action="store_false", default=True,
                    help="Do not compute missing metrics from raw CSVs.")
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else find_latest_run(Path("."))
    if run_dir is None or not run_dir.is_dir():
        raise SystemExit("No run directory found - pass one explicitly.")
    out_dir = Path(args.out) if args.out else run_dir / "hysteresis_maps"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = collect_pixel_metrics(run_dir, heal=args.heal,
                                    reanalyse=args.reanalyse,
                                    gap_um=args.gap_um, alpha=args.alpha)
    if not results:
        raise SystemExit(f"No per-pixel hysteresis metrics found under {run_dir}")
    print(f"Collected loop metrics for {len(results)} pixel(s).")

    write_combined_csv(results, out_dir / "hysteresis_metrics_all_pixels.csv")
    specs = list(MAP_SPECS)
    if any("fields" in res for res in results):
        specs += FIELD_SPECS
    n_maps = 0
    for spec in specs:
        if draw_map(results, spec, out_dir / f"map_{spec[0]}.png", run_dir.name):
            n_maps += 1
    if draw_type_map(results, out_dir / "map_hysteresis_type.png", run_dir.name):
        n_maps += 1
    if draw_loop_gallery(results, out_dir / "map_loop_gallery.png", run_dir.name):
        n_maps += 1
    print(f"[SAVED] {n_maps} map(s) under {out_dir}")


if __name__ == "__main__":
    main()
