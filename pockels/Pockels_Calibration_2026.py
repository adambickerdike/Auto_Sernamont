#!/usr/bin/env python3
"""
Pockels Calibration 2026 - incident-polarization-resolved Pockels measurement.

Performs a full voltage-series analyzer sweep at each of 7 incident
polarizations (HWP angles 0-90 deg, step 15 deg). Follows Stefan Abel's 2014
thesis sec. 3.3.2-3.3.3: delta_max is the amplitude of the sine fit to the
Pockels rotation delta vs incident polarization theta_i. The per-theta_i
analyzer sweep is the inner loop from analyser_sweep_voltage_series.py.

Per-HWP workflow:
  1. Move HWP -> theta_i
  2. Quick-null QWP+ANL (first_null_fast from pockels_campaign) so the static
     BTO birefringence is compensated for THIS theta_i. Seeded with previous
     HWP's null so convergence is fast.
  3. For each V_pp in [1, 3, 5, 7, 9]:
        - Set function generator CH1 amplitude
        - Return analyzer to the null position
        - Run analyzer sweep 15-355 deg, step 14 deg
        - Record scope DC power + lock-in mag/phase
  4. Save per-HWP CSVs + overlay PNG + null_info.json

Post-loop: fit delta(theta_i) from the lock-in sweeps and save the full
campaign summary for downstream r_eff extraction.

DC poling: SMU4201 (COM11) sources the DC bias. During the per-HWP analyzer
sweeps the SMU holds DC_POLING_V_PHASE_A (default 40 V) so the ferroelectric
domain state stays saturated and 180 deg domains do not cancel the AC signal
(Abel sec. 3.2.3).

Post-loop: find the (HWP, V_pp, analyzer-angle) point with the largest lock-in
magnitude, return all motors / funcgen there, and run a DC hysteresis sweep
+V_max -> -V_max -> +V_max using the SMU. At each DC
point the lock-in mag/phase is read with the same settle+averaging window used
in the analyzer sweep, giving a hysteresis loop of the AC Pockels response vs
DC poling voltage.
"""

import os
import sys
import json
import time
import math
import argparse
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from scipy.optimize import curve_fit

import _bootstrap  # noqa: F401  # pin CWD to the repo root so data is shared

# Hardware + primitives
from elliptec_serial import ElliptecRotator
from POL_Chip_Test_Working_2026 import (
    PORT_HWP, ADDR_HWP,
    PORT_QWP, ADDR_QWP,
    PORT_ANL, ADDR_ANL,
    MOVE_SETTLE_S,
    SCOPE_VISA, SCOPE_SOURCE,
    DetectorTekTBS,
    safe_move_abs,
    wrap180,
)

# Reuse the analyzer-sweep inner loop verbatim
from analyser_sweep_voltage_series import (
    ANL_START, ANL_STOP, ANL_STEP,
    AC_VOLTAGES_VPP,
    LOCKIN_SETTLE_S, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S,
    LOCKIN_RESOURCE, LOCKIN_TIMEOUT_MS,
    OVERLOAD_SETTLE_S,
    FUNCGEN_RESOURCE, FUNCGEN_CH_SWEEP, FUNCGEN_CH_REF,
    FUNCGEN_REF_AMPL_VPP, FUNCGEN_FREQ_HZ, FUNCGEN_SETTLE_S,
    connect_funcgen, funcgen_send, funcgen_set_voltage,
    run_single_sweep, save_sweep_csv, save_raw_samples_csv,
    read_lockin_averaged, angular_error, safe_move_wrap,
    check_overload, fmt_elapsed,
)

# DC poling source (replaces the old un-programmable bench supply)
from smu4201_iv_sweep import SMU4201

# Fast null routines
from pockels_campaign import (
    first_null_fast,
    NULL_2D_STEP_COARSE_DEG, NULL_2D_STEP_FINE_DEG,
)

from Lock_In_Mag_Phase_Track import (
    DSP7230, SENS_TABLE_VOLTS,
    format_voltage, nearest_sensitivity_index,
)
from pockels_lockin_ranging import (
    DEFAULT_RANGE_INDICES,
    PredictiveHystereticRangeController,
)


# ============================== CONFIG ==================================

# HWP sweep: 0 to 90 deg in 15 deg steps -> 7 points.
# HWP rotates polarization by 2x, so 0-90 deg HWP covers the full 0-180 deg
# incident-polarization range (one complete period of the delta(theta_i) sinusoid).
HWP_START_DEG = 0.0
HWP_STOP_DEG = 90.0
HWP_STEP_DEG = 15.0

# Override imported analyzer-sweep defaults for Pockels measurement
LOCKIN_SETTLE_S = 7.0           # longer settle for better SNR (was 4.0)
ANL_START = 15.0
ANL_STOP = 195.0
N_ANL_POINTS = 18               # 18 points over 180 deg (one full Malus period)

OUTDIR_ROOT = "pockels_calibration"

# --- DC poling (SMU4201) -------------------------------------------------
SMU_PORT = "COM13"
SMU_BAUD = 9600
SMU_NPLC = 1.0                  # NPLC for the (unused-but-still-armed) measure path
SMU_COMPLIANCE_A = 1e-3         # 1 mA default; GUI/CLI may select another limit
DC_POLING_V_PHASE_A = 40.0      # V — held during the 7x4x18 analyzer sweep
DC_HYST_VMAX = 40.0             # V — hard outer rails of every hysteresis trajectory
DC_HYST_COARSE_STEP = 5.0       # V — used outside the switching region
DC_HYST_FINE_STEP = 2.5         # V — used through the coercive region
DC_HYST_FINE_LIMIT = 10.0       # V — fine grid from -limit to +limit
DC_HYST_STEP = DC_HYST_FINE_STEP  # Legacy/minimum step reported to old callers
# Standard one-pass voltage levels requested for the BTO hysteresis campaign.
# The acquisition mirrors these absolute values about 0 V and traverses them
# from +Vmax -> -Vmax -> +Vmax.  This concentrates resolution where the recent
# coercive crossings occur without running a separate conditioning/recon loop.
DC_HYST_CENTER_DENSE_ABS_LEVELS_V = (
    0.0, 1.25, 2.5, 5.0, 7.5, 10.0, 12.5,
    15.0, 20.0, 25.0, 30.0, 40.0,
)
DC_HYST_DEFAULT_GRID_PROFILE = "center_dense"
DC_HYSTERESIS_LIVE_JSON = "dc_hysteresis_live.json"
DC_RAMP_PRESET_S = 0.3          # short pause after SMU set_voltage() before poling dwell
DC_POLING_DWELL_S = 30.0        # extra hold AT each DC step BEFORE lock-in window (poling time)
DC_HYST_MIN_POLING_DWELL_S = 30.0  # default floor; explicit min_dwell_s overrides (v2)
DC_HYST_DEFAULT_AC_VPP = 4.0       # small-signal hysteresis probe; AC only during read window
DC_HYST_DYNAMIC_RANGE_INDICES = DEFAULT_RANGE_INDICES
DC_HYST_DYNAMIC_RANGE_TARGET_FRACTION = 0.60
DC_HYST_DYNAMIC_RANGE_RESCUE_FRACTION = 0.85
DC_HYST_DYNAMIC_RANGE_SAFETY_SIGMA = 4.0
DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V = 0.10e-6
DC_HYST_DYNAMIC_RANGE_NARROW_CONFIRMATIONS = 2
DC_HYST_DYNAMIC_RANGE_HOLD_POINTS = 2
DC_HYST_DYNAMIC_RANGE_MAX_RESCUES = 3
DC_HYST_RECON_DWELL_S = 2.0        # dwell used by the adaptive coarse recon loop (v2)
DC_HYST_ADAPTIVE_HALF_WIDTH_V = 7.5  # fine-grid half-width around each detected V_c (v2)
SMU_RAMP_STEP_V = 5.0           # ramp ramp-on/ramp-off in chunks (avoid step transients)
SMU_RAMP_DWELL_S = 0.2
SMU_DC_READBACK_TOLERANCE_V = 0.5  # abort if DC-only measured V misses target by more
SMU_DC_READBACK_ATTEMPTS = 3     # reject a point if live DC telemetry stays unavailable
SMU_DC_READBACK_RETRY_S = 0.1
AC_VPP_PRE_MEASURE_DWELL_S = 60.0  # DC/AC hold before each fixed-peak AC Vpp read

# --- Domain-reset depoling (SMU PULSe shape) ----------------------------
# Bipolar pulse train with envelope amplitude decaying from RESET_VMAX -> RESET_VMIN
# over RESET_AMP_STEPS amplitudes, RESET_CYCLES_PER_AMP pulses per amplitude.
# Designed to take ~30 s wall-clock and deliver ~10^4 bipolar reversals,
# emulating Eltes 2022 Nat. Photonics's "alternating-field erase" step
# (s41566-022-01003-0).
RESET_VMAX = 40.0               # never exceed the hysteresis voltage ceiling
RESET_VMIN = 0.05               # envelope freezes near zero field
RESET_AMP_STEPS = 30            # amplitudes through coercive region
RESET_DECAY = "exponential"     # "exponential" or "linear"
RESET_CYCLES_PER_AMP = 200      # cycles at each amplitude
# The SMU shows a multi-second "Counts/Shapes" banner each time OUTPut is
# enabled, during which the rails are not fully on. To avoid that, we keep
# OUTPut ON throughout the reset and software-step the bipolar pulses with
# set_voltage() instead of using SHAPe PULSe + OUTPut toggling.
RESET_PULSE_FIRST_MS = 5.0      # explicit sleep at +V_n (+SCPI roundtrip)
RESET_PULSE_SECOND_MS = 5.0     # explicit sleep at -V_n
SMU_OUTPUT_BANNER_S = 5.0       # wait time after a fresh OUTPut:STATe ON

# Slew rate for SMU voltage changes. SOURce:VOLTage:SLEW:MAXimum gives the
# instrument's full slew which, into the BTO device's small capacitance,
# produces transient currents (i = C dV/dt) that can trip the 1 mA compliance
# limit. 50 V/ms keeps the same effective domain-switching capability for the
# reset (50 V swing in 1 ms is still ms-scale, well above ferroelectric
# switching time which is sub-microsecond) but caps i at C * 50 V/ms - for
# C ~ 1 nF that's only 50 uA, comfortably below 1 mA compliance.
SMU_SLEW_RATE_V_PER_MS = 50.0

# Default calibration to auto-load at startup. Path is resolved relative to
# this script's directory so it works identically on Windows (D:\...) and
# Linux/WSL (/mnt/d/...). Override by typing a different path at the prompt.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CAL_PATH = os.path.join(
    _THIS_DIR, "pockels_campaign",
    "20260417_104823", "first_null_reference.json"
)

# Where 3-step calibration JSONs are written. Used by the loader to
# auto-find the most recent calibration so the user can skip the full
# QWP-out/QWP-in/sample-in physical sequence.
CALIBRATION_JSON_DIRS = [
    "calibration_results_3step",       # Pockels_Calibration_3Step.py
    "calibration_results_withSample",  # sample_calibration.py
    "pockels_campaign",                # pockels_campaign.py runs
    "orientation_results",             # pockels_orientation_finder.py backups
]

# Null quality flag: if the quick-null residual is > this x the best null
# we have seen so far, print a warning (but do not abort).
NULL_WARN_RATIO = 10.0


# ========================== CALIBRATION LOADER ===========================

def _find_latest_calibration_json():
    """Scan the known calibration directories and return the newest JSON
    file that contains Null_QWP_deg + Null_Analyzer_deg keys.

    Returns the absolute path (str) or None if nothing usable was found.
    """
    candidates = []
    for root in CALIBRATION_JSON_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if not fn.endswith(".json"):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, "r") as f:
                        d = json.load(f)
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                if (d.get("Null_QWP_deg") is not None
                        and d.get("Null_Analyzer_deg") is not None):
                    candidates.append((os.path.getmtime(p), p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _load_calibration_json(path):
    """Load a calibration JSON and extract (hwp, q_null, a_null, p_null).

    Returns a dict with keys hwp_deg, q_null_deg, a_null_deg, p_null_W,
    source (absolute path), raw (full JSON). Any missing numeric field is
    None so the caller can decide what to do (typically keep the rotator
    at its current angle for that axis).
    """
    with open(path, "r") as f:
        d = json.load(f)
    return {
        "hwp_deg": d.get("HWP_fixed_deg"),
        "q_null_deg": d.get("Null_QWP_deg"),
        "a_null_deg": d.get("Null_Analyzer_deg"),
        "p_null_W": d.get("Null_Pmin_W"),
        "source": os.path.abspath(path),
        "raw": d,
    }


def _prompt_load_calibration():
    """Interactive menu at startup.

    Defaults to DEFAULT_CAL_PATH (the 17 Apr 2026 pockels_campaign reference
    null). Pressing Enter loads that. Returns a dict like
    _load_calibration_json() or None if user chose to skip.
    """
    print("\n" + "=" * 60)
    print("  CALIBRATION SEED")
    print("=" * 60)

    default_ok = os.path.isfile(DEFAULT_CAL_PATH)
    if default_ok:
        print(f"  Default calibration:")
        print(f"    {DEFAULT_CAL_PATH}")
        try:
            preview = _load_calibration_json(DEFAULT_CAL_PATH)
            print(f"    HWP={preview['hwp_deg']}, "
                  f"QWP={preview['q_null_deg']}, "
                  f"ANL={preview['a_null_deg']}, "
                  f"P_null={preview['p_null_W']}")
        except Exception as e:
            print(f"    (preview failed: {e})")
            default_ok = False

    latest = _find_latest_calibration_json()
    if latest and latest != os.path.abspath(DEFAULT_CAL_PATH):
        print(f"\n  Newest found (alternative):")
        print(f"    {latest}")

    print("\n  Options:")
    if default_ok:
        print("    [Enter]   load the default calibration shown above")
    print("    <path>    load a specific calibration JSON")
    print("    latest    load the newest calibration found")
    print("    n         skip - use current rotator positions as seed")

    choice = input("  Your choice: ").strip()

    if choice.lower() in ("n", "no", "skip"):
        return None

    if choice == "":
        if not default_ok:
            print("  No default available - falling back to current rotator "
                  "positions.")
            return None
        path = DEFAULT_CAL_PATH
    elif choice.lower() == "latest":
        if latest is None:
            print("  No 'latest' found - falling back to current rotator "
                  "positions.")
            return None
        path = latest
    else:
        path = os.path.expanduser(choice)
        if not os.path.isfile(path):
            print(f"  WARNING: '{path}' not found. Falling back to current "
                  f"rotator positions.")
            return None

    try:
        cal = _load_calibration_json(path)
    except Exception as e:
        print(f"  WARNING: failed to read '{path}': {e}")
        print(f"  Falling back to current rotator positions.")
        return None

    print(f"\n  [LOADED] {cal['source']}")
    print(f"    HWP_fixed_deg   = {cal['hwp_deg']}")
    print(f"    Null_QWP_deg    = {cal['q_null_deg']}")
    print(f"    Null_Analyzer   = {cal['a_null_deg']}")
    print(f"    Null_Pmin_W     = {cal['p_null_W']}")
    return cal


# =========================== DETECTOR ADAPTER ===========================

class _ScopeDetectorAdapter:
    """Wraps DetectorTekTBS so first_null_fast sees the ScopeDetector
    interface it expects (.read_averaged(n, auto_scale) returning
    (mean_v, std_v, power_w, vdiv)).

    DetectorTekTBS.read_power_w_stable() already performs internal
    auto-ranging and stability-checked averaging, so we collapse both
    probe types into a single call. The n argument is ignored (TBS's
    stability loop determines sample count internally).
    """

    def __init__(self, det_tbs):
        self._det = det_tbs

    def read_averaged(self, n=2, auto_scale=True):
        pw, v = self._det.read_power_w_stable()
        return float(v), 0.0, float(pw), None


# =========================== ANALYSIS HELPERS ===========================

def _abs_sin_2x_model(theta_deg, amp, delta_deg, baseline):
    """|sin(2(theta - delta))| model for |lock-in mag| vs analyzer angle.

    Lock-in mag tracks |dP/dV| which for P = P_max cos^2(theta - delta)
    is proportional to |sin(2(theta - delta))|. Baseline absorbs residual
    polarizer leakage / background.
    """
    th = np.deg2rad(theta_deg)
    d = np.deg2rad(delta_deg)
    return amp * np.abs(np.sin(2.0 * (th - d))) + baseline


def fit_delta_from_sweep(angles_deg, lockin_mag):
    """Fit |sin(2(theta - delta))| to a single analyzer sweep.

    Returns delta in degrees, wrapped into [-45, +45] (since the model
    has 90 deg ambiguity). None if the fit fails.
    """
    ang = np.asarray(angles_deg, dtype=float)
    mag = np.asarray(lockin_mag, dtype=float)
    mask = np.isfinite(mag)
    if mask.sum() < 6:
        return None
    ang = ang[mask]
    mag = mag[mask]

    amp0 = float(np.nanmax(mag) - np.nanmin(mag))
    baseline0 = float(np.nanmin(mag))
    # Seed delta by the analyzer angle nearest the maximum of |sin(2x)|
    # (which is at theta - delta = 45 deg).
    i_max = int(np.nanargmax(mag))
    delta0 = float((ang[i_max] - 45.0) % 90.0)

    try:
        popt, _ = curve_fit(
            _abs_sin_2x_model, ang, mag,
            p0=[amp0, delta0, baseline0],
            bounds=([0.0, -90.0, 0.0],
                    [10 * amp0 + 1e-12, 90.0, amp0 + baseline0 + 1e-12]),
            maxfev=5000,
        )
        amp, delta, base = popt
        # Wrap into [-45, +45] — the fit is 90 deg periodic.
        delta = ((delta + 45.0) % 90.0) - 45.0
        return {
            "amp": float(amp),
            "delta_deg": float(delta),
            "baseline": float(base),
        }
    except Exception:
        return None


def _sine_model(theta_i_deg, amp, theta0_deg, offset):
    """delta(theta_i) = amp * sin(2(theta_i - theta0)) + offset.

    The factor of 2 inside sin accounts for the 90 deg periodicity of the
    Pockels rotation vs incident polarization (Abel fig. 3.9).
    """
    return amp * np.sin(np.deg2rad(2.0 * (theta_i_deg - theta0_deg))) + offset


def fit_delta_vs_theta_i(theta_i_deg, delta_deg):
    """Fit sine to delta(theta_i). Returns dict with amp, theta0, offset."""
    ti = np.asarray(theta_i_deg, dtype=float)
    de = np.asarray(delta_deg, dtype=float)
    mask = np.isfinite(de)
    if mask.sum() < 4:
        return None
    ti = ti[mask]
    de = de[mask]

    amp0 = float((np.nanmax(de) - np.nanmin(de)) / 2.0)
    offset0 = float(np.nanmean(de))
    # Seed theta0 at HWP angle where delta crosses zero going positive
    theta0_0 = 0.0

    try:
        popt, _ = curve_fit(
            _sine_model, ti, de,
            p0=[amp0, theta0_0, offset0],
            maxfev=5000,
        )
        amp, theta0, offset = popt
        # Force amp positive; absorb sign into theta0 shift
        if amp < 0:
            amp = -amp
            theta0 = theta0 + 45.0
        theta0 = theta0 % 90.0
        return {
            "delta_max_deg": float(amp),
            "theta0_deg": float(theta0),
            "offset_deg": float(offset),
        }
    except Exception:
        return None


# ============================ DEVICE BRINGUP ============================

def _connect_rotators():
    """Connect HWP, QWP, ANL without homing (preserve calibrated nulls)."""
    print("\nConnecting rotators (NO HOMING - preserving calibration)...")
    rot_hwp = ElliptecRotator(port=PORT_HWP, address=ADDR_HWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_qwp = ElliptecRotator(port=PORT_QWP, address=ADDR_QWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_anl = ElliptecRotator(port=PORT_ANL, address=ADDR_ANL,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    for rot, name in [(rot_hwp, "HWP"), (rot_qwp, "QWP"), (rot_anl, "ANL")]:
        pos = rot.get_angle()
        print(f"  {name}: {pos:.2f} deg")
    return rot_hwp, rot_qwp, rot_anl


def _connect_scope():
    print("\nConnecting oscilloscope...")
    det = DetectorTekTBS(SCOPE_VISA, SCOPE_SOURCE)
    det.connect()
    print("  Oscilloscope connected.")
    return det


def _connect_lockin():
    print("\nConnecting lock-in...")
    lockin = DSP7230(resource_name=LOCKIN_RESOURCE, timeout_ms=LOCKIN_TIMEOUT_MS)
    lockin.connect()
    lockin.apply_safe_startup(run_auto_measure=False)
    try:
        idn = lockin.get_id()
        print(f"  Lock-in: {idn}")
    except Exception:
        print("  Lock-in connected (IDN read failed)")
    try:
        sens_v = lockin.get_sensitivity_volts()
        sens_idx = nearest_sensitivity_index(sens_v)
        print(f"  Lock-in sensitivity: {format_voltage(sens_v)} "
              f"(index {sens_idx}) - KEEPING AS-IS")
    except Exception:
        sens_idx = 15
        print(f"  Lock-in sensitivity: could not read, assuming idx {sens_idx}")
    return lockin, sens_idx


def _check_fixed_lockin_overload(lockin, current_idx):
    """Report overloads without changing the GUI-selected sensitivity."""
    current_idx = int(current_idx)
    try:
        overloaded = bool(int(lockin.get_overload_byte()) & 0x03)
    except Exception:
        return current_idx, False
    if overloaded:
        fullscale_v = SENS_TABLE_VOLTS.get(current_idx)
        range_text = (
            f"{float(fullscale_v) * 1e6:g} uV RMS full scale"
            if fullscale_v is not None
            else f"sensitivity index {current_idx}"
        )
        print(
            f"    [LOCKIN] OVERLOAD at fixed {range_text}; "
            "range left unchanged."
        )
    return current_idx, overloaded


def _sensitivity_fullscale_v(index):
    """Read a sensitivity table entry from either a mapping or test sequence."""
    try:
        return float(SENS_TABLE_VOLTS[int(index)])
    except (KeyError, IndexError, TypeError, ValueError):
        return float("nan")


def configure_fixed_lockin_range(lockin, current_idx, requested_idx=None):
    """Enable fixed sensitivity for a standalone GUI follow-up worker."""
    if requested_idx is None:
        return int(current_idx)
    requested_idx = int(requested_idx)
    if requested_idx not in SENS_TABLE_VOLTS:
        raise ValueError(
            f"DSP7230 sensitivity index {requested_idx} is not a valid "
            "voltage sensitivity."
        )

    # This module calls check_overload directly for DC and AC sweeps.
    globals()["check_overload"] = _check_fixed_lockin_overload
    # The analyser sweep delegates to an imported function whose globals
    # remain in analyser_sweep_voltage_series.
    function_globals = getattr(run_single_sweep, "__globals__", None)
    if isinstance(function_globals, dict) and "check_overload" in function_globals:
        function_globals["check_overload"] = _check_fixed_lockin_overload

    lockin.set_automatic_ac_gain(False)
    lockin.set_sensitivity(requested_idx)
    time.sleep(0.15)
    print(
        f"  [LOCKIN] Fixed range: index {requested_idx} "
        f"({SENS_TABLE_VOLTS[requested_idx] * 1e6:g} uV RMS full scale); "
        "automatic AC gain and sensitivity changes disabled."
    )
    return requested_idx


def _hysteresis_peak_lockin_anchor_v(peak, dither_vpp):
    """Estimate the first hysteresis signal from the certified chip-map read."""
    if not isinstance(peak, dict):
        return 0.0
    candidates = [
        peak.get("max_abs_signed_response_V"),
        peak.get("lockin_metric_V"),
        peak.get("signed_response_V"),
    ]
    certificate = peak.get("normalized_peak_certificate")
    if isinstance(certificate, dict):
        candidates.extend(
            (
                certificate.get("lockin_metric_V"),
                certificate.get("signed_response_V"),
            )
        )
    # ``peak['mag']`` is retained only as a last resort because normalized
    # fast-map peaks may store rotation slope rather than detector volts there.
    candidates.append(peak.get("mag"))
    finite = []
    for value in candidates:
        try:
            value = abs(float(value))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            finite.append(value)
    anchor = max(finite, default=0.0)
    try:
        source_vpp = abs(float(peak.get("vpp")))
        target_vpp = abs(float(dither_vpp))
    except (TypeError, ValueError):
        return anchor
    if source_vpp > 0.0 and math.isfinite(source_vpp) and math.isfinite(target_vpp):
        anchor *= target_vpp / source_vpp
    return float(anchor)


def _lockin_xy_noise_sigma(raw_mags, raw_phases):
    """Conservative one-sample X/Y noise used by the range predictor."""
    samples = []
    for magnitude, phase in zip(raw_mags, raw_phases):
        try:
            magnitude = float(magnitude)
            phase = float(phase)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(magnitude) and math.isfinite(phase)):
            continue
        phase_rad = math.radians(phase)
        samples.append(
            complex(
                magnitude * math.cos(phase_rad),
                magnitude * math.sin(phase_rad),
            )
        )
    if len(samples) < 2:
        return float(DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V)
    values = np.asarray(samples, dtype=np.complex128)
    return float(
        max(
            np.std(values.real, ddof=1),
            np.std(values.imag, ddof=1),
            DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V,
        )
    )


def _lockin_range_diagnostics(lockin):
    """Read output- and input-overload diagnostics without changing gain."""
    overload_byte = None
    status_byte = None
    try:
        overload_byte = int(lockin.get_overload_byte())
    except Exception:
        pass
    try:
        getter = getattr(lockin, "get_status_byte", None)
        status_byte = int(getter() if callable(getter) else lockin.query_int("ST"))
    except Exception:
        pass
    return {
        "overload_byte": overload_byte,
        "status_byte": status_byte,
        "output_overload": bool(overload_byte is not None and overload_byte & 0x03),
        # DSP7230 ST bit 6 reports broadband/input overload.  Changing SEN
        # cannot repair it, so keep it distinct from X/Y output overload.
        "input_overload": bool(status_byte is not None and status_byte & 0x40),
    }


def _configure_funcgen():
    """Bring up Aim-TTi: CH2 reference ON, CH1 sweep ON (voltage set per-sweep)."""
    print("\nConnecting function generator (Aim-TTi)...")
    fg = connect_funcgen()
    print(f"  Configuring CH{FUNCGEN_CH_REF} (reference): "
          f"{FUNCGEN_REF_AMPL_VPP} Vpp @ {FUNCGEN_FREQ_HZ} Hz")
    funcgen_send(fg, f"CHN {FUNCGEN_CH_REF}")
    funcgen_send(fg, "ZLOAD OPEN")
    funcgen_send(fg, f"FREQ {FUNCGEN_FREQ_HZ}")
    funcgen_send(fg, f"AMPL {FUNCGEN_REF_AMPL_VPP}")
    funcgen_send(fg, "OUTPUT ON")
    time.sleep(1.0)
    print(f"  Configuring CH{FUNCGEN_CH_SWEEP} (sweep): {FUNCGEN_FREQ_HZ} Hz")
    funcgen_send(fg, f"CHN {FUNCGEN_CH_SWEEP}")
    funcgen_send(fg, "ZLOAD OPEN")
    funcgen_send(fg, f"FREQ {FUNCGEN_FREQ_HZ}")
    time.sleep(1.0)
    funcgen_send(fg, "OUTPUT ON")
    print("  Function generator OK.")
    return fg


# ============================ PER-HWP WORKFLOW ==========================

def do_quick_null(det_adapter, rot_qwp, rot_anl, q_seed, a_seed):
    """Run first_null_fast and return (q, a, p_null) tuple."""
    log_rows = []
    q, a, p_null = first_null_fast(
        det_adapter, rot_qwp, rot_anl, q_seed, a_seed, log_rows=log_rows
    )
    return float(q), float(a), float(p_null), log_rows


def save_null_info(path, theta_i, q, a, p_null, q_seed, a_seed, log_rows):
    """Write null_info.json for one HWP angle."""
    info = {
        "theta_i_HWP_deg": float(theta_i),
        "q_null_deg": float(q),
        "a_null_deg": float(a),
        "p_null_W": float(p_null),
        "q_seed_deg": float(q_seed),
        "a_seed_deg": float(a_seed),
        "n_probe_reads": len(log_rows),
        "probe_log": [
            {"tag": t, "q_deg": qq, "a_deg": aa, "p_W": pp}
            for (t, qq, aa, pp) in log_rows
        ],
    }
    with open(path, "w") as f:
        json.dump(info, f, indent=2)


def run_voltage_series_at_hwp(
    rot_anl, det, lockin, funcgen, sens_idx,
    a_null, angles,
    hwp_deg, run_label,
    out_dir,
    t_global_start, points_done_before, total_points_all,
):
    """Run the full [1,3,5,7,9] Vpp analyzer sweep at this HWP angle.

    Returns a dict: {vpp -> result dict}.
    """
    all_results = {}
    consistency_log = []

    for v_idx, vpp in enumerate(AC_VOLTAGES_VPP):
        print(f"\n  --- Voltage {v_idx+1}/{len(AC_VOLTAGES_VPP)}: {vpp} Vpp "
              f"(HWP={hwp_deg:.1f} deg) ---")
        funcgen_set_voltage(funcgen, vpp)
        print(f"    Settling {FUNCGEN_SETTLE_S:.0f}s for voltage change...",
              end="", flush=True)
        time.sleep(FUNCGEN_SETTLE_S)
        print(" done.")

        # Return analyzer to the null position for this HWP angle
        safe_move_wrap(rot_anl, a_null)
        anl_readback = rot_anl.get_angle()
        time.sleep(1.5)
        check_p, check_v = det.read_power_w_stable()
        check_mag, check_pha, _, _, _, _, _ = read_lockin_averaged(
            lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S)
        pos_err = angular_error(anl_readback, a_null)

        consistency_log.append({
            "vpp": vpp, "when": "before",
            "target": a_null, "actual": anl_readback,
            "error_deg": pos_err,
            "power_W": check_p, "li_mag_V": check_mag, "li_phase": check_pha,
        })

        print(f"    [VERIFY] ANL target={a_null:.2f} actual={anl_readback:.2f} "
              f"(err={pos_err:.3f} deg) P={check_p:.3e} W")

        res, raw_samples, sens_idx = run_single_sweep(
            rot_anl, det, lockin, sens_idx, angles,
            voltage_label=f"HWP{hwp_deg:.0f}_{vpp}Vpp",
            t_start=t_global_start,
            points_done_before=points_done_before,
            total_points_all=total_points_all,
        )
        all_results[vpp] = res
        points_done_before += len(angles)

        # Save per-voltage CSVs in this HWP's output directory
        csv_path = os.path.join(out_dir, f"sweep_{vpp}Vpp.csv")
        save_sweep_csv(csv_path, res, vpp,
                       run_label=f"{run_label} HWP={hwp_deg:.1f}deg")
        raw_path = os.path.join(out_dir, f"sweep_{vpp}Vpp_raw_samples.csv")
        save_raw_samples_csv(raw_path, raw_samples, vpp,
                             run_label=f"{run_label} HWP={hwp_deg:.1f}deg")
        print(f"    [SAVED] {csv_path}")

        # Return analyzer to null after sweep
        safe_move_wrap(rot_anl, a_null)
        anl_readback = rot_anl.get_angle()
        time.sleep(1.0)
        pos_err = angular_error(anl_readback, a_null)
        consistency_log.append({
            "vpp": vpp, "when": "after",
            "target": a_null, "actual": anl_readback,
            "error_deg": pos_err,
            "power_W": float("nan"), "li_mag_V": float("nan"),
            "li_phase": float("nan"),
        })

    # Save consistency log for this HWP
    con_path = os.path.join(out_dir, "consistency_log.csv")
    with open(con_path, "w") as f:
        f.write(f"# HWP={hwp_deg:.2f} deg, Run={run_label}\n")
        f.write("Vpp,When,Target_deg,Actual_deg,Error_deg,"
                "Power_W,LockIn_Mag_V,LockIn_Phase_deg\n")
        for entry in consistency_log:
            f.write(f"{entry['vpp']},{entry['when']},"
                    f"{entry['target']:.2f},{entry['actual']:.2f},"
                    f"{entry['error_deg']:.3f},{entry['power_W']:.6e},"
                    f"{entry['li_mag_V']:.6e},{entry['li_phase']:.3f}\n")

    return all_results, sens_idx


def save_hwp_overlay_plot(out_dir, all_results, hwp_deg, run_label):
    """Save the 3-panel overlay plot for one HWP angle."""
    n_voltages = len(AC_VOLTAGES_VPP)
    colors = plt.cm.viridis(np.linspace(0.1, 0.9, n_voltages))
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)
    for idx, vpp in enumerate(AC_VOLTAGES_VPP):
        if vpp not in all_results:
            continue
        res = all_results[vpp]
        ang = np.array(res["angle"])
        pwr = np.array(res["power"])
        mag = np.array(res["mag"])
        pha = np.array(res["phase"])
        mag_std = np.array(res["mag_std"])
        pha_std = np.array(res["phase_std"])
        c = colors[idx]
        label = f"{vpp} Vpp"
        ax1.plot(ang, pwr * 1e3, "o-", ms=3, color=c, label=label)
        ax2.errorbar(ang, mag * 1e6, yerr=mag_std * 1e6,
                     fmt="s-", ms=3, color=c, capsize=2,
                     elinewidth=0.8, label=label)
        ax3.errorbar(ang, pha, yerr=pha_std,
                     fmt="d-", ms=3, color=c, capsize=2,
                     elinewidth=0.8, label=label)
    ax1.set_ylabel("DC Power (mW)")
    ax1.set_title(f"HWP={hwp_deg:.1f} deg - {run_label}")
    ax1.grid(True); ax1.legend(fontsize=9)
    ax2.set_ylabel("Lock-in Magnitude (uV)")
    ax2.grid(True); ax2.legend(fontsize=9)
    ax3.set_ylabel("Lock-in Phase (deg)")
    ax3.set_xlabel("Analyzer Angle (deg)")
    ax3.grid(True); ax3.legend(fontsize=9)
    fig.tight_layout()
    p = os.path.join(out_dir, f"overlay_hwp{hwp_deg:.0f}deg.png")
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


# ============================ CAMPAIGN SUMMARY ==========================

def save_campaign_summary(root_dir, summary_rows, run_label):
    """Write delta(theta_i) CSV + PNG. summary_rows is a list of dicts
    per HWP angle with keys: theta_i, q_null, a_null, p_null, delta_by_vpp.
    """
    csv_path = os.path.join(root_dir, "delta_vs_theta_i.csv")
    with open(csv_path, "w") as f:
        f.write(f"# Run={run_label}\n")
        cols = ["theta_i_HWP_deg", "q_null_deg", "a_null_deg", "p_null_W"]
        for vpp in AC_VOLTAGES_VPP:
            cols += [f"delta_{vpp}Vpp_deg",
                     f"amp_{vpp}Vpp_V",
                     f"baseline_{vpp}Vpp_V"]
        f.write(",".join(cols) + "\n")
        for r in summary_rows:
            row = [f"{r['theta_i']:.2f}",
                   f"{r['q_null']:.3f}",
                   f"{r['a_null']:.3f}",
                   f"{r['p_null']:.6e}"]
            for vpp in AC_VOLTAGES_VPP:
                fit = r["fits"].get(vpp)
                if fit is None:
                    row += ["nan", "nan", "nan"]
                else:
                    row += [f"{fit['delta_deg']:.4f}",
                            f"{fit['amp']:.6e}",
                            f"{fit['baseline']:.6e}"]
            f.write(",".join(row) + "\n")
    print(f"[SAVED] {csv_path}")

    # Plot delta(theta_i) per voltage + sine fit at highest voltage
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    colors = plt.cm.viridis(np.linspace(0.1, 0.9, len(AC_VOLTAGES_VPP)))
    theta_i_arr = np.array([r["theta_i"] for r in summary_rows])

    sine_fits_by_vpp = {}
    for idx, vpp in enumerate(AC_VOLTAGES_VPP):
        delta_arr = np.array([r["fits"].get(vpp, {}).get("delta_deg", np.nan)
                              for r in summary_rows])
        ax1.plot(theta_i_arr, delta_arr, "o-", ms=5, color=colors[idx],
                 label=f"{vpp} Vpp")
        # Sine fit
        fit = fit_delta_vs_theta_i(theta_i_arr, delta_arr)
        sine_fits_by_vpp[vpp] = fit
        if fit is not None:
            ti_fine = np.linspace(theta_i_arr.min(), theta_i_arr.max(), 200)
            de_fit = _sine_model(ti_fine, fit["delta_max_deg"],
                                 fit["theta0_deg"], fit["offset_deg"])
            ax1.plot(ti_fine, de_fit, "--", color=colors[idx], alpha=0.5)

    ax1.set_ylabel("Pockels rotation delta (deg)")
    ax1.set_title(f"delta(theta_i) - {run_label}")
    ax1.grid(True); ax1.legend(fontsize=9)

    # delta_max vs Vpp (should be linear in V_ac per Abel eq. 3.8)
    vpp_arr = np.array(AC_VOLTAGES_VPP, dtype=float)
    dmax_arr = np.array([
        sine_fits_by_vpp[v]["delta_max_deg"]
        if sine_fits_by_vpp.get(v) is not None else np.nan
        for v in AC_VOLTAGES_VPP
    ])
    ax2.plot(vpp_arr, dmax_arr, "o-", ms=6, color="tab:red")
    ax2.set_xlabel("V_AC (Vpp)")
    ax2.set_ylabel("delta_max (deg)")
    ax2.set_title("Pockels rotation amplitude vs drive voltage")
    ax2.grid(True)

    fig.tight_layout()
    png_path = os.path.join(root_dir, "delta_vs_theta_i.png")
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"[SAVED] {png_path}")

    # JSON summary with the sine-fit params at each Vpp
    json_path = os.path.join(root_dir, "campaign_summary.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_label": run_label,
            "hwp_angles_deg": theta_i_arr.tolist(),
            "voltages_vpp": list(AC_VOLTAGES_VPP),
            "sine_fits_by_vpp": {
                str(v): sine_fits_by_vpp[v] for v in AC_VOLTAGES_VPP
            },
            "per_hwp": [
                {
                    "theta_i_HWP_deg": r["theta_i"],
                    "q_null_deg": r["q_null"],
                    "a_null_deg": r["a_null"],
                    "p_null_W": r["p_null"],
                    "delta_fits": {
                        str(v): r["fits"].get(v) for v in AC_VOLTAGES_VPP
                    },
                }
                for r in summary_rows
            ],
        }, f, indent=2)
    print(f"[SAVED] {json_path}")


# =========================== SMU BRINGUP / RAMP =========================

def _smu_set_hv(smu, on):
    """Enable/disable the SMU4201 global high-voltage interlock.

    Factory default is LV (4201 caps at ~42 V). HV mode is required to source
    above that. The front-panel password interlock must be disabled for the
    SCPI command to take effect (page 24, SMU4000 Programming Manual).
    """
    target = "ON" if on else "OFF"
    # The SMU error queue is session-wide. Drain anything already queued so
    # it cannot be falsely attributed to this HV transition.
    try:
        prior_errs = smu.check_errors()
    except Exception:
        prior_errs = []
    if prior_errs:
        print(
            f"  [WARN] SMU errors queued before HV-state {target} "
            f"(not caused by the HV command): {prior_errs}"
        )

    cmd = "SYSTem:MODE:HV:STATe " + target
    smu.write(cmd)
    time.sleep(0.3)
    # Verify (query form follows the standard SCPI convention)
    try:
        state = smu.query("SYSTem:MODE:HV:STATe?").strip()
    except Exception:
        state = "<query failed>"
    try:
        errs = smu.check_errors()
    except Exception:
        errs = []
    if errs:
        print(f"  [WARN] HV-state {target} errors: {errs}")
    return state


def _connect_smu(hold_voltage_v, *, smu=None, enable_output=True):
    """Bring up SMU4201, enable HV mode (interlock LV -> HV so we can source
    > 42 V), configure source-V/measure-I (1 mA compliance), enable output,
    and ramp up to hold_voltage_v in SMU_RAMP_STEP_V chunks.

    Recovery may supply an already-open SMU and enable_output=False to
    configure it at 0 V without ever enabling output before pixel routing.
    """
    print(f"\nConnecting SMU4201 on {SMU_PORT}...")
    if smu is None:
        smu = SMU4201(port=SMU_PORT, baud=SMU_BAUD, timeout=2.0)
    smu.output(False)
    print(f"  {smu.idn()}")
    smu.configure_source_voltage_measure_current(SMU_COMPLIANCE_A, SMU_NPLC)
    errs = smu.check_errors()
    if errs:
        print(f"  [WARN] SMU base setup errors: {errs}")

    # Source-control settings must be established before OUTPUT is enabled.
    # Re-sending them during a live hysteresis sweep produced repeatable
    # 103/104 errors on the lab SMU4201.
    smu.write(f"SOURce:VOLTage:SLEW {SMU_SLEW_RATE_V_PER_MS:.3f}V/ms")
    slew_errs = smu.check_errors()
    if slew_errs:
        print(f"  [WARN] SMU slew-rate setup errors: {slew_errs}")

    # Enable HV mode BEFORE turning the output on / ramping. The 4201's
    # factory default is LV which caps at ~42 V; HV mode unlocks up to 200 V.
    print(f"  Enabling HV interlock (LV -> HV) for >42 V operation...")
    hv_state = _smu_set_hv(smu, True)
    print(f"  HV state readback: {hv_state}")
    if hv_state.strip() not in ("1", "ON"):
        raise RuntimeError(
            f"Could not enable HV mode on SMU4201 (state={hv_state!r}). "
            f"Check the front-panel password interlock is disabled."
        )

    smu.set_voltage(0.0)
    if not enable_output:
        if float(hold_voltage_v) != 0.0:
            raise ValueError("Output-disabled SMU setup requires a 0 V hold level")
        state = smu.query("OUTPut:STATe?").strip().upper()
        if state not in ("0", "OFF"):
            raise RuntimeError(f"SMU output failed to disable (state={state!r})")
        print("  SMU armed at 0 V with output OFF.")
        return smu
    smu.output(True)
    time.sleep(0.5)
    state = smu.query("OUTPut:STATe?")
    if state.strip() not in ("1", "ON"):
        raise RuntimeError(f"SMU output failed to enable (state={state!r})")
    print(f"  SMU output ON (compliance {SMU_COMPLIANCE_A*1e3:.1f} mA, "
          f"HV mode). Ramping 0 -> {hold_voltage_v:+.1f} V...")
    smu_ramp_to(smu, 0.0, hold_voltage_v)
    print(f"  SMU holding {hold_voltage_v:+.1f} V.")
    return smu


def smu_ramp_to(smu, v_from, v_to,
                step_v=SMU_RAMP_STEP_V, dwell_s=SMU_RAMP_DWELL_S):
    """Ramp the SMU from v_from to v_to in step_v chunks (avoids step transients
    that could trip compliance on a high-impedance ferroelectric)."""
    if step_v <= 0:
        smu.set_voltage(float(v_to))
        time.sleep(dwell_s)
        return
    n = max(1, int(math.ceil(abs(v_to - v_from) / step_v)))
    pts = np.linspace(v_from, v_to, n + 1)[1:]
    for v in pts:
        smu.set_voltage(float(v))
        time.sleep(dwell_s)


def smu_ensure_output_on(smu, banner_wait_s=None):
    """Verify the SMU output stage is enabled. If not, enable it and wait for
    the front-panel "Counts/Shapes" banner to clear so the rails are
    actually live before any voltage is applied (mirrors the PRESWEEP_S
    pattern in smu4201_iv_sweep.py).

    Returns True if output is on after this call, False if it failed."""
    if smu is None:
        return False
    if banner_wait_s is None:
        banner_wait_s = SMU_OUTPUT_BANNER_S
    try:
        state = smu.query("OUTPut:STATe?").strip()
    except Exception as e:
        print(f"  [WARN] could not query SMU output state: {e}")
        state = ""
    if state in ("1", "ON"):
        return True
    print(f"  [SMU] OUTPut was OFF -> enabling (waiting {banner_wait_s:.1f}s "
          f"for front-panel banner to clear)...")
    try:
        smu.write("OUTPut:STATe ON")
    except Exception as e:
        print(f"  [WARN] OUTPut:STATe ON failed: {e}")
        return False
    time.sleep(banner_wait_s)
    try:
        state = smu.query("OUTPut:STATe?").strip()
    except Exception:
        state = ""
    if state in ("1", "ON"):
        print(f"  [SMU] OUTPut now ON.")
        return True
    print(f"  [WARN] SMU OUTPut did not enable (state={state!r}).")
    return False


def smu_safe_shutdown(smu):
    """Ramp to 0 V, disable output, return HV interlock to LV factory default.
    Safe to call from finally blocks (every step swallows its own errors)."""
    if smu is None:
        return
    try:
        v_now = float(smu.query("SOURce:VOLTage:FIXed:LEVel?") or 0.0)
    except Exception:
        v_now = 0.0
    try:
        smu_ramp_to(smu, v_now, 0.0)
    except Exception:
        pass
    try:
        smu.output(False)
    except Exception:
        pass
    # Do not change the global HV mode while OUTPUT is still transitioning or
    # while the continuous measurement engine is running.
    time.sleep(0.3)
    try:
        output_state = smu.query("OUTPut:STATe?").strip()
        if output_state not in ("0", "OFF"):
            print(
                f"  [WARN] SMU output readback is {output_state!r} during "
                "shutdown; retrying OUTPUT OFF."
            )
            smu.output(False)
            time.sleep(0.3)
    except Exception:
        pass
    try:
        smu.write("SOURce:VOLTage:MEASure:COUNt:INFinite OFF")
        time.sleep(0.1)
    except Exception:
        pass
    # Return the interlock to LV so the unit is left in a safe default state.
    try:
        hv_state = _smu_set_hv(smu, False)
        if str(hv_state).strip() not in ("0", "OFF"):
            print(f"  [WARN] SMU HV mode remained enabled (state={hv_state!r}).")
    except Exception as exc:
        print(f"  [WARN] SMU HV-state OFF failed during shutdown: {exc}")
    try:
        smu.close()
    except Exception:
        pass


# ============================ FUNCGEN HELPERS ============================

def _funcgen_query_int(funcgen, command):
    """Return an integer TGF3000 query response, tolerating VISA whitespace."""
    response = str(funcgen.query(str(command))).strip()
    if not response:
        raise RuntimeError(f"empty response to {command}")
    return int(float(response.split()[0].rstrip(",")))


def _funcgen_assert_no_execution_error(funcgen, context):
    """Raise when the TGF3000 reports that a preceding command failed."""
    error_code = _funcgen_query_int(funcgen, "EER?")
    if error_code != 0:
        detail = (
            "output-voltage overload; the generator switched its output OFF for safety"
            if error_code == -80
            else "function-generator execution error"
        )
        raise RuntimeError(
            f"TGF3162 execution error {error_code} during {context}: {detail}"
        )
    return error_code


def _funcgen_set_sweep_output(
    funcgen,
    on,
    *,
    verify_command=False,
    required=False,
    context="AC sweep output",
):
    """Toggle the AC drive (CH1 / FUNCGEN_CH_SWEEP) on or off without touching
    the lock-in reference channel (CH2). Used for domain-reset isolation and
    the short, gated hysteresis measurement windows.

    The TGF3162 has no documented OUTPUT-state query.  For critical paths,
    verify that CH1 is selected and query EER? after OUTPUT; error -80 means
    the instrument disabled its output because of an output-voltage overload.
    """
    try:
        if verify_command:
            # Clear and expose any stale execution error before issuing a new
            # command, so a later EER? belongs to this transition.
            _funcgen_query_int(funcgen, "EER?")
        funcgen_send(funcgen, f"CHN {FUNCGEN_CH_SWEEP}")
        if verify_command:
            selected_channel = _funcgen_query_int(funcgen, "CHN?")
            if selected_channel != int(FUNCGEN_CH_SWEEP):
                raise RuntimeError(
                    f"selected channel readback is {selected_channel}, "
                    f"expected {int(FUNCGEN_CH_SWEEP)}"
                )
        funcgen_send(funcgen, "OUTPUT " + ("ON" if on else "OFF"))
        if verify_command:
            _funcgen_assert_no_execution_error(funcgen, context)
        time.sleep(0.2)
        return True
    except Exception as e:
        message = (
            f"funcgen CH{FUNCGEN_CH_SWEEP} OUTPUT "
            f"{'ON' if on else 'OFF'} failed during {context}: {e}"
        )
        if required:
            raise RuntimeError(message) from e
        print(f"  [WARN] {message}")
        return False


def _prepare_hysteresis_ac_drive(funcgen, dither_vpp):
    """Program the hysteresis amplitude and leave CH1 verified-command OFF.

    Hysteresis points deliberately pole with DC alone.  CH1 is enabled later,
    after the complete per-point DC dwell, and is disabled again immediately
    after the optical measurement window.
    """
    _funcgen_set_sweep_output(
        funcgen,
        False,
        verify_command=True,
        required=True,
        context="hysteresis AC preparation",
    )
    # Clear an earlier error, program the final range/amplitude while the
    # output is disabled, and verify that the amplitude command was accepted.
    _funcgen_query_int(funcgen, "EER?")
    funcgen_set_voltage(funcgen, float(dither_vpp))
    _funcgen_assert_no_execution_error(funcgen, "hysteresis AC amplitude setup")
    print(
        f"  [FUNCGEN] CH{FUNCGEN_CH_SWEEP} prepared at "
        f"{float(dither_vpp):g} Vpp with OUTPUT OFF"
    )


# ============================== DOMAIN RESET =============================

def validate_hysteresis_voltage_limit(value, label="Voltage"):
    """Return a positive amplitude bounded by the production +/-40 V limit."""
    amplitude = abs(float(value))
    limit = abs(float(DC_HYST_VMAX))
    if not math.isfinite(amplitude) or amplitude <= 0.0:
        raise ValueError(f"{label} must be finite and positive.")
    if amplitude > limit + 1e-9:
        raise ValueError(
            f"{label} {amplitude:g} V exceeds the hard +/-{limit:g} V "
            "hysteresis limit."
        )
    return amplitude


def validate_reset_voltage_window(vmax, vmin):
    """Validate both reset-envelope endpoints against the hysteresis ceiling."""
    maximum = validate_hysteresis_voltage_limit(vmax, "Domain-reset Vmax")
    minimum = validate_hysteresis_voltage_limit(vmin, "Domain-reset Vmin")
    if minimum > maximum:
        raise ValueError(
            f"Domain-reset Vmin {minimum:g} V cannot exceed Vmax {maximum:g} V."
        )
    return maximum, minimum


def _build_reset_envelope(vmax, vmin, n_steps, decay):
    """Return an array of N positive amplitudes from vmax down to >= vmin.

    'exponential' decays as V_n = vmax * exp(-3 n / (N-1)) clamped at vmin.
    'linear' is a straight line from vmax to vmin.
    """
    n = max(2, int(n_steps))
    if decay == "linear":
        amps = np.linspace(vmax, vmin, n)
    else:  # exponential
        amps = vmax * np.exp(-3.0 * np.arange(n) / (n - 1))
        amps = np.maximum(amps, vmin)
    return amps


def _hysteresis_cancel_checkpoint(cancel_callback=None):
    """Run an optional acquisition-cancellation callback without swallowing it."""
    if cancel_callback is not None:
        cancel_callback()


def _hysteresis_interruptible_sleep(seconds, cancel_callback=None, poll_s=0.25):
    """Sleep in short chunks so a GUI pixel-skip is observed promptly."""
    duration = max(0.0, float(seconds))
    if cancel_callback is None:
        time.sleep(duration)
        return
    interval = max(0.01, float(poll_s))
    deadline = time.time() + duration
    _hysteresis_cancel_checkpoint(cancel_callback)
    while True:
        remaining = deadline - time.time()
        if remaining <= 0.0:
            break
        time.sleep(min(interval, remaining))
        _hysteresis_cancel_checkpoint(cancel_callback)


def reset_domains_pulsed(
    smu, funcgen, out_dir, run_label,
    vmax=None, vmin=None, n_amp_steps=None, decay=None,
    cycles_per_amp=None,
    pulse_first_ms=None, pulse_second_ms=None,
    cancel_callback=None,
):
    """AC-depole the BTO ferroelectric domains by software-stepping bipolar
    set_voltage() calls. Python alternates +V_n / -V_n for cycles_per_amp
    cycles at each amplitude, while the envelope decays from vmax to vmin
    over n_amp_steps amplitudes.

    SHAPe PULSe was the obvious choice on paper, but in practice each
    OUTPut:STATe ON triggers a multi-second front-panel banner during which
    the rails are not fully live (same effect that motivates PRESWEEP_S in
    smu4201_iv_sweep.py). Toggling output once per amplitude burst meant the
    SMU was effectively off for most of the reset. So this implementation
    keeps OUTPut ON for the entire routine and steps levels via the same
    set_voltage() path the IV sweep uses.

    The funcgen sweep channel (CH1) is turned OFF for the duration of the
    reset so the AC drive does not interfere with the depoling pulses, then
    re-enabled when the reset finishes.
    """
    vmax, vmin = validate_reset_voltage_window(
        RESET_VMAX if vmax is None else vmax,
        RESET_VMIN if vmin is None else vmin,
    )
    n_amp_steps = RESET_AMP_STEPS if n_amp_steps is None else int(n_amp_steps)
    decay = RESET_DECAY if decay is None else str(decay)
    cycles_per_amp = (RESET_CYCLES_PER_AMP if cycles_per_amp is None
                      else int(cycles_per_amp))
    pulse_first_ms = (RESET_PULSE_FIRST_MS if pulse_first_ms is None
                      else float(pulse_first_ms))
    pulse_second_ms = (RESET_PULSE_SECOND_MS if pulse_second_ms is None
                       else float(pulse_second_ms))

    amps = _build_reset_envelope(vmax, vmin, n_amp_steps, decay)
    cycle_s = (pulse_first_ms + pulse_second_ms) / 1000.0
    burst_time_s = cycles_per_amp * cycle_s
    # SCPI roundtrip overhead (~5-10 ms per set_voltage call) - rough estimate.
    scpi_overhead_per_cycle_s = 0.012
    est_burst_total_s = burst_time_s + cycles_per_amp * scpi_overhead_per_cycle_s
    est_total_s = n_amp_steps * est_burst_total_s
    total_reversals = n_amp_steps * cycles_per_amp * 2

    print(f"\n{'=' * 70}")
    print(f"  DOMAIN RESET (software-stepped bipolar pulses)")
    print(f"{'=' * 70}")
    print(f"  Decay envelope : {decay}, "
          f"{vmax:.1f} V -> {amps[-1]:.3f} V over {n_amp_steps} amplitudes")
    print(f"  Per amplitude  : {cycles_per_amp} cycles x "
          f"({pulse_first_ms:.2f}+{pulse_second_ms:.2f}) ms sleep "
          f"+ SCPI overhead = ~{est_burst_total_s:.2f} s")
    print(f"  Total          : ~{total_reversals} bipolar reversals, "
          f"~{est_total_s:.0f} s wall-clock")

    os.makedirs(out_dir, exist_ok=True)

    # Funcgen AC drive OFF during reset so it does not perturb the SMU pulses
    if funcgen is not None:
        print(f"  Disabling funcgen CH{FUNCGEN_CH_SWEEP} (sweep) for the reset...")
        _funcgen_set_sweep_output(funcgen, False)

    pulse_first_s = pulse_first_ms / 1000.0
    pulse_second_s = pulse_second_ms / 1000.0

    rows = []
    reset_completed = False
    try:
        _hysteresis_cancel_checkpoint(cancel_callback)
        # _connect_smu configured FIXed shape, trigger, and slew before OUTPUT
        # was enabled.  Do not re-send source-control settings to a running
        # SMU4201; just return its live fixed level to zero.
        try:
            smu.set_voltage(0.0)
        except Exception as e:
            print(f"  [WARN] SMU reset zero-level prep: {e}")
        smu_ensure_output_on(smu)
        errs = smu.check_errors()
        if errs:
            print(f"  [WARN] SMU pre-reset errors: {errs}")

        t_start = time.time()
        try:
            for i, vamp in enumerate(amps):
                _hysteresis_cancel_checkpoint(cancel_callback)
                v_first = float(vamp)
                v_second = -float(vamp)
                t_burst_start = time.time() - t_start

                # Software-stepped bipolar burst at this amplitude.
                # OUTPut stays ON throughout — only the level changes.
                for _ in range(cycles_per_amp):
                    _hysteresis_cancel_checkpoint(cancel_callback)
                    smu.set_voltage(v_first)
                    if pulse_first_s > 0:
                        _hysteresis_interruptible_sleep(
                            pulse_first_s,
                            cancel_callback,
                        )
                    smu.set_voltage(v_second)
                    if pulse_second_s > 0:
                        _hysteresis_interruptible_sleep(
                            pulse_second_s,
                            cancel_callback,
                        )

                rows.append({
                    "i": i, "t_start_s": t_burst_start,
                    "v_first": v_first, "v_second": v_second,
                    "cycles": cycles_per_amp,
                    "first_ms": pulse_first_ms, "second_ms": pulse_second_ms,
                })
                # Periodic progress + sanity-check OUTPut hasn't gone off
                if (i + 1) % max(1, n_amp_steps // 10) == 0 or i == n_amp_steps - 1:
                    elapsed = time.time() - t_start
                    try:
                        out_state = smu.query("OUTPut:STATe?").strip()
                    except Exception:
                        out_state = "?"
                    print(f"    [{i+1:3d}/{n_amp_steps}] +/-{vamp:6.3f} V  "
                          f"({elapsed:5.1f} s, OUTPut={out_state})")
        except KeyboardInterrupt:
            print("\n  [INTERRUPTED] reset aborted - returning SMU to 0 V.")
            raise
        finally:
            # Always return to 0 V (output stays ON for the next sweep)
            try:
                smu.set_voltage(0.0)
                time.sleep(0.1)
            except Exception as e:
                print(f"  [WARN] could not return SMU to 0 V: {e}")

        elapsed = time.time() - t_start
        try:
            out_state = smu.query("OUTPut:STATe?").strip()
        except Exception:
            out_state = "?"
        print(f"  Reset complete in {elapsed:.1f} s, "
              f"SMU at 0 V FIXed (OUTPut={out_state}).")
        reset_completed = True

    finally:
        # Re-enable only after a completed reset. Cancellation keeps the
        # sample-drive output OFF while the pixel-level cleanup unwinds.
        if funcgen is not None:
            if reset_completed:
                print(f"  Re-enabling funcgen CH{FUNCGEN_CH_SWEEP}...")
                _funcgen_set_sweep_output(funcgen, True)
                time.sleep(FUNCGEN_SETTLE_S)
            else:
                _funcgen_set_sweep_output(
                    funcgen,
                    False,
                    verify_command=False,
                    required=False,
                    context="cancelled domain-reset cleanup",
                )

    # --- save log + plot ---
    csv_path = os.path.join(out_dir, "reset_log.csv")
    with open(csv_path, "w") as f:
        f.write(f"# Run={run_label}\n")
        f.write(f"# Decay={decay}, vmax={vmax}, vmin={vmin}, "
                f"amp_steps={n_amp_steps}\n")
        f.write(f"# Cycles_per_amp={cycles_per_amp}, "
                f"first_ms={pulse_first_ms}, second_ms={pulse_second_ms}\n")
        f.write(f"# Total_bipolar_reversals={total_reversals}\n")
        f.write("idx,t_start_s,V_first_V,V_second_V,cycles,first_ms,second_ms\n")
        for r in rows:
            f.write(f"{r['i']},{r['t_start_s']:.4f},{r['v_first']:.4f},"
                    f"{r['v_second']:.4f},{r['cycles']},"
                    f"{r['first_ms']:.4f},{r['second_ms']:.4f}\n")
    print(f"  [SAVED] {csv_path}")

    json_path = os.path.join(out_dir, "reset_config.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_label": run_label,
            "decay": decay, "vmax": vmax, "vmin": vmin,
            "n_amp_steps": n_amp_steps,
            "cycles_per_amp": cycles_per_amp,
            "first_ms": pulse_first_ms,
            "second_ms": pulse_second_ms,
            "total_bipolar_reversals": total_reversals,
            "actual_envelope_v": amps.tolist(),
        }, f, indent=2)
    print(f"  [SAVED] {json_path}")

    try:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.plot(amps, "o-", ms=4, color="tab:blue", label="amplitude envelope")
        ax.plot(-amps, "s-", ms=4, color="tab:red", alpha=0.6,
                label="negative half (alternating)")
        ax.axhline(0, color="k", lw=0.5)
        ax.set_xlabel("Amplitude step #")
        ax.set_ylabel("V (V)")
        ax.set_title(f"Domain reset envelope - {decay} decay\n"
                     f"{n_amp_steps} amps x {cycles_per_amp} cycles "
                     f"= {total_reversals} bipolar reversals")
        ax.grid(True, alpha=0.3); ax.legend()
        fig.tight_layout()
        png_path = os.path.join(out_dir, "reset_envelope.png")
        fig.savefig(png_path, dpi=150)
        plt.close(fig)
        print(f"  [SAVED] {png_path}")
    except Exception as e:
        print(f"  [WARN] envelope plot failed: {e}")


# ===================== PEAK FINDER + DC HYSTERESIS =======================

def find_peak_response(summary_rows, all_results_by_hwp):
    """Across all (theta_i, vpp, analyzer-angle) points already measured,
    return the one with the largest lock-in magnitude.

    Returns dict with theta_i, vpp, anl_angle, mag, q_null, a_null — or None.
    """
    best = None
    for r in summary_rows:
        theta_i = r["theta_i"]
        results = all_results_by_hwp.get(theta_i)
        if results is None:
            continue
        for vpp, res in results.items():
            mags = np.asarray(res.get("mag", []), dtype=float)
            angs = np.asarray(res.get("angle", []), dtype=float)
            if mags.size == 0 or not np.any(np.isfinite(mags)):
                continue
            i_peak = int(np.nanargmax(mags))
            mag_peak = float(mags[i_peak])
            ang_peak = float(angs[i_peak])
            if best is None or mag_peak > best["mag"]:
                best = {
                    "theta_i": float(theta_i),
                    "vpp": vpp,
                    "anl_angle": ang_peak,
                    "mag": mag_peak,
                    "q_null": float(r["q_null"]),
                    "a_null": float(r["a_null"]),
                }
    return best


def _voltage_grid_uniform(vmin, vmax, step):
    """Inclusive voltage grid with rounded values to avoid float artifacts."""
    step = abs(float(step))
    if step <= 0.0:
        raise ValueError("Voltage step must be positive")
    eps = step / 1000.0
    return np.round(np.arange(float(vmin), float(vmax) + eps, step), 10)


def _build_center_dense_hysteresis_points(vmax):
    """Return the standard symmetric, centre-dense DC voltage levels.

    At the production +/-40 V range this is exactly:
      0, +/-1.25, +/-2.5, +/-5, +/-7.5, +/-10, +/-12.5,
      +/-15, +/-20, +/-25, +/-30, +/-40 V.

    A smaller non-standard ``vmax`` truncates the profile and always adds its
    exact endpoint, which keeps test/diagnostic sweeps symmetric and bounded.
    """
    vmax = abs(float(vmax))
    if not math.isfinite(vmax) or vmax <= 0.0:
        raise ValueError("Hysteresis Vmax must be finite and positive")

    abs_levels = [
        float(v)
        for v in DC_HYST_CENTER_DENSE_ABS_LEVELS_V
        if float(v) <= vmax + 1e-9
    ]
    if not any(math.isclose(v, vmax, rel_tol=0.0, abs_tol=1e-9)
               for v in abs_levels):
        abs_levels.append(vmax)
    abs_levels = sorted(set(round(v, 10) for v in abs_levels))
    negative = [-v for v in reversed(abs_levels) if v > 0.0]
    return np.asarray(negative + abs_levels, dtype=float)


def _resolve_fine_windows(vmax, fine_windows=None):
    """Normalize the fine-grid window spec.

    None       -> no explicit windows; use the standard centre-dense profile
    []         -> no fine points at all (pure coarse grid)
    [(lo, hi)] -> explicit windows, e.g. one around each detected V_c (v2)
    """
    vmax = abs(float(vmax))
    if fine_windows is None:
        return []
    out = []
    for lo, hi in fine_windows:
        lo = max(-vmax, min(float(lo), float(hi)))
        hi = min(vmax, max(float(lo), float(hi)))
        if hi > lo:
            out.append((lo, hi))
    return out


def _build_hybrid_hysteresis_points(vmax, fine_windows=None):
    """Build standard centre-dense or explicit-window voltage levels.

    ``fine_windows is None`` selects the standard one-pass centre-dense grid.
    Explicit windows retain the v2 5 V coarse grid plus 2.5 V fine points;
    an empty list remains the adaptive reconnaissance coarse-only grid.
    """
    vmax = abs(float(vmax))
    if fine_windows is None:
        return _build_center_dense_hysteresis_points(vmax)
    windows = _resolve_fine_windows(vmax, fine_windows)

    values = set()
    for v in _voltage_grid_uniform(-vmax, vmax, DC_HYST_COARSE_STEP):
        values.add(float(v))
    for lo, hi in windows:
        for v in _voltage_grid_uniform(lo, hi, DC_HYST_FINE_STEP):
            values.add(float(v))
    return np.array(sorted(values), dtype=float)


def describe_hysteresis_spacing(fine_windows=None, vmax=None):
    effective_vmax = validate_hysteresis_voltage_limit(
        DC_HYST_VMAX if vmax is None else vmax,
        "DC hysteresis Vmax",
    )
    if fine_windows is None:
        points = _build_center_dense_hysteresis_points(effective_vmax)
        positive_levels = sorted(
            {float(value) for value in points if float(value) >= 0.0},
            reverse=True,
        )
        levels = ", ".join(
            f"{value:g}" for value in positive_levels
        )
        return f"centre-dense standard (+/-[{levels}] V, including 0 V)"
    windows = _resolve_fine_windows(effective_vmax, fine_windows)
    if not windows:
        return f"{DC_HYST_COARSE_STEP:g} V everywhere (coarse-only)"
    wtxt = ", ".join(f"[{lo:+g}, {hi:+g}] V" for lo, hi in windows)
    return (f"{DC_HYST_COARSE_STEP:g} V coarse, "
            f"{DC_HYST_FINE_STEP:g} V fine in {wtxt}")


def _build_hysteresis_trajectory(vmax, step=None, start_from_zero=True,
                                 cycles=1, fine_windows=None):
    """Build a butterfly-loop trajectory.

    start_from_zero=True  -> 0 -> +vmax -> -vmax -> +vmax  (captures the
                             virgin curve on leg 1; use after a domain reset).
    start_from_zero=False -> +vmax -> -vmax -> +vmax  (clean butterfly with
                             both V_DC=0 crossings at the remnant points;
                             use when the sample is already poled).
    cycles>1 repeats the (down, up) pair; branch labels then carry the cycle
    number ("down1", "up1", "down2", ...) while cycles==1 keeps the legacy
    unsuffixed labels. If step and fine_windows are both None, uses the
    standard centre-dense grid. Explicit fine_windows select the hybrid
    coarse/fine grid; a numeric step selects a uniform override.
    Returns (voltages_array, branch_labels, cycle_numbers).
    """
    vmax = abs(float(vmax))
    cycles = max(1, int(cycles))
    if step is None:
        points = _build_hybrid_hysteresis_points(vmax, fine_windows)
    else:
        points = _voltage_grid_uniform(-vmax, vmax, float(step))

    leg_down = points[points < vmax][::-1]                    # +vmax -> -vmax
    leg_up = points[points > -vmax]                           # -vmax -> +vmax

    if start_from_zero:
        lead = points[points >= 0.0]                          # 0 -> +vmax (virgin)
        lead_branch = "virgin"
    else:
        lead = np.array([vmax], dtype=float)                  # start at +vmax
        lead_branch = "sat"

    volt_parts = [lead]
    branches = [lead_branch] * len(lead)
    cycle_nums = [1] * len(lead)
    for c in range(1, cycles + 1):
        suffix = str(c) if cycles > 1 else ""
        volt_parts.extend([leg_down, leg_up])
        branches.extend(["down" + suffix] * len(leg_down))
        branches.extend(["up" + suffix] * len(leg_up))
        cycle_nums.extend([c] * (len(leg_down) + len(leg_up)))

    voltages = np.concatenate(volt_parts)
    return voltages, branches, cycle_nums


def _finite_json_number(value):
    """Return a plain finite float for the live JSON stream, else None."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _hysteresis_live_point(row):
    """Compact, JSON-safe form of one acquired hysteresis point."""
    return {
        "idx": int(row.get("idx", 0)),
        "t_s": _finite_json_number(row.get("t")),
        "branch": str(row.get("branch", "") or ""),
        "cycle": int(row.get("cycle", 1) or 1),
        "v_dc_V": _finite_json_number(row.get("v_dc")),
        "lockin_mag_V": _finite_json_number(row.get("li_mag")),
        "lockin_phase_deg": _finite_json_number(row.get("li_phase")),
        "lockin_mag_std_V": _finite_json_number(row.get("li_mag_std")),
        "lockin_sensitivity_index": int(row.get("sens_idx", -1) or -1),
        "lockin_sensitivity_fullscale_V": _finite_json_number(row.get("sens_v")),
        "lockin_range_mode": str(row.get("lockin_range_mode", "fixed") or "fixed"),
        "lockin_range_rescue_reread": int(
            row.get("lockin_range_rescue_reread", 0) or 0
        ),
        "lockin_input_overload": int(row.get("lockin_input_overload", 0) or 0),
        "lockin_output_overload_final": int(
            row.get("lockin_output_overload_final", 0) or 0
        ),
        "p_dc_W": _finite_json_number(row.get("p_dc")),
        "smu_i_A": _finite_json_number(row.get("smu_i")),
        "smu_v_read_V": _finite_json_number(row.get("smu_v_read")),
        "smu_v_measured_V": _finite_json_number(row.get("smu_v_measured")),
        "smu_v_set_V": _finite_json_number(row.get("smu_v_set")),
        "compliance_tripped": int(row.get("tripped", 0) or 0),
        "ac_vpp": _finite_json_number(row.get("ac_vpp")),
        "ac_measurement_window_s": _finite_json_number(row.get("ac_window_s")),
    }


def make_hysteresis_progress_json_callback(path, pixel=None):
    """Create an atomic cumulative live-trace writer for GUI consumers.

    ``run_dc_hysteresis_sweep`` emits one small event at a time.  The callback
    stores the complete point list on every update so a polling GUI cannot
    miss points between refreshes.  A new sweep id (for example adaptive
    recon followed by the main loop) resets the trace cleanly.
    """
    live_path = os.path.abspath(os.fspath(path))
    points = []
    current_sweep_id = None
    sweep_metadata = {}

    try:
        pixel_number = int(str(pixel).strip())
    except (TypeError, ValueError):
        digits = "".join(ch for ch in str(pixel or "") if ch.isdigit())
        pixel_number = int(digits) if digits else None

    def write_event(event):
        nonlocal current_sweep_id
        event = dict(event or {})
        event_type = str(event.get("event", "") or "point")
        sweep_id = str(event.get("sweep_id", "") or event.get("out_dir", ""))
        if event_type == "start" or sweep_id != current_sweep_id:
            points.clear()
            current_sweep_id = sweep_id
            sweep_metadata.clear()
        for key in ("voltage_grid_profile", "voltage_levels_v", "vmax_v"):
            if key in event:
                sweep_metadata[key] = event[key]

        point = event.get("point")
        if isinstance(point, dict):
            compact = _hysteresis_live_point(point)
            point_number = max(1, int(event.get("point_index", len(points) + 1) or 1))
            if point_number <= len(points):
                points[point_number - 1] = compact
                del points[point_number:]
            else:
                points.append(compact)

        now = time.time()
        payload = {
            "schema_version": 1,
            "pixel": pixel_number,
            "event": event_type,
            "complete": event_type == "complete",
            "sweep_id": current_sweep_id,
            "sweep_stage": str(event.get("sweep_stage", "") or "main"),
            "run_label": str(event.get("run_label", "") or ""),
            "out_dir": str(event.get("out_dir", "") or ""),
            "point_index": int(event.get("point_index", len(points)) or 0),
            "total_points": int(event.get("total_points", 0) or 0),
            "points": list(points),
            "updated": datetime.now().isoformat(),
            "updated_ts": now,
        }
        payload.update(sweep_metadata)
        for key in (
            "ac_output_on",
            "voltage_sequence",
            "v_dc",
            "branch",
            "cycle",
            "ac_vpp",
            "voltage_grid_profile",
            "voltage_levels_v",
            "vmax_v",
            "lockin_range_mode",
        ):
            if key in event:
                payload[key] = event[key]
        if event_type == "complete":
            metrics_path = os.path.join(
                str(event.get("out_dir", "") or ""),
                "dc_hysteresis_metrics.json",
            )
            try:
                with open(metrics_path, "r", encoding="utf-8") as handle:
                    metrics = json.load(handle)
                if isinstance(metrics, dict):
                    payload["metrics"] = metrics
            except Exception:
                pass
        os.makedirs(os.path.dirname(live_path) or ".", exist_ok=True)
        tmp_path = live_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
        os.replace(tmp_path, live_path)

    return write_event


def run_dc_hysteresis_sweep(
    smu, det, lockin, sens_idx,
    out_dir, run_label, peak,
    hold_voltage_v,
    poling_dwell_s=None,
    start_from_zero=False,
    cycles=1,
    min_dwell_s=None,
    lockin_settle_s=None,
    lockin_avg_readings=None,
    lockin_read_delay_s=None,
    lockin_tc_index=None,
    lockin_slope_index=None,
    lockin_sensitivity_index=None,
    lockin_range_mode="fixed",
    funcgen=None,
    ac_vpp=None,
    adaptive_fine=False,
    fine_windows=None,
    voltage_step_v=None,
    vmax_v=None,
    run_analysis=True,
    progress_callback=None,
    cancel_callback=None,
):
    """At the peak (theta_i, anl_angle, vpp) condition, sweep DC poling voltage
    +Vmax -> -Vmax -> +Vmax, bounded by the production +/-40 V ceiling.

    poling_dwell_s controls the DC-only hold time at each voltage BEFORE the
    AC/lock-in window starts (defaults to DC_POLING_DWELL_S).  The function
    generator output remains OFF during the SMU ramp, preset, and complete
    poling dwell.  It is enabled only for lock-in settling/averaging, then
    disabled before the next DC step.

    v2 configuration options:
      cycles            - number of full down+up loops (>=2 separates wake-up
                          transients from the steady loop; metrics use the
                          last cycle).
      min_dwell_s       - explicit dwell floor. None keeps the legacy 30 s
                          floor; pass e.g. 0.5 for fast "map-mode" loops.
                          NOTE: loop shape is dwell(rate)-dependent - keep the
                          dwell fixed within a campaign for comparability.
      lockin_settle_s / lockin_avg_readings / lockin_read_delay_s -
                          per-run overrides of the module-level lock-in window.
      lockin_tc_index   - explicitly program the DSP7230 time constant (and
                          optionally lockin_slope_index) instead of trusting
                          front-panel state; settle is auto-raised to >=5*TC.
      lockin_sensitivity_index - explicitly set the starting DSP7230 voltage
                          sensitivity. It is also the range restored after a
                          predictive hysteresis sweep.
      lockin_range_mode - ``fixed`` preserves that sensitivity for the whole
                          loop. ``predictive_hysteretic`` selects among 5, 10,
                          20, 50, 100 and 200 uV RMS full scale while AC is
                          off, using prior branch points and conservative
                          hysteresis to avoid noise-driven range chatter.
      funcgen + ac_vpp  - gate the requested AC dither around each optical
                          measurement only (restored OFF afterwards).
                          Recommended 1-4 Vpp so the dither is small compared
                          to the coercive window.
      adaptive_fine     - run a coarse recon loop first and centre the fine
                          grid on the detected coercive voltages.
      fine_windows      - explicit fine-grid windows [(lo, hi), ...]; []
                          means coarse-only. None selects the standard
                          centre-dense one-pass grid.
      voltage_step_v    - optional uniform voltage spacing for a deliberately
                          different grid. When supplied, it takes precedence
                          over the standard/adaptive/fine-window profiles.
      vmax_v            - symmetric trajectory endpoint. Defaults to 40 V and
                          may be reduced per run, but never raised above 40 V.
      run_analysis      - compute signed-loop metrics + plots via
                          pockels_hysteresis_analysis after saving the CSV.
      progress_callback - optional callable receiving start/point/complete
                          events. Callback failures are warned and isolated
                          from acquisition.
      cancel_callback   - optional control-flow checkpoint. It is polled
                          during long waits and any exception it raises is
                          deliberately propagated after safe sweep cleanup.

    Records per DC step: scope DC power, lock-in mag/phase (+X/Y), a DC-only
    SMU current/programmed-level/terminal-voltage snapshot taken before AC is
    enabled, the AC-on window duration, and the compliance-trip flag.
    Saves CSV + raw samples + 3-panel hysteresis PNG (+ metrics JSON and
    signed-loop PNG when run_analysis is enabled).
    """
    hyst_vmax = validate_hysteresis_voltage_limit(
        DC_HYST_VMAX if vmax_v is None else vmax_v,
        "DC hysteresis Vmax",
    )

    # Entry interlock: callers can arrive from an AC-on optical phase.  Force
    # CH1 off immediately, before lock-in setup, trajectory preparation, or
    # any SMU output/level operation performed by this routine.
    if funcgen is not None:
        _funcgen_set_sweep_output(
            funcgen,
            False,
            verify_command=True,
            required=True,
            context="hysteresis entry interlock",
        )

    os.makedirs(out_dir, exist_ok=True)

    if poling_dwell_s is None:
        poling_dwell_s = DC_POLING_DWELL_S
    poling_dwell_s = float(poling_dwell_s)
    dwell_floor = (DC_HYST_MIN_POLING_DWELL_S if min_dwell_s is None
                   else max(0.0, float(min_dwell_s)))
    if poling_dwell_s < dwell_floor:
        print(
            f"  [WARN] Requested DC hysteresis dwell {poling_dwell_s:.2f}s is below "
            f"the enforced minimum {dwell_floor:.2f}s; using "
            f"{dwell_floor:.2f}s."
        )
        poling_dwell_s = dwell_floor

    # Per-run lock-in window (defaults preserve the module-level values).
    settle_s = LOCKIN_SETTLE_S if lockin_settle_s is None else float(lockin_settle_s)
    avg_n = LOCKIN_AVG_READINGS if lockin_avg_readings is None else int(lockin_avg_readings)
    read_delay_s = (LOCKIN_READ_DELAY_S if lockin_read_delay_s is None
                    else float(lockin_read_delay_s))

    range_mode = str(lockin_range_mode or "fixed").strip().lower().replace("-", "_")
    if range_mode in {"dynamic", "predictive"}:
        range_mode = "predictive_hysteretic"
    if range_mode not in {"fixed", "predictive_hysteretic"}:
        raise ValueError(
            "lockin_range_mode must be 'fixed' or 'predictive_hysteretic'."
        )

    if lockin_sensitivity_index is not None:
        requested_sens_idx = int(lockin_sensitivity_index)
        if requested_sens_idx not in SENS_TABLE_VOLTS:
            raise ValueError(
                f"DSP7230 sensitivity index {requested_sens_idx} is not a "
                "valid voltage sensitivity."
            )
        lockin.set_sensitivity(requested_sens_idx)
        time.sleep(0.2)
        sens_idx = requested_sens_idx
        try:
            sens_readback = int(lockin.get_sensitivity_index())
        except Exception:
            sens_readback = requested_sens_idx
        if sens_readback != requested_sens_idx:
            raise RuntimeError(
                f"DSP7230 sensitivity readback {sens_readback} does not "
                f"match requested {requested_sens_idx}."
            )
        print(
            f"  [LOCKIN] Starting sensitivity index {requested_sens_idx} "
            f"-> {SENS_TABLE_VOLTS[requested_sens_idx] * 1e6:g} uV RMS full scale"
        )
    range_restore_idx = int(sens_idx)

    # Explicit lock-in filter programming (v2): the legacy path inherited
    # whatever TC/slope the front panel happened to hold, which makes the
    # settle time meaningless. When a TC index is supplied, program + verify
    # it and keep the settle window at >= 5*TC.
    if lockin_tc_index is not None:
        try:
            lockin.set_time_constant(int(lockin_tc_index))
            if lockin_slope_index is not None:
                lockin.set_filter_slope(int(lockin_slope_index))
            time.sleep(0.2)
            tc_s = float(lockin.get_time_constant_seconds())
            if settle_s < 5.0 * tc_s:
                print(f"  [LOCKIN] settle {settle_s:.2f}s raised to "
                      f"{5.0 * tc_s:.2f}s (5xTC)")
                settle_s = 5.0 * tc_s
            print(f"  [LOCKIN] TC index {int(lockin_tc_index)} -> {tc_s*1e3:.0f} ms"
                  + (f", slope index {int(lockin_slope_index)}"
                     if lockin_slope_index is not None else ""))
        except Exception as exc:
            print(f"  [WARN] Could not program lock-in TC/slope: {exc}")

    # Select the AC dither for the loop.  Its amplitude is programmed with
    # CH1 OFF before the trajectory; output gating happens only around each
    # measurement window.  Changing 9 -> 4 Vpp while ON can cross a TGF3162
    # attenuator boundary, so amplitude changes must never happen live.
    peak_vpp = peak.get("vpp") if isinstance(peak, dict) else None
    dither_vpp = peak_vpp
    restore_vpp = None
    if funcgen is not None and ac_vpp is not None:
        restore_vpp = peak_vpp
        dither_vpp = float(ac_vpp)

    range_controller = None
    if range_mode == "predictive_hysteretic":
        try:
            lockin.set_automatic_ac_gain(False)
        except Exception as exc:
            print(f"  [WARN] Could not reaffirm fixed DSP7230 AC gain: {exc}")
        range_controller = PredictiveHystereticRangeController(
            SENS_TABLE_VOLTS,
            sens_idx,
            range_indices=DC_HYST_DYNAMIC_RANGE_INDICES,
            initial_anchor_v=_hysteresis_peak_lockin_anchor_v(peak, dither_vpp),
            target_fraction=DC_HYST_DYNAMIC_RANGE_TARGET_FRACTION,
            rescue_fraction=DC_HYST_DYNAMIC_RANGE_RESCUE_FRACTION,
            safety_sigma=DC_HYST_DYNAMIC_RANGE_SAFETY_SIGMA,
            noise_floor_v=DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V,
            narrow_confirmations=DC_HYST_DYNAMIC_RANGE_NARROW_CONFIRMATIONS,
            hold_points_after_change=DC_HYST_DYNAMIC_RANGE_HOLD_POINTS,
        )

    uniform_step_v = None
    if voltage_step_v is not None:
        uniform_step_v = float(voltage_step_v)
        if not math.isfinite(uniform_step_v) or uniform_step_v <= 0.0:
            raise ValueError("DC hysteresis voltage step must be finite and positive.")
        if uniform_step_v > 2.0 * hyst_vmax:
            raise ValueError(
                "DC hysteresis voltage step cannot exceed the full "
                f"{2.0 * hyst_vmax:g} V sweep span."
            )
        if adaptive_fine or fine_windows is not None:
            print(
                f"  [GRID] Uniform {uniform_step_v:g} V hysteresis spacing "
                "overrides adaptive/fine windows."
            )
        adaptive_fine = False
        fine_windows = []

    # Adaptive fine window (v2): quick coarse-only loop, detect the coercive
    # voltages from the signed response, then centre the fine grid on them.
    if adaptive_fine and fine_windows is None:
        recon_dir = os.path.join(out_dir, "recon_coarse")
        print("  [ADAPTIVE] Coarse recon loop to locate the coercive region...")
        sens_idx = run_dc_hysteresis_sweep(
            smu, det, lockin, sens_idx,
            recon_dir, run_label + "_recon", peak,
            hold_voltage_v,
            poling_dwell_s=min(poling_dwell_s, DC_HYST_RECON_DWELL_S),
            start_from_zero=False,
            cycles=1,
            min_dwell_s=0.0,
            lockin_settle_s=settle_s,
            lockin_avg_readings=avg_n,
            lockin_read_delay_s=read_delay_s,
            lockin_range_mode=range_mode,
            funcgen=funcgen,
            ac_vpp=dither_vpp,
            fine_windows=[],
            vmax_v=hyst_vmax,
            run_analysis=False,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
        )
        hold_voltage_v = 0.0  # the recon loop ends ramped to 0 V
        try:
            from pockels_hysteresis_analysis import detect_coercive_windows
            fine_windows = detect_coercive_windows(
                os.path.join(recon_dir, "dc_hysteresis.csv"),
                half_width_v=DC_HYST_ADAPTIVE_HALF_WIDTH_V,
                vmax=hyst_vmax,
            )
            print(f"  [ADAPTIVE] Fine windows around detected V_c: "
                  + ", ".join(f"[{lo:+.1f}, {hi:+.1f}] V" for lo, hi in fine_windows))
        except Exception as exc:
            print(f"  [WARN] Adaptive window detection failed ({exc}); "
                  "using the standard centre-dense one-pass grid.")
            fine_windows = None

    voltages, branches, cycle_nums = _build_hysteresis_trajectory(
        hyst_vmax, start_from_zero=start_from_zero,
        cycles=cycles, fine_windows=fine_windows, step=uniform_step_v,
    )
    n = len(voltages)
    resolved_fine_windows = (
        _resolve_fine_windows(hyst_vmax, fine_windows)
        if fine_windows
        else []
    )
    if uniform_step_v is not None:
        grid_profile = "uniform"
    elif fine_windows is None:
        grid_profile = DC_HYST_DEFAULT_GRID_PROFILE
    elif resolved_fine_windows:
        grid_profile = (
            "adaptive_vc_windows" if adaptive_fine else "explicit_fine_windows"
        )
    else:
        grid_profile = "coarse_only"
    voltage_levels_v = sorted({float(v) for v in voltages})
    progress_callback_warned = False
    sweep_out_dir = os.path.abspath(out_dir)
    sweep_stage = (
        "recon"
        if "recon_coarse" in os.path.normpath(sweep_out_dir).split(os.sep)
        else "main"
    )

    def emit_progress(event_type, point=None, point_index=0, **state):
        nonlocal progress_callback_warned
        if progress_callback is None:
            return
        event = {
            "event": str(event_type),
            "sweep_id": sweep_out_dir,
            "sweep_stage": sweep_stage,
            "run_label": str(run_label),
            "out_dir": sweep_out_dir,
            "point_index": int(point_index),
            "total_points": int(n),
        }
        event.update(state)
        if point is not None:
            event["point"] = dict(point)
        try:
            progress_callback(event)
        except Exception as exc:
            if not progress_callback_warned:
                print(f"  [WARN] Hysteresis live-progress update failed: {exc}")
                progress_callback_warned = True

    print(f"\n{'=' * 70}")
    print(f"  DC HYSTERESIS SWEEP")
    print(f"{'=' * 70}")
    print(f"  Peak conditions: theta_i={peak['theta_i']:.1f} deg, "
          f"V_AC={peak['vpp']} Vpp, ANL={peak['anl_angle']:.2f} deg")
    print(f"  Peak lock-in mag: {peak['mag']*1e6:.3f} uV")
    traj_label = ("0 -> +" if start_from_zero else "+")
    print(f"  Trajectory: {traj_label}{hyst_vmax:.0f} -> "
          f"-{hyst_vmax:.0f} -> +{hyst_vmax:.0f} V x {max(1, int(cycles))} cycle(s)")
    spacing_label = (
        f"{uniform_step_v:g} V uniform"
        if uniform_step_v is not None
        else describe_hysteresis_spacing(fine_windows, hyst_vmax)
    )
    print(f"  Grid       : {grid_profile}")
    print(f"  Spacing    : {spacing_label} ({n} points)")
    if dither_vpp is not None:
        print(
            f"  AC probe   : {dither_vpp:g} Vpp @ {FUNCGEN_FREQ_HZ:g} Hz; "
            "gated ON for measurement only"
        )
    if range_controller is not None:
        ladder_text = ", ".join(
            f"{SENS_TABLE_VOLTS[index] * 1e6:g}"
            for index in range_controller.range_indices
        )
        print(
            "  LI ranging : predictive hysteretic "
            f"({ladder_text} uV RMS FS); changes occur with AC OFF"
        )
    else:
        try:
            fixed_fs = float(SENS_TABLE_VOLTS[int(sens_idx)])
        except (KeyError, IndexError, TypeError, ValueError):
            fixed_fs = float("nan")
        print(
            "  LI ranging : fixed"
            + (
                f" at {fixed_fs * 1e6:g} uV RMS FS"
                if math.isfinite(fixed_fs)
                else f" at sensitivity index {int(sens_idx)}"
            )
        )
    print(f"  Per-step sequence: DC-only {poling_dwell_s:.2f} s poling -> "
          f"{settle_s:.2f} s lock-in settle + "
          f"{avg_n}x{read_delay_s:.2f} s AC-on averaging -> AC OFF")
    time_per_point_s = (DC_RAMP_PRESET_S + poling_dwell_s + settle_s
                        + avg_n * read_delay_s
                        + (0.4 if funcgen is not None else 0.0))
    print(f"  Est. duration  : {n * time_per_point_s / 60.0:.1f} min")

    if funcgen is not None:
        if dither_vpp is None:
            raise RuntimeError(
                "DC hysteresis requires a finite AC dither amplitude, but none "
                "was available from the peak or --hyst-ac-vpp setting."
            )
        if not math.isfinite(float(dither_vpp)) or float(dither_vpp) <= 0.0:
            raise RuntimeError(
                f"DC hysteresis AC dither must be positive; got {dither_vpp!r}."
            )
        _prepare_hysteresis_ac_drive(funcgen, float(dither_vpp))
        print(
            f"  [FUNCGEN] AC remains OFF for every DC ramp/poling dwell"
            + (f" (will restore {float(restore_vpp):g} Vpp while OFF)"
               if restore_vpp is not None else "")
        )

    # Only after AC is confirmed OFF and its future amplitude is prepared do
    # we verify/enable the DC source. Without a live SMU output, set_voltage()
    # changes only the register and the optical data would be meaningless.
    if not smu_ensure_output_on(smu):
        print(f"  [ERROR] Cannot proceed - SMU OUTPut would not enable. "
              f"Aborting hysteresis sweep.")
        return sens_idx
    # Shape/trigger/slew were configured before OUTPUT ON.  The historical
    # code re-issued those source-control commands here while OUTPUT was live;
    # the same runs repeatedly reported 103/104 queue errors. Keep live setup
    # untouched and clear only a pre-existing protection latch.
    try:
        queued_before_hysteresis = smu.check_errors()
    except Exception:
        queued_before_hysteresis = []
    if queued_before_hysteresis:
        print(
            "  [WARN] SMU errors queued before hysteresis setup "
            f"(not caused by this sweep): {queued_before_hysteresis}"
        )
    try:
        smu.write("SYSTem:PROTection:CURRent:CLEAr")
        clear_errs = smu.check_errors()
    except Exception as exc:
        print(f"  [WARN] SMU protection-clear command failed: {exc}")
        clear_errs = []
    if clear_errs:
        print(f"  [WARN] SMU protection-clear errors: {clear_errs}")

    emit_progress(
        "start",
        ac_output_on=False,
        voltage_sequence="dc_only_poling_then_ac_measurement",
        voltage_grid_profile=grid_profile,
        voltage_levels_v=voltage_levels_v,
        vmax_v=hyst_vmax,
        lockin_range_mode=range_mode,
    )

    # Bring SMU from its current hold to the first trajectory point. For the
    # saturated loop this is +Vmax; for a virgin/reset loop this is 0 V.
    start_v = float(voltages[0]) if len(voltages) else 0.0
    print(
        f"  Ramping SMU {hold_voltage_v:+.1f} V -> {start_v:+.1f} V "
        "to start (AC OFF)..."
    )
    smu_ramp_to(smu, hold_voltage_v, start_v)

    rows = []
    raw_rows = []
    t0 = time.time()
    last_v = start_v
    n_trips = 0
    n_range_changes = 0
    n_range_rescues = 0
    n_lockin_input_overloads = 0
    n_lockin_output_overloads = 0

    try:
        for i, (v, branch, cyc) in enumerate(zip(voltages, branches, cycle_nums)):
            _hysteresis_cancel_checkpoint(cancel_callback)
            # Verify the DC source before every point.  Silently re-enabling
            # a dropped SMU output invalidates the dwell because the 4201 has
            # a multi-second output-start banner, so fail closed instead.
            try:
                output_state = smu.query("OUTPut:STATe?").strip().upper()
            except Exception as exc:
                raise RuntimeError(
                    f"Could not verify SMU output before hysteresis point "
                    f"{i + 1}/{n}; AC is OFF and the sweep is aborting."
                ) from exc
            if output_state not in ("1", "ON"):
                raise RuntimeError(
                    f"SMU output dropped before hysteresis point {i + 1}/{n} "
                    f"(state={output_state!r}). AC is OFF; aborting instead "
                    "of silently re-enabling and shortening the DC pole."
                )

            # Ramp between adjacent points if step is large (here step==2.5V is fine
            # to apply directly; SMU_RAMP_STEP_V chunking only kicks in for big jumps)
            if abs(float(v) - last_v) > SMU_RAMP_STEP_V:
                smu_ramp_to(smu, last_v, float(v))
            else:
                smu.set_voltage(float(v))
            last_v = float(v)

            range_predicted_v = float("nan")
            range_noise_prior_v = float("nan")
            range_upper_bound_v = float("nan")
            range_requested_idx = int(sens_idx)
            range_change_reason = "fixed_range"
            range_changed = False
            range_rescue_reread = False
            if range_controller is not None:
                decision = range_controller.plan(float(v), str(branch), int(cyc))
                range_predicted_v = float(decision.predicted_magnitude_v)
                range_noise_prior_v = float(decision.noise_sigma_v)
                range_upper_bound_v = float(decision.upper_bound_v)
                range_requested_idx = int(decision.requested_index)
                range_change_reason = str(decision.reason)
                if int(decision.sensitivity_index) != int(sens_idx):
                    previous_idx = int(sens_idx)
                    lockin.set_sensitivity(int(decision.sensitivity_index))
                    sens_idx = int(decision.sensitivity_index)
                    range_changed = True
                    n_range_changes += 1
                    print(
                        "    [LOCKIN RANGE] AC OFF: "
                        f"{SENS_TABLE_VOLTS[previous_idx] * 1e6:g} -> "
                        f"{SENS_TABLE_VOLTS[sens_idx] * 1e6:g} uV RMS FS "
                        f"({range_change_reason}; predicted upper bound "
                        f"{range_upper_bound_v * 1e6:.2f} uV)."
                    )
            emit_progress(
                "dc_poling",
                point_index=i + 1,
                ac_output_on=False,
                v_dc=float(v),
                branch=str(branch),
                cycle=int(cyc),
                lockin_range_mode=range_mode,
                lockin_sensitivity_index=int(sens_idx),
                lockin_sensitivity_fullscale_v=_sensitivity_fullscale_v(sens_idx),
            )
            _hysteresis_interruptible_sleep(
                DC_RAMP_PRESET_S,
                cancel_callback,
            )

            # DC-only poling dwell.  CH1 was prepared OFF before the trajectory
            # and the preceding point's measurement finally-block also forced
            # it OFF, so the SMU is the only source acting during this phase.
            if poling_dwell_s > 0.0:
                _hysteresis_interruptible_sleep(
                    poling_dwell_s,
                    cancel_callback,
                )

            # Capture the electrical/leakage snapshot while AC is still OFF.
            # This prevents the 30 kHz probe from contaminating the SMU current
            # value and makes the saved electrical loop explicitly DC-only.
            i_meas = float("nan")
            v_set = float("nan")
            v_measured = float("nan")
            for read_attempt in range(
                max(1, int(SMU_DC_READBACK_ATTEMPTS))
            ):
                try:
                    i_meas = float(smu.measure_primary())
                except Exception:
                    i_meas = float("nan")
                try:
                    v_set = float(
                        smu.query(
                            "SOURce:VOLTage:FIXed:LEVel?"
                        ).split(",")[0]
                    )
                except Exception:
                    v_set = float("nan")
                try:
                    v_measured = float(smu.measure_secondary())
                except Exception:
                    v_measured = float("nan")
                if all(
                    math.isfinite(value)
                    for value in (i_meas, v_set, v_measured)
                ):
                    break
                if read_attempt + 1 < max(
                    1, int(SMU_DC_READBACK_ATTEMPTS)
                ):
                    _hysteresis_interruptible_sleep(
                        SMU_DC_READBACK_RETRY_S,
                        cancel_callback,
                    )

            missing_readbacks = [
                name
                for name, value in (
                    ("primary current", i_meas),
                    ("voltage setpoint", v_set),
                    ("measured terminal voltage", v_measured),
                )
                if not math.isfinite(value)
            ]
            if missing_readbacks:
                raise RuntimeError(
                    f"SMU DC-only telemetry unavailable at hysteresis point "
                    f"{i + 1}/{n} after "
                    f"{max(1, int(SMU_DC_READBACK_ATTEMPTS))} attempts "
                    f"({', '.join(missing_readbacks)}). AC is still OFF; "
                    "aborting instead of saving an unverified point."
                )
            # Retain the historical column name as an alias for downstream
            # analysis, but it now always contains actual terminal voltage.
            v_read = v_measured

            if (
                math.isfinite(v_set)
                and abs(v_set - float(v)) > 0.01
            ):
                raise RuntimeError(
                    f"SMU setpoint readback mismatch at hysteresis point "
                    f"{i + 1}/{n}: requested {float(v):+.3f} V, register "
                    f"reports {v_set:+.3f} V."
                )
            if (
                math.isfinite(v_measured)
                and abs(v_measured - float(v))
                > float(SMU_DC_READBACK_TOLERANCE_V)
            ):
                raise RuntimeError(
                    f"SMU measured-voltage mismatch during DC-only poling at "
                    f"point {i + 1}/{n}: requested {float(v):+.3f} V, "
                    f"measured {v_measured:+.3f} V (limit "
                    f"{float(SMU_DC_READBACK_TOLERANCE_V):.3f} V). "
                    "AC is still OFF; aborting before the optical read."
                )

            trip_during_poling = False
            try:
                tripped = smu.query(
                    "SYSTem:PROTection:CURRent:TRIPped?"
                ).strip()
                if tripped in ("1", "ON"):
                    trip_during_poling = True
                    smu.write("SYSTem:PROTection:CURRent:CLEAr")
            except Exception:
                pass

            # Gate AC on only for the optical read.  The try/finally begins
            # before OUTPUT ON so even an enable/read/cancel failure attempts
            # a verified-command OUTPUT OFF before control can unwind.
            ac_window_started = float("nan")
            ac_window_s = float("nan")
            measurement_completed = False
            range_attempt_records = []
            final_diagnostics = {
                "overload_byte": None,
                "status_byte": None,
                "output_overload": False,
                "input_overload": False,
            }
            lockin_noise_sigma_v = float(DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V)
            try:
                if funcgen is not None:
                    _funcgen_assert_no_execution_error(
                        funcgen,
                        f"DC-only hysteresis hold at {float(v):+.3f} V",
                    )
                    ac_window_started = time.time()
                    _funcgen_set_sweep_output(
                        funcgen,
                        True,
                        verify_command=True,
                        required=True,
                        context=(
                            f"hysteresis measurement {i + 1}/{n} "
                            f"at {float(v):+.3f} V"
                        ),
                    )
                emit_progress(
                    "ac_measurement",
                    point_index=i + 1,
                    ac_output_on=(funcgen is not None),
                    v_dc=float(v),
                    branch=str(branch),
                    cycle=int(cyc),
                    ac_vpp=(
                        float(dither_vpp)
                        if dither_vpp is not None
                        else None
                    ),
                )

                # The lock-in settle clock starts only after OUTPUT ON has
                # completed and its command/error checks have passed.
                _hysteresis_interruptible_sleep(
                    settle_s,
                    cancel_callback,
                )
                _hysteresis_cancel_checkpoint(cancel_callback)
                try:
                    p_dc, v_scope = det.read_power_w_stable()
                except Exception as scope_exc:
                    raise RuntimeError(
                        "Oscilloscope detector read failed at hysteresis point "
                        f"{i + 1}/{n} ({float(v):+.3f} V): {scope_exc}"
                    ) from scope_exc
                if not (
                    math.isfinite(float(p_dc))
                    and math.isfinite(float(v_scope))
                ):
                    raise RuntimeError(
                        "Oscilloscope detector telemetry unavailable at hysteresis "
                        f"point {i + 1}/{n} ({float(v):+.3f} V) after its read "
                        "retries; aborting instead of continuing with a dead "
                        "instrument session."
                    )

                # Predictive selection is the normal path.  This loop exists
                # only as a rare fail-safe: an unexpectedly large *averaged*
                # response or a real X/Y output-overload widens the range and
                # reacquires the complete window.  It never narrows from a
                # single noisy point.
                max_attempts = (
                    int(DC_HYST_DYNAMIC_RANGE_MAX_RESCUES) + 1
                    if range_controller is not None else 1
                )
                for range_attempt in range(max_attempts):
                    acquisition_sens_idx = int(sens_idx)
                    (
                        avg_mag,
                        avg_phase,
                        std_mag,
                        std_phase,
                        raw_mags,
                        raw_phases,
                        raw_ts,
                    ) = read_lockin_averaged(lockin, avg_n, read_delay_s)
                    lockin_noise_sigma_v = _lockin_xy_noise_sigma(
                        raw_mags, raw_phases
                    )
                    final_diagnostics = _lockin_range_diagnostics(lockin)
                    attempt_record = {
                        "attempt": int(range_attempt),
                        "sens_idx": acquisition_sens_idx,
                        "sens_v": _sensitivity_fullscale_v(
                            acquisition_sens_idx
                        ),
                        "raw_mags": list(raw_mags),
                        "raw_phases": list(raw_phases),
                        "raw_ts": list(raw_ts),
                        "discarded_for_range_rescue": False,
                        **final_diagnostics,
                    }
                    range_attempt_records.append(attempt_record)

                    if range_controller is None:
                        break
                    next_idx = range_controller.emergency_widen_index(
                        avg_mag,
                        lockin_noise_sigma_v,
                        output_overload=bool(
                            final_diagnostics["output_overload"]
                        ),
                    )
                    if next_idx == acquisition_sens_idx:
                        if final_diagnostics["output_overload"]:
                            print(
                                "    [LOCKIN RANGE] Output overload remains at "
                                f"maximum {SENS_TABLE_VOLTS[acquisition_sens_idx] * 1e6:g} "
                                "uV RMS FS; point is flagged."
                            )
                        break
                    if range_attempt + 1 >= max_attempts:
                        range_controller.current_index = acquisition_sens_idx
                        print(
                            "    [LOCKIN RANGE] Rescue limit reached before another "
                            "full window could be acquired; retaining and flagging "
                            "the last completed range."
                        )
                        break

                    attempt_record["discarded_for_range_rescue"] = True
                    lockin.set_sensitivity(int(next_idx))
                    sens_idx = int(next_idx)
                    range_changed = True
                    range_rescue_reread = True
                    n_range_changes += 1
                    n_range_rescues += 1
                    range_change_reason = (
                        f"{range_change_reason}+emergency_widen"
                    )
                    print(
                        "    [LOCKIN RANGE] Unexpected high signal: "
                        f"{SENS_TABLE_VOLTS[acquisition_sens_idx] * 1e6:g} -> "
                        f"{SENS_TABLE_VOLTS[sens_idx] * 1e6:g} uV RMS FS; "
                        "discarding this window and reacquiring once settled."
                    )
                    _hysteresis_interruptible_sleep(
                        settle_s,
                        cancel_callback,
                    )
                _hysteresis_cancel_checkpoint(cancel_callback)
                measurement_completed = True
            finally:
                if funcgen is not None:
                    try:
                        _funcgen_set_sweep_output(
                            funcgen,
                            False,
                            verify_command=True,
                            required=True,
                            context=(
                                f"hysteresis measurement cleanup {i + 1}/{n} "
                                f"at {float(v):+.3f} V"
                            ),
                        )
                    except Exception as off_exc:
                        if measurement_completed:
                            raise
                        # Preserve the original acquisition/cancellation
                        # exception; the outer sweep cleanup attempts OFF again.
                        print(
                            "  [WARN] AC OUTPUT OFF verification also failed "
                            f"while unwinding point {i + 1}: {off_exc}"
                        )
                    finally:
                        if math.isfinite(ac_window_started):
                            ac_window_s = max(
                                0.0,
                                time.time() - ac_window_started,
                            )

            point_input_overload = any(
                bool(attempt.get("input_overload"))
                for attempt in range_attempt_records
            )
            point_output_overload_observed = any(
                bool(attempt.get("output_overload"))
                for attempt in range_attempt_records
            )
            point_output_overload_final = bool(
                final_diagnostics.get("output_overload")
            )
            if point_input_overload:
                n_lockin_input_overloads += 1
                print(
                    "  [WARN] DSP7230 input-overload status at this point; "
                    "changing sensitivity cannot repair broadband input overload, "
                    "so the row is explicitly flagged."
                )
            if point_output_overload_observed:
                n_lockin_output_overloads += 1
                if range_controller is None:
                    print(
                        "    [LOCKIN] OVERLOAD at fixed "
                        f"{_sensitivity_fullscale_v(sens_idx) * 1e6:g} uV RMS "
                        "full scale; range left unchanged and row flagged."
                    )

            # Catch a compliance event caused specifically by the short AC
            # window as well as one caused by the preceding DC-only poling.
            trip_during_measurement = False
            try:
                tripped = smu.query(
                    "SYSTem:PROTection:CURRent:TRIPped?"
                ).strip()
                if tripped in ("1", "ON"):
                    trip_during_measurement = True
                    smu.write("SYSTem:PROTection:CURRent:CLEAr")
            except Exception:
                pass
            tripped_flag = int(
                bool(trip_during_poling or trip_during_measurement)
            )
            if tripped_flag:
                n_trips += 1
                trip_stages = []
                if trip_during_poling:
                    trip_stages.append("DC-only poling")
                if trip_during_measurement:
                    trip_stages.append("AC measurement")
                print(
                    f"  [WARN] Current-compliance trip at point {i + 1} "
                    f"(V_DC={v:+.2f} V; {' + '.join(trip_stages)}) - "
                    "recorded + cleared."
                )

            li_x = avg_mag * math.cos(math.radians(avg_phase))
            li_y = avg_mag * math.sin(math.radians(avg_phase))

            if range_controller is not None:
                range_controller.record(
                    float(v),
                    str(branch),
                    int(cyc),
                    avg_mag,
                    lockin_noise_sigma_v,
                )

            sens_v = _sensitivity_fullscale_v(sens_idx)
            t_now = time.time() - t0
            n_ok = len(raw_mags)
            overload_values = [
                int(attempt["overload_byte"])
                for attempt in range_attempt_records
                if attempt.get("overload_byte") is not None
            ]
            status_values = [
                int(attempt["status_byte"])
                for attempt in range_attempt_records
                if attempt.get("status_byte") is not None
            ]
            overload_byte_observed = 0
            for value in overload_values:
                overload_byte_observed |= value
            status_byte_observed = 0
            for value in status_values:
                status_byte_observed |= value

            row = {
                "idx": i, "t": t_now, "branch": branch, "v_dc": float(v),
                "p_dc": p_dc, "li_mag": avg_mag, "li_phase": avg_phase,
                "sens_v": sens_v, "li_mag_std": std_mag, "li_phase_std": std_phase,
                "n_ok": n_ok,
                "cycle": int(cyc), "li_x": li_x, "li_y": li_y,
                "smu_i": i_meas,
                "smu_v_read": v_read,
                "smu_v_measured": v_measured,
                "smu_v_set": v_set,
                "tripped": tripped_flag,
                "ac_vpp": (float(dither_vpp) if dither_vpp is not None
                           else float("nan")),
                "ac_window_s": ac_window_s,
                "sens_idx": int(sens_idx),
                "lockin_range_mode": range_mode,
                "lockin_range_predicted_v": range_predicted_v,
                "lockin_range_noise_prior_v": range_noise_prior_v,
                "lockin_range_upper_bound_v": range_upper_bound_v,
                "lockin_range_requested_idx": int(range_requested_idx),
                "lockin_range_change_reason": range_change_reason,
                "lockin_range_changed": int(bool(range_changed)),
                "lockin_range_rescue_reread": int(bool(range_rescue_reread)),
                "lockin_noise_sigma_v": float(lockin_noise_sigma_v),
                "lockin_overload_byte": (
                    int(final_diagnostics["overload_byte"])
                    if final_diagnostics.get("overload_byte") is not None else -1
                ),
                "lockin_overload_byte_observed": (
                    int(overload_byte_observed) if overload_values else -1
                ),
                "lockin_status_byte": (
                    int(final_diagnostics["status_byte"])
                    if final_diagnostics.get("status_byte") is not None else -1
                ),
                "lockin_status_byte_observed": (
                    int(status_byte_observed) if status_values else -1
                ),
                "lockin_output_overload_observed": int(
                    bool(point_output_overload_observed)
                ),
                "lockin_output_overload_final": int(
                    bool(point_output_overload_final)
                ),
                "lockin_input_overload": int(bool(point_input_overload)),
            }
            rows.append(row)
            emit_progress(
                "point",
                row,
                i + 1,
                ac_output_on=False,
                v_dc=float(v),
                branch=str(branch),
                cycle=int(cyc),
            )
            for attempt in range_attempt_records:
                attempt_mags = attempt.get("raw_mags", [])
                attempt_phases = attempt.get("raw_phases", [])
                attempt_ts = attempt.get("raw_ts", [])
                for j, (rm, rp) in enumerate(
                    zip(attempt_mags, attempt_phases)
                ):
                    raw_rows.append({
                        "idx": i,
                        "v_dc": float(v),
                        "branch": branch,
                        "sample_j": j,
                        "li_mag": rm,
                        "li_phase": rp,
                        "sample_t": (
                            attempt_ts[j]
                            if j < len(attempt_ts) else float("nan")
                        ),
                        "range_attempt": int(attempt.get("attempt", 0)),
                        "sens_idx": int(attempt.get("sens_idx", sens_idx)),
                        "sens_v": float(attempt.get("sens_v", sens_v)),
                        "discarded_for_range_rescue": int(bool(
                            attempt.get("discarded_for_range_rescue", False)
                        )),
                        "overload_byte": (
                            int(attempt["overload_byte"])
                            if attempt.get("overload_byte") is not None else -1
                        ),
                        "status_byte": (
                            int(attempt["status_byte"])
                            if attempt.get("status_byte") is not None else -1
                        ),
                    })

            eta_s = (n - i - 1) * time_per_point_s
            i_txt = (
                f"I_dc={i_meas*1e6:8.2f} uA  "
                if np.isfinite(i_meas)
                else ""
            )
            v_txt = (
                f"V_smu={v_measured:+6.2f} V  "
                if np.isfinite(v_measured)
                else ""
            )
            ac_txt = (
                f"AC_on={ac_window_s:5.2f}s  "
                if np.isfinite(ac_window_s)
                else ""
            )
            print(f"  [{i+1:3d}/{n}] {branch:>6} V_dc={v:+6.2f} V  "
                  f"P_dc={p_dc*1e3:7.3f} mW  "
                  f"|LI|={avg_mag*1e6:8.3f} uV  "
                  f"phi={avg_phase:+7.2f} deg  "
                  f"FS={sens_v*1e6:5.0f}uV  {i_txt}"
                  f"{v_txt}{ac_txt}"
                  f"ETA {fmt_elapsed(eta_s)}")

        # Ramp down to 0 V before re-applying Phase-A hold
        print(f"  Ramping SMU {last_v:+.1f} V -> 0.0 V (sweep complete)...")
        smu_ramp_to(smu, last_v, 0.0)
        last_v = 0.0
    finally:
        if abs(float(last_v)) > 1e-9:
            try:
                print(
                    f"  [HYSTERESIS CLEANUP] Ramping SMU {last_v:+.1f} V -> "
                    "0.0 V after interrupted sweep..."
                )
                smu_ramp_to(smu, last_v, 0.0)
                last_v = 0.0
            except Exception as exc:
                print(f"  [WARN] Could not ramp interrupted hysteresis sweep to 0 V: {exc}")
        # Always leave the sample-drive output OFF.  Restore the pre-loop
        # amplitude only while OFF; a later workflow must explicitly enable it.
        if funcgen is not None:
            _funcgen_set_sweep_output(
                funcgen,
                False,
                verify_command=False,
                required=False,
                context="hysteresis cleanup",
            )
        if restore_vpp is not None and funcgen is not None:
            try:
                funcgen_set_voltage(funcgen, float(restore_vpp))
                time.sleep(FUNCGEN_SETTLE_S)
                print(
                    f"  [FUNCGEN] AC amplitude restored to {restore_vpp:g} Vpp "
                    "with CH1 OUTPUT OFF"
                )
            except Exception as exc:
                print(f"  [WARN] Could not restore AC dither: {exc}")
        if range_controller is not None and int(sens_idx) != int(range_restore_idx):
            try:
                lockin.set_sensitivity(int(range_restore_idx))
                print(
                    "  [LOCKIN RANGE] Restored post-hysteresis sensitivity to "
                    f"index {int(range_restore_idx)} "
                    f"({SENS_TABLE_VOLTS[int(range_restore_idx)] * 1e6:g} uV RMS FS)."
                )
                sens_idx = int(range_restore_idx)
            except Exception as exc:
                print(
                    "  [WARN] Could not restore the pre-hysteresis DSP7230 "
                    f"sensitivity index {int(range_restore_idx)}: {exc}"
                )
    if n_trips:
        print(f"  [WARN] {n_trips} compliance trip(s) recorded during the loop - "
              f"affected rows have compliance_tripped=1 and should be treated "
              f"as invalid for loop metrics.")
    if n_lockin_input_overloads:
        print(
            f"  [WARN] {n_lockin_input_overloads} point(s) reported DSP7230 "
            "input overload; sensitivity changes cannot correct those rows."
        )

    # --- save CSVs ---
    csv_path = os.path.join(out_dir, "dc_hysteresis.csv")
    with open(csv_path, "w") as f:
        f.write(f"# Run={run_label}\n")
        f.write(f"# theta_i_HWP_deg={peak['theta_i']:.3f}\n")
        f.write(f"# V_AC_Vpp={peak['vpp']}\n")
        f.write(f"# ANL_peak_deg={peak['anl_angle']:.3f}\n")
        f.write(f"# Q_null_deg={peak['q_null']:.3f}\n")
        f.write(f"# A_null_deg={peak['a_null']:.3f}\n")
        for metadata_key, header_key in (
            ("eo_lockin_reference_phase_deg", "EO_lockin_reference_phase_deg"),
            ("rotation_reference_phase_deg", "Rotation_reference_phase_deg"),
            ("dc_fringe_null_power_W", "DC_fringe_null_power_W"),
            ("dc_fringe_quadrature_power_W", "DC_fringe_quadrature_power_W"),
            ("dc_fringe_bright_power_W", "DC_fringe_bright_power_W"),
        ):
            try:
                metadata_value = float(peak.get(metadata_key, float("nan")))
            except (TypeError, ValueError):
                metadata_value = float("nan")
            if np.isfinite(metadata_value):
                f.write(f"# {header_key}={metadata_value:.12g}\n")
        f.write(f"# DC_HYST_VMAX={hyst_vmax}\n")
        f.write(f"# DC_HYST_COARSE_STEP={DC_HYST_COARSE_STEP}\n")
        f.write(f"# DC_HYST_FINE_STEP={DC_HYST_FINE_STEP}\n")
        f.write(f"# DC_HYST_FINE_LIMIT={DC_HYST_FINE_LIMIT}\n")
        f.write(f"# DC_HYST_GRID_PROFILE={grid_profile}\n")
        f.write(f"# DC_HYST_SPACING={spacing_label}\n")
        f.write(
            "# DC_HYST_VOLTAGE_LEVELS_V="
            f"{json.dumps(voltage_levels_v, separators=(',', ':'))}\n"
        )
        if uniform_step_v is not None:
            f.write(f"# DC_HYST_UNIFORM_STEP={uniform_step_v}\n")
        f.write(f"# Trajectory={'0->+->-->+ (virgin)' if start_from_zero else '+->->+ (saturated)'}\n")
        f.write(f"# Cycles={max(1, int(cycles))}\n")
        f.write(f"# Fine_windows={resolved_fine_windows}\n")
        f.write(f"# Poling_dwell_s={poling_dwell_s}\n")
        f.write(f"# Lock_in_settle_s={settle_s}\n")
        f.write(f"# Lock_in_avg_readings={avg_n}\n")
        f.write(f"# Lock_in_read_delay_s={read_delay_s}\n")
        if lockin_tc_index is not None:
            f.write(f"# Lock_in_tc_index={int(lockin_tc_index)}\n")
        if lockin_sensitivity_index is not None:
            f.write(f"# Lock_in_sensitivity_index={int(lockin_sensitivity_index)}\n")
        f.write(f"# Lock_in_range_mode={range_mode}\n")
        if range_controller is not None:
            f.write(
                "# Lock_in_dynamic_range_indices="
                f"{json.dumps(list(range_controller.range_indices), separators=(',', ':'))}\n"
            )
            f.write(
                "# Lock_in_dynamic_fullscales_uV="
                f"{json.dumps([SENS_TABLE_VOLTS[index] * 1e6 for index in range_controller.range_indices], separators=(',', ':'))}\n"
            )
            f.write(
                "# Lock_in_dynamic_target_fraction="
                f"{DC_HYST_DYNAMIC_RANGE_TARGET_FRACTION}\n"
            )
            f.write(
                "# Lock_in_dynamic_rescue_fraction="
                f"{DC_HYST_DYNAMIC_RANGE_RESCUE_FRACTION}\n"
            )
        f.write(f"# Lock_in_range_changes={n_range_changes}\n")
        f.write(f"# Lock_in_range_rescue_rereads={n_range_rescues}\n")
        f.write(f"# Lock_in_input_overload_points={n_lockin_input_overloads}\n")
        f.write(f"# Lock_in_output_overload_points={n_lockin_output_overloads}\n")
        if dither_vpp is not None:
            f.write(f"# AC_dither_Vpp={dither_vpp}\n")
        f.write("# Voltage_sequence=DC-only_pole_then_gated_AC_measurement\n")
        f.write("# AC_enabled_during_DC_ramp_or_poling=False\n")
        f.write("# SMU_electrical_sample_phase=after_DC_poling_before_AC_enable\n")
        f.write("# SMU_V_read_semantics=measured_terminal_voltage\n")
        f.write(
            f"# SMU_DC_readback_tolerance_V="
            f"{SMU_DC_READBACK_TOLERANCE_V}\n"
        )
        f.write(
            f"# SMU_DC_readback_attempts={SMU_DC_READBACK_ATTEMPTS}\n"
        )
        f.write(f"# Compliance_A={SMU_COMPLIANCE_A}\n")
        f.write(f"# N_compliance_trips={n_trips}\n")
        f.write("idx,t_s,branch,V_dc_V,P_dc_W,LockIn_Mag_V,LockIn_Phase_deg,"
                "LockIn_Sens_V,LockIn_Mag_Std_V,LockIn_Phase_Std_deg,N_ok,"
                "cycle,LockIn_X_V,LockIn_Y_V,SMU_I_A,SMU_V_read_V,"
                "compliance_tripped,AC_Vpp,AC_measurement_window_s,"
                "SMU_V_measured_V,SMU_V_set_V,LockIn_Sens_Index,"
                "LockIn_Range_Mode,LockIn_Range_Predicted_V,"
                "LockIn_Range_Noise_Prior_V,LockIn_Range_Upper_Bound_V,"
                "LockIn_Range_Requested_Index,LockIn_Range_Change_Reason,"
                "LockIn_Range_Changed,LockIn_Range_Rescue_Reread,"
                "LockIn_XY_Noise_Sigma_V,LockIn_Overload_Byte,"
                "LockIn_Overload_Byte_Observed,LockIn_Status_Byte,"
                "LockIn_Status_Byte_Observed,LockIn_Output_Overload_Observed,"
                "LockIn_Output_Overload_Final,LockIn_Input_Overload\n")
        for r in rows:
            f.write(f"{r['idx']},{r['t']:.3f},{r['branch']},{r['v_dc']:.4f},"
                    f"{r['p_dc']:.6e},{r['li_mag']:.6e},{r['li_phase']:.4f},"
                    f"{r['sens_v']:.6e},{r['li_mag_std']:.6e},"
                    f"{r['li_phase_std']:.4f},{r['n_ok']},"
                    f"{r['cycle']},{r['li_x']:.6e},{r['li_y']:.6e},"
                    f"{r['smu_i']:.6e},{r['smu_v_read']:.4f},"
                    f"{r['tripped']},{r['ac_vpp']:.4f},"
                    f"{r['ac_window_s']:.4f},"
                    f"{r['smu_v_measured']:.4f},{r['smu_v_set']:.4f},"
                    f"{r['sens_idx']},{r['lockin_range_mode']},"
                    f"{r['lockin_range_predicted_v']:.6e},"
                    f"{r['lockin_range_noise_prior_v']:.6e},"
                    f"{r['lockin_range_upper_bound_v']:.6e},"
                    f"{r['lockin_range_requested_idx']},"
                    f"{r['lockin_range_change_reason']},"
                    f"{r['lockin_range_changed']},"
                    f"{r['lockin_range_rescue_reread']},"
                    f"{r['lockin_noise_sigma_v']:.6e},"
                    f"{r['lockin_overload_byte']},"
                    f"{r['lockin_overload_byte_observed']},"
                    f"{r['lockin_status_byte']},"
                    f"{r['lockin_status_byte_observed']},"
                    f"{r['lockin_output_overload_observed']},"
                    f"{r['lockin_output_overload_final']},"
                    f"{r['lockin_input_overload']}\n")
    print(f"  [SAVED] {csv_path}")

    raw_path = os.path.join(out_dir, "dc_hysteresis_raw_samples.csv")
    with open(raw_path, "w") as f:
        f.write(f"# Run={run_label}  raw lock-in samples\n")
        f.write("idx,V_dc_V,branch,range_attempt,sample_j,sample_t_s,"
                "LockIn_Mag_V,LockIn_Phase_deg,LockIn_Sens_Index,"
                "LockIn_Sens_V,Discarded_For_Range_Rescue,"
                "LockIn_Overload_Byte,LockIn_Status_Byte\n")
        for r in raw_rows:
            f.write(f"{r['idx']},{r['v_dc']:.4f},{r['branch']},"
                    f"{r['range_attempt']},{r['sample_j']},"
                    f"{r['sample_t']:.4f},{r['li_mag']:.6e},"
                    f"{r['li_phase']:.4f},{r['sens_idx']},"
                    f"{r['sens_v']:.6e},{r['discarded_for_range_rescue']},"
                    f"{r['overload_byte']},{r['status_byte']}\n")
    print(f"  [SAVED] {raw_path}")

    # --- plot ---
    V = np.array([r["v_dc"] for r in rows])
    M = np.array([r["li_mag"] for r in rows]) * 1e6   # uV
    P = np.array([r["p_dc"] for r in rows]) * 1e3     # mW
    Phi = np.array([r["li_phase"] for r in rows])
    BR = np.array([r["branch"] for r in rows])
    base_colors = {"virgin": "tab:purple", "sat": "tab:orange",
                   "down": "tab:red", "up": "tab:green"}
    base_labels = {"virgin": "0 -> +Vmax (virgin)",
                   "sat": "pre-saturate +Vmax",
                   "down": "+Vmax -> -Vmax",
                   "up": "-Vmax -> +Vmax"}

    def _branch_style(name):
        base = name.rstrip("0123456789")
        color = base_colors.get(base, "tab:blue")
        label = base_labels.get(base, name)
        if name != base:
            label = f"{label} (cycle {name[len(base):]})"
        return color, label

    # preserve first-appearance order of branches
    seen_branches = list(dict.fromkeys(BR.tolist()))

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 11), sharex=True)
    for br in seen_branches:
        m = (BR == br)
        if not np.any(m):
            continue
        color, label = _branch_style(br)
        ax1.plot(V[m], M[m], "o-", ms=4, color=color, label=label)
        ax2.plot(V[m], Phi[m], "s-", ms=4, color=color)
        ax3.plot(V[m], P[m], "d-", ms=4, color=color)
    ax1.set_ylabel("Lock-in |M| (uV)")
    ax1.set_title(f"DC hysteresis - {run_label}\n"
                  f"theta_i={peak['theta_i']:.1f} deg, "
                  f"V_AC={peak['vpp']} Vpp, "
                  f"ANL={peak['anl_angle']:.2f} deg")
    ax1.grid(True); ax1.legend(fontsize=9)
    ax2.set_ylabel("Lock-in phase (deg)")
    ax2.grid(True)
    ax3.set_ylabel("DC power (mW)")
    ax3.set_xlabel("V_DC (V)")
    ax3.grid(True)
    fig.tight_layout()
    png_path = os.path.join(out_dir, "dc_hysteresis.png")
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"  [SAVED] {png_path}")

    # --- JSON summary ---
    json_path = os.path.join(out_dir, "dc_hysteresis_peak_conditions.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_label": run_label,
            "peak": peak,
            "DC_HYST_VMAX": hyst_vmax,
            "DC_HYST_COARSE_STEP": DC_HYST_COARSE_STEP,
            "DC_HYST_FINE_STEP": DC_HYST_FINE_STEP,
            "DC_HYST_FINE_LIMIT": DC_HYST_FINE_LIMIT,
            "dc_hyst_grid_profile": grid_profile,
            "dc_hyst_spacing": spacing_label,
            "dc_hyst_voltage_levels_v": voltage_levels_v,
            "dc_hyst_uniform_step_v": uniform_step_v,
            "fine_windows": resolved_fine_windows,
            "n_points": int(n),
            "cycles": int(max(1, int(cycles))),
            "lockin_settle_s": float(settle_s),
            "lockin_avg_readings": int(avg_n),
            "lockin_read_delay_s": float(read_delay_s),
            "lockin_tc_index": (int(lockin_tc_index)
                                if lockin_tc_index is not None else None),
            "lockin_sensitivity_index_requested": (
                int(lockin_sensitivity_index)
                if lockin_sensitivity_index is not None else None
            ),
            "lockin_range_mode": range_mode,
            "lockin_dynamic_range_indices": (
                list(range_controller.range_indices)
                if range_controller is not None else []
            ),
            "lockin_dynamic_fullscales_uV": (
                [
                    float(SENS_TABLE_VOLTS[index] * 1e6)
                    for index in range_controller.range_indices
                ]
                if range_controller is not None else []
            ),
            "lockin_dynamic_target_fraction": (
                float(DC_HYST_DYNAMIC_RANGE_TARGET_FRACTION)
                if range_controller is not None else None
            ),
            "lockin_dynamic_rescue_fraction": (
                float(DC_HYST_DYNAMIC_RANGE_RESCUE_FRACTION)
                if range_controller is not None else None
            ),
            "lockin_dynamic_safety_sigma": (
                float(DC_HYST_DYNAMIC_RANGE_SAFETY_SIGMA)
                if range_controller is not None else None
            ),
            "lockin_range_changes": int(n_range_changes),
            "lockin_range_rescue_rereads": int(n_range_rescues),
            "lockin_input_overload_points": int(n_lockin_input_overloads),
            "lockin_output_overload_points": int(n_lockin_output_overloads),
            "ac_dither_vpp": (float(dither_vpp) if dither_vpp is not None
                              else None),
            "voltage_sequence": "dc_only_poling_then_ac_measurement",
            "ac_enabled_during_dc_ramp_or_poling": False,
            "smu_electrical_sample_phase": (
                "after_dc_poling_before_ac_measurement"
            ),
            "smu_voltage_readback": "secondary_live_terminal_voltage",
            "smu_dc_readback_tolerance_V": float(
                SMU_DC_READBACK_TOLERANCE_V
            ),
            "smu_dc_readback_attempts": int(
                SMU_DC_READBACK_ATTEMPTS
            ),
            "poling_dwell_s": float(poling_dwell_s),
            "start_from_zero": bool(start_from_zero),
            "smu_compliance_A": float(SMU_COMPLIANCE_A),
            "n_compliance_trips": int(n_trips),
        }, f, indent=2)
    print(f"  [SAVED] {json_path}")

    # v2: signed-loop metric extraction (coercive voltages, loop width,
    # imprint, remanence, squareness, area, ...). Guarded so an analysis bug
    # can never lose an acquired dataset.
    if run_analysis:
        try:
            from pockels_hysteresis_analysis import analyse_and_save
            analyse_and_save(csv_path, out_dir=out_dir)
        except Exception as exc:
            print(f"  [WARN] Hysteresis metric analysis failed "
                  f"(data is saved; re-run offline): {exc}")

    emit_progress(
        "complete",
        point_index=len(rows),
        ac_output_on=False,
        voltage_sequence="dc_only_poling_then_ac_measurement",
        voltage_grid_profile=grid_profile,
        voltage_levels_v=voltage_levels_v,
        vmax_v=hyst_vmax,
        lockin_range_mode=range_mode,
    )
    return sens_idx


def _hyst_kwargs_from_cli(cli_args, funcgen=None):
    """Explicit GUI/CLI overrides for run_dc_hysteresis_sweep.

    The function-generator handle is always forwarded so every launch path
    uses DC-only poling followed by a gated AC measurement window.  Other
    flags retain their established defaults (30 s dwell floor, single cycle,
    4 Vpp small-signal probe, centre-dense grid, front-panel TC). The fast-map GUI
    follow-up queue passes its selected values explicitly so queued
    hysteresis runs match the in-run post-map sweeps.
    """
    kwargs = {}
    cycles = getattr(cli_args, "hyst_cycles", None)
    if cycles is not None and int(cycles) > 1:
        kwargs["cycles"] = int(cycles)
    min_dwell = getattr(cli_args, "hyst_min_dwell", None)
    if min_dwell is not None:
        kwargs["min_dwell_s"] = float(min_dwell)
    if funcgen is not None:
        kwargs["funcgen"] = funcgen
    ac_vpp = getattr(cli_args, "hyst_ac_vpp", None)
    if ac_vpp is not None and float(ac_vpp) > 0.0 and funcgen is not None:
        kwargs["ac_vpp"] = float(ac_vpp)
    if bool(getattr(cli_args, "hyst_adaptive_window", False)):
        kwargs["adaptive_fine"] = True
    voltage_step = getattr(cli_args, "hyst_voltage_step", None)
    if voltage_step is not None:
        kwargs["voltage_step_v"] = float(voltage_step)
    hyst_vmax = getattr(cli_args, "hyst_vmax", None)
    if hyst_vmax is not None:
        kwargs["vmax_v"] = validate_hysteresis_voltage_limit(
            hyst_vmax,
            "DC hysteresis Vmax",
        )
    tc_index = getattr(cli_args, "hyst_tc_index", None)
    if tc_index is not None:
        kwargs["lockin_tc_index"] = int(tc_index)
    sensitivity_index = getattr(cli_args, "hyst_sensitivity_index", None)
    if sensitivity_index is not None:
        kwargs["lockin_sensitivity_index"] = int(sensitivity_index)
    if bool(getattr(cli_args, "hyst_dynamic_lockin_range", False)):
        kwargs["lockin_range_mode"] = "predictive_hysteretic"
    else:
        kwargs["lockin_range_mode"] = "fixed"
    lockin_settle = getattr(cli_args, "hyst_lockin_settle", None)
    if lockin_settle is not None:
        kwargs["lockin_settle_s"] = float(lockin_settle)
    lockin_readings = getattr(cli_args, "hyst_lockin_readings", None)
    if lockin_readings is not None:
        kwargs["lockin_avg_readings"] = int(lockin_readings)
    lockin_read_delay = getattr(cli_args, "hyst_lockin_read_delay", None)
    if lockin_read_delay is not None:
        kwargs["lockin_read_delay_s"] = float(lockin_read_delay)
    live_json = str(getattr(cli_args, "hysteresis_live_json", "") or "").strip()
    if live_json:
        kwargs["progress_callback"] = make_hysteresis_progress_json_callback(
            live_json,
            getattr(cli_args, "pixel_id", None),
        )
    return kwargs


# ============================ MODE SELECTOR ==============================

def _select_mode_interactive():
    """Interactive run-mode picker - shown when no mode flag was provided
    (typical when the script is launched from Spyder by pressing F5).

    Returns one of:
      'full'        - full Phase A+B+C campaign (the original 2-hour run)
      'hyst'        - hysteresis-only (motors -> peak, then DC sweep)
      'reset'       - domain reset only, then exit
      'reset_hyst'  - domain reset, then motors -> peak, then DC sweep
      'anl'         - motors -> peak, then analyser-only diagnostic sweep
    """
    print("\n" + "=" * 60)
    print("  POCKELS CALIBRATION 2026 - MODE SELECT")
    print("=" * 60)
    print("  1. Full campaign (HWP sweep + peak find + DC hysteresis)")
    print("  2. DC hysteresis only (set motors to known peak, run sweep)")
    print("  3. Domain reset only (AC depole and exit)")
    print("  4. Domain reset + DC hysteresis (depole then sweep at peak)")
    print("  5. ANL sweep diagnostic (pole pixel, sweep analyser only)")
    print("")
    while True:
        choice = input("  Choice [1]: ").strip()
        if choice == "" or choice == "1":
            return "full"
        if choice == "2":
            return "hyst"
        if choice == "3":
            return "reset"
        if choice == "4":
            return "reset_hyst"
        if choice == "5":
            return "anl"
        print("    Please enter 1, 2, 3, 4 or 5.")


def _cli_yes(cli_args):
    return bool(getattr(cli_args, "yes", False))


def _sanitize_dir_part(text, fallback="run"):
    text = str(text or "").strip() or fallback
    safe = text.replace(" ", "_").replace("/", "_").replace("\\", "_")
    return safe or fallback


def _run_identity(cli_args, default_name):
    if _cli_yes(cli_args):
        run_name = str(getattr(cli_args, "run_name", "") or default_name).strip() or default_name
        pixel_id = str(getattr(cli_args, "pixel_id", "") or "").strip()
        notes = str(getattr(cli_args, "notes", "") or "").strip()
        return run_name, pixel_id, notes
    run_name = input(f"  Run name [{default_name}]: ").strip() or default_name
    pixel_id = input("  Pixel / position ID [optional]: ").strip()
    notes = input("  Notes [optional]: ").strip()
    return run_name, pixel_id, notes


def _maybe_wait_for_enter(cli_args, prompt):
    if _cli_yes(cli_args):
        print("\n  [AUTO] " + " ".join(str(prompt).split()))
        return
    input(prompt)


def _parse_vpp_values(raw):
    values = []
    for part in str(raw or "").replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        values.append(float(item))
    if not values:
        raise ValueError("No AC Vpp values were supplied.")
    return values


LAB_MAPPING_JSON = "polarisation_lab_mapping_current.json"


def _finite_float_or_none(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _wrap180_period(angle_deg):
    return float(angle_deg) % 180.0


def _normalise_lab_mapping_config(config):
    if not isinstance(config, dict):
        return None
    try:
        raw_refs = config.get("raw_references_deg", {})
        signs = config.get("signs", {})
        return {
            "path": config.get("mapping_path"),
            "H_y_raw": float(raw_refs["HWP_incident_y"]),
            "Q_y_raw": float(raw_refs["QWP_axis_y"]),
            "A_y_raw": float(raw_refs["ANL_transmit_y"]),
            "s_H": int(signs.get("s_H", 1)),
            "s_Q": int(signs.get("s_Q", 1)),
            "s_A": int(signs.get("s_A", 1)),
        }
    except Exception:
        return None


def _load_lab_angle_mapping(path):
    try:
        with open(path, "r") as handle:
            data = json.load(handle)
        axis_ref = data["calibrated_lab_axis_reference"]
        raw = axis_ref["raw_setpoints_deg"]
        signs = data.get("raw_to_lab_formulas", {}).get("signs", {})
        mapping = {
            "path": os.path.abspath(path),
            "H_y_raw": float(raw["HWP_incident_y"]),
            "Q_y_raw": float(raw["QWP_axis_y"]),
            "A_y_raw": float(raw["ANL_transmit_y"]),
            "s_H": int(signs.get("s_H", 1)),
            "s_Q": int(signs.get("s_Q", 1)),
            "s_A": int(signs.get("s_A", 1)),
        }
        for key in ("s_H", "s_Q", "s_A"):
            if mapping[key] not in (-1, 1):
                mapping[key] = 1
        return mapping
    except Exception:
        return None


LAB_ANGLE_MAPPING = _load_lab_angle_mapping(os.path.join(os.path.dirname(__file__), LAB_MAPPING_JSON))


def _active_lab_angle_mapping(peak=None):
    if isinstance(peak, dict):
        mapping = _normalise_lab_mapping_config(peak.get("lab_angle_mapping"))
        if mapping:
            return mapping
    return LAB_ANGLE_MAPPING


def _theta_i_lab_from_hwp(hwp_deg, mapping=None):
    raw = _finite_float_or_none(hwp_deg)
    if raw is None:
        return float("nan")
    if mapping:
        return _wrap180_period(90.0 + 2.0 * mapping["s_H"] * (raw - mapping["H_y_raw"]))
    return _wrap180_period(2.0 * raw)


def _qwp_axis_lab_from_raw(qwp_deg, mapping=None):
    raw = _finite_float_or_none(qwp_deg)
    if raw is None or not mapping:
        return float("nan")
    return _wrap180_period(90.0 + mapping["s_Q"] * (raw - mapping["Q_y_raw"]))


def _analyser_axis_lab_from_raw(anl_deg, mapping=None):
    raw = _finite_float_or_none(anl_deg)
    if raw is None or not mapping:
        return float("nan")
    return _wrap180_period(90.0 + mapping["s_A"] * (raw - mapping["A_y_raw"]))


def _peak_theta_i_lab(peak):
    theta_i = _finite_float_or_none(peak.get("theta_i_deg") if isinstance(peak, dict) else None)
    if theta_i is not None:
        return _wrap180_period(theta_i)
    mapping = _active_lab_angle_mapping(peak)
    return _theta_i_lab_from_hwp((peak or {}).get("hwp_deg", (peak or {}).get("theta_i")), mapping)


def _analyser_theta_ana_from_raw(anl_deg, peak):
    mapping = _active_lab_angle_mapping(peak)
    axis_lab = _analyser_axis_lab_from_raw(anl_deg, mapping)
    theta_i = _peak_theta_i_lab(peak)
    if not (math.isfinite(float(axis_lab)) and math.isfinite(float(theta_i))):
        return float("nan")
    return _wrap180_period(float(axis_lab) - float(theta_i) - 90.0)


def _qwp_theta_wav_from_raw(qwp_deg, peak):
    mapping = _active_lab_angle_mapping(peak)
    axis_lab = _qwp_axis_lab_from_raw(qwp_deg, mapping)
    theta_i = _peak_theta_i_lab(peak)
    if not (math.isfinite(float(axis_lab)) and math.isfinite(float(theta_i))):
        return float("nan")
    return _wrap180_period(float(axis_lab) - float(theta_i))


def _enrich_peak_angle_convention(peak):
    if not isinstance(peak, dict):
        return peak
    peak.setdefault("theta_i_deg", _peak_theta_i_lab(peak))
    peak.setdefault("qwp_theta_wav_deg", _qwp_theta_wav_from_raw(peak.get("qwp_angle", peak.get("q_null")), peak))
    peak.setdefault("qwp_readout_theta_wav_deg", _qwp_theta_wav_from_raw(peak.get("qwp_angle", peak.get("q_null")), peak))
    peak.setdefault("q_null_theta_wav_deg", _qwp_theta_wav_from_raw(peak.get("q_calibration_null", peak.get("q_null")), peak))
    peak.setdefault("anl_theta_ana_deg", _analyser_theta_ana_from_raw(peak.get("anl_angle"), peak))
    peak.setdefault("a_null_theta_ana_deg", _analyser_theta_ana_from_raw(peak.get("a_null"), peak))
    return peak


def _add_analyser_convention_to_sweep_results(res, raw_samples, peak):
    target_theta = []
    actual_theta = []
    target_axis_lab = []
    actual_axis_lab = []
    mapping = _active_lab_angle_mapping(peak)
    for target, actual in zip(res.get("angle", []), res.get("angle_actual", [])):
        target_axis_lab.append(_analyser_axis_lab_from_raw(target, mapping))
        actual_axis_lab.append(_analyser_axis_lab_from_raw(actual, mapping))
        target_theta.append(_analyser_theta_ana_from_raw(target, peak))
        actual_theta.append(_analyser_theta_ana_from_raw(actual, peak))
    res["angle_target_axis_lab_deg"] = target_axis_lab
    res["angle_actual_axis_lab_deg"] = actual_axis_lab
    res["angle_target_theta_ANA_deg"] = target_theta
    res["angle_actual_theta_ANA_deg"] = actual_theta
    for pt in raw_samples:
        pt["angle_target_axis_lab_deg"] = _analyser_axis_lab_from_raw(pt.get("angle_target"), mapping)
        pt["angle_actual_axis_lab_deg"] = _analyser_axis_lab_from_raw(pt.get("angle_actual"), mapping)
        pt["angle_target_theta_ANA_deg"] = _analyser_theta_ana_from_raw(pt.get("angle_target"), peak)
        pt["angle_actual_theta_ANA_deg"] = _analyser_theta_ana_from_raw(pt.get("angle_actual"), peak)


def _coerce_peak_conditions(payload, source, lab_angle_mapping=None):
    """Normalize peak/analyser-sweep JSON from fast-map and calibration runs."""
    p = payload.get("peak", payload) if isinstance(payload, dict) else {}
    qwp_target = float(
        p.get(
            "qwp_angle",
            p.get("qwp_readout", p.get("qwp_readout_deg", p.get("q_null"))),
        )
    )
    q_calibration_null = float(p.get("q_null", p.get("q_calibration_null", qwp_target)))
    theta_i = float(p.get("theta_i", p.get("hwp_deg", p.get("hwp"))))
    anl_angle = float(p.get("anl_angle", p.get("anl_target_deg", p.get("anl"))))
    peak = {
        "theta_i": theta_i,
        "hwp_deg": theta_i,
        "vpp": float(p.get("vpp", float("nan"))),
        "anl_angle": anl_angle,
        # q_null is the historical motor target used by the follow-up routines.
        # New fast-map peak JSON also carries qwp_angle/readout, which is the
        # actual readout bias after the per-HWP null.
        "q_null": qwp_target,
        "qwp_angle": qwp_target,
        "q_calibration_null": q_calibration_null,
        "a_null": float(p.get("a_null", p.get("a_null_deg", anl_angle))),
        "mag": float(p.get("mag", p.get("lockin_mag_V", float("nan")))),
        "source": source,
    }
    for key in (
        "theta_i_deg",
        "theta_i_legacy_deg",
        "qwp_axis_lab_deg",
        "qwp_readout_axis_lab_deg",
        "qwp_theta_wav_deg",
        "qwp_readout_theta_wav_deg",
        "q_null_theta_wav_deg",
        "anl_lab_deg",
        "a_null_lab_deg",
        "anl_theta_ana_deg",
        "a_null_theta_ana_deg",
        "slope_side",
        "hwp_index",
        "quality_flags",
        "lab_angle_mapping",
    ):
        if key in p:
            peak[key] = p[key]
    if lab_angle_mapping and "lab_angle_mapping" not in peak:
        peak["lab_angle_mapping"] = lab_angle_mapping
    return _enrich_peak_angle_convention(peak)


def _load_peak_json_payload(path_arg):
    path = os.path.expanduser(path_arg)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"--peak-json not found: {path}")
    with open(path, "r") as f:
        return json.load(f), path


def _load_analyser_sweep_points(cli_args):
    if not cli_args.peak_json:
        return None, []
    payload, path = _load_peak_json_payload(cli_args.peak_json)
    raw_points = (
        payload.get("analyser_sweep_points")
        or payload.get("analyzer_sweep_points")
        or payload.get("hwp_sweep_points")
    )
    if not raw_points:
        return None, []
    lab_angle_mapping = payload.get("lab_angle_mapping")
    points = [
        _coerce_peak_conditions(item, f"{path}#{idx:03d}", lab_angle_mapping=lab_angle_mapping)
        for idx, item in enumerate(raw_points, start=1)
    ]
    json_vpp_values = payload.get("vpp_values") or payload.get("voltages_vpp") or []
    return points, [float(v) for v in json_vpp_values]


def _build_anl_sweep_angles(start_deg, stop_deg, step_deg):
    start = float(start_deg)
    stop = float(stop_deg)
    step = float(step_deg)
    if not np.isfinite(start) or not np.isfinite(stop) or not np.isfinite(step):
        raise ValueError("ANL sweep start/stop/step must be finite numbers.")
    if step <= 0.0:
        raise ValueError("ANL sweep step must be positive.")
    if stop < start:
        raise ValueError("ANL sweep stop must be greater than or equal to start; avoid crossing 360 in one sweep.")
    n = int(math.floor((stop - start) / step + 1e-9)) + 1
    angles = [start + i * step for i in range(max(1, n))]
    if not angles or abs(angles[-1] - stop) > 1e-6:
        angles.append(stop)
    return np.array([a % 360.0 for a in angles], dtype=float)


# =================== RESET-ONLY ENTRY POINT ==============================

def run_reset_only(cli_args):
    """Domain-reset-only run. Brings up the SMU + funcgen, runs the depoling
    pulse train, saves the log, and exits. The funcgen sweep channel is
    toggled OFF/ON automatically inside reset_domains_pulsed()."""
    os.makedirs(OUTDIR_ROOT, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  POCKELS CALIBRATION 2026 - DOMAIN-RESET ONLY")
    print("=" * 60)
    run_name = input("  Run name [reset]: ").strip() or "reset"
    run_name_safe = run_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    pixel_id = input("  Pixel / position ID [optional]: ").strip()
    notes = input("  Notes [optional]: ").strip()

    dir_parts = [timestamp, run_name_safe, "reset_only"]
    if pixel_id:
        dir_parts.append(pixel_id.replace(" ", "_").replace("/", "_"))
    root_dir = os.path.join(OUTDIR_ROOT, "_".join(dir_parts))
    os.makedirs(root_dir, exist_ok=True)
    run_label = run_name + (f" [{pixel_id}]" if pixel_id else "") + " (reset)"

    with open(os.path.join(root_dir, "run_info.txt"), "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id or '(not specified)'}\n")
        f.write(f"Mode     : reset-only\n")
        f.write(f"Notes    : {notes or '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")

    # Bring up the funcgen so we can toggle CH1 OFF during the reset (the
    # rest of the funcgen config is otherwise idle).
    try:
        funcgen = _configure_funcgen()
    except Exception as e:
        print(f"  FATAL: function generator: {e}")
        return

    smu = None
    try:
        smu = _connect_smu(0.0)
    except Exception as e:
        print(f"  FATAL: SMU4201 bringup: {e}")
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception: pass
        return

    try:
        input("\n  Press Enter to start the domain-reset depoling...")
        reset_domains_pulsed(
            smu, funcgen,
            os.path.join(root_dir, "domain_reset"), run_label,
            vmax=cli_args.reset_vmax,
            vmin=cli_args.reset_vmin,
            n_amp_steps=cli_args.reset_amp_steps,
            decay=cli_args.reset_decay,
            cycles_per_amp=cli_args.reset_cycles_per_amp,
            pulse_first_ms=cli_args.reset_pulse_ms,
            pulse_second_ms=cli_args.reset_pulse_ms,
        )
        print(f"\nReset log saved in: {os.path.abspath(root_dir)}")
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Bringing SMU to 0 V before exit...")
    except Exception as e:
        print(f"\n[ERROR] Reset run aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        smu_safe_shutdown(smu)

    input("\nPress Enter to close and disconnect...")
    plt.close("all")
    try:
        funcgen_send(funcgen, "LOCAL")
        funcgen.close()
    except Exception: pass
    print("[DONE] All hardware disconnected.")


# ====================== HYSTERESIS-ONLY ENTRY POINT ======================

def _resolve_peak_for_hyst_only(cli_args):
    """Build the peak-conditions dict from --peak-json, --peak-* args, or
    interactive prompts. Returns {theta_i, vpp, anl_angle, q_null, a_null,
    mag, source}."""
    if cli_args.peak_json:
        d, path = _load_peak_json_payload(cli_args.peak_json)
        return _coerce_peak_conditions(d.get("peak", d), path)

    # Try CLI numeric args first
    if all(v is not None for v in (cli_args.peak_theta, cli_args.peak_anl,
                                   cli_args.peak_qnull, cli_args.peak_vpp)):
        return {
            "theta_i": float(cli_args.peak_theta),
            "vpp": float(cli_args.peak_vpp),
            "anl_angle": float(cli_args.peak_anl),
            "q_null": float(cli_args.peak_qnull),
            "a_null": float(cli_args.peak_anl),
            "mag": float("nan"),
            "source": "CLI args",
        }

    # Interactive prompts
    print("\n  Peak conditions (any missing CLI args - prompt):")
    def _ask_float(name, default=None, allow_none=False):
        prompt = f"    {name}"
        if default is not None:
            prompt += f" [{default}]"
        prompt += ": "
        while True:
            raw = input(prompt).strip()
            if raw == "" and default is not None:
                return float(default)
            if raw == "" and allow_none:
                return None
            try:
                return float(raw)
            except ValueError:
                print(f"    Could not parse '{raw}', try again.")

    theta_i = _ask_float("theta_i (HWP, deg)",
                         default=cli_args.peak_theta)
    anl = _ask_float("Analyzer angle at peak (deg)",
                     default=cli_args.peak_anl)
    qnull = _ask_float("QWP null (deg)",
                       default=cli_args.peak_qnull)
    vpp = _ask_float("V_AC (Vpp)",
                     default=cli_args.peak_vpp)
    return {
        "theta_i": theta_i, "vpp": vpp,
        "anl_angle": anl, "q_null": qnull, "a_null": anl,
        "mag": float("nan"),
        "source": "interactive prompt",
    }


def run_hysteresis_only(cli_args):
    """Hysteresis-only run mode: skip Phase A (full sweep) and Phase B (peak
    find). Move motors to a known peak, drive the funcgen to the peak Vpp,
    bring up the SMU in HV mode, and run Phase C only - typically with a
    longer poling dwell at each DC step (--dwell).

    If --reset-domains was passed, run the AC-depoling pulse train first
    (just before the hysteresis sweep starts) and choose the virgin-style
    0->+->-->+ trajectory; otherwise default to the saturated +->->+
    butterfly. Either default can be overridden with --start-from-zero or
    --start-from-vmax."""
    poling_dwell_s = cli_args.dwell if cli_args.dwell is not None else DC_POLING_DWELL_S
    do_reset = bool(cli_args.reset_domains)
    requested_hyst_vmax = getattr(cli_args, "hyst_vmax", None)
    hyst_vmax = validate_hysteresis_voltage_limit(
        DC_HYST_VMAX if requested_hyst_vmax is None else requested_hyst_vmax,
        "DC hysteresis Vmax",
    )
    reset_vmax = None
    reset_vmin = None
    if do_reset:
        reset_vmax, reset_vmin = validate_reset_voltage_window(
            hyst_vmax if cli_args.reset_vmax is None else cli_args.reset_vmax,
            RESET_VMIN if cli_args.reset_vmin is None else cli_args.reset_vmin,
        )

    # Trajectory selection: virgin curve after a reset, saturated butterfly
    # otherwise. CLI overrides win.
    if cli_args.start_from_zero:
        start_from_zero = True
    elif cli_args.start_from_vmax:
        start_from_zero = False
    else:
        start_from_zero = do_reset

    os.makedirs(OUTDIR_ROOT, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  POCKELS CALIBRATION 2026 - HYSTERESIS-ONLY MODE")
    print("=" * 60)
    run_name, pixel_id, notes = _run_identity(cli_args, "hyst_unnamed")
    run_name_safe = _sanitize_dir_part(run_name, "hyst_unnamed")

    peak = _resolve_peak_for_hyst_only(cli_args)
    print(f"\n  Peak source: {peak['source']}")
    print(f"    theta_i (HWP)  : {peak['theta_i']:.2f} deg")
    print(f"    Analyzer       : {peak['anl_angle']:.2f} deg")
    print(f"    QWP null       : {peak['q_null']:.2f} deg")
    print(f"    V_AC           : {peak['vpp']} Vpp")
    if not math.isnan(peak["mag"]):
        print(f"    (recorded |M| = {peak['mag']*1e6:.3f} uV)")

    dir_parts = [timestamp, run_name_safe, "hyst_only"]
    if pixel_id:
        dir_parts.append(pixel_id.replace(" ", "_").replace("/", "_"))
    root_dir = os.path.join(OUTDIR_ROOT, "_".join(dir_parts))
    os.makedirs(root_dir, exist_ok=True)
    run_label = run_name + (f" [{pixel_id}]" if pixel_id else "") + " (hyst-only)"

    # Run metadata
    mode_str = "reset+hysteresis" if do_reset else "hysteresis-only"
    traj_str = ("0 -> +V -> -V -> +V (virgin curve on leg 1)"
                if start_from_zero else
                "+V -> -V -> +V (saturated butterfly, no virgin leg)")
    with open(os.path.join(root_dir, "run_info.txt"), "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id or '(not specified)'}\n")
        f.write(f"Mode     : {mode_str}\n")
        f.write(f"Notes    : {notes or '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Peak src : {peak['source']}\n")
        f.write(f"theta_i  : {peak['theta_i']:.3f} deg\n")
        f.write(f"ANL_peak : {peak['anl_angle']:.3f} deg\n")
        f.write(f"QWP_null : {peak['q_null']:.3f} deg\n")
        f.write(f"V_AC     : {peak['vpp']} Vpp\n")
        f.write(f"DC hyst  : {traj_str}, +/-{hyst_vmax:.1f} V, "
                f"{describe_hysteresis_spacing(vmax=hyst_vmax)}\n")
        f.write(f"Dwell    : {poling_dwell_s:.2f} s per step "
                f"(+ {LOCKIN_SETTLE_S:.2f}s lock-in settle)\n")
        if do_reset:
            f.write(f"Reset    : +/-{reset_vmax:.1f} V SMU PULSe, "
                    f"{cli_args.reset_amp_steps or RESET_AMP_STEPS} amplitudes "
                    f"x {cli_args.reset_cycles_per_amp or RESET_CYCLES_PER_AMP} "
                    f"cycles\n")

    print(f"\n  Output dir : {os.path.abspath(root_dir)}")
    print(f"  Per-step poling dwell: {poling_dwell_s:.2f} s "
          f"(+ {LOCKIN_SETTLE_S:.2f} s lock-in settle + "
          f"{LOCKIN_AVG_READINGS}x{LOCKIN_READ_DELAY_S:.2f}s avg)")

    # --- Hardware bringup ---
    try:
        funcgen = _configure_funcgen()
    except Exception as e:
        print(f"  FATAL: function generator: {e}")
        return
    # Hysteresis-only bringup must not energise CH1 while the SMU is connected.
    # CH2 remains on as the external lock-in reference.
    try:
        _funcgen_set_sweep_output(
            funcgen,
            False,
            verify_command=True,
            required=True,
            context="hysteresis-only hardware bringup",
        )
    except Exception as e:
        print(f"  FATAL: function-generator CH1 would not disable: {e}")
        _funcgen_set_sweep_output(
            funcgen,
            False,
            verify_command=False,
            required=False,
            context="failed hysteresis-only bringup cleanup",
        )
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception:
            pass
        return

    rot_hwp, rot_qwp, rot_anl = _connect_rotators()
    det = _connect_scope()
    lockin, sens_idx = _connect_lockin()
    sens_idx = configure_fixed_lockin_range(
        lockin,
        sens_idx,
        getattr(cli_args, "fixed_lockin_sensitivity_index", None),
    )

    # SMU bringup with HV mode (>42 V). Always 0 V on first ramp here — the
    # hysteresis sweep itself starts from 0 V, so no Phase-A hold to apply.
    smu = None
    try:
        smu = _connect_smu(0.0)
    except Exception as e:
        print(f"  FATAL: SMU4201 bringup: {e}")
        for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
            try: closer.close()
            except Exception: pass
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception: pass
        return

    try:
        # Move motors to peak
        print(f"\n  Moving HWP -> {peak['theta_i']:.2f} deg...")
        safe_move_abs(rot_hwp, float(peak["theta_i"]), settle_s=MOVE_SETTLE_S)
        print(f"  Moving QWP -> {peak['q_null']:.2f} deg...")
        safe_move_abs(rot_qwp, float(peak["q_null"]), settle_s=MOVE_SETTLE_S)
        print(f"  Moving ANL -> {peak['anl_angle']:.2f} deg...")
        safe_move_wrap(rot_anl, float(peak["anl_angle"]))
        time.sleep(0.5)

        # Position readback — verify each axis actually got where requested
        hwp_act = rot_hwp.get_angle()
        qwp_act = rot_qwp.get_angle()
        anl_act = rot_anl.get_angle()
        print(f"\n  [POSITION VERIFY]")
        print(f"    HWP  target={peak['theta_i']:7.2f}  actual={hwp_act:7.2f}  "
              f"err={(hwp_act - peak['theta_i']):+6.3f} deg")
        print(f"    QWP  target={peak['q_null']:7.2f}  actual={qwp_act:7.2f}  "
              f"err={(qwp_act - peak['q_null']):+6.3f} deg")
        anl_err = ((anl_act - peak['anl_angle'] + 180.0) % 360.0) - 180.0
        print(f"    ANL  target={peak['anl_angle']:7.2f}  actual={anl_act:7.2f}  "
              f"err={anl_err:+6.3f} deg (wrapped)")
        for name, t, a in (("HWP", peak["theta_i"], hwp_act),
                           ("QWP", peak["q_null"], qwp_act)):
            if abs(a - t) > 0.5:
                print(f"    [WARN] {name} is >0.5 deg off target!")
        if abs(anl_err) > 0.5:
            print(f"    [WARN] ANL is >0.5 deg off target!")

        # Set funcgen to peak Vpp
        print(f"\n  Setting funcgen CH{FUNCGEN_CH_SWEEP} -> {peak['vpp']} Vpp...")
        funcgen_set_voltage(funcgen, peak["vpp"])
        print(f"    Settling {FUNCGEN_SETTLE_S:.0f}s...", end="", flush=True)
        time.sleep(FUNCGEN_SETTLE_S)
        print(" done.")

        traj_short = ("0->+->-->+ (virgin leg)" if start_from_zero
                      else "+->->+ (saturated)")
        reset_line = ("  Domain reset: ON (will run before sweep)\n"
                      if do_reset else "")
        _maybe_wait_for_enter(
            cli_args,
            f"\n--- READY ({mode_str}) ---\n"
            f"  HWP={peak['theta_i']:.1f} deg, "
            f"QWP={peak['q_null']:.2f} deg, "
            f"ANL={peak['anl_angle']:.2f} deg, "
            f"V_AC={peak['vpp']} Vpp\n"
            f"{reset_line}"
            f"  Trajectory   : {traj_short}, +/-{hyst_vmax:.0f} V\n"
            f"  Spacing      : {describe_hysteresis_spacing(vmax=hyst_vmax)}.\n"
            f"  Per-step     : {poling_dwell_s:.1f}s poling + "
            f"{LOCKIN_SETTLE_S:.1f}s lock-in settle + "
            f"{LOCKIN_AVG_READINGS}x{LOCKIN_READ_DELAY_S:.2f}s avg.\n"
            f"  Press Enter to start..."
        )

        # Optional domain reset just before the hysteresis sweep
        if do_reset:
            reset_dir = os.path.join(root_dir, "domain_reset")
            os.makedirs(reset_dir, exist_ok=True)
            reset_domains_pulsed(
                smu, funcgen, reset_dir, run_label,
                vmax=reset_vmax,
                vmin=reset_vmin,
                n_amp_steps=cli_args.reset_amp_steps,
                decay=cli_args.reset_decay,
                cycles_per_amp=cli_args.reset_cycles_per_amp,
                pulse_first_ms=cli_args.reset_pulse_ms,
                pulse_second_ms=cli_args.reset_pulse_ms,
            )

        hyst_dir = os.path.join(root_dir, "dc_hysteresis")
        os.makedirs(hyst_dir, exist_ok=True)
        run_dc_hysteresis_sweep(
            smu, det, lockin, sens_idx,
            hyst_dir, run_label, peak,
            hold_voltage_v=0.0,
            poling_dwell_s=poling_dwell_s,
            start_from_zero=start_from_zero,
            **_hyst_kwargs_from_cli(cli_args, funcgen),
        )
        print(f"\nAll data saved in: {os.path.abspath(root_dir)}")

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Bringing SMU to 0 V before exit...")
    except Exception as e:
        print(f"\n[ERROR] Hysteresis run aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        smu_safe_shutdown(smu)

    _maybe_wait_for_enter(cli_args, "\nPress Enter to close and disconnect...")
    plt.close("all")
    for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
        try: closer.close()
        except Exception: pass
    try:
        funcgen_send(funcgen, "LOCAL")
        funcgen.close()
    except Exception: pass
    print("[DONE] All hardware disconnected.")


def run_ac_vpp_sweep_at_peak(
    smu, det, lockin, funcgen, sens_idx,
    out_dir, run_label, peak,
    voltages_vpp,
    dc_hold_v,
    pre_measure_dwell_s=None,
):
    """At the saved peak HWP/QWP/ANL condition, sweep AC drive amplitude.

    This is the fixed-geometry linear-EO check that complements the DC
    hysteresis loop: same lock-in magnitude readout and averaging window, but
    x-axis is function-generator Vpp.
    """
    os.makedirs(out_dir, exist_ok=True)
    voltages = [float(v) for v in voltages_vpp]
    if pre_measure_dwell_s is None:
        pre_measure_dwell_s = AC_VPP_PRE_MEASURE_DWELL_S
    pre_measure_dwell_s = max(0.0, float(pre_measure_dwell_s))
    n = len(voltages)
    time_per_point_s = (FUNCGEN_SETTLE_S + pre_measure_dwell_s
                        + LOCKIN_SETTLE_S
                        + LOCKIN_AVG_READINGS * LOCKIN_READ_DELAY_S)

    print(f"\n{'=' * 70}")
    print("  AC VPP LINEAR EO SWEEP")
    print(f"{'=' * 70}")
    print(f"  Peak conditions: theta_i={peak['theta_i']:.1f} deg, "
          f"QWP={peak['q_null']:.2f} deg, ANL={peak['anl_angle']:.2f} deg")
    print(f"  DC hold        : {dc_hold_v:+.2f} V")
    print(f"  AC Vpp values  : {', '.join(f'{v:g}' for v in voltages)}")
    print(f"  Pre-read dwell : {pre_measure_dwell_s:.1f} s at each Vpp")
    print(f"  Est. duration  : {n * time_per_point_s / 60.0:.1f} min")

    if smu is not None and not smu_ensure_output_on(smu):
        print("  [ERROR] Cannot proceed - SMU OUTPut would not enable.")
        return sens_idx

    rows = []
    raw_rows = []
    t0 = time.time()

    for i, vpp in enumerate(voltages):
        print(f"\n  [{i+1:3d}/{n}] V_AC={vpp:g} Vpp")
        funcgen_set_voltage(funcgen, vpp)
        print(f"    Settling funcgen {FUNCGEN_SETTLE_S:.1f}s...", end="", flush=True)
        time.sleep(FUNCGEN_SETTLE_S)
        print(" done.")

        if pre_measure_dwell_s > 0.0:
            print(f"    Holding DC/AC before read {pre_measure_dwell_s:.1f}s...",
                  end="", flush=True)
            time.sleep(pre_measure_dwell_s)
            print(" done.")

        time.sleep(LOCKIN_SETTLE_S)
        sens_idx, changed = check_overload(lockin, sens_idx)
        if changed:
            print(f"    [LOCKIN] overload settle {OVERLOAD_SETTLE_S:.0f}s...",
                  end="", flush=True)
            time.sleep(OVERLOAD_SETTLE_S)
            print(" done.")

        try:
            p_dc, v_scope = det.read_power_w_stable()
        except Exception:
            p_dc, v_scope = float("nan"), float("nan")

        avg_mag, avg_phase, std_mag, std_phase, raw_mags, raw_phases, raw_ts = \
            read_lockin_averaged(lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S)

        sens_v = SENS_TABLE_VOLTS[sens_idx] if 0 <= sens_idx < len(SENS_TABLE_VOLTS) else float("nan")
        rows.append({
            "idx": i,
            "t": time.time() - t0,
            "vpp": float(vpp),
            "dc_hold_v": float(dc_hold_v),
            "p_dc": p_dc,
            "v_scope": v_scope,
            "li_mag": avg_mag,
            "li_phase": avg_phase,
            "sens_v": sens_v,
            "li_mag_std": std_mag,
            "li_phase_std": std_phase,
            "n_ok": len(raw_mags),
        })
        for j, (rm, rp) in enumerate(zip(raw_mags, raw_phases)):
            raw_rows.append({
                "idx": i,
                "vpp": float(vpp),
                "sample_j": j,
                "li_mag": rm,
                "li_phase": rp,
                "sample_t": raw_ts[j] if j < len(raw_ts) else float("nan"),
            })

        print(f"    P_dc={p_dc*1e3:7.3f} mW  "
              f"|LI|={avg_mag*1e6:8.3f} uV  "
              f"phi={avg_phase:+7.2f} deg")

    csv_path = os.path.join(out_dir, "ac_vpp_sweep.csv")
    with open(csv_path, "w") as f:
        f.write(f"# Run={run_label}\n")
        f.write(f"# theta_i_HWP_deg={peak['theta_i']:.3f}\n")
        f.write(f"# Q_null_deg={peak['q_null']:.3f}\n")
        f.write(f"# ANL_peak_deg={peak['anl_angle']:.3f}\n")
        f.write(f"# A_null_deg={peak['a_null']:.3f}\n")
        f.write(f"# DC_hold_V={dc_hold_v:.6g}\n")
        f.write(f"# Pre_measure_dwell_s={pre_measure_dwell_s:.6g}\n")
        f.write(f"# Lock_in_settle_s={LOCKIN_SETTLE_S}\n")
        f.write(f"# Lock_in_avg_readings={LOCKIN_AVG_READINGS}\n")
        f.write("idx,t_s,V_AC_Vpp,DC_hold_V,P_dc_W,V_scope_V,LockIn_Mag_V,"
                "LockIn_Phase_deg,LockIn_Sens_V,LockIn_Mag_Std_V,"
                "LockIn_Phase_Std_deg,N_ok\n")
        for r in rows:
            f.write(f"{r['idx']},{r['t']:.3f},{r['vpp']:.6g},{r['dc_hold_v']:.6g},"
                    f"{r['p_dc']:.6e},{r['v_scope']:.6e},{r['li_mag']:.6e},"
                    f"{r['li_phase']:.4f},{r['sens_v']:.6e},"
                    f"{r['li_mag_std']:.6e},{r['li_phase_std']:.4f},"
                    f"{r['n_ok']}\n")
    print(f"  [SAVED] {csv_path}")

    raw_path = os.path.join(out_dir, "ac_vpp_sweep_raw_samples.csv")
    with open(raw_path, "w") as f:
        f.write(f"# Run={run_label}  raw lock-in samples\n")
        f.write("idx,V_AC_Vpp,sample_j,sample_t_s,LockIn_Mag_V,LockIn_Phase_deg\n")
        for r in raw_rows:
            f.write(f"{r['idx']},{r['vpp']:.6g},{r['sample_j']},"
                    f"{r['sample_t']:.4f},{r['li_mag']:.6e},{r['li_phase']:.4f}\n")
    print(f"  [SAVED] {raw_path}")

    V = np.array([r["vpp"] for r in rows], dtype=float)
    M = np.array([r["li_mag"] for r in rows], dtype=float) * 1e6
    P = np.array([r["p_dc"] for r in rows], dtype=float) * 1e3
    Phi = np.array([r["li_phase"] for r in rows], dtype=float)

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 10), sharex=True)
    ax1.plot(V, M, "o-", ms=5, color="tab:blue")
    finite = np.isfinite(V) & np.isfinite(M)
    if np.count_nonzero(finite) >= 2:
        coeff = np.polyfit(V[finite], M[finite], 1)
        fit_y = np.polyval(coeff, V[finite])
        ax1.plot(V[finite], fit_y, "--", color="tab:orange",
                 label=f"linear fit {coeff[0]:.3g} uV/Vpp")
        ax1.legend(fontsize=9)
    ax1.set_ylabel("Lock-in magnitude (uV)")
    ax1.set_title(f"AC Vpp sweep - {run_label}\n"
                  f"HWP={peak['theta_i']:.1f} deg, "
                  f"QWP={peak['q_null']:.2f} deg, "
                  f"ANL={peak['anl_angle']:.2f} deg, "
                  f"DC={dc_hold_v:+.1f} V")
    ax1.grid(True)
    ax2.plot(V, Phi, "s-", ms=5, color="tab:green")
    ax2.set_ylabel("Lock-in phase (deg)")
    ax2.grid(True)
    ax3.plot(V, P, "d-", ms=5, color="tab:red")
    ax3.set_ylabel("DC power (mW)")
    ax3.set_xlabel("V_AC (Vpp)")
    ax3.grid(True)
    fig.tight_layout()
    png_path = os.path.join(out_dir, "ac_vpp_sweep.png")
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"  [SAVED] {png_path}")

    json_path = os.path.join(out_dir, "ac_vpp_sweep_peak_conditions.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_label": run_label,
            "peak": peak,
            "voltages_vpp": voltages,
            "dc_hold_v": float(dc_hold_v),
            "n_points": int(n),
            "pre_measure_dwell_s": float(pre_measure_dwell_s),
            "lockin_settle_s": float(LOCKIN_SETTLE_S),
            "lockin_avg_readings": int(LOCKIN_AVG_READINGS),
        }, f, indent=2)
    print(f"  [SAVED] {json_path}")

    return sens_idx


def run_ac_vpp_sweep_only(cli_args):
    """One-pixel fixed-peak AC Vpp sweep launched from the GUI or terminal."""
    voltages = _parse_vpp_values(cli_args.ac_vpp_values)
    dc_hold_v = float(cli_args.ac_dc_hold)
    pre_measure_dwell_s = max(0.0, float(cli_args.ac_pre_measure_dwell))
    os.makedirs(OUTDIR_ROOT, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  POCKELS CALIBRATION 2026 - AC VPP SWEEP MODE")
    print("=" * 60)
    run_name, pixel_id, notes = _run_identity(cli_args, "ac_vpp_sweep")
    run_name_safe = _sanitize_dir_part(run_name, "ac_vpp_sweep")

    peak = _resolve_peak_for_hyst_only(cli_args)
    print(f"\n  Peak source: {peak['source']}")
    print(f"    theta_i (HWP)  : {peak['theta_i']:.2f} deg")
    print(f"    Analyzer       : {peak['anl_angle']:.2f} deg")
    print(f"    QWP null       : {peak['q_null']:.2f} deg")
    print(f"    AC Vpp sweep   : {', '.join(f'{v:g}' for v in voltages)}")
    print(f"    DC hold        : {dc_hold_v:+.2f} V")
    print(f"    Pre-read dwell : {pre_measure_dwell_s:.1f} s per Vpp")

    dir_parts = [timestamp, run_name_safe, "ac_vpp_sweep"]
    if pixel_id:
        dir_parts.append(_sanitize_dir_part(pixel_id, "pixel"))
    root_dir = os.path.join(OUTDIR_ROOT, "_".join(dir_parts))
    os.makedirs(root_dir, exist_ok=True)
    run_label = run_name + (f" [{pixel_id}]" if pixel_id else "") + " (AC Vpp sweep)"

    with open(os.path.join(root_dir, "run_info.txt"), "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id or '(not specified)'}\n")
        f.write(f"Mode     : fixed-peak AC Vpp sweep\n")
        f.write(f"Notes    : {notes or '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Peak src : {peak['source']}\n")
        f.write(f"theta_i  : {peak['theta_i']:.3f} deg\n")
        f.write(f"ANL_peak : {peak['anl_angle']:.3f} deg\n")
        f.write(f"QWP_null : {peak['q_null']:.3f} deg\n")
        f.write(f"DC hold  : {dc_hold_v:+.6g} V\n")
        f.write(f"AC Vpp   : {', '.join(f'{v:g}' for v in voltages)}\n")
        f.write(f"Dwell    : {pre_measure_dwell_s:.6g} s before each Vpp read\n")

    try:
        funcgen = _configure_funcgen()
    except Exception as e:
        print(f"  FATAL: function generator: {e}")
        return

    rot_hwp, rot_qwp, rot_anl = _connect_rotators()
    det = _connect_scope()
    lockin, sens_idx = _connect_lockin()
    sens_idx = configure_fixed_lockin_range(
        lockin,
        sens_idx,
        getattr(cli_args, "fixed_lockin_sensitivity_index", None),
    )

    smu = None
    try:
        smu = _connect_smu(dc_hold_v)
    except Exception as e:
        print(f"  FATAL: SMU4201 bringup: {e}")
        for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
            try: closer.close()
            except Exception: pass
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception: pass
        return

    try:
        print(f"\n  Moving HWP -> {peak['theta_i']:.2f} deg...")
        safe_move_abs(rot_hwp, float(peak["theta_i"]), settle_s=MOVE_SETTLE_S)
        print(f"  Moving QWP -> {peak['q_null']:.2f} deg...")
        safe_move_abs(rot_qwp, float(peak["q_null"]), settle_s=MOVE_SETTLE_S)
        print(f"  Moving ANL -> {peak['anl_angle']:.2f} deg...")
        safe_move_wrap(rot_anl, float(peak["anl_angle"]))
        time.sleep(0.5)

        hwp_act = rot_hwp.get_angle()
        qwp_act = rot_qwp.get_angle()
        anl_act = rot_anl.get_angle()
        anl_err = ((anl_act - peak['anl_angle'] + 180.0) % 360.0) - 180.0
        print(f"\n  [POSITION VERIFY]")
        print(f"    HWP  target={peak['theta_i']:7.2f}  actual={hwp_act:7.2f}  "
              f"err={(hwp_act - peak['theta_i']):+6.3f} deg")
        print(f"    QWP  target={peak['q_null']:7.2f}  actual={qwp_act:7.2f}  "
              f"err={(qwp_act - peak['q_null']):+6.3f} deg")
        print(f"    ANL  target={peak['anl_angle']:7.2f}  actual={anl_act:7.2f}  "
              f"err={anl_err:+6.3f} deg (wrapped)")

        _maybe_wait_for_enter(
            cli_args,
            f"\n--- READY (AC Vpp sweep) ---\n"
            f"  HWP={peak['theta_i']:.1f} deg, "
            f"QWP={peak['q_null']:.2f} deg, "
            f"ANL={peak['anl_angle']:.2f} deg\n"
            f"  DC hold={dc_hold_v:+.1f} V, "
            f"AC Vpp={', '.join(f'{v:g}' for v in voltages)}\n"
            f"  Pre-read dwell={pre_measure_dwell_s:.1f}s at each Vpp\n"
            f"  Press Enter to start..."
        )

        sweep_dir = os.path.join(root_dir, "ac_vpp_sweep")
        sens_idx = run_ac_vpp_sweep_at_peak(
            smu, det, lockin, funcgen, sens_idx,
            sweep_dir, run_label, peak,
            voltages_vpp=voltages,
            dc_hold_v=dc_hold_v,
            pre_measure_dwell_s=pre_measure_dwell_s,
        )
        print(f"\nAll data saved in: {os.path.abspath(root_dir)}")

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Bringing SMU to 0 V before exit...")
    except Exception as e:
        print(f"\n[ERROR] AC Vpp sweep aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        smu_safe_shutdown(smu)

    _maybe_wait_for_enter(cli_args, "\nPress Enter to close and disconnect...")
    plt.close("all")
    for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
        try: closer.close()
        except Exception: pass
    try:
        funcgen_send(funcgen, "LOCAL")
        funcgen.close()
    except Exception: pass
    print("[DONE] All hardware disconnected.")


def run_analyser_sweep_at_peak(
    smu, rot_anl, det, lockin, funcgen, sens_idx,
    out_dir, run_label, peak,
    angles_deg,
    dc_hold_v,
    vpp,
    poling_dwell_s,
):
    """At fixed HWP/QWP and fixed DC/AC drive, sweep only analyser angle."""
    os.makedirs(out_dir, exist_ok=True)
    peak = _enrich_peak_angle_convention(dict(peak))
    angles = np.asarray(angles_deg, dtype=float)
    n = len(angles)
    time_per_point_s = LOCKIN_SETTLE_S + LOCKIN_AVG_READINGS * LOCKIN_READ_DELAY_S

    print(f"\n{'=' * 70}")
    print("  ANALYSER ANGLE DIAGNOSTIC SWEEP")
    print(f"{'=' * 70}")
    print(f"  Fixed HWP/QWP  : HWP={peak['theta_i']:.2f} deg, QWP={peak['q_null']:.2f} deg")
    print(f"  ANL sweep      : {angles[0]:.2f} -> {angles[-1]:.2f} deg ({n} points)")
    theta_ana_seed = _finite_float_or_none(peak.get("anl_theta_ana_deg"))
    if theta_ana_seed is not None:
        print(f"  Convention     : theta_ANA=0 crossed/blocked; saved ANL theta_ANA={theta_ana_seed:.2f} deg")
    print(f"  DC hold        : {dc_hold_v:+.2f} V")
    print(f"  AC drive       : {vpp:g} Vpp")
    print(f"  Pre-sweep dwell: {poling_dwell_s:.2f} s")
    print(f"  Est. duration  : {(max(0.0, poling_dwell_s) + n * time_per_point_s) / 60.0:.1f} min")

    if smu is not None and not smu_ensure_output_on(smu):
        print("  [ERROR] Cannot proceed - SMU OUTPut would not enable.")
        return sens_idx

    print(f"\n  Setting funcgen CH{FUNCGEN_CH_SWEEP} -> {vpp:g} Vpp...")
    funcgen_set_voltage(funcgen, float(vpp))
    _funcgen_set_sweep_output(funcgen, True)
    print(f"    Settling {FUNCGEN_SETTLE_S:.1f}s...", end="", flush=True)
    time.sleep(FUNCGEN_SETTLE_S)
    print(" done.")

    if poling_dwell_s > 0.0:
        print(f"  Holding DC={dc_hold_v:+.2f} V for {poling_dwell_s:.1f}s before ANL sweep...")
        time.sleep(float(poling_dwell_s))

    res, raw_samples, sens_idx = run_single_sweep(
        rot_anl, det, lockin, sens_idx, angles,
        voltage_label=f"ANL_diag_{vpp:g}Vpp_{dc_hold_v:+g}Vdc",
        t_start=time.time(),
        points_done_before=0,
        total_points_all=n,
    )
    _add_analyser_convention_to_sweep_results(res, raw_samples, peak)

    csv_path = os.path.join(out_dir, "analyser_sweep.csv")
    save_sweep_csv(csv_path, res, float(vpp), run_label=run_label)
    print(f"  [SAVED] {csv_path}")

    raw_path = os.path.join(out_dir, "analyser_sweep_raw_samples.csv")
    save_raw_samples_csv(raw_path, raw_samples, float(vpp), run_label=run_label)
    print(f"  [SAVED] {raw_path}")

    angle = np.asarray(res["angle_actual"], dtype=float)
    theta_ana = np.asarray(res.get("angle_actual_theta_ANA_deg", []), dtype=float)
    power_mw = np.asarray(res["power"], dtype=float) * 1e3
    scope_v = np.asarray(res["volt"], dtype=float)
    mag_uv = np.asarray(res["mag"], dtype=float) * 1e6
    phase = np.asarray(res["phase"], dtype=float)
    if theta_ana.size == angle.size and np.all(np.isfinite(theta_ana)):
        order = np.argsort(theta_ana)
        x = theta_ana[order]
        power_mw = power_mw[order]
        scope_v = scope_v[order]
        mag_uv = mag_uv[order]
        phase = phase[order]
        x_label = "theta_ANA (deg, 0=blocked/crossed)"
    else:
        x = angle
        x_label = "Analyser raw angle (deg)"

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(10, 12), sharex=True)
    ax1.plot(x, power_mw, "o-", ms=4, color="tab:red")
    ax1.set_ylabel("Scope power (mW)")
    ax1.grid(True)
    ax2.plot(x, scope_v, "o-", ms=4, color="tab:orange")
    ax2.set_ylabel("Scope signal (V)")
    ax2.grid(True)
    ax3.plot(x, mag_uv, "o-", ms=4, color="tab:blue")
    ax3.set_ylabel("Lock-in |M| (uV)")
    ax3.grid(True)
    ax4.plot(x, phase, "o-", ms=4, color="tab:green")
    ax4.set_ylabel("Lock-in phase (deg)")
    ax4.set_xlabel(x_label)
    ax4.grid(True)
    ax1.set_title(
        f"Analyser sweep diagnostic - {run_label}\n"
        f"HWP={peak['theta_i']:.1f} deg, QWP={peak['q_null']:.2f} deg, "
        f"DC={dc_hold_v:+.1f} V, AC={vpp:g} Vpp"
    )
    fig.tight_layout()
    png_path = os.path.join(out_dir, "analyser_sweep.png")
    fig.savefig(png_path, dpi=150)
    plt.close(fig)
    print(f"  [SAVED] {png_path}")

    json_path = os.path.join(out_dir, "analyser_sweep_conditions.json")
    with open(json_path, "w") as f:
        json.dump({
            "run_label": run_label,
            "peak": peak,
            "angles_deg": [float(a) for a in angles],
            "angles_theta_ANA_deg": [float(a) for a in res.get("angle_target_theta_ANA_deg", [])],
            "angle_convention": {
                "theta_ANA_deg": "wrap180(analyser_transmission_axis_lab_deg - theta_i_deg - 90)",
                "theta_ANA_0_deg": "blocked/crossed analyser for the incident linear polarization",
                "theta_ANA_90_deg": "parallel analyser transmission axis",
                "theta_WAV_deg": "wrap180(qwp_axis_lab_deg - theta_i_deg)",
            },
            "dc_hold_v": float(dc_hold_v),
            "vpp": float(vpp),
            "poling_dwell_s": float(poling_dwell_s),
            "lockin_settle_s": float(LOCKIN_SETTLE_S),
            "lockin_avg_readings": int(LOCKIN_AVG_READINGS),
            "outputs": {
                "summary_csv": csv_path,
                "raw_samples_csv": raw_path,
                "plot_png": png_path,
            },
        }, f, indent=2)
    print(f"  [SAVED] {json_path}")

    try:
        safe_move_wrap(rot_anl, float(peak["anl_angle"]))
    except Exception as exc:
        print(f"  [WARN] Could not return ANL to saved peak angle: {exc}")
    return sens_idx


def run_analyser_sweep_only(cli_args):
    """One-pixel fixed-HWP/QWP analyser-angle diagnostic launched from GUI or terminal."""
    angles = _build_anl_sweep_angles(cli_args.anl_sweep_start, cli_args.anl_sweep_stop, cli_args.anl_sweep_step)
    dc_hold_v = float(cli_args.anl_dc_hold)
    poling_dwell_s = max(0.0, float(cli_args.anl_poling_dwell))
    os.makedirs(OUTDIR_ROOT, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  POCKELS CALIBRATION 2026 - ANALYSER SWEEP DIAGNOSTIC")
    print("=" * 60)
    run_name, pixel_id, notes = _run_identity(cli_args, "anl_sweep")
    run_name_safe = _sanitize_dir_part(run_name, "anl_sweep")

    sweep_points, json_vpp_values = _load_analyser_sweep_points(cli_args)
    if sweep_points:
        if cli_args.anl_vpp is not None:
            vpp_values = [float(cli_args.anl_vpp)]
        elif cli_args.anl_vpp_values:
            vpp_values = _parse_vpp_values(cli_args.anl_vpp_values)
        elif json_vpp_values:
            vpp_values = [float(v) for v in json_vpp_values]
        else:
            vpp_values = sorted({
                float(p["vpp"]) for p in sweep_points
                if np.isfinite(float(p.get("vpp", float("nan"))))
            })
        if not vpp_values:
            raise ValueError("No AC Vpp values were supplied for the analyser sweep.")
        peak = sweep_points[0]
        print(f"\n  Peak source: {cli_args.peak_json}")
        print(f"    HWP points     : {len(sweep_points)}")
        print(f"    first HWP/QWP  : {peak['theta_i']:.2f} deg / {peak['q_null']:.2f} deg")
        print(f"    ANL sweep      : {angles[0]:.2f} -> {angles[-1]:.2f} deg, step request {cli_args.anl_sweep_step:g}")
        print(f"    AC Vpp values  : {', '.join(f'{v:g}' for v in vpp_values)}")
        print(f"    DC hold        : {dc_hold_v:+.2f} V")
    else:
        peak = _resolve_peak_for_hyst_only(cli_args)
        if cli_args.anl_vpp is not None:
            vpp_values = [float(cli_args.anl_vpp)]
        elif cli_args.anl_vpp_values:
            vpp_values = _parse_vpp_values(cli_args.anl_vpp_values)
        else:
            vpp_values = [float(peak["vpp"])]
        print(f"\n  Peak source: {peak['source']}")
        print(f"    theta_i (HWP)  : {peak['theta_i']:.2f} deg")
        print(f"    QWP            : {peak['q_null']:.2f} deg")
        print(f"    saved ANL peak : {peak['anl_angle']:.2f} deg")
        print(f"    ANL sweep      : {angles[0]:.2f} -> {angles[-1]:.2f} deg, step request {cli_args.anl_sweep_step:g}")
        print(f"    AC Vpp values  : {', '.join(f'{v:g}' for v in vpp_values)}")
        print(f"    DC hold        : {dc_hold_v:+.2f} V")

    dir_parts = [timestamp, run_name_safe, "analyser_sweep"]
    if pixel_id:
        dir_parts.append(_sanitize_dir_part(pixel_id, "pixel"))
    root_dir = os.path.join(OUTDIR_ROOT, "_".join(dir_parts))
    os.makedirs(root_dir, exist_ok=True)
    run_label = run_name + (f" [{pixel_id}]" if pixel_id else "") + " (ANL sweep)"

    with open(os.path.join(root_dir, "run_info.txt"), "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id or '(not specified)'}\n")
        mode = "per-HWP fast-map analyser sweep" if sweep_points else "fixed-HWP/QWP analyser diagnostic sweep"
        f.write(f"Mode     : {mode}\n")
        f.write(f"Notes    : {notes or '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Peak src : {cli_args.peak_json or peak['source']}\n")
        f.write(f"HWP points: {len(sweep_points) if sweep_points else 1}\n")
        f.write(f"theta_i  : {peak['theta_i']:.3f} deg\n")
        f.write(f"QWP      : {peak['q_null']:.3f} deg\n")
        f.write(f"ANL_peak : {peak['anl_angle']:.3f} deg\n")
        f.write(f"ANL sweep: {angles[0]:.3f} -> {angles[-1]:.3f} deg, {len(angles)} points\n")
        f.write(f"DC hold  : {dc_hold_v:+.6g} V\n")
        f.write(f"DC dwell : {poling_dwell_s:.6g} s\n")
        f.write(f"AC Vpp   : {', '.join(f'{v:g}' for v in vpp_values)}\n")

    try:
        funcgen = _configure_funcgen()
    except Exception as e:
        print(f"  FATAL: function generator: {e}")
        return

    rot_hwp, rot_qwp, rot_anl = _connect_rotators()
    det = _connect_scope()
    lockin, sens_idx = _connect_lockin()
    sens_idx = configure_fixed_lockin_range(
        lockin,
        sens_idx,
        getattr(cli_args, "fixed_lockin_sensitivity_index", None),
    )

    smu = None
    try:
        smu = _connect_smu(dc_hold_v)
    except Exception as e:
        print(f"  FATAL: SMU4201 bringup: {e}")
        for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
            try: closer.close()
            except Exception: pass
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception: pass
        return

    try:
        points_to_run = sweep_points or [peak]
        _maybe_wait_for_enter(
            cli_args,
            f"\n--- READY (ANL sweep diagnostic) ---\n"
            f"  HWP points={len(points_to_run)}, AC Vpp values={', '.join(f'{v:g}' for v in vpp_values)}\n"
            f"  ANL sweep={angles[0]:.1f}->{angles[-1]:.1f} deg ({len(angles)} points)\n"
            f"  DC hold={dc_hold_v:+.1f} V, first-HWP dwell={poling_dwell_s:.1f}s\n"
            f"  Press Enter to start..."
        )

        total_sweeps = len(points_to_run) * len(vpp_values)
        sweep_counter = 0
        for i_point, point in enumerate(points_to_run, start=1):
            print(f"\n  [HWP {i_point}/{len(points_to_run)}] Moving HWP -> {point['theta_i']:.2f} deg...")
            safe_move_abs(rot_hwp, float(point["theta_i"]), settle_s=MOVE_SETTLE_S)
            print(f"  Moving QWP -> {point['q_null']:.2f} deg...")
            safe_move_abs(rot_qwp, float(point["q_null"]), settle_s=MOVE_SETTLE_S)
            print(f"  Moving ANL -> saved peak {point['anl_angle']:.2f} deg...")
            safe_move_wrap(rot_anl, float(point["anl_angle"]))
            time.sleep(0.5)

            hwp_act = rot_hwp.get_angle()
            qwp_act = rot_qwp.get_angle()
            anl_act = rot_anl.get_angle()
            anl_err = ((anl_act - point["anl_angle"] + 180.0) % 360.0) - 180.0
            print(f"\n  [POSITION VERIFY]")
            print(f"    HWP  target={point['theta_i']:7.2f}  actual={hwp_act:7.2f}  "
                  f"err={(hwp_act - point['theta_i']):+6.3f} deg")
            print(f"    QWP  target={point['q_null']:7.2f}  actual={qwp_act:7.2f}  "
                  f"err={(qwp_act - point['q_null']):+6.3f} deg")
            print(f"    ANL  saved ={point['anl_angle']:7.2f}  actual={anl_act:7.2f}  "
                  f"err={anl_err:+6.3f} deg (wrapped)")

            hwp_part = _sanitize_dir_part(f"hwp_{i_point:03d}_{float(point['theta_i']):.3f}", "hwp")
            for i_vpp, vpp in enumerate(vpp_values, start=1):
                sweep_counter += 1
                point_for_vpp = dict(point)
                point_for_vpp["vpp"] = float(vpp)
                vpp_part = _sanitize_dir_part(f"{float(vpp):g}Vpp", "vpp")
                sweep_dir = os.path.join(root_dir, "analyser_sweep", hwp_part, vpp_part)
                sweep_label = (
                    f"{run_label} HWP {i_point}/{len(points_to_run)} "
                    f"Vpp {i_vpp}/{len(vpp_values)}"
                )
                print(f"\n  [ANL SWEEP {sweep_counter}/{total_sweeps}] "
                      f"HWP={point['theta_i']:.2f} deg, Vpp={float(vpp):g}")
                sens_idx = run_analyser_sweep_at_peak(
                    smu, rot_anl, det, lockin, funcgen, sens_idx,
                    sweep_dir, sweep_label, point_for_vpp,
                    angles_deg=angles,
                    dc_hold_v=dc_hold_v,
                    vpp=float(vpp),
                    poling_dwell_s=poling_dwell_s if i_vpp == 1 else 0.0,
                )
        print(f"\nAll data saved in: {os.path.abspath(root_dir)}")

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Bringing SMU to 0 V before exit...")
    except Exception as e:
        print(f"\n[ERROR] ANL sweep aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        smu_safe_shutdown(smu)

    _maybe_wait_for_enter(cli_args, "\nPress Enter to close and disconnect...")
    plt.close("all")
    for closer in (lockin, det, rot_hwp, rot_qwp, rot_anl):
        try: closer.close()
        except Exception: pass
    try:
        funcgen_send(funcgen, "LOCAL")
        funcgen.close()
    except Exception: pass
    print("[DONE] All hardware disconnected.")


# ================================= MAIN =================================

def main():
    global SMU_COMPLIANCE_A
    parser = argparse.ArgumentParser(
        description="Pockels Calibration 2026 - full Abel HWP+analyzer sweep")
    parser.add_argument("--yes", action="store_true",
                        help="Run non-interactively; skip Enter prompts and "
                             "use CLI metadata defaults.")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Run name for non-interactive follow-up modes.")
    parser.add_argument("--pixel-id", type=str, default=None,
                        help="Pixel/position label for non-interactive "
                             "follow-up modes.")
    parser.add_argument("--notes", type=str, default="",
                        help="Notes stored in run_info.txt for follow-up modes.")
    parser.add_argument(
        "--smu-compliance",
        type=float,
        default=SMU_COMPLIANCE_A,
        help=(
            "SMU4201 source-voltage current compliance in amperes "
            f"[default {SMU_COMPLIANCE_A:g} A = "
            f"{SMU_COMPLIANCE_A * 1e3:g} mA]."
        ),
    )
    parser.add_argument(
        "--fixed-lockin-sensitivity-index",
        type=int,
        choices=range(3, 28),
        default=None,
        help=(
            "Keep the DSP7230 on this voltage sensitivity for the entire "
            "worker run; overloads are reported without changing range "
            "(14 = 50 uV RMS full scale)."
        ),
    )
    parser.add_argument("--cal", type=str, default=None,
                        help="Path to calibration JSON (skips interactive "
                             "calibration prompt)")
    parser.add_argument("--hysteresis-only", action="store_true",
                        help="Skip Phase A (full sweep) and Phase B (peak find). "
                             "Move motors to a known peak condition, drive the "
                             "funcgen to the peak Vpp, and run only the DC "
                             "hysteresis sweep.")
    parser.add_argument("--ac-vpp-sweep-only", action="store_true",
                        help="Move motors to a known peak condition and run "
                             "only a fixed-geometry AC Vpp linearity sweep.")
    parser.add_argument("--analyser-sweep-only", "--analyzer-sweep-only",
                        dest="analyser_sweep_only", action="store_true",
                        help="Move motors to a known peak condition, pole/hold "
                             "the pixel, and sweep only the analyser angle "
                             "while logging scope signal, lock-in magnitude, "
                             "and lock-in phase.")
    parser.add_argument("--peak-json", type=str, default=None,
                        help="Path to a dc_hysteresis_peak_conditions.json "
                             "from a previous run. Required for "
                             "--hysteresis-only unless --peak-* args are given.")
    parser.add_argument("--peak-theta", type=float, default=None,
                        help="theta_i (HWP angle, deg) for hysteresis-only mode")
    parser.add_argument("--peak-anl", type=float, default=None,
                        help="Analyzer angle (deg) for hysteresis-only mode")
    parser.add_argument("--peak-qnull", type=float, default=None,
                        help="QWP null angle (deg) for hysteresis-only mode")
    parser.add_argument("--peak-vpp", type=float, default=None,
                        help="V_AC (Vpp) for hysteresis-only mode")
    parser.add_argument("--dwell", type=float, default=None,
                        help=f"Poling dwell (s) at each DC step "
                             f"[default {DC_POLING_DWELL_S:.2f}]")
    parser.add_argument("--ac-vpp-values", type=str,
                        default=",".join(str(v) for v in AC_VOLTAGES_VPP),
                        help="Comma-separated AC amplitudes for "
                             "--ac-vpp-sweep-only.")
    parser.add_argument("--ac-dc-hold", type=float,
                        default=DC_POLING_V_PHASE_A,
                        help="SMU DC hold voltage during --ac-vpp-sweep-only "
                             f"[default {DC_POLING_V_PHASE_A:.1f} V].")
    parser.add_argument("--ac-pre-measure-dwell", type=float,
                        default=AC_VPP_PRE_MEASURE_DWELL_S,
                        help="DC/AC hold time before each fixed-peak AC Vpp "
                             "measurement "
                             f"[default {AC_VPP_PRE_MEASURE_DWELL_S:.1f} s].")
    parser.add_argument("--anl-sweep-start", type=float, default=ANL_START,
                        help="Start analyser angle for "
                             "--analyser-sweep-only "
                             f"[default {ANL_START:.1f} deg].")
    parser.add_argument("--anl-sweep-stop", type=float, default=ANL_STOP,
                        help="Stop analyser angle for "
                             "--analyser-sweep-only "
                             f"[default {ANL_STOP:.1f} deg].")
    parser.add_argument("--anl-sweep-step", type=float, default=10.0,
                        help="Analyser angle step for "
                             "--analyser-sweep-only [default 10 deg].")
    parser.add_argument("--anl-dc-hold", type=float,
                        default=DC_POLING_V_PHASE_A,
                        help="SMU DC hold voltage during "
                             "--analyser-sweep-only "
                             f"[default {DC_POLING_V_PHASE_A:.1f} V].")
    parser.add_argument("--anl-poling-dwell", type=float,
                        default=DC_POLING_DWELL_S,
                        help="Extra DC hold time before the analyser sweep "
                             f"[default {DC_POLING_DWELL_S:.2f} s].")
    parser.add_argument("--anl-vpp", type=float, default=None,
                        help="Optional AC Vpp override for "
                             "--analyser-sweep-only. If omitted, the saved "
                             "peak Vpp is used.")
    parser.add_argument("--anl-vpp-values", type=str, default=None,
                        help="Comma-separated AC Vpp values for "
                             "--analyser-sweep-only. Used for per-HWP "
                             "fast-map analyser sweeps unless --anl-vpp is "
                             "supplied.")
    parser.add_argument("--reset-domains", action="store_true",
                        help="AC-depole the BTO domains via SMU PULSe shape "
                             "before the measurement. Combine with "
                             "--hysteresis-only to depole then sweep, or use "
                             "alone to depole and exit.")
    parser.add_argument("--reset-vmax", type=float, default=None,
                        help=f"Depole peak amplitude V "
                             f"[default {RESET_VMAX:.1f}]")
    parser.add_argument("--reset-vmin", type=float, default=None,
                        help=f"Depole final amplitude V (where envelope "
                             f"freezes) [default {RESET_VMIN:.2f}]")
    parser.add_argument("--reset-amp-steps", type=int, default=None,
                        help=f"Depole amplitude steps "
                             f"[default {RESET_AMP_STEPS}]")
    parser.add_argument("--reset-cycles-per-amp", type=int, default=None,
                        help=f"Depole cycles per amplitude "
                             f"[default {RESET_CYCLES_PER_AMP}]")
    parser.add_argument("--reset-pulse-ms", type=float, default=None,
                        help=f"Depole pulse width (ms, sets both FIRSt and "
                             f"SECond) [default {RESET_PULSE_FIRST_MS:.2f}]")
    parser.add_argument("--reset-decay", type=str, default=None,
                        choices=["linear", "exponential"],
                        help=f"Depole envelope shape [default {RESET_DECAY}]")
    parser.add_argument("--hyst-cycles", type=int, default=None,
                        help="v2: full down+up loop cycles for DC hysteresis "
                             "[default 1 = legacy; metrics use the last cycle].")
    parser.add_argument("--hyst-min-dwell", type=float, default=None,
                        help="v2: dwell floor (s) for DC hysteresis; unset keeps "
                             f"the legacy {DC_HYST_MIN_POLING_DWELL_S:.0f} s floor.")
    parser.add_argument("--hyst-ac-vpp", type=float, default=DC_HYST_DEFAULT_AC_VPP,
                        help="v2: AC probe (Vpp), gated on only after each "
                             "DC-only poling dwell for the lock-in measurement; "
                             f"default {DC_HYST_DEFAULT_AC_VPP:g} Vpp keeps the probe "
                             "small vs the coercive window.")
    parser.add_argument("--hyst-adaptive-window", action="store_true", default=False,
                        help="v2: coarse recon loop first, fine grid centred on the "
                             "detected coercive voltages.")
    parser.add_argument("--hyst-vmax", type=float, default=DC_HYST_VMAX,
                        help="Symmetric DC hysteresis endpoint in volts. May be "
                             f"reduced per run but cannot exceed {DC_HYST_VMAX:g} V "
                             f"[default {DC_HYST_VMAX:g} V].")
    parser.add_argument("--hyst-voltage-step", type=float, default=None,
                        help="Optional uniform DC hysteresis voltage-step "
                             "override (V). Unset uses the centre-dense "
                             "profile (45 points at the default +/-40 V); "
                             "supplying a value overrides adaptive/fine-grid "
                             "spacing.")
    parser.add_argument("--hyst-tc-index", type=int, default=None,
                        help="v2: explicitly program the DSP7230 time-constant index "
                             "for the loop (14 = 500 ms) instead of trusting "
                             "front-panel state.")
    parser.add_argument("--hyst-sensitivity-index", type=int, default=None,
                        help="Explicit starting DSP7230 voltage sensitivity index "
                             "for the loop (14 = 50 uV RMS full scale); also the "
                             "post-loop restore range when dynamic ranging is enabled.")
    parser.add_argument(
        "--hyst-dynamic-lockin-range",
        dest="hyst_dynamic_lockin_range",
        action="store_true",
        default=False,
        help=(
            "Enable predictive hysteretic DSP7230 ranging during DC hysteresis "
            "only (5-200 uV RMS full scale). Normal changes occur with the AC "
            "probe off during the existing DC dwell."
        ),
    )
    parser.add_argument(
        "--hyst-fixed-lockin-range",
        dest="hyst_dynamic_lockin_range",
        action="store_false",
        help="Keep the requested DSP7230 sensitivity fixed during hysteresis.",
    )
    parser.add_argument("--hyst-lockin-settle", type=float, default=None,
                        help="Per-point lock-in settle time (s); automatically "
                             "raised to at least 5x the programmed time constant.")
    parser.add_argument("--hyst-lockin-readings", type=int, default=None,
                        help="Number of lock-in readings averaged at each DC point.")
    parser.add_argument("--hyst-lockin-read-delay", type=float, default=None,
                        help="Delay (s) between lock-in readings at each DC point.")
    parser.add_argument("--hysteresis-live-json", type=str, default=None,
                        help="Optional GUI progress JSON path. The cumulative "
                             "lock-in magnitude versus Vdc trace is replaced "
                             "atomically after every hysteresis point.")
    parser.add_argument("--start-from-zero", dest="start_from_zero",
                        action="store_true", default=None,
                        help="Force hysteresis trajectory to start from 0 V "
                             "(virgin curve) regardless of reset state.")
    parser.add_argument("--start-from-vmax", dest="start_from_vmax",
                        action="store_true", default=None,
                        help="Force hysteresis trajectory to start from "
                             "+V_max (saturated butterfly).")
    cli_args, _ = parser.parse_known_args()
    if not math.isfinite(float(cli_args.smu_compliance)) or float(cli_args.smu_compliance) <= 0.0:
        parser.error("--smu-compliance must be finite and greater than zero amperes")
    SMU_COMPLIANCE_A = float(cli_args.smu_compliance)

    # Spyder/no-flag-friendly: if no run-mode flag was provided, show an
    # interactive menu so the user can pick between full sweep / hysteresis-
    # only / domain-reset-only / reset-then-hysteresis / analyser diagnostic.
    # CLI flags still take precedence for power users on the terminal.
    if not (cli_args.hysteresis_only or cli_args.reset_domains
            or cli_args.ac_vpp_sweep_only or cli_args.analyser_sweep_only):
        mode = _select_mode_interactive()
        if mode == "hyst":
            cli_args.hysteresis_only = True
        elif mode == "reset":
            cli_args.reset_domains = True
        elif mode == "reset_hyst":
            cli_args.reset_domains = True
            cli_args.hysteresis_only = True
        elif mode == "anl":
            cli_args.analyser_sweep_only = True
        # mode == "full" -> leave both False, fall through to full sweep below

    # Mode dispatch -----------------------------------------------------------
    if cli_args.ac_vpp_sweep_only:
        run_ac_vpp_sweep_only(cli_args)
        return
    if cli_args.analyser_sweep_only:
        run_analyser_sweep_only(cli_args)
        return
    if cli_args.hysteresis_only:
        run_hysteresis_only(cli_args)
        return
    if cli_args.reset_domains:
        run_reset_only(cli_args)
        return
    # Otherwise: continue with the full Phase A+B+C sweep.
    hyst_vmax = validate_hysteresis_voltage_limit(
        cli_args.hyst_vmax,
        "DC hysteresis Vmax",
    )

    os.makedirs(OUTDIR_ROOT, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("=" * 60)
    print("  POCKELS CALIBRATION 2026 - RUN IDENTIFICATION")
    print("=" * 60)
    run_name = input("  Run name (e.g. BTO_chip2, pixel_A3): ").strip() or "unnamed"
    run_name_safe = run_name.replace(" ", "_").replace("/", "_").replace("\\", "_")
    pixel_id = input("  Pixel / position ID [optional]: ").strip()
    raw_v_pol = input(f"  DC poling voltage during sweeps (V) "
                      f"[Enter = {DC_POLING_V_PHASE_A:.1f}]: ").strip()
    if raw_v_pol == "":
        v_pol_phase_a = DC_POLING_V_PHASE_A
    else:
        try:
            v_pol_phase_a = float(raw_v_pol)
        except ValueError:
            print(f"  Could not parse '{raw_v_pol}', using "
                  f"{DC_POLING_V_PHASE_A:.1f} V.")
            v_pol_phase_a = DC_POLING_V_PHASE_A
    if abs(v_pol_phase_a) > DC_HYST_VMAX:
        print(f"  WARNING: |V_pol|={abs(v_pol_phase_a):.1f} V exceeds "
              f"DC_HYST_VMAX={DC_HYST_VMAX:.1f} V; clamping.")
        v_pol_phase_a = math.copysign(DC_HYST_VMAX, v_pol_phase_a)
    notes = input("  Notes [optional]: ").strip()

    dir_parts = [timestamp, run_name_safe]
    if pixel_id:
        dir_parts.append(pixel_id.replace(" ", "_").replace("/", "_"))
    root_dir = os.path.join(OUTDIR_ROOT, "_".join(dir_parts))
    os.makedirs(root_dir, exist_ok=True)

    run_label = run_name + (f" [{pixel_id}]" if pixel_id else "")

    hwp_angles = np.arange(HWP_START_DEG, HWP_STOP_DEG + 1e-9, HWP_STEP_DEG)
    analyzer_angles = np.linspace(ANL_START, ANL_STOP, N_ANL_POINTS)
    n_hwp = len(hwp_angles)
    n_vpp = len(AC_VOLTAGES_VPP)
    n_ang = len(analyzer_angles)
    total_points_all = n_hwp * n_vpp * n_ang

    # Time estimate
    time_per_point_s = LOCKIN_SETTLE_S + LOCKIN_AVG_READINGS * LOCKIN_READ_DELAY_S
    est_total_s = total_points_all * time_per_point_s

    # Run metadata
    meta_path = os.path.join(root_dir, "run_info.txt")
    with open(meta_path, "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id or '(not specified)'}\n")
        f.write(f"DC pol.  : {v_pol_phase_a:+.2f} V (SMU4201 hold during sweeps)\n")
        f.write(f"Notes    : {notes or '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"HWP range: {HWP_START_DEG} to {HWP_STOP_DEG} deg, "
                f"step {HWP_STEP_DEG} deg ({n_hwp} points)\n")
        f.write(f"Voltages : {AC_VOLTAGES_VPP}\n")
        f.write(f"Analyzer : {ANL_START} to {ANL_STOP} deg, "
                f"{n_ang} points (linspace)\n")
        f.write(f"Frequency: {FUNCGEN_FREQ_HZ} Hz\n")
        f.write(f"Lock-in  : {LOCKIN_AVG_READINGS} readings x "
                f"{LOCKIN_READ_DELAY_S}s\n")
        f.write(f"DC hyst  : +{hyst_vmax:.1f} -> "
                f"-{hyst_vmax:.1f} -> +{hyst_vmax:.1f} V, "
                f"{describe_hysteresis_spacing(vmax=hyst_vmax)} "
                f"(compliance {SMU_COMPLIANCE_A*1e3:.1f} mA)\n")

    print("\n" + "=" * 60)
    print("  POCKELS CALIBRATION 2026")
    print("=" * 60)
    print(f"  Run name       : {run_name}")
    if pixel_id: print(f"  Pixel ID       : {pixel_id}")
    print(f"  DC poling      : {v_pol_phase_a:+.2f} V (SMU4201, "
          f"{SMU_COMPLIANCE_A*1e3:.1f} mA compliance)")
    print(f"  HWP sweep      : {HWP_START_DEG:.0f} to {HWP_STOP_DEG:.0f} deg, "
          f"step {HWP_STEP_DEG:.0f} deg ({n_hwp} points)")
    print(f"  Voltages (Vpp) : {AC_VOLTAGES_VPP}")
    print(f"  Analyzer       : {ANL_START:.0f}-{ANL_STOP:.0f} deg, "
          f"{n_ang} points (linspace)")
    print(f"  Total points   : {total_points_all}")
    print(f"  Est. sweep time: {est_total_s/60:.0f} min "
          f"({est_total_s/3600:.1f} hrs)")
    print(f"  Null overhead  : ~15 s x {n_hwp} HWP angles = "
          f"~{15*n_hwp/60:.1f} min")
    print(f"  Output dir     : {os.path.abspath(root_dir)}")

    # --- Hardware bringup ---
    try:
        funcgen = _configure_funcgen()
    except Exception as e:
        print(f"  FATAL: function generator: {e}")
        return

    rot_hwp, rot_qwp, rot_anl = _connect_rotators()
    det = _connect_scope()
    lockin, sens_idx = _connect_lockin()
    sens_idx = configure_fixed_lockin_range(
        lockin,
        sens_idx,
        getattr(cli_args, "fixed_lockin_sensitivity_index", None),
    )

    det_adapter = _ScopeDetectorAdapter(det)

    # SMU bringup + ramp to Phase-A poling voltage. Anything below this point
    # must funnel through the cleanup block at the bottom so the SMU is left
    # in a safe state (0 V, output OFF).
    smu = None
    try:
        smu = _connect_smu(v_pol_phase_a)
    except Exception as e:
        print(f"  FATAL: SMU4201 bringup: {e}")
        # Best-effort tear-down of what we've already opened
        try: lockin.close()
        except Exception: pass
        try: det.close()
        except Exception: pass
        try: rot_hwp.close()
        except Exception: pass
        try: rot_qwp.close()
        except Exception: pass
        try: rot_anl.close()
        except Exception: pass
        try:
            funcgen_send(funcgen, "LOCAL")
            funcgen.close()
        except Exception: pass
        return

    # --- Calibration seed: load from JSON, or fall back to current positions ---
    if cli_args.cal:
        try:
            cal = _load_calibration_json(cli_args.cal)
            print(f"\n  [LOADED] {cal['source']}  (from --cal)")
            print(f"    HWP_fixed_deg   = {cal['hwp_deg']}")
            print(f"    Null_QWP_deg    = {cal['q_null_deg']}")
            print(f"    Null_Analyzer   = {cal['a_null_deg']}")
            print(f"    Null_Pmin_W     = {cal['p_null_W']}")
            if cal["raw"].get("orientation_calibration"):
                oc = cal["raw"]["orientation_calibration"]
                print(f"    Orientation     : regime={oc.get('regime')}, "
                      f"E-field={oc.get('E_field_in_polariser_frame_deg')} deg")
        except Exception as e:
            print(f"  WARNING: --cal '{cli_args.cal}' failed: {e}")
            print(f"  Falling back to interactive prompt.")
            cal = _prompt_load_calibration()
    else:
        cal = _prompt_load_calibration()
    loaded_src = None
    if cal is not None:
        loaded_src = cal["source"]
        # Move each axis to the loaded value (skip any that's None).
        if cal["hwp_deg"] is not None:
            print(f"  Moving HWP -> {cal['hwp_deg']:.2f} deg (from cal)...")
            safe_move_abs(rot_hwp, float(cal["hwp_deg"]),
                          settle_s=MOVE_SETTLE_S)
        if cal["q_null_deg"] is not None:
            print(f"  Moving QWP -> {cal['q_null_deg']:.2f} deg (from cal)...")
            safe_move_abs(rot_qwp, float(cal["q_null_deg"]),
                          settle_s=MOVE_SETTLE_S)
        if cal["a_null_deg"] is not None:
            print(f"  Moving ANL -> {cal['a_null_deg']:.2f} deg (from cal)...")
            safe_move_abs(rot_anl, float(cal["a_null_deg"]),
                          settle_s=MOVE_SETTLE_S)
        time.sleep(0.5)

    # Seed for the first_null_fast call uses whatever QWP/ANL are at NOW
    # (either the loaded calibration or the pre-existing position).
    q_seed = rot_qwp.get_angle() or 0.0
    a_seed = rot_anl.get_angle() or 0.0
    hwp_cur = rot_hwp.get_angle() or 0.0
    print(f"\n  Initial null seed: QWP={q_seed:.2f} deg, ANL={a_seed:.2f} deg "
          f"(HWP currently at {hwp_cur:.2f} deg)")
    if loaded_src is not None:
        print(f"  Seed sourced from: {loaded_src}")
    else:
        print(f"  Seed sourced from: current rotator positions "
              f"(no calibration loaded)")

    input(
        f"\n--- READY ---\n"
        f"  {n_hwp} HWP angles x {n_vpp} voltages x {n_ang} analyzer points\n"
        f"  SMU holding {v_pol_phase_a:+.1f} V DC poling for the entire sweep.\n"
        f"  After the sweep: peak find + DC hysteresis +{hyst_vmax:.0f} "
        f"-> -{hyst_vmax:.0f} -> +{hyst_vmax:.0f} V "
        f"({describe_hysteresis_spacing(vmax=hyst_vmax)}).\n"
        f"  Press Enter to start..."
    )

    # --- Main nested loop ---
    t_global_start = time.time()
    points_done_before = 0
    summary_rows = []
    all_results_by_hwp = {}        # theta_i -> {vpp -> result dict} for peak finder
    current_q_seed, current_a_seed = q_seed, a_seed

    # Phase A (analyzer sweep) + Phase B (peak find) + Phase C (DC hysteresis)
    # all live inside this try/finally so the SMU is guaranteed to be ramped
    # to 0 V and disabled even on Ctrl-C or any unhandled exception.
    try:
        for i_hwp, hwp_deg in enumerate(hwp_angles):
            print(f"\n{'=' * 70}")
            print(f"  HWP {i_hwp+1}/{n_hwp}: theta_i = {hwp_deg:.1f} deg  "
                  f"[Elapsed {fmt_elapsed(time.time() - t_global_start)}]")
            print(f"{'=' * 70}")

            # Per-HWP output directory
            hwp_dir = os.path.join(root_dir, f"hwp_{hwp_deg:05.1f}deg")
            os.makedirs(hwp_dir, exist_ok=True)

            # 1. Move HWP to target
            print(f"  Moving HWP -> {hwp_deg:.2f} deg...")
            safe_move_abs(rot_hwp, float(hwp_deg), settle_s=MOVE_SETTLE_S)
            time.sleep(0.5)

            # 2. Quick null QWP+ANL (seeded by previous null for fast convergence)
            print(f"  Quick-nulling QWP+ANL "
                  f"(seed: q={current_q_seed:.2f}, a={current_a_seed:.2f})...")
            t_null = time.time()
            try:
                q_null, a_null, p_null, log_rows = do_quick_null(
                    det_adapter, rot_qwp, rot_anl,
                    current_q_seed, current_a_seed
                )
                null_dt = time.time() - t_null
                print(f"  [NULL] q={q_null:.2f} deg, a={a_null:.2f} deg, "
                      f"P_min={p_null:.3e} W  ({null_dt:.1f}s, "
                      f"{len(log_rows)} probes)")
            except Exception as e:
                print(f"  [NULL FAILED]: {e}")
                print(f"  Falling back to seed position and continuing...")
                q_null, a_null = current_q_seed, current_a_seed
                p_null = float("nan")
                log_rows = []

            # Save null_info.json
            save_null_info(
                os.path.join(hwp_dir, "null_info.json"),
                hwp_deg, q_null, a_null, p_null,
                current_q_seed, current_a_seed, log_rows
            )

            # Update seed for next HWP angle
            current_q_seed, current_a_seed = q_null, a_null

            # 3. Run the full voltage series at this HWP + null
            all_results, sens_idx = run_voltage_series_at_hwp(
                rot_anl, det, lockin, funcgen, sens_idx,
                a_null, analyzer_angles,
                hwp_deg, run_label,
                hwp_dir,
                t_global_start, points_done_before, total_points_all,
            )
            points_done_before += n_vpp * n_ang
            all_results_by_hwp[float(hwp_deg)] = all_results

            # Per-HWP overlay plot
            save_hwp_overlay_plot(hwp_dir, all_results, hwp_deg, run_label)

            # Extract delta from each voltage's sweep
            fits_by_vpp = {}
            for vpp, res in all_results.items():
                fit = fit_delta_from_sweep(res["angle"], res["mag"])
                fits_by_vpp[vpp] = fit
                if fit is not None:
                    print(f"  [FIT] {vpp} Vpp: delta={fit['delta_deg']:+.3f} deg "
                          f"(amp={fit['amp']*1e6:.2f} uV)")

            summary_rows.append({
                "theta_i": float(hwp_deg),
                "q_null": q_null,
                "a_null": a_null,
                "p_null": p_null,
                "fits": fits_by_vpp,
            })

            # Partial-save the campaign summary after every HWP in case we crash
            try:
                save_campaign_summary(root_dir, summary_rows, run_label)
            except Exception as e:
                print(f"  [WARN] partial summary save failed: {e}")

        total_elapsed = time.time() - t_global_start
        print(f"\n{'=' * 70}")
        print(f"  PHASE A COMPLETE - Total time: {fmt_elapsed(total_elapsed)}")
        print(f"{'=' * 70}")

        # --- Final summary (with all HWP points) ---
        save_campaign_summary(root_dir, summary_rows, run_label)

        # ============== PHASE B: peak finder ==================================
        peak = find_peak_response(summary_rows, all_results_by_hwp)
        if peak is None:
            print("\n  [WARN] no valid peak found - skipping DC hysteresis sweep.")
        else:
            print(f"\n{'=' * 70}")
            print(f"  PHASE B - PEAK RESPONSE")
            print(f"{'=' * 70}")
            print(f"  Best lock-in mag : {peak['mag']*1e6:.3f} uV")
            print(f"    theta_i (HWP)  : {peak['theta_i']:.2f} deg")
            print(f"    V_AC           : {peak['vpp']} Vpp")
            print(f"    Analyzer       : {peak['anl_angle']:.2f} deg")
            print(f"    QWP null       : {peak['q_null']:.2f} deg")
            print(f"    ANL null       : {peak['a_null']:.2f} deg")

            # Move HWP / QWP / ANL to peak conditions
            print(f"\n  Moving motors to peak conditions...")
            safe_move_abs(rot_hwp, float(peak["theta_i"]), settle_s=MOVE_SETTLE_S)
            safe_move_abs(rot_qwp, float(peak["q_null"]),  settle_s=MOVE_SETTLE_S)
            safe_move_wrap(rot_anl, float(peak["anl_angle"]))
            time.sleep(0.5)

            # Set funcgen to the peak Vpp (CH1 sweep channel)
            print(f"  Setting funcgen CH{FUNCGEN_CH_SWEEP} -> {peak['vpp']} Vpp...")
            funcgen_set_voltage(funcgen, peak["vpp"])
            print(f"    Settling {FUNCGEN_SETTLE_S:.0f}s...", end="", flush=True)
            time.sleep(FUNCGEN_SETTLE_S)
            print(" done.")

            # ============ PHASE C: DC hysteresis sweep ========================
            hyst_dir = os.path.join(root_dir, "dc_hysteresis")
            os.makedirs(hyst_dir, exist_ok=True)
            try:
                sens_idx = run_dc_hysteresis_sweep(
                    smu, det, lockin, sens_idx,
                    hyst_dir, run_label, peak,
                    hold_voltage_v=v_pol_phase_a,
                    poling_dwell_s=cli_args.dwell,
                    **_hyst_kwargs_from_cli(cli_args, funcgen),
                )
            except Exception as e:
                print(f"  [ERROR] DC hysteresis sweep failed: {e}")
                import traceback
                traceback.print_exc()

            # Restore Phase-A poling so the device stays poled during teardown
            print(f"  Restoring SMU to {v_pol_phase_a:+.1f} V Phase-A hold...")
            smu_ramp_to(smu, 0.0, v_pol_phase_a)

        print(f"\nAll data saved in: {os.path.abspath(root_dir)}")

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Bringing SMU to 0 V before exit...")
    except Exception as e:
        print(f"\n[ERROR] Campaign aborted: {e}")
        import traceback
        traceback.print_exc()
    finally:
        smu_safe_shutdown(smu)

    # --- Cleanup ---
    input("\nPress Enter to close and disconnect...")
    plt.close("all")
    try: lockin.close()
    except Exception: pass
    try: det.close()
    except Exception: pass
    try: rot_hwp.close()
    except Exception: pass
    try: rot_qwp.close()
    except Exception: pass
    try: rot_anl.close()
    except Exception: pass
    try:
        funcgen_send(funcgen, "LOCAL")
        funcgen.close()
    except Exception: pass
    print("[DONE] All hardware disconnected.")


if __name__ == "__main__":
    main()
