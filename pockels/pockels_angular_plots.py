"""Saved angular-response products for the fast Pockels map.

The plotting functions consume the JSON-safe output from
``pockels_measurement_analysis.fit_angular_response``.  They do not trigger
hardware reads, so every pixel gets a Figure-S10-style validation plot with no
additional acquisition time.
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Mapping

import numpy as np


ANGULAR_RESPONSE_CSV = "fast_map_angular_response.csv"
ANGULAR_POLAR_PNG = "fast_map_angular_polar.png"
ANGULAR_POLAR_PDF = "fast_map_angular_polar.pdf"
ANGULAR_DIAGNOSTIC_PNG = "fast_map_angular_diagnostics.png"
ANGULAR_DIAGNOSTIC_PDF = "fast_map_angular_diagnostics.pdf"


def _finite(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _selected_hwp_from_certificate(summary: Mapping, fit: Mapping) -> float | None:
    """Use the actual peak certificate, especially after a fit fallback."""
    certificate = summary.get("normalized_peak_certificate") or {}
    if str(certificate.get("status", "")) == "certified":
        certified_hwp = _finite(certificate.get("hwp_deg"))
        if certified_hwp is not None:
            return certified_hwp
    if bool(fit.get("fit_valid", False)):
        return _finite(fit.get("selected_measured_hwp_deg"))
    return None


def write_angular_response_csv(pixel_dir: Path, summary: Mapping) -> Path | None:
    fit = summary.get("angular_response_fit") or {}
    points = list(fit.get("points") or [])
    if not points:
        return None
    path = Path(pixel_dir) / ANGULAR_RESPONSE_CSV
    fields = (
        "hwp_deg",
        "theta_i_deg",
        "angle_source",
        "rotation_x_rad_per_Vrms",
        "rotation_y_rad_per_Vrms",
        "rotation_mag_rad_per_Vrms",
        "retardation_x_rad_per_Vrms",
        "retardation_y_rad_per_Vrms",
        "retardation_mag_rad_per_Vrms",
        "response_phase_deg",
        "predicted_rotation_x_rad_per_Vrms",
        "predicted_rotation_y_rad_per_Vrms",
        "predicted_rotation_mag_rad_per_Vrms",
        "predicted_retardation_mag_rad_per_Vrms",
        "selected_for_hysteresis",
        "angular_fit_status",
        "angular_fit_r_squared",
    )
    selected_hwp = _selected_hwp_from_certificate(summary, fit)
    rows = []
    for point in points:
        hwp = _finite(point.get("hwp_deg"))
        rotation_x = _finite(point.get("response_x_rad_per_Vrms"))
        rotation_y = _finite(point.get("response_y_rad_per_Vrms"))
        rotation_mag = _finite(point.get("response_mag_rad_per_Vrms"))
        predicted_x = _finite(point.get("predicted_x_rad_per_Vrms"))
        predicted_y = _finite(point.get("predicted_y_rad_per_Vrms"))
        predicted_mag = _finite(point.get("predicted_mag_rad_per_Vrms"))
        rows.append({
            "hwp_deg": hwp,
            "theta_i_deg": _finite(point.get("theta_i_deg")),
            "angle_source": str(point.get("angle_source", "")),
            "rotation_x_rad_per_Vrms": rotation_x,
            "rotation_y_rad_per_Vrms": rotation_y,
            "rotation_mag_rad_per_Vrms": rotation_mag,
            # Senarmont retardation Gamma is twice the fitted rotation delta.
            "retardation_x_rad_per_Vrms": 2.0 * rotation_x if rotation_x is not None else None,
            "retardation_y_rad_per_Vrms": 2.0 * rotation_y if rotation_y is not None else None,
            "retardation_mag_rad_per_Vrms": 2.0 * rotation_mag if rotation_mag is not None else None,
            "response_phase_deg": _finite(point.get("response_phase_deg")),
            "predicted_rotation_x_rad_per_Vrms": predicted_x,
            "predicted_rotation_y_rad_per_Vrms": predicted_y,
            "predicted_rotation_mag_rad_per_Vrms": predicted_mag,
            "predicted_retardation_mag_rad_per_Vrms": (
                2.0 * predicted_mag if predicted_mag is not None else None
            ),
            "selected_for_hysteresis": int(
                hwp is not None
                and selected_hwp is not None
                and abs(hwp - selected_hwp) <= 1e-6
            ),
            "angular_fit_status": str(fit.get("status", "")),
            "angular_fit_r_squared": _finite(fit.get("r_squared_complex")),
        })
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _load_pyplot(pixel_dir: Path):
    os.environ.setdefault("MPLCONFIGDIR", str(Path(pixel_dir) / ".mplconfig"))
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    return plt


def _periodic_polar_curve(fit_curve: list[Mapping]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    theta = np.asarray([float(point["theta_i_deg"]) for point in fit_curve], dtype=float)
    x = np.asarray([float(point["response_x_rad_per_Vrms"]) for point in fit_curve])
    y = np.asarray([float(point["response_y_rad_per_Vrms"]) for point in fit_curve])
    gamma_mag = 2.0 * np.hypot(x, y)
    gamma_phase = np.degrees(np.arctan2(y, x))
    theta_full = np.concatenate((theta, theta + 180.0, [360.0]))
    mag_full = np.concatenate((gamma_mag, gamma_mag, [gamma_mag[0]]))
    phase_full = np.concatenate((gamma_phase, gamma_phase, [gamma_phase[0]]))
    return np.radians(theta_full), mag_full, phase_full


def save_angular_plots(pixel_dir: Path, summary: Mapping) -> list[Path]:
    """Save polar and Cartesian theory-validation figures for one pixel."""
    pixel_dir = Path(pixel_dir)
    fit = summary.get("angular_response_fit") or {}
    points = list(fit.get("points") or [])
    fit_curve = list(fit.get("fit_curve") or [])
    if not points or not fit_curve:
        return []
    plt = _load_pyplot(pixel_dir)

    theta_deg = np.asarray([float(point["theta_i_deg"]) for point in points])
    hwp_deg = np.asarray([float(point["hwp_deg"]) for point in points])
    response_x = np.asarray([float(point["response_x_rad_per_Vrms"]) for point in points])
    response_y = np.asarray([float(point["response_y_rad_per_Vrms"]) for point in points])
    gamma_mag = 2.0 * np.hypot(response_x, response_y)
    response_phase = np.degrees(np.arctan2(response_y, response_x))
    polar_theta, polar_gamma, polar_phase = _periodic_polar_curve(fit_curve)

    selected_hwp = _selected_hwp_from_certificate(summary, fit)
    selected_mask = (
        np.isclose(hwp_deg, selected_hwp, atol=1e-6)
        if selected_hwp is not None else np.zeros_like(hwp_deg, dtype=bool)
    )
    pixel = int(summary.get("pixel", 0) or 0)
    r_squared = _finite(fit.get("r_squared_complex"))
    certificate = summary.get("normalized_peak_certificate") or {}
    certificate_status = str(certificate.get("status", "invalid"))

    fig = plt.figure(figsize=(8.2, 7.2), constrained_layout=True)
    ax = fig.add_subplot(111, projection="polar")
    ax.plot(polar_theta, polar_gamma * 1e6, color="#1d4ed8", lw=2.0,
            label="|signed 2θ complex fit| (four-lobed)")
    scatter_theta = np.radians(np.concatenate((theta_deg, theta_deg + 180.0)))
    scatter_gamma = np.concatenate((gamma_mag, gamma_mag)) * 1e6
    scatter_phase = np.concatenate((response_phase, response_phase))
    scatter = ax.scatter(
        scatter_theta,
        scatter_gamma,
        c=scatter_phase,
        cmap="twilight",
        vmin=-180.0,
        vmax=180.0,
        s=42,
        edgecolors="white",
        linewidths=0.5,
        zorder=3,
        label="normalized measurements",
    )
    if np.any(selected_mask):
        selected_theta = float(theta_deg[selected_mask][0])
        selected_gamma = float(gamma_mag[selected_mask][0]) * 1e6
        for alias in (selected_theta, selected_theta + 180.0):
            ax.scatter(
                math.radians(alias), selected_gamma, marker="*", s=180,
                color="#dc2626", edgecolors="black", linewidths=0.7,
                zorder=5, label="hysteresis HWP" if alias == selected_theta else None,
            )
    ax.set_theta_zero_location("E")
    ax.set_theta_direction(1)
    ax.set_thetagrids(np.arange(0, 360, 45))
    ax.set_rlabel_position(22.5)
    ax.set_ylabel("")
    ax.set_title(
        f"Pixel {pixel:03d}: normalized EO angular response\n"
        f"$|\\Gamma|/V_{{rms}}$ ($\\mu$rad/V), "
        f"R$^2$={r_squared:.3f} | peak {certificate_status}"
        if r_squared is not None
        else f"Pixel {pixel:03d}: normalized EO angular response",
        pad=22,
    )
    ax.legend(loc="upper right", bbox_to_anchor=(1.24, 1.13), fontsize=8)
    colorbar = fig.colorbar(scatter, ax=ax, pad=0.10, shrink=0.78)
    colorbar.set_label("lock-in phase (deg)")
    polar_png = pixel_dir / ANGULAR_POLAR_PNG
    polar_pdf = pixel_dir / ANGULAR_POLAR_PDF
    fig.savefig(polar_png, dpi=220)
    fig.savefig(polar_pdf)
    plt.close(fig)

    curve_theta = np.asarray([float(point["theta_i_deg"]) for point in fit_curve])
    curve_x = 2.0 * np.asarray(
        [float(point["response_x_rad_per_Vrms"]) for point in fit_curve]
    )
    curve_y = 2.0 * np.asarray(
        [float(point["response_y_rad_per_Vrms"]) for point in fit_curve]
    )
    order = np.argsort(theta_deg)
    fig, axes = plt.subplots(2, 1, figsize=(9.0, 7.0), sharex=True,
                             constrained_layout=True)
    axes[0].plot(curve_theta, 1e6 * curve_x, color="#2563eb", label="fit $\\Gamma_x$")
    axes[0].plot(curve_theta, 1e6 * curve_y, color="#db2777", label="fit $\\Gamma_y$")
    axes[0].scatter(theta_deg[order], 2e6 * response_x[order], color="#2563eb", s=28)
    axes[0].scatter(theta_deg[order], 2e6 * response_y[order], color="#db2777", s=28)
    axes[0].axhline(0.0, color="#64748b", lw=0.8)
    axes[0].set_ylabel("$\\Gamma/V_{rms}$ ($\\mu$rad/V)")
    axes[0].legend(fontsize=8, ncol=2)
    axes[0].grid(alpha=0.25)
    axes[1].plot(curve_theta, 1e6 * np.hypot(curve_x, curve_y), color="#1d4ed8")
    axes[1].scatter(theta_deg[order], 1e6 * gamma_mag[order], color="#0f172a", s=28)
    if np.any(selected_mask):
        axes[1].scatter(theta_deg[selected_mask], 1e6 * gamma_mag[selected_mask],
                        marker="*", s=170, color="#dc2626", edgecolors="black")
    axes[1].set_xlabel("incident polarization $\\theta_i$ (deg)")
    axes[1].set_ylabel("$|\\Gamma|/V_{rms}$ ($\\mu$rad/V)")
    axes[1].set_xlim(0.0, 180.0)
    axes[1].grid(alpha=0.25)
    fig.suptitle(
        f"Pixel {pixel:03d}: signed 2θ EO fit / four-lobed magnitude validation"
    )
    diagnostic_png = pixel_dir / ANGULAR_DIAGNOSTIC_PNG
    diagnostic_pdf = pixel_dir / ANGULAR_DIAGNOSTIC_PDF
    fig.savefig(diagnostic_png, dpi=220)
    fig.savefig(diagnostic_pdf)
    plt.close(fig)
    return [polar_png, polar_pdf, diagnostic_png, diagnostic_pdf]


def save_pixel_angular_products(pixel_dir: Path, summary: Mapping) -> list[Path]:
    """Write CSV and figures, returning every product that was created."""
    products: list[Path] = []
    csv_path = write_angular_response_csv(pixel_dir, summary)
    if csv_path is not None:
        products.append(csv_path)
    products.extend(save_angular_plots(pixel_dir, summary))
    return products
