#!/usr/bin/env python3
"""Signed-loop analysis of DC hysteresis sweeps (v2).

Turns the raw ``dc_hysteresis.csv`` written by
``Pockels_Calibration_2026.run_dc_hysteresis_sweep`` into ferroelectric loop
metrics. Pure numerical module: no hardware imports, no GUI, importable and
testable offline, and usable retroactively on CSVs from the original (v1)
codebase (which stored only lock-in magnitude + phase - X/Y are reconstructed).

Physics
-------
The linear EO response is odd in the ferroelectric polarization: reversing the
saturated domain state flips the lock-in phasor by ~180 deg at constant
magnitude. The raw |R|(V_dc) is therefore a *butterfly* (wings + near-zero
minima at the coercive voltages), while the physically meaningful loop is the
*signed* projection onto the saturation phase axis:

    S(V) = R * cos(phase - phi_ref)        (ferroelectric S-curve)
    Q(V) = R * sin(phase - phi_ref)        (quadrature residual)

phi_ref is taken from the phasor-weighted mean phase at positive saturation
(|V| >= sat_fraction * Vmax), with the sign fixed so S(+Vmax) > 0. |Q| staying
small validates the single-axis projection; growing |Q| flags reference
pickup, thermal or electro-absorption contamination.

Metric conventions (per cycle, computed on the LAST cycle by default):
    v_c_plus    zero crossing of S on the ascending (up) branch
    v_c_minus   zero crossing of S on the descending (down) branch
    loop_width  v_c_plus - v_c_minus
    imprint     (v_c_plus + v_c_minus) / 2      (built-in field / 0-V asymmetry)
    s_rem_pos   S at V=0 on the down branch     (remanence after +poling)
    s_rem_neg   S at V=0 on the up branch       (remanence after -poling)
    s_sat_pos/neg  mean S over the saturation tails
    switchable  (s_sat_pos - s_sat_neg) / 2
    frozen      (s_sat_pos + s_sat_neg) / 2     (non-switchable + any residual
                                                 common-mode pickup)
All response metrics are in lock-in volts (RMS, as recorded).
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import warnings

import numpy as np

try:  # optional - branch fits degrade gracefully without scipy
    from scipy.optimize import OptimizeWarning, curve_fit
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    OptimizeWarning = Warning
    _HAVE_SCIPY = False

# numpy 2.x renamed trapz -> trapezoid; support both (lab PC runs numpy 1.x)
_trapezoid = getattr(np, "trapezoid", None) or getattr(np, "trapz")

SAT_FRACTION_DEFAULT = 0.8       # |V| >= f*Vmax counts as saturation tail
MIN_BRANCH_POINTS = 4            # fewer -> branch skipped

# ------------------------- loop classification --------------------------
# Thresholds for classify_loop(). All overridable per call.
CLASSIFY_MIN_SWITCHABLE_V = 1e-6      # below -> no_response / frozen_response
CLASSIFY_MAX_QUADRATURE = 0.5         # |Q|/|S| above -> invalid_projection
CLASSIFY_MIN_WIDTH_V = 2.0            # loop width below -> linear_no_hysteresis
CLASSIFY_SQUARENESS_SQUARE = 0.7      # mean squareness above -> square
CLASSIFY_SQUARENESS_SLANTED = 0.3     # between -> slanted, below -> rounded
CLASSIFY_IMPRINT_MIN_V = 3.0          # |imprint| above max(this, frac*halfwidth)
CLASSIFY_IMPRINT_FRACTION = 0.5       #   -> "imprinted" modifier
CLASSIFY_PINCH_DIP_FRACTION = 0.5     # opening dip below frac of humps -> pinched
CLASSIFY_OPEN_FRACTION_LINEAR = 0.15  # opening/(2*switchable) below -> linear
CLASSIFY_FROZEN_RATIO = 1.0           # |frozen/switchable| above -> partially_frozen
CLASSIFY_LEAKY_S = 10e-9              # leakage conductance above -> leaky
CLASSIFY_DRIFT_FRACTION = 0.3         # closure/|switchable| above -> drifting_loop
CLASSIFY_SAT_ASYM = 0.3               # |sat_asymmetry| above -> saturation_asymmetric
DC_HALF_FRINGE_TOLERANCE = 0.075      # same normalized tolerance as S9 certificate
PHASE_REFERENCE_AXIS_WARN_DEG = 15.0  # calibrated versus saturation-derived axis

TYPE_SHORT_CODES = {
    "ferroelectric_square": "SQ",
    "ferroelectric_slanted": "SL",
    "ferroelectric_rounded": "RN",
    "pinched": "PN",
    "linear_no_hysteresis": "LN",
    "frozen_response": "FZ",
    "no_response": "NR",
    "invalid_projection": "IV",
    "partial_loop_unresolved": "PL",
    "unclassified": "?",
}

# Shared display colors for the loop types (used by make_hysteresis_maps and
# the fast-map GUI chip-map layer selector).
TYPE_COLORS = {
    "ferroelectric_square": "#1a9850",
    "ferroelectric_slanted": "#91cf60",
    "ferroelectric_rounded": "#d9ef8b",
    "pinched": "#fc8d59",
    "linear_no_hysteresis": "#4575b4",
    "frozen_response": "#9970ab",
    "partial_loop_unresolved": "#fee08b",
    "no_response": "#bdbdbd",
    "invalid_projection": "#d73027",
    "unclassified": "#eeeeee",
}


# ============================ CSV ingestion =============================

def load_dc_hysteresis_csv(path):
    """Parse a dc_hysteresis.csv (v1 or v2 column set).

    Returns dict with:
      meta      - dict of '# key=value' header comments (strings)
      columns   - dict column-name -> np.ndarray (numeric) or list[str]
      V, X, Y, R, phase_deg, P_dc, branch, cycle, I_smu, tripped,
      lockin_invalid - convenience
                  arrays (X/Y reconstructed from R/phase when absent; cycle
                  defaults to 1; I_smu/tripped NaN/0 when absent).
    """
    meta = {}
    header = None
    data_rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if line.startswith("#"):
                body = line.lstrip("#").strip()
                if "=" in body:
                    k, v = body.split("=", 1)
                    meta[k.strip()] = v.strip()
                continue
            if header is None:
                header = [c.strip() for c in line.split(",")]
                continue
            data_rows.append(line.split(","))

    if header is None or not data_rows:
        raise ValueError(f"No data rows found in {path}")

    ncol = len(header)
    columns = {name: [] for name in header}
    for parts in data_rows:
        if len(parts) != ncol:
            continue  # skip malformed row
        for name, value in zip(header, parts):
            columns[name].append(value.strip())

    def _num(name, default=None):
        if name not in columns:
            return default
        out = np.empty(len(columns[name]), dtype=float)
        for i, v in enumerate(columns[name]):
            try:
                out[i] = float(v)
            except ValueError:
                out[i] = np.nan
        return out

    V = _num("V_dc_V")
    if V is None:
        raise ValueError(f"{path} has no V_dc_V column")
    R = _num("LockIn_Mag_V")
    phase = _num("LockIn_Phase_deg")
    X = _num("LockIn_X_V")
    Y = _num("LockIn_Y_V")
    if X is None or Y is None:
        if R is None or phase is None:
            raise ValueError(f"{path} has neither X/Y nor Mag/Phase columns")
        X = R * np.cos(np.radians(phase))
        Y = R * np.sin(np.radians(phase))
    if R is None:
        R = np.hypot(X, Y)
    if phase is None:
        phase = np.degrees(np.arctan2(Y, X))

    branch = columns.get("branch", ["?"] * len(V))
    cycle = _num("cycle")
    if cycle is None:
        cycle = _infer_cycles_from_branches(branch)

    lockin_input_overload = np.nan_to_num(
        _num("LockIn_Input_Overload", np.zeros(len(V)))
    ).astype(int)
    lockin_output_overload = np.nan_to_num(
        _num("LockIn_Output_Overload_Final", np.zeros(len(V)))
    ).astype(int)

    return {
        "path": path,
        "meta": meta,
        "columns": columns,
        "V": V,
        "X": X,
        "Y": Y,
        "R": R,
        "phase_deg": phase,
        "P_dc": _num("P_dc_W", np.full(len(V), np.nan)),
        "R_std": _num("LockIn_Mag_Std_V", np.full(len(V), np.nan)),
        "branch": list(branch),
        "cycle": cycle.astype(int),
        "I_smu": _num("SMU_I_A", np.full(len(V), np.nan)),
        "tripped": np.nan_to_num(
            _num("compliance_tripped", np.zeros(len(V)))).astype(int),
        "lockin_input_overload": lockin_input_overload,
        "lockin_output_overload": lockin_output_overload,
        "lockin_invalid": (
            (lockin_input_overload != 0) | (lockin_output_overload != 0)
        ).astype(int),
        "t_s": _num("t_s", np.full(len(V), np.nan)),
    }


def _meta_float(meta, key):
    """Return one finite numeric metadata value, otherwise ``None``."""
    try:
        value = float(meta.get(key, "nan"))
    except (AttributeError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _branch_base(name):
    return str(name).rstrip("0123456789")


def _branch_cycle_number(name):
    digits = str(name)[len(_branch_base(name)):]
    return int(digits) if digits else 1


def _infer_cycles_from_branches(branches):
    return np.array([_branch_cycle_number(b) for b in branches], dtype=float)


# ======================= signed-loop construction =======================

def saturation_phase_deg(V, X, Y, sat_fraction=SAT_FRACTION_DEFAULT):
    """Reference phase from the positive-saturation tail.

    Phasor-weighted mean phase over V >= sat_fraction*max(V); sign convention
    is fixed by the caller applying signed_projection and checking S(+Vmax).
    """
    V = np.asarray(V, float)
    vmax = np.nanmax(np.abs(V))
    if not np.isfinite(vmax) or vmax <= 0:
        raise ValueError("No finite voltages")
    m = (V >= sat_fraction * vmax) & np.isfinite(X) & np.isfinite(Y)
    if not np.any(m):
        raise ValueError("No saturation-tail points to define the phase reference")
    xs, ys = float(np.mean(X[m])), float(np.mean(Y[m]))
    if xs == 0.0 and ys == 0.0:
        raise ValueError("Zero saturation phasor - cannot define phase reference")
    return math.degrees(math.atan2(ys, xs))


def signed_projection(X, Y, phi_ref_deg):
    """Project the phasor onto the reference axis: returns (S, Q)."""
    phi = math.radians(phi_ref_deg)
    X = np.asarray(X, float)
    Y = np.asarray(Y, float)
    S = X * math.cos(phi) + Y * math.sin(phi)
    Q = -X * math.sin(phi) + Y * math.cos(phi)
    return S, Q


# ============================ branch helpers ============================

def _best_zero_crossing(Vb, Sb):
    """Interpolated V of the sign change with the steepest local slope.

    Returns None when the branch never changes sign.
    """
    Vb = np.asarray(Vb, float)
    Sb = np.asarray(Sb, float)
    ok = np.isfinite(Vb) & np.isfinite(Sb)
    Vb, Sb = Vb[ok], Sb[ok]
    if len(Vb) < 2:
        return None
    sign = np.sign(Sb)
    idx = np.where(sign[:-1] * sign[1:] < 0)[0]
    # also accept exact zeros
    zeros = np.where(Sb == 0.0)[0]
    candidates = []
    for i in idx:
        dv = Vb[i + 1] - Vb[i]
        ds = Sb[i + 1] - Sb[i]
        if ds == 0.0:
            continue
        vc = Vb[i] - Sb[i] * dv / ds
        slope = abs(ds / dv) if dv != 0 else 0.0
        candidates.append((slope, vc))
    for i in zeros:
        candidates.append((np.inf if 0 < i < len(Vb) - 1 else 0.0, Vb[i]))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return float(candidates[0][1])


def _interp_at(Vb, Sb, v0=0.0):
    """S interpolated at V=v0 along the branch (None outside the range)."""
    Vb = np.asarray(Vb, float)
    Sb = np.asarray(Sb, float)
    ok = np.isfinite(Vb) & np.isfinite(Sb)
    Vb, Sb = Vb[ok], Sb[ok]
    if len(Vb) < 2 or not (Vb.min() <= v0 <= Vb.max()):
        return None
    order = np.argsort(Vb)
    return float(np.interp(v0, Vb[order], Sb[order]))


def _switching_distribution(Vb, Sb, baseline_slope=0.0):
    """Switching-field distribution of one branch: |dS/dV| after removing the
    field-induced (quadratic-EO) baseline slope.

    Returns dict with the peak (position/height/FWHM, as before) plus the
    weighted moments of the distribution — mean, sigma (disorder width) and
    skewness (nucleation asymmetry within the branch). Weights below 10 % of
    the peak are excluded from the moments so saturation-tail noise does not
    dominate. None when the branch is too short.
    """
    Vb = np.asarray(Vb, float)
    Sb = np.asarray(Sb, float)
    ok = np.isfinite(Vb) & np.isfinite(Sb)
    Vb, Sb = Vb[ok], Sb[ok]
    if len(Vb) < 4:
        return None
    order = np.argsort(Vb)
    Vs, Ss = Vb[order], Sb[order]
    # collapse duplicate voltages (fine+coarse grid overlap)
    Vu, inv = np.unique(np.round(Vs, 6), return_inverse=True)
    Su = np.array([Ss[inv == k].mean() for k in range(len(Vu))])
    if len(Vu) < 4:
        return None
    d = np.gradient(Su, Vu) - float(baseline_slope)
    ad = np.abs(d)
    k = int(np.argmax(ad))
    peak = float(ad[k])
    v_peak = float(Vu[k])
    if peak <= 0.0:
        return None
    half = peak / 2.0
    lo = hi = None
    for i in range(k, -1, -1):
        if ad[i] < half:
            lo = np.interp(half, [ad[i], ad[i + 1]], [Vu[i], Vu[i + 1]])
            break
    for i in range(k, len(Vu)):
        if ad[i] < half:
            hi = np.interp(half, [ad[i - 1], ad[i]][::-1], [Vu[i - 1], Vu[i]][::-1]) \
                if ad[i - 1] != ad[i] else Vu[i]
            break
    fwhm = float(hi - lo) if (lo is not None and hi is not None) else None

    weights = np.where(ad >= 0.1 * peak, ad, 0.0)
    wsum = float(weights.sum())
    mean = sigma = skew = None
    if wsum > 0.0:
        mean = float(np.sum(weights * Vu) / wsum)
        var = float(np.sum(weights * (Vu - mean) ** 2) / wsum)
        sigma = float(math.sqrt(var)) if var > 0 else 0.0
        if sigma and sigma > 0:
            skew = float(np.sum(weights * (Vu - mean) ** 3) / (wsum * sigma ** 3))
    return {
        "v_peak_V": v_peak,
        "peak_V_per_V": peak,
        "fwhm_V": fwhm,
        "mean_V": mean,
        "sigma_V": sigma,
        "skewness": skew,
    }


def _tail_linear_slope(V, S, m_pos_tail, m_neg_tail):
    """Field-induced (quadratic-EO) slope: linear fit of S in each saturation
    tail, averaged over the two tails. Model-free (no scipy needed)."""
    slopes = []
    for m in (m_pos_tail, m_neg_tail):
        if m.sum() >= 3:
            coeffs = np.polyfit(V[m], S[m], 1)
            slopes.append(float(coeffs[0]))
    return float(np.mean(slopes)) if slopes else None


def _transition_width_25_75(Vb, Sb, s_low, s_high):
    """Voltage span between the 25 % and 75 % crossings of the switching
    transition (s_low/s_high = the two saturation levels)."""
    if s_low is None or s_high is None or not np.isfinite(s_low) \
            or not np.isfinite(s_high) or s_high == s_low:
        return None
    span = s_high - s_low
    v25 = _best_zero_crossing(Vb, np.asarray(Sb, float) - (s_low + 0.25 * span))
    v75 = _best_zero_crossing(Vb, np.asarray(Sb, float) - (s_low + 0.75 * span))
    if v25 is None or v75 is None:
        return None
    return float(abs(v75 - v25))


def _wrap180(deg):
    return (np.asarray(deg, float) + 180.0) % 360.0 - 180.0


def fit_poling_kinetics(t_s, mag_v):
    """Stretched-exponential fit of the poling monitor:
    M(t) = M_inf - (M_inf - M0) * exp(-(t/tau)^beta).

    tau is the per-pixel poling (domain-alignment) time constant; beta < 1
    indicates dispersive/creep-like kinetics. Requires scipy; raises
    ValueError when under-determined or the fit fails.
    """
    if not _HAVE_SCIPY:
        raise ValueError("scipy required for poling-kinetics fit")
    t = np.asarray([float(x) for x in t_s], float)
    m = np.asarray([float(x) for x in mag_v], float)
    ok = np.isfinite(t) & np.isfinite(m)
    t, m = t[ok], m[ok]
    if len(t) < 5:
        raise ValueError("Need >= 5 finite poling samples")

    def model(tt, m_inf, m0, tau, beta):
        return m_inf - (m_inf - m0) * np.exp(-(np.maximum(tt, 0.0)
                                               / max(tau, 1e-6)) ** beta)

    p0 = [float(m[-1]), float(m[0]), max(float(t[-1]) / 3.0, 1.0), 1.0]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", OptimizeWarning)
            popt, _pcov = curve_fit(
                model, t, m, p0=p0,
                bounds=(
                    [-np.inf, -np.inf, 1e-3, 0.2],
                    [np.inf, np.inf, 1e5, 2.0],
                ),
                maxfev=20000,
            )
    except Exception as exc:
        raise ValueError(f"Poling-kinetics fit failed: {exc}") from exc
    resid = m - model(t, *popt)
    ss_tot = float(np.sum((m - m.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid ** 2)) / ss_tot if ss_tot > 0 else float("nan")
    return {
        "m_inf_V": float(popt[0]),
        "m0_V": float(popt[1]),
        "tau_s": float(popt[2]),
        "beta": float(popt[3]),
        "r_squared": r2,
        "n_points": int(len(t)),
    }


def load_poling_kinetics_csv(path):
    """Parse a poling_kinetics.csv -> (t_s, mag_V) arrays."""
    t_vals, m_vals = [], []
    with open(path, "r") as f:
        header = None
        for line in f:
            parts = [p.strip() for p in line.strip().split(",")]
            if header is None:
                header = parts
                try:
                    it = header.index("t_s")
                    im = header.index("lockin_mag_V")
                except ValueError as exc:
                    raise ValueError(f"{path}: missing columns") from exc
                continue
            try:
                t_vals.append(float(parts[it]))
                m_vals.append(float(parts[im]))
            except (ValueError, IndexError):
                continue
    return np.asarray(t_vals), np.asarray(m_vals)


def _tanh_branch_fit(Vb, Sb):
    """Fit S = a + b*V + s_s*tanh((V - v_c)/w); returns dict or None."""
    if not _HAVE_SCIPY:
        return None
    Vb = np.asarray(Vb, float)
    Sb = np.asarray(Sb, float)
    ok = np.isfinite(Vb) & np.isfinite(Sb)
    Vb, Sb = Vb[ok], Sb[ok]
    if len(Vb) < 6:
        return None

    def model(v, a, b, s_s, v_c, w):
        return a + b * v + s_s * np.tanh((v - v_c) / max(abs(w), 1e-9))

    vc0 = _best_zero_crossing(Vb, Sb) or 0.0
    s0 = (np.nanmax(Sb) - np.nanmin(Sb)) / 2.0 or 1e-9
    try:
        with warnings.catch_warnings():
            # A degenerate/near-linear branch can have a valid best fit while
            # its parameter covariance is not identifiable.  We already
            # report that uncertainty as NaN below, so avoid printing a
            # traceback-looking warning into the acquisition log.
            warnings.simplefilter("ignore", OptimizeWarning)
            popt, pcov = curve_fit(
                model, Vb, Sb, p0=[0.0, 0.0, s0, vc0, 2.0],
                maxfev=20000,
            )
        resid = Sb - model(Vb, *popt)
        ss_res = float(np.sum(resid ** 2))
        ss_tot = float(np.sum((Sb - Sb.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        perr = np.sqrt(np.diag(pcov)) if np.all(np.isfinite(pcov)) else [np.nan] * 5
        return {
            "offset_V": float(popt[0]),
            "linear_V_per_V": float(popt[1]),
            "s_sat_V": float(popt[2]),
            "v_c_V": float(popt[3]),
            "width_V": float(abs(popt[4])),
            "v_c_err_V": float(perr[3]),
            "r_squared": r2,
        }
    except Exception:
        return None


# ========================= loop classification =========================

def _cycle_branch_masks(data, cycle_number):
    V = data["V"]
    base = np.array([_branch_base(b) for b in data["branch"]])
    cyc = data["cycle"]
    good = (
        (data["tripped"] == 0)
        & (data.get("lockin_invalid", np.zeros(len(V), dtype=int)) == 0)
        & np.isfinite(V)
    )
    m_down = good & (base == "down") & (cyc == cycle_number)
    m_up = good & (base == "up") & (cyc == cycle_number)
    return m_down, m_up, good


def _loop_opening(V, S, m_down, m_up, n_grid=121):
    """Opening profile ΔS(V) = S_down - S_up on a common voltage grid.

    For a normal ferroelectric loop this is a single hump between the two
    coercive voltages; a pinched (constricted) loop shows two humps with a
    dip near the constriction.
    """
    Vd, Sd = V[m_down], S[m_down]
    Vu, Su = V[m_up], S[m_up]
    if len(Vd) < MIN_BRANCH_POINTS or len(Vu) < MIN_BRANCH_POINTS:
        return None, None
    lo = max(np.min(Vd), np.min(Vu))
    hi = min(np.max(Vd), np.max(Vu))
    if not (hi > lo):
        return None, None
    grid = np.linspace(lo, hi, n_grid)
    od, ou = np.argsort(Vd), np.argsort(Vu)
    opening = np.interp(grid, Vd[od], Sd[od]) - np.interp(grid, Vu[ou], Su[ou])
    # light smoothing (~5 grid points) to suppress point-noise humps
    kernel = np.ones(5) / 5.0
    opening = np.convolve(opening, kernel, mode="same")
    return grid, opening


def _opening_pinch_info(grid, opening,
                        dip_fraction=CLASSIFY_PINCH_DIP_FRACTION):
    """Return (is_pinched, n_humps, dip_over_hump) from the opening profile."""
    if grid is None or opening is None:
        return False, 0, None
    peak = float(np.nanmax(opening))
    if not np.isfinite(peak) or peak <= 0:
        return False, 0, None
    thr = 0.3 * peak
    humps = []
    for i in range(1, len(opening) - 1):
        if (opening[i] >= opening[i - 1] and opening[i] >= opening[i + 1]
                and opening[i] >= thr):
            if humps and i - humps[-1] <= 3:
                if opening[i] > opening[humps[-1]]:
                    humps[-1] = i
                continue
            humps.append(i)
    if len(humps) < 2:
        return False, len(humps), None
    top2 = sorted(sorted(humps, key=lambda i: opening[i], reverse=True)[:2])
    i1, i2 = top2
    dip = float(np.min(opening[i1:i2 + 1]))
    smaller_hump = min(float(opening[i1]), float(opening[i2]))
    ratio = dip / smaller_hump if smaller_hump > 0 else None
    pinched = ratio is not None and ratio < dip_fraction
    return pinched, len(humps), ratio


def classify_loop(data, cycle_number, phi_ref_deg, metrics,
                  min_switchable_V=CLASSIFY_MIN_SWITCHABLE_V):
    """Classify the loop shape into a material-behavior category.

    Primary types:
      ferroelectric_square / _slanted / _rounded - switching loop, graded by
          squareness (slanted/rounded = broad coercive-field distribution);
      pinched        - constricted loop (defect pinning / internal bias
                       pair / antiferroelectric-like);
      linear_no_hysteresis - EO response without a loop (paraelectric-like
                       or fully reversible);
      frozen_response - response present but non-switchable within +/-Vmax;
      partial_loop_unresolved - hysteretic but coercive voltages not both
                       inside the sweep range;
      no_response / invalid_projection / unclassified - data-quality bins.
    Modifiers: imprinted, partially_frozen, leaky, drifting_loop,
    saturation_asymmetric.
    """
    if metrics is None:
        return {"primary": "unclassified", "modifiers": [],
                "reasons": {"error": "no complete down+up cycle"}}

    S, _Q = signed_projection(data["X"], data["Y"], phi_ref_deg)
    m_down, m_up, _good = _cycle_branch_masks(data, cycle_number)
    m_down &= np.isfinite(S)
    m_up &= np.isfinite(S)
    m_cycle = m_down | m_up

    switchable = metrics.get("switchable_V")
    frozen = metrics.get("frozen_V")
    qf = metrics.get("quadrature_fraction")
    width = metrics.get("loop_width_V")
    imprint = metrics.get("imprint_V")
    closure = metrics.get("loop_closure_V")
    sat_asym = metrics.get("sat_asymmetry")
    leak = (metrics.get("leakage") or {}).get("conductance_S") \
        if isinstance(metrics.get("leakage"), dict) else None

    amp = float(np.nanmax(np.abs(S[m_cycle]))) if np.any(m_cycle) else 0.0
    grid, opening = _loop_opening(data["V"], S, m_down, m_up)
    pinched, n_humps, dip_ratio = _opening_pinch_info(grid, opening)
    max_opening = float(np.nanmax(opening)) if opening is not None else 0.0
    # opening significance: a pinch/loop verdict is only meaningful when the
    # branches actually separate by a real fraction of the switchable range -
    # otherwise the "humps" are noise on a closed (linear) trace.
    rel_open = (max_opening / (2.0 * abs(switchable))
                if switchable and np.isfinite(switchable) and switchable != 0.0
                else 0.0)

    reasons = {
        "max_abs_S_V": amp,
        "max_opening_V": max_opening,
        "relative_opening": rel_open,
        "n_opening_humps": int(n_humps),
        "opening_dip_over_hump": dip_ratio,
    }
    modifiers: list[str] = []

    if amp < min_switchable_V:
        primary = "no_response"
    elif qf is not None and np.isfinite(qf) and qf > CLASSIFY_MAX_QUADRATURE:
        primary = "invalid_projection"
    elif switchable is None or not np.isfinite(switchable) \
            or abs(switchable) < min_switchable_V:
        primary = ("frozen_response"
                   if frozen is not None and np.isfinite(frozen)
                   and abs(frozen) >= min_switchable_V else "no_response")
    elif pinched and rel_open >= CLASSIFY_OPEN_FRACTION_LINEAR:
        primary = "pinched"
    elif metrics.get("v_c_plus_V") is None or metrics.get("v_c_minus_V") is None:
        primary = ("linear_no_hysteresis"
                   if rel_open < CLASSIFY_OPEN_FRACTION_LINEAR
                   else "partial_loop_unresolved")
    elif (width is not None and np.isfinite(width)
          and abs(width) < CLASSIFY_MIN_WIDTH_V) \
            or rel_open < CLASSIFY_OPEN_FRACTION_LINEAR:
        primary = "linear_no_hysteresis"
    else:
        sq_vals = [abs(metrics.get(k)) for k in ("squareness_pos", "squareness_neg")
                   if metrics.get(k) is not None
                   and np.isfinite(metrics.get(k))]
        sq = float(np.mean(sq_vals)) if sq_vals else float("nan")
        reasons["mean_squareness"] = sq
        if np.isfinite(sq) and sq >= CLASSIFY_SQUARENESS_SQUARE:
            primary = "ferroelectric_square"
        elif np.isfinite(sq) and sq >= CLASSIFY_SQUARENESS_SLANTED:
            primary = "ferroelectric_slanted"
        else:
            primary = "ferroelectric_rounded"

    if primary.startswith("ferroelectric") or primary == "pinched":
        half_width = abs(width) / 2.0 if width is not None and np.isfinite(width) else 0.0
        if imprint is not None and np.isfinite(imprint) and abs(imprint) > max(
                CLASSIFY_IMPRINT_MIN_V, CLASSIFY_IMPRINT_FRACTION * half_width):
            modifiers.append("imprinted")
        if (frozen is not None and switchable and np.isfinite(frozen)
                and abs(frozen / switchable) > CLASSIFY_FROZEN_RATIO):
            modifiers.append("partially_frozen")
        if (closure is not None and switchable and np.isfinite(closure)
                and abs(closure / switchable) > CLASSIFY_DRIFT_FRACTION):
            modifiers.append("drifting_loop")
        if sat_asym is not None and np.isfinite(sat_asym) \
                and abs(sat_asym) > CLASSIFY_SAT_ASYM:
            modifiers.append("saturation_asymmetric")
    if leak is not None and np.isfinite(leak) and abs(leak) > CLASSIFY_LEAKY_S:
        modifiers.append("leaky")

    return {
        "primary": primary,
        "short_code": TYPE_SHORT_CODES.get(primary, "?"),
        "modifiers": modifiers,
        "reasons": reasons,
    }


def coercive_fields(metrics, gap_um, field_correction=1.0):
    """Convert coercive/imprint voltages to fields: E = alpha * V / gap.

    Returns V/um and kV/cm (1 V/um = 10 kV/cm). gap and alpha must be the
    MEASURED electrode gap and the FEM field-correction factor for this
    device (see the physics audit) - do not guess them.
    """
    g = float(gap_um)
    a = float(field_correction)
    if g <= 0:
        raise ValueError("gap_um must be positive")

    def conv(key):
        v = metrics.get(key)
        if v is None or not np.isfinite(v):
            return None
        return a * float(v) / g

    out = {
        "gap_um": g,
        "field_correction": a,
        "units": "E = alpha*V/gap; 1 V/um = 10 kV/cm",
    }
    for src, dst in (("v_c_plus_V", "e_c_plus"), ("v_c_minus_V", "e_c_minus"),
                     ("loop_width_V", "e_width"), ("imprint_V", "e_imprint")):
        val = conv(src)
        out[dst + "_V_per_um"] = val
        out[dst + "_kV_per_cm"] = None if val is None else val * 10.0
    return out


# ============================ loop metrics ==============================

def loop_metrics_for_cycle(data, cycle_number, phi_ref_deg,
                           sat_fraction=SAT_FRACTION_DEFAULT):
    """Metrics for one full down+up cycle. Tripped points are excluded."""
    V = data["V"]
    S, Q = signed_projection(data["X"], data["Y"], phi_ref_deg)
    base = np.array([_branch_base(b) for b in data["branch"]])
    cyc = data["cycle"]
    good = (
        (data["tripped"] == 0)
        & (data.get("lockin_invalid", np.zeros(len(V), dtype=int)) == 0)
        & np.isfinite(V)
        & np.isfinite(S)
    )

    m_down = good & (base == "down") & (cyc == cycle_number)
    m_up = good & (base == "up") & (cyc == cycle_number)
    if m_down.sum() < MIN_BRANCH_POINTS or m_up.sum() < MIN_BRANCH_POINTS:
        return None

    vmax = float(np.nanmax(np.abs(V[good])))
    sat_thr = sat_fraction * vmax
    m_cycle = m_down | m_up
    m_sat_pos = m_cycle & (V >= sat_thr)
    m_sat_neg = m_cycle & (V <= -sat_thr)

    s_sat_pos = float(np.mean(S[m_sat_pos])) if np.any(m_sat_pos) else float("nan")
    s_sat_neg = float(np.mean(S[m_sat_neg])) if np.any(m_sat_neg) else float("nan")

    v_c_minus = _best_zero_crossing(V[m_down], S[m_down])
    v_c_plus = _best_zero_crossing(V[m_up], S[m_up])

    s_rem_pos = _interp_at(V[m_down], S[m_down], 0.0)  # descending from +sat
    s_rem_neg = _interp_at(V[m_up], S[m_up], 0.0)      # ascending from -sat

    # closed-loop signed area, traversed in acquisition order
    idx_cycle = np.where(m_cycle)[0]
    area = float(abs(_trapezoid(S[idx_cycle], V[idx_cycle]))) \
        if len(idx_cycle) > 2 else float("nan")

    switchable = ((s_sat_pos - s_sat_neg) / 2.0
                  if np.isfinite(s_sat_pos) and np.isfinite(s_sat_neg) else None)
    frozen = ((s_sat_pos + s_sat_neg) / 2.0
              if np.isfinite(s_sat_pos) and np.isfinite(s_sat_neg) else None)

    # Field-induced (quadratic-EO) slope from the saturation tails; its ratio
    # to the switchable amplitude is a paraelectric-fraction / phase-boundary
    # proximity indicator (composition-sensitive). The switchable amplitude
    # used for the ratio has the reversible linear term subtracted first, so
    # it is the pure hysteron amplitude rather than tail means inflated by
    # the field-induced contribution.
    quad_slope = _tail_linear_slope(V, S, m_sat_pos, m_sat_neg)
    switchable_corrected = switchable
    if quad_slope is not None:
        s_corr = S - quad_slope * V
        pos_c = float(np.mean(s_corr[m_sat_pos])) if np.any(m_sat_pos) else float("nan")
        neg_c = float(np.mean(s_corr[m_sat_neg])) if np.any(m_sat_neg) else float("nan")
        if np.isfinite(pos_c) and np.isfinite(neg_c):
            switchable_corrected = (pos_c - neg_c) / 2.0
    quad_ratio = (abs(quad_slope) * vmax / abs(switchable_corrected)
                  if quad_slope is not None and switchable_corrected
                  and np.isfinite(switchable_corrected)
                  and switchable_corrected != 0.0 else None)

    dist_dn = _switching_distribution(V[m_down], S[m_down],
                                      baseline_slope=quad_slope or 0.0)
    dist_up = _switching_distribution(V[m_up], S[m_up],
                                      baseline_slope=quad_slope or 0.0)

    def _mean_of(key, *dicts):
        vals = [d[key] for d in dicts
                if d and d.get(key) is not None and np.isfinite(d[key])]
        return float(np.mean(vals)) if vals else None

    h_dn = dist_dn.get("peak_V_per_V") if dist_dn else None
    h_up = dist_up.get("peak_V_per_V") if dist_up else None
    nucleation_asymmetry = (
        (h_up - h_dn) / ((h_up + h_dn) / 2.0)
        if h_dn and h_up and (h_up + h_dn) > 0 else None)

    # butterfly minima as a cross-check on the coercive voltages
    R = data["R"]

    def _butterfly_min(mask):
        if mask.sum() < 3:
            return None
        i = np.nanargmin(R[mask])
        return float(V[mask][i])

    def _butterfly_contrast(mask):
        """min|R| over the branch relative to its saturation-tail |R|:
        near 0 = complete domain cancellation at Vc (clean 180-deg
        switching); large = incomplete/partial switching."""
        m_tail = mask & (np.abs(V) >= sat_thr) & np.isfinite(R)
        if mask.sum() < 4 or m_tail.sum() < 2:
            return None
        r_sat = float(np.nanmean(R[m_tail]))
        if not np.isfinite(r_sat) or r_sat <= 0:
            return None
        return float(np.nanmin(R[mask]) / r_sat)

    bc_dn = _butterfly_contrast(m_down)
    bc_up = _butterfly_contrast(m_up)
    tw_dn = _transition_width_25_75(V[m_down], S[m_down], s_sat_neg, s_sat_pos)
    tw_up = _transition_width_25_75(V[m_up], S[m_up], s_sat_neg, s_sat_pos)

    def _phase_intermediate_fraction(mask, vc, half_window_v=7.5):
        """Fraction of coercive-window points whose phasor phase sits between
        the two domain states (45-135 deg off the reference axis): ~0 for an
        abrupt 180-deg flip, larger for gradual multi-domain rotation."""
        if vc is None:
            return None
        m_win = mask & (np.abs(V - float(vc)) <= float(half_window_v))
        if m_win.sum() < 3:
            return None
        rel = np.abs(_wrap180(data["phase_deg"][m_win] - phi_ref_deg))
        return float(np.mean((rel > 45.0) & (rel < 135.0)))

    # loop closure: S at +Vmax at the start of "down" vs the end of "up"
    closure = None
    try:
        s_start = S[m_down][int(np.argmax(V[m_down]))]
        s_end = S[m_up][int(np.argmax(V[m_up]))]
        closure = float(abs(s_end - s_start))
    except Exception:
        pass

    metrics = {
        "cycle": int(cycle_number),
        "phi_ref_deg": float(phi_ref_deg),
        "v_c_plus_V": v_c_plus,
        "v_c_minus_V": v_c_minus,
        "loop_width_V": (v_c_plus - v_c_minus
                         if v_c_plus is not None and v_c_minus is not None
                         else None),
        "imprint_V": ((v_c_plus + v_c_minus) / 2.0
                      if v_c_plus is not None and v_c_minus is not None
                      else None),
        "s_rem_pos_V": s_rem_pos,
        "s_rem_neg_V": s_rem_neg,
        "s_sat_pos_V": s_sat_pos,
        "s_sat_neg_V": s_sat_neg,
        "squareness_pos": (s_rem_pos / s_sat_pos
                           if s_rem_pos is not None and s_sat_pos
                           and np.isfinite(s_sat_pos) and s_sat_pos != 0
                           else None),
        "squareness_neg": (s_rem_neg / s_sat_neg
                           if s_rem_neg is not None and s_sat_neg
                           and np.isfinite(s_sat_neg) and s_sat_neg != 0
                           else None),
        "sat_asymmetry": (
            (abs(s_sat_pos) - abs(s_sat_neg))
            / ((abs(s_sat_pos) + abs(s_sat_neg)) / 2.0)
            if np.isfinite(s_sat_pos) and np.isfinite(s_sat_neg)
            and (abs(s_sat_pos) + abs(s_sat_neg)) > 0 else None),
        "switchable_V": switchable,
        "frozen_V": frozen,
        "loop_area_V2": area,
        "loop_closure_V": closure,
        "switching_slope_down": dist_dn,
        "switching_slope_up": dist_up,
        "switching_sigma_V": _mean_of("sigma_V", dist_dn, dist_up),
        "switching_skewness": _mean_of("skewness", dist_dn, dist_up),
        "nucleation_asymmetry": nucleation_asymmetry,
        "quad_eo_slope_V_per_V": quad_slope,
        "quad_eo_ratio": quad_ratio,
        "switchable_corrected_V": (
            float(switchable_corrected)
            if switchable_corrected is not None
            and np.isfinite(switchable_corrected) else None),
        "butterfly_min_down_V": _butterfly_min(m_down),
        "butterfly_min_up_V": _butterfly_min(m_up),
        "butterfly_min_over_sat_down": bc_dn,
        "butterfly_min_over_sat_up": bc_up,
        "butterfly_min_over_sat": (
            float(np.mean([b for b in (bc_dn, bc_up) if b is not None]))
            if any(b is not None for b in (bc_dn, bc_up)) else None),
        "transition_width_25_75_down_V": tw_dn,
        "transition_width_25_75_up_V": tw_up,
        "transition_width_25_75_V": (
            float(np.mean([w for w in (tw_dn, tw_up) if w is not None]))
            if any(w is not None for w in (tw_dn, tw_up)) else None),
        "phase_intermediate_fraction_down":
            _phase_intermediate_fraction(m_down, v_c_minus),
        "phase_intermediate_fraction_up":
            _phase_intermediate_fraction(m_up, v_c_plus),
        "tanh_fit_down": _tanh_branch_fit(V[m_down], S[m_down]),
        "tanh_fit_up": _tanh_branch_fit(V[m_up], S[m_up]),
        "quadrature_fraction": (
            float(np.nanmax(np.abs(Q[m_cycle])) / np.nanmax(np.abs(S[m_cycle])))
            if np.nanmax(np.abs(S[m_cycle])) > 0 else None),
        "n_points": int(m_cycle.sum()),
        "n_tripped_excluded": int(((data["tripped"] != 0)
                                   & (cyc == cycle_number)).sum()),
        "n_lockin_overload_excluded": int((
            (data.get("lockin_invalid", np.zeros(len(V), dtype=int)) != 0)
            & (cyc == cycle_number)
        ).sum()),
    }

    # Leakage from the saturation tails (steady-state; no switching there)
    I = data["I_smu"]
    m_leak = m_cycle & np.isfinite(I) & (np.abs(V) >= sat_thr)
    if m_leak.sum() >= 4:
        G, I0 = np.polyfit(V[m_leak], I[m_leak], 1)
        metrics["leakage"] = {
            "conductance_S": float(G),
            "offset_A": float(I0),
            "i_at_pos_sat_A": float(np.mean(I[m_sat_pos & np.isfinite(I)]))
            if np.any(m_sat_pos & np.isfinite(I)) else None,
            "i_at_neg_sat_A": float(np.mean(I[m_sat_neg & np.isfinite(I)]))
            if np.any(m_sat_neg & np.isfinite(I)) else None,
            "i_max_abs_A": float(np.nanmax(np.abs(I[m_cycle]))),
        }
    else:
        metrics["leakage"] = None

    # DC transmission (electro-absorption / thermal cross-check)
    P = data["P_dc"]
    m_p = m_cycle & np.isfinite(P)
    if np.any(m_p):
        metrics["p_dc"] = {
            "mean_W": float(np.mean(P[m_p])),
            "pp_W": float(np.max(P[m_p]) - np.min(P[m_p])),
        }
        p_null = _meta_float(data.get("meta", {}), "DC_fringe_null_power_W")
        p_bright = _meta_float(data.get("meta", {}), "DC_fringe_bright_power_W")
        p_quadrature = _meta_float(
            data.get("meta", {}), "DC_fringe_quadrature_power_W"
        )
        if (
            p_null is not None
            and p_bright is not None
            and p_bright > p_null
        ):
            fractions = (P[m_p] - p_null) / (p_bright - p_null)
            finite_fractions = fractions[np.isfinite(fractions)]
            if finite_fractions.size:
                errors = np.abs(finite_fractions - 0.5)
                metrics["p_dc"].update({
                    "reference_null_W": float(p_null),
                    "reference_quadrature_W": (
                        float(p_quadrature)
                        if p_quadrature is not None else None
                    ),
                    "reference_bright_W": float(p_bright),
                    "normalized_fringe_fraction_mean": float(
                        np.mean(finite_fractions)
                    ),
                    "normalized_fringe_fraction_min": float(
                        np.min(finite_fractions)
                    ),
                    "normalized_fringe_fraction_max": float(
                        np.max(finite_fractions)
                    ),
                    "max_half_fringe_error": float(np.max(errors)),
                    "fraction_outside_half_fringe_tolerance": float(
                        np.mean(errors > DC_HALF_FRINGE_TOLERANCE)
                    ),
                    "half_fringe_tolerance": float(
                        DC_HALF_FRINGE_TOLERANCE
                    ),
                })
    else:
        metrics["p_dc"] = None

    return metrics


# =========================== top-level API ==============================

def analyse_loop_file(csv_path, sat_fraction=SAT_FRACTION_DEFAULT,
                      gap_um=None, field_correction=1.0):
    """Analyse a dc_hysteresis.csv; returns a JSON-serializable result dict.

    When available, the phase axis certified by the preceding chip sweep is
    used and cross-checked against the positive-saturation tail. Older files
    fall back to the saturation-derived axis. The sign is fixed so S(+Vmax)
    > 0. Metrics are computed for every cycle; ``metrics`` points at the last
    (steady) cycle. The last cycle is also shape-classified
    (``classification``), and when gap_um (and
    optionally field_correction alpha) is given, coercive voltages are
    converted to fields in a ``fields`` block.
    """
    data = load_dc_hysteresis_csv(csv_path)
    good = (
        (data["tripped"] == 0)
        & (
            data.get(
                "lockin_invalid",
                np.zeros(len(data["V"]), dtype=int),
            )
            == 0
        )
    )
    saturation_phi_ref = saturation_phase_deg(
        data["V"][good], data["X"][good], data["Y"][good], sat_fraction)
    calibrated_phi_ref = _meta_float(
        data["meta"], "EO_lockin_reference_phase_deg"
    )
    if calibrated_phi_ref is not None:
        phi_ref = float(calibrated_phi_ref) % 360.0
        phase_reference_source = "calibrated_chip_sweep_eo_axis"
    else:
        phi_ref = float(saturation_phi_ref)
        phase_reference_source = "positive_saturation_tail"
    S, _ = signed_projection(data["X"], data["Y"], phi_ref)
    vmax = float(np.nanmax(np.abs(data["V"])))
    m_pos = data["V"] >= sat_fraction * vmax
    if np.nanmean(S[m_pos & good]) < 0:
        phi_ref = (phi_ref + 180.0) % 360.0
    phase_axis_delta = abs(
        (float(phi_ref) - float(saturation_phi_ref) + 90.0) % 180.0 - 90.0
    )

    cycles = sorted({int(c) for c, b in zip(data["cycle"], data["branch"])
                     if _branch_base(b) in ("down", "up")})
    per_cycle = []
    for c in cycles:
        m = loop_metrics_for_cycle(data, c, phi_ref, sat_fraction)
        if m is not None:
            per_cycle.append(m)
    if not per_cycle:
        raise ValueError(f"No complete down+up cycle found in {csv_path}")

    wake_up = None
    if len(per_cycle) >= 2:
        first, last = per_cycle[0], per_cycle[-1]
        if (first.get("loop_width_V") is not None
                and last.get("loop_width_V") is not None):
            wake_up = {
                "loop_width_change_V":
                    last["loop_width_V"] - first["loop_width_V"],
                "imprint_change_V":
                    (last["imprint_V"] - first["imprint_V"])
                    if first.get("imprint_V") is not None
                    and last.get("imprint_V") is not None else None,
            }

    last = per_cycle[-1]
    classification = classify_loop(data, last["cycle"], phi_ref, last)
    classification.setdefault("modifiers", [])
    classification.setdefault("reasons", {})
    classification["reasons"]["phase_reference_axis_delta_deg"] = float(
        phase_axis_delta
    )
    if (
        calibrated_phi_ref is not None
        and phase_axis_delta > PHASE_REFERENCE_AXIS_WARN_DEG
        and "calibration_phase_mismatch" not in classification["modifiers"]
    ):
        classification["modifiers"].append("calibration_phase_mismatch")
    p_dc_metrics = last.get("p_dc") or {}
    outside_fraction = p_dc_metrics.get(
        "fraction_outside_half_fringe_tolerance"
    )
    max_half_error = p_dc_metrics.get("max_half_fringe_error")
    if (
        (outside_fraction is not None and outside_fraction > 0.10)
        or (max_half_error is not None and max_half_error > 0.15)
    ) and "dc_quadrature_departure" not in classification["modifiers"]:
        classification["modifiers"].append("dc_quadrature_departure")
    result = {
        "source_csv": os.path.abspath(csv_path),
        "meta": data["meta"],
        "phi_ref_deg": float(phi_ref),
        "phase_reference_source": phase_reference_source,
        "saturation_phase_reference_deg": float(saturation_phi_ref),
        "phase_reference_axis_delta_deg": float(phase_axis_delta),
        "sat_fraction": float(sat_fraction),
        "n_cycles_analysed": len(per_cycle),
        "per_cycle": per_cycle,
        "metrics": last,
        "classification": classification,
        "cycle_to_cycle": wake_up,
        "units_note": ("response metrics are lock-in volts (RMS); multiply by "
                       "1e6 for uV. Signed S(V) is the projection onto the "
                       "positive-saturation phase axis."),
    }
    if gap_um is not None:
        result["fields"] = coercive_fields(last, gap_um, field_correction)
    return result


def detect_coercive_windows(csv_path, half_width_v=7.5, vmax=50.0,
                            sat_fraction=SAT_FRACTION_DEFAULT):
    """Fine-grid windows centred on the coercive voltages of a recon loop.

    Used by the acquisition engine's adaptive mode. Raises if either branch
    shows no signed zero crossing (caller falls back to the fixed window).
    """
    result = analyse_loop_file(csv_path, sat_fraction=sat_fraction)
    m = result["metrics"]
    vcs = [m.get("v_c_minus_V"), m.get("v_c_plus_V")]
    if any(v is None for v in vcs):
        raise ValueError("Coercive voltage not detected on both branches")
    windows = []
    for vc in vcs:
        lo = max(-abs(vmax), vc - abs(half_width_v))
        hi = min(abs(vmax), vc + abs(half_width_v))
        windows.append((float(lo), float(hi)))
    windows.sort()
    return windows


def plot_loop(result, csv_path=None, out_png=None):
    """4-panel loop figure: S(V), |R|(V), I(V) (or Q), P_dc(V)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = load_dc_hysteresis_csv(csv_path or result["source_csv"])
    phi_ref = result["phi_ref_deg"]
    S, Q = signed_projection(data["X"], data["Y"], phi_ref)
    V = data["V"]
    base = np.array([_branch_base(b) for b in data["branch"]])
    cyc = data["cycle"]
    m = result["metrics"]

    colors = {"virgin": "tab:purple", "sat": "tab:orange",
              "down": "tab:red", "up": "tab:green"}

    fig, axes = plt.subplots(2, 2, figsize=(12, 9))
    ax_s, ax_r, ax_i, ax_p = axes.ravel()

    for b in dict.fromkeys(zip(base, cyc)):
        bb, cc = b
        mask = (base == bb) & (cyc == cc)
        alpha = 1.0 if cc == max(cyc) else 0.35
        lbl = f"{bb}{cc if len(set(cyc)) > 1 else ''}"
        ax_s.plot(V[mask], S[mask] * 1e6, "o-", ms=3,
                  color=colors.get(bb, "tab:blue"), alpha=alpha, label=lbl)
        ax_r.plot(V[mask], data["R"][mask] * 1e6, "o-", ms=3,
                  color=colors.get(bb, "tab:blue"), alpha=alpha)
        if np.any(np.isfinite(data["I_smu"][mask])):
            ax_i.plot(V[mask], data["I_smu"][mask] * 1e6, "o-", ms=3,
                      color=colors.get(bb, "tab:blue"), alpha=alpha)
        if np.any(np.isfinite(data["P_dc"][mask])):
            ax_p.plot(V[mask], data["P_dc"][mask] * 1e3, "o-", ms=3,
                      color=colors.get(bb, "tab:blue"), alpha=alpha)

    for vc, style in ((m.get("v_c_plus_V"), "g--"), (m.get("v_c_minus_V"), "r--")):
        if vc is not None:
            ax_s.axvline(vc, ls=style[1:], color=style[0], lw=1)
    ax_s.axhline(0, color="k", lw=0.7)
    ax_s.axvline(0, color="k", lw=0.7)

    def _fmt(x, nd=2):
        return "n/a" if x is None else f"{x:+.{nd}f}"

    ax_s.set_title(
        f"Signed loop S(V)   Vc+={_fmt(m.get('v_c_plus_V'))} V  "
        f"Vc-={_fmt(m.get('v_c_minus_V'))} V\n"
        f"width={_fmt(m.get('loop_width_V'))} V  "
        f"imprint={_fmt(m.get('imprint_V'))} V  "
        f"S_rem+={_fmt((m.get('s_rem_pos_V') or float('nan')) * 1e6, 2)} uV",
        fontsize=10)
    ax_s.set_ylabel("S (uV)")
    ax_s.legend(fontsize=7)
    ax_r.set_title("Butterfly |R|(V)", fontsize=10)
    ax_r.set_ylabel("|R| (uV)")
    if not ax_i.lines:
        ax_i.plot(V, Q * 1e6, ".", ms=3, color="tab:gray")
        ax_i.set_title("Quadrature residual Q(V)", fontsize=10)
        ax_i.set_ylabel("Q (uV)")
    else:
        ax_i.set_title("SMU current I(V)", fontsize=10)
        ax_i.set_ylabel("I (uA)")
    ax_p.set_title("DC optical power", fontsize=10)
    ax_p.set_ylabel("P_dc (mW)")
    for metadata_key, label, style in (
        ("DC_fringe_null_power_W", "cal null", ":"),
        ("DC_fringe_quadrature_power_W", "cal half-fringe", "--"),
        ("DC_fringe_bright_power_W", "cal bright", ":"),
    ):
        reference_power = _meta_float(data.get("meta", {}), metadata_key)
        if reference_power is not None:
            ax_p.axhline(
                reference_power * 1e3,
                color="k",
                ls=style,
                lw=0.8,
                alpha=0.55,
                label=label,
            )
    if len(ax_p.lines) > 1:
        ax_p.legend(fontsize=7)
    for ax in axes.ravel():
        ax.grid(True, alpha=0.4)
        ax.set_xlabel("V_DC (V)")
    cls = result.get("classification") or {}
    type_txt = cls.get("primary", "")
    if cls.get("modifiers"):
        type_txt += " [" + ", ".join(cls["modifiers"]) + "]"
    fig.suptitle(os.path.basename(os.path.dirname(os.path.abspath(
        csv_path or result["source_csv"])))
        + (f"  -  {type_txt}" if type_txt else ""), fontsize=11)
    fig.tight_layout()
    if out_png:
        fig.savefig(out_png, dpi=150)
    plt.close(fig)
    return out_png


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


def analyse_and_save(csv_path, out_dir=None, sat_fraction=SAT_FRACTION_DEFAULT,
                     gap_um=None, field_correction=1.0):
    """Analyse + write dc_hysteresis_metrics.json and dc_hysteresis_loops.png."""
    out_dir = out_dir or os.path.dirname(os.path.abspath(csv_path))
    result = analyse_loop_file(csv_path, sat_fraction=sat_fraction,
                               gap_um=gap_um, field_correction=field_correction)

    json_path = os.path.join(out_dir, "dc_hysteresis_metrics.json")
    with open(json_path, "w") as f:
        json.dump(_jsonable(result), f, indent=2)
    print(f"  [SAVED] {json_path}")

    try:
        png_path = os.path.join(out_dir, "dc_hysteresis_loops.png")
        plot_loop(result, csv_path=csv_path, out_png=png_path)
        print(f"  [SAVED] {png_path}")
    except Exception as exc:  # plotting must never lose the metrics
        print(f"  [WARN] Loop plot failed: {exc}")

    m = result["metrics"]

    def _f(key, scale=1.0, nd=2):
        v = m.get(key)
        return "n/a" if v is None else f"{v * scale:+.{nd}f}"

    print(f"  Loop metrics (cycle {m['cycle']}): "
          f"Vc+={_f('v_c_plus_V')} V, Vc-={_f('v_c_minus_V')} V, "
          f"width={_f('loop_width_V')} V, imprint={_f('imprint_V')} V")
    print(f"    S_rem+={_f('s_rem_pos_V', 1e6)} uV, "
          f"S_rem-={_f('s_rem_neg_V', 1e6)} uV, "
          f"switchable={_f('switchable_V', 1e6)} uV, "
          f"frozen={_f('frozen_V', 1e6)} uV")
    cls = result.get("classification") or {}
    if cls:
        mods = (" [" + ", ".join(cls.get("modifiers", [])) + "]"
                if cls.get("modifiers") else "")
        print(f"    Loop type: {cls.get('primary', '?')}{mods}")
    fields = result.get("fields")
    if fields:
        def _e(key):
            v = fields.get(key)
            return "n/a" if v is None else f"{v:+.2f}"
        print(f"    Fields (gap {fields['gap_um']:g} um, alpha "
              f"{fields['field_correction']:g}): "
              f"Ec+={_e('e_c_plus_kV_per_cm')} kV/cm, "
              f"Ec-={_e('e_c_minus_kV_per_cm')} kV/cm, "
              f"imprint={_e('e_imprint_kV_per_cm')} kV/cm")
    return result


def main():
    ap = argparse.ArgumentParser(
        description="Signed-loop analysis of dc_hysteresis.csv files")
    ap.add_argument("paths", nargs="+",
                    help="dc_hysteresis.csv file(s) or glob pattern(s)")
    ap.add_argument("--sat-fraction", type=float, default=SAT_FRACTION_DEFAULT)
    ap.add_argument("--gap-um", type=float, default=None,
                    help="Measured electrode gap (um) - enables coercive-FIELD "
                         "output (E = alpha*V/gap).")
    ap.add_argument("--alpha", type=float, default=1.0,
                    help="FEM field-correction factor for E = alpha*V/gap "
                         "[default 1.0 = plain parallel-plate estimate].")
    args = ap.parse_args()

    files = []
    for p in args.paths:
        files.extend(sorted(glob.glob(p, recursive=True)) if any(
            c in p for c in "*?[") else [p])
    if not files:
        raise SystemExit("No input files matched")
    for f in files:
        print(f"\n=== {f} ===")
        try:
            analyse_and_save(f, sat_fraction=args.sat_fraction,
                             gap_um=args.gap_um, field_correction=args.alpha)
        except Exception as exc:
            print(f"  [ERROR] {exc}")


if __name__ == "__main__":
    main()
