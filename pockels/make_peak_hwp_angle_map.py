#!/usr/bin/env python3
"""Create peak HWP/input-polarisation angle chip maps from fast-map runs.

No-data cells are deliberately filled with 0 degrees, matching the requested
display convention.

Usage:
    python make_peak_hwp_angle_map.py pockels_fast_map/fast_map_20260515_092806
    python make_peak_hwp_angle_map.py RUN_1 RUN_2 RUN_3
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path


def _set_mplconfig() -> None:
    if "MPLCONFIGDIR" in os.environ:
        return
    os.environ["MPLCONFIGDIR"] = str(Path.cwd() / ".mplconfig")


_set_mplconfig()

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize
import numpy as np
import pandas as pd


OUT_DIR_NAME = "extra_fast_map_plots"
HWP_VMIN = -25.0
HWP_VMAX = 70.0
THETA_VMIN = 0.0
THETA_VMAX = 180.0


def read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def number(value) -> float:
    try:
        out = float(value)
    except Exception:
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def first_number(row: pd.Series, names: tuple[str, ...]) -> float:
    for name in names:
        if name in row:
            value = number(row[name])
            if math.isfinite(value):
                return value
    return float("nan")


def load_positions(run_dir: Path) -> pd.DataFrame:
    positions = read_csv(run_dir / "stage_alignment" / "pixel_positions.csv")
    if not positions.empty and {"pixel", "i", "j"}.issubset(positions.columns):
        out = positions[["pixel", "i", "j"]].copy()
        out["pixel"] = pd.to_numeric(out["pixel"], errors="coerce").astype("Int64")
        out["display_row"] = pd.to_numeric(out["j"], errors="coerce") + 1
        out["display_col"] = 10 - pd.to_numeric(out["i"], errors="coerce")
        return out[["pixel", "display_row", "display_col"]].dropna()

    pixels = np.arange(1, 101)
    out = pd.DataFrame({"pixel": pixels})
    out["display_row"] = ((out["pixel"] - 1) // 10) + 1
    out["display_col"] = 10 - ((out["pixel"] - 1) % 10)
    return out


def best_row_from_pixel_csv(pixel_dir: Path) -> tuple[float, float, float]:
    fast_map = read_csv(pixel_dir / "fast_map.csv")
    if fast_map.empty or "hwp_deg" not in fast_map.columns:
        return float("nan"), float("nan"), float("nan")

    lockin = pd.Series(np.nan, index=fast_map.index)
    if "lockin_net_mag_V" in fast_map.columns:
        lockin = pd.to_numeric(fast_map["lockin_net_mag_V"], errors="coerce").abs()
    if "lockin_mag_V" in fast_map.columns:
        raw = pd.to_numeric(fast_map["lockin_mag_V"], errors="coerce").abs()
        lockin = lockin.where(lockin.notna(), raw)
    if lockin.dropna().empty:
        return float("nan"), float("nan"), float("nan")

    idx = lockin.idxmax()
    row = fast_map.loc[idx]
    hwp = first_number(row, ("hwp_deg", "hwp_actual_deg"))
    theta = first_number(row, ("theta_i_deg", "hwp_actual_theta_i_deg", "theta_i_legacy_deg"))
    lockin_uV = float(lockin.loc[idx]) * 1e6
    return hwp, theta, lockin_uV


def build_peak_angle_grid(run_dir: Path) -> pd.DataFrame:
    positions = load_positions(run_dir)
    summary = read_csv(run_dir / "fast_map_all_pixels.csv")

    by_pixel: dict[int, dict[str, object]] = {}
    if not summary.empty and "pixel" in summary.columns:
        summary = summary.copy()
        summary["pixel"] = pd.to_numeric(summary["pixel"], errors="coerce").astype("Int64")
        if "status" in summary.columns:
            summary = summary[summary["status"].astype(str).str.lower().eq("complete")]
        for _, row in summary.iterrows():
            pixel = int(row["pixel"])
            hwp = first_number(row, ("best_mag_hwp_deg", "best_hwp_deg"))
            theta = first_number(row, ("best_mag_theta_i_deg", "best_theta_i_deg"))
            lockin_uV = first_number(row, ("max_lockin_mag_V", "max_abs_signed_response_V"))
            if math.isfinite(lockin_uV):
                lockin_uV *= 1e6
            source = "fast_map_all_pixels"
            if not math.isfinite(hwp):
                pixel_dir = run_dir / "pockels_pixels" / f"pixel_{pixel:03d}"
                hwp_csv, theta_csv, lockin_csv = best_row_from_pixel_csv(pixel_dir)
                hwp = hwp_csv
                theta = theta if math.isfinite(theta) else theta_csv
                lockin_uV = lockin_uV if math.isfinite(lockin_uV) else lockin_csv
                source = "per_pixel_fast_map"
            if math.isfinite(hwp):
                by_pixel[pixel] = {
                    "measured": 1,
                    "peak_hwp_deg": hwp,
                    "peak_theta_i_deg": theta,
                    "peak_lockin_mag_uV": lockin_uV,
                    "source": source,
                }

    if not by_pixel:
        for pixel_dir in sorted((run_dir / "pockels_pixels").glob("pixel_*")):
            try:
                pixel = int(pixel_dir.name.split("_", 1)[1])
            except Exception:
                continue
            hwp, theta, lockin_uV = best_row_from_pixel_csv(pixel_dir)
            if math.isfinite(hwp):
                by_pixel[pixel] = {
                    "measured": 1,
                    "peak_hwp_deg": hwp,
                    "peak_theta_i_deg": theta,
                    "peak_lockin_mag_uV": lockin_uV,
                    "source": "per_pixel_fast_map",
                }

    records: list[dict[str, object]] = []
    for _, pos in positions.sort_values(["display_row", "display_col"]).iterrows():
        pixel = int(pos["pixel"])
        record = {
            "display_row": int(pos["display_row"]),
            "display_col": int(pos["display_col"]),
            "pixel": pixel,
            "measured": 0,
            "peak_hwp_deg": 0.0,
            "peak_theta_i_deg": 0.0,
            "peak_lockin_mag_uV": float("nan"),
            "source": "no_data_filled_0deg",
        }
        if pixel in by_pixel:
            record.update(by_pixel[pixel])
        records.append(record)

    return pd.DataFrame(records).sort_values(["display_row", "display_col"])


def to_array(grid: pd.DataFrame, col: str) -> np.ndarray:
    rows = int(grid["display_row"].max())
    cols = int(grid["display_col"].max())
    arr = np.full((rows, cols), np.nan)
    for _, row in grid.iterrows():
        r = int(row["display_row"]) - 1
        c = int(row["display_col"]) - 1
        arr[r, c] = float(row[col])
    return arr


def format_angle(value: float) -> str:
    if abs(value) < 0.05:
        return "0"
    if abs(value - round(value)) < 0.05:
        return f"{int(round(value))}"
    return f"{value:.1f}"


def annotate_cells(ax: plt.Axes, arr: np.ndarray, cmap_name: str, vmin: float, vmax: float) -> None:
    cmap = plt.get_cmap(cmap_name)
    norm = Normalize(vmin=vmin, vmax=vmax)
    for r in range(arr.shape[0]):
        for c in range(arr.shape[1]):
            value = arr[r, c]
            if not np.isfinite(value):
                continue
            red, green, blue, _ = cmap(norm(value))
            luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
            color = "black" if luminance > 0.55 else "white"
            ax.text(c, r, format_angle(float(value)), ha="center", va="center", fontsize=7, color=color)


def draw_map(ax: plt.Axes, arr: np.ndarray, title: str, label: str, cmap: str, vmin: float, vmax: float) -> None:
    im = ax.imshow(arr, origin="upper", aspect="equal", cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(title)
    ax.set_xlabel("Display column")
    ax.set_ylabel("Display row")
    ax.set_xticks(np.arange(arr.shape[1]))
    ax.set_xticklabels(np.arange(1, arr.shape[1] + 1))
    ax.set_yticks(np.arange(arr.shape[0]))
    ax.set_yticklabels(np.arange(1, arr.shape[0] + 1))
    annotate_cells(ax, arr, cmap, vmin, vmax)
    cbar = ax.figure.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label(label)


def save_map(run_dir: Path) -> Path:
    out_dir = run_dir / OUT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)

    grid = build_peak_angle_grid(run_dir)
    csv_path = out_dir / "peak_hwp_input_polarisation_map_filled0.csv"
    grid.to_csv(csv_path, index=False)

    hwp = to_array(grid, "peak_hwp_deg")
    theta = to_array(grid, "peak_theta_i_deg")

    fig, axes = plt.subplots(1, 2, figsize=(12, 5.4))
    draw_map(
        axes[0],
        hwp,
        "Peak HWP angle\n(no data filled as 0 deg)",
        "HWP angle (deg)",
        "viridis",
        HWP_VMIN,
        HWP_VMAX,
    )
    draw_map(
        axes[1],
        theta,
        "Peak input polarisation theta_i\n(no data filled as 0 deg)",
        "theta_i (deg)",
        "twilight",
        THETA_VMIN,
        THETA_VMAX,
    )
    fig.suptitle(run_dir.name, y=1.02)
    fig.tight_layout()
    png_path = out_dir / "peak_hwp_input_polarisation_map_filled0.png"
    fig.savefig(png_path, dpi=240, bbox_inches="tight")
    fig.savefig(png_path.with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)

    return png_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path, help="Fast-map run directories")
    args = parser.parse_args()

    for raw_dir in args.run_dirs:
        run_dir = raw_dir.expanduser().resolve()
        if not run_dir.is_dir():
            raise SystemExit(f"Run directory not found: {run_dir}")
        png_path = save_map(run_dir)
        print(f"Wrote peak HWP/input-polarisation map: {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
