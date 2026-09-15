#!/usr/bin/env python3
"""Compositional analysis report across the chip (v2).

Joins every per-pixel result the campaign produces — hysteresis loop metrics
(incl. the quadratic-EO ratio, switching-distribution moments, butterfly
contrast, transition width), poling-kinetics fits from poling_kinetics.csv,
and EO-anisotropy from the per-pixel cos(4*HWP) fit — into one table, and
renders the cross-metric analytics used to resolve composition dependence:

- compositional_metrics_all_pixels.csv   - the mega-table
- corr_matrix.png                        - Spearman correlation of metrics
- merit_scatter.png                      - switchable response vs Vc+ (the
      EO-magnitude vs switching-cost trade-off), imprint vs leakage
- trend_<metric>.png                     - each headline metric vs
      composition (or pixel number), with Spearman rho and p-value
- cluster_map.png                        - k-means clustering of normalized
      loop-metric vectors -> objective compositional regions
- map_tau_pole_s.png / map_anisotropy.png

Composition join: pass --composition composition.csv with columns
``pixel,composition`` (extra columns ignored; a ``label`` column is carried
through). Without it, trends use the pixel number as the x-axis and say so.

Usage:
    python make_compositional_report.py <run_dir> [--composition composition.csv]
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    from scipy import stats as _scipy_stats
except Exception:  # pragma: no cover
    _scipy_stats = None

from make_hysteresis_maps import (
    collect_pixel_metrics,
    dig,
    draw_map,
    find_latest_run,
    pixel_display,
)
from pockels_hysteresis_analysis import (
    analyse_and_save,
    fit_poling_kinetics,
    load_poling_kinetics_csv,
)

# (column, metrics path, scale) pulled from each pixel's hysteresis metrics
HYST_COLUMNS = [
    ("hysteresis_type", ("classification", "primary"), None),
    ("v_c_plus_V", ("metrics", "v_c_plus_V"), 1.0),
    ("v_c_minus_V", ("metrics", "v_c_minus_V"), 1.0),
    ("loop_width_V", ("metrics", "loop_width_V"), 1.0),
    ("imprint_V", ("metrics", "imprint_V"), 1.0),
    ("switchable_uV", ("metrics", "switchable_corrected_V"), 1e6),
    ("frozen_uV", ("metrics", "frozen_V"), 1e6),
    ("s_rem_pos_uV", ("metrics", "s_rem_pos_V"), 1e6),
    ("squareness_pos", ("metrics", "squareness_pos"), 1.0),
    ("sat_asymmetry", ("metrics", "sat_asymmetry"), 1.0),
    ("quad_eo_ratio", ("metrics", "quad_eo_ratio"), 1.0),
    ("switching_sigma_V", ("metrics", "switching_sigma_V"), 1.0),
    ("switching_skewness", ("metrics", "switching_skewness"), 1.0),
    ("nucleation_asymmetry", ("metrics", "nucleation_asymmetry"), 1.0),
    ("butterfly_contrast", ("metrics", "butterfly_min_over_sat"), 1.0),
    ("transition_width_V", ("metrics", "transition_width_25_75_V"), 1.0),
    ("loop_area_uVV", ("metrics", "loop_area_V2"), 1e6),
    ("leakage_nS", ("metrics", "leakage", "conductance_S"), 1e9),
    ("quadrature_fraction", ("metrics", "quadrature_fraction"), 1.0),
]

TREND_METRICS = [
    "switchable_uV", "v_c_plus_V", "loop_width_V", "imprint_V",
    "quad_eo_ratio", "switching_sigma_V", "squareness_pos",
    "butterfly_contrast", "tau_pole_s", "anisotropy_ratio",
]

CLUSTER_FEATURES = [
    "v_c_plus_V", "v_c_minus_V", "loop_width_V", "imprint_V",
    "squareness_pos", "switchable_uV", "frozen_uV", "quad_eo_ratio",
    "switching_sigma_V", "butterfly_contrast",
]


def _num(value):
    if value is None:
        return float("nan")
    try:
        v = float(value)
        return v if math.isfinite(v) else float("nan")
    except (TypeError, ValueError):
        return float("nan")


def load_composition(path):
    comp, labels = {}, {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        cols = [c.strip().lower() for c in (reader.fieldnames or [])]
        if "pixel" not in cols:
            raise SystemExit(f"{path}: needs a 'pixel' column")
        value_col = None
        for cand in ("composition", "x", "value"):
            if cand in cols:
                value_col = cand
                break
        if value_col is None:
            value_col = next((c for c in cols if c not in ("pixel", "label")),
                             None)
        if value_col is None:
            raise SystemExit(f"{path}: no composition value column found")
        for row in reader:
            row = {k.strip().lower(): v for k, v in row.items() if k}
            try:
                pix = int(float(row["pixel"]))
            except (TypeError, ValueError):
                continue
            comp[pix] = _num(row.get(value_col))
            if row.get("label"):
                labels[pix] = str(row["label"])
    return comp, labels, value_col


def gather_rows(run_dir: Path, heal: bool, reanalyse: bool) -> list[dict]:
    results = collect_pixel_metrics(run_dir, heal=heal, reanalyse=reanalyse)
    # transparently upgrade metrics JSONs that predate the new extractables
    for res in results:
        if dig(res, ("metrics", "quad_eo_ratio")) is None \
                and dig(res, ("metrics", "quad_eo_slope_V_per_V")) is None \
                and heal:
            src = res.get("source_csv")
            if src and Path(src).exists():
                try:
                    print(f"[upgrade] pixel {res['_pixel']:03d}: re-analysing "
                          f"for new metrics")
                    fresh = analyse_and_save(src)
                    fresh["_pixel"] = res["_pixel"]
                    fresh["_metrics_path"] = res.get("_metrics_path")
                    res.clear()
                    res.update(fresh)
                except Exception as exc:
                    print(f"[upgrade] pixel {res.get('_pixel')}: failed: {exc}")

    rows = []
    for res in results:
        pix = res["_pixel"]
        dr, dc = pixel_display(pix)
        row = {"pixel": pix, "display_row": dr, "display_col": dc}
        for name, path, scale in HYST_COLUMNS:
            value = dig(res, path)
            if scale is None:
                row[name] = value if value is not None else ""
            else:
                row[name] = (_num(value) * scale
                             if isinstance(value, (int, float)) else float("nan"))
        rows.append(row)
    by_pixel = {r["pixel"]: r for r in rows}

    # poling kinetics (every fast-map pixel writes poling_kinetics.csv)
    for kin_path in sorted(run_dir.glob("pockels_pixels/pixel_*/poling_kinetics.csv")):
        try:
            pix = int(kin_path.parent.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        row = by_pixel.setdefault(pix, {
            "pixel": pix,
            **dict(zip(("display_row", "display_col"), pixel_display(pix))),
        })
        try:
            t_s, mag = load_poling_kinetics_csv(kin_path)
            fit = fit_poling_kinetics(t_s, mag)
            row["tau_pole_s"] = fit["tau_s"]
            row["poling_beta"] = fit["beta"]
            row["poling_fit_r2"] = fit["r_squared"]
        except Exception as exc:
            print(f"[kinetics] pixel {pix:03d}: {exc}")

    # EO anisotropy from the per-pixel cos(4*HWP) fit
    for summary_path in sorted(run_dir.glob("pockels_pixels/pixel_*/fast_map_summary.json")):
        try:
            pix = int(summary_path.parent.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except Exception:
            continue
        fit = summary.get("best_hwp_fit") or {}
        amp = _num(fit.get("amplitude_V"))
        off = _num(fit.get("offset_V"))
        row = by_pixel.setdefault(pix, {
            "pixel": pix,
            **dict(zip(("display_row", "display_col"), pixel_display(pix))),
        })
        if math.isfinite(amp) and math.isfinite(off) and off > 0:
            row["anisotropy_ratio"] = amp / off
        row["peak_hwp_deg"] = _num(fit.get("peak_hwp_deg"))
        row["hwp_fit_r2"] = _num(fit.get("r_squared"))
        row["best_mag_uV"] = _num(summary.get("max_lockin_mag_V")) * 1e6

    return sorted(by_pixel.values(), key=lambda r: r["pixel"])


def spearman(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 4:
        return float("nan"), float("nan")
    if _scipy_stats is not None:
        rho, p = _scipy_stats.spearmanr(x[ok], y[ok])
        return float(rho), float(p)
    rx = np.argsort(np.argsort(x[ok]))
    ry = np.argsort(np.argsort(y[ok]))
    rho = float(np.corrcoef(rx, ry)[0, 1])
    return rho, float("nan")


def kmeans(features, k, iters=100, seed=0):
    rng = np.random.default_rng(seed)
    centers = features[rng.choice(len(features), size=k, replace=False)]
    labels = np.zeros(len(features), int)
    for _ in range(iters):
        dist = ((features[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        labels = dist.argmin(1)
        new = np.array([
            features[labels == j].mean(0) if np.any(labels == j) else centers[j]
            for j in range(k)
        ])
        if np.allclose(new, centers):
            break
        centers = new
    return labels


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", nargs="?", default=None)
    ap.add_argument("--composition", default=None,
                    help="CSV with pixel,composition[,label] columns")
    ap.add_argument("--out", default=None)
    ap.add_argument("--clusters", type=int, default=0,
                    help="k for loop-shape clustering [0 = auto]")
    ap.add_argument("--reanalyse", action="store_true")
    ap.add_argument("--no-heal", dest="heal", action="store_false", default=True)
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else find_latest_run(Path("."))
    if run_dir is None or not run_dir.is_dir():
        raise SystemExit("No run directory found - pass one explicitly.")
    out_dir = Path(args.out) if args.out else run_dir / "compositional_report"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = gather_rows(run_dir, heal=args.heal, reanalyse=args.reanalyse)
    if not rows:
        raise SystemExit(f"No per-pixel results found under {run_dir}")

    comp_name = "pixel number"
    if args.composition:
        comp, labels, value_col = load_composition(args.composition)
        comp_name = f"composition ({value_col})"
        for row in rows:
            row["composition"] = comp.get(row["pixel"], float("nan"))
            if row["pixel"] in labels:
                row["composition_label"] = labels[row["pixel"]]
    else:
        for row in rows:
            row["composition"] = float(row["pixel"])
    print(f"Gathered {len(rows)} pixels; x-axis = {comp_name}")

    # ---- mega table -----------------------------------------------------
    all_fields = ["pixel", "display_row", "display_col", "composition",
                  "composition_label"]
    all_fields += [c for c, _p, _s in HYST_COLUMNS]
    all_fields += ["tau_pole_s", "poling_beta", "poling_fit_r2",
                   "anisotropy_ratio", "peak_hwp_deg", "hwp_fit_r2",
                   "best_mag_uV", "cluster"]
    csv_path = out_dir / "compositional_metrics_all_pixels.csv"

    # ---- clustering -----------------------------------------------------
    feat_rows = [r for r in rows
                 if sum(math.isfinite(_num(r.get(f))) for f in CLUSTER_FEATURES) >= 5]
    if len(feat_rows) >= 4:
        matrix = np.array([[_num(r.get(f)) for f in CLUSTER_FEATURES]
                           for r in feat_rows])
        col_mean = np.nanmean(matrix, axis=0)
        idx = np.where(np.isnan(matrix))
        matrix[idx] = np.take(col_mean, idx[1])
        col_std = matrix.std(0)
        col_std[col_std == 0] = 1.0
        matrix = (matrix - matrix.mean(0)) / col_std
        k = args.clusters or min(4, max(2, len(feat_rows) // 4))
        labels = kmeans(matrix, k)
        for r, lab in zip(feat_rows, labels):
            r["cluster"] = int(lab)
        pseudo = [{"_pixel": r["pixel"], "extra": {"cluster": float(r["cluster"])}}
                  for r in feat_rows]
        draw_map(pseudo, ("cluster", ("extra", "cluster"), 1.0, "", "viridis", False),
                 out_dir / "cluster_map.png", run_dir.name)
        print(f"[SAVED] cluster_map.png (k={k})")

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"[SAVED] {csv_path}")

    # ---- extra chip maps ------------------------------------------------
    for name, unit, cmap in (("tau_pole_s", "s", "magma"),
                             ("anisotropy_ratio", "", "viridis")):
        pseudo = [{"_pixel": r["pixel"], "extra": {name: _num(r.get(name))}}
                  for r in rows if math.isfinite(_num(r.get(name)))]
        if pseudo:
            draw_map(pseudo, (name, ("extra", name), 1.0, unit, cmap, False),
                     out_dir / f"map_{name}.png", run_dir.name)

    # ---- correlation matrix ---------------------------------------------
    numeric = [c for c, _p, s in HYST_COLUMNS if s is not None]
    numeric += ["tau_pole_s", "anisotropy_ratio", "best_mag_uV", "composition"]
    numeric = [c for c in numeric
               if sum(math.isfinite(_num(r.get(c))) for r in rows) >= 4]
    n = len(numeric)
    corr = np.full((n, n), np.nan)
    for i in range(n):
        for j in range(n):
            corr[i, j], _ = spearman([_num(r.get(numeric[i])) for r in rows],
                                     [_num(r.get(numeric[j])) for r in rows])
    fig, ax = plt.subplots(figsize=(0.55 * n + 3, 0.55 * n + 2.4))
    im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1)
    ax.set_xticks(range(n), numeric, rotation=90, fontsize=7)
    ax.set_yticks(range(n), numeric, fontsize=7)
    for i in range(n):
        for j in range(n):
            if math.isfinite(corr[i, j]):
                ax.text(j, i, f"{corr[i, j]:+.2f}", ha="center", va="center",
                        fontsize=5.5)
    ax.set_title(f"Spearman correlation - {run_dir.name}")
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(out_dir / "corr_matrix.png", dpi=180)
    plt.close(fig)
    print("[SAVED] corr_matrix.png")

    # ---- merit + imprint/leakage scatters -------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    comp_vals = np.array([_num(r.get("composition")) for r in rows])
    sc = ax1.scatter([_num(r.get("v_c_plus_V")) for r in rows],
                     [_num(r.get("switchable_uV")) for r in rows],
                     c=comp_vals, cmap="viridis", s=42)
    for r in rows:
        if math.isfinite(_num(r.get("v_c_plus_V"))):
            ax1.annotate(str(r["pixel"]), (r["v_c_plus_V"], r["switchable_uV"]),
                         fontsize=6, xytext=(3, 3), textcoords="offset points")
    ax1.set_xlabel("Vc+ (V)  [switching cost]")
    ax1.set_ylabel("switchable response (uV)  [EO magnitude]")
    ax1.set_title("merit map: response vs coercivity")
    fig.colorbar(sc, ax=ax1, label=comp_name)
    ax2.scatter([_num(r.get("leakage_nS")) for r in rows],
                [_num(r.get("imprint_V")) for r in rows],
                c=comp_vals, cmap="viridis", s=42)
    ax2.set_xlabel("leakage (nS)")
    ax2.set_ylabel("imprint (V)")
    ax2.set_title("imprint vs leakage (defect signatures)")
    for ax in (ax1, ax2):
        ax.grid(True, alpha=0.4)
    fig.suptitle(run_dir.name)
    fig.tight_layout()
    fig.savefig(out_dir / "merit_scatter.png", dpi=180)
    plt.close(fig)
    print("[SAVED] merit_scatter.png")

    # ---- trends vs composition ------------------------------------------
    n_saved = 0
    for metric in TREND_METRICS:
        y = [_num(r.get(metric)) for r in rows]
        x = [_num(r.get("composition")) for r in rows]
        ok = [i for i in range(len(x))
              if math.isfinite(x[i]) and math.isfinite(y[i])]
        if len(ok) < 4:
            continue
        rho, p = spearman([x[i] for i in ok], [y[i] for i in ok])
        fig, ax = plt.subplots(figsize=(6.4, 4.6))
        ax.scatter([x[i] for i in ok], [y[i] for i in ok], s=38)
        for i in ok:
            ax.annotate(str(rows[i]["pixel"]), (x[i], y[i]), fontsize=6,
                        xytext=(3, 3), textcoords="offset points")
        p_txt = f", p={p:.3g}" if math.isfinite(p) else ""
        ax.set_xlabel(comp_name)
        ax.set_ylabel(metric)
        ax.set_title(f"{metric} vs {comp_name}\nSpearman rho={rho:+.2f}{p_txt}"
                     f"  (n={len(ok)})")
        ax.grid(True, alpha=0.4)
        fig.tight_layout()
        fig.savefig(out_dir / f"trend_{metric}.png", dpi=170)
        plt.close(fig)
        n_saved += 1
    print(f"[SAVED] {n_saved} trend plot(s)")
    print(f"Report complete under {out_dir}")


if __name__ == "__main__":
    main()
