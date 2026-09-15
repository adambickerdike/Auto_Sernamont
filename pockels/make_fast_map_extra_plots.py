#!/usr/bin/env python3
"""Create additional report figures from a Pockels fast-map run.

Usage:
    python make_fast_map_extra_plots.py pockels_fast_map/fast_map_20260625_132718

The script only uses files already produced by the fast-map sweep. It writes
figures into <run_dir>/extra_fast_map_plots and avoids using placeholder-filled
non-measured pixels for distributions or statistics.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path


def _set_mplconfig() -> None:
    if len(sys.argv) > 1:
        candidate = Path(sys.argv[1]).expanduser()
        target = candidate if candidate.is_dir() else candidate.parent
    else:
        target = Path.cwd()
    os.environ.setdefault("MPLCONFIGDIR", str(target / ".mplconfig"))


_set_mplconfig()

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm, TwoSlopeNorm
import numpy as np
import pandas as pd


PLOT_DIR_NAME = "extra_fast_map_plots"
MIN_POSITIVE = 1e-30


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def first_col(df: pd.DataFrame, names: list[str]) -> pd.Series:
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([np.nan] * len(df), index=df.index)


def first_valid_numeric(df: pd.DataFrame, names: list[str]) -> pd.Series:
    cols = [numeric(df[name]) for name in names if name in df.columns]
    if not cols:
        return pd.Series([np.nan] * len(df), index=df.index)
    return pd.concat(cols, axis=1).bfill(axis=1).iloc[:, 0]


def safe_median(values: pd.Series) -> float:
    values = numeric(values).dropna()
    return float(values.median()) if not values.empty else float("nan")


def prepare_grid(run_dir: Path, summary: pd.DataFrame) -> pd.DataFrame:
    grid = read_csv(run_dir / "effective_pockels_coefficient_heatmap_grid.csv")
    if grid.empty:
        grid = read_csv(run_dir / "best_pockels_angle_map.csv")
        rename = {
            "best_lockin_mag_uV": "lockin_mag_uV_used",
            "best_r_eff_pm_per_V": "r_eff_pm_per_V",
            "best_hwp_deg": "hwp_deg_used",
            "best_theta_i_deg": "theta_i_deg_used",
            "slope_side": "slope_side_used",
            "vpp": "vpp_used",
            "detector_V": "detector_V_used",
        }
        grid = grid.rename(columns=rename)

    if grid.empty and not summary.empty:
        grid = summary.copy()
        grid["display_row"] = numeric(grid.get("row", pd.Series(index=grid.index))) + 1
        grid["display_col"] = numeric(grid.get("col", pd.Series(index=grid.index))) + 1
        grid["lockin_mag_uV_used"] = numeric(first_col(grid, ["max_lockin_mag_V"])) * 1e6
        grid["hwp_deg_used"] = numeric(first_col(grid, ["best_mag_hwp_deg", "best_hwp_deg"]))
        grid["theta_i_deg_used"] = numeric(first_col(grid, ["best_mag_theta_i_deg", "best_theta_i_deg"]))
        grid["measured"] = grid.get("status", "").astype(str).str.lower().eq("complete").astype(int)

    if grid.empty:
        return grid

    for col in (
        "display_row",
        "display_col",
        "pixel",
        "measured",
        "r_eff_pm_per_V",
        "lockin_mag_uV_used",
        "detector_V_used",
        "vpp_used",
        "hwp_deg_used",
        "theta_i_deg_used",
        "n_lockin_rows",
    ):
        if col in grid.columns:
            grid[col] = numeric(grid[col])

    if "measured" not in grid.columns:
        if "status" in grid.columns:
            grid["measured"] = grid["status"].astype(str).str.lower().eq("complete").astype(int)
        else:
            grid["measured"] = 1

    return grid


def load_fast_rows(run_dir: Path) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for path in sorted((run_dir / "pockels_pixels").glob("pixel_*/fast_map.csv")):
        df = read_csv(path)
        if df.empty:
            continue
        match = re.search(r"pixel_(\d+)", str(path.parent))
        if match and "pixel" not in df.columns:
            df["pixel"] = int(match.group(1))
        df["source_csv"] = str(path)
        rows.append(df)
    if not rows:
        return pd.DataFrame()

    data = pd.concat(rows, ignore_index=True, sort=False)
    for col in (
        "pixel",
        "row",
        "col",
        "hwp_deg",
        "theta_i_deg",
        "theta_i_legacy_deg",
        "p_null_W",
        "p_null_mV",
        "power_W",
        "detector_V",
        "lockin_mag_V",
        "lockin_net_mag_V",
        "lockin_signed_V",
        "lockin_signed_std_V",
        "lockin_mag_std_V",
        "lockin_phase_deg",
        "lockin_phase_std_deg",
        "snr_estimate",
        "phase_flip_score",
        "side_symmetry_ratio",
        "vpp",
        "n_good_samples",
        "elapsed_s",
    ):
        if col in data.columns:
            data[col] = numeric(data[col])
    data["lockin_mag_uV"] = numeric(first_col(data, ["lockin_mag_V"])) * 1e6
    data["lockin_net_mag_uV"] = numeric(first_col(data, ["lockin_net_mag_V"])) * 1e6
    data["lockin_signed_uV"] = numeric(first_col(data, ["lockin_signed_V"])) * 1e6
    data["lockin_mag_std_uV"] = numeric(first_col(data, ["lockin_mag_std_V"])) * 1e6
    data["lockin_signed_std_uV"] = numeric(first_col(data, ["lockin_signed_std_V"])) * 1e6
    return data


def best_fast_rows(fast_rows: pd.DataFrame) -> pd.DataFrame:
    if fast_rows.empty or "pixel" not in fast_rows.columns:
        return pd.DataFrame()
    work = fast_rows.copy()
    metric = first_valid_numeric(work, ["lockin_net_mag_uV", "lockin_mag_uV"]).abs()
    work["_best_metric"] = metric
    work = work[work["_best_metric"].notna()]
    if work.empty:
        return pd.DataFrame()
    idx = work.groupby("pixel", sort=False)["_best_metric"].idxmax()
    return work.loc[idx].copy().drop(columns=["_best_metric"])


def map_display_positions(df: pd.DataFrame, grid: pd.DataFrame) -> pd.DataFrame:
    if df.empty or grid.empty or "pixel" not in df.columns or "pixel" not in grid.columns:
        return df
    cols = [c for c in ["pixel", "display_row", "display_col"] if c in grid.columns]
    pos = grid[cols].dropna(subset=["pixel"]).drop_duplicates("pixel")
    merged = df.merge(pos, on="pixel", how="left", suffixes=("", "_grid"))
    return merged


def measured_grid(grid: pd.DataFrame) -> pd.DataFrame:
    if grid.empty:
        return grid
    work = grid.copy()
    if "measured" in work.columns:
        work = work[numeric(work["measured"]).fillna(0).astype(int).eq(1)]
    elif "status" in work.columns:
        work = work[work["status"].astype(str).str.lower().eq("complete")]
    return work


def grid_array(grid: pd.DataFrame, value_col: str) -> np.ndarray:
    if grid.empty or value_col not in grid.columns:
        return np.empty((0, 0))
    work = grid.dropna(subset=["display_row", "display_col"]).copy()
    if work.empty:
        return np.empty((0, 0))
    rows = int(numeric(work["display_row"]).max())
    cols = int(numeric(work["display_col"]).max())
    arr = np.full((rows, cols), np.nan)
    for _, row in work.iterrows():
        try:
            r = int(row["display_row"]) - 1
            c = int(row["display_col"]) - 1
            arr[r, c] = float(row[value_col])
        except Exception:
            continue
    return arr


def with_bad_color(cmap_name: str, bad: str = "#eeeeee"):
    cmap = plt.get_cmap(cmap_name).copy()
    cmap.set_bad(bad)
    return cmap


def plot_heatmap(
    ax: plt.Axes,
    grid: pd.DataFrame,
    value_col: str,
    title: str,
    label: str,
    cmap: str = "viridis",
    *,
    log: bool = False,
    diverging: bool = False,
) -> None:
    arr = grid_array(grid, value_col)
    if arr.size == 0:
        ax.text(0.5, 0.5, "No data", ha="center", va="center")
        ax.set_axis_off()
        return

    masked = np.ma.masked_invalid(arr)
    kwargs = {"cmap": with_bad_color(cmap)}
    finite = arr[np.isfinite(arr)]
    if log and finite.size and np.nanmax(finite) > 0:
        positive = finite[finite > 0]
        if positive.size:
            kwargs["norm"] = LogNorm(vmin=max(float(np.nanmin(positive)), MIN_POSITIVE), vmax=float(np.nanmax(positive)))
    elif diverging and finite.size:
        bound = float(np.nanmax(np.abs(finite)))
        if bound > 0:
            kwargs["norm"] = TwoSlopeNorm(vcenter=0.0, vmin=-bound, vmax=bound)

    im = ax.imshow(masked, origin="upper", aspect="equal", **kwargs)
    ax.set_title(title)
    ax.set_xlabel("Display column")
    ax.set_ylabel("Display row")
    ax.set_xticks(np.arange(arr.shape[1]))
    ax.set_xticklabels(np.arange(1, arr.shape[1] + 1))
    ax.set_yticks(np.arange(arr.shape[0]))
    ax.set_yticklabels(np.arange(1, arr.shape[0] + 1))
    cbar = ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(label)


def savefig(fig: plt.Figure, path: Path) -> None:
    fig.tight_layout()
    fig.savefig(path, dpi=220)
    fig.savefig(path.with_suffix(".pdf"))
    plt.close(fig)


def finite_values(df: pd.DataFrame, col: str) -> pd.Series:
    if df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return numeric(df[col]).replace([np.inf, -np.inf], np.nan).dropna()


def maybe_log_x(ax: plt.Axes, values: pd.Series) -> None:
    positive = values[values > 0]
    if len(positive) > 3 and positive.max() / max(positive.min(), MIN_POSITIVE) > 30:
        ax.set_xscale("log")


def maybe_log_y(ax: plt.Axes, values: pd.Series) -> None:
    positive = values[values > 0]
    if len(positive) > 3 and positive.max() / max(positive.min(), MIN_POSITIVE) > 30:
        ax.set_yscale("log")


def plot_response_distributions(out_dir: Path, grid_measured: pd.DataFrame, summary: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))

    lockin = finite_values(grid_measured, "lockin_mag_uV_used")
    ax = axes[0, 0]
    if not lockin.empty:
        bins = np.geomspace(lockin[lockin > 0].min(), lockin.max(), 26) if (lockin > 0).all() else 25
        ax.hist(lockin, bins=bins, color="#4062bb", alpha=0.85)
        maybe_log_x(ax, lockin)
    ax.set_title("Best lock-in magnitude distribution")
    ax.set_xlabel("Lock-in magnitude (uV)")
    ax.set_ylabel("Pixels")

    r_eff = finite_values(grid_measured, "r_eff_pm_per_V")
    ax = axes[0, 1]
    if not r_eff.empty:
        bins = np.geomspace(r_eff[r_eff > 0].min(), r_eff.max(), 26) if (r_eff > 0).all() else 25
        ax.hist(r_eff, bins=bins, color="#7b2cbf", alpha=0.85)
        maybe_log_x(ax, r_eff)
    ax.set_title("Effective coefficient distribution")
    ax.set_xlabel("r_eff (pm/V)")
    ax.set_ylabel("Pixels")

    signed_abs = pd.Series(dtype=float)
    if not summary.empty and "max_abs_signed_response_V" in summary.columns:
        signed_abs = numeric(summary["max_abs_signed_response_V"]) * 1e6
        if "status" in summary.columns:
            signed_abs = signed_abs[summary["status"].astype(str).str.lower().eq("complete")]
        signed_abs = signed_abs.dropna()
    ax = axes[1, 0]
    if not signed_abs.empty:
        ax.hist(signed_abs, bins=25, color="#0f766e", alpha=0.85)
        maybe_log_x(ax, signed_abs)
    ax.set_title("Absolute signed response distribution")
    ax.set_xlabel("|signed response| (uV)")
    ax.set_ylabel("Pixels")

    ax = axes[1, 1]
    if not lockin.empty:
        ordered = np.sort(lockin.to_numpy())
        y = np.arange(1, len(ordered) + 1) / len(ordered)
        ax.plot(ordered, y, color="#222222", lw=2)
        maybe_log_x(ax, lockin)
        ax.axvline(np.median(ordered), color="#d00000", lw=1.5, ls="--", label=f"median {np.median(ordered):.2g} uV")
        ax.legend(frameon=False)
    ax.set_title("Cumulative yield")
    ax.set_xlabel("Best lock-in magnitude (uV)")
    ax.set_ylabel("Fraction of measured pixels")

    savefig(fig, out_dir / "extra_01_response_distributions.png")


def plot_angle_response(out_dir: Path, fast_rows: pd.DataFrame, grid_measured: pd.DataFrame) -> None:
    if fast_rows.empty or "theta_i_deg" not in fast_rows.columns:
        return
    work = fast_rows.dropna(subset=["theta_i_deg", "lockin_mag_uV", "pixel"]).copy()
    work = work[work["lockin_mag_uV"].notna() & work["lockin_mag_uV"].ge(0)]
    if work.empty:
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    ax = axes[0]
    top_pixels: list[int] = []
    if not grid_measured.empty and "lockin_mag_uV_used" in grid_measured.columns:
        top_pixels = (
            grid_measured.dropna(subset=["lockin_mag_uV_used", "pixel"])
            .sort_values("lockin_mag_uV_used", ascending=False)
            .head(5)["pixel"]
            .astype(int)
            .tolist()
        )

    for pixel, group in work.groupby("pixel"):
        group = group.sort_values("theta_i_deg")
        if int(pixel) in top_pixels:
            continue
        ax.plot(group["theta_i_deg"], group["lockin_mag_uV"], color="#b8b8b8", lw=0.8, alpha=0.45)

    colors = plt.cm.tab10(np.linspace(0, 1, max(len(top_pixels), 1)))
    for color, pixel in zip(colors, top_pixels):
        group = work[work["pixel"].eq(pixel)].sort_values("theta_i_deg")
        if not group.empty:
            ax.plot(group["theta_i_deg"], group["lockin_mag_uV"], marker="o", lw=1.8, ms=4, color=color, label=f"pixel {pixel:03d}")
    ax.set_title("Polarisation-angle response traces")
    ax.set_xlabel("Incident polarisation angle theta_i (deg)")
    ax.set_ylabel("Lock-in magnitude (uV)")
    maybe_log_y(ax, work["lockin_mag_uV"])
    if top_pixels:
        ax.legend(frameon=False, fontsize=8)

    ax = axes[1]
    binned = work.copy()
    binned["theta_bin"] = (binned["theta_i_deg"] / 7.5).round() * 7.5
    stats = binned.groupby("theta_bin")["lockin_mag_uV"].agg(
        median="median",
        q25=lambda s: s.quantile(0.25),
        q75=lambda s: s.quantile(0.75),
        count="count",
    )
    if not stats.empty:
        x = stats.index.to_numpy(dtype=float)
        med = stats["median"].to_numpy(dtype=float)
        low = med - stats["q25"].to_numpy(dtype=float)
        high = stats["q75"].to_numpy(dtype=float) - med
        ax.errorbar(x, med, yerr=[low, high], fmt="o-", color="#1f7a8c", capsize=3)
    ax.set_title("Across-chip median response by theta_i")
    ax.set_xlabel("theta_i bin (deg)")
    ax.set_ylabel("Median lock-in magnitude (uV)")
    maybe_log_y(ax, work["lockin_mag_uV"])

    savefig(fig, out_dir / "extra_02_angle_response_traces.png")


def plot_spatial_support_maps(out_dir: Path, grid: pd.DataFrame, fast_best: pd.DataFrame, summary: pd.DataFrame) -> None:
    if grid.empty:
        return
    support = grid.copy()
    if not fast_best.empty:
        best_cols = [
            c
            for c in ["pixel", "p_null_mV", "detector_V", "lockin_signed_uV", "lockin_phase_deg", "lockin_mag_std_uV", "snr_estimate"]
            if c in fast_best.columns
        ]
        support = support.merge(fast_best[best_cols].drop_duplicates("pixel"), on="pixel", how="left")
    if not summary.empty and "pixel" in summary.columns:
        add = summary.copy()
        add["max_signed_response_uV"] = numeric(first_col(add, ["max_signed_response_V"])) * 1e6
        add_cols = [c for c in ["pixel", "max_signed_response_uV"] if c in add.columns]
        support = support.merge(add[add_cols].drop_duplicates("pixel"), on="pixel", how="left")

    if "measured" in support.columns:
        for col in ("lockin_mag_uV_used", "r_eff_pm_per_V", "hwp_deg_used", "theta_i_deg_used", "p_null_mV", "detector_V", "lockin_signed_uV", "max_signed_response_uV"):
            if col in support.columns:
                support.loc[numeric(support["measured"]).fillna(0).astype(int).ne(1), col] = np.nan

    fig, axes = plt.subplots(2, 2, figsize=(11, 9))
    plot_heatmap(axes[0, 0], support, "lockin_mag_uV_used", "Best lock-in magnitude", "uV", "magma", log=True)
    plot_heatmap(axes[0, 1], support, "theta_i_deg_used", "Best polarisation angle", "theta_i (deg)", "twilight")
    plot_heatmap(axes[1, 0], support, "p_null_mV", "Null leakage at selected point", "detector signal at null (mV)", "cividis", log=True)
    signed_col = "max_signed_response_uV" if "max_signed_response_uV" in support.columns else "lockin_signed_uV"
    plot_heatmap(axes[1, 1], support, signed_col, "Signed response", "uV", "coolwarm", diverging=True)
    savefig(fig, out_dir / "extra_03_spatial_support_maps.png")


def plot_controls(out_dir: Path, fast_best: pd.DataFrame) -> None:
    if fast_best.empty:
        return
    work = fast_best.copy()
    for col in ("detector_V", "p_null_mV", "lockin_mag_uV", "lockin_net_mag_uV", "lockin_signed_uV"):
        if col in work.columns:
            work[col] = numeric(work[col])
    work["response_uV"] = first_valid_numeric(work, ["lockin_net_mag_uV", "lockin_mag_uV"])
    response_col = "response_uV"
    work = work.dropna(subset=[response_col])
    if work.empty:
        return

    fig, axes = plt.subplots(2, 2, figsize=(11, 8))

    ax = axes[0, 0]
    if "detector_V" in work.columns:
        ax.scatter(work["detector_V"], work[response_col], s=28, alpha=0.8, color="#3a86ff")
        ax.set_xlabel("Detector DC signal (V)")
        ax.set_ylabel("Lock-in response (uV)")
        maybe_log_y(ax, work[response_col])
    ax.set_title("Response vs optical throughput")

    ax = axes[0, 1]
    if "p_null_mV" in work.columns:
        ax.scatter(work["p_null_mV"], work[response_col], s=28, alpha=0.8, color="#ff7f51")
        ax.set_xlabel("Null leakage (mV)")
        ax.set_ylabel("Lock-in response (uV)")
        maybe_log_x(ax, work["p_null_mV"].dropna())
        maybe_log_y(ax, work[response_col])
    ax.set_title("Response vs null leakage")

    ax = axes[1, 0]
    if "detector_V" in work.columns and "p_null_mV" in work.columns:
        extinction = (work["p_null_mV"] / 1000.0) / work["detector_V"]
        extinction = extinction.replace([np.inf, -np.inf], np.nan).dropna()
        if not extinction.empty:
            ax.hist(extinction, bins=24, color="#2a9d8f", alpha=0.85)
            maybe_log_x(ax, extinction)
        ax.set_xlabel("Null leakage / detector DC")
    ax.set_title("Null extinction proxy")
    ax.set_ylabel("Pixels")

    ax = axes[1, 1]
    if "detector_V" in work.columns:
        normed = work[response_col] / work["detector_V"]
        normed = normed.replace([np.inf, -np.inf], np.nan).dropna()
        if not normed.empty:
            ax.hist(normed, bins=24, color="#6a4c93", alpha=0.85)
            maybe_log_x(ax, normed)
        ax.set_xlabel("Lock-in response / detector DC (uV/V)")
    ax.set_title("Normalised response distribution")
    ax.set_ylabel("Pixels")

    savefig(fig, out_dir / "extra_04_null_and_throughput_controls.png")


def plot_phase_signed(out_dir: Path, fast_best: pd.DataFrame) -> None:
    if fast_best.empty or "lockin_phase_deg" not in fast_best.columns:
        return
    work = fast_best.dropna(subset=["lockin_phase_deg"]).copy()
    if work.empty:
        return
    work["lockin_phase_wrapped_deg"] = ((work["lockin_phase_deg"] + 180) % 360) - 180

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.6))
    ax = axes[0]
    ax.hist(work["lockin_phase_wrapped_deg"], bins=np.linspace(-180, 180, 25), color="#4361ee", alpha=0.85)
    ax.set_title("Lock-in phase distribution")
    ax.set_xlabel("Phase (deg)")
    ax.set_ylabel("Pixels")

    ax = axes[1]
    ycol = "lockin_signed_uV" if "lockin_signed_uV" in work.columns else "lockin_mag_uV"
    sc = ax.scatter(work["lockin_phase_wrapped_deg"], work[ycol], c=work.get("theta_i_deg", pd.Series(np.nan, index=work.index)), cmap="twilight", s=34, alpha=0.85)
    ax.axhline(0, color="#333333", lw=0.8)
    ax.set_title("Phase and signed response")
    ax.set_xlabel("Phase (deg)")
    ax.set_ylabel("Signed response (uV)" if ycol == "lockin_signed_uV" else "Magnitude (uV)")
    if "theta_i_deg" in work.columns:
        cbar = fig.colorbar(sc, ax=ax)
        cbar.set_label("theta_i (deg)")

    ax = axes[2]
    if "theta_i_deg" in work.columns:
        mag = work.get("lockin_mag_uV", pd.Series(np.nan, index=work.index))
        ax.scatter(work["theta_i_deg"], work["lockin_phase_wrapped_deg"], s=np.clip(mag.fillna(0), 10, 120), color="#d00000", alpha=0.45)
        ax.set_xlabel("theta_i (deg)")
        ax.set_ylabel("Phase (deg)")
    ax.set_title("Phase vs selected polarisation angle")

    savefig(fig, out_dir / "extra_05_phase_signed_response.png")


def split_flags(series: pd.Series) -> Counter[str]:
    counts: Counter[str] = Counter()
    for value in series.dropna().astype(str):
        for flag in value.split(";"):
            flag = flag.strip()
            if flag:
                counts[flag] += 1
    return counts


def plot_noise_quality(out_dir: Path, fast_rows: pd.DataFrame, summary: pd.DataFrame, pixel_measurements: pd.DataFrame, grid: pd.DataFrame) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))

    ax = axes[0, 0]
    mag_std = finite_values(fast_rows, "lockin_mag_std_uV")
    if not mag_std.empty:
        ax.hist(mag_std, bins=25, color="#457b9d", alpha=0.85)
        maybe_log_x(ax, mag_std)
    ax.set_title("Per-point lock-in magnitude noise")
    ax.set_xlabel("Std dev (uV)")
    ax.set_ylabel("Rows")

    ax = axes[0, 1]
    phase_std = finite_values(fast_rows, "lockin_phase_std_deg")
    if not phase_std.empty:
        ax.hist(phase_std, bins=25, color="#e76f51", alpha=0.85)
    ax.set_title("Per-point phase noise")
    ax.set_xlabel("Phase std dev (deg)")
    ax.set_ylabel("Rows")

    ax = axes[1, 0]
    status_counts = Counter()
    if not grid.empty and "status" in grid.columns:
        status_counts.update(grid["status"].fillna("unknown").astype(str).str.lower())
    elif not pixel_measurements.empty and "status" in pixel_measurements.columns:
        latest = pixel_measurements.drop_duplicates("pixel", keep="last")
        status_counts.update(latest["status"].fillna("unknown").astype(str).str.lower())
    if status_counts:
        labels, values = zip(*status_counts.most_common())
        ax.bar(labels, values, color="#264653")
        ax.tick_params(axis="x", rotation=25)
    ax.set_title("Measurement coverage/status")
    ax.set_ylabel("Pixels")

    ax = axes[1, 1]
    flags = Counter()
    if not summary.empty and "quality_flags" in summary.columns:
        flags.update(split_flags(summary["quality_flags"]))
    if flags:
        top = flags.most_common(12)
        labels, values = zip(*top)
        y = np.arange(len(labels))
        ax.barh(y, values, color="#9d4edd")
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=8)
        ax.invert_yaxis()
    ax.set_title("Most common quality flags")
    ax.set_xlabel("Occurrences")

    savefig(fig, out_dir / "extra_06_noise_quality.png")


def write_ranked_results(out_dir: Path, grid_measured: pd.DataFrame, fast_best: pd.DataFrame, summary: pd.DataFrame) -> None:
    if grid_measured.empty:
        return
    result = grid_measured.copy()
    keep = [
        "pixel",
        "display_row",
        "display_col",
        "lockin_mag_uV_used",
        "r_eff_pm_per_V",
        "theta_i_deg_used",
        "hwp_deg_used",
        "p_null_mV",
        "detector_V",
        "lockin_phase_deg",
        "lockin_signed_uV",
        "snr_estimate",
        "quality",
        "quality_flags",
    ]
    if not fast_best.empty:
        add_cols = [c for c in ["pixel", "p_null_mV", "detector_V", "lockin_phase_deg", "lockin_signed_uV", "snr_estimate"] if c in fast_best.columns]
        result = result.merge(fast_best[add_cols].drop_duplicates("pixel"), on="pixel", how="left")
    if not summary.empty and "pixel" in summary.columns and "quality_flags" in summary.columns:
        result = result.merge(summary[["pixel", "quality_flags"]].drop_duplicates("pixel"), on="pixel", how="left")
    for col in keep:
        if col not in result.columns:
            result[col] = np.nan
    ranked = result[keep].sort_values("lockin_mag_uV_used", ascending=False)
    ranked.to_csv(out_dir / "extra_ranked_pixel_results.csv", index=False)


def write_notes(out_dir: Path, run_dir: Path, grid_measured: pd.DataFrame, summary: pd.DataFrame) -> None:
    n_measured = len(grid_measured)
    lockin = finite_values(grid_measured, "lockin_mag_uV_used")
    r_eff = finite_values(grid_measured, "r_eff_pm_per_V")
    theta = finite_values(grid_measured, "theta_i_deg_used")
    lines = [
        "# Extra Fast-Map Plot Notes",
        "",
        f"Run folder: `{run_dir}`",
        "",
        "Use complete/measured pixels for quantitative statements. Non-measured placeholder values from filled heatmap grids are excluded here.",
        "",
        "Suggested figures to include:",
        "",
        "- `extra_01_response_distributions`: response histogram/CDF; useful for chip yield and spread.",
        "- `extra_02_angle_response_traces`: lock-in response vs incident polarisation angle; shows the polarisation dependence behind the heatmap.",
        "- `extra_03_spatial_support_maps`: supporting chip maps for best angle, null leakage, and signed response.",
        "- `extra_04_null_and_throughput_controls`: control plots showing whether response follows optical throughput or null leakage.",
        "- `extra_05_phase_signed_response`: phase/sign information; use this when discussing polarity or domain orientation.",
        "- `extra_06_noise_quality`: noise, coverage, and quality flags; useful as a methods/supplementary figure.",
        "- `extra_ranked_pixel_results.csv`: table of strongest pixels and their angles/quality values.",
        "",
        "Quick numbers from this run:",
        "",
        f"- measured pixels used: {n_measured}",
    ]
    if not lockin.empty:
        lines.append(f"- best lock-in magnitude: median {lockin.median():.3g} uV, max {lockin.max():.3g} uV")
    if not r_eff.empty:
        lines.append(f"- r_eff: median {r_eff.median():.3g} pm/V, max {r_eff.max():.3g} pm/V")
    if not theta.empty:
        lines.append(f"- selected theta_i range: {theta.min():.3g} to {theta.max():.3g} deg")
    if not summary.empty and "status" in summary.columns:
        counts = summary["status"].fillna("unknown").astype(str).str.lower().value_counts()
        lines.append("- summary status counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    lines.append("")
    lines.append("Caption wording idea: 'Only completed/measured pixels are included in distributions; blank or grey map cells indicate pixels not used in quantitative statistics.'")
    (out_dir / "extra_plot_notes.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="Fast-map run directory")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory; default: <run_dir>/extra_fast_map_plots")
    args = parser.parse_args()

    run_dir = args.run_dir.expanduser().resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory not found: {run_dir}")
    out_dir = args.out_dir.expanduser().resolve() if args.out_dir else run_dir / PLOT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = read_csv(run_dir / "fast_map_all_pixels.csv")
    pixel_measurements = read_csv(run_dir / "pixel_measurements.csv")
    grid = prepare_grid(run_dir, summary)
    grid_measured = measured_grid(grid)
    fast_rows = load_fast_rows(run_dir)
    fast_best = map_display_positions(best_fast_rows(fast_rows), grid)

    plot_response_distributions(out_dir, grid_measured, summary)
    plot_angle_response(out_dir, fast_rows, grid_measured)
    plot_spatial_support_maps(out_dir, grid, fast_best, summary)
    plot_controls(out_dir, fast_best)
    plot_phase_signed(out_dir, fast_best)
    plot_noise_quality(out_dir, fast_rows, summary, pixel_measurements, grid)
    write_ranked_results(out_dir, grid_measured, fast_best, summary)
    write_notes(out_dir, run_dir, grid_measured, summary)

    print(f"Wrote extra fast-map plots to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
