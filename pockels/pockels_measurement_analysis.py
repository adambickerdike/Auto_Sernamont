"""Numerical analysis for the BTO fast Pockels map.

The acquisition GUI intentionally delegates the measurement equations to this
module so they can be tested without hardware.  Lock-in quantities are treated
as complex RMS phasors.  The optical normalization is the small-signal
Senarmont/null-slope model

    V_dc(beta) = V_null + A sin(beta)^2
    dV_ac / d(delta) = -A sin(2 beta),

where ``delta`` is the polarization rotation and the retardance is
``Gamma = 2 delta``.  Encoder sign conventions can reverse the reported phase
or sign, so the material result exposed here is the magnitude of ``r_eff``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Mapping, Sequence

import numpy as np


SQRT2 = math.sqrt(2.0)
# The *signed* linear EO response reverses sign every 90 degrees of incident
# polarization, so it is a 2*theta_i quantity.  Its magnitude is therefore
# four-lobed (4*theta_i), as in a Figure-S10-style polar plot.  Fitting the
# complex signed response directly with harmonic 4 destroys that sign change
# and was the cause of the misleading low-R2 angular fits in early v2 runs.
DEFAULT_ANGULAR_HARMONIC = 2
DEFAULT_ANGULAR_MIN_POINTS = 5
DEFAULT_ANGULAR_MIN_R_SQUARED = 0.80
MAX_QWP_OFFSET_FROM_NULL_DEG = 3.0
MAX_ANALYSER_QUADRATURE_ERROR_DEG = 15.0
MAX_NULL_LEAKAGE_FRACTION = 0.05
VALID_SLOPE_SIDES = {
    "plus45",
    "minus45",
    "learned_anchor",
    "learned_opposite",
}
DISQUALIFYING_ROW_FLAGS = {
    "lockin_overload",
    "lockin_range_exceeded",
    "s9_operating_point_failed",
}


def qwp_retardance_from_parallel_ratio(ratio_min_to_max: float) -> tuple[float, float]:
    """Infer retarder phase from a parallel-analyser QWP sweep.

    For linear input and a parallel analyser,
    ``I_min/I_max = cos(delta/2)^2``.  The returned principal retardance is
    in the physically relevant range 0..pi (and 0..180 degrees).
    """
    ratio = finite_float(ratio_min_to_max)
    if ratio is None:
        return float("nan"), float("nan")
    ratio = min(1.0, max(0.0, ratio))
    delta_rad = 2.0 * math.acos(math.sqrt(ratio))
    return float(delta_rad), float(math.degrees(delta_rad))


def finite_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def phasor_statistics(
    magnitudes_v: Sequence[float],
    phases_deg: Sequence[float],
    reference_phase_deg: float = 0.0,
) -> dict:
    """Average lock-in samples in X/Y, avoiding magnitude rectification bias."""
    samples: list[complex] = []
    for magnitude, phase in zip(magnitudes_v, phases_deg):
        mag = finite_float(magnitude)
        pha = finite_float(phase)
        if mag is None or pha is None:
            continue
        phase_rad = math.radians(pha)
        samples.append(complex(mag * math.cos(phase_rad), mag * math.sin(phase_rad)))

    if not samples:
        nan = float("nan")
        return {
            "n": 0,
            "mean_x_V": nan,
            "mean_y_V": nan,
            "mean_mag_V": nan,
            "mean_phase_deg": nan,
            "signed_V": nan,
            "std_x_V": nan,
            "std_y_V": nan,
            "sem_x_V": nan,
            "sem_y_V": nan,
            "cov_xy_V2": nan,
            "cov_mean_xy_V2": nan,
            "magnitude_std_V": nan,
            "magnitude_sem_V": nan,
            "phase_std_deg": nan,
            "phase_sem_deg": nan,
        }

    values = np.asarray(samples, dtype=np.complex128)
    x = values.real
    y = values.imag
    mean_x = float(np.mean(x))
    mean_y = float(np.mean(y))
    mean = complex(mean_x, mean_y)
    magnitude = abs(mean)
    phase_deg = math.degrees(math.atan2(mean_y, mean_x)) if magnitude > 0.0 else 0.0
    reference_rad = math.radians(float(reference_phase_deg))
    signed = mean_x * math.cos(reference_rad) + mean_y * math.sin(reference_rad)

    n = len(values)
    if n > 1:
        covariance = np.cov(np.vstack((x, y)), ddof=1)
        var_x = max(0.0, float(covariance[0, 0]))
        var_y = max(0.0, float(covariance[1, 1]))
        cov_xy = float(covariance[0, 1])
        std_x = math.sqrt(var_x)
        std_y = math.sqrt(var_y)
        sem_x = std_x / math.sqrt(n)
        sem_y = std_y / math.sqrt(n)
        cov_mean = cov_xy / n
    else:
        var_x = var_y = cov_xy = cov_mean = float("nan")
        std_x = std_y = sem_x = sem_y = float("nan")

    if n > 1 and magnitude > 0.0:
        ux = mean_x / magnitude
        uy = mean_y / magnitude
        radial_var = max(0.0, ux * ux * var_x + uy * uy * var_y + 2.0 * ux * uy * cov_xy)
        tangential_var = max(0.0, uy * uy * var_x + ux * ux * var_y - 2.0 * ux * uy * cov_xy)
    elif n > 1:
        radial_var = tangential_var = max(0.0, 0.5 * (var_x + var_y))
    else:
        radial_var = tangential_var = float("nan")

    magnitude_std = math.sqrt(radial_var) if math.isfinite(radial_var) else float("nan")
    magnitude_sem = magnitude_std / math.sqrt(n) if n > 1 else float("nan")
    if magnitude > 0.0 and math.isfinite(tangential_var):
        phase_std_deg = math.degrees(math.sqrt(tangential_var) / magnitude)
        phase_sem_deg = phase_std_deg / math.sqrt(n)
    else:
        phase_std_deg = phase_sem_deg = float("nan")

    return {
        "n": n,
        "mean_x_V": mean_x,
        "mean_y_V": mean_y,
        "mean_mag_V": float(magnitude),
        "mean_phase_deg": float(phase_deg),
        "signed_V": float(signed),
        "std_x_V": float(std_x),
        "std_y_V": float(std_y),
        "sem_x_V": float(sem_x),
        "sem_y_V": float(sem_y),
        "cov_xy_V2": float(cov_xy),
        "cov_mean_xy_V2": float(cov_mean),
        "magnitude_std_V": float(magnitude_std),
        "magnitude_sem_V": float(magnitude_sem),
        "phase_std_deg": float(phase_std_deg),
        "phase_sem_deg": float(phase_sem_deg),
    }


def fit_sinusoid_response(psi_deg, x_v, y_v):
    """Fit the unrestricted complex first-harmonic analyser response.

    For an ideal rotating linear analyser and a linear detector, the most
    general complex first-harmonic response is

        Z(psi) = P + E_s*sin(2*psi) + E_c*cos(2*psi).

    ``P`` is analyser-independent modulation.  It can include electrical
    pickup, genuine total-transmission modulation, residual laser modulation,
    or detector offsets, so it must not be labelled as purely electrical
    background.  ``E_s`` and ``E_c`` are fitted as independent complex
    coefficients; a common temporal phase is tested only after the fit.

    Returns dict with:
      peak_psi_deg / alt_peak_psi_deg - the two extrema of the signed
          response in (-90, 90] (near +45 and -45 for a good null),
      amplitude_V (the best common-phase component), phase_offset_deg,
      eo_axis_phase_deg, analyser_independent_mag_V (|P|),
      predicted_mag_peak_V / predicted_mag_alt_V - expected |Z| at each
          extremum (for the confirm-point check),
      quadrature_fraction / temporal_rank_fraction - diagnostics of whether
          E_s and E_c share a single complex temporal phase,
      r_squared, rms_residual_V, n_points.
    Raises ValueError when under-determined or degenerate.
    """
    psi = np.asarray([float(p) for p in psi_deg], dtype=float)
    z = np.asarray(
        [complex(float(x), float(y)) for x, y in zip(x_v, y_v)], dtype=complex
    )
    ok = np.isfinite(psi) & np.isfinite(z.real) & np.isfinite(z.imag)
    psi, z = psi[ok], z[ok]
    if len(psi) < 4:
        raise ValueError("fit_sinusoid_response needs >= 4 finite probes")

    two_psi = np.radians(2.0 * psi)
    design = np.column_stack(
        [np.ones_like(two_psi), np.sin(two_psi), np.cos(two_psi)]
    )
    if np.linalg.matrix_rank(design) < 3:
        raise ValueError("Analyser probe angles are degenerate for the fit")
    coeffs, _res, _rank, _sv = np.linalg.lstsq(design, z, rcond=None)
    pickup, e1, e2 = (complex(c) for c in coeffs)

    # EO axis: e1^2 + e2^2 = (e*A)^2 regardless of phi.
    axis_sq = e1 * e1 + e2 * e2
    if abs(axis_sq) <= 0.0:
        raise ValueError("Zero EO amplitude - nothing to fit")
    axis_phase = 0.5 * math.atan2(axis_sq.imag, axis_sq.real)
    e_axis = complex(math.cos(axis_phase), math.sin(axis_phase))
    a_in = (e1 * e_axis.conjugate()).real
    b_in = (e2 * e_axis.conjugate()).real
    a_quad = (e1 * e_axis.conjugate()).imag
    b_quad = (e2 * e_axis.conjugate()).imag
    amplitude = math.hypot(a_in, b_in)
    if amplitude <= 0.0:
        raise ValueError("Zero in-axis EO amplitude")
    quadrature_fraction = math.hypot(a_quad, b_quad) / amplitude
    temporal_matrix = np.asarray(
        [[e2.real, e1.real], [e2.imag, e1.imag]], dtype=float
    )
    singular_values = np.linalg.svd(temporal_matrix, compute_uv=False)
    temporal_rank_fraction = (
        float(singular_values[1] / singular_values[0])
        if len(singular_values) >= 2 and singular_values[0] > 0.0
        else 0.0
    )
    phi_deg = math.degrees(math.atan2(b_in, a_in))

    def _wrap_half(value_deg: float) -> float:
        wrapped = (value_deg + 90.0) % 180.0 - 90.0
        return 90.0 if wrapped == -90.0 else wrapped

    peak_psi = _wrap_half((90.0 - phi_deg) / 2.0)     # sin(2 psi + phi) = +1
    alt_psi = _wrap_half((-90.0 - phi_deg) / 2.0)     # sin(2 psi + phi) = -1
    predicted_peak = abs(pickup + e_axis * amplitude)
    predicted_alt = abs(pickup - e_axis * amplitude)
    predicted_peak_phasor = pickup + e_axis * amplitude
    predicted_alt_phasor = pickup - e_axis * amplitude

    model = design @ coeffs
    resid = z - model
    ss_res = float(np.sum(np.abs(resid) ** 2))
    centered = z - z.mean()
    ss_tot = float(np.sum(np.abs(centered) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")

    return {
        "peak_psi_deg": float(peak_psi),
        "alt_peak_psi_deg": float(alt_psi),
        "amplitude_V": float(amplitude),
        "phase_offset_deg": float(phi_deg),
        "eo_axis_phase_deg": float(math.degrees(axis_phase)),
        "analyser_independent_mag_V": float(abs(pickup)),
        "analyser_independent_x_V": float(pickup.real),
        "analyser_independent_y_V": float(pickup.imag),
        "coefficient_sin_x_V": float(e1.real),
        "coefficient_sin_y_V": float(e1.imag),
        "coefficient_cos_x_V": float(e2.real),
        "coefficient_cos_y_V": float(e2.imag),
        # Compatibility aliases for pre-correction JSON/CSV consumers.  New
        # code must use the analyser-independent names above.
        "pickup_mag_V": float(abs(pickup)),
        "pickup_x_V": float(pickup.real),
        "pickup_y_V": float(pickup.imag),
        "predicted_mag_peak_V": float(predicted_peak),
        "predicted_mag_alt_V": float(predicted_alt),
        "predicted_peak_x_V": float(predicted_peak_phasor.real),
        "predicted_peak_y_V": float(predicted_peak_phasor.imag),
        "predicted_alt_x_V": float(predicted_alt_phasor.real),
        "predicted_alt_y_V": float(predicted_alt_phasor.imag),
        "quadrature_fraction": float(quadrature_fraction),
        "temporal_rank_fraction": float(temporal_rank_fraction),
        "r_squared": float(r_squared),
        "rms_residual_V": float(math.sqrt(ss_res / len(psi))),
        "n_points": int(len(psi)),
        "model": "Z(psi) = P + E_s*sin(2*psi) + E_c*cos(2*psi), unrestricted complex linear LSQ",
    }


def fit_dc_malus_response(psi_deg, detector_v):
    """Fit the simultaneous DC analyser fringe in the compensated-null frame.

    The linear least-squares form

        Vdc(psi) = C0 + Cc*cos(2*psi) + Cs*sin(2*psi)

    is exactly equivalent to

        Vdc(psi) = Vmin + A*sin(psi - psi0)^2.

    ``psi0`` is the fitted extinction point.  The two maximum-slope
    (Sénarmont quadrature) points are therefore ``psi0 +/- 45 deg``.
    At either point the normalized fringe fraction is 0.5 and the DC optical
    slope has maximum magnitude.  This independent DC fit is intentionally
    paired with :func:`fit_sinusoid_response`: a raw lock-in maximum by itself
    is not sufficient evidence of correct optical bias.
    """
    psi = np.asarray([float(value) for value in psi_deg], dtype=float)
    voltage = np.asarray([float(value) for value in detector_v], dtype=float)
    ok = np.isfinite(psi) & np.isfinite(voltage)
    psi, voltage = psi[ok], voltage[ok]
    if len(psi) < 4:
        raise ValueError("fit_dc_malus_response needs >= 4 finite probes")

    two_psi = np.radians(2.0 * psi)
    design = np.column_stack(
        [np.ones_like(two_psi), np.cos(two_psi), np.sin(two_psi)]
    )
    if np.linalg.matrix_rank(design) < 3:
        raise ValueError("DC Malus probe angles are degenerate for the fit")
    coefficients, _res, _rank, _sv = np.linalg.lstsq(design, voltage, rcond=None)
    c0, cc, cs = (float(value) for value in coefficients)
    half_swing = math.hypot(cc, cs)
    scale = max(float(np.max(np.abs(voltage))), 1e-15)
    if half_swing <= max(1e-15, 1e-9 * scale):
        raise ValueError("DC Malus fringe has zero fitted optical swing")

    def _wrap_half(value_deg: float) -> float:
        wrapped = (float(value_deg) + 90.0) % 180.0 - 90.0
        return 90.0 if wrapped == -90.0 else wrapped

    # cc=-R*cos(2*psi0), cs=-R*sin(2*psi0) for Vmin+A*sin^2(psi-psi0).
    null_psi = _wrap_half(0.5 * math.degrees(math.atan2(-cs, -cc)))
    plus_quadrature = _wrap_half(null_psi + 45.0)
    minus_quadrature = _wrap_half(null_psi - 45.0)
    v_min = c0 - half_swing
    v_max = c0 + half_swing
    swing = 2.0 * half_swing

    model = design @ coefficients
    residual = voltage - model
    ss_res = float(np.sum(np.square(residual)))
    centered = voltage - float(np.mean(voltage))
    ss_tot = float(np.sum(np.square(centered)))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")

    return {
        "null_psi_deg": float(null_psi),
        "bright_psi_deg": float(_wrap_half(null_psi + 90.0)),
        "plus_quadrature_psi_deg": float(plus_quadrature),
        "minus_quadrature_psi_deg": float(minus_quadrature),
        "v_min_V": float(v_min),
        "v_max_V": float(v_max),
        "fringe_swing_V": float(swing),
        "midfringe_V": float(c0),
        "coefficient_0_V": float(c0),
        "coefficient_cos_V": float(cc),
        "coefficient_sin_V": float(cs),
        "r_squared": float(r_squared),
        "rms_residual_V": float(math.sqrt(ss_res / len(psi))),
        "n_points": int(len(psi)),
        "model": "Vdc(psi) = Vmin + A*sin(psi-psi0)^2, linear LSQ",
    }


def dc_malus_fraction_at_psi(fit: Mapping, psi_deg: float) -> float:
    """Return fitted ``(Vdc-Vmin)/(Vmax-Vmin)`` at an analyser offset."""
    c0 = finite_float(fit.get("coefficient_0_V"))
    cc = finite_float(fit.get("coefficient_cos_V"))
    cs = finite_float(fit.get("coefficient_sin_V"))
    v_min = finite_float(fit.get("v_min_V"))
    swing = finite_float(fit.get("fringe_swing_V"))
    psi = finite_float(psi_deg)
    if None in (c0, cc, cs, v_min, swing) or float(swing) <= 0.0:
        return float("nan")
    two_psi = math.radians(2.0 * float(psi))
    voltage = float(c0) + float(cc) * math.cos(two_psi) + float(cs) * math.sin(two_psi)
    return float((voltage - float(v_min)) / float(swing))


def predict_complex_analyser_response(fit: Mapping, psi_deg: float) -> complex:
    """Evaluate an unrestricted complex analyser fit at ``psi_deg``."""
    p = complex(
        float(fit["analyser_independent_x_V"]),
        float(fit["analyser_independent_y_V"]),
    )
    e_s = complex(
        float(fit["coefficient_sin_x_V"]),
        float(fit["coefficient_sin_y_V"]),
    )
    e_c = complex(
        float(fit["coefficient_cos_x_V"]),
        float(fit["coefficient_cos_y_V"]),
    )
    two_psi = math.radians(2.0 * float(psi_deg))
    return p + e_s * math.sin(two_psi) + e_c * math.cos(two_psi)


def joint_senarmont_from_fits(dc_fit: Mapping, complex_fit: Mapping) -> dict:
    """Project the complex AC response onto the measured DC-fringe derivative.

    For

        D = C0 + Cc*cos(2 psi) + Cs*sin(2 psi)
        Z = P  + Ec*cos(2 psi) + Es*sin(2 psi),

    a scalar Senarmont rotation satisfies ``Z-P = delta_psi*dD/dpsi``.
    The returned complex ``equivalent_rotation`` is the least-squares
    derivative-aligned component.  Any orthogonal AC component is retained as
    ``derivative_residual_fraction`` and must be treated as contamination, not
    as a new operating point to follow.
    """
    cc = finite_float(dc_fit.get("coefficient_cos_V"))
    cs = finite_float(dc_fit.get("coefficient_sin_V"))
    required = (
        finite_float(complex_fit.get("coefficient_cos_x_V")),
        finite_float(complex_fit.get("coefficient_cos_y_V")),
        finite_float(complex_fit.get("coefficient_sin_x_V")),
        finite_float(complex_fit.get("coefficient_sin_y_V")),
    )
    if cc is None or cs is None or any(value is None for value in required):
        raise ValueError("Joint Senarmont fit is missing DC or complex coefficients")
    ec = complex(float(required[0]), float(required[1]))
    es = complex(float(required[2]), float(required[3]))
    derivative_cos = 2.0 * float(cs)
    derivative_sin = -2.0 * float(cc)
    denominator = derivative_cos ** 2 + derivative_sin ** 2
    if denominator <= 0.0:
        raise ValueError("DC analyser fringe has zero derivative norm")

    equivalent_rotation = (
        derivative_cos * ec + derivative_sin * es
    ) / denominator
    aligned_ec = equivalent_rotation * derivative_cos
    aligned_es = equivalent_rotation * derivative_sin
    residual_norm = math.sqrt(abs(ec - aligned_ec) ** 2 + abs(es - aligned_es) ** 2)
    response_norm = math.sqrt(abs(ec) ** 2 + abs(es) ** 2)
    derivative_residual_fraction = (
        residual_norm / response_norm if response_norm > 0.0 else float("inf")
    )

    p = complex(
        float(complex_fit.get("analyser_independent_x_V", 0.0)),
        float(complex_fit.get("analyser_independent_y_V", 0.0)),
    )
    aligned_amplitude = abs(equivalent_rotation) * math.sqrt(denominator)
    independent_fraction = (
        abs(p) / aligned_amplitude if aligned_amplitude > 0.0 else float("inf")
    )

    dc_quadratures = (
        float(dc_fit["plus_quadrature_psi_deg"]),
        float(dc_fit["minus_quadrature_psi_deg"]),
    )
    ac_extrema = (
        finite_float(complex_fit.get("peak_psi_deg")),
        finite_float(complex_fit.get("alt_peak_psi_deg")),
    )
    extrema_offsets = [
        abs(_periodic_delta_deg(dc_psi, ac_psi, 180.0))
        for dc_psi in dc_quadratures
        for ac_psi in ac_extrema
        if ac_psi is not None
    ]

    return {
        "equivalent_rotation_x_rad_rms": float(equivalent_rotation.real),
        "equivalent_rotation_y_rad_rms": float(equivalent_rotation.imag),
        "equivalent_rotation_mag_rad_rms": float(abs(equivalent_rotation)),
        "equivalent_rotation_phase_deg": float(
            math.degrees(math.atan2(equivalent_rotation.imag, equivalent_rotation.real))
        ),
        "equivalent_retardance_x_rad_rms": float(2.0 * equivalent_rotation.real),
        "equivalent_retardance_y_rad_rms": float(2.0 * equivalent_rotation.imag),
        "equivalent_retardance_mag_rad_rms": float(2.0 * abs(equivalent_rotation)),
        "aligned_ac_amplitude_V": float(aligned_amplitude),
        "derivative_residual_fraction": float(derivative_residual_fraction),
        "temporal_rank_fraction": float(
            complex_fit.get("temporal_rank_fraction", float("nan"))
        ),
        "analyser_independent_mag_V": float(abs(p)),
        "analyser_independent_fraction": float(independent_fraction),
        "ac_extremum_shift_from_dc_quadrature_deg": (
            float(min(extrema_offsets)) if extrema_offsets else float("nan")
        ),
        "dc_plus_quadrature_psi_deg": dc_quadratures[0],
        "dc_minus_quadrature_psi_deg": dc_quadratures[1],
        "model": "Z-P = delta_psi*dD/dpsi; Gamma=2*delta_psi",
    }


def joint_senarmont_triplet(
    *,
    detector_null_v: float,
    detector_plus_v: float,
    detector_minus_v: float,
    phasor_null: complex,
    phasor_plus: complex,
    phasor_minus: complex,
) -> dict:
    """Exact joint DC/complex coefficients from the 0,+45,-45 triplet.

    The triplet has no residual degrees of freedom, so this is a production
    verification calculation rather than a redundant model fit.
    """
    v0 = float(detector_null_v)
    vp = float(detector_plus_v)
    vm = float(detector_minus_v)
    z0 = complex(phasor_null)
    zp = complex(phasor_plus)
    zm = complex(phasor_minus)
    if not all(math.isfinite(value) for value in (v0, vp, vm)):
        raise ValueError("Triplet detector readings must be finite")
    if not all(
        math.isfinite(value)
        for value in (z0.real, z0.imag, zp.real, zp.imag, zm.real, zm.imag)
    ):
        raise ValueError("Triplet phasors must be finite")

    c0 = 0.5 * (vp + vm)
    cs = 0.5 * (vp - vm)
    cc = v0 - c0
    half_swing = math.hypot(cc, cs)
    if half_swing <= 0.0:
        raise ValueError("Triplet DC fringe has zero optical swing")
    null_psi = (0.5 * math.degrees(math.atan2(-cs, -cc)) + 90.0) % 180.0 - 90.0
    p = 0.5 * (zp + zm)
    es = 0.5 * (zp - zm)
    ec = z0 - p
    temporal_matrix = np.asarray(
        [[ec.real, es.real], [ec.imag, es.imag]], dtype=float
    )
    singular_values = np.linalg.svd(temporal_matrix, compute_uv=False)
    rank_fraction = (
        float(singular_values[1] / singular_values[0])
        if len(singular_values) >= 2 and singular_values[0] > 0.0
        else 0.0
    )
    dc_fit = {
        "null_psi_deg": float(null_psi),
        "bright_psi_deg": float(_periodic_delta_deg(0.0, null_psi + 90.0, 180.0)),
        "plus_quadrature_psi_deg": float(
            _periodic_delta_deg(0.0, null_psi + 45.0, 180.0)
        ),
        "minus_quadrature_psi_deg": float(
            _periodic_delta_deg(0.0, null_psi - 45.0, 180.0)
        ),
        "v_min_V": float(c0 - half_swing),
        "v_max_V": float(c0 + half_swing),
        "fringe_swing_V": float(2.0 * half_swing),
        "midfringe_V": float(c0),
        "coefficient_0_V": float(c0),
        "coefficient_cos_V": float(cc),
        "coefficient_sin_V": float(cs),
        "r_squared": float("nan"),
        "rms_residual_V": float("nan"),
        "n_points": 3,
        "model": "exact 0,+45,-45 DC triplet",
    }
    complex_fit = {
        "analyser_independent_mag_V": float(abs(p)),
        "analyser_independent_x_V": float(p.real),
        "analyser_independent_y_V": float(p.imag),
        "coefficient_sin_x_V": float(es.real),
        "coefficient_sin_y_V": float(es.imag),
        "coefficient_cos_x_V": float(ec.real),
        "coefficient_cos_y_V": float(ec.imag),
        "temporal_rank_fraction": float(rank_fraction),
        "n_points": 3,
        "model": "exact 0,+45,-45 complex triplet",
    }
    result = joint_senarmont_from_fits(dc_fit, complex_fit)
    result.update({
        "dc_fit": dc_fit,
        "complex_fit": complex_fit,
        "dc_balance_error": float(abs(vp - vm) / max(half_swing, 1e-15)),
        "has_model_redundancy": False,
    })
    return result


def _periodic_delta_deg(reference_deg: float, value_deg: float, period_deg: float = 180.0) -> float:
    return (float(value_deg) - float(reference_deg) + 0.5 * period_deg) % period_deg - 0.5 * period_deg


def _row_phasor(row: Mapping) -> complex | None:
    for prefix in ("lockin_net", "lockin"):
        x = finite_float(row.get(f"{prefix}_x_V"))
        y = finite_float(row.get(f"{prefix}_y_V"))
        if x is not None and y is not None:
            return complex(x, y)
        mag = finite_float(row.get(f"{prefix}_mag_V"))
        phase = finite_float(row.get(f"{prefix}_phase_deg"))
        if mag is not None and phase is not None:
            phase_rad = math.radians(phase)
            return complex(mag * math.cos(phase_rad), mag * math.sin(phase_rad))
    return None


def _row_raw_phasor(row: Mapping) -> complex | None:
    x = finite_float(row.get("lockin_x_V"))
    y = finite_float(row.get("lockin_y_V"))
    if x is not None and y is not None:
        return complex(x, y)
    mag = finite_float(row.get("lockin_mag_V"))
    phase = finite_float(row.get("lockin_phase_deg"))
    if mag is None or phase is None:
        return None
    phase_rad = math.radians(phase)
    return complex(mag * math.cos(phase_rad), mag * math.sin(phase_rad))


def _row_background_phasor(row: Mapping) -> complex | None:
    x = finite_float(row.get("lockin_bg_x_V"))
    y = finite_float(row.get("lockin_bg_y_V"))
    if x is not None and y is not None:
        return complex(x, y)
    mag = finite_float(row.get("lockin_bg_mag_V"))
    phase = finite_float(row.get("lockin_bg_phase_deg"))
    if mag is None or phase is None:
        return None
    phase_rad = math.radians(phase)
    return complex(mag * math.cos(phase_rad), mag * math.sin(phase_rad))


def _row_has_background_corrected_phasor(row: Mapping) -> bool:
    x = finite_float(row.get("lockin_net_x_V"))
    y = finite_float(row.get("lockin_net_y_V"))
    if x is not None and y is not None:
        return True
    return (
        finite_float(row.get("lockin_net_mag_V")) is not None
        and finite_float(row.get("lockin_net_phase_deg")) is not None
    )


def _row_has_disqualifying_quality(row: Mapping) -> bool:
    flags = {
        item
        for item in str(row.get("quality_flags", "") or "").split(";")
        if item
    }
    return bool(flags.intersection(DISQUALIFYING_ROW_FLAGS))


def _row_component_sem(row: Mapping, component: str) -> float | None:
    for prefix in ("lockin_net", "lockin"):
        value = finite_float(row.get(f"{prefix}_{component}_sem_V"))
        if value is not None and value >= 0.0:
            return value
    value = finite_float(row.get("lockin_mag_sem_V"))
    return value if value is not None and value >= 0.0 else None


def _analyser_offset_deg(row: Mapping) -> float | None:
    for actual_key, null_key in (
        ("anl_actual_lab_deg", "anl_null_actual_lab_deg"),
        ("anl_actual_deg", "anl_null_actual_deg"),
    ):
        actual = finite_float(row.get(actual_key))
        null = finite_float(row.get(null_key))
        if actual is not None and null is not None:
            return _periodic_delta_deg(null, actual)
    return finite_float(row.get("anl_offset_from_null_deg"))


def _qwp_offset_deg(row: Mapping) -> float | None:
    q_null = finite_float(row.get("q_null_deg"))
    for actual_key in ("qwp_readout_actual_deg", "qwp_actual_deg"):
        actual = finite_float(row.get(actual_key))
        if actual is not None and q_null is not None:
            return _periodic_delta_deg(q_null, actual)
    return finite_float(row.get("qwp_readout_offset_from_null_deg"))


def derive_rotation_observation(
    rows: Iterable[Mapping],
    detector_ac_gain_over_dc_gain: float = 1.0,
) -> dict | None:
    """Normalize one HWP/Vpp group to a complex RMS rotation observation."""
    detector_gain_ratio = finite_float(detector_ac_gain_over_dc_gain)
    if detector_gain_ratio is None or detector_gain_ratio <= 0.0:
        detector_gain_ratio = 1.0
    usable: list[dict] = []
    hwp = theta_i = vpp = None
    sides: list[str] = []
    for row in rows:
        side = str(row.get("slope_side", ""))
        if side not in VALID_SLOPE_SIDES:
            continue
        if _row_has_disqualifying_quality(row):
            continue
        q_offset = _qwp_offset_deg(row)
        if q_offset is not None and abs(q_offset) > MAX_QWP_OFFSET_FROM_NULL_DEG:
            continue
        phasor = _row_phasor(row)
        detector_v = finite_float(row.get("detector_V"))
        null_reference_v = finite_float(row.get("detector_null_reference_V"))
        null_mv = finite_float(row.get("p_null_mV"))
        if null_reference_v is None and null_mv is not None:
            # Backward compatibility for data recorded before null references
            # included a simultaneous detector reading.
            null_reference_v = 1e-3 * null_mv
        beta_deg = _analyser_offset_deg(row)
        if phasor is None or detector_v is None or null_reference_v is None or beta_deg is None:
            continue
        beta_rad = math.radians(beta_deg)
        quadrature_error_deg = abs(abs(float(beta_deg)) - 45.0)
        if quadrature_error_deg > MAX_ANALYSER_QUADRATURE_ERROR_DEG:
            continue
        sin_sq = math.sin(beta_rad) ** 2
        if sin_sq < 0.02:
            continue
        optical_swing_v = (detector_v - null_reference_v) / sin_sq
        null_v = max(0.0, null_reference_v)
        null_leakage_fraction = null_v / (null_v + optical_swing_v) if optical_swing_v > 0.0 else float("nan")
        derivative_v_per_rad = -optical_swing_v * math.sin(2.0 * beta_rad)
        if not math.isfinite(derivative_v_per_rad) or abs(derivative_v_per_rad) < 1e-12:
            continue
        if optical_swing_v <= 0.0:
            continue
        usable.append({
            # Convert the lock-in channel voltage to the DC detector channel's
            # equivalent volts before dividing by its measured Malus slope.
            "phasor": phasor / detector_gain_ratio,
            "derivative": derivative_v_per_rad,
            "sem_x": (
                _row_component_sem(row, "x") / detector_gain_ratio
                if _row_component_sem(row, "x") is not None else None
            ),
            "sem_y": (
                _row_component_sem(row, "y") / detector_gain_ratio
                if _row_component_sem(row, "y") is not None else None
            ),
            "beta_deg": beta_deg,
            "quadrature_error_deg": quadrature_error_deg,
            "qwp_offset_deg": abs(float(q_offset)) if q_offset is not None else 0.0,
            "optical_swing_v": optical_swing_v,
            "null_leakage_fraction": null_leakage_fraction,
            "side": side,
            "background_corrected": _row_has_background_corrected_phasor(row),
            "row": row,
        })
        sides.append(side)
        hwp = finite_float(row.get("hwp_deg"))
        row_theta_i = finite_float(row.get("hwp_actual_theta_i_deg"))
        if row_theta_i is None:
            row_theta_i = finite_float(row.get("theta_i_deg"))
        if row_theta_i is not None:
            theta_i = row_theta_i
        vpp = finite_float(row.get("vpp"))

    if not usable or hwp is None or vpp is None or vpp <= 0.0:
        return None
    has_opposing_slopes = (
        any(item["derivative"] < 0.0 for item in usable)
        and any(item["derivative"] > 0.0 for item in usable)
    )
    if not any(item["background_corrected"] for item in usable) and not has_opposing_slopes:
        # A single raw phasor cannot distinguish an optical EO response from
        # reference pickup.  Require a measured null phasor or opposite slopes.
        return None

    denominator = sum(item["derivative"] ** 2 for item in usable)
    if denominator <= 0.0:
        return None
    rotation = sum(item["derivative"] * item["phasor"] for item in usable) / denominator
    joint_triplet = None
    plus_candidates = [
        item for item in usable if abs(float(item["beta_deg"]) - 45.0) <= 2.0
    ]
    minus_candidates = [
        item for item in usable if abs(float(item["beta_deg"]) + 45.0) <= 2.0
    ]
    if plus_candidates and minus_candidates:
        plus_item = min(
            plus_candidates, key=lambda item: abs(float(item["beta_deg"]) - 45.0)
        )
        minus_item = min(
            minus_candidates, key=lambda item: abs(float(item["beta_deg"]) + 45.0)
        )
        plus_raw = _row_raw_phasor(plus_item["row"])
        minus_raw = _row_raw_phasor(minus_item["row"])
        null_raw = _row_background_phasor(plus_item["row"])
        if null_raw is None:
            null_raw = _row_background_phasor(minus_item["row"])
        detector_null = finite_float(
            plus_item["row"].get("detector_null_reference_V")
        )
        if detector_null is None:
            detector_null = finite_float(
                minus_item["row"].get("detector_null_reference_V")
            )
        try:
            if None not in (plus_raw, minus_raw, null_raw, detector_null):
                joint_triplet = joint_senarmont_triplet(
                    detector_null_v=float(detector_null),
                    detector_plus_v=float(plus_item["row"]["detector_V"]),
                    detector_minus_v=float(minus_item["row"]["detector_V"]),
                    phasor_null=complex(null_raw) / detector_gain_ratio,
                    phasor_plus=complex(plus_raw) / detector_gain_ratio,
                    phasor_minus=complex(minus_raw) / detector_gain_ratio,
                )
                rotation = complex(
                    float(joint_triplet["equivalent_rotation_x_rad_rms"]),
                    float(joint_triplet["equivalent_rotation_y_rad_rms"]),
                )
        except (KeyError, TypeError, ValueError):
            joint_triplet = None

    def propagated_sem(component: str) -> float:
        terms = []
        for item in usable:
            sem = item[f"sem_{component}"]
            if sem is None:
                return float("nan")
            terms.append((item["derivative"] * sem) ** 2)
        return math.sqrt(sum(terms)) / denominator

    sem_x = propagated_sem("x")
    sem_y = propagated_sem("y")
    magnitude = abs(rotation)
    if magnitude > 0.0 and math.isfinite(sem_x) and math.isfinite(sem_y):
        magnitude_sem = math.sqrt(
            (rotation.real / magnitude * sem_x) ** 2
            + (rotation.imag / magnitude * sem_y) ** 2
        )
    else:
        magnitude_sem = float("nan")

    result = {
        "hwp_deg": float(hwp),
        "theta_i_deg": float(theta_i) if theta_i is not None else None,
        "vpp_source": float(vpp),
        "vrms_source": float(vpp) / (2.0 * SQRT2),
        "rotation_x_rad_rms": float(rotation.real),
        "rotation_y_rad_rms": float(rotation.imag),
        "rotation_mag_rad_rms": float(magnitude),
        "rotation_phase_deg": float(math.degrees(math.atan2(rotation.imag, rotation.real))),
        "rotation_x_sem_rad_rms": float(sem_x),
        "rotation_y_sem_rad_rms": float(sem_y),
        "rotation_mag_sem_rad_rms": float(magnitude_sem),
        "n_slope_rows": len(usable),
        "slope_sides": sorted(set(sides)),
        "paired_slopes": "plus45" in sides and "minus45" in sides,
        "background_corrected": all(item["background_corrected"] for item in usable),
        "opposing_slopes": has_opposing_slopes,
        "geometry_certified": True,
        "analyser_offsets_deg": [float(item["beta_deg"]) for item in usable],
        "max_analyser_quadrature_error_deg": max(
            float(item["quadrature_error_deg"]) for item in usable
        ),
        "max_qwp_offset_from_null_deg": max(
            float(item["qwp_offset_deg"]) for item in usable
        ),
        "optical_swing_estimates_V": [float(item["optical_swing_v"]) for item in usable],
        "null_leakage_fractions": [float(item["null_leakage_fraction"]) for item in usable],
        "max_null_leakage_fraction": max(float(item["null_leakage_fraction"]) for item in usable),
        "detector_ac_gain_over_dc_gain": detector_gain_ratio,
    }
    if joint_triplet is not None:
        result.update({
            "normalisation_source": "joint_raw_triplet_dc_derivative_projection",
            "derivative_residual_fraction": float(
                joint_triplet["derivative_residual_fraction"]
            ),
            "temporal_rank_fraction": float(
                joint_triplet["temporal_rank_fraction"]
            ),
            "analyser_independent_mag_V": float(
                joint_triplet["analyser_independent_mag_V"]
            ),
            "analyser_independent_fraction": float(
                joint_triplet["analyser_independent_fraction"]
            ),
            "triplet_dc_balance_error": float(joint_triplet["dc_balance_error"]),
        })
    else:
        result["normalisation_source"] = "local_dc_slope_regression"
    return result


def _fit_component(x: np.ndarray, y: np.ndarray, sem: np.ndarray, intercept: bool) -> tuple[float, float, float]:
    design = np.column_stack((x, np.ones_like(x))) if intercept else x[:, None]
    use_weights = bool(np.all(np.isfinite(sem)) and np.all(sem > 0.0))
    if use_weights:
        weights = 1.0 / np.square(sem)
        normal = design.T @ (weights[:, None] * design)
        rhs = design.T @ (weights * y)
        covariance = np.linalg.pinv(normal)
        coefficients = covariance @ rhs
        residual = y - design @ coefficients
        dof = len(x) - design.shape[1]
        if dof > 0:
            reduced_chi2 = float(np.sum(np.square(residual / sem)) / dof)
            covariance *= max(1.0, reduced_chi2)
    else:
        coefficients, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
        residual = y - design @ coefficients
        dof = len(x) - design.shape[1]
        if dof > 0:
            covariance = np.linalg.pinv(design.T @ design) * float(np.sum(residual ** 2) / dof)
        else:
            covariance = np.full((design.shape[1], design.shape[1]), np.nan)
    slope = float(coefficients[0])
    offset = float(coefficients[1]) if intercept else 0.0
    slope_sem = math.sqrt(max(0.0, float(covariance[0, 0]))) if math.isfinite(float(covariance[0, 0])) else float("nan")
    if len(x) == 1 and math.isfinite(float(sem[0])) and x[0] != 0.0:
        slope_sem = abs(float(sem[0] / x[0]))
    return slope, offset, slope_sem


def fit_rotation_vs_voltage(observations: Sequence[Mapping]) -> dict | None:
    valid = [
        obs for obs in observations
        if finite_float(obs.get("vrms_source")) is not None
        and finite_float(obs.get("rotation_x_rad_rms")) is not None
        and finite_float(obs.get("rotation_y_rad_rms")) is not None
    ]
    if not valid:
        return None
    valid = sorted(valid, key=lambda obs: float(obs["vrms_source"]))
    x = np.asarray([float(obs["vrms_source"]) for obs in valid], dtype=float)
    yr = np.asarray([float(obs["rotation_x_rad_rms"]) for obs in valid], dtype=float)
    yi = np.asarray([float(obs["rotation_y_rad_rms"]) for obs in valid], dtype=float)
    ser = np.asarray([finite_float(obs.get("rotation_x_sem_rad_rms")) or np.nan for obs in valid], dtype=float)
    sei = np.asarray([finite_float(obs.get("rotation_y_sem_rad_rms")) or np.nan for obs in valid], dtype=float)
    intercept = len(valid) >= 3 and len(np.unique(np.round(x, 12))) >= 3
    slope_r, offset_r, slope_sem_r = _fit_component(x, yr, ser, intercept)
    slope_i, offset_i, slope_sem_i = _fit_component(x, yi, sei, intercept)
    slope = complex(slope_r, slope_i)
    offset = complex(offset_r, offset_i)
    predicted = slope * x + offset
    measured = yr + 1j * yi
    residual_ss = float(np.sum(np.abs(measured - predicted) ** 2))
    if intercept:
        total_ss = float(np.sum(np.abs(measured - np.mean(measured)) ** 2))
    else:
        total_ss = float(np.sum(np.abs(measured) ** 2))
    r_squared = 1.0 - residual_ss / total_ss if total_ss > 0.0 else float("nan")
    slope_mag = abs(slope)
    if slope_mag > 0.0 and math.isfinite(slope_sem_r) and math.isfinite(slope_sem_i):
        slope_mag_sem = math.sqrt(
            (slope.real / slope_mag * slope_sem_r) ** 2
            + (slope.imag / slope_mag * slope_sem_i) ** 2
        )
    else:
        slope_mag_sem = float("nan")
    null_leakages = [
        finite_float(obs.get("max_null_leakage_fraction")) for obs in valid
    ]
    null_leakages = [value for value in null_leakages if value is not None]
    quadrature_errors = [
        finite_float(obs.get("max_analyser_quadrature_error_deg")) for obs in valid
    ]
    quadrature_errors = [value for value in quadrature_errors if value is not None]
    qwp_offsets = [
        finite_float(obs.get("max_qwp_offset_from_null_deg")) for obs in valid
    ]
    qwp_offsets = [value for value in qwp_offsets if value is not None]
    max_null_leakage = max(null_leakages) if null_leakages else float("nan")
    max_quadrature_error = max(quadrature_errors) if quadrature_errors else float("nan")
    max_qwp_offset = max(qwp_offsets) if qwp_offsets else float("nan")
    geometry_certified = bool(
        null_leakages
        and quadrature_errors
        and qwp_offsets
        and max_null_leakage <= MAX_NULL_LEAKAGE_FRACTION
        and max_quadrature_error <= MAX_ANALYSER_QUADRATURE_ERROR_DEG
        and max_qwp_offset <= MAX_QWP_OFFSET_FROM_NULL_DEG
        and all(bool(obs.get("geometry_certified", False)) for obs in valid)
        and all(bool(obs.get("opposing_slopes", False)) for obs in valid)
        and all(bool(obs.get("background_corrected", False)) for obs in valid)
    )
    return {
        "hwp_deg": float(valid[0]["hwp_deg"]),
        "theta_i_deg": finite_float(valid[0].get("theta_i_deg")),
        "n_voltages": len(valid),
        "fit_mode": "free_intercept" if intercept else "through_origin",
        "rotation_slope_x_rad_per_Vrms": float(slope.real),
        "rotation_slope_y_rad_per_Vrms": float(slope.imag),
        "rotation_slope_mag_rad_per_Vrms": float(slope_mag),
        "rotation_slope_phase_deg": float(math.degrees(math.atan2(slope.imag, slope.real))),
        "rotation_slope_mag_sem_rad_per_Vrms": float(slope_mag_sem),
        "rotation_intercept_x_rad_rms": float(offset.real),
        "rotation_intercept_y_rad_rms": float(offset.imag),
        "voltage_linearity_r_squared": float(r_squared),
        "max_null_leakage_fraction": float(max_null_leakage),
        "max_analyser_quadrature_error_deg": float(max_quadrature_error),
        "max_qwp_offset_from_null_deg": float(max_qwp_offset),
        "geometry_certified": geometry_certified,
        "opposing_slopes": all(bool(obs.get("opposing_slopes", False)) for obs in valid),
        "background_corrected": all(bool(obs.get("background_corrected", False)) for obs in valid),
        "observations": [dict(obs) for obs in valid],
    }


def fit_angular_response(
    rotation_fits: Sequence[Mapping],
    *,
    harmonic: int = DEFAULT_ANGULAR_HARMONIC,
    min_points: int = DEFAULT_ANGULAR_MIN_POINTS,
    min_r_squared: float = DEFAULT_ANGULAR_MIN_R_SQUARED,
) -> dict | None:
    """Fit normalized complex EO response versus physical polarization angle.

    The *signed* response is fitted at harmonic 2 in ``theta_i`` (the
    incident-polarization angle), never in raw HWP motor coordinates.  Its
    absolute magnitude is consequently four-lobed.  Both lock-in quadratures
    are fitted simultaneously so the EO sign reversal is preserved instead
    of being destroyed by taking a magnitude before the fit.
    """
    try:
        harmonic = int(harmonic)
    except (TypeError, ValueError):
        harmonic = DEFAULT_ANGULAR_HARMONIC
    if harmonic <= 0:
        harmonic = DEFAULT_ANGULAR_HARMONIC
    try:
        min_points = max(3, int(min_points))
    except (TypeError, ValueError):
        min_points = DEFAULT_ANGULAR_MIN_POINTS

    valid: list[dict] = []
    for fit in rotation_fits:
        hwp = finite_float(fit.get("hwp_deg"))
        theta_i = finite_float(fit.get("theta_i_deg"))
        slope_x = finite_float(fit.get("rotation_slope_x_rad_per_Vrms"))
        slope_y = finite_float(fit.get("rotation_slope_y_rad_per_Vrms"))
        if hwp is None or slope_x is None or slope_y is None:
            continue
        if theta_i is None:
            # A raw HWP angle is not a physical incident-polarization angle
            # unless the calibrated offset/sign mapping is available. Do not
            # manufacture a theory-validation plot with a naïve 2*HWP guess.
            continue
        theta_i %= 180.0
        angle_source = "calibrated_theta_i_deg"
        if not bool(fit.get("geometry_certified", False)):
            continue
        valid.append({
            "fit": fit,
            "hwp_deg": float(hwp),
            "theta_i_deg": float(theta_i),
            "angle_source": angle_source,
            "response": complex(slope_x, slope_y),
        })

    if len(valid) < min_points:
        return None
    distinct_angles = {round(item["theta_i_deg"], 8) for item in valid}
    if len(distinct_angles) < min_points:
        return None

    theta_deg = np.asarray([item["theta_i_deg"] for item in valid], dtype=float)
    response = np.asarray([item["response"] for item in valid], dtype=np.complex128)
    phase = np.radians(float(harmonic) * theta_deg)
    design = np.column_stack((np.ones_like(phase), np.cos(phase), np.sin(phase)))
    if np.linalg.matrix_rank(design) < design.shape[1]:
        return None
    coefficients, _, _, _ = np.linalg.lstsq(design, response, rcond=None)
    predicted = design @ coefficients
    residual_ss = float(np.sum(np.abs(response - predicted) ** 2))
    centered_ss = float(np.sum(np.abs(response - np.mean(response)) ** 2))
    r_squared = 1.0 - residual_ss / centered_ss if centered_ss > 0.0 else float("nan")

    response_mag = np.abs(response)
    predicted_mag = np.abs(predicted)
    mag_total_ss = float(np.sum((response_mag - np.mean(response_mag)) ** 2))
    mag_residual_ss = float(np.sum((response_mag - predicted_mag) ** 2))
    magnitude_from_complex_r_squared = (
        1.0 - mag_residual_ss / mag_total_ss if mag_total_ss > 0.0 else float("nan")
    )

    # Also report the direct fourfold magnitude diagnostic used by an S10-like
    # polar plot.  This is diagnostic only: peak selection remains tied to the
    # signed complex 2-theta model so rectified pickup cannot set the HWP.
    magnitude_phase = np.radians(float(2 * harmonic) * theta_deg)
    magnitude_design = np.column_stack(
        (np.ones_like(magnitude_phase), np.cos(magnitude_phase), np.sin(magnitude_phase))
    )
    magnitude_coefficients, _, _, _ = np.linalg.lstsq(
        magnitude_design, response_mag, rcond=None
    )
    magnitude_prediction_fourfold = magnitude_design @ magnitude_coefficients
    magnitude_fourfold_residual_ss = float(
        np.sum(np.square(response_mag - magnitude_prediction_fourfold))
    )
    magnitude_fourfold_r_squared = (
        1.0 - magnitude_fourfold_residual_ss / mag_total_ss
        if mag_total_ss > 0.0 else float("nan")
    )

    dense_theta_deg = np.linspace(0.0, 180.0, 1441, endpoint=False)
    dense_phase = np.radians(float(harmonic) * dense_theta_deg)
    dense_design = np.column_stack(
        (np.ones_like(dense_phase), np.cos(dense_phase), np.sin(dense_phase))
    )
    dense_response = dense_design @ coefficients
    dense_magnitude = np.abs(dense_response)
    dense_peak_index = int(np.argmax(dense_magnitude))

    # Hysteresis uses the largest geometry-certified response that was
    # actually measured.  The 2-theta fit validates the angular physics and
    # reports a continuous peak, but must not move the choice away from a
    # stronger acquired point merely because of model smoothing.
    selected_index = int(np.argmax(response_mag))
    selected = valid[selected_index]
    selected_fit = selected["fit"]
    fit_ok = bool(math.isfinite(r_squared) and r_squared >= float(min_r_squared))
    status = "ok" if fit_ok else "low_r_squared"
    return {
        "status": status,
        "fit_valid": fit_ok,
        "model": (
            f"complex C0 + Cc*cos({harmonic}*theta_i) + "
            f"Cs*sin({harmonic}*theta_i)"
        ),
        "harmonic": int(harmonic),
        "theory_period_deg": 360.0 / float(harmonic),
        "n_points": int(len(valid)),
        "n_distinct_angles": int(len(distinct_angles)),
        "r_squared_complex": float(r_squared),
        "r_squared_magnitude": float(magnitude_fourfold_r_squared),
        "r_squared_magnitude_from_complex": float(magnitude_from_complex_r_squared),
        "magnitude_harmonic": int(2 * harmonic),
        "magnitude_coefficient_0": float(magnitude_coefficients[0]),
        "magnitude_coefficient_cos": float(magnitude_coefficients[1]),
        "magnitude_coefficient_sin": float(magnitude_coefficients[2]),
        "minimum_r_squared": float(min_r_squared),
        "coefficient_0_x": float(coefficients[0].real),
        "coefficient_0_y": float(coefficients[0].imag),
        "coefficient_cos_x": float(coefficients[1].real),
        "coefficient_cos_y": float(coefficients[1].imag),
        "coefficient_sin_x": float(coefficients[2].real),
        "coefficient_sin_y": float(coefficients[2].imag),
        "continuous_peak_theta_i_deg": float(dense_theta_deg[dense_peak_index]),
        "continuous_peak_response_rad_per_Vrms": float(dense_magnitude[dense_peak_index]),
        "selected_measured_hwp_deg": float(selected["hwp_deg"]),
        "selected_measured_theta_i_deg": float(selected["theta_i_deg"]),
        "selected_measured_response_rad_per_Vrms": float(abs(selected["response"])),
        "selected_predicted_response_rad_per_Vrms": float(predicted_mag[selected_index]),
        "selected_rotation_phase_deg": float(
            math.degrees(math.atan2(selected["response"].imag, selected["response"].real))
        ),
        "selected_fit": dict(selected_fit),
        "points": [
            {
                "hwp_deg": float(item["hwp_deg"]),
                "theta_i_deg": float(item["theta_i_deg"]),
                "angle_source": str(item["angle_source"]),
                "response_x_rad_per_Vrms": float(item["response"].real),
                "response_y_rad_per_Vrms": float(item["response"].imag),
                "response_mag_rad_per_Vrms": float(abs(item["response"])),
                "response_phase_deg": float(
                    math.degrees(math.atan2(item["response"].imag, item["response"].real))
                ),
                "predicted_x_rad_per_Vrms": float(pred.real),
                "predicted_y_rad_per_Vrms": float(pred.imag),
                "predicted_mag_rad_per_Vrms": float(abs(pred)),
            }
            for item, pred in zip(valid, predicted)
        ],
        "fit_curve": [
            {
                "theta_i_deg": float(theta),
                "response_x_rad_per_Vrms": float(value.real),
                "response_y_rad_per_Vrms": float(value.imag),
                "response_mag_rad_per_Vrms": float(abs(value)),
            }
            for theta, value in zip(dense_theta_deg, dense_response)
        ],
    }


def effective_pockels_coefficient(rotation_fit: Mapping | None, config: Mapping | None) -> dict:
    """Calculate |r_eff| only after all geometry/electrical inputs are explicit."""
    result = {
        "status": "not_calculated",
        "r_eff_abs_pm_per_V": None,
        "r_eff_abs_sem_pm_per_V": None,
    }
    if not rotation_fit:
        result["reason"] = "no_valid_rotation_voltage_fit"
        return result
    config = dict(config or {})
    if not bool(config.get("geometry_confirmed", False)):
        result["reason"] = "senarmont_geometry_not_confirmed"
        return result
    if not bool(config.get("sine_drive_confirmed", False)):
        result["reason"] = "sinusoidal_drive_not_confirmed"
        return result
    if config.get("lab_angle_mapping_trusted") is False:
        result["reason"] = "lab_angle_mapping_not_trusted"
        return result
    qwp_retardance_deg = finite_float(config.get("qwp_retardance_deg"))
    if qwp_retardance_deg is not None and abs(qwp_retardance_deg - 90.0) > 10.0:
        result["reason"] = "qwp_retardance_outside_senarmont_tolerance"
        result["qwp_retardance_deg"] = qwp_retardance_deg
        return result

    required = {
        "wavelength_nm": "wavelength_nm",
        "film_thickness_nm": "film_thickness_nm",
        "electrode_gap_um": "electrode_gap_um",
        "field_correction": "field_correction",
        "refractive_index": "refractive_index",
        "device_vpp_scale": "device_vpp_scale",
        "detector_ac_gain_over_dc_gain": "detector_ac_gain_over_dc_gain",
    }
    values: dict[str, float] = {}
    missing: list[str] = []
    for key, label in required.items():
        value = finite_float(config.get(key))
        if value is None or value <= 0.0:
            missing.append(label)
        else:
            values[key] = value
    if missing:
        result["reason"] = "missing_or_nonpositive_inputs"
        result["missing_inputs"] = missing
        return result

    slope = finite_float(rotation_fit.get("rotation_slope_mag_rad_per_Vrms"))
    if slope is None:
        result["reason"] = "invalid_rotation_slope"
        return result
    n_voltages = int(rotation_fit.get("n_voltages", 0) or 0)
    linearity_r2 = finite_float(rotation_fit.get("voltage_linearity_r_squared"))
    if n_voltages >= 3 and linearity_r2 is not None and linearity_r2 < 0.98:
        result["reason"] = "voltage_response_failed_linearity_check"
        result["voltage_linearity_r_squared"] = linearity_r2
        return result
    max_null_fraction = finite_float(rotation_fit.get("max_null_leakage_fraction"))
    if max_null_fraction is not None and max_null_fraction > 0.05:
        result["reason"] = "null_leakage_exceeds_five_percent"
        result["max_null_leakage_fraction"] = max_null_fraction
        return result
    wavelength_m = values["wavelength_nm"] * 1e-9
    thickness_m = values["film_thickness_nm"] * 1e-9
    gap_m = values["electrode_gap_um"] * 1e-6
    alpha = values["field_correction"]
    refractive_index = values["refractive_index"]
    voltage_scale = values["device_vpp_scale"]
    slope_per_device_v = slope / voltage_scale
    coefficient_m_per_v = (
        2.0 * wavelength_m * gap_m * slope_per_device_v
        / (math.pi * refractive_index ** 3 * alpha * thickness_m)
    )

    relative_variance = 0.0
    uncertainty_terms = {
        "rotation_slope": (rotation_fit.get("rotation_slope_mag_sem_rad_per_Vrms"), slope, 1.0),
        "wavelength": (config.get("wavelength_std_nm"), values["wavelength_nm"], 1.0),
        "film_thickness": (config.get("film_thickness_std_nm"), values["film_thickness_nm"], 1.0),
        "electrode_gap": (config.get("electrode_gap_std_um"), values["electrode_gap_um"], 1.0),
        "field_correction": (config.get("field_correction_std"), alpha, 1.0),
        "refractive_index": (config.get("refractive_index_std"), refractive_index, 3.0),
        "device_vpp_scale": (config.get("device_vpp_scale_std"), voltage_scale, 1.0),
        "detector_ac_gain_over_dc_gain": (
            config.get("detector_ac_gain_over_dc_gain_std"),
            values["detector_ac_gain_over_dc_gain"],
            1.0,
        ),
    }
    included_terms: list[str] = []
    for label, (uncertainty, nominal, exponent) in uncertainty_terms.items():
        std = finite_float(uncertainty)
        if std is not None and std >= 0.0 and nominal > 0.0:
            relative_variance += (exponent * std / nominal) ** 2
            included_terms.append(label)
    coefficient_sem = abs(coefficient_m_per_v) * math.sqrt(relative_variance) if included_terms else None
    result.update({
        "status": "calculated" if n_voltages >= 3 else "calculated_linearity_unverified",
        "reason": None if n_voltages >= 3 else "fewer_than_three_voltage_levels",
        "r_eff_abs_pm_per_V": abs(coefficient_m_per_v) * 1e12,
        "r_eff_abs_sem_pm_per_V": coefficient_sem * 1e12 if coefficient_sem is not None else None,
        "rotation_slope_rad_per_device_Vrms": slope_per_device_v,
        "uncertainty_terms_included": included_terms,
        "model": "Senarmont small-signal, Gamma=2*delta, E=alpha*V_device/g",
        "voltage_convention": "DSP7230 RMS response divided by source Vpp/(2*sqrt(2)); device_vpp_scale applies equally to RMS",
        "detector_convention": "lock-in volts divided by detector_ac_gain_over_dc_gain before DC Malus-slope normalization",
        "interpretation": "geometry-specific effective coefficient magnitude, not an intrinsic BTO tensor element",
    })
    return result


def analyse_fast_map_rows(rows: Sequence[Mapping], physics_config: Mapping | None = None) -> dict:
    physics_config = dict(physics_config or {})
    detector_gain_ratio = finite_float(physics_config.get("detector_ac_gain_over_dc_gain")) or 1.0
    grouped: dict[tuple[float, float], list[Mapping]] = defaultdict(list)
    for row in rows:
        hwp = finite_float(row.get("hwp_deg"))
        vpp = finite_float(row.get("vpp"))
        if hwp is not None and vpp is not None:
            grouped[(round(hwp, 9), round(vpp, 9))].append(row)

    observations: list[dict] = []
    for grouped_rows in grouped.values():
        observation = derive_rotation_observation(
            grouped_rows,
            detector_ac_gain_over_dc_gain=detector_gain_ratio,
        )
        if observation is not None:
            observations.append(observation)

    by_hwp: dict[float, list[dict]] = defaultdict(list)
    for observation in observations:
        by_hwp[round(float(observation["hwp_deg"]), 9)].append(observation)
    fits = [fit for fit in (fit_rotation_vs_voltage(items) for items in by_hwp.values()) if fit]
    certified_fits = [fit for fit in fits if bool(fit.get("geometry_certified", False))]
    raw_best = (
        max(fits, key=lambda item: item["rotation_slope_mag_rad_per_Vrms"])
        if fits else None
    )
    angular_fit = fit_angular_response(certified_fits)
    selection_mode = "no_valid_normalized_response"
    best = None
    if angular_fit and bool(angular_fit.get("fit_valid", False)):
        target_hwp = finite_float(angular_fit.get("selected_measured_hwp_deg"))
        if target_hwp is not None:
            matching = [
                fit for fit in certified_fits
                if abs(float(fit["hwp_deg"]) - target_hwp) <= 1e-7
            ]
            if matching:
                best = max(
                    matching,
                    key=lambda item: item["rotation_slope_mag_rad_per_Vrms"],
                )
                selection_mode = "signed_2theta_fit_certified_measured_hwp"
    if best is None and certified_fits:
        best = max(
            certified_fits,
            key=lambda item: item["rotation_slope_mag_rad_per_Vrms"],
        )
        selection_mode = "certified_normalized_measured_max_fallback"
    coefficient = effective_pockels_coefficient(best, physics_config)
    certificate_flags: list[str] = []
    if not best:
        certificate_flags.append("no_geometry_certified_normalized_hwp")
    if angular_fit is None:
        certificate_flags.append("insufficient_points_for_signed_2theta_fit")
    elif not bool(angular_fit.get("fit_valid", False)):
        certificate_flags.append("signed_2theta_fit_low_r_squared")
    peak_certificate = {
        "status": "certified" if best else "invalid",
        "selection_mode": selection_mode,
        "hwp_deg": float(best["hwp_deg"]) if best else None,
        "theta_i_deg": finite_float(best.get("theta_i_deg")) if best else None,
        "rotation_slope_rad_per_Vrms": (
            float(best["rotation_slope_mag_rad_per_Vrms"]) if best else None
        ),
        "rotation_phase_deg": (
            float(best["rotation_slope_phase_deg"]) if best else None
        ),
        "max_null_leakage_fraction": (
            finite_float(best.get("max_null_leakage_fraction")) if best else None
        ),
        "max_analyser_quadrature_error_deg": (
            finite_float(best.get("max_analyser_quadrature_error_deg")) if best else None
        ),
        "max_qwp_offset_from_null_deg": (
            finite_float(best.get("max_qwp_offset_from_null_deg")) if best else None
        ),
        "angular_fit_status": angular_fit.get("status") if angular_fit else "unavailable",
        "angular_fit_r_squared": (
            finite_float(angular_fit.get("r_squared_complex")) if angular_fit else None
        ),
        "quality_flags": certificate_flags,
    }
    return {
        "status": "ok" if best else "no_valid_senarmont_slope_rows",
        "normalisation_model": "Vdc=Vnull+A*sin(beta)^2; dVac/d(delta)=-A*sin(2*beta)",
        "voltage_convention": "source_Vrms=source_Vpp/(2*sqrt(2)); lock-in phasors are RMS",
        "rotation_observations": observations,
        "hwp_voltage_fits": fits,
        "geometry_certified_hwp_voltage_fits": certified_fits,
        "raw_best_rotation_fit": raw_best,
        "angular_response_fit": angular_fit,
        "peak_certificate": peak_certificate,
        "peak_selection_mode": selection_mode,
        "best_rotation_fit": best,
        "effective_pockels": coefficient,
    }
