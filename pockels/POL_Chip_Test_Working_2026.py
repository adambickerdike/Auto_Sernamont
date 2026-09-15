#!/usr/bin/env python3
# --------------------------------------------------------------------
# BTO polarization calibration + 25x25 mm scan
#
# Workflow:
#   1) HOME STAGE (this blocks beam)
#   2) MOVE STAGE TO CENTRE (0,0) → beam path clear
#   3) CALIBRATION (NO BTO SAMPLE):
#         - QWP removed: analyzer sweep → cos² fit (extinction, parallel)
#         - QWP inserted: QWP sweep → γ0, δ
#         - QWP+Analyzer null → full extinction (+ 2D landscape)
#   4) PAUSE: ask user to insert BTO sample at centre
#   5) SCAN: 25×25 mm around centre with polarization measurements
#
# Now extended so you can:
#   - either run full calibration,
#   - or load an existing polar_calibration.json (like the one you provided)
# --------------------------------------------------------------------

import os, csv, json, time, math
from datetime import datetime
import threading
from decimal import Decimal as PyDecimal, getcontext

import numpy as np
import pyvisa
import matplotlib.pyplot as plt

from pockels_measurement_analysis import qwp_retardance_from_parallel_ratio

# High precision for stage coords
getcontext().prec = 10

# ====================== .NET / Kinesis initialisation ======================

import clr_loader
try:
    runtime = clr_loader.get_netfx()
    from pythonnet import set_runtime
    set_runtime(runtime)
except RuntimeError:
    # Already initialised
    pass

import clr

# --- Kinesis DLL references (EDIT PATHS IF NEEDED) ---
# Wrapped in try/except so scripts that only need rotators + detector
# (e.g. Lithium_Niobate_Calibration.py) can import without the stage connected.
HAVE_KINESIS = False
try:
    clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll")
    clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericMotorCLI.dll")
    clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.KCube.StepperMotorCLI.dll")
    # IMPORTANT: wildcard imports, like your working script
    from Thorlabs.MotionControl.DeviceManagerCLI import *
    from Thorlabs.MotionControl.GenericMotorCLI import *
    from Thorlabs.MotionControl.KCube.StepperMotorCLI import *
    from System import Decimal as NetDecimal
    HAVE_KINESIS = True
except Exception:
    NetDecimal = None
    HAVE_KINESIS = False

# Elliptec rotators
from elliptec_serial import ElliptecRotator

# Optional KLS1550 laser
try:
    from kls1550 import KLS1550
    HAVE_LASER = True
except Exception:
    KLS1550 = None
    HAVE_LASER = False

# Thread-safe print
print_lock = threading.Lock()
def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

# ============================== User Config ===============================

OUTDIR = "calibration_results_preBTO"
os.makedirs(OUTDIR, exist_ok=True)

# Scope (Tektronix TBS2kB)
SCOPE_VISA   = 'USB0::0x0699::0x03C7::C021052::0::INSTR'
SCOPE_SOURCE = 'CH1'

# Rotators
PORT_QWP, ADDR_QWP = "COM4", 2   # Quarter-wave plate
PORT_HWP, ADDR_HWP = "COM5", 1   # Half-wave plate
PORT_ANL, ADDR_ANL = "COM8", 2   # Analyzer

# Stage (KCubeStepper serial numbers)
STAGE_SERIAL_X = "26006987"
STAGE_SERIAL_Y = "26007025"
STAGE_TRAVEL_MM = 25.0
STAGE_CENTER_MM = PyDecimal('12.5')  # 25 / 2 → global 0,0

# Laser config
USE_LASER = HAVE_LASER
KLS_SERIAL = None
KLS_POWER_MW = 7.0
LASER_STABILIZE_S = 4.0

# Rotator motion tuning
MOVE_SETTLE_S = 0.28
MAX_CHUNK_DEG = 4.0
STEP_VERIFY_TOL_DEG = 0.08     # Tightened from 0.25 deg (ELL14 spec: +/-0.05 deg)
STEP_VERIFY_RETRY = 3           # Increased from 1
ROTATOR_BACKLASH_DEG = 0.3      # Overshoot for backlash compensation
LOG_MOTOR_POSITIONS = True       # Log all motor moves for post-hoc analysis
SWEEP_STEP_PAUSE_S = 1.60

# === STAGE ACCURACY SETTINGS ===
STAGE_POSITION_TOL_MM    = 0.005   # 5 um tolerance (KCubeStepper resolves ~0.5 um)
STAGE_BACKLASH_MM        = 0.02    # Backlash compensation offset (20 um)
STAGE_SETTLE_TIME_S      = 0.3     # Vibration damping after move
STAGE_MOVE_RETRIES       = 3       # Retry attempts per move
STAGE_PRECISION_VELOCITY = 0.5     # mm/s during scanning
STAGE_PRECISION_ACCEL    = 0.5     # mm/s2 during scanning

# PDA30B2 detector (V -> W)
RESP_A_PER_W = 0.875
PDA_GAIN_TABLE = {
    0:  {"HIZ": 1.51e3, "50OHM": 0.75e3},
    10: {"HIZ": 4.75e3, "50OHM": 2.38e3},
    20: {"HIZ": 1.50e4, "50OHM": 0.75e4},
    30: {"HIZ": 4.75e4, "50OHM": 2.38e4},
    40: {"HIZ": 1.51e5, "50OHM": 0.75e5},
    50: {"HIZ": 4.75e5, "50OHM": 2.38e5},
    60: {"HIZ": 1.50e6, "50OHM": 0.75e6},
    70: {"HIZ": 4.75e6, "50OHM": 2.38e6},
}
PDA_GAIN_DB = 10
PDA_LOAD    = "HIZ"

def volts_to_watts_scale():
    G_v_per_a = PDA_GAIN_TABLE[PDA_GAIN_DB][PDA_LOAD]
    return 1.0 / (RESP_A_PER_W * G_v_per_a)

V_TO_W = volts_to_watts_scale()
DARK_VOLTAGE_OFFSET = 0.0

# Measurement stability (scope MEAN)
STABLE_MIN_SAMPLES    = 18
STABLE_RSD_TOL        = 0.0030
STABLE_MAX_DURATION_S = 2.2
SAMPLE_SLEEP_S        = 0.020
# MAD_K moved to SPIKE/NOISE FILTERING SETTINGS section below

# Dynamic vertical scaling (from BTO_POL_CHIP_TEST.py - working version)
# VDIV_LEVELS: available V/div settings from largest to smallest
# THRESH_DOWN: if abs(voltage) < threshold, step to more sensitive scale
# THRESH_UP: hysteresis thresholds for stepping back to less sensitive scale
VDIV_LEVELS = [5.0, 1.0, 0.5, 0.2]
THRESH_DOWN = [5.0, 1.0, 0.5]      # voltage below this -> use next finer scale
THRESH_UP   = [6.5, 1.3, 0.65]    # voltage above this -> step back to coarser scale

# Polarization sweeps
CONFIRM_HWP_FIXED = 23.0

ANL_SWEEP_RANGE   = (0.0, 180.0); ANL_SWEEP_STEP   = 5.0
HWP_SWEEP_RANGE   = (0.0, 180.0); HWP_SWEEP_STEP   = 5.0
QWP_SWEEP_RANGE   = (0.0, 180.0); QWP_SWEEP_STEP   = 5.0

REFINE_WINDOW     = 12.0
REFINE_STEP       = 2.0

# Scan config (BTO)
ENABLE_BTO_SCAN          = True
SCAN_SIZE_MM             = 25.0          # 25×25 mm region (full central travel)
SCAN_POINTS_PER_AXIS     = 10            # 10×10 grid
SCAN_STEP_MM             = SCAN_SIZE_MM / (SCAN_POINTS_PER_AXIS - 1)  # nominal step
SCAN_CENTER_X_MM         = 0.0           # relative centre
SCAN_CENTER_Y_MM         = 0.0

# ===== FAST SCAN MODE (optimized for polarization mapping) =====
SCAN_STEP_PAUSE_S        = 1.5           # Wait after rotation for signal to fully settle
SCAN_SKIP_REDUNDANT      = True          # Skip moves when already at target angle
# Measurement: take many samples and average for clean data
SCAN_STABLE_MIN_SAMPLES    = 40          # Take 40 samples minimum for solid average
SCAN_STABLE_RSD_TOL        = 0.05        # Relaxed tolerance - we want ALL samples, not early exit
SCAN_STABLE_MAX_DURATION_S = 3.0         # Enough time to collect all 40+ samples
SCAN_SAMPLE_SLEEP_S        = 0.020       # 20ms between samples (40 samples = ~0.8s + overhead)

# ===== DETAILED LOGGING FOR DEBUGGING FLUCTUATIONS =====
ENABLE_DETAILED_LOGGING    = True        # Enable comprehensive logging for debugging
SAVE_WAVEFORMS             = True        # Save raw oscilloscope waveforms (one per P_par measurement)
SAVE_ALL_SAMPLES           = True        # Save all individual voltage samples to CSV
LOG_EVERY_N_WAVEFORMS      = 1           # Save waveform every N points (1 = every point, 5 = every 5th)

# ===== SPIKE/NOISE FILTERING SETTINGS =====
DISCARD_FIRST_N_SAMPLES    = 5           # Discard first N samples after settling (was 3)
MAD_K                      = 2.5         # Outlier threshold: samples > MAD_K * MAD from median are removed (was 3.5)
CLIP_DETECTION_MARGIN      = 0.90        # If reading > 90% of vdiv*8 (full scale), consider it clipped
RETRY_ON_SUSPICIOUS        = True        # Retry measurement if result looks suspicious
MAX_RETRY_ATTEMPTS         = 2           # Max retries for suspicious readings
SUSPICIOUS_RSD_THRESHOLD   = 0.15        # RSD > 15% triggers retry
SUSPICIOUS_RANGE_RATIO     = 5.0         # If V_max/V_min > 5, measurement is suspicious

# ===== STABLE MEASUREMENT FRAMEWORK (for P_par accuracy) =====
# Wait times for measurement stability
ROTATION_SETTLE_TIME_S     = 3.0         # Wait after waveplate rotation before any measurement (was 2.0)
PRE_AUTOSET_SETTLE_S       = 0.5         # Wait before triggering autoset
POST_AUTOSET_SETTLE_S      = 1.5         # Wait after autoset for display to stabilize
POST_SCALE_CHANGE_SETTLE_S = 2.0         # Wait after manual scale change (was 0.8 - critical fix!)

# ===== ROBUST RANGE SELECTION (Option C improvements) =====
RANGE_PROBE_SAMPLES        = 8           # Number of samples for range decision (was 1)
RANGE_PROBE_DELAY_S        = 0.025       # Delay between probe samples
RANGE_HEADROOM_FACTOR      = 1.5         # Headroom multiplier to avoid threshold bouncing
POST_RANGE_DISCARD_SAMPLES = 5           # Samples to discard after range change (scope settling)
POST_RANGE_DISCARD_DELAY_S = 0.020       # Delay between discarded samples

# Smart range selection (4x rule: signal should be within 4x the V/div range)
# This means for each V/div, max acceptable signal is 4 * V/div
# E.g., 10mV/div range handles signals up to 40mV, then switch to 20mV/div
SMART_RANGE_MULTIPLIER     = 4.0         # Signal should be < V/div * this multiplier
SMART_RANGE_MIN_FILL       = 0.25        # Signal should be > V/div * this (25% of one division minimum)

# Available V/div settings (mV scale for low signals, V scale for high)
# Ordered from smallest to largest for smart selection
SCOPE_VDIV_OPTIONS_MV = [10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0]  # in mV

# Stable measurement parameters
STABLE_MEAS_MIN_SAMPLES    = 25          # Minimum samples for stable measurement
STABLE_MEAS_MAX_SAMPLES    = 60          # Maximum samples to collect
STABLE_MEAS_TARGET_RSD     = 0.02        # Target RSD (2%) for early exit
STABLE_MEAS_MAX_DURATION_S = 5.0         # Maximum measurement duration
STABLE_MEAS_SAMPLE_DELAY_S = 0.025       # Delay between samples (25ms)
STABLE_MEAS_VALIDATION_ROUNDS = 2        # Number of validation rounds for critical measurements

# AUTOSET configuration
USE_AUTOSET                = True        # Use scope's AUTOSET command when available
AUTOSET_TIMEOUT_S          = 3.0         # Timeout waiting for autoset to complete

# P_par only mode - skip extinction and quadrature measurements for faster, focused P_par scans
PPAR_ONLY_MODE             = True        # Set True to only measure P_par (faster, more stable)
PPAR_VALIDATION_REMEASURE  = True        # Re-measure P_par if validation fails

# ========================== Motion Logger =============================

class MotionLogger:
    """Comprehensive motion logging for all actuators during a measurement run."""

    COLUMNS = [
        "timestamp_utc", "elapsed_s", "actuator", "move_type",
        "target", "actual", "error", "unit", "attempt",
        "backlash_applied", "settle_time_s", "notes"
    ]

    def __init__(self, log_path, scan_name="", cal_file=""):
        self._start_time = time.perf_counter()
        self._start_utc = datetime.utcnow()
        self._path = log_path
        self._stats = {}

        self._f = open(log_path, "w", newline="")
        self._f.write(f"# Run: {scan_name}\n")
        self._f.write(f"# Started: {self._start_utc.isoformat()}Z\n")
        self._f.write(f"# Calibration: {cal_file}\n")
        self._f.write(f"# Stage tolerance: {STAGE_POSITION_TOL_MM} mm\n")
        self._f.write(f"# Rotator tolerance: {STEP_VERIFY_TOL_DEG} deg\n")
        self._f.write(f"# Backlash (stage): {STAGE_BACKLASH_MM} mm\n")
        self._f.write(f"# Backlash (rotator): {ROTATOR_BACKLASH_DEG} deg\n")
        self._f.write(f"# Stage velocity: {STAGE_PRECISION_VELOCITY} mm/s\n")
        self._w = csv.writer(self._f)
        self._w.writerow(self.COLUMNS)
        self._f.flush()

    def log_move(self, actuator, move_type, target, actual, unit,
                 attempt=1, backlash_applied=False, settle_time_s=0.0, notes=""):
        error = (actual - target) if (actual is not None and target is not None) else 0.0
        now = datetime.utcnow()
        elapsed = time.perf_counter() - self._start_time

        self._w.writerow([
            now.isoformat() + "Z",
            f"{elapsed:.3f}",
            actuator,
            move_type,
            f"{target:.6f}" if target is not None else "",
            f"{actual:.6f}" if actual is not None else "",
            f"{error:.6f}",
            unit,
            attempt,
            backlash_applied,
            f"{settle_time_s:.3f}",
            notes
        ])
        self._f.flush()

        # Update per-actuator stats
        if actuator not in self._stats:
            self._stats[actuator] = {"errors": [], "retries": 0, "n_moves": 0}
        s = self._stats[actuator]
        if move_type not in ("BACKLASH_PRE",):
            s["errors"].append(abs(error))
            s["n_moves"] += 1
        if attempt > 1:
            s["retries"] += 1

    def log_event(self, actuator, event_type, notes=""):
        now = datetime.utcnow()
        elapsed = time.perf_counter() - self._start_time
        self._w.writerow([
            now.isoformat() + "Z",
            f"{elapsed:.3f}",
            actuator,
            event_type,
            "", "", "", "", "", "", "",
            notes
        ])
        self._f.flush()

    def print_summary(self):
        total_time = time.perf_counter() - self._start_time
        total_retries = 0
        lines = ["\n=== MOTION ACCURACY SUMMARY ==="]

        for actuator in sorted(self._stats.keys()):
            s = self._stats[actuator]
            errors = np.array(s["errors"]) if s["errors"] else np.array([0.0])
            scale = 1000.0 if "STAGE" in actuator else 1.0
            unit_str = "um" if "STAGE" in actuator else "deg"

            mean_err = float(np.mean(errors)) * scale
            max_err = float(np.max(errors)) * scale
            std_err = float(np.std(errors) * scale) if len(errors) > 1 else 0.0

            lines.append(
                f"  {actuator:10s}: n_moves={s['n_moves']}, "
                f"mean_error={mean_err:.1f}{unit_str}, max_error={max_err:.1f}{unit_str}, "
                f"std={std_err:.1f}{unit_str}, retries={s['retries']}"
            )
            total_retries += s["retries"]

        lines.append(f"  Total motion time: {total_time:.1f}s")
        lines.append(f"  Total retries: {total_retries}")

        summary_text = "\n".join(lines)
        print(summary_text)

        if hasattr(self, '_f') and self._f and not self._f.closed:
            self._f.write("\n" + summary_text + "\n")
            self._f.flush()

        return summary_text

    def close(self):
        if hasattr(self, '_f') and self._f and not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

# ========================== Rotator helpers ===========================

def safe_move_abs(rot: ElliptecRotator, target_deg: float,
                  settle_s: float = MOVE_SETTLE_S,
                  max_chunk_deg: float = MAX_CHUNK_DEG,
                  verify_tol_deg: float = STEP_VERIFY_TOL_DEG,
                  verify_retry: int = STEP_VERIFY_RETRY,
                  motion_logger=None, rot_name: str = "ROT",
                  move_type: str = "SCAN"):
    """Absolute move with chunking + backlash compensation + verify; one auto-home on error."""
    target = target_deg % 360.0
    backlash_target = (target - ROTATOR_BACKLASH_DEG) % 360.0

    def shortest(cur, tgt): return (tgt - cur + 540.0) % 360.0 - 180.0

    def _move_to(dest):
        cur = (rot.get_angle() or 0.0) % 360.0
        delta = shortest(cur, dest)
        n = max(1, int(math.ceil(abs(delta) / max(1e-6, max_chunk_deg))))
        step = delta / n; pos = cur
        for _ in range(n):
            pos = (pos + step) % 360.0
            rot.set_angle(pos, settle_s=settle_s)
            time.sleep(0.015)
        rot.set_angle(dest, settle_s=settle_s)

    tries = 0
    auto_home_done = False
    final = target
    while True:
        try:
            # Backlash compensation: move past target in negative direction first
            _move_to(backlash_target)
            if motion_logger:
                bl_actual = rot.get_angle()
                if bl_actual is None: bl_actual = backlash_target
                motion_logger.log_move(rot_name, "BACKLASH_PRE", backlash_target,
                                       bl_actual, "deg", attempt=tries + 1,
                                       backlash_applied=True)
            # Final approach from below (always positive direction)
            rot.set_angle(target, settle_s=settle_s)
            time.sleep(0.03)
            # Always verify position via readback
            final = rot.get_angle()
            if final is None:
                final = target
            err = abs(((final - target + 540.0) % 360.0) - 180.0)
            if motion_logger:
                notes = ""
                if err > verify_tol_deg and tries < verify_retry:
                    notes = f"exceeded tolerance ({err:.4f} > {verify_tol_deg}), retrying"
                motion_logger.log_move(rot_name, move_type, target, final, "deg",
                                       attempt=tries + 1, backlash_applied=True,
                                       settle_time_s=SWEEP_STEP_PAUSE_S, notes=notes)
            if err <= verify_tol_deg or tries >= verify_retry:
                break
            tries += 1
        except Exception as e:
            if (('sensor' in str(e).lower()) or ('limit' in str(e).lower()) or ('range' in str(e).lower())) and not auto_home_done:
                try:
                    try: rot.stop()
                    except: pass
                    rot.home(direction=0, settle_s=max(2.5, settle_s)); rot.tare()
                    if motion_logger:
                        motion_logger.log_event(rot_name, "HOME",
                                                "auto-homed after sensor/limit error")
                except Exception:
                    pass
                auto_home_done = True
            else:
                raise
    time.sleep(SWEEP_STEP_PAUSE_S)
    return final

def safe_move_abs_fast(rot: ElliptecRotator, target_deg: float,
                       current_angle_cache: dict = None,
                       rot_name: str = "ROT",
                       settle_s: float = MOVE_SETTLE_S,
                       pause_s: float = SCAN_STEP_PAUSE_S,
                       angle_tolerance: float = STEP_VERIFY_TOL_DEG,
                       motion_logger=None):
    """
    Fast move for scanning with backlash compensation and verification.
    Verifies position after every move; updates cache with actual angle.
    """
    target = target_deg % 360.0

    def shortest(cur, tgt): return (tgt - cur + 540.0) % 360.0 - 180.0

    # Check if already at target using cache, then verify
    if current_angle_cache is not None and rot_name in current_angle_cache:
        cached = current_angle_cache[rot_name]
        delta = abs(((target - cached + 540.0) % 360.0) - 180.0)
        if delta <= angle_tolerance and SCAN_SKIP_REDUNDANT:
            actual = rot.get_angle()
            if actual is not None:
                err = abs(((actual - target + 540.0) % 360.0) - 180.0)
                if err <= angle_tolerance:
                    if motion_logger:
                        motion_logger.log_move(rot_name, "SCAN", target, actual,
                                               "deg", attempt=1,
                                               notes="cache hit - skipped move")
                    current_angle_cache[rot_name] = actual
                    return actual

    # Move with backlash compensation and verification
    backlash_target = (target - ROTATOR_BACKLASH_DEG) % 360.0
    actual = target

    for attempt in range(1, STEP_VERIFY_RETRY + 1):
        # Step 1: Backlash pre-move
        cur = (rot.get_angle() or 0.0) % 360.0
        delta_bl = abs(shortest(cur, backlash_target))
        if delta_bl > 0.01:
            rot.set_angle(backlash_target, settle_s=settle_s * 0.5)
            if motion_logger:
                bl_actual = rot.get_angle()
                if bl_actual is None: bl_actual = backlash_target
                motion_logger.log_move(rot_name, "BACKLASH_PRE", backlash_target,
                                       bl_actual, "deg", attempt=attempt,
                                       backlash_applied=True)

        # Step 2: Final approach from below
        rot.set_angle(target, settle_s=settle_s)
        time.sleep(pause_s)

        # Step 3: Verify position
        actual = rot.get_angle()
        if actual is None:
            actual = target

        err = abs(((actual - target + 540.0) % 360.0) - 180.0)

        if err <= angle_tolerance:
            if motion_logger:
                motion_logger.log_move(rot_name, "SCAN", target, actual, "deg",
                                       attempt=attempt, backlash_applied=True,
                                       settle_time_s=pause_s)
            break
        else:
            if motion_logger:
                notes = f"err={err:.4f} > tol={angle_tolerance}"
                if attempt < STEP_VERIFY_RETRY:
                    notes += ", retrying"
                motion_logger.log_move(rot_name, "VERIFY_RETRY", target, actual,
                                       "deg", attempt=attempt, backlash_applied=True,
                                       notes=notes)

    # Update cache with verified actual angle
    if current_angle_cache is not None:
        current_angle_cache[rot_name] = actual

    return actual

# ====================== Tektronix TBS detector ======================

class DetectorTekTBS:
    def __init__(self, visa_addr=SCOPE_VISA, source=SCOPE_SOURCE):
        self.addr = visa_addr; self.source = source
        self.rm = None; self.scope = None; self.measu_ok = False
        self.cur_vdiv = None
        self.npts = self.xincr = self.xzero = self.ymult = self.yzero = self.yoff = None

    def _set_vdiv(self, vdiv):
        self.scope.write(f"{self.source}:SCAle {vdiv}")
        self.cur_vdiv = vdiv
        time.sleep(0.10)

    def _get_vdiv(self):
        try: v = float(self.scope.query(f"{self.source}:SCAle?"))
        except Exception: v = None
        self.cur_vdiv = v
        return v

    def _maybe_autoscale(self, v_probe):
        """
        Autoscale using absolute voltage thresholds with hysteresis.
        From BTO_POL_CHIP_TEST.py (working version).
        Returns True if scale was changed, False otherwise.
        """
        if self.cur_vdiv is None:
            self._get_vdiv()

        absv = abs(v_probe)

        # Determine target index based on voltage thresholds
        idx = 0
        if absv < THRESH_DOWN[0]: idx = 1
        if absv < THRESH_DOWN[1]: idx = 2
        if absv < THRESH_DOWN[2]: idx = 3

        cur_idx = VDIV_LEVELS.index(self.cur_vdiv) if self.cur_vdiv in VDIV_LEVELS else 0

        # Hysteresis: only step UP (to coarser scale) if voltage exceeds THRESH_UP
        if idx < cur_idx:
            if cur_idx == 3 and absv > THRESH_UP[2]: idx = 2
            if cur_idx == 2 and absv > THRESH_UP[1]: idx = 1
            if cur_idx == 1 and absv > THRESH_UP[0]: idx = 0

        if idx != cur_idx:
            self._set_vdiv(VDIV_LEVELS[idx])
            return True
        return False

    def connect(self):
        self.rm = pyvisa.ResourceManager()
        self.scope = self.rm.open_resource(self.addr)
        self.scope.timeout = 3000
        self.scope.read_termination = '\n'
        self.scope.write_termination = None
        self.scope.encoding = 'latin_1'
        self.scope.write('*CLS')
        print("[SCOPE]", self.scope.query('*IDN?').strip())

        self.scope.write(f'SELEct:{self.source} ON')
        self.scope.write('ACQuire:STOPAfter RUNSTop')
        self.scope.write('ACQuire:STATE RUN')
        self.scope.write('TRIGger:A:TYPe EDGE')
        self.scope.write('TRIGger:A:MODe AUTO')
        try: self.scope.write('TRIGger:A:EDGE:SOURce LINE')
        except: self.scope.write(f'TRIGger:A:EDGE:SOURce {self.source}')

        try:
            self.scope.write(f"MEASUrement:IMMed:SOURce1 {self.source}")
        except Exception:
            self.scope.write(f"MEASUrement:IMMed:SOURce {self.source}")
        self.scope.write("MEASUrement:IMMed:TYPe MEAN")
        try:
            _ = float(self.scope.query("MEASUrement:IMMed:VALue?"))
            self.measu_ok = True
        except Exception:
            self.measu_ok = False
            self.scope.write('HEADER 0')
            self.scope.write(f'DATA:SOURce {self.source}')
            self.scope.write('DATA:ENCdg RIBinary')
            self.scope.write('DATA:WIDth 1')
            self.scope.write('DATA:START 1')
            self._refresh_preamble()

        try: self._set_vdiv(VDIV_LEVELS[0])
        except: pass

    def _refresh_preamble(self):
        self.npts  = int(self.scope.query('WFMPRe:NR_PT?'))
        self.scope.write(f'DATA:STOP {self.npts}')
        self.xincr = float(self.scope.query('WFMPRe:XINCR?'))
        self.xzero = float(self.scope.query('WFMPRe:XZERO?'))
        self.ymult = float(self.scope.query('WFMPRe:YMULT?'))
        self.yzero = float(self.scope.query('WFMPRe:YZERO?'))
        self.yoff  = float(self.scope.query('WFMPRe:YOFF?'))

    def _read_mean_v_once(self):
        if self.measu_ok:
            return float(self.scope.query("MEASUrement:IMMed:VALue?"))
        raw = self.scope.query_binary_values('CURVe?', datatype='b', container=np.array)
        v = (raw.astype(np.float64) - self.yoff) * self.ymult + self.yzero
        return float(np.mean(v))

    def _robust_reduce(self, vals):
        a = np.array(vals, dtype=np.float64)
        med = np.median(a)
        mad = np.median(np.abs(a - med)) + 1e-15
        keep = np.abs(a - med) <= MAD_K * mad
        a2 = a[keep]
        if a2.size >= 3:
            return float(np.mean(a2)), float(np.std(a2, ddof=1))
        return float(np.mean(a)), float(np.std(a, ddof=1) if a.size > 1 else 0.0)

    def read_power_w_stable(self):
        """
        Stable measurement - from BTO_POL_CHIP_TEST.py (working version).
        Autoscale ONCE at start, then collect samples until stable.
        """
        # One-time autoscale at start
        try:
            v_probe = self._read_mean_v_once()
        except Exception:
            if not self.measu_ok: self._refresh_preamble()
            v_probe = 0.0

        try:
            if self._get_vdiv() is not None:
                if self._maybe_autoscale(v_probe):
                    time.sleep(0.12)
        except Exception:
            pass

        # Collect samples until stable
        vals = []
        t0 = time.perf_counter()
        while True:
            try:
                vm = self._read_mean_v_once()
            except Exception:
                if not self.measu_ok: self._refresh_preamble()
                time.sleep(SAMPLE_SLEEP_S); continue
            vals.append(vm)
            n = len(vals)
            if n >= max(4, STABLE_MIN_SAMPLES):
                v_mean, v_std = self._robust_reduce(vals)
                rsd = v_std / max(1e-12, abs(v_mean))
                if rsd <= STABLE_RSD_TOL:
                    v_final = v_mean; break
            if (time.perf_counter() - t0) >= STABLE_MAX_DURATION_S:
                v_final, _ = self._robust_reduce(vals); break
            time.sleep(SAMPLE_SLEEP_S)

        v_final = v_final - DARK_VOLTAGE_OFFSET
        p_w = v_final * V_TO_W
        return p_w, v_final

    def read_power_w_fast(self):
        """
        Fast measurement for spatial scanning - autoscale once, then collect samples.
        """
        sample_sleep = SCAN_SAMPLE_SLEEP_S if 'SCAN_SAMPLE_SLEEP_S' in globals() else SAMPLE_SLEEP_S
        min_samples = SCAN_STABLE_MIN_SAMPLES if 'SCAN_STABLE_MIN_SAMPLES' in globals() else STABLE_MIN_SAMPLES
        max_dur = SCAN_STABLE_MAX_DURATION_S if 'SCAN_STABLE_MAX_DURATION_S' in globals() else STABLE_MAX_DURATION_S

        # One-time autoscale at start
        try:
            v_probe = self._read_mean_v_once()
        except Exception:
            if not self.measu_ok: self._refresh_preamble()
            v_probe = 0.0

        try:
            if self._get_vdiv() is not None:
                if self._maybe_autoscale(v_probe):
                    time.sleep(0.12)
        except Exception:
            pass

        # Collect samples
        vals = []
        t0 = time.perf_counter()

        while len(vals) < min_samples:
            try:
                vm = self._read_mean_v_once()
                vals.append(vm)
            except Exception:
                if not self.measu_ok: self._refresh_preamble()
                time.sleep(sample_sleep)
                continue

            if (time.perf_counter() - t0) >= max_dur:
                break

            time.sleep(sample_sleep)

        if vals:
            v_final, _ = self._robust_reduce(vals)
        else:
            v_final = 0.0

        v_final = v_final - DARK_VOLTAGE_OFFSET
        p_w = v_final * V_TO_W
        return p_w, v_final

    def get_raw_waveform(self):
        """
        Capture the raw waveform from the oscilloscope.
        Returns: (time_array, voltage_array, metadata_dict)
        """
        try:
            # Ensure we have preamble data
            if self.npts is None:
                self._refresh_preamble()

            # Get current waveform
            self.scope.write('HEADER 0')
            self.scope.write(f'DATA:SOURce {self.source}')
            self.scope.write('DATA:ENCdg RIBinary')
            self.scope.write('DATA:WIDth 1')
            self.scope.write('DATA:START 1')
            self._refresh_preamble()

            raw = self.scope.query_binary_values('CURVe?', datatype='b', container=np.array)
            voltage = (raw.astype(np.float64) - self.yoff) * self.ymult + self.yzero
            time_arr = np.arange(len(voltage)) * self.xincr + self.xzero

            metadata = {
                'npts': int(len(voltage)),
                'xincr': float(self.xincr),
                'xzero': float(self.xzero),
                'ymult': float(self.ymult),
                'yzero': float(self.yzero),
                'yoff': float(self.yoff),
                'vdiv': float(self.cur_vdiv) if self.cur_vdiv else None,
            }
            return time_arr, voltage, metadata
        except Exception as e:
            return None, None, {'error': str(e)}

    def _is_clipped(self, voltage):
        """Check if voltage reading is likely clipped (near full scale)."""
        if self.cur_vdiv is None:
            return False
        full_scale = self.cur_vdiv * 8.0  # 8 divisions on typical scope
        return abs(voltage) > full_scale * CLIP_DETECTION_MARGIN

    def _is_measurement_suspicious(self, vals, diagnostics):
        """
        Check if measurement looks suspicious and should be retried.
        Returns: (is_suspicious, reason_string)
        """
        if len(vals) < 5:
            return False, ""

        a = np.array(vals, dtype=np.float64)
        v_mean = np.mean(a)
        v_std = np.std(a, ddof=1) if len(a) > 1 else 0.0
        v_min = np.min(a)
        v_max = np.max(a)

        # Check RSD (relative standard deviation)
        rsd = v_std / max(abs(v_mean), 1e-12)
        if rsd > SUSPICIOUS_RSD_THRESHOLD:
            return True, f"RSD={rsd*100:.1f}% > {SUSPICIOUS_RSD_THRESHOLD*100:.0f}%"

        # Check range ratio (max/min)
        if v_min > 0:
            range_ratio = v_max / v_min
            if range_ratio > SUSPICIOUS_RANGE_RATIO:
                return True, f"V_max/V_min={range_ratio:.1f} > {SUSPICIOUS_RANGE_RATIO:.0f}"

        # Check for clipped samples
        n_clipped = sum(1 for v in vals if self._is_clipped(v))
        if n_clipped > len(vals) * 0.1:  # More than 10% clipped
            return True, f"{n_clipped}/{len(vals)} samples clipped"

        return False, ""

    def read_power_w_fast_with_diagnostics(self, retry_count=0):
        """
        Fast measurement with full diagnostic data for debugging fluctuations.
        """
        diagnostics = {
            'all_samples': [],
            'all_samples_used': [],
            'sample_times': [],
            'range_changes': [],
            'range_changed_flag': False,
            'vdiv_initial': self.cur_vdiv,
            'vdiv_final': None,
            'scale_fill_percent': 0.0,
            'n_samples': 0,
            'n_samples_used': 0,
            'v_mean': 0.0,
            'v_std': 0.0,
            'v_min': 0.0,
            'v_max': 0.0,
            'outliers_removed': 0,
            'samples_discarded_initial': 0,
            'clipped_samples': 0,
            'failed_reads': 0,
            'measurement_duration_s': 0.0,
            'retry_count': retry_count,
            'suspicious_flag': False,
            'suspicious_reason': '',
        }

        meas_start = time.perf_counter()

        sample_sleep = SCAN_SAMPLE_SLEEP_S if 'SCAN_SAMPLE_SLEEP_S' in globals() else SAMPLE_SLEEP_S
        min_samples = SCAN_STABLE_MIN_SAMPLES if 'SCAN_STABLE_MIN_SAMPLES' in globals() else STABLE_MIN_SAMPLES
        max_dur = SCAN_STABLE_MAX_DURATION_S if 'SCAN_STABLE_MAX_DURATION_S' in globals() else STABLE_MAX_DURATION_S
        discard_first = DISCARD_FIRST_N_SAMPLES if 'DISCARD_FIRST_N_SAMPLES' in globals() else 0

        # ===== PHASE 1: One-time autoscale at start =====
        range_changed = False
        try:
            v_probe = self._read_mean_v_once()
        except Exception:
            if not self.measu_ok: self._refresh_preamble()
            v_probe = 0.0

        try:
            old_vdiv = self.cur_vdiv
            if self._get_vdiv() is not None:
                if self._maybe_autoscale(v_probe):
                    diagnostics['range_changes'].append({
                        'time': time.perf_counter() - meas_start,
                        'old_vdiv': old_vdiv,
                        'new_vdiv': self.cur_vdiv,
                        'trigger_voltage': v_probe
                    })
                    range_changed = True
                    time.sleep(0.12)
        except Exception:
            pass

        diagnostics['vdiv_final'] = self.cur_vdiv
        diagnostics['range_changed_flag'] = range_changed

        # ===== PHASE 2: Collect samples =====
        target_samples = min_samples + discard_first
        vals = []
        sample_times = []
        t0 = time.perf_counter()

        while len(vals) < target_samples:
            try:
                vm = self._read_mean_v_once()
                vals.append(vm)
                sample_times.append(time.perf_counter() - t0)

                # Check for clipping
                if self._is_clipped(vm):
                    diagnostics['clipped_samples'] += 1

            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
                time.sleep(sample_sleep)
                continue

            if (time.perf_counter() - t0) >= max_dur:
                break

            time.sleep(sample_sleep)

        diagnostics['all_samples'] = list(vals)
        diagnostics['sample_times'] = list(sample_times)
        diagnostics['n_samples'] = len(vals)

        # ===== PHASE 3: Discard first N samples (often noisy after settling) =====
        if len(vals) > discard_first:
            vals_used = vals[discard_first:]
            diagnostics['samples_discarded_initial'] = discard_first
        else:
            vals_used = vals
            diagnostics['samples_discarded_initial'] = 0

        diagnostics['measurement_duration_s'] = time.perf_counter() - meas_start

        # ===== PHASE 4: Robust averaging to remove outliers =====
        if vals_used:
            a = np.array(vals_used, dtype=np.float64)

            # Record raw statistics before filtering
            diagnostics['v_mean'] = float(np.mean(a))
            diagnostics['v_min'] = float(np.min(a))
            diagnostics['v_max'] = float(np.max(a))

            # MAD-based outlier removal
            med = np.median(a)
            mad = np.median(np.abs(a - med)) + 1e-15
            keep = np.abs(a - med) <= MAD_K * mad
            a2 = a[keep]
            diagnostics['outliers_removed'] = int(len(a) - len(a2))
            diagnostics['all_samples_used'] = list(a2)
            diagnostics['n_samples_used'] = len(a2)

            if a2.size >= 3:
                v_final = float(np.mean(a2))
                diagnostics['v_std'] = float(np.std(a2, ddof=1))
            else:
                v_final = float(np.mean(a))
                diagnostics['v_std'] = float(np.std(a, ddof=1) if a.size > 1 else 0.0)

            # Calculate scale fill percentage (how well signal uses the ADC range)
            if self.cur_vdiv is not None and self.cur_vdiv > 0:
                full_scale = self.cur_vdiv * 8.0
                diagnostics['scale_fill_percent'] = (abs(diagnostics['v_mean']) / full_scale) * 100.0
        else:
            v_final = 0.0

        # ===== PHASE 5: Check if measurement is suspicious and retry if needed =====
        is_suspicious, reason = self._is_measurement_suspicious(vals_used, diagnostics)
        diagnostics['suspicious_flag'] = is_suspicious
        diagnostics['suspicious_reason'] = reason

        if is_suspicious and RETRY_ON_SUSPICIOUS and retry_count < MAX_RETRY_ATTEMPTS:
            safe_print(f"    [RETRY] Suspicious measurement ({reason}), attempt {retry_count + 2}/{MAX_RETRY_ATTEMPTS + 1}")
            time.sleep(0.2)  # Brief pause before retry
            return self.read_power_w_fast_with_diagnostics(retry_count=retry_count + 1)

        v_final = v_final - DARK_VOLTAGE_OFFSET
        p_w = v_final * V_TO_W

        return p_w, v_final, diagnostics

    # ====================== STABLE MEASUREMENT FRAMEWORK ======================

    def _enable_autoset(self):
        """Enable the scope's AUTOSET feature if available."""
        try:
            self.scope.write("AUTOSet:ENABLE ON")
            return True
        except Exception as e:
            safe_print(f"    [AUTOSET] Enable failed: {e}")
            return False

    def _trigger_autoset(self):
        """
        Trigger AUTOSET and wait for it to complete.
        Returns True if successful, False otherwise.
        """
        if not USE_AUTOSET:
            return False

        try:
            safe_print("    [AUTOSET] Triggering automatic scale adjustment...")
            self.scope.write("AUTOSet EXECute")
            time.sleep(POST_AUTOSET_SETTLE_S)

            # Read back the new V/div
            self._get_vdiv()
            safe_print(f"    [AUTOSET] Complete. New V/div = {self.cur_vdiv}")
            return True
        except Exception as e:
            safe_print(f"    [AUTOSET] Failed: {e}")
            return False

    def _select_optimal_range_smart(self, signal_voltage_v):
        """
        Select the optimal V/div range based on the 4x rule:
        - Signal should be within 4x the V/div setting
        - E.g., 10mV/div → up to 40mV signal, 20mV/div → up to 80mV signal

        Returns the selected V/div value in Volts.
        """
        signal_mv = abs(signal_voltage_v) * 1000.0  # Convert to mV

        # Find the smallest range that can accommodate the signal with 4x headroom
        for vdiv_mv in SCOPE_VDIV_OPTIONS_MV:
            max_signal_mv = vdiv_mv * SMART_RANGE_MULTIPLIER
            min_signal_mv = vdiv_mv * SMART_RANGE_MIN_FILL

            if signal_mv <= max_signal_mv:
                # This range can handle the signal
                # But check if signal is too small (less than 25% of one division)
                if signal_mv >= min_signal_mv or vdiv_mv == SCOPE_VDIV_OPTIONS_MV[0]:
                    return vdiv_mv / 1000.0  # Return in Volts

        # Signal is larger than all ranges can handle, use the largest
        return SCOPE_VDIV_OPTIONS_MV[-1] / 1000.0

    def _set_optimal_range(self, signal_voltage_v):
        """
        Set the scope to the optimal range for the given signal voltage.
        Returns True if range was changed.
        """
        optimal_vdiv = self._select_optimal_range_smart(signal_voltage_v)

        if self.cur_vdiv is None:
            self._get_vdiv()

        if self.cur_vdiv != optimal_vdiv:
            safe_print(f"    [RANGE] Changing from {self.cur_vdiv*1000:.0f}mV/div to {optimal_vdiv*1000:.0f}mV/div "
                      f"(signal: {abs(signal_voltage_v)*1000:.1f}mV)")
            self._set_vdiv(optimal_vdiv)
            time.sleep(POST_SCALE_CHANGE_SETTLE_S)
            return True
        return False

    def read_power_stable_ppar(self, rotation_just_done=True):
        """
        STABLE MEASUREMENT FRAMEWORK for P_par measurements (Option C - Improved).

        This method provides the most accurate measurements by:
        1. Waiting for mechanical settling after rotation
        2. Multi-sample probing for robust range decision
        3. Adding headroom to avoid threshold bouncing
        4. Generous settling after range change
        5. Discarding post-range-change samples
        6. Collecting multiple samples with validation
        7. Robust outlier removal and averaging

        Args:
            rotation_just_done: If True, applies full rotation settle time

        Returns:
            (power_w, voltage_v, diagnostics_dict)
        """
        diagnostics = {
            'method': 'stable_ppar_v2',
            'rotation_settle_applied': rotation_just_done,
            'autoset_used': False,
            'range_changed': False,
            'initial_vdiv': self.cur_vdiv,
            'final_vdiv': None,
            'probe_voltage': 0.0,
            'probe_samples': [],
            'probe_median': 0.0,
            'probe_with_headroom': 0.0,
            'all_samples': [],
            'samples_used': [],
            'n_samples': 0,
            'n_samples_used': 0,
            'v_mean': 0.0,
            'v_std': 0.0,
            'v_min': 0.0,
            'v_max': 0.0,
            'rsd': 0.0,
            'outliers_removed': 0,
            'post_range_discarded': 0,
            'measurement_duration_s': 0.0,
            'validation_passed': True,
        }

        meas_start = time.perf_counter()

        # ===== PHASE 1: Full mechanical settling after rotation =====
        if rotation_just_done:
            safe_print(f"    [PHASE 1] Rotation settle: waiting {ROTATION_SETTLE_TIME_S:.1f}s...")
            time.sleep(ROTATION_SETTLE_TIME_S)

        # ===== PHASE 2: Multi-sample probe for robust range decision =====
        safe_print(f"    [PHASE 2] Probing signal level ({RANGE_PROBE_SAMPLES} samples)...")
        probe_vals = []
        for i in range(RANGE_PROBE_SAMPLES):
            try:
                v = self._read_mean_v_once()
                probe_vals.append(abs(v))
            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
            time.sleep(RANGE_PROBE_DELAY_S)

        diagnostics['probe_samples'] = list(probe_vals)

        if probe_vals:
            # Use median for robustness against outliers
            v_probe = float(np.median(probe_vals))
            diagnostics['probe_median'] = v_probe
            safe_print(f"    [PHASE 2] Probe: median={v_probe*1000:.2f}mV, "
                      f"range=[{min(probe_vals)*1000:.2f}, {max(probe_vals)*1000:.2f}]mV")
        else:
            v_probe = 0.0
            diagnostics['probe_median'] = 0.0
            safe_print(f"    [PHASE 2] WARNING: No valid probe readings!")

        diagnostics['probe_voltage'] = v_probe

        # ===== PHASE 3: Range selection with headroom to avoid threshold bouncing =====
        # Add headroom so signal stays well within the selected range
        v_with_headroom = v_probe * RANGE_HEADROOM_FACTOR
        diagnostics['probe_with_headroom'] = v_with_headroom

        # Special case: very weak signals - use AUTOSET
        if USE_AUTOSET and v_probe < 0.005:  # < 5mV
            safe_print(f"    [PHASE 3] Very weak signal, using AUTOSET...")
            diagnostics['autoset_used'] = self._trigger_autoset()
            if diagnostics['autoset_used']:
                diagnostics['range_changed'] = True
        else:
            # Use smart range selection with headroom
            optimal_vdiv = self._select_optimal_range_smart(v_with_headroom)

            if self.cur_vdiv is None:
                self._get_vdiv()

            if self.cur_vdiv != optimal_vdiv:
                safe_print(f"    [PHASE 3] Range change: {self.cur_vdiv*1000:.0f}mV/div -> "
                          f"{optimal_vdiv*1000:.0f}mV/div (signal={v_probe*1000:.2f}mV, "
                          f"headroom={v_with_headroom*1000:.2f}mV)")
                self._set_vdiv(optimal_vdiv)
                diagnostics['range_changed'] = True
            else:
                safe_print(f"    [PHASE 3] Range OK: {self.cur_vdiv*1000:.0f}mV/div "
                          f"(signal={v_probe*1000:.2f}mV)")

        diagnostics['final_vdiv'] = self.cur_vdiv

        # ===== PHASE 4: Generous settling after range change =====
        if diagnostics['range_changed']:
            safe_print(f"    [PHASE 4] Post-range-change settle: waiting {POST_SCALE_CHANGE_SETTLE_S:.1f}s...")
            time.sleep(POST_SCALE_CHANGE_SETTLE_S)

            # Discard samples during scope settling (these are often garbage)
            safe_print(f"    [PHASE 4] Discarding {POST_RANGE_DISCARD_SAMPLES} settling samples...")
            discarded = 0
            for _ in range(POST_RANGE_DISCARD_SAMPLES):
                try:
                    _ = self._read_mean_v_once()
                    discarded += 1
                except Exception:
                    pass
                time.sleep(POST_RANGE_DISCARD_DELAY_S)
            diagnostics['post_range_discarded'] = discarded
        else:
            # Even without range change, brief settle
            time.sleep(0.1)

        # ===== PHASE 5: Collect measurement samples with stability monitoring =====
        vals = []
        sample_times = []
        t0 = time.perf_counter()
        stable_achieved = False

        safe_print(f"    [PHASE 5] Collecting samples (min={STABLE_MEAS_MIN_SAMPLES}, "
                  f"target RSD={STABLE_MEAS_TARGET_RSD*100:.1f}%)...")

        while len(vals) < STABLE_MEAS_MAX_SAMPLES:
            try:
                vm = self._read_mean_v_once()
                vals.append(vm)
                sample_times.append(time.perf_counter() - t0)

            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
                time.sleep(STABLE_MEAS_SAMPLE_DELAY_S)
                continue

            # Check for early exit once we have enough samples
            n = len(vals)
            if n >= STABLE_MEAS_MIN_SAMPLES:
                a = np.array(vals, dtype=np.float64)
                v_mean = np.mean(a)
                v_std = np.std(a, ddof=1) if len(a) > 1 else 0.0
                rsd = v_std / max(abs(v_mean), 1e-12)

                if rsd <= STABLE_MEAS_TARGET_RSD:
                    stable_achieved = True
                    safe_print(f"    [PHASE 5] Stability achieved at {n} samples (RSD={rsd*100:.2f}%)")
                    break

            # Check timeout
            if (time.perf_counter() - t0) >= STABLE_MEAS_MAX_DURATION_S:
                safe_print(f"    [PHASE 5] Timeout at {len(vals)} samples")
                break

            time.sleep(STABLE_MEAS_SAMPLE_DELAY_S)

        diagnostics['all_samples'] = list(vals)
        diagnostics['n_samples'] = len(vals)
        diagnostics['measurement_duration_s'] = time.perf_counter() - meas_start

        # ===== PHASE 6: Discard initial samples (may still have transients) =====
        if len(vals) > DISCARD_FIRST_N_SAMPLES:
            vals_filtered = vals[DISCARD_FIRST_N_SAMPLES:]
        else:
            vals_filtered = vals

        # ===== PHASE 7: Robust averaging with MAD-based outlier removal =====
        if vals_filtered:
            a = np.array(vals_filtered, dtype=np.float64)

            # Raw statistics
            diagnostics['v_min'] = float(np.min(a))
            diagnostics['v_max'] = float(np.max(a))

            # MAD-based outlier removal
            med = np.median(a)
            mad = np.median(np.abs(a - med)) + 1e-15
            keep = np.abs(a - med) <= MAD_K * mad
            a_clean = a[keep]

            diagnostics['outliers_removed'] = int(len(a) - len(a_clean))
            diagnostics['samples_used'] = list(a_clean)
            diagnostics['n_samples_used'] = len(a_clean)

            if len(a_clean) >= 3:
                v_final = float(np.mean(a_clean))
                v_std = float(np.std(a_clean, ddof=1))
            else:
                v_final = float(np.mean(a))
                v_std = float(np.std(a, ddof=1) if len(a) > 1 else 0.0)

            diagnostics['v_mean'] = v_final
            diagnostics['v_std'] = v_std
            diagnostics['rsd'] = v_std / max(abs(v_final), 1e-12)

        else:
            v_final = 0.0
            diagnostics['validation_passed'] = False

        # ===== PHASE 8: Validation check =====
        if diagnostics['rsd'] > SUSPICIOUS_RSD_THRESHOLD:
            diagnostics['validation_passed'] = False
            safe_print(f"    [PHASE 8] WARNING: High RSD={diagnostics['rsd']*100:.1f}%")

        if vals_filtered and diagnostics['outliers_removed'] > len(vals_filtered) * 0.3:
            diagnostics['validation_passed'] = False
            safe_print(f"    [PHASE 8] WARNING: Many outliers: {diagnostics['outliers_removed']}/{len(vals_filtered)}")

        # Apply dark offset correction
        v_final = v_final - DARK_VOLTAGE_OFFSET
        p_w = v_final * V_TO_W

        # Final summary
        safe_print(f"    [RESULT] V={v_final*1000:.3f}mV, P={p_w*1e6:.2f}µW, "
                  f"RSD={diagnostics['rsd']*100:.2f}%, n={diagnostics['n_samples_used']}, "
                  f"outliers={diagnostics['outliers_removed']}, range_changed={diagnostics['range_changed']}")

        return p_w, v_final, diagnostics

    def read_power_with_autorange(self, max_iterations=3):
        """
        Read power with iterative auto-ranging.
        Keeps adjusting range until signal is optimally scaled.

        Returns:
            (power_w, voltage_v)
        """
        for iteration in range(max_iterations):
            # Read current signal
            try:
                v_probe = self._read_mean_v_once()
            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
                time.sleep(0.1)
                continue

            # Check if range needs adjustment
            optimal_vdiv = self._select_optimal_range_smart(v_probe)

            if self.cur_vdiv is None:
                self._get_vdiv()

            if abs(self.cur_vdiv - optimal_vdiv) < 0.001:
                # Range is already optimal, do stable measurement
                break

            # Adjust range
            self._set_vdiv(optimal_vdiv)
            time.sleep(POST_SCALE_CHANGE_SETTLE_S)

        # Final measurement with current range
        return self.read_power_w_stable()

    def close(self):
        try:
            if self.scope: self.scope.close()
        finally:
            if self.rm: self.rm.close()

# ======================= Stage helpers (Kinesis style) =====================

def py_to_net_decimal(py_dec):
    """Convert Python Decimal to .NET Decimal without float precision loss."""
    return NetDecimal.Parse(str(py_dec))

def set_stage_velocity(device, velocity_mm_s, accel_mm_s2):
    """Set stage velocity and acceleration parameters."""
    vel_params = device.GetVelocityParams()
    vel_params.MaxVelocity = NetDecimal(velocity_mm_s)
    vel_params.Acceleration = NetDecimal(accel_mm_s2)
    device.SetVelocityParams(vel_params)

def initialize_device(serial_no):
    """Initialize and connect to a single KCubeStepper (as in your working code)."""
    device = KCubeStepper.CreateKCubeStepper(serial_no)
    device.Connect(serial_no)
    time.sleep(0.25)

    device_info = device.GetDeviceInfo()
    safe_print(f"Connected to: {device_info.Description} (S/N: {serial_no})")

    device.StartPolling(250)
    time.sleep(0.25)
    device.EnableDevice()
    time.sleep(0.25)

    use_file_settings = DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings
    _ = device.LoadMotorConfiguration(device.DeviceID, use_file_settings)

    vel_params = device.GetVelocityParams()
    vel_params.MaxVelocity = NetDecimal(2.0)
    vel_params.Acceleration = NetDecimal(2.0)
    device.SetVelocityParams(vel_params)
    time.sleep(0.25)

    vel_params_check = device.GetVelocityParams()
    safe_print(f"  Velocity set to: {vel_params_check.MaxVelocity} mm/s, "
               f"Accel: {vel_params_check.Acceleration}")
    return device

def home_device(device, serial_no):
    safe_print(f"[STAGE] Homing {serial_no}.")
    try:
        device.Home(60000)
        safe_print(f"[STAGE] {serial_no} Homed. Position: {device.Position} mm")
    except Exception as e:
        safe_print(f"[STAGE] Homing warning for {serial_no}: {e}")
        safe_print(f"[STAGE] Current position: {device.Position} mm")

def move_device_absolute(device, position_netdecimal, axis_name="STAGE",
                         motion_logger=None, move_type="SCAN"):
    """Move stage axis to absolute position with verification and retry."""
    target_mm = float(str(position_netdecimal))
    actual_pos = target_mm

    for attempt in range(1, STAGE_MOVE_RETRIES + 1):
        device.MoveTo(position_netdecimal, 60000)
        actual_pos = float(str(device.Position))
        error_mm = abs(actual_pos - target_mm)

        if error_mm <= STAGE_POSITION_TOL_MM or attempt == STAGE_MOVE_RETRIES:
            notes = ""
            if error_mm > STAGE_POSITION_TOL_MM:
                notes = f"accepted after {attempt} attempts, err={error_mm*1000:.1f}um"
            if motion_logger:
                motion_logger.log_move(axis_name, move_type, target_mm, actual_pos,
                                       "mm", attempt=attempt,
                                       settle_time_s=STAGE_SETTLE_TIME_S, notes=notes)
            break
        else:
            if motion_logger:
                motion_logger.log_move(axis_name, "VERIFY_RETRY", target_mm,
                                       actual_pos, "mm", attempt=attempt,
                                       notes=f"err={error_mm*1000:.1f}um > tol={STAGE_POSITION_TOL_MM*1000:.0f}um, retrying")

    return actual_pos

def motor_to_global(motor_pos, center=STAGE_CENTER_MM):
    return PyDecimal(str(motor_pos)) - center

def global_to_motor(global_pos, center=STAGE_CENTER_MM):
    return global_pos + center

class XYStage:
    """
    Wrapper around your Kinesis stage, matching style of combined_stage_rotator_scan.
    Global coordinates: (-12.5..+12.5 mm) around physical centre (beam path at 0,0).
    """
    def __init__(self, device_x, device_y, motor_center=STAGE_CENTER_MM):
        self.device_x = device_x
        self.device_y = device_y
        self.motor_center = motor_center
        self._x_global = 0.0
        self._y_global = 0.0

    def home(self, serial_x, serial_y):
        home_device(self.device_x, serial_x)
        home_device(self.device_y, serial_y)

        x_motor = PyDecimal(str(self.device_x.Position))
        y_motor = PyDecimal(str(self.device_y.Position))
        x_global = motor_to_global(x_motor, self.motor_center)
        y_global = motor_to_global(y_motor, self.motor_center)
        self._x_global = float(x_global)
        self._y_global = float(y_global)
        safe_print(f"[STAGE] Homed. Approx global = ({self._x_global:+.3f}, {self._y_global:+.3f}) mm")

    def move_to_mm(self, x_global: float, y_global: float, motion_logger=None,
                   move_type: str = "SCAN"):
        gx = PyDecimal(str(x_global))
        gy = PyDecimal(str(y_global))

        motor_x = global_to_motor(gx, self.motor_center)
        motor_y = global_to_motor(gy, self.motor_center)

        safe_print(f"[STAGE] Move to global ({float(gx):+.3f}, {float(gy):+.3f}) mm "
                   f"-> motor ({float(motor_x):.3f}, {float(motor_y):.3f}) mm")

        # Backlash compensation: undershoot then approach from below
        backlash_offset = PyDecimal(str(STAGE_BACKLASH_MM))
        backlash_x = motor_x - backlash_offset
        backlash_y = motor_y - backlash_offset

        # Clamp to valid range
        zero = PyDecimal('0')
        travel_max = PyDecimal(str(STAGE_TRAVEL_MM))
        backlash_x = max(zero, min(backlash_x, travel_max))
        backlash_y = max(zero, min(backlash_y, travel_max))

        # Step 1: Backlash pre-move (undershoot)
        bx_net = py_to_net_decimal(backlash_x)
        by_net = py_to_net_decimal(backlash_y)
        self.device_x.MoveTo(bx_net, 60000)
        self.device_y.MoveTo(by_net, 60000)

        if motion_logger:
            bx_actual = float(str(self.device_x.Position))
            by_actual = float(str(self.device_y.Position))
            motion_logger.log_move("STAGE_X", "BACKLASH_PRE", float(backlash_x),
                                   bx_actual, "mm", backlash_applied=True)
            motion_logger.log_move("STAGE_Y", "BACKLASH_PRE", float(backlash_y),
                                   by_actual, "mm", backlash_applied=True)

        # Step 2: Final move to target with verification and retry
        mx_net = py_to_net_decimal(motor_x)
        my_net = py_to_net_decimal(motor_y)
        move_device_absolute(self.device_x, mx_net, "STAGE_X", motion_logger, move_type)
        move_device_absolute(self.device_y, my_net, "STAGE_Y", motion_logger, move_type)

        # Post-move settling for vibration damping
        time.sleep(STAGE_SETTLE_TIME_S)

        # Read back final positions
        x_motor_act = PyDecimal(str(self.device_x.Position))
        y_motor_act = PyDecimal(str(self.device_y.Position))
        x_global_act = motor_to_global(x_motor_act, self.motor_center)
        y_global_act = motor_to_global(y_motor_act, self.motor_center)

        self._x_global = float(x_global_act)
        self._y_global = float(y_global_act)

        safe_print(f"[STAGE] Actual global ≈ ({self._x_global:+.3f}, {self._y_global:+.3f}) mm")
        return self._x_global, self._y_global

    @property
    def pos_mm(self):
        return self._x_global, self._y_global

# ============================= Generic helpers =============================

def save_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)

def sweep_angles(rot, angles_deg, measure_fn, period=180.0,
                 motion_logger=None, rot_name="ROT"):
    rows = []
    for a in angles_deg:
        safe_move_abs(rot, a % 360.0, motion_logger=motion_logger,
                      rot_name=rot_name, move_type="SWEEP")
        p_w, v = measure_fn()
        rows.append((float(a % period), p_w, v))
    return np.array(rows)

def refine_extremum_1d(rot, center_deg, window_deg, step_deg, want='min',
                       measure_fn=None, period=180.0,
                       motion_logger=None, rot_name="ROT"):
    lo = center_deg - window_deg
    hi = center_deg + window_deg
    grid = np.arange(lo, hi + 1e-9, step_deg)
    best_a, best_p = None, None
    for a in grid:
        safe_move_abs(rot, a % 360.0, motion_logger=motion_logger,
                      rot_name=rot_name, move_type="REFINE")
        p, _ = measure_fn()
        if best_p is None or ((want == 'min' and p < best_p) or (want == 'max' and p > best_p)):
            best_a, best_p = a % period, p
    return best_a, best_p

def fit_cos2(theta_deg, P_w):
    t = np.deg2rad(theta_deg)
    X = np.column_stack([np.ones_like(t), np.cos(2*t), np.sin(2*t)])
    coeff, *_ = np.linalg.lstsq(X, P_w, rcond=None)
    A, B, C = coeff
    R = np.hypot(B, C)
    Pmax = 2.0 * R
    theta_off = (0.5 * np.arctan2(C, B)) % np.pi
    Pbg = A - 0.5 * Pmax
    return dict(Pmax=Pmax, theta_off_deg=np.rad2deg(theta_off), Pbg=Pbg, A=A, B=B, C=C)

def fit_qwp_cos4(gamma_deg, I_w):
    g = np.deg2rad(gamma_deg)
    X = np.column_stack([np.ones_like(g), np.cos(4*g), np.sin(4*g)])
    coeff, *_ = np.linalg.lstsq(X, I_w, rcond=None)
    A, B, C = coeff
    R = np.hypot(B, C)
    phi = np.arctan2(C, B)
    gamma0_deg = (np.rad2deg(phi) / 4.0) % 180.0
    Imax = A + R
    Imin = A - R
    visibility = (Imax - Imin) / max(1e-12, (Imax + Imin))
    r = Imin / max(1e-12, Imax)
    delta_est, delta_est_deg = qwp_retardance_from_parallel_ratio(r)
    return dict(A=A,B=B,C=C,R=R,gamma0_deg=gamma0_deg,
                Imax=Imax,Imin=Imin,visibility=visibility,
                r=r,delta_est_rad=delta_est,delta_est_deg=delta_est_deg)

def wrap180(x):
    y = x % 180.0
    if y < 0: y += 180.0
    return y

def golden_section_min_1d(measure_axis_fn, center_deg, span_deg,
                          tol_deg=1.2, max_iter=30):
    phi = (1 + 5**0.5) / 2
    invphi = 1 / phi
    a_u, b_u = -span_deg, +span_deg
    c_u = b_u - invphi * (b_u - a_u)
    d_u = a_u + invphi * (b_u - a_u)

    def f_u(u):
        ang = wrap180(center_deg + u)
        return measure_axis_fn(ang)

    fc = f_u(c_u)
    fd = f_u(d_u)

    it = 0
    while (b_u - a_u) > tol_deg and it < max_iter:
        if fc < fd:
            b_u, d_u, fd = d_u, c_u, fc
            c_u = b_u - invphi * (b_u - a_u)
            fc = f_u(c_u)
        else:
            a_u, c_u, fc = c_u, d_u, fd
            d_u = a_u + invphi * (b_u - a_u)
            fd = f_u(d_u)
        it += 1

    u_star = 0.5 * (a_u + b_u)
    p_star = f_u(u_star)
    return wrap180(center_deg + u_star), p_star

def quad_refine_1d(measure_axis_fn, center_deg, step_deg=2.0):
    offs = np.array([-2*step_deg, -step_deg, 0.0, step_deg, 2*step_deg])
    pts = []
    for d in offs:
        ang = wrap180(center_deg + d)
        p = measure_axis_fn(ang)
        pts.append((d, p))
    xs = np.array([d for d,_ in pts])
    ys = np.array([p for _,p in pts], dtype=float)
    X = np.column_stack([xs**2, xs, np.ones_like(xs)])
    try:
        a, b, c = np.linalg.lstsq(X, ys, rcond=None)[0]
        if a > 0:
            x_star = -b/(2*a)
            if abs(x_star) <= 2*step_deg:
                ang_star = wrap180(center_deg + x_star)
                p_star = measure_axis_fn(ang_star)
                return ang_star, p_star
    except Exception:
        pass
    i = int(np.argmin(ys))
    return wrap180(center_deg + offs[i]), float(ys[i])

def null_qwp_analyzer(det, rot_qwp, rot_anl,
                      start_qwp_deg, start_anl_deg,
                      init_span_qwp=20.0, init_span_anl=20.0,
                      cycles=3, tol_deg=1.2, log_path=None):
    path_log = []

    def measure_qwp(qdeg):
        safe_move_abs(rot_qwp, qdeg)
        p, _ = det.read_power_w_stable()
        path_log.append(("QWP", float(qdeg), float(p)))
        return p

    def measure_anl(adeg):
        safe_move_abs(rot_anl, adeg)
        p, _ = det.read_power_w_stable()
        path_log.append(("ANL", float(adeg), float(p)))
        return p

    q, a = wrap180(start_qwp_deg), wrap180(start_anl_deg)
    span_q, span_a = float(init_span_qwp), float(init_span_anl)

    safe_move_abs(rot_qwp, q); safe_move_abs(rot_anl, a)
    p0, _ = det.read_power_w_stable()
    print(f"  [START] q={q:.2f}°, a={a:.2f}°, P={p0:.3e} W")

    for k in range(cycles):
        def m_q(z):
            safe_move_abs(rot_anl, a)
            return measure_qwp(z)
        q, p_q = golden_section_min_1d(m_q, q, span_q, tol_deg=tol_deg)
        print(f"  [C{k+1}] QWP min → q={q:.2f}°, P={p_q:.3e} W")

        def m_a(z):
            safe_move_abs(rot_qwp, q)
            return measure_anl(z)
        a, p_a = golden_section_min_1d(m_a, a, span_a, tol_deg=tol_deg)
        print(f"  [C{k+1}] ANL  min → a={a:.2f}°, P={p_a:.3e} W")

        span_q *= 0.6; span_a *= 0.6

    safe_move_abs(rot_anl, a)
    q_ref, p1 = quad_refine_1d(measure_qwp, q, step_deg=2.0)
    safe_move_abs(rot_qwp, q_ref)
    a_ref, p2 = quad_refine_1d(measure_anl, a, step_deg=2.0)
    q, a = q_ref, a_ref
    print(f"  [REFINE] q={q:.2f}°, a={a:.2f}°, P≈{min(p1,p2):.3e} W")

    offsets = [-2.0, 0.0, +2.0]
    best = (q, a)
    best_p = float('inf')
    for dq in offsets:
        for da in offsets:
            qq = wrap180(q + dq); aa = wrap180(a + da)
            safe_move_abs(rot_qwp, qq); safe_move_abs(rot_anl, aa)
            p, _ = det.read_power_w_stable()
            path_log.append(("2D", float(qq), float(p)))
            if p < best_p:
                best_p, best = p, (qq, aa)
    q, a = best
    safe_move_abs(rot_qwp, q); safe_move_abs(rot_anl, a)
    p_final, _ = det.read_power_w_stable()
    print(f"  [FINAL] q={q:.2f}°, a={a:.2f}°, P={p_final:.3e} W")

    if log_path is not None:
        with open(log_path, "w", newline="") as f:
            w = csv.writer(f); w.writerow(["Axis","Angle_deg","Power_W"])
            for row in path_log: w.writerow(row)

    return q, a, p_final

def null_landscape_scan(det, rot_qwp, rot_anl,
                        q_center, a_center,
                        span_q=12.0, span_a=12.0,
                        step_deg=2.0,
                        outdir: str = OUTDIR):
    """
    Scan a 2D region around the null (QWP, Analyzer) and generate a heatmap
    of power vs the two angles.
    """
    q_vals = np.arange(q_center - span_q, q_center + span_q + 1e-9, step_deg)
    a_vals = np.arange(a_center - span_a, a_center + span_a + 1e-9, step_deg)

    Q, A = np.meshgrid(q_vals, a_vals)
    P = np.zeros_like(Q, dtype=float)
    rows = []

    print("\n[NULL] 2D landscape scan around null point...")
    for iy, a in enumerate(a_vals):
        for ix, q in enumerate(q_vals):
            safe_move_abs(rot_qwp, q)
            safe_move_abs(rot_anl, a)
            p, _ = det.read_power_w_stable()
            P[iy, ix] = p
            rows.append((q, a, p))
        print(f"  Row {iy+1}/{len(a_vals)} done.")

    csv_path = os.path.join(outdir, "null_landscape.csv")
    save_csv(csv_path, ["QWP_deg", "Analyzer_deg", "Power_W"], rows)
    print(f"[NULL] Saved 2D landscape CSV -> {os.path.abspath(csv_path)}")

    plt.figure()
    plt.imshow(P*1e3, origin='lower',
               extent=[q_vals.min(), q_vals.max(),
                       a_vals.min(), a_vals.max()],
               aspect='auto')
    plt.colorbar(label="Power (mW)")
    plt.xlabel("QWP angle (deg)")
    plt.ylabel("Analyzer angle (deg)")
    plt.title("Nulling landscape: power vs QWP/Analyzer angles")
    plt.scatter([q_center], [a_center], marker='x')
    fig_path = os.path.join(outdir, "null_landscape_heatmap.png")
    plt.savefig(fig_path, dpi=150)
    print(f"[NULL] Saved 2D nulling landscape plot -> {os.path.abspath(fig_path)}")

# ---------- Calibration pack save/load ----------

CAL_FILE = os.path.join(OUTDIR, "polar_calibration.json")

def save_calibration_pack(pack: dict, path: str = CAL_FILE):
    pack = dict(pack)
    pack["timestamp_utc"] = datetime.utcnow().isoformat() + "Z"
    with open(path, "w") as f:
        json.dump(pack, f, indent=2)
    print(f"[CAL] Saved calibration pack -> {os.path.abspath(path)}")

def load_calibration_pack(path: str = CAL_FILE):
    """Load an existing polarization calibration JSON."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No calibration file found at: {path}")
    with open(path, "r") as f:
        pack = json.load(f)
    print(f"[CAL] Loaded calibration pack <- {os.path.abspath(path)}")
    return pack

def list_calibration_files(outdir: str = OUTDIR):
    """List all calibration JSON files in the output directory."""
    import glob
    pattern = os.path.join(outdir, "*_calibration.json")
    files = glob.glob(pattern)
    # Also check for the default file
    default_file = os.path.join(outdir, "polar_calibration.json")
    if os.path.isfile(default_file) and default_file not in files:
        files.append(default_file)
    # Sort by modification time (newest first)
    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
    return files

def select_or_create_calibration(outdir: str = OUTDIR):
    """
    Interactive menu to select an existing calibration or create a new one.
    Returns: (cal_pack, cal_file_path) if using existing, or (None, new_cal_name) if creating new.
    """
    cal_files = list_calibration_files(outdir)

    print("\n" + "="*60)
    print("CALIBRATION SELECTION")
    print("="*60)

    if cal_files:
        print("\nAvailable calibration files:")
        for i, f in enumerate(cal_files, 1):
            fname = os.path.basename(f)
            mtime = datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")
            # Try to read timestamp from file
            try:
                with open(f, 'r') as fp:
                    data = json.load(fp)
                    ts = data.get('timestamp_utc', 'unknown')
                    ext_db = data.get('Extinction_dB_vs_QWPoutMax', 0)
                    print(f"  [{i}] {fname}")
                    print(f"      Created: {ts}, Extinction: {ext_db:.1f} dB")
            except:
                print(f"  [{i}] {fname} (modified: {mtime})")

        print(f"\n  [N] Create NEW calibration")
        print("="*60)

        while True:
            choice = input("\nEnter number to load calibration, or 'N' for new: ").strip().upper()
            if choice == 'N':
                # Create new calibration
                cal_name = input("\nEnter name for new calibration (e.g., 'setup_20jan', 'post_alignment'): ").strip()
                if not cal_name:
                    cal_name = datetime.now().strftime("%Y%m%d_%H%M%S")
                # Sanitize
                cal_name = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in cal_name).strip("_")
                if not cal_name:
                    cal_name = datetime.now().strftime("%Y%m%d_%H%M%S")
                return None, cal_name
            else:
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(cal_files):
                        cal_pack = load_calibration_pack(cal_files[idx])
                        return cal_pack, cal_files[idx]
                    else:
                        print("Invalid selection. Try again.")
                except ValueError:
                    print("Invalid input. Enter a number or 'N'.")
    else:
        print("\nNo existing calibration files found.")
        print("A new calibration will be created.")
        cal_name = input("\nEnter name for new calibration (e.g., 'setup_20jan', 'post_alignment'): ").strip()
        if not cal_name:
            cal_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Sanitize
        cal_name = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in cal_name).strip("_")
        if not cal_name:
            cal_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        return None, cal_name

def quick_health_check(det, rot_qwp, rot_anl, qwp_angle, anl_extinction, anl_parallel):
    results = {}
    safe_move_abs(rot_qwp, qwp_angle)

    safe_move_abs(rot_anl, anl_extinction)
    p_ext, _ = det.read_power_w_stable()

    safe_move_abs(rot_anl, anl_parallel)
    p_par, _ = det.read_power_w_stable()

    quad = (anl_extinction + 45.0) % 180.0
    safe_move_abs(rot_anl, quad); p_quad, _ = det.read_power_w_stable()

    ps = []
    for a in [quad - 4.0, quad, quad + 4.0]:
        safe_move_abs(rot_anl, a % 180.0)
        p, _ = det.read_power_w_stable()
        ps.append((a % 180.0, p))
    ps = sorted(ps)
    slope = (ps[2][1] - ps[0][1]) / (ps[2][0] - ps[0][0] + 1e-9)

    results["P_ext_W"] = p_ext
    results["P_par_W"] = p_par
    results["P_quad_W"] = p_quad
    results["ext_ratio_linear"] = (p_par / max(p_ext, 1e-15))
    results["ext_ratio_dB"] = 10.0 * np.log10(results["ext_ratio_linear"] + 1e-15)
    results["visibility_est"] = (p_par - p_ext) / max(p_par + p_ext, 1e-15)
    results["dP_dtheta_W_per_deg"] = slope
    print(f"[HEALTH] Extinction ~ {results['ext_ratio_dB']:.1f} dB; "
          f"Visibility ~ {results['visibility_est']:.3f}; "
          f"dP/dθ @ quadrature ~ {1e3*results['dP_dtheta_W_per_deg']:.3f} mW/deg")
    return results

# ======================== Position Verification ========================

def verify_all_positions(stage, rot_qwp, rot_anl, target_x, target_y,
                         target_qwp, target_anl):
    """Read back all hardware positions, return dict of actuals and errors."""
    x_act, y_act = stage.pos_mm
    qwp_act = rot_qwp.get_angle()
    anl_act = rot_anl.get_angle()

    if qwp_act is None: qwp_act = target_qwp
    if anl_act is None: anl_act = target_anl

    x_err = x_act - target_x
    y_err = y_act - target_y
    qwp_err = ((qwp_act - target_qwp + 540.0) % 360.0) - 180.0
    anl_err = ((anl_act - target_anl + 540.0) % 360.0) - 180.0

    return {
        'x_actual_mm': x_act,
        'y_actual_mm': y_act,
        'qwp_actual_deg': qwp_act,
        'anl_actual_deg': anl_act,
        'x_error_um': x_err * 1000.0,
        'y_error_um': y_err * 1000.0,
        'qwp_error_deg': qwp_err,
        'anl_error_deg': anl_err,
        'all_ok': (abs(x_err) <= STAGE_POSITION_TOL_MM and
                   abs(y_err) <= STAGE_POSITION_TOL_MM and
                   abs(qwp_err) <= STEP_VERIFY_TOL_DEG and
                   abs(anl_err) <= STEP_VERIFY_TOL_DEG),
    }

# ============================= BTO SCAN =============================

def scan_bto_region(det, rot_qwp, rot_anl, stage: XYStage, cal_pack: dict,
                    size_mm: float = SCAN_SIZE_MM,
                    step_mm: float = SCAN_STEP_MM,
                    center_x_mm: float = SCAN_CENTER_X_MM,
                    center_y_mm: float = SCAN_CENTER_Y_MM,
                    outdir: str = OUTDIR,
                    filename_prefix: str = "",
                    motion_logger=None):
    """
    Uses calibration angles to map a 25x25 mm region (from BTO_POL_CHIP_TEST.py):
      - P_ext (analyzer at extinction)
      - P_par (analyzer at parallel)
      - P_q1, P_q2 at two quadrature analyzer angles (ellipticity-like info)
    Produces:
      - P_par map
      - visibility map (P_par - P_ext)/(P_par + P_ext)
      - quadrature asymmetry map (P_q1 - P_q2)/(P_q1 + P_q2)

    Also saves a detailed log CSV with all measurements.
    """
    # Prepare filename prefix
    if filename_prefix and not filename_prefix.endswith("_"):
        filename_prefix = filename_prefix + "_"

    a_ext   = float(cal_pack["Null_Analyzer_deg"])            # use true null/extinction
    a_par   = float(cal_pack["Analyzer_parallel_deg_QWPout"]) # parallel from QWP-out fit
    qwp_opt = float(cal_pack["Null_QWP_deg"])
    a_q1    = (a_ext + 45.0) % 180.0
    a_q2    = (a_ext + 135.0) % 180.0

    print("\n=== BTO SCAN: 25x25 mm region ===")
    print(f"  size = {size_mm} mm, nominal step ≈ {step_mm:.3f} mm, "
          f"points per axis = {SCAN_POINTS_PER_AXIS}, centre = ({center_x_mm:.2f},{center_y_mm:.2f}) mm")
    print(f"  angles: A_ext={a_ext:.2f}°, A_par={a_par:.2f}°, A_q1={a_q1:.2f}°, A_q2={a_q2:.2f}°, QWP={qwp_opt:.2f}°")

    half = size_mm / 2.0
    xs = np.linspace(center_x_mm - half, center_x_mm + half, SCAN_POINTS_PER_AXIS)
    ys = np.linspace(center_y_mm - half, center_y_mm + half, SCAN_POINTS_PER_AXIS)

    safe_move_abs(rot_qwp, qwp_opt, motion_logger=motion_logger,
                  rot_name="ROT_QWP", move_type="CALIBRATION")
    if motion_logger:
        motion_logger.log_event("SCAN", "SCAN_START",
                                f"grid={SCAN_POINTS_PER_AXIS}x{SCAN_POINTS_PER_AXIS}, "
                                f"size={size_mm}mm, step={step_mm:.3f}mm")

    Nx, Ny = len(xs), len(ys)

    P_ext_grid = np.full((Ny, Nx), np.nan, dtype=float)
    P_par_grid = np.full((Ny, Nx), np.nan, dtype=float)
    P_q1_grid  = np.full((Ny, Nx), np.nan, dtype=float)
    P_q2_grid  = np.full((Ny, Nx), np.nan, dtype=float)

    rows = []

    # Timing statistics
    point_times = []
    scan_start_time = time.perf_counter()

    # Position error tracking for summary
    all_x_errors_um = []
    all_y_errors_um = []
    all_anl_errors_deg = []
    all_qwp_errors_deg = []

    # Log file for all measurements
    log_path = os.path.join(outdir, f"{filename_prefix}bto_scan_point_log.csv")
    log_f = open(log_path, "w", newline="")
    log_w = csv.writer(log_f)
    log_w.writerow([
        "timestamp_utc",
        "scan_elapsed_s",
        "point_index",
        "i", "j",
        "x_mm", "y_mm",
        "measurement",
        "analyzer_deg",
        "qwp_deg",
        "P_W",
        "V",
        "vdiv",
        "n_samples",
        "rsd_percent",
        "autoset_used",
        "range_changed",
    ])

    def log_meas(label, analyzer_deg, p_w, v, point_idx, i, j, x_mm, y_mm,
                 n_samples=0, rsd_percent=0.0, autoset_used=False, range_changed=False):
        log_w.writerow([
            datetime.utcnow().isoformat() + "Z",
            time.perf_counter() - scan_start_time,
            point_idx,
            i, j,
            float(x_mm), float(y_mm),
            label,
            float(analyzer_deg),
            float(qwp_opt),
            float(p_w),
            float(v),
            det.cur_vdiv,
            n_samples,
            f"{rsd_percent:.3f}",
            autoset_used,
            range_changed,
        ])

    # ---------- Live plot setup (P_par) ----------
    plt.ion()
    fig_live, ax_live = plt.subplots()
    im_live = ax_live.imshow(
        P_par_grid*1e3,
        origin='lower',
        extent=[xs.min(), xs.max(), ys.min(), ys.max()],
        aspect='equal'
    )
    cbar_live = plt.colorbar(im_live, ax=ax_live, label="P_par (mW)")
    ax_live.set_xlabel("x (mm)")
    ax_live.set_ylabel("y (mm)")
    ax_live.set_title("Live BTO scan: P_par (mW)")
    fig_live.tight_layout()

    # ---------- Scan loop with STABLE MEASUREMENT FRAMEWORK ----------
    # For P_par measurements, we use the full stable framework with:
    # - Extended settling time after rotation
    # - Smart auto-ranging (4x rule)
    # - Multiple samples with outlier removal
    # - Validation checks
    print("\n" + "="*60)
    print("STABLE MEASUREMENT FRAMEWORK")
    print("="*60)
    print(f"  Mode: {'P_par ONLY' if PPAR_ONLY_MODE else 'Full (ext/par/q1/q2)'}")
    print(f"  Rotation settle time: {ROTATION_SETTLE_TIME_S}s")
    print(f"  Smart range selection: 4x rule (signal < 4 * V/div)")
    print(f"  Sample collection: {STABLE_MEAS_MIN_SAMPLES}-{STABLE_MEAS_MAX_SAMPLES} samples")
    print(f"  Target RSD: {STABLE_MEAS_TARGET_RSD*100:.1f}%")
    print(f"  Validation re-measure: {PPAR_VALIDATION_REMEASURE}")
    print(f"  AUTOSET enabled: {USE_AUTOSET}")
    print("="*60 + "\n")

    try:
        for j, y_mm in enumerate(ys):
            for i, x_mm in enumerate(xs):
                point_start_time = time.perf_counter()
                point_idx = j * Nx + i

                print(f"\n{'='*50}")
                print(f"Point ({i+1}/{Nx}, {j+1}/{Ny}) at ({x_mm:.2f}, {y_mm:.2f}) mm")
                print(f"{'='*50}")

                stage.move_to_mm(float(x_mm), float(y_mm),
                                 motion_logger=motion_logger, move_type="SCAN")

                # Initialize values
                P_ext, V_ext = 0.0, 0.0
                P_q1, V_q1 = 0.0, 0.0
                P_q2, V_q2 = 0.0, 0.0

                if not PPAR_ONLY_MODE:
                    # 1) Extinction - standard measurement
                    print("  [EXT] Measuring extinction...")
                    safe_move_abs(rot_anl, a_ext, motion_logger=motion_logger,
                                  rot_name="ROT_ANL", move_type="SCAN")
                    P_ext, V_ext = det.read_power_w_stable()
                    log_meas("ext", a_ext, P_ext, V_ext, point_idx, i, j, x_mm, y_mm)

                # 2) Parallel - USE STABLE FRAMEWORK for maximum accuracy
                print("  [PAR] Measuring parallel with STABLE framework...")
                safe_move_abs(rot_anl, a_par, motion_logger=motion_logger,
                              rot_name="ROT_ANL", move_type="SCAN")

                # Verify ALL positions before measurement
                target_anl_now = a_par
                pos_check = verify_all_positions(stage, rot_qwp, rot_anl,
                                                 float(x_mm), float(y_mm),
                                                 qwp_opt, target_anl_now)
                if not pos_check['all_ok']:
                    # Retry specific out-of-tolerance axes
                    if abs(pos_check['x_error_um']) > STAGE_POSITION_TOL_MM * 1000:
                        stage.move_to_mm(float(x_mm), float(y_mm),
                                         motion_logger=motion_logger, move_type="VERIFY_RETRY")
                    if abs(pos_check['anl_error_deg']) > STEP_VERIFY_TOL_DEG:
                        safe_move_abs(rot_anl, target_anl_now, motion_logger=motion_logger,
                                      rot_name="ROT_ANL", move_type="VERIFY_RETRY")
                    if abs(pos_check['qwp_error_deg']) > STEP_VERIFY_TOL_DEG:
                        safe_move_abs(rot_qwp, qwp_opt, motion_logger=motion_logger,
                                      rot_name="ROT_QWP", move_type="VERIFY_RETRY")
                    # Re-check after retries
                    pos_check = verify_all_positions(stage, rot_qwp, rot_anl,
                                                     float(x_mm), float(y_mm),
                                                     qwp_opt, target_anl_now)

                # Use the full stable measurement framework
                P_par, V_par, diag_par = det.read_power_stable_ppar(rotation_just_done=True)

                # Validation check - re-measure if needed
                if PPAR_VALIDATION_REMEASURE and not diag_par['validation_passed']:
                    print("  [PAR] Validation failed, re-measuring...")
                    time.sleep(0.5)  # Brief extra settle
                    P_par2, V_par2, diag_par2 = det.read_power_stable_ppar(rotation_just_done=False)
                    # Use the better measurement (lower RSD)
                    if diag_par2['rsd'] < diag_par['rsd']:
                        P_par, V_par, diag_par = P_par2, V_par2, diag_par2
                        print(f"  [PAR] Re-measurement improved RSD: {diag_par['rsd']*100:.2f}%")

                log_meas("par", a_par, P_par, V_par, point_idx, i, j, x_mm, y_mm,
                        n_samples=diag_par['n_samples_used'],
                        rsd_percent=diag_par['rsd']*100,
                        autoset_used=diag_par['autoset_used'],
                        range_changed=diag_par['range_changed'])

                if not PPAR_ONLY_MODE:
                    # 3) Quadrature 1 - standard measurement
                    print("  [Q1] Measuring quadrature 1...")
                    safe_move_abs(rot_anl, a_q1, motion_logger=motion_logger,
                                  rot_name="ROT_ANL", move_type="SCAN")
                    P_q1, V_q1 = det.read_power_w_stable()
                    log_meas("q1", a_q1, P_q1, V_q1, point_idx, i, j, x_mm, y_mm)

                    # 4) Quadrature 2 - standard measurement
                    print("  [Q2] Measuring quadrature 2...")
                    safe_move_abs(rot_anl, a_q2, motion_logger=motion_logger,
                                  rot_name="ROT_ANL", move_type="SCAN")
                    P_q2, V_q2 = det.read_power_w_stable()
                    log_meas("q2", a_q2, P_q2, V_q2, point_idx, i, j, x_mm, y_mm)

                P_ext_grid[j, i] = P_ext
                P_par_grid[j, i] = P_par
                P_q1_grid[j, i]  = P_q1
                P_q2_grid[j, i]  = P_q2

                # Track position errors for summary
                all_x_errors_um.append(pos_check['x_error_um'])
                all_y_errors_um.append(pos_check['y_error_um'])
                all_anl_errors_deg.append(pos_check['anl_error_deg'])
                all_qwp_errors_deg.append(pos_check['qwp_error_deg'])

                rows.append((float(x_mm), float(y_mm),
                             P_ext, P_par, P_q1, P_q2,
                             V_ext, V_par, V_q1, V_q2,
                             pos_check['x_actual_mm'], pos_check['y_actual_mm'],
                             pos_check['anl_actual_deg'], pos_check['qwp_actual_deg'],
                             pos_check['x_error_um'], pos_check['y_error_um']))

                # Track timing
                point_time = time.perf_counter() - point_start_time
                point_times.append(point_time)
                avg_time = np.mean(point_times)
                remaining_points = (Nx * Ny) - (point_idx + 1)
                est_remaining_min = (remaining_points * avg_time) / 60.0

                # Summary for this point
                if PPAR_ONLY_MODE:
                    print(f"  [SUMMARY] P_par={P_par*1e3:.4f}mW ({P_par*1e6:.2f}µW), "
                          f"V={V_par*1000:.3f}mV, RSD={diag_par['rsd']*100:.2f}%, "
                          f"samples={diag_par['n_samples_used']}")
                else:
                    vis_point = (P_par - P_ext) / max(P_par + P_ext, 1e-15)
                    print(f"  [SUMMARY] P_par={P_par*1e3:.3f}mW, P_ext={P_ext*1e3:.3f}mW, "
                          f"Vis={vis_point:.3f}, RSD={diag_par['rsd']*100:.2f}%")
                print(f"[SCAN] ({i+1:02d}/{Nx}, {j+1:02d}/{Ny}) complete")

                # ---------- Live plot update ----------
                im_live.set_data(P_par_grid*1e3)
                valid = np.isfinite(P_par_grid)
                if np.any(valid):
                    current_min = np.nanmin(P_par_grid[valid])*1e3
                    current_max = np.nanmax(P_par_grid[valid])*1e3
                    if current_max > current_min:
                        im_live.set_clim(vmin=current_min, vmax=current_max)

                ax_live.set_title(f"Live BTO scan: P_par (mW)\n"
                                  f"Point ({i+1}/{Nx}, {j+1}/{Ny}) at x={x_mm:.2f} mm, y={y_mm:.2f} mm")
                fig_live.canvas.draw()
                fig_live.canvas.flush_events()
                time.sleep(0.01)
    finally:
        log_f.close()

    plt.ioff()

    # Print timing summary
    if point_times:
        total_time_min = sum(point_times) / 60.0
        avg_time_per_point = np.mean(point_times)
        print(f"\n[SCAN COMPLETE]")
        print(f"  Total scan time: {total_time_min:.1f} minutes ({sum(point_times):.0f} seconds)")
        print(f"  Average per point: {avg_time_per_point:.1f} seconds")
        print(f"  Total points: {len(point_times)}")

    scan_csv = os.path.join(outdir, f"{filename_prefix}bto_scan_25x25mm_10x10.csv")
    save_csv(scan_csv,
             ["x_mm","y_mm",
              "P_ext_W","P_par_W","P_q1_W","P_q2_W",
              "V_ext","V_par","V_q1","V_q2",
              "x_actual_mm","y_actual_mm","anl_actual_deg","qwp_actual_deg",
              "x_error_um","y_error_um"],
             rows)
    print(f"[SCAN] Saved CSV -> {os.path.abspath(scan_csv)}")
    print(f"[SCAN] Saved detailed log -> {os.path.abspath(log_path)}")

    # Print positioning accuracy summary
    if all_x_errors_um:
        ax = np.array(all_x_errors_um)
        ay = np.array(all_y_errors_um)
        aa = np.array(all_anl_errors_deg)
        aq = np.array(all_qwp_errors_deg)
        print(f"\n{'='*50}")
        print("POSITIONING ACCURACY SUMMARY")
        print(f"{'='*50}")
        print(f"  Stage X:   max={np.max(np.abs(ax)):.1f}um, mean={np.mean(np.abs(ax)):.1f}um, std={np.std(ax):.1f}um")
        print(f"  Stage Y:   max={np.max(np.abs(ay)):.1f}um, mean={np.mean(np.abs(ay)):.1f}um, std={np.std(ay):.1f}um")
        print(f"  Analyzer:  max={np.max(np.abs(aa)):.4f}deg, mean={np.mean(np.abs(aa)):.4f}deg, std={np.std(aa):.4f}deg")
        print(f"  QWP:       max={np.max(np.abs(aq)):.4f}deg, mean={np.mean(np.abs(aq)):.4f}deg, std={np.std(aq):.4f}deg")
        print(f"{'='*50}")

    if motion_logger:
        motion_logger.log_event("SCAN", "SCAN_END",
                                f"total_points={len(point_times)}, "
                                f"total_time={sum(point_times):.1f}s")

    vis_grid = (P_par_grid - P_ext_grid) / np.maximum(P_par_grid + P_ext_grid, 1e-15)
    quad_asym_grid = (P_q1_grid - P_q2_grid) / np.maximum(P_q1_grid + P_q2_grid, 1e-15)

    # ---------- Final static plots ----------
    plt.figure()
    plt.imshow(P_par_grid*1e3, origin='lower',
               extent=[xs.min(), xs.max(),
                       ys.min(), ys.max()],
               aspect='equal')
    plt.colorbar(label="P_par (mW)")
    plt.xlabel("x (mm)"); plt.ylabel("y (mm)")
    plt.title("BTO scan (25x25 mm, 10×10): parallel intensity P_par")
    plt.savefig(os.path.join(outdir, f"{filename_prefix}bto_scan25x25_10x10_Ppar.png"), dpi=150)

    plt.figure()
    plt.imshow(vis_grid, origin='lower',
               extent=[xs.min(), xs.max(),
                       ys.min(), ys.max()],
               aspect='equal', vmin=0.0, vmax=1.0)
    plt.colorbar(label="(P_par - P_ext)/(P_par + P_ext)")
    plt.xlabel("x (mm)"); plt.ylabel("y (mm)")
    plt.title("BTO scan (25x25 mm, 10×10): polarization visibility")
    plt.savefig(os.path.join(outdir, f"{filename_prefix}bto_scan25x25_10x10_visibility.png"), dpi=150)

    plt.figure()
    plt.imshow(quad_asym_grid, origin='lower',
               extent=[xs.min(), xs.max(),
                       ys.min(), ys.max()],
               aspect='equal', vmin=-1.0, vmax=1.0)
    plt.colorbar(label="(P_q1 - P_q2)/(P_q1 + P_q2)")
    plt.xlabel("x (mm)"); plt.ylabel("y (mm)")
    plt.title("BTO scan (25x25 mm, 10×10): quadrature asymmetry (ellipticity-like)")
    plt.savefig(os.path.join(outdir, f"{filename_prefix}bto_scan25x25_10x10_quadrature_asym.png"), dpi=150)

    print("[SCAN] Saved BTO scan plots.")

# ================================ Main ================================

def main():
    print("="*60)
    print("BTO POLARIZATION SCAN - MEASUREMENT SETUP")
    print("="*60)

    # Ask for measurement folder name at the very start
    scan_name = input("\nEnter folder name for this measurement (e.g., 'chip1_50V_scan1', 'BTO_test01'): ").strip()
    if not scan_name:
        scan_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"No name entered, using timestamp: {scan_name}")
    else:
        # Sanitize the name
        scan_name = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in scan_name).strip("_")
        if not scan_name:
            scan_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"Measurement folder: {scan_name}")

    # Create measurement-specific output directory
    measurement_outdir = os.path.join(OUTDIR, scan_name)
    os.makedirs(measurement_outdir, exist_ok=True)

    print(f"\nAll files will be saved to:")
    print(f"  {os.path.abspath(measurement_outdir)}")
    print("="*60)

    # Rotators
    print("\nConnecting rotators...")
    rot_hwp = ElliptecRotator(port=PORT_HWP, address=ADDR_HWP, verbose=False, settle_time=MOVE_SETTLE_S)
    rot_anl = ElliptecRotator(port=PORT_ANL, address=ADDR_ANL, verbose=False, settle_time=MOVE_SETTLE_S)
    rot_qwp = ElliptecRotator(port=PORT_QWP, address=ADDR_QWP, verbose=False, settle_time=MOVE_SETTLE_S)

    print("Homing rotators...")
    for rot, name in [(rot_hwp,"HWP"), (rot_anl,"ANL"), (rot_qwp,"QWP")]:
        rot.home(direction=0, settle_s=3.0); rot.tare()
        print(f"[{name}] Home @ {rot.get_angle():.2f}°")

    # Detector
    det = DetectorTekTBS(SCOPE_VISA, SCOPE_SOURCE); det.connect()

    # Stage: Kinesis style
    safe_print("\n[STAGE] Building device list.")
    DeviceManagerCLI.BuildDeviceList()

    safe_print("[STAGE] Initializing X/Y KCubeStepper.")
    device_x = initialize_device(STAGE_SERIAL_X)
    device_y = initialize_device(STAGE_SERIAL_Y)

    stage = XYStage(device_x, device_y, motor_center=STAGE_CENTER_MM)
    stage.home(STAGE_SERIAL_X, STAGE_SERIAL_Y)

    # Move stage to global centre so beam is clear
    print("\n[STAGE] Moving to optical centre (global 0,0) for calibration.")
    stage.move_to_mm(0.0, 0.0)
    print(f"[STAGE] Centre position (should be beam path). Current global: {stage.pos_mm}")

    # Laser (optional)
    laser = None
    if USE_LASER and KLS1550 is not None:
        try:
            serial = KLS1550.find_first() if KLS_SERIAL is None else KLS_SERIAL
            if serial:
                laser = KLS1550(serial); laser.connect()
                laser.set_power_absolute(KLS_POWER_MW); laser.on()
                print(f"[LASER] ON @ {KLS_POWER_MW} mW; stabilizing {LASER_STABILIZE_S:.1f}s")
                time.sleep(LASER_STABILIZE_S)
            else:
                print("[LASER] Not found; continuing without laser control.")
        except Exception as e:
            print("[LASER] WARN:", e)

    # -----------------------------------------------------------------
    # CHOICE: load existing calibration JSON, or do full calibration?
    # -----------------------------------------------------------------
    cal_pack_result, cal_identifier = select_or_create_calibration(OUTDIR)

    if cal_pack_result is not None:
        # --------- USING EXISTING CALIBRATION ---------
        cal_pack = cal_pack_result
        print("\n[CAL] Using existing calibration values:")
        print(f"  HWP_fixed_deg                  = {cal_pack['HWP_fixed_deg']:.2f}°")
        print(f"  Analyzer_extinction_deg_QWPout = {cal_pack['Analyzer_extinction_deg_QWPout']:.2f}°")
        print(f"  Analyzer_parallel_deg_QWPout   = {cal_pack['Analyzer_parallel_deg_QWPout']:.2f}°")
        print(f"  Null_QWP_deg                   = {cal_pack['Null_QWP_deg']:.2f}°")
        print(f"  Null_Analyzer_deg              = {cal_pack['Null_Analyzer_deg']:.2f}°")

        # Put optics into known configuration
        safe_move_abs(rot_hwp, cal_pack["HWP_fixed_deg"])
        safe_move_abs(rot_qwp, cal_pack["Null_QWP_deg"])
        safe_move_abs(rot_anl, cal_pack["Null_Analyzer_deg"])

        # Optional quick health check to make sure extinction hasn't drifted too far
        print("\n=== QUICK HEALTH CHECK (extinction / visibility / slope) ===")
        _ = quick_health_check(det, rot_qwp, rot_anl,
                               qwp_angle=cal_pack["Null_QWP_deg"],
                               anl_extinction=cal_pack["Null_Analyzer_deg"],
                               anl_parallel=cal_pack["Analyzer_parallel_deg_QWPout"])

        # --------- INSERT BTO SAMPLE AND SCAN ---------
        print("\n=== INSERT BTO SAMPLE ===")
        print("Stage is at global (0,0) – this is the beam centre.")
        input("Now insert/mount the BTO thin film so that the region of interest "
              "is centred at this beam position. When ready, press Enter to start scan...")

        # Initialize motion logger
        motion_logger = None
        if LOG_MOTOR_POSITIONS:
            motion_log_path = os.path.join(measurement_outdir,
                                           f"{scan_name}_motion_log.csv")
            motion_logger = MotionLogger(motion_log_path, scan_name=scan_name,
                                         cal_file=str(cal_identifier))
            motion_logger.log_event("SCAN", "CALIBRATION_LOADED",
                                    f"file={cal_identifier}")

        # Set precision velocity for scanning
        set_stage_velocity(device_x, STAGE_PRECISION_VELOCITY, STAGE_PRECISION_ACCEL)
        set_stage_velocity(device_y, STAGE_PRECISION_VELOCITY, STAGE_PRECISION_ACCEL)
        safe_print(f"[STAGE] Precision velocity set: {STAGE_PRECISION_VELOCITY} mm/s, "
                   f"{STAGE_PRECISION_ACCEL} mm/s²")
        if motion_logger:
            motion_logger.log_event("STAGE_X", "VELOCITY_CHANGE",
                                    f"vel={STAGE_PRECISION_VELOCITY}, accel={STAGE_PRECISION_ACCEL}")
            motion_logger.log_event("STAGE_Y", "VELOCITY_CHANGE",
                                    f"vel={STAGE_PRECISION_VELOCITY}, accel={STAGE_PRECISION_ACCEL}")

        try:
            if ENABLE_BTO_SCAN:
                scan_bto_region(det, rot_qwp, rot_anl, stage, cal_pack,
                                size_mm=SCAN_SIZE_MM,
                                step_mm=SCAN_STEP_MM,
                                center_x_mm=SCAN_CENTER_X_MM,
                                center_y_mm=SCAN_CENTER_Y_MM,
                                outdir=measurement_outdir,
                                filename_prefix=scan_name,
                                motion_logger=motion_logger)
        finally:
            # Motion logger summary and cleanup (always runs, even on crash)
            if motion_logger:
                try:
                    motion_logger.print_summary()
                except Exception:
                    pass
                motion_logger.close()

        print(f"\n{'='*60}")
        print(f"SCAN COMPLETE!")
        print(f"{'='*60}")
        print(f"Measurement folder: {scan_name}")
        print(f"Output directory: {os.path.abspath(measurement_outdir)}")
        print(f"\nFiles saved:")
        print(f"  - {scan_name}_bto_scan_25x25mm_10x10.csv (raw data)")
        print(f"  - {scan_name}_bto_scan_point_log.csv (per-measurement log with V/div)")
        if LOG_MOTOR_POSITIONS:
            print(f"  - {scan_name}_motion_log.csv (comprehensive motion log)")
        print(f"  - {scan_name}_bto_scan25x25_10x10_Ppar.png (parallel power map)")
        print(f"  - {scan_name}_bto_scan25x25_10x10_visibility.png (polarization visibility)")
        print(f"  - {scan_name}_bto_scan25x25_10x10_quadrature_asym.png (ellipticity-like)")
        print(f"{'='*60}")

        # Show plots non-blocking, then wait for user
        plt.show(block=False)
        plt.pause(0.5)  # Give plots time to render
        input("\nPlots are displayed. Press Enter to close plots and cleanup hardware...")
        plt.close('all')  # Close all plot windows

        # Cleanup
        print("\n[CLEANUP] Shutting down hardware...")
        try:
            if USE_LASER and (laser is not None): laser.off(); laser.close()
            print("  - Laser OFF")
        except: pass
        try:
            det.close()
            print("  - Detector closed")
        except: pass
        for rot in (rot_qwp, rot_hwp, rot_anl):
            try: rot.close()
            except: pass
        print("  - Rotators closed")

        # Restore coarse velocity and return stage to center
        try:
            set_stage_velocity(device_x, 2.0, 2.0)
            set_stage_velocity(device_y, 2.0, 2.0)
            print("  - Moving stage to center (0, 0)...")
            stage.move_to_mm(0.0, 0.0)
            print("  - Stage at center")
        except Exception as e:
            print(f"  - Stage center move failed: {e}")

        try:
            device_x.StopPolling(); device_x.Disconnect()
        except: pass
        try:
            device_y.StopPolling(); device_y.Disconnect()
        except: pass
        print("  - Stage disconnected")
        print("[CLEANUP] Done. Script finished.")

        return  # END: existing-calibration path

    # -----------------------------------------------------------------
    # FULL CALIBRATION PATH (as before)
    # -----------------------------------------------------------------

    # ======================= STEP 1: QWP REMOVED =======================
    print("\n=== STEP 1: QWP REMOVED → Analyzer sweep (+ optional HWP) ===")
    input("Ensure BTO SAMPLE IS REMOVED and QWP is REMOVED from the beam, "
          "stage is at centre (0,0). Then press Enter...")

    safe_move_abs(rot_hwp, CONFIRM_HWP_FIXED)
    print(f"[HWP] {CONFIRM_HWP_FIXED:.2f}°")

    anl_angles = np.arange(ANL_SWEEP_RANGE[0], ANL_SWEEP_RANGE[1] + 1e-9, ANL_SWEEP_STEP)
    data_anl = sweep_angles(rot_anl, anl_angles, det.read_power_w_stable)
    save_csv(os.path.join(OUTDIR, "analyzer_sweep_QWPout.csv"), ["Analyzer_deg","Power_W","Volt"], data_anl)

    fit_anl = fit_cos2(data_anl[:,0], data_anl[:,1])
    anl_theta_off = fit_anl["theta_off_deg"] % 180.0
    Pmax_fit = fit_anl["Pmax"]; Pbg_fit = fit_anl["Pbg"]

    i_max = int(np.argmax(data_anl[:,1])); i_min = int(np.argmin(data_anl[:,1]))
    anl_coarse_max = float(data_anl[i_max,0]); anl_coarse_min = float(data_anl[i_min,0])
    a_ref_min, p_ref_min = refine_extremum_1d(rot_anl, anl_coarse_min, REFINE_WINDOW, REFINE_STEP, 'min', det.read_power_w_stable)
    a_ref_max, p_ref_max = refine_extremum_1d(rot_anl, anl_coarse_max, REFINE_WINDOW, REFINE_STEP, 'max', det.read_power_w_stable)

    print(f"[ANALYZER|QWP-out] MIN @ {a_ref_min:.2f}° : {p_ref_min:.3e} W")
    print(f"[ANALYZER|QWP-out] MAX @ {a_ref_max:.2f}° : {p_ref_max:.3e} W")
    print(f"[ANALYZER|QWP-out] θ_off (fit) = {anl_theta_off:.2f}°, Pbg = {Pbg_fit:.3e} W")

    plt.figure()
    tt = np.linspace(0, 180, 721)
    model = fit_anl["Pmax"]*(np.cos(np.deg2rad(tt - anl_theta_off))**2) + Pbg_fit
    plt.plot(data_anl[:,0], data_anl[:,1]*1e3, 'o-', ms=3, label="data")
    plt.plot(tt, model*1e3, '-', lw=1.4, label="cos² fit")
    plt.axvline(a_ref_min, ls='--', label=f"min {a_ref_min:.2f}°")
    plt.axvline(a_ref_max, ls='--', label=f"max {a_ref_max:.2f}°")
    plt.title(f"Analyzer sweep (QWP removed) @ HWP={CONFIRM_HWP_FIXED:.2f}°\n"
              f"Pbg={Pbg_fit:.2e} W, θ_off={anl_theta_off:.2f}°")
    plt.xlabel("Analyzer angle (deg)"); plt.ylabel("Detected power (mW)")
    plt.grid(True); plt.legend()
    plt.savefig(os.path.join(OUTDIR, "analyzer_sweep_QWPout.png"), dpi=150)

    hwp_angles = np.arange(HWP_SWEEP_RANGE[0], HWP_SWEEP_RANGE[1] + 1e-9, HWP_SWEEP_STEP)
    safe_move_abs(rot_anl, a_ref_max)
    data_hwp = sweep_angles(rot_hwp, hwp_angles, det.read_power_w_stable)
    save_csv(os.path.join(OUTDIR, "hwp_sweep_QWPout.csv"), ["HWP_deg","Power_W","Volt"], data_hwp)
    plt.figure(); plt.plot(data_hwp[:,0], data_hwp[:,1]*1e3, 'o-', ms=3)
    plt.title(f"HWP sweep (QWP removed) @ Analyzer={a_ref_max:.2f}°"); plt.grid(True)
    plt.xlabel("HWP angle (deg)"); plt.ylabel("Detected power (mW)")
    plt.savefig(os.path.join(OUTDIR, "hwp_sweep_QWPout.png"), dpi=150)

    # ======================= STEP 2: QWP INSERTED =======================
    print("\n=== STEP 2: INSERT QWP → QWP sweep & fit ===")
    input("Insert the QWP (before analyzer) BUT STILL NO BTO SAMPLE, "
          "beam through air, then press Enter...")
    safe_move_abs(rot_anl, a_ref_max)

    qwp_angles = np.arange(QWP_SWEEP_RANGE[0], QWP_SWEEP_RANGE[1] + 1e-9, QWP_SWEEP_STEP)
    data_qwp = sweep_angles(rot_qwp, qwp_angles, det.read_power_w_stable, period=180.0)
    save_csv(os.path.join(OUTDIR, "qwp_sweep_QWPin.csv"), ["QWP_deg","Power_W","Volt"], data_qwp)

    fit_q = fit_qwp_cos4(data_qwp[:,0], data_qwp[:,1])
    gamma0 = fit_q["gamma0_deg"]; Imax = fit_q["Imax"]; Imin = fit_q["Imin"]
    vis = fit_q["visibility"]; delta_deg = fit_q["delta_est_deg"]; r = Imin / max(1e-12, Imax)

    print("\n[QWP calibration] (QWP in, no sample)")
    print(f"  γ0 (fast-axis zero): {gamma0:.2f}°")
    print(f"  δ (retardance)     : {delta_deg:.2f}°   (ideal 90°)")
    print(f"  Imax / Imin        : {Imax:.3e} W / {Imin:.3e} W (r={r:.3f}), visibility={vis:.3f}")

    gfit = np.linspace(0, 180.0, 721); g = np.deg2rad(gfit)
    Ifit = fit_q["A"] + fit_q["B"]*np.cos(4*g) + fit_q["C"]*np.sin(4*g)
    plt.figure()
    plt.plot(data_qwp[:,0], data_qwp[:,1]*1e3, 'o', ms=3, label="data")
    plt.plot(gfit, Ifit*1e3, '-', label="fit")
    plt.axvline(gamma0, ls='--', label=f"γ0 = {gamma0:.2f}°")
    plt.title(f"QWP sweep (QWP in) @ Analyzer={a_ref_max:.2f}°")
    plt.xlabel("QWP angle (deg)"); plt.ylabel("Detected power (mW)")
    plt.grid(True); plt.legend()
    plt.savefig(os.path.join(OUTDIR, "qwp_sweep_fit.png"), dpi=150)

    # ======================= STEP 3: NULL (QWP + ANL) =======================
    print("\n=== STEP 3: QWP + Analyzer nulling (golden-section 2D) ===")
    null_log = os.path.join(OUTDIR, "nulling_path.csv")
    q_null, a_null, p_null = null_qwp_analyzer(det, rot_qwp, rot_anl,
                                               start_qwp_deg=gamma0,
                                               start_anl_deg=a_ref_min,
                                               init_span_qwp=20.0,
                                               init_span_anl=20.0,
                                               cycles=3, tol_deg=1.2,
                                               log_path=null_log)
    print(f"[NULL] QWP_null={q_null:.2f}°, Analyzer_null={a_null:.2f}°, Pmin={p_null:.3e} W")

    ext_ratio_linear = (Pmax_fit / max(p_null, 1e-15))
    ext_ratio_dB = 10.0 * np.log10(ext_ratio_linear + 1e-15)
    print(f"[EXTINCTION] Using P_max(QWP-out) vs P_null(QWP+ANL): ~{ext_ratio_dB:.1f} dB")

    null_landscape_scan(det, rot_qwp, rot_anl,
                        q_center=q_null, a_center=a_null,
                        span_q=12.0, span_a=12.0,
                        step_deg=2.0,
                        outdir=OUTDIR)

    cal_pack = {
        "HWP_fixed_deg": float(CONFIRM_HWP_FIXED),
        "Analyzer_extinction_deg_QWPout": float(a_ref_min),
        "Analyzer_parallel_deg_QWPout": float(a_ref_max),
        "Analyzer_theta_off_fit_deg_QWPout": float(anl_theta_off),
        "Analyzer_Pbg_fit_W_QWPout": float(Pbg_fit),

        "QWP_gamma0_fit_deg": float(gamma0),
        "QWP_delta_deg": float(delta_deg),
        "QWP_visibility": float(vis),
        "QWP_fit_coeffs": {"A": float(fit_q["A"]), "B": float(fit_q["B"]), "C": float(fit_q["C"])},

        "Null_QWP_deg": float(q_null),
        "Null_Analyzer_deg": float(a_null),
        "Null_Pmin_W": float(p_null),
        "Extinction_dB_vs_QWPoutMax": float(ext_ratio_dB),

        "Detector": {
            "PDA_gain_dB": PDA_GAIN_DB, "PDA_load": PDA_LOAD,
            "A_per_W": RESP_A_PER_W, "V_to_W": V_TO_W,
            "Dark_offset_V": DARK_VOLTAGE_OFFSET
        },
        "Scope": {
            "Vdiv_levels": VDIV_LEVELS, "Thresh_down": THRESH_DOWN, "Thresh_up": THRESH_UP
        },
        "Laser": {
            "Power_mW": KLS_POWER_MW,
            "Wavelength_nm": 1550.0
        }
    }
    with open(os.path.join(OUTDIR, "null_summary.txt"), "w") as f:
        f.write(f"QWP_null = {q_null:.2f} deg\nAnalyzer_null = {a_null:.2f} deg\nPmin = {p_null:.3e} W\n"
                f"Extinction_dB_vs_QWPoutMax = {ext_ratio_dB:.2f} dB\n")

    # Save calibration with user-specified name
    # cal_identifier contains the name the user entered for the new calibration
    cal_file_path = os.path.join(OUTDIR, f"{cal_identifier}_calibration.json")
    cal_pack["calibration_name"] = cal_identifier
    save_calibration_pack(cal_pack, path=cal_file_path)
    print(f"\n[CAL] Calibration saved as: {cal_identifier}_calibration.json")

    # ======================= QUICK HEALTH CHECK =======================
    print("\n=== QUICK HEALTH CHECK (extinction / visibility / slope) ===")
    _ = quick_health_check(det, rot_qwp, rot_anl,
                           qwp_angle=cal_pack["Null_QWP_deg"],
                           anl_extinction=cal_pack["Null_Analyzer_deg"],
                           anl_parallel=cal_pack["Analyzer_parallel_deg_QWPout"])

    # ======================= BTO SAMPLE INSERTION ======================
    print("\n=== INSERT BTO SAMPLE ===")
    print("Stage is at global (0,0) – this is the beam centre.")
    input("Now insert/mount the BTO thin film so that the region of interest "
          "is centred at this beam position. When ready, press Enter to start scan...")

    # Initialize motion logger
    motion_logger = None
    if LOG_MOTOR_POSITIONS:
        motion_log_path = os.path.join(measurement_outdir,
                                       f"{scan_name}_motion_log.csv")
        motion_logger = MotionLogger(motion_log_path, scan_name=scan_name,
                                     cal_file=str(cal_identifier))
        motion_logger.log_event("SCAN", "CALIBRATION_LOADED",
                                f"file={cal_identifier}")

    # Set precision velocity for scanning
    set_stage_velocity(device_x, STAGE_PRECISION_VELOCITY, STAGE_PRECISION_ACCEL)
    set_stage_velocity(device_y, STAGE_PRECISION_VELOCITY, STAGE_PRECISION_ACCEL)
    safe_print(f"[STAGE] Precision velocity set: {STAGE_PRECISION_VELOCITY} mm/s, "
               f"{STAGE_PRECISION_ACCEL} mm/s²")
    if motion_logger:
        motion_logger.log_event("STAGE_X", "VELOCITY_CHANGE",
                                f"vel={STAGE_PRECISION_VELOCITY}, accel={STAGE_PRECISION_ACCEL}")
        motion_logger.log_event("STAGE_Y", "VELOCITY_CHANGE",
                                f"vel={STAGE_PRECISION_VELOCITY}, accel={STAGE_PRECISION_ACCEL}")

    # ======================= BTO SCAN =======================
    try:
        if ENABLE_BTO_SCAN:
            scan_bto_region(det, rot_qwp, rot_anl, stage, cal_pack,
                            size_mm=SCAN_SIZE_MM,
                            step_mm=SCAN_STEP_MM,
                            center_x_mm=SCAN_CENTER_X_MM,
                            center_y_mm=SCAN_CENTER_Y_MM,
                            outdir=measurement_outdir,
                            filename_prefix=scan_name,
                            motion_logger=motion_logger)
    finally:
        # Motion logger summary and cleanup (always runs, even on crash)
        if motion_logger:
            try:
                motion_logger.print_summary()
            except Exception:
                pass
            motion_logger.close()

    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE!")
    print(f"{'='*60}")
    print(f"Measurement folder: {scan_name}")
    print(f"Output directory: {os.path.abspath(measurement_outdir)}")
    print(f"\nFiles saved:")
    print(f"  - {scan_name}_bto_scan_25x25mm_10x10.csv (raw data)")
    print(f"  - {scan_name}_bto_scan_point_log.csv (per-measurement log with V/div)")
    if LOG_MOTOR_POSITIONS:
        print(f"  - {scan_name}_motion_log.csv (comprehensive motion log)")
    print(f"  - {scan_name}_bto_scan25x25_10x10_Ppar.png (parallel power map)")
    print(f"  - {scan_name}_bto_scan25x25_10x10_visibility.png (polarization visibility)")
    print(f"  - {scan_name}_bto_scan25x25_10x10_quadrature_asym.png (ellipticity-like)")
    print(f"{'='*60}")

    # Show plots non-blocking, then wait for user
    plt.show(block=False)
    plt.pause(0.5)  # Give plots time to render
    input("\nPlots are displayed. Press Enter to close plots and cleanup hardware...")
    plt.close('all')  # Close all plot windows

    # Cleanup
    print("\n[CLEANUP] Shutting down hardware...")
    try:
        if USE_LASER and (laser is not None): laser.off(); laser.close()
        print("  - Laser OFF")
    except: pass
    try:
        det.close()
        print("  - Detector closed")
    except: pass
    for rot in (rot_qwp, rot_hwp, rot_anl):
        try: rot.close()
        except: pass
    print("  - Rotators closed")

    # Restore coarse velocity and return stage to center
    try:
        set_stage_velocity(device_x, 2.0, 2.0)
        set_stage_velocity(device_y, 2.0, 2.0)
        print("  - Moving stage to center (0, 0)...")
        stage.move_to_mm(0.0, 0.0)
        print("  - Stage at center")
    except Exception as e:
        print(f"  - Stage center move failed: {e}")

    try:
        device_x.StopPolling(); device_x.Disconnect()
    except: pass
    try:
        device_y.StopPolling(); device_y.Disconnect()
    except: pass
    print("  - Stage disconnected")
    print("[CLEANUP] Done. Script finished.")

if __name__ == "__main__":
    main()
