#!/usr/bin/env python3
"""
Analyzer Sweep vs AC Voltage Series

Repeats the 0-360 deg analyzer sweep at multiple AC voltages, automatically
controlled via an Aim-TTi function generator (serial/USB). Between each
voltage step, the analyzer returns to its starting (null) position and the
function generator is programmed to the next Vpp before sweeping.

Records oscilloscope power, lock-in magnitude and phase at each point.
Produces per-voltage plots + a combined overlay plot.

CRITICAL: Does NOT home any rotators. HWP and QWP stay at their
          calibrated null positions. Only the analyzer moves.
"""

import os
import time
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import math
import pyvisa

from pockels_measurement_analysis import phasor_statistics

from elliptec_serial import ElliptecRotator
from POL_Chip_Test_Working_2026 import (
    PORT_ANL, ADDR_ANL,
    MOVE_SETTLE_S,
    SCOPE_VISA, SCOPE_SOURCE,
    DetectorTekTBS,
    safe_move_abs,
    MAX_CHUNK_DEG, ROTATOR_BACKLASH_DEG,
    STEP_VERIFY_TOL_DEG, STEP_VERIFY_RETRY, SWEEP_STEP_PAUSE_S,
)
from Lock_In_Mag_Phase_Track import (
    DSP7230, SENS_TABLE_VOLTS, MAX_SENS_IDX,
    format_voltage,
)

# ---- Configuration ----
LOCKIN_RESOURCE = "TCPIP0::169.254.150.230::50001::SOCKET"
LOCKIN_TIMEOUT_MS = 3000

ANL_START = 15.0
ANL_STOP = 355.0
ANL_STEP = 20.0

# AC voltages (Vpp) to sweep — set automatically via function generator
AC_VOLTAGES_VPP = [1, 3, 7, 9]

LOCKIN_SETTLE_S = 4.0
LOCKIN_AVG_READINGS = 15
LOCKIN_READ_DELAY_S = 0.25  # 15 samples * 0.25s = 3.75s measurement window
OVERLOAD_SETTLE_S = 5.0

OUTDIR = "analyser_sweep_voltage_series"

# Function Generator (Aim-TTi, serial over USB)
FUNCGEN_RESOURCE = "ASRL3::INSTR"
FUNCGEN_TIMEOUT_MS = 2000
FUNCGEN_CH_SWEEP = 1         # Channel for AC voltage sweep
FUNCGEN_CH_REF = 2           # Reference channel (fixed amplitude)
FUNCGEN_REF_AMPL_VPP = 0.5  # Reference channel amplitude (Vpp)
FUNCGEN_FREQ_HZ = 30000     # Modulation frequency (Hz)
FUNCGEN_SETTLE_S = 2.0       # Settle time after voltage change (s)
PRINT_WRAP_MOVES = True      # Fast automation can suppress successful move chatter.


def funcgen_send(instr, cmd):
    """Send command to Aim-TTi function generator with processing delay."""
    instr.write(cmd)
    time.sleep(0.1)


def connect_funcgen(resource_name=None):
    """
    Connect to the Aim-TTi function generator and verify communication.
    Returns the pyvisa instrument object, or raises on failure.
    """
    rm = pyvisa.ResourceManager()
    fg = rm.open_resource(resource_name or FUNCGEN_RESOURCE)
    fg.write_termination = '\n'
    fg.read_termination = '\n'
    fg.timeout = FUNCGEN_TIMEOUT_MS
    try:
        idn = fg.query("*IDN?").strip()
    except Exception:
        fg.close()
        raise
    print(f"  Function generator: {idn}")
    return fg


def funcgen_set_voltage(fg, vpp):
    """Set the sweep channel amplitude on the function generator."""
    funcgen_send(fg, f"CHN {FUNCGEN_CH_SWEEP}")
    # Preserve the requested sweep value; rounding to 0.1 V biased fits using
    # finer voltage grids while the CSV continued to record the unrounded value.
    funcgen_send(fg, f"AMPL {float(vpp):.9g}")


def angular_error(a, b):
    """Wrap-aware angular difference in degrees (always 0..180)."""
    diff = abs((a % 360.0) - (b % 360.0))
    return min(diff, 360.0 - diff)


def safe_move_wrap(rot, target_deg, settle_s=MOVE_SETTLE_S):
    """
    Move rotator to target, NEVER crossing the 360/0 encoder boundary.

    The ELL14 position counter accumulates — crossing 360/0 pushes it
    past the firmware range limit. If the shortest path would cross, we
    take the long way around instead.

    Uses chunked moves (<=MAX_CHUNK_DEG per step), backlash compensation,
    and position verification with retries.

    CRITICAL: Never homes the rotator — preserves calibrated null position.
    """
    target = target_deg % 360.0
    cur = (rot.get_angle() or 0.0) % 360.0

    # Rotator sometimes reports ~359.99 when physically at 0 — normalize
    if cur > 359.5:
        cur = 0.0

    def _shortest(a, b):
        return (b - a + 540.0) % 360.0 - 180.0

    def _safe_delta(start, end):
        """Compute delta that avoids crossing the 360/0 encoder boundary.
        If shortest path would cross, returns the long way around instead."""
        short = _shortest(start, end)
        endpoint = start + short
        if endpoint > 360.0 or endpoint < 0.0:
            # Shortest path crosses boundary — go the other way
            if short > 0:
                return short - 360.0
            else:
                return short + 360.0
        return short

    if abs(_shortest(cur, target)) < 0.05:
        return  # already there

    def _chunked_move(dest):
        """Move to dest in small chunks using RELATIVE moves.

        Previous version re-read position via get_angle() and used set_angle()
        for each chunk.  At the 360/0 boundary the readback can flip between
        0.00 and 359.99, causing _safe_delta to pick the wrong (345 deg) path,
        which the firmware rejects as 'out of range'.

        Fix: compute total delta once from the CALLER's known position, then
        use shift_angle (relative moves) for every intermediate chunk so the
        firmware never has to resolve the 360/0 ambiguity.  Only the final
        landing uses set_angle to nail the exact target.
        """
        delta = _safe_delta(cur, dest)   # use caller's 'cur', NOT a fresh read
        n = max(1, int(math.ceil(abs(delta) / MAX_CHUNK_DEG)))
        step = delta / n
        for _ in range(n):
            rot.shift_angle(step, settle_s=settle_s)
            time.sleep(0.015)
        rot.set_angle(dest, settle_s=settle_s)

    # Backlash compensation: first move to just below target, then approach from below
    backlash_target = (target - ROTATOR_BACKLASH_DEG) % 360.0

    path = _safe_delta(cur, backlash_target)
    n_chunks = max(1, int(math.ceil(abs(path) / MAX_CHUNK_DEG)))
    crosses = "boundary-safe" if path != _shortest(cur, backlash_target) else "direct"
    if PRINT_WRAP_MOVES:
        print(f"  [WRAP-MOVE] {cur:.2f} -> {target:.2f} deg "
              f"({crosses}, {path:+.1f} deg, {n_chunks} chunks)")

    for attempt in range(STEP_VERIFY_RETRY + 1):
        # Re-read position on each attempt (rotator may have partially moved)
        cur = (rot.get_angle() or 0.0) % 360.0
        if cur > 359.5:
            cur = 0.0

        try:
            _chunked_move(backlash_target)
            # Final approach from below (always positive direction, +0.3 deg)
            rot.set_angle(target, settle_s=settle_s)
            time.sleep(0.03)

            final = rot.get_angle()
            if final is None:
                final = target
            err = abs(_shortest(final, target))

            if err <= STEP_VERIFY_TOL_DEG:
                break

            if attempt < STEP_VERIFY_RETRY:
                print(f"  [WRAP-MOVE] Position error {err:.3f} deg > "
                      f"{STEP_VERIFY_TOL_DEG} deg, retrying ({attempt+1}/{STEP_VERIFY_RETRY})...")
            else:
                print(f"  [WRAP-MOVE] WARNING: Position error {err:.3f} deg after "
                      f"{STEP_VERIFY_RETRY + 1} attempts (continuing anyway)")
        except Exception as e:
            if attempt < STEP_VERIFY_RETRY:
                print(f"  [WRAP-MOVE] Move error: {e}, retrying ({attempt+1}/{STEP_VERIFY_RETRY})...")
                time.sleep(0.5)
            else:
                # NEVER home — preserve calibrated null position
                print(f"  [WRAP-MOVE] FAILED after {STEP_VERIFY_RETRY + 1} attempts: {e}")
                print(f"  [WRAP-MOVE] NOT homing (preserving calibrated null position)")
                raise

    time.sleep(SWEEP_STEP_PAUSE_S)
    actual = rot.get_angle()
    if PRINT_WRAP_MOVES:
        print(f"  [WRAP-MOVE] Target={target:.2f} deg, Actual={actual:.2f} deg")


def check_overload(lockin, current_idx):
    """Only bump sensitivity if overloaded. Never reduce."""
    try:
        overload = lockin.get_overload_byte()
    except Exception:
        return current_idx, False

    if overload & 0x03:
        new_idx = min(current_idx + 2, MAX_SENS_IDX)
        if new_idx != current_idx:
            lockin.set_sensitivity(new_idx)
            fs = SENS_TABLE_VOLTS[new_idx]
            print(f"    [LOCKIN] OVERLOAD -> sensitivity to {format_voltage(fs)}")
            return new_idx, True

    return current_idx, False


def circular_mean_deg(angles_deg):
    """Compute circular (wrap-safe) mean of angles in degrees."""
    rads = np.deg2rad(angles_deg)
    mean_rad = np.arctan2(np.mean(np.sin(rads)), np.mean(np.cos(rads)))
    return float(np.rad2deg(mean_rad))


def circular_std_deg(angles_deg):
    """Compute circular std deviation of angles in degrees."""
    rads = np.deg2rad(angles_deg)
    R = np.sqrt(np.mean(np.sin(rads))**2 + np.mean(np.cos(rads))**2)
    # R=1 means no spread, R=0 means uniform; circular variance = 1-R
    return float(np.rad2deg(np.sqrt(-2.0 * np.log(max(R, 1e-10)))))


def read_lockin_averaged(lockin, n_readings, delay_s):
    """Read synchronized lock-in samples and average their X/Y phasors.

    Averaging magnitude directly has a positive rectification bias near the
    noise floor.  The return signature remains compatible with older callers;
    ``std_mag`` and ``std_phase`` are delta-method phasor uncertainties.
    """
    mags = []
    phases = []
    timestamps = []
    attempts = 0
    max_attempts = max(int(n_readings), int(n_readings) * 4)
    while len(mags) < int(n_readings) and attempts < max_attempts:
        attempts += 1
        try:
            mag, pha = lockin.get_mag_phase()
            if not (np.isfinite(float(mag)) and np.isfinite(float(pha))):
                raise ValueError(f"non-finite lock-in read mag={mag!r}, phase={pha!r}")
            mags.append(mag)
            phases.append(pha)
            timestamps.append(time.time())
        except Exception as e:
            print(f" [WARN: {e}]", end="")
        time.sleep(delay_s)

    if mags:
        stats = phasor_statistics(mags, phases)
        return (stats["mean_mag_V"], stats["mean_phase_deg"],
                stats["magnitude_std_V"], stats["phase_std_deg"],
                mags, phases, timestamps)
    return float('nan'), float('nan'), float('nan'), float('nan'), [], [], []


def fmt_elapsed(seconds):
    """Format seconds as H:MM:SS."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def run_single_sweep(rot_anl, det, lockin, sens_idx, angles, voltage_label,
                     t_start, points_done_before=0, total_points_all=0):
    """Run one full analyzer sweep. Returns dict of result arrays, raw samples list, + final sens_idx."""
    n_points = len(angles)

    res = {
        'angle': [], 'angle_actual': [], 'power': [], 'volt': [],
        'mag': [], 'phase': [], 'sens': [],
        'mag_std': [], 'phase_std': [],
        'mag_min': [], 'mag_max': [],
        'phase_min': [], 'phase_max': [],
        'n_good_samples': [], 'timestamp': [],
    }
    # Raw samples: list of dicts, one per point
    raw_samples = []

    # Pre-sweep sanity check: read position, fix 360/0 boundary alias
    # The rotator at null (~360 deg) can report 359.99, 360.00, OR 0.00
    # — all are the same physical position but 0.00 causes safe_move_wrap
    # to miscompute the path.  Nudge away from the boundary in ALL cases.
    pre_pos = rot_anl.get_angle() or 0.0
    if pre_pos > 359.5 or pre_pos < 0.5:
        print(f"  [PRE-SWEEP] Rotator reports {pre_pos:.2f} deg (360/0 boundary), "
              f"nudging to 5 deg then to {angles[0]:.1f} deg...")
        safe_move_abs(rot_anl, 5.0, settle_s=MOVE_SETTLE_S)
        time.sleep(0.5)
        pre_pos = rot_anl.get_angle() or 0.0
    print(f"  [PRE-SWEEP] Position verified: {pre_pos:.2f} deg  -> first target: {angles[0]:.1f} deg")

    print(f"\n  Sweeping {n_points} points at {voltage_label}...\n")
    print(f"  {'#':>4s}  {'Angle':>7s}  {'Power (W)':>12s}  "
          f"{'LI Mag (V)':>14s}  {'Phase':>10s}")
    print(f"  {'-' * 58}")

    for i, angle in enumerate(angles):
        if i == 0:
            # First move may cross 0/360 boundary — use wrap-safe move
            safe_move_wrap(rot_anl, float(angle))
        else:
            safe_move_abs(rot_anl, float(angle))

        actual_angle = rot_anl.get_angle() or float(angle)

        print(f"  [{i+1:3d}/{n_points}] ANL -> {angle:6.1f} deg, settling {LOCKIN_SETTLE_S:.0f}s...",
              end="", flush=True)
        time.sleep(LOCKIN_SETTLE_S)

        sens_idx, changed = check_overload(lockin, sens_idx)
        if changed:
            print(f" overload settle {OVERLOAD_SETTLE_S:.0f}s...", end="", flush=True)
            time.sleep(OVERLOAD_SETTLE_S)

        point_time = time.time()
        p_w, v_det = det.read_power_w_stable()
        avg_mag, avg_phase, std_mag, std_phase, raw_mags, raw_phases, raw_ts = read_lockin_averaged(
            lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S
        )

        sens_volts = float(SENS_TABLE_VOLTS.get(sens_idx, 0))

        res['angle'].append(float(angle))
        res['angle_actual'].append(float(actual_angle))
        res['power'].append(float(p_w))
        res['volt'].append(float(v_det))
        res['mag'].append(float(avg_mag))
        res['phase'].append(float(avg_phase))
        res['sens'].append(sens_volts)
        res['mag_std'].append(float(std_mag))
        res['phase_std'].append(float(std_phase))
        res['mag_min'].append(float(np.min(raw_mags)) if raw_mags else float('nan'))
        res['mag_max'].append(float(np.max(raw_mags)) if raw_mags else float('nan'))
        res['phase_min'].append(float(np.min(raw_phases)) if raw_phases else float('nan'))
        res['phase_max'].append(float(np.max(raw_phases)) if raw_phases else float('nan'))
        res['n_good_samples'].append(len(raw_mags))
        res['timestamp'].append(point_time)

        # Store every individual sample
        raw_samples.append({
            'angle_target': float(angle),
            'angle_actual': float(actual_angle),
            'power_W': float(p_w),
            'voltage_V': float(v_det),
            'sens_V': sens_volts,
            'mags': list(raw_mags),
            'phases': list(raw_phases),
            'timestamps': list(raw_ts),
        })

        elapsed = time.time() - t_start
        global_done = points_done_before + i + 1
        if global_done > 0 and total_points_all > 0:
            rate = elapsed / global_done
            remaining = rate * (total_points_all - global_done)
            eta_str = f"  Elapsed {fmt_elapsed(elapsed)} | ETA {fmt_elapsed(remaining)}"
        else:
            eta_str = ""

        print(f"\r  [{i+1:3d}/{n_points}] {angle:6.1f} deg (act={actual_angle:.2f})  "
              f"P={p_w:.3e} W  Mag={avg_mag:.3e}+/-{std_mag:.1e} V  Phase={avg_phase:+7.1f} deg"
              f"  [{len(raw_mags)}/{LOCKIN_AVG_READINGS} ok]{eta_str}")

    return res, raw_samples, sens_idx


def save_sweep_csv(path, res, vpp, run_label=""):
    """Save one sweep's summary data to CSV (one row per angle)."""
    with open(path, "w") as f:
        if run_label:
            f.write(f"# Run={run_label}\n")
        f.write(f"# AC_Voltage_Vpp={vpp}\n")
        f.write(f"# Samples_per_point={LOCKIN_AVG_READINGS}\n")
        f.write("Analyzer_target_deg,Analyzer_actual_deg,Power_W,Voltage_V,"
                "LockIn_Mag_Mean_V,LockIn_Phase_Mean_deg,LockIn_Sens_V,"
                "LockIn_Mag_Std_V,LockIn_Phase_Std_deg,"
                "LockIn_Mag_Min_V,LockIn_Mag_Max_V,"
                "LockIn_Phase_Min_deg,LockIn_Phase_Max_deg,"
                "N_good_samples,Timestamp_epoch\n")
        for j in range(len(res['angle'])):
            f.write(f"{res['angle'][j]:.2f},"
                    f"{res['angle_actual'][j]:.3f},"
                    f"{res['power'][j]:.6e},"
                    f"{res['volt'][j]:.6e},"
                    f"{res['mag'][j]:.6e},"
                    f"{res['phase'][j]:.3f},"
                    f"{res['sens'][j]:.6e},"
                    f"{res['mag_std'][j]:.6e},"
                    f"{res['phase_std'][j]:.3f},"
                    f"{res['mag_min'][j]:.6e},"
                    f"{res['mag_max'][j]:.6e},"
                    f"{res['phase_min'][j]:.3f},"
                    f"{res['phase_max'][j]:.3f},"
                    f"{res['n_good_samples'][j]:d},"
                    f"{res['timestamp'][j]:.3f}\n")


def save_raw_samples_csv(path, raw_samples, vpp, run_label=""):
    """Save every individual lock-in reading to CSV (one row per sample)."""
    with open(path, "w") as f:
        if run_label:
            f.write(f"# Run={run_label}\n")
        f.write(f"# AC_Voltage_Vpp={vpp}\n")
        f.write(f"# All individual lock-in readings, {LOCKIN_AVG_READINGS} per angle\n")
        f.write("Analyzer_target_deg,Analyzer_actual_deg,Sample_idx,"
                "LockIn_Mag_V,LockIn_Phase_deg,Timestamp_epoch,"
                "Power_W,Voltage_V,LockIn_Sens_V\n")
        for pt in raw_samples:
            for k in range(len(pt['mags'])):
                f.write(f"{pt['angle_target']:.2f},"
                        f"{pt['angle_actual']:.3f},"
                        f"{k},"
                        f"{pt['mags'][k]:.6e},"
                        f"{pt['phases'][k]:.3f},"
                        f"{pt['timestamps'][k]:.3f},"
                        f"{pt['power_W']:.6e},"
                        f"{pt['voltage_V']:.6e},"
                        f"{pt['sens_V']:.6e}\n")


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ---- User identification for this run ----
    print("=" * 60)
    print("  RUN IDENTIFICATION")
    print("=" * 60)
    run_name = input("  Run name / label (e.g. pixel_A3, BTO_chip2): ").strip()
    if not run_name:
        run_name = "unnamed"
    # Sanitise for filesystem: replace spaces/slashes with underscores
    run_name_safe = run_name.replace(" ", "_").replace("/", "_").replace("\\", "_")

    pixel_id = input("  Pixel / position ID (e.g. row2_col5, centre) [optional]: ").strip()
    notes = input("  Notes (e.g. 50um spot, after anneal) [optional]: ").strip()

    # Build directory name: timestamp + run_name (+ pixel if given)
    dir_parts = [timestamp, run_name_safe]
    if pixel_id:
        pixel_id_safe = pixel_id.replace(" ", "_").replace("/", "_").replace("\\", "_")
        dir_parts.append(pixel_id_safe)
    else:
        pixel_id_safe = ""

    run_dir = os.path.join(OUTDIR, "_".join(dir_parts))
    os.makedirs(run_dir, exist_ok=True)

    # Save run metadata to a text file for easy reference
    meta_path = os.path.join(run_dir, "run_info.txt")
    with open(meta_path, "w") as f:
        f.write(f"Run name : {run_name}\n")
        f.write(f"Pixel ID : {pixel_id if pixel_id else '(not specified)'}\n")
        f.write(f"Notes    : {notes if notes else '(none)'}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Voltages : {AC_VOLTAGES_VPP}\n")
        f.write(f"Analyzer : {ANL_START} to {ANL_STOP} deg, step {ANL_STEP}\n")
        f.write(f"Frequency: {FUNCGEN_FREQ_HZ} Hz\n")
        f.write(f"Lock-in  : {LOCKIN_AVG_READINGS} readings x {LOCKIN_READ_DELAY_S}s\n")

    # Build a label string for plots and CSV headers
    run_label = run_name
    if pixel_id:
        run_label += f" [{pixel_id}]"

    n_voltages = len(AC_VOLTAGES_VPP)
    angles = np.arange(ANL_START, ANL_STOP + 1e-9, ANL_STEP)
    n_points = len(angles)
    time_per_point = LOCKIN_SETTLE_S + LOCKIN_AVG_READINGS * LOCKIN_READ_DELAY_S
    est_total_s = n_points * n_voltages * time_per_point

    print("\n" + "=" * 60)
    print("ANALYZER SWEEP - VOLTAGE SERIES")
    print("=" * 60)
    print(f"  Run name       : {run_name}")
    if pixel_id:
        print(f"  Pixel ID       : {pixel_id}")
    if notes:
        print(f"  Notes          : {notes}")
    print(f"  Analyzer       : {ANL_START:.0f} to {ANL_STOP:.0f} deg, step {ANL_STEP:.0f} deg")
    print(f"  Voltages (Vpp) : {AC_VOLTAGES_VPP}")
    print(f"  Total sweeps   : {n_voltages}")
    print(f"  Points/sweep   : {n_points}")
    print(f"  Lock-in settle : {LOCKIN_SETTLE_S:.0f} s per point")
    print(f"  Sampling       : {LOCKIN_AVG_READINGS} readings x {LOCKIN_READ_DELAY_S}s = {LOCKIN_AVG_READINGS * LOCKIN_READ_DELAY_S:.0f}s per point")
    print(f"  Func gen       : {FUNCGEN_RESOURCE} (CH{FUNCGEN_CH_SWEEP} sweep, CH{FUNCGEN_CH_REF} ref @ {FUNCGEN_REF_AMPL_VPP} Vpp)")
    print(f"  Frequency      : {FUNCGEN_FREQ_HZ} Hz")
    print(f"  Est. total time: {est_total_s/60:.0f} min ({est_total_s/3600:.1f} hrs)")
    print(f"  Output dir     : {os.path.abspath(run_dir)}")

    # ---- Connect function generator FIRST (quick sanity check) ----
    print("\nConnecting function generator (Aim-TTi)...")
    try:
        funcgen = connect_funcgen()
    except Exception as e:
        print(f"  FATAL: Cannot connect to function generator: {e}")
        print(f"  Check USB connection and that FUNCGEN_RESOURCE = '{FUNCGEN_RESOURCE}' is correct.")
        return

    # Configure reference channel (fixed amplitude, always on)
    print(f"  Configuring CH{FUNCGEN_CH_REF} (reference): "
          f"{FUNCGEN_REF_AMPL_VPP} Vpp @ {FUNCGEN_FREQ_HZ} Hz...")
    funcgen_send(funcgen, f"CHN {FUNCGEN_CH_REF}")
    funcgen_send(funcgen, "ZLOAD OPEN")
    funcgen_send(funcgen, f"FREQ {FUNCGEN_FREQ_HZ}")
    funcgen_send(funcgen, f"AMPL {FUNCGEN_REF_AMPL_VPP}")
    funcgen_send(funcgen, "OUTPUT ON")

    # Configure sweep channel (AMPL set later per-sweep, matching working Aim-TTi sequence)
    print(f"  Configuring CH{FUNCGEN_CH_SWEEP} (sweep): {FUNCGEN_FREQ_HZ} Hz, output ON...")
    funcgen_send(funcgen, f"CHN {FUNCGEN_CH_SWEEP}")
    funcgen_send(funcgen, "ZLOAD OPEN")
    funcgen_send(funcgen, f"FREQ {FUNCGEN_FREQ_HZ}")
    funcgen_send(funcgen, "OUTPUT ON")
    print("  Function generator OK.")

    # ---- Connect remaining hardware ----
    print("\nConnecting analyzer rotator (NO HOMING)...")
    rot_anl = ElliptecRotator(port=PORT_ANL, address=ADDR_ANL,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    anl_start_pos = rot_anl.get_angle()
    print(f"  Analyzer current position: {anl_start_pos:.2f} deg (preserving null)")

    print("Connecting oscilloscope...")
    det = DetectorTekTBS(SCOPE_VISA, SCOPE_SOURCE)
    det.connect()
    print("  Oscilloscope connected (auto-ranging enabled).")

    print("Connecting lock-in amplifier...")
    lockin = DSP7230(resource_name=LOCKIN_RESOURCE, timeout_ms=LOCKIN_TIMEOUT_MS)
    lockin.connect()
    lockin.apply_safe_startup(run_auto_measure=False)
    try:
        idn = lockin.get_id()
        print(f"  Lock-in: {idn}")
    except Exception:
        print("  Lock-in connected (IDN read failed, continuing)")

    try:
        sens_v = lockin.get_sensitivity_volts()
        from Lock_In_Mag_Phase_Track import nearest_sensitivity_index
        sens_idx = nearest_sensitivity_index(sens_v)
        print(f"  Lock-in sensitivity: {format_voltage(sens_v)} (index {sens_idx}) - KEEPING AS-IS")
    except Exception:
        sens_idx = 15
        print(f"  Lock-in sensitivity: could not read, assuming index {sens_idx} (100 uV)")

    print("  Testing lock-in MP. read...", end="")
    try:
        test_mag, test_pha = lockin.get_mag_phase()
        print(f" OK (mag={test_mag:.3e} V, phase={test_pha:.1f} deg)")
    except Exception as e:
        print(f" WARN: {e}")

    # ---- Baseline reading at null position ----
    print("\n  Taking baseline reading at null position...")
    time.sleep(2.0)
    baseline_p, baseline_v = det.read_power_w_stable()
    baseline_mag, baseline_pha, _, _, _, _, _ = read_lockin_averaged(lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S)
    print(f"  [BASELINE] P={baseline_p:.3e} W  LI_Mag={baseline_mag:.3e} V  "
          f"LI_Phase={baseline_pha:+.1f} deg  ANL={anl_start_pos:.2f} deg")

    # ---- Confirm before starting ----
    input(
        f"\n--- READY ---\n"
        f"  All hardware connected. Function generator outputs ON.\n"
        f"  Analyzer at {anl_start_pos:.2f} deg. HWP and QWP are NOT touched.\n"
        f"  Voltage sweep: {AC_VOLTAGES_VPP} Vpp (automated).\n"
        f"  Press Enter to start voltage series..."
    )

    # ---- Run voltage series ----
    all_results = {}  # vpp -> result dict
    consistency_log = []  # track return-to-null verification
    t_start = time.time()
    total_points_all = n_points * n_voltages

    for v_idx, vpp in enumerate(AC_VOLTAGES_VPP):
        print(f"\n{'=' * 60}")
        print(f"  VOLTAGE {v_idx+1}/{n_voltages}: {vpp} Vpp")
        print(f"{'=' * 60}")

        # Set function generator to this voltage
        print(f"  Setting function generator CH{FUNCGEN_CH_SWEEP} to {vpp} Vpp...")
        funcgen_set_voltage(funcgen, vpp)
        print(f"  Settling {FUNCGEN_SETTLE_S:.0f}s for voltage change...", end="", flush=True)
        time.sleep(FUNCGEN_SETTLE_S)
        print(" done.")

        # Return to start position before each voltage
        safe_move_wrap(rot_anl, anl_start_pos)
        anl_readback = rot_anl.get_angle()
        time.sleep(2.0)
        check_p, check_v = det.read_power_w_stable()
        check_mag, check_pha, _, _, _, _, _ = read_lockin_averaged(lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S)
        pos_error = angular_error(anl_readback, anl_start_pos)

        consistency_log.append({
            'vpp': vpp, 'when': 'before',
            'target': anl_start_pos, 'actual': anl_readback,
            'error_deg': pos_error,
            'power_W': check_p, 'li_mag_V': check_mag, 'li_phase': check_pha,
        })

        print(f"  [VERIFY] ANL target={anl_start_pos:.2f} deg, actual={anl_readback:.2f} deg "
              f"(err={pos_error:.3f} deg)")
        print(f"  [VERIFY] P={check_p:.3e} W  LI_Mag={check_mag:.3e} V  "
              f"(baseline P was {baseline_p:.3e} W)")

        if pos_error > 0.5:
            print(f"  *** WARNING: Position error {pos_error:.3f} deg > 0.5 deg! ***")

        points_done_before = v_idx * n_points
        print(f"\n  Starting sweep {v_idx+1}/{n_voltages} at {vpp} Vpp...  "
              f"[Elapsed {fmt_elapsed(time.time() - t_start)}]")
        res, raw_samples, sens_idx = run_single_sweep(
            rot_anl, det, lockin, sens_idx, angles,
            voltage_label=f"{vpp} Vpp",
            t_start=t_start, points_done_before=points_done_before,
            total_points_all=total_points_all,
        )
        all_results[vpp] = res

        # Save summary CSV (one row per angle)
        csv_path = os.path.join(run_dir, f"sweep_{vpp}Vpp.csv")
        save_sweep_csv(csv_path, res, vpp, run_label=run_label)
        print(f"\n  [SAVED] {csv_path}")

        # Save raw samples CSV (one row per individual reading)
        raw_path = os.path.join(run_dir, f"sweep_{vpp}Vpp_raw_samples.csv")
        save_raw_samples_csv(raw_path, raw_samples, vpp, run_label=run_label)
        print(f"  [SAVED] {raw_path}  ({sum(len(pt['mags']) for pt in raw_samples)} total readings)")

        # Return to start after sweep and verify
        safe_move_wrap(rot_anl, anl_start_pos)
        anl_readback = rot_anl.get_angle()
        time.sleep(2.0)
        check_p, check_v = det.read_power_w_stable()
        check_mag, check_pha, _, _, _, _, _ = read_lockin_averaged(lockin, LOCKIN_AVG_READINGS, LOCKIN_READ_DELAY_S)
        pos_error = angular_error(anl_readback, anl_start_pos)

        consistency_log.append({
            'vpp': vpp, 'when': 'after',
            'target': anl_start_pos, 'actual': anl_readback,
            'error_deg': pos_error,
            'power_W': check_p, 'li_mag_V': check_mag, 'li_phase': check_pha,
        })

        print(f"  [VERIFY] ANL returned to {anl_readback:.2f} deg (err={pos_error:.3f} deg)")
        print(f"  [VERIFY] P={check_p:.3e} W  LI_Mag={check_mag:.3e} V")

        print(f"\n  Sweep {v_idx+1}/{n_voltages} COMPLETE ({vpp} Vpp)  "
              f"[Elapsed {fmt_elapsed(time.time() - t_start)}]")

    # ---- Combined overlay plots ----
    total_elapsed = time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"ALL SWEEPS COMPLETE - Total time: {fmt_elapsed(total_elapsed)}")
    print(f"{'=' * 60}")

    colors = plt.cm.viridis(np.linspace(0.1, 0.9, n_voltages))

    # 3-panel overlay
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    for idx, vpp in enumerate(AC_VOLTAGES_VPP):
        res = all_results[vpp]
        ang = np.array(res['angle'])
        pwr = np.array(res['power'])
        mag = np.array(res['mag'])
        pha = np.array(res['phase'])
        mag_std = np.array(res['mag_std'])
        pha_std = np.array(res['phase_std'])
        c = colors[idx]
        label = f"{vpp} Vpp"

        ax1.plot(ang, pwr * 1e3, 'o-', ms=3, color=c, label=label)
        ax2.errorbar(ang, mag * 1e6, yerr=mag_std * 1e6,
                     fmt='s-', ms=3, color=c, capsize=2, elinewidth=0.8, label=label)
        ax3.errorbar(ang, pha, yerr=pha_std,
                     fmt='d-', ms=3, color=c, capsize=2, elinewidth=0.8, label=label)

    ax1.set_ylabel("Detected Power (mW)")
    ax1.set_title(f"Analyzer Sweep vs AC Voltage - {run_label}")
    ax1.grid(True)
    ax1.legend(fontsize=9)

    ax2.set_ylabel("Lock-in Magnitude (uV)")
    ax2.grid(True)
    ax2.legend(fontsize=9)

    ax3.set_ylabel("Lock-in Phase (deg)")
    ax3.set_xlabel("Analyzer Angle (deg)")
    ax3.grid(True)
    ax3.legend(fontsize=9)

    fig.tight_layout()
    fig_path = os.path.join(run_dir, "overlay_all_voltages.png")
    fig.savefig(fig_path, dpi=150)
    print(f"[SAVED] {fig_path}")

    # Individual per-voltage plots
    for vpp in AC_VOLTAGES_VPP:
        res = all_results[vpp]
        ang = np.array(res['angle'])
        pwr = np.array(res['power'])
        mag = np.array(res['mag'])
        pha = np.array(res['phase'])
        mag_std = np.array(res['mag_std'])
        pha_std = np.array(res['phase_std'])

        fig_v, (a1, a2, a3) = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
        a1.plot(ang, pwr * 1e3, 'o-', ms=4, color='tab:blue')
        a1.set_ylabel("Detected Power (mW)")
        a1.set_title(f"Analyzer Sweep @ {vpp} Vpp - {run_label}  (N={LOCKIN_AVG_READINGS} samples/pt)")
        a1.grid(True)

        a2.errorbar(ang, mag * 1e6, yerr=mag_std * 1e6,
                    fmt='s-', ms=4, color='tab:red', capsize=2, elinewidth=0.8)
        a2.set_ylabel("Lock-in Magnitude (uV)")
        a2.grid(True)

        a3.errorbar(ang, pha, yerr=pha_std,
                    fmt='d-', ms=4, color='tab:green', capsize=2, elinewidth=0.8)
        a3.set_ylabel("Lock-in Phase (deg)")
        a3.set_xlabel("Analyzer Angle (deg)")
        a3.grid(True)

        fig_v.tight_layout()
        fig_v_path = os.path.join(run_dir, f"sweep_{vpp}Vpp.png")
        fig_v.savefig(fig_v_path, dpi=150)

    print(f"[SAVED] Individual plots for each voltage")

    plt.show(block=False)
    plt.pause(0.5)

    # ---- Summary ----
    print(f"\n{'=' * 60}")
    print("VOLTAGE SERIES SUMMARY")
    print(f"{'=' * 60}")
    print(f"{'Vpp':>6s}  {'P_max (mW)':>12s}  {'LI_max (uV)':>12s}  "
          f"{'Phase_at_max':>12s}  {'Avg σ_mag(uV)':>14s}  {'Avg σ_pha(°)':>12s}")
    print(f"{'-' * 72}")
    for vpp in AC_VOLTAGES_VPP:
        res = all_results[vpp]
        mag = np.array(res['mag'])
        pwr = np.array(res['power'])
        pha = np.array(res['phase'])
        mag_std = np.array(res['mag_std'])
        pha_std = np.array(res['phase_std'])
        i_mag_max = np.nanargmax(mag)
        print(f"{vpp:6d}  {np.max(pwr)*1e3:12.4f}  "
              f"{mag[i_mag_max]*1e6:12.4f}  {pha[i_mag_max]:+12.1f}  "
              f"{np.nanmean(mag_std)*1e6:14.4f}  {np.nanmean(pha_std):12.2f}")

    # ---- Consistency log ----
    print(f"\nPOSITION CONSISTENCY LOG")
    print(f"{'Vpp':>6s}  {'When':>6s}  {'Target':>8s}  {'Actual':>8s}  "
          f"{'Err(deg)':>8s}  {'Power(W)':>12s}  {'LI_Mag(V)':>12s}")
    print(f"{'-' * 70}")
    for entry in consistency_log:
        print(f"{entry['vpp']:6d}  {entry['when']:>6s}  "
              f"{entry['target']:8.2f}  {entry['actual']:8.2f}  "
              f"{entry['error_deg']:8.3f}  {entry['power_W']:12.3e}  "
              f"{entry['li_mag_V']:12.3e}")

    # Save consistency log
    con_path = os.path.join(run_dir, "consistency_log.csv")
    with open(con_path, "w") as f:
        if run_label:
            f.write(f"# Run={run_label}\n")
        f.write("Vpp,When,Target_deg,Actual_deg,Error_deg,Power_W,LockIn_Mag_V,LockIn_Phase_deg\n")
        for entry in consistency_log:
            f.write(f"{entry['vpp']},{entry['when']},"
                    f"{entry['target']:.2f},{entry['actual']:.2f},"
                    f"{entry['error_deg']:.3f},{entry['power_W']:.6e},"
                    f"{entry['li_mag_V']:.6e},{entry['li_phase']:.3f}\n")
    print(f"[SAVED] {con_path}")

    print(f"\nAll data saved in: {os.path.abspath(run_dir)}")

    # ---- Cleanup ----
    input("\nPress Enter to close and disconnect...")
    plt.close('all')

    lockin.close()
    det.close()
    rot_anl.close()
    funcgen_send(funcgen, "LOCAL")
    funcgen.close()
    print("[DONE] All hardware disconnected. Analyzer at null position.")


if __name__ == "__main__":
    main()
