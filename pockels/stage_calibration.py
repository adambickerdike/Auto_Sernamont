#!/usr/bin/env python3
"""
stage_calibration.py
====================
Interactive stage position calibration tool for the scan grid.

Collects comprehensive ML training data at each pixel:
  - Programmed (nominal) position
  - Arrived position (where motor actually lands)
  - Calibrated position (after manual alignment to max transmission)
  - Oscilloscope intensity at arrival AND after alignment (with smart V/div)
  - Camera images at arrival AND after alignment
  - All position errors and corrections in micrometers

Uses plain input() for maximum compatibility across all terminals.

Controls (type letter + Enter):
  Enter or c  -- Capture current position + intensity + image
  a           -- Auto-align: coarse scan + fine scan (thorough, ~60s)
  gs          -- Golden section search (fast, ~12s, needs signal nearby)
  cv          -- CV-guided: camera estimates gap position, then tight GS (~5s)
  fa          -- Full auto: align + capture all remaining pixels (uses CV+GS)
  m           -- Calibrate pixel-to-stage transform (one-time, needed for 'cv')
  e           -- Enter coordinates manually from controller display
  s           -- Skip this pixel (keeps nominal position)
  r           -- Redo / go back to previous pixel
  g           -- Go to a specific pixel number
  p           -- Show progress summary
  f           -- Refresh / re-read current position + intensity
  i           -- Read intensity only (for monitoring while adjusting)
  q           -- Quit and save progress (allows resume later)

Output (stage_calibration/ directory):
  pixel_positions.json  -- Primary calibration file (auto-saved after every capture)
  pixel_positions.csv   -- Human-readable CSV version
  images/               -- Camera frames (arrival + calibrated per pixel)

Per-pixel voltage application (NEW):
  When the Arduino switch matrix (arduino_switch_matrix.py + the firmware
  in Arduino_Manually_Switching.cc) is reachable on ARDUINO_PORT, each
  calibrated peak triggers a switch-on of the mapped electrode channel
  from PIXEL_TO_PIN, a VOLTAGE_DWELL_S hold, then
  switch-off before moving to the next pixel. Voltage-on scope readings
  are recorded in voltage_on_* columns. See:
    STAGE_CALIBRATION_VOLTAGE_INTEGRATION.md

  To disable, set ARDUINO_ENABLED = False below.

IMPORTANT: Must be run from Windows Python (not WSL) -- requires .NET/Kinesis.
"""

import os, sys, csv, json, re, time, threading, queue
from datetime import datetime
from decimal import Decimal as PyDecimal, getcontext
import numpy as np
import pyvisa

try:
    import matplotlib
    matplotlib.use('Agg')  # non-interactive backend for saving only
    import matplotlib.pyplot as plt
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False

try:
    import cv2
    HAVE_CAMERA = True
except ImportError:
    HAVE_CAMERA = False

# Arduino 100-channel switching matrix (optional). When present, voltage is
# applied at the calibrated peak of each pixel for VOLTAGE_DWELL_S seconds
# before moving on to the next pixel. See STAGE_CALIBRATION_VOLTAGE_INTEGRATION.md.
try:
    from arduino_switch_matrix import ArduinoSwitchMatrix
    HAVE_SWITCH_MATRIX = True
except ImportError:
    HAVE_SWITCH_MATRIX = False

getcontext().prec = 10

# ====================== .NET / Kinesis initialisation ======================

import clr_loader
try:
    runtime = clr_loader.get_netfx()
    from pythonnet import set_runtime
    set_runtime(runtime)
except RuntimeError:
    pass

import clr

clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.KCube.StepperMotorCLI.dll")

from Thorlabs.MotionControl.DeviceManagerCLI import *
from Thorlabs.MotionControl.GenericMotorCLI import *
from Thorlabs.MotionControl.GenericMotorCLI.Settings import KCubeMMISettings
try:
    from Thorlabs.MotionControl.GenericMotorCLI.Settings import MotorJogModes
except ImportError:
    MotorJogModes = None
from Thorlabs.MotionControl.KCube.StepperMotorCLI import *
from System import Decimal as NetDecimal

# =============================== CONFIG ===================================

STAGE_SERIAL_X = "26006987"
STAGE_SERIAL_Y = "26007025"

STAGE_TRAVEL_MM    = 25.0
STAGE_CENTER_MM    = PyDecimal('12.5')
STAGE_MIN_MM       = -0.3
STAGE_MAX_MM       = 25.3
HARD_MIN_MM        = 0.0
HARD_MAX_MM        = STAGE_TRAVEL_MM
ALLOW_OVERTRAVEL   = True
OVERTRAVEL_TIMEOUT_S = 2.0
OVERTRAVEL_POLL_S    = 0.05

SCAN_SIZE_MM         = 25.0
SCAN_POINTS_PER_AXIS = 10
SCAN_CENTER_X_MM     = 0.0
SCAN_CENTER_Y_MM     = 0.0

OUTDIR_ROOT = "stage_calibration"      # parent dir; never changes at runtime
OUTDIR      = OUTDIR_ROOT              # active run dir; set by set_run_dir()
IMGDIR      = os.path.join(OUTDIR, "images")
ALIGNDIR    = os.path.join(OUTDIR, "alignment_logs")
JSON_FILE   = os.path.join(OUTDIR, "pixel_positions.json")
CSV_FILE    = os.path.join(OUTDIR, "pixel_positions.csv")

# ── Auto-alignment config ──
# pixel_stage_calib.json stays at the root so it is shared across runs
# (it's a camera-to-stage mapping that doesn't change between scans).
ALIGN_CALIB_FILE              = os.path.join(OUTDIR_ROOT, "pixel_stage_calib.json")
ALIGN_CALIB_STEP_MM           = 0.050    # Stage step for pixel-to-stage calibration (50um)
# Coarse scan: wide sweep to find the signal
ALIGN_COARSE_RANGE_UM         = 400.0    # Total coarse search range per axis (um)
ALIGN_COARSE_STEP_UM          = 5.0      # Coarse step size (um)
# Fine scan: narrow sweep to pinpoint the peak
ALIGN_FINE_RANGE_UM           = 10.0     # Total fine search range per axis (um)
ALIGN_FINE_STEP_UM            = 0.5      # Fine step size (um)
# Timing per measurement point
ALIGN_SETTLE_S                = 0.5      # Settle time after each move (seconds)
ALIGN_AVG_READS               = 5        # Scope reads averaged per point
ALIGN_COARSE_MAX_CORRECTION_MM = 0.5     # Reject CV corrections larger than this (mm)

# ── FAST peak-finder (scan-mixed GS + parabolic refinement) ──
# This is the default full-auto method — ~2.5-3x faster than plain GS with
# similar sub-µm precision. GS converges to ALIGN_FAST_TOL_UM (coarse),
# then a parabolic fit on the top 3 measurements refines below 1 µm.
ALIGN_FAST_SETTLE_S           = 0.15     # shorter settle (small GS moves)
ALIGN_FAST_AVG_READS          = 3        # fewer averaged reads
ALIGN_FAST_TOL_UM             = 5.0      # coarse GS tolerance, parabolic refines
FULL_AUTO_USE_FAST            = True     # full-auto uses fast_peak_align by default

# ── HILL-CLIMB peak-finder (2D ring lock + adaptive 1D gradient) ──
# Smartest when starting close to the peak (e.g. after calibration).
# Phase 1 (optional "lock"): sample N points on a circle of increasing radius
# around the start position. If any ring point reads > center × threshold,
# move there — we've "locked on" to the peak's bright region.
# Phase 2 (1D refine): from the locked position, 3-point probe each axis,
# then grow steps geometrically until a reading drops (peak crossed) and
# finish with a parabolic fit.
# Typical: 9 lock + 4+4 refine = 17 evals when the first ring locks, or
# 4+4 when already on peak and lock phase is skipped/negative.
#
# ▸ TO TOGGLE THE RING LOCK: change HILL_CLIMB_USE_LOCK below.
#     True  → 2D ring probe runs first (more robust for drifted peaks)
#     False → plain 1D hill-climb only (faster, matches earlier behaviour)
HILL_CLIMB_USE_LOCK           = True     # flip to False for plain hill-climb
HILL_CLIMB_LOCK_RADII_UM      = [20.0, 80.0]   # try these in order (µm)
HILL_CLIMB_LOCK_N_POINTS      = 8        # points per ring (8 = octagon)
HILL_CLIMB_LOCK_THRESHOLD     = 1.15     # V_max/V_center ≥ this → "locked"
HILL_CLIMB_INITIAL_STEP_UM    = 5.0      # first 1D probe distance from start
HILL_CLIMB_MAX_RANGE_UM       = 200.0    # give up and fall back past ±this
HILL_CLIMB_STEP_GROWTH        = 1.6      # step multiplier per climb
HILL_CLIMB_STEP_CAP_UM        = 50.0     # never step larger than this
HILL_CLIMB_FLAT_V_TOL_PCT     = 0.5      # 3-probe considered "flat" if within this %
FULL_AUTO_USE_HILL_CLIMB      = True     # full-auto tries hill-climb FIRST

# CV-guided alignment
CV_GUIDED_RANGE_UM    = 100.0    # GS search range after CV correction (um)
CV_MAX_CORRECTION_MM  = 0.5      # Reject CV corrections larger than this
CV_GAP_WIDTH_UM       = 7.0      # Expected electrode gap width (for matched filter)

# Timing & accuracy
SETTLE_TIME_S        = 0.5        # Position must be stable this long before capture
SETTLE_TOLERANCE_MM  = 0.0005     # Movement below this = considered settled
CAPTURE_AVERAGES     = 10         # Number of position reads to average on capture
CAPTURE_AVG_DELAY_S  = 0.02       # Delay between averaged reads (seconds)
BACKLASH_MM          = 0.02       # Backlash compensation: approach from below by this

# Oscilloscope / detector
SCOPE_VISA   = 'USB0::0x0699::0x03C7::C021052::0::INSTR'
SCOPE_SOURCE = 'CH1'
SCOPE_AVG_READS = 5               # Averaged scope readings per measurement

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
V_TO_W = 1.0 / (RESP_A_PER_W * PDA_GAIN_TABLE[PDA_GAIN_DB][PDA_LOAD])
DARK_VOLTAGE_OFFSET = 0.0

# Smart V/div scaling (4x rule)
SCOPE_VDIV_OPTIONS_MV = [10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0]
SMART_RANGE_MULTIPLIER = 4.0      # Signal should be < V/div * this
SMART_RANGE_MIN_FILL   = 0.25     # Signal should be > V/div * this (25% of one division)
POST_SCALE_CHANGE_SETTLE_S = 1.0  # Wait after scale change for scope to stabilize
PRINT_RANGE_CHANGES = True        # Fast automation can disable routine [RANGE] chatter.

# ── Arduino switch matrix (per-pixel voltage application) ──
# When ARDUINO_ENABLED is True and arduino_switch_matrix.py is importable,
# stage_calibration will turn on the matching Arduino channel at each
# calibrated peak, hold it for VOLTAGE_DWELL_S seconds, then turn it off
# before moving to the next pixel. The mapping below is stage pixel position
# -> Arduino switch-matrix pin/channel for the electrode wiring.
ARDUINO_ENABLED       = True
ARDUINO_PORT          = "COM12"
ARDUINO_BAUD          = 9600
VOLTAGE_DWELL_S       = 5.0     # how long voltage is held at the peak
VOLTAGE_SETTLE_S      = 0.2     # delay after switch-on before scope read
VOLTAGE_ON_SCOPE_AVG  = 5       # averaged scope reads during voltage-on (0 = skip)

# Display
W = 72


# ====================== Arduino voltage switching ========================

# Module-level handle to the live ArduinoSwitchMatrix. Set in main() at startup
# and used by apply_voltage_for_pixel(). Kept module-level (rather than threaded
# through every function) so we don't churn the long alignment call chain.
_active_switch_matrix = None


PIXEL_TO_PIN = {
    1: 26, 2: 24, 3: 21, 4: 18, 5: 14, 6: 13, 7: 9, 8: 6, 9: 3, 10: 1,
    11: 28, 12: 27, 13: 23, 14: 19, 15: 15, 16: 12, 17: 8, 18: 4, 19: 100, 20: 99,
    21: 31, 22: 30, 23: 25, 24: 20, 25: 16, 26: 11, 27: 7, 28: 2, 29: 97, 30: 96,
    31: 35, 32: 33, 33: 32, 34: 29, 35: 17, 36: 10, 37: 98, 38: 95, 39: 94, 40: 92,
    41: 38, 42: 37, 43: 36, 44: 34, 45: 22, 46: 5, 47: 93, 48: 91, 49: 90, 50: 89,
    51: 39, 52: 40, 53: 41, 54: 43, 55: 55, 56: 72, 57: 84, 58: 86, 59: 87, 60: 88,
    61: 42, 62: 44, 63: 45, 64: 48, 65: 60, 66: 67, 67: 79, 68: 82, 69: 83, 70: 85,
    71: 46, 72: 47, 73: 52, 74: 57, 75: 61, 76: 66, 77: 70, 78: 75, 79: 80, 80: 81,
    81: 49, 82: 50, 83: 54, 84: 58, 85: 62, 86: 65, 87: 69, 88: 73, 89: 77, 90: 78,
    91: 51, 92: 53, 93: 56, 94: 59, 95: 63, 96: 64, 97: 68, 98: 71, 99: 74, 100: 76,
}


def pixel_to_channel(pix):
    """Map a 1..100 stage pixel number to its Arduino electrode pin/channel.

    The stage pixel is the optical position. The returned channel is the
    switch-matrix pin that energizes that pixel's electrodes. Returning None
    disables voltage for that pixel.
    """
    if pix is None or pix < 1 or pix > 100:
        return None
    return PIXEL_TO_PIN.get(int(pix))


def apply_voltage_for_pixel(pixel, detector=None, live_view=None):
    """Apply voltage via the Arduino at the calibrated peak position.

    Sequence:
      1. Switch ON the channel mapped from this pixel.
      2. Optionally take averaged scope readings during the dwell.
      3. Hold for VOLTAGE_DWELL_S total seconds.
      4. ALWAYS disable the channel before returning (lab safety).

    No-op if the Arduino isn't connected or ARDUINO_ENABLED is False.
    Voltage-on scope results are recorded under voltage_on_* keys on the
    pixel dict so save_json/save_csv can persist them.
    """
    if not ARDUINO_ENABLED or _active_switch_matrix is None:
        return

    pix = pixel.get("pixel")
    channel = pixel_to_channel(pix)
    if channel is None:
        print(f"  [Arduino] No channel mapped for pixel {pix} — skipping voltage.")
        return

    print(f"  [Arduino] APPLY VOLTAGE  pixel {pix} -> channel {channel}, "
          f"hold {VOLTAGE_DWELL_S:.1f}s ...")

    try:
        _active_switch_matrix.switch_to_channel(channel)
    except Exception as e:
        print(f"  [Arduino] FAILED to switch on channel {channel}: {e}")
        return

    voltage_on_v = voltage_on_std = voltage_on_pw = voltage_on_vdiv = None
    t_dwell_start = time.monotonic()
    try:
        # Pause live-view scope reads so the voltage-on read is exclusive.
        if live_view is not None:
            try: live_view.pause_scope_reads()
            except Exception: pass

        time.sleep(VOLTAGE_SETTLE_S)

        if detector is not None and VOLTAGE_ON_SCOPE_AVG > 0:
            try:
                voltage_on_v, voltage_on_std, voltage_on_pw, voltage_on_vdiv = (
                    detector.read_averaged(n=VOLTAGE_ON_SCOPE_AVG)
                )
            except Exception as e:
                print(f"  [Arduino] Scope read during dwell failed: {e}")

        # Sleep for whatever dwell time remains after settle + scope reads.
        elapsed = time.monotonic() - t_dwell_start
        remaining = VOLTAGE_DWELL_S - elapsed
        if remaining > 0:
            time.sleep(remaining)
    finally:
        # ALWAYS turn off — never leave a channel hot if something raised.
        try:
            _active_switch_matrix.turn_all_off()
        except Exception as e:
            print(f"  [Arduino] !! WARNING: failed to disable channel "
                  f"{channel}: {e}")
        if live_view is not None:
            try: live_view.resume_scope_reads()
            except Exception: pass

    if voltage_on_v is not None:
        pixel["voltage_on_channel"] = channel
        pixel["voltage_on_voltage_v"] = voltage_on_v
        pixel["voltage_on_voltage_std"] = voltage_on_std
        pixel["voltage_on_power_w"] = voltage_on_pw
        pixel["voltage_on_vdiv"] = voltage_on_vdiv
        pixel["voltage_on_dwell_s"] = VOLTAGE_DWELL_S
        cal_v = pixel.get("calibrated_voltage_v") or 0.0
        delta_pct = None
        if cal_v and voltage_on_v:
            delta_pct = 100.0 * (voltage_on_v - cal_v) / cal_v
            pixel["voltage_on_delta_pct"] = round(delta_pct, 3)
        delta_str = (f", Δ={delta_pct:+.2f}% vs no-voltage"
                     if delta_pct is not None else "")
        print(f"  [Arduino] Voltage-on V = {voltage_on_v*1000:.2f} mV"
              f"{delta_str}")
    else:
        # Still record that voltage was applied even without scope data.
        pixel["voltage_on_channel"] = channel
        pixel["voltage_on_dwell_s"] = VOLTAGE_DWELL_S
    print(f"  [Arduino] Channel {channel} OFF.")


# ========================== Scope Detector ================================

class ScopeDetector:
    """Oscilloscope detector with smart V/div auto-scaling.

    Thread-safe: all scope I/O is serialized through `_lock` (RLock). This
    prevents VISA conflicts when the LiveView main-thread ticker and an
    alignment worker thread both access the scope concurrently.
    """

    def __init__(self, visa_addr=SCOPE_VISA, source=SCOPE_SOURCE):
        self.addr = visa_addr
        self.source = source
        self.rm = None
        self.scope = None
        self.measu_ok = False
        self.cur_vdiv = None
        self.npts = None
        self.ymult = self.yzero = self.yoff = None
        self._lock = threading.RLock()
        self._consecutive_errors = 0

    def connect(self):
        with self._lock:
            self.rm = pyvisa.ResourceManager()
            self.scope = self.rm.open_resource(self.addr)
            self.scope.timeout = 5000
            self.scope.read_termination = '\n'
            self.scope.write_termination = None
            self.scope.encoding = 'latin_1'
            self.scope.write('*CLS')
            idn = self.scope.query('*IDN?').strip()
            print(f"  [SCOPE] {idn}")

            self.scope.write(f'SELEct:{self.source} ON')
            self.scope.write('ACQuire:STOPAfter RUNSTop')
            self.scope.write('ACQuire:STATE RUN')
            self.scope.write('TRIGger:A:TYPe EDGE')
            self.scope.write('TRIGger:A:MODe AUTO')
            try:
                self.scope.write('TRIGger:A:EDGE:SOURce LINE')
            except Exception:
                self.scope.write(f'TRIGger:A:EDGE:SOURce {self.source}')

            # Try MEASUREMENT:IMMEDIATE path first
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

            # Read initial V/div
            self._get_vdiv()

    def _refresh_preamble(self):
        with self._lock:
            self.npts = int(self.scope.query('WFMPRe:NR_PT?'))
            self.scope.write(f'DATA:STOP {self.npts}')
            self.ymult = float(self.scope.query('WFMPRe:YMULT?'))
            self.yzero = float(self.scope.query('WFMPRe:YZERO?'))
            self.yoff = float(self.scope.query('WFMPRe:YOFF?'))

    def _get_vdiv(self):
        with self._lock:
            try:
                v = float(self.scope.query(f"{self.source}:SCAle?"))
            except Exception:
                v = None
            self.cur_vdiv = v
            return v

    def _set_vdiv(self, vdiv):
        with self._lock:
            try:
                self.scope.write(f"{self.source}:SCAle {vdiv}")
                self.cur_vdiv = vdiv
            except Exception as e:
                print(f"  [SCOPE] _set_vdiv failed: {e}")
            time.sleep(0.10)

    def recover(self):
        """Recover from accumulated scope errors. Idempotent and thread-safe.

        Flushes the VISA buffer, re-asserts acquire state, and verifies a
        test read. Returns True if recovery succeeded.
        """
        with self._lock:
            try:
                try:
                    self.scope.clear()
                except Exception:
                    pass
                try:
                    self.scope.write('*CLS')
                except Exception:
                    pass
                time.sleep(0.15)
                try:
                    self.scope.write('ACQuire:STATE RUN')
                    time.sleep(0.1)
                except Exception:
                    pass
                if not self.measu_ok:
                    try:
                        self._refresh_preamble()
                    except Exception:
                        pass
                # Test a single read
                for _ in range(3):
                    try:
                        if self.measu_ok:
                            _ = float(self.scope.query("MEASUrement:IMMed:VALue?"))
                        else:
                            raw = self.scope.query_binary_values(
                                'CURVe?', datatype='b', container=np.array)
                            _ = float(np.mean(
                                (raw.astype(np.float64) - self.yoff) * self.ymult + self.yzero))
                        self._consecutive_errors = 0
                        print(f"  [SCOPE] Recovered.")
                        return True
                    except Exception:
                        time.sleep(0.2)
                print(f"  [SCOPE] Recovery failed (test read still erroring)")
                return False
            except Exception as e:
                print(f"  [SCOPE] Recovery error: {e}")
                return False

    def _select_optimal_range(self, signal_voltage_v):
        """Select optimal V/div using the 4x rule with min-fill check."""
        signal_mv = abs(signal_voltage_v) * 1000.0
        for vdiv_mv in SCOPE_VDIV_OPTIONS_MV:
            max_signal_mv = vdiv_mv * SMART_RANGE_MULTIPLIER
            min_signal_mv = vdiv_mv * SMART_RANGE_MIN_FILL
            if signal_mv <= max_signal_mv:
                if signal_mv >= min_signal_mv or vdiv_mv == SCOPE_VDIV_OPTIONS_MV[0]:
                    return vdiv_mv / 1000.0
        return SCOPE_VDIV_OPTIONS_MV[-1] / 1000.0

    def _auto_scale(self, signal_voltage_v):
        """Set scope to optimal range for signal. Returns True if changed."""
        with self._lock:
            optimal_vdiv = self._select_optimal_range(signal_voltage_v)
            if self.cur_vdiv is None:
                self._get_vdiv()
            if self.cur_vdiv != optimal_vdiv:
                if PRINT_RANGE_CHANGES:
                    print(f"    [RANGE] {self.cur_vdiv*1000:.0f} -> {optimal_vdiv*1000:.0f} mV/div "
                          f"(signal: {abs(signal_voltage_v)*1000:.1f} mV)")
                self._set_vdiv(optimal_vdiv)
                time.sleep(POST_SCALE_CHANGE_SETTLE_S)
                return True
            return False

    def read_voltage(self, retries=2):
        """Single voltage reading from scope. Thread-safe with retry.

        On failure, clears the VISA buffer and retries up to `retries` times.
        Returns None if all attempts fail.
        """
        with self._lock:
            last_err = None
            for attempt in range(retries + 1):
                try:
                    if self.measu_ok:
                        v = float(self.scope.query("MEASUrement:IMMed:VALue?"))
                    else:
                        raw = self.scope.query_binary_values(
                            'CURVe?', datatype='b', container=np.array)
                        v = float(np.mean(
                            (raw.astype(np.float64) - self.yoff) * self.ymult + self.yzero))
                    self._consecutive_errors = 0
                    return v
                except Exception as e:
                    last_err = e
                    if attempt < retries:
                        try:
                            self.scope.clear()
                        except Exception:
                            pass
                        time.sleep(0.08 + 0.07 * attempt)
                        if not self.measu_ok:
                            try:
                                self._refresh_preamble()
                            except Exception:
                                pass
            self._consecutive_errors += 1
            if self._consecutive_errors in (1, 5, 20):
                print(f"  [SCOPE] read_voltage failed (errs={self._consecutive_errors}): {last_err}")
            return None

    def read_averaged(self, n=SCOPE_AVG_READS, auto_scale=True):
        """Take n readings with smart scaling. Returns (mean_v, std_v, power_w, vdiv).

        Entire averaged read is done under the detector lock so no other
        thread can interleave commands mid-sequence.
        """
        with self._lock:
            # Probe for auto-scaling
            if auto_scale:
                probe = self.read_voltage()
                if probe is not None:
                    self._auto_scale(probe)

            readings = []
            for _ in range(n):
                v = self.read_voltage()
                if v is not None:
                    readings.append(v)
                time.sleep(0.05)

            if not readings:
                return None, None, None, self.cur_vdiv

            # MAD-based outlier removal
            arr = np.array(readings, dtype=np.float64)
            med = np.median(arr)
            mad = np.median(np.abs(arr - med)) + 1e-15
            keep = np.abs(arr - med) <= 2.5 * mad
            cleaned = arr[keep] if np.sum(keep) >= 3 else arr

            mean_v = float(np.mean(cleaned))
            std_v = float(np.std(cleaned, ddof=1)) if len(cleaned) > 1 else 0.0
            power_w = (mean_v - DARK_VOLTAGE_OFFSET) * V_TO_W
            return mean_v, std_v, power_w, self.cur_vdiv

    def format_reading(self, mean_v, power_w):
        """Format voltage + power for display."""
        if mean_v is None:
            return "N/A"
        if abs(mean_v) < 0.001:
            v_str = f"{mean_v*1e6:.1f} uV"
        elif abs(mean_v) < 1.0:
            v_str = f"{mean_v*1000:.2f} mV"
        else:
            v_str = f"{mean_v:.4f} V"

        if power_w is not None:
            abs_p = abs(power_w)
            if abs_p < 1e-6:
                p_str = f"{power_w*1e9:.1f} nW"
            elif abs_p < 1e-3:
                p_str = f"{power_w*1e6:.2f} uW"
            else:
                p_str = f"{power_w*1e3:.3f} mW"
            return f"{v_str}  ({p_str})"
        return v_str

    def close(self):
        with self._lock:
            try:
                if self.scope:
                    self.scope.close()
            finally:
                if self.rm:
                    self.rm.close()


# ========================== Stage helpers ==================================

def py_to_net_decimal(py_dec):
    """Convert Python Decimal to .NET Decimal without float precision loss."""
    return NetDecimal.Parse(str(py_dec))


def set_velocity_mode(device, label=""):
    """Set KCube front panel wheel to VELOCITY mode (not jog)."""
    try:
        params = device.GetMMIParams()
        try:
            params.JoystickMode = KCubeMMISettings.KCubeJoystickMode.Velocity
        except AttributeError:
            params.WheelMode = KCubeMMISettings.KCubeWheelMode.Velocity
        device.SetMMIParams(params)
    except Exception as e:
        print(f"  [{label}] WARN: Could not set velocity mode: {e}")
        print(f"  [{label}] Manually press MODE button on the KCube controller.")


def initialize_device(serial_no, device=None):
    """Initialize and connect to a single KCubeStepper.
    Matches the working stage_camera_viewer.py setup sequence.
    """
    if device is None:
        device = KCubeStepper.CreateKCubeStepper(serial_no)
    device.Connect(serial_no)
    time.sleep(0.5)

    # Wait for settings to be ready (critical — without this, moves fail)
    if not device.IsSettingsInitialized():
        device.WaitForSettingsInitialized(10000)

    device_info = device.GetDeviceInfo()
    print(f"  Connected: {device_info.Description} (S/N: {serial_no})")

    device.StartPolling(50)    # 50ms polling (faster feedback)
    time.sleep(0.5)
    device.EnableDevice()
    time.sleep(1.0)            # 1s wait for enable to take effect

    # Load motor config with device name (MTS25-Z8)
    config = device.LoadMotorConfiguration(
        serial_no, DeviceConfiguration.DeviceSettingsUseOptionType.UseFileSettings)
    config.DeviceSettingsName = "MTS25-Z8"
    config.UpdateCurrentConfiguration()

    vel_params = device.GetVelocityParams()
    vel_params.MaxVelocity = NetDecimal(2.0)
    vel_params.Acceleration = NetDecimal(2.0)
    device.SetVelocityParams(vel_params)
    time.sleep(0.5)

    set_velocity_mode(device, label=serial_no)

    vel_params_check = device.GetVelocityParams()
    print(f"  Velocity: {vel_params_check.MaxVelocity} mm/s, "
          f"Accel: {vel_params_check.Acceleration}, "
          f"Enabled: {device.IsEnabled}")
    return device


def home_device(device, serial_no):
    """Home a single KCubeStepper axis."""
    print(f"  Homing {serial_no}...")
    try:
        device.Home(60000)
        # Home() can return before the stage fully decelerates.
        deadline = time.time() + 10.0
        while time.time() < deadline:
            time.sleep(0.1)
            try:
                status = device.Status
                if not status.IsInMotion:
                    break
            except Exception:
                break
        time.sleep(0.5)  # extra settle margin
        print(f"  {serial_no} homed at {device.Position} mm")
    except Exception as e:
        print(f"  Homing warning {serial_no}: {e}")
        print(f"  Current position: {device.Position} mm")


def motor_to_global(motor_pos, center=STAGE_CENTER_MM):
    return PyDecimal(str(motor_pos)) - center


def global_to_motor(global_pos, center=STAGE_CENTER_MM):
    return global_pos + center


def read_motor_pos(device):
    """Read the current motor position in mm (float)."""
    return float(str(device.Position))


def read_motor_pos_averaged(device_x, device_y,
                            n=CAPTURE_AVERAGES, delay=CAPTURE_AVG_DELAY_S):
    """Read both axes N times and return averaged (x, y) for better accuracy."""
    xs, ys = [], []
    for _ in range(n):
        xs.append(float(str(device_x.Position)))
        ys.append(float(str(device_y.Position)))
        time.sleep(delay)
    return round(np.mean(xs), 6), round(np.mean(ys), 6)


def safe_move_to(device, target_mm, label=""):
    """Move one axis to target with limit-state handling and verification.
    Matches the working stage_camera_viewer.py approach.
    """
    target_mm = max(STAGE_MIN_MM, min(STAGE_MAX_MM, target_mm))

    # Only nudge off the reverse limit if the switch is actually active.
    try:
        status = device.Status
        at_rev_limit = status.IsAtReverseLimit
    except Exception:
        at_rev_limit = False

    if at_rev_limit:
        try:
            device.MoveContinuous(MotorDirection.Forward)
            time.sleep(0.3)
            device.Stop(0)
            time.sleep(0.3)
        except Exception:
            pass

    # Handle small overtravel outside device MoveTo limits.
    if target_mm < HARD_MIN_MM or target_mm > HARD_MAX_MM:
        if not ALLOW_OVERTRAVEL:
            target_mm = max(HARD_MIN_MM, min(HARD_MAX_MM, target_mm))
        else:
            boundary = HARD_MIN_MM if target_mm < HARD_MIN_MM else HARD_MAX_MM
            try:
                device.MoveTo(NetDecimal(boundary), 60000)
            except Exception as e:
                print(f"  [{label}] MoveTo failed: {e}")
            direction = (MotorDirection.Backward
                         if target_mm < boundary
                         else MotorDirection.Forward)
            try:
                device.MoveContinuous(direction)
                deadline = time.time() + OVERTRAVEL_TIMEOUT_S
                while time.time() < deadline:
                    pos = read_motor_pos(device)
                    if direction == MotorDirection.Forward:
                        if pos >= target_mm:
                            break
                    else:
                        if pos <= target_mm:
                            break
                    time.sleep(OVERTRAVEL_POLL_S)
            except Exception as e:
                print(f"  [{label}] MoveContinuous failed: {e}")
            finally:
                try:
                    device.Stop(0)
                except Exception:
                    pass
                time.sleep(0.2)

            actual = float(str(device.Position))
            err_um = (actual - target_mm) * 1000
            if abs(err_um) > 50:
                print(f"  [{label}] WARN: target={target_mm:.4f} actual={actual:.4f} err={err_um:+.0f}um")
            return actual

    # Move using NetDecimal directly (same as working stage_camera_viewer)
    try:
        device.MoveTo(NetDecimal(target_mm), 60000)
    except Exception as e:
        print(f"  [{label}] MoveTo failed: {e}")

    actual = float(str(device.Position))
    err_um = (actual - target_mm) * 1000
    if abs(err_um) > 50:  # >50um = something wrong
        print(f"  [{label}] WARN: target={target_mm:.4f} actual={actual:.4f} err={err_um:+.0f}um")
    return actual


def move_to_motor_with_backlash(device, target_mm, label=""):
    """Move with backlash compensation -- always approach from below."""
    target_mm = max(STAGE_MIN_MM, min(STAGE_MAX_MM, target_mm))
    undershoot = target_mm - BACKLASH_MM
    if HARD_MIN_MM <= target_mm <= HARD_MAX_MM and undershoot >= HARD_MIN_MM:
        safe_move_to(device, undershoot, label)
    return safe_move_to(device, target_mm, label)


def wait_for_settle(device_x, device_y,
                    timeout_s=10.0, settle_s=SETTLE_TIME_S,
                    tol=SETTLE_TOLERANCE_MM):
    """Wait until both axes are stationary for settle_s seconds."""
    last_x = read_motor_pos(device_x)
    last_y = read_motor_pos(device_y)
    stable_since = time.time()
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        time.sleep(0.05)
        cur_x = read_motor_pos(device_x)
        cur_y = read_motor_pos(device_y)
        if abs(cur_x - last_x) > tol or abs(cur_y - last_y) > tol:
            stable_since = time.time()
            last_x, last_y = cur_x, cur_y
        if time.time() - stable_since >= settle_s:
            return cur_x, cur_y

    return read_motor_pos(device_x), read_motor_pos(device_y)


# ========================== Live Camera View ===============================

class LiveView:
    """Live camera view that runs on the MAIN THREAD (required by Windows OpenCV).

    Call tick() repeatedly from the main thread to update the display.
    input() runs in a background thread via get_input().
    """

    def __init__(self, detector=None):
        self.detector = detector
        self.camera = None
        self._latest_frame = None
        self._scope_v = None
        self._scope_pw = None
        self._scope_vdiv = None
        self._scope_last_read = 0.0
        self._scope_paused = False  # Set True to stop scope reads (thread safety)
        self._input_queue = queue.Queue()

        # Display state
        self.state = {
            'pixel': 0,
            'total': 0,
            'n_calibrated': 0,
            'nom_x': 0.0,
            'nom_y': 0.0,
            'cur_x': 0.0,
            'cur_y': 0.0,
            'err_x_um': 0.0,
            'err_y_um': 0.0,
            'arrival_v': None,
            'status': 'IDLE',
        }

    def _try_open_camera(self):
        """Try multiple camera indices and backends to find a working camera."""
        backends = [
            ("DirectShow", cv2.CAP_DSHOW),
            ("MSMF", cv2.CAP_MSMF),
            ("Default", cv2.CAP_ANY),
        ]
        indices = [0, 1, 2]

        for idx in indices:
            for bname, backend in backends:
                try:
                    print(f"    Trying camera index {idx} ({bname})...", end="", flush=True)
                    cap = cv2.VideoCapture(idx, backend)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None and frame.size > 0:
                            print(f" OK ({frame.shape[1]}x{frame.shape[0]})")
                            return cap
                        else:
                            print(f" opened but no frames")
                            cap.release()
                    else:
                        print(f" not found")
                except Exception as e:
                    print(f" error: {e}")
        return None

    def start(self):
        """Open camera and create window (must be called from main thread)."""
        if not HAVE_CAMERA:
            print("  WARN: cv2 not installed -- live view disabled")
            return False

        print("  Searching for camera...")
        self.camera = self._try_open_camera()
        if self.camera is None:
            print("  WARN: No working camera found -- live view disabled")
            return False

        # Warm up
        for _ in range(5):
            self.camera.read()

        cv2.namedWindow("Stage Calibration", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Stage Calibration", 1280, 720)
        cv2.namedWindow("Edge Detection", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Edge Detection", 640, 480)
        print("  Live camera view started (2 windows)")
        return True

    def stop(self):
        """Release camera and destroy windows."""
        if self.camera is not None:
            self.camera.release()
            self.camera = None
        try:
            cv2.destroyAllWindows()
            cv2.waitKey(1)
        except Exception:
            pass

    def update(self, **kwargs):
        """Update display state."""
        self.state.update(kwargs)

    @property
    def available(self):
        return self.camera is not None

    def tick(self):
        """Update one frame of display. MUST be called from main thread."""
        if self.camera is None:
            cv2.waitKey(30)
            return -1

        ret, frame = self.camera.read()
        if not ret:
            cv2.waitKey(30)
            return -1

        self._latest_frame = frame.copy()

        # Read scope at ~10 Hz
        now = time.time()
        if now - self._scope_last_read > 0.1:
            sv, spw, svd = self._read_scope_fast()
            if sv is not None:
                self._scope_v, self._scope_pw, self._scope_vdiv = sv, spw, svd
            self._scope_last_read = now

        # Main view with overlay
        display = frame.copy()
        self._draw_overlay(display)
        cv2.imshow("Stage Calibration", display)

        # Edge detection view
        edge_view = self._detect_edges_display(frame)
        cv2.imshow("Edge Detection", edge_view)

        return cv2.waitKey(30) & 0xFF

    def grab_frame(self):
        """Get the latest raw camera frame."""
        if self._latest_frame is not None:
            return self._latest_frame.copy()
        return None

    def capture_image(self, pixel_num, tag):
        """Save camera frame + edge detection frame to disk."""
        frame = self.grab_frame()
        if frame is None:
            return ""
        os.makedirs(IMGDIR, exist_ok=True)
        fname = f"pix{pixel_num:03d}_{tag}.png"
        cv2.imwrite(os.path.join(IMGDIR, fname), frame)
        # Save edge-detected version for ML (same algorithm as live view)
        edges = self._detect_edges_raw(frame)
        cv2.imwrite(os.path.join(IMGDIR, f"pix{pixel_num:03d}_{tag}_edges.png"), edges)
        return fname

    def get_input(self, prompt="  > "):
        """Non-blocking input: starts a background thread for input().

        Call poll_input() to check if the user has typed something.
        """
        # Clear any stale input
        while not self._input_queue.empty():
            try:
                self._input_queue.get_nowait()
            except queue.Empty:
                break
        print(prompt, end="", flush=True)
        t = threading.Thread(target=self._input_worker, daemon=True)
        t.start()

    def _input_worker(self):
        try:
            line = input()
            self._input_queue.put(line)
        except EOFError:
            self._input_queue.put("q")

    def poll_input(self):
        """Check if input is ready. Returns the string or None."""
        try:
            return self._input_queue.get_nowait()
        except queue.Empty:
            return None

    def _read_scope_fast(self):
        """Non-blocking scope read for live view display.

        Never blocks waiting for the detector lock — if another thread holds
        it (e.g. an alignment read), we simply skip this frame. This keeps
        the live view responsive and avoids racing with alignment.
        """
        if self.detector is None or self._scope_paused:
            return None, None, None
        lock = self.detector._lock
        if not lock.acquire(blocking=False):
            return None, None, None  # another thread owns the scope; skip
        try:
            v = self.detector.read_voltage(retries=0)
            if v is not None:
                return v, (v - DARK_VOLTAGE_OFFSET) * V_TO_W, self.detector.cur_vdiv
        except Exception:
            pass
        finally:
            lock.release()
        return None, None, None

    def pause_scope_reads(self):
        """Pause live-view scope reads. Blocks until any in-flight read finishes.

        After this returns, no thread is touching the scope on behalf of the
        live view, so an alignment routine can safely take exclusive access.
        """
        self._scope_paused = True
        if self.detector is not None:
            try:
                with self.detector._lock:
                    pass
            except Exception:
                pass

    def resume_scope_reads(self):
        """Resume live-view scope reads."""
        self._scope_paused = False

    def _gradient_map(self, frame):
        """Compute gradient magnitude — the proven edge detection pipeline."""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        sobel_x = cv2.Sobel(enhanced, cv2.CV_64F, 1, 0, ksize=3)
        sobel_y = cv2.Sobel(enhanced, cv2.CV_64F, 0, 1, ksize=3)
        mag = np.sqrt(sobel_x ** 2 + sobel_y ** 2)
        if mag.max() > 0:
            mag = (mag / mag.max() * 255).astype(np.uint8)
        else:
            mag = mag.astype(np.uint8)
        return mag

    def _detect_edges_raw(self, frame):
        """Binary edges for saving to disk — Otsu threshold on gradient."""
        mag = self._gradient_map(frame)
        _, edges = cv2.threshold(mag, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return edges

    def _detect_edges_display(self, frame):
        """White edge outlines on black — gradient squared to suppress noise."""
        h, w = frame.shape[:2]
        mag = self._gradient_map(frame)

        # Square to suppress weak noise, boost strong edges
        mag_f = mag.astype(np.float32)
        mag_f = (mag_f * mag_f) / 255.0
        bright = np.clip(mag_f * 2.0, 0, 255).astype(np.uint8)

        # White outlines on black background
        result = cv2.cvtColor(bright, cv2.COLOR_GRAY2BGR)

        # Crosshair
        cx, cy = w // 2, h // 2
        cv2.line(result, (cx - 30, cy), (cx + 30, cy), (0, 0, 255), 1)
        cv2.line(result, (cx, cy - 30), (cx, cy + 30), (0, 0, 255), 1)

        return result

    def _draw_overlay(self, frame):
        """Draw position, intensity, and status info on the frame."""
        h, w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        state = self.state
        scope_v = self._scope_v
        scope_pw = self._scope_pw
        scope_vdiv = self._scope_vdiv

        # ── Top-left: pixel info ──
        cv2.rectangle(frame, (0, 0), (420, 110), (0, 0, 0), -1)
        pix, total = state['pixel'], state['total']
        n_cal = state['n_calibrated']
        cv2.putText(frame, f"Pixel {pix}/{total}  |  Calibrated: {n_cal}/{total}",
                    (10, 25), font, 0.55, (255, 255, 255), 1)

        nom_x, nom_y = state['nom_x'], state['nom_y']
        cv2.putText(frame, f"Nominal:  ({nom_x:.4f}, {nom_y:.4f}) mm",
                    (10, 50), font, 0.45, (180, 180, 180), 1)

        cur_x, cur_y = state['cur_x'], state['cur_y']
        err_x, err_y = state['err_x_um'], state['err_y_um']
        cv2.putText(frame, f"Current:  ({cur_x:.4f}, {cur_y:.4f}) mm",
                    (10, 72), font, 0.45, (0, 255, 255), 1)
        cv2.putText(frame, f"Error:    ({err_x:+.1f}, {err_y:+.1f}) um",
                    (10, 94), font, 0.45, (0, 200, 255), 1)

        # ── Top-right: status ──
        status = state['status']
        status_color = {
            'MOVING': (0, 200, 255),
            'ADJUSTING': (0, 255, 0),
            'CAPTURED': (255, 200, 0),
            'IDLE': (150, 150, 150),
        }.get(status, (200, 200, 200))
        cv2.rectangle(frame, (w - 200, 0), (w, 35), (0, 0, 0), -1)
        cv2.putText(frame, status, (w - 190, 25), font, 0.6, status_color, 2)

        # ── Right side: intensity bar ──
        bar_x = w - 70
        bar_w = 35
        bar_top = 70
        bar_bot = h - 100
        bar_h = bar_bot - bar_top

        cv2.rectangle(frame, (bar_x - 15, bar_top - 35),
                      (w - 5, bar_bot + 65), (0, 0, 0), -1)
        cv2.putText(frame, "INTENSITY", (bar_x - 12, bar_top - 12),
                    font, 0.42, (180, 180, 180), 1)
        cv2.rectangle(frame, (bar_x, bar_top), (bar_x + bar_w, bar_bot),
                      (100, 100, 100), 1)

        if scope_v is not None:
            frac = float(np.clip(abs(scope_v) / 5.0, 0.0, 1.0))
            fill_h = int(frac * bar_h)
            if frac < 0.33:    color = (0, 0, 200)
            elif frac < 0.66:  color = (0, 200, 200)
            else:              color = (0, 200, 0)
            if fill_h > 0:
                cv2.rectangle(frame, (bar_x + 1, bar_bot - fill_h),
                              (bar_x + bar_w - 1, bar_bot), color, -1)

            if abs(scope_v) < 0.001:
                v_text = f"{scope_v*1e6:.0f} uV"
            elif abs(scope_v) < 1.0:
                v_text = f"{scope_v*1000:.2f} mV"
            else:
                v_text = f"{scope_v:.4f} V"
            cv2.putText(frame, v_text, (bar_x - 15, bar_bot + 18),
                        font, 0.38, (255, 255, 255), 1)

            if scope_pw is not None:
                abs_p = abs(scope_pw)
                if abs_p < 1e-6:
                    p_text = f"{scope_pw*1e9:.0f} nW"
                elif abs_p < 1e-3:
                    p_text = f"{scope_pw*1e6:.1f} uW"
                else:
                    p_text = f"{scope_pw*1e3:.2f} mW"
                cv2.putText(frame, p_text, (bar_x - 15, bar_bot + 38),
                            font, 0.38, (200, 200, 200), 1)

            if scope_vdiv:
                cv2.putText(frame, f"{scope_vdiv*1000:.0f}mV/div",
                            (bar_x - 15, bar_bot + 55),
                            font, 0.32, (120, 120, 120), 1)

            arr_v = state.get('arrival_v')
            if arr_v is not None and abs(arr_v) > 1e-9:
                improv = (scope_v - arr_v) / abs(arr_v) * 100.0
                imp_color = (0, 255, 0) if improv > 0 else (0, 0, 255)
                cv2.putText(frame, f"{improv:+.1f}%", (bar_x - 8, bar_top + 15),
                            font, 0.45, imp_color, 1)
        else:
            cv2.putText(frame, "NO SCOPE", (bar_x - 12, bar_bot + 18),
                        font, 0.35, (0, 0, 200), 1)

        # ── Centre crosshair ──
        cx, cy = w // 2, h // 2
        cv2.line(frame, (cx - 25, cy), (cx + 25, cy), (0, 0, 255), 1)
        cv2.line(frame, (cx, cy - 25), (cx, cy + 25), (0, 0, 255), 1)

        # ── Bottom: help ──
        cv2.rectangle(frame, (0, h - 30), (w, h), (0, 0, 0), -1)
        cv2.putText(frame, "Adjust knobs to maximize intensity bar | Type commands in terminal",
                    (10, h - 10), font, 0.4, (150, 150, 150), 1)


# ==================== Intensity-Based Auto-Alignment =======================

def _read_intensity(detector, n=ALIGN_AVG_READS):
    """Read averaged intensity from scope. Returns voltage (float) or 0.0."""
    if detector is None:
        return 0.0
    v, _, _, _ = detector.read_averaged(n=n, auto_scale=False)
    return v if v is not None else 0.0


def _wait_until_stopped(device, timeout_s=5.0):
    """Wait until the device is no longer in motion."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status = device.Status
            if not status.IsInMotion:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def _safe_scan_move(device, target_mm, label=""):
    """Move for scanning with overtravel via MoveJog.

    0-25mm: direct MoveTo.
    Past 0 or 25: MoveJog in small steps. The .NET API throws
    VerifyDeviceMovement exceptions but the hardware DOES move —
    confirmed by real intensity readings past 25mm. All errors
    are silently suppressed.
    """
    target_mm = max(STAGE_MIN_MM, min(STAGE_MAX_MM, target_mm))
    _wait_until_stopped(device, timeout_s=5.0)

    if HARD_MIN_MM <= target_mm <= HARD_MAX_MM:
        try:
            device.MoveTo(NetDecimal(target_mm), 60000)
        except Exception:
            time.sleep(0.5)
            _wait_until_stopped(device, timeout_s=3.0)
            try:
                device.MoveTo(NetDecimal(target_mm), 60000)
            except Exception:
                pass
    else:
        # Move to boundary first
        cur = read_motor_pos(device)
        if HARD_MIN_MM <= cur <= HARD_MAX_MM:
            boundary = HARD_MAX_MM if target_mm > HARD_MAX_MM else HARD_MIN_MM
            try:
                device.MoveTo(NetDecimal(boundary), 60000)
            except Exception:
                pass
            _wait_until_stopped(device, timeout_s=5.0)

        # MoveJog past the limit — throws exception but DOES move
        direction = (MotorDirection.Forward if target_mm > read_motor_pos(device)
                     else MotorDirection.Backward)
        try:
            jog_params = device.GetJogParams()
            jog_params.StepSize = NetDecimal(0.005)
            if MotorJogModes is not None:
                try:
                    jog_params.JogMode = MotorJogModes.SingleStep
                except Exception:
                    pass
            device.SetJogParams(jog_params)
        except Exception:
            pass

        max_steps = int(abs(target_mm - read_motor_pos(device)) / 0.005) + 10
        for _ in range(max_steps):
            try:
                device.MoveJog(direction, 60000)
            except Exception:
                pass  # exception expected, move happens anyway
            _wait_until_stopped(device, timeout_s=2.0)
            pos = read_motor_pos(device)
            if direction == MotorDirection.Forward and pos >= target_mm - 0.001:
                break
            if direction == MotorDirection.Backward and pos <= target_mm + 0.001:
                break

    return float(str(device.Position))


def line_scan(device, detector, center_mm, range_um, step_um, axis_label="?"):
    """Scan one axis across a range, measuring intensity at each point.

    Moves from (center - range/2) to (center + range/2) in steps of step_um.
    Always scans in the positive direction (low to high) for backlash consistency.
    Positions are clamped to [STAGE_MIN_MM, STAGE_MAX_MM] (-0.3 to 25.3) —
    overtravel past 0/25 is handled by safe_move_to via MoveContinuous.

    Returns (positions_mm[], voltages[], best_pos_mm, best_voltage).
    """
    half_range_mm = range_um / 2000.0
    step_mm = step_um / 1000.0
    start = center_mm - half_range_mm
    stop = center_mm + half_range_mm

    start = max(STAGE_MIN_MM, start)
    stop = min(STAGE_MAX_MM, stop)

    n_steps = max(2, int(round((stop - start) / step_mm)) + 1)
    positions = np.linspace(start, stop, n_steps)

    print(f"    [{axis_label}] Scanning {(stop-start)*1000:.0f} um "
          f"({n_steps} points, {step_um:.1f} um steps)  "
          f"[{start:.4f} -> {stop:.4f} mm]")

    # Approach from below for backlash consistency
    undershoot = start - BACKLASH_MM
    if undershoot >= STAGE_MIN_MM:
        _safe_scan_move(device, undershoot, f"{axis_label}")
        time.sleep(0.3)

    voltages = []
    best_v = -1e30
    best_pos = center_mm

    for i, pos in enumerate(positions):
        _safe_scan_move(device, pos, f"{axis_label}")
        time.sleep(ALIGN_SETTLE_S)
        v = _read_intensity(detector)
        voltages.append(v)

        if v > best_v:
            best_v = v
            best_pos = pos

        # Progress bar
        bar_len = 20
        filled = int((i + 1) / n_steps * bar_len)
        bar = '=' * filled + '-' * (bar_len - filled)
        v_str = f"{v*1000:.2f} mV" if abs(v) >= 0.001 else f"{v*1e6:.0f} uV"
        print(f"\r    [{axis_label}] [{bar}] {i+1}/{n_steps}  "
              f"pos={pos:.4f}  V={v_str}    ", end="", flush=True)

    print()  # newline after progress bar

    best_v_str = f"{best_v*1000:.2f} mV" if abs(best_v) >= 0.001 else f"{best_v*1e6:.0f} uV"
    print(f"    [{axis_label}] Peak: {best_v_str} at {best_pos:.4f} mm")

    return list(positions), voltages, best_pos, best_v


def _measure_at(device, detector, pos_mm, label="?"):
    """Move to position, wait, read intensity. Returns voltage."""
    _safe_scan_move(device, pos_mm, label)
    time.sleep(ALIGN_SETTLE_S)
    return _read_intensity(detector)


def _fmt_v(v):
    """Format voltage for display."""
    if abs(v) >= 0.001:
        return f"{v*1000:.2f} mV"
    return f"{v*1e6:.0f} uV"


def golden_section_search(device, detector, a, b, tol_um=1.0, axis_label="?"):
    """Golden section search for peak intensity on one axis.

    Finds the position that maximises intensity in [a, b].
    Assumes the intensity profile is unimodal (single peak).

    Returns (best_pos_mm, best_voltage, n_evals, history) where history is
    a dict with 'positions', 'voltages', 'brackets' for diagnostic plotting.
    """
    history = {"positions": [], "voltages": [], "brackets": []}

    a = max(STAGE_MIN_MM, a)
    b = min(STAGE_MAX_MM, b)
    if a >= b:
        v = _measure_at(device, detector, a, axis_label)
        history["positions"].append(a)
        history["voltages"].append(v)
        return a, v, 1, history

    phi = (1.0 + 5.0 ** 0.5) / 2.0
    rho = 2.0 - phi

    tol_mm = tol_um / 1000.0
    n_evals = 0

    x1 = a + rho * (b - a)
    x2 = b - rho * (b - a)

    undershoot = a - BACKLASH_MM
    if undershoot >= STAGE_MIN_MM:
        _safe_scan_move(device, undershoot, axis_label)
        time.sleep(0.2)

    I1 = _measure_at(device, detector, x1, axis_label)
    n_evals += 1
    history["positions"].append(x1)
    history["voltages"].append(I1)

    I2 = _measure_at(device, detector, x2, axis_label)
    n_evals += 1
    history["positions"].append(x2)
    history["voltages"].append(I2)

    history["brackets"].append((a, b))

    print(f"    [{axis_label}] Golden section: [{a:.4f}, {b:.4f}] mm "
          f"({(b-a)*1000:.0f} um range, tol {tol_um:.1f} um)")

    while (b - a) * 1000.0 > tol_um:
        if I1 < I2:
            a = x1
            x1 = x2
            I1 = I2
            x2 = b - rho * (b - a)
            I2 = _measure_at(device, detector, x2, axis_label)
            n_evals += 1
            history["positions"].append(x2)
            history["voltages"].append(I2)
        else:
            b = x2
            x2 = x1
            I2 = I1
            x1 = a + rho * (b - a)
            I1 = _measure_at(device, detector, x1, axis_label)
            n_evals += 1
            history["positions"].append(x1)
            history["voltages"].append(I1)

        history["brackets"].append((a, b))

        best_v = max(I1, I2)
        best_pos = x1 if I1 >= I2 else x2
        print(f"\r    [{axis_label}] interval: {(b-a)*1000:.1f} um  "
              f"best: {_fmt_v(best_v)} at {best_pos:.4f}  "
              f"({n_evals} evals)    ", end="", flush=True)

    print()

    best_pos = (a + b) / 2.0
    _safe_scan_move(device, best_pos, f"{axis_label}-final")
    time.sleep(ALIGN_SETTLE_S)
    final_v = _read_intensity(detector)
    n_evals += 1
    history["positions"].append(best_pos)
    history["voltages"].append(final_v)

    print(f"    [{axis_label}] Result: {_fmt_v(final_v)} at {best_pos:.4f} mm  "
          f"({n_evals} evals)")

    return best_pos, final_v, n_evals, history


def golden_section_align(device_x, device_y, detector, live_view,
                         range_um=None, **_ignored):
    """Auto-alignment using golden section search.

    Fast approach (~12 seconds):
      1. Golden section search on X
      2. Golden section search on Y at optimised X
      3. Auto-extends range if peak is at edge

    Args:
        range_um: Search range per axis (um). Defaults to ALIGN_COARSE_RANGE_UM.
                  Use smaller values when CV-guided (gap already approximately centered).

    Returns dict with alignment results or None on failure.
    """
    if detector is None:
        print("  GOLDEN SECTION: No oscilloscope -- cannot align.")
        return None

    search_range_um = range_um if range_um is not None else ALIGN_COARSE_RANGE_UM
    range_mm = search_range_um / 1000.0

    print(f"\n  {'─'*55}")
    print(f"  GOLDEN SECTION SEARCH")
    print(f"  Range: ±{search_range_um/2:.0f} um per axis")
    print(f"  Tolerance: 1.0 um")
    print(f"  Settle: {ALIGN_SETTLE_S:.1f}s per point, "
          f"{ALIGN_AVG_READS} averaged reads")
    print(f"  {'─'*55}")

    # Pause live view scope reads to avoid VISA resource lock conflicts
    # (synchronous: blocks until any in-flight live-view read completes)
    if live_view is not None:
        live_view.pause_scope_reads()

    # Set scope to 1V/div — fixed for entire alignment, no auto-scaling
    detector._set_vdiv(1.0)
    time.sleep(0.5)

    start_x = read_motor_pos(device_x)
    start_y = read_motor_pos(device_y)

    edge_threshold_um = 3.0  # if peak is within this of boundary, extend
    max_extensions = 3       # max times to extend per axis
    extend_um = ALIGN_COARSE_RANGE_UM  # extend by this much each time

    # ── Golden section on X with auto-extend ──
    print(f"\n  Optimising X axis...")
    x_lo = start_x - range_mm / 2.0
    x_hi = start_x + range_mm / 2.0
    total_n_x = 0
    hist_x = None

    for attempt in range(1 + max_extensions):
        best_x, best_xv, n_x, hist_x = golden_section_search(
            device_x, detector, x_lo, x_hi,
            tol_um=1.0, axis_label="X")
        total_n_x += n_x

        # Check if peak is at the edge of the search range
        at_lo = (best_x - max(x_lo, STAGE_MIN_MM)) * 1000 < edge_threshold_um
        at_hi = (min(x_hi, STAGE_MAX_MM) - best_x) * 1000 < edge_threshold_um
        at_hard_limit = ((best_x - STAGE_MIN_MM) * 1000 < edge_threshold_um or
                         (STAGE_MAX_MM - best_x) * 1000 < edge_threshold_um)

        if (at_lo or at_hi) and not at_hard_limit:
            if at_lo:
                new_lo = x_lo - extend_um / 1000.0
                print(f"  >> Peak at lower edge — extending range to "
                      f"{new_lo:.4f} mm ({attempt+1}/{max_extensions})")
                x_lo = new_lo
            else:
                new_hi = x_hi + extend_um / 1000.0
                print(f"  >> Peak at upper edge — extending range to "
                      f"{new_hi:.4f} mm ({attempt+1}/{max_extensions})")
                x_hi = new_hi
        else:
            break

    # ── Golden section on Y with auto-extend ──
    print(f"\n  Optimising Y axis...")
    y_lo = start_y - range_mm / 2.0
    y_hi = start_y + range_mm / 2.0
    total_n_y = 0
    hist_y = None

    for attempt in range(1 + max_extensions):
        best_y, best_yv, n_y, hist_y = golden_section_search(
            device_y, detector, y_lo, y_hi,
            tol_um=1.0, axis_label="Y")
        total_n_y += n_y

        at_lo = (best_y - max(y_lo, STAGE_MIN_MM)) * 1000 < edge_threshold_um
        at_hi = (min(y_hi, STAGE_MAX_MM) - best_y) * 1000 < edge_threshold_um
        at_hard_limit = ((best_y - STAGE_MIN_MM) * 1000 < edge_threshold_um or
                         (STAGE_MAX_MM - best_y) * 1000 < edge_threshold_um)

        if (at_lo or at_hi) and not at_hard_limit:
            if at_lo:
                new_lo = y_lo - extend_um / 1000.0
                print(f"  >> Peak at lower edge — extending range to "
                      f"{new_lo:.4f} mm ({attempt+1}/{max_extensions})")
                y_lo = new_lo
            else:
                new_hi = y_hi + extend_um / 1000.0
                print(f"  >> Peak at upper edge — extending range to "
                      f"{new_hi:.4f} mm ({attempt+1}/{max_extensions})")
                y_hi = new_hi
        else:
            break

    n_x = total_n_x
    n_y = total_n_y

    # ── Final position ──
    _safe_scan_move(device_x, best_x, "X-final")
    _safe_scan_move(device_y, best_y, "Y-final")
    time.sleep(ALIGN_SETTLE_S * 2)

    # Final validation read (no auto_scale - keep fixed)
    final_v, _, final_pw, _ = detector.read_averaged(auto_scale=False)
    final_v = final_v if final_v is not None else 0.0
    total_evals = n_x + n_y + 3  # +3 for bracket check

    correction_x_um = (best_x - start_x) * 1000.0
    correction_y_um = (best_y - start_y) * 1000.0
    v_str = detector.format_reading(final_v, final_pw)

    print(f"\n  {'─'*55}")
    print(f"  GOLDEN SECTION COMPLETE")
    print(f"  Start:      ({start_x:.4f}, {start_y:.4f}) mm")
    print(f"  Aligned to: ({best_x:.4f}, {best_y:.4f}) mm")
    print(f"  Correction: ({correction_x_um:+.1f}, {correction_y_um:+.1f}) um")
    print(f"  Final intensity: {v_str}")
    print(f"  Total measurements: {total_evals}")
    print(f"  {'─'*55}")

    # Resume live view scope reads
    if live_view is not None:
        live_view.resume_scope_reads()
        live_view.update(
            cur_x=best_x, cur_y=best_y,
            status='ADJUSTING',
        )

    return {
        "best_x": best_x,
        "best_y": best_y,
        "peak_v": final_v,
        "n_evals": total_evals,
        "coarse_correction_um": [
            round(correction_x_um, 1),
            round(correction_y_um, 1),
        ],
        "method": "golden_section",
        "scan_data": {
            "X": hist_x,
            "Y": hist_y,
        },
    }


# ================== FAST peak finder (GS + parabolic) ======================

def _parabolic_vertex(x0, x1, x2, y0, y1, y2):
    """Fit a parabola through 3 points; return the x of its vertex.

    Returns None if ill-conditioned (collinear, convex-up, or vertex outside
    the bracket [x0, x2]).
    """
    denom = (x0 - x1) * (x0 - x2) * (x1 - x2)
    if abs(denom) < 1e-18:
        return None
    A = (x2 * (y1 - y0) + x1 * (y0 - y2) + x0 * (y2 - y1)) / denom
    B = (x2 * x2 * (y0 - y1) + x1 * x1 * (y2 - y0) + x0 * x0 * (y1 - y2)) / denom
    if abs(A) < 1e-18 or A >= 0:   # flat line or convex (minimum) — reject
        return None
    vx = -B / (2.0 * A)
    if vx < min(x0, x2) or vx > max(x0, x2):
        return None
    return vx


def _measure_fast(device, detector, pos_mm, settle_s, avg_reads, label="?"):
    """Fast measurement: reduced settle + fewer averages."""
    _safe_scan_move(device, pos_mm, label)
    time.sleep(settle_s)
    if detector is None:
        return 0.0
    v, _, _, _ = detector.read_averaged(n=avg_reads, auto_scale=False)
    return v if v is not None else 0.0


def fast_peak_search(device, detector, a, b, axis_label="?",
                     tol_um=ALIGN_FAST_TOL_UM,
                     settle_s=ALIGN_FAST_SETTLE_S,
                     avg_reads=ALIGN_FAST_AVG_READS):
    """GS to a coarse tolerance, then refine with a parabolic fit.

    Rationale: GS converges linearly (each iteration narrows by φ); parabolic
    interpolation converges quadratically near a smooth peak. Running GS only
    to a loose tolerance (default 5 µm) and then fitting a parabola to the
    best 3 evaluations typically gives <1 µm final precision in ~10 evals
    instead of ~16.

    Returns (best_pos_mm, best_voltage, n_evals, history).
    """
    history = {"positions": [], "voltages": [], "brackets": []}

    a = max(STAGE_MIN_MM, a)
    b = min(STAGE_MAX_MM, b)
    if a >= b:
        v = _measure_fast(device, detector, a, settle_s, avg_reads, axis_label)
        history["positions"].append(a)
        history["voltages"].append(v)
        return a, v, 1, history

    phi = (1.0 + 5.0 ** 0.5) / 2.0
    rho = 2.0 - phi
    n_evals = 0

    x1 = a + rho * (b - a)
    x2 = b - rho * (b - a)

    # Approach from below for backlash consistency
    undershoot = a - BACKLASH_MM
    if undershoot >= STAGE_MIN_MM:
        _safe_scan_move(device, undershoot, axis_label)
        time.sleep(0.2)

    I1 = _measure_fast(device, detector, x1, settle_s, avg_reads, axis_label)
    n_evals += 1
    history["positions"].append(x1); history["voltages"].append(I1)

    I2 = _measure_fast(device, detector, x2, settle_s, avg_reads, axis_label)
    n_evals += 1
    history["positions"].append(x2); history["voltages"].append(I2)

    history["brackets"].append((a, b))

    print(f"    [{axis_label}] Fast search [{a:.4f}, {b:.4f}] mm "
          f"({(b-a)*1000:.0f} µm, tol {tol_um:.1f} µm + parabolic)")

    while (b - a) * 1000.0 > tol_um:
        if I1 < I2:
            a = x1
            x1 = x2; I1 = I2
            x2 = b - rho * (b - a)
            I2 = _measure_fast(device, detector, x2, settle_s, avg_reads, axis_label)
            n_evals += 1
            history["positions"].append(x2); history["voltages"].append(I2)
        else:
            b = x2
            x2 = x1; I2 = I1
            x1 = a + rho * (b - a)
            I1 = _measure_fast(device, detector, x1, settle_s, avg_reads, axis_label)
            n_evals += 1
            history["positions"].append(x1); history["voltages"].append(I1)

        history["brackets"].append((a, b))
        best_v = max(I1, I2)
        best_pos = x1 if I1 >= I2 else x2
        print(f"\r    [{axis_label}] interval {(b-a)*1000:.1f} µm  "
              f"best {_fmt_v(best_v)} at {best_pos:.4f}  ({n_evals} evals)    ",
              end="", flush=True)
    print()

    # ── Parabolic refinement on the 3 best measurements ──
    hx = np.array(history["positions"])
    hv = np.array(history["voltages"])
    k = int(np.argmax(hv))

    # Pick 2 points that bracket k in position (closest on either side if possible)
    left_candidates  = np.where(hx < hx[k])[0]
    right_candidates = np.where(hx > hx[k])[0]
    vx = None
    if left_candidates.size and right_candidates.size:
        l = left_candidates[np.argmin(hx[k] - hx[left_candidates])]
        r = right_candidates[np.argmin(hx[right_candidates] - hx[k])]
        vx = _parabolic_vertex(hx[l], hx[k], hx[r], hv[l], hv[k], hv[r])

    if vx is not None:
        peak_pos = max(STAGE_MIN_MM, min(STAGE_MAX_MM, vx))
        method_tag = "parabolic"
    else:
        peak_pos = (a + b) / 2.0
        method_tag = "mid-bracket"

    # Measure at refined peak
    final_v = _measure_fast(device, detector, peak_pos, settle_s, avg_reads,
                            f"{axis_label}-final")
    n_evals += 1
    history["positions"].append(peak_pos); history["voltages"].append(final_v)

    # If parabolic vertex gave a LOWER reading than the best GS point, keep GS best
    if final_v < hv.max() * 0.98:
        k_best = int(np.argmax(hv[:-1]))  # exclude the just-added parabolic point
        peak_pos = float(hx[k_best])
        final_v  = float(hv[k_best])
        method_tag = "GS-best (parabolic worse)"
        _safe_scan_move(device, peak_pos, f"{axis_label}-revert")
        time.sleep(settle_s)

    print(f"    [{axis_label}] Peak ({method_tag}): {_fmt_v(final_v)} at "
          f"{peak_pos:.4f} mm  ({n_evals} evals)")

    return peak_pos, final_v, n_evals, history


def fast_peak_align(device_x, device_y, detector, live_view,
                    range_um=None, **_ignored):
    """Drop-in replacement for golden_section_align — ~2.5-3x faster.

    Same interface, same auto-extend-on-edge logic, same return dict. Uses
    `fast_peak_search` (coarse GS + parabolic + reduced settle/averaging).
    """
    if detector is None:
        print("  FAST ALIGN: No oscilloscope -- cannot align.")
        return None

    search_range_um = range_um if range_um is not None else ALIGN_COARSE_RANGE_UM
    range_mm = search_range_um / 1000.0

    print(f"\n  {'─'*55}")
    print(f"  FAST PEAK SEARCH  (coarse GS + parabolic)")
    print(f"  Range: ±{search_range_um/2:.0f} µm per axis")
    print(f"  GS tol: {ALIGN_FAST_TOL_UM:.0f} µm  →  parabolic refines <1 µm")
    print(f"  Settle: {ALIGN_FAST_SETTLE_S:.2f}s, "
          f"{ALIGN_FAST_AVG_READS} avg reads")
    print(f"  {'─'*55}")

    if live_view is not None:
        live_view.pause_scope_reads()

    detector._set_vdiv(1.0)
    time.sleep(0.3)

    start_x = read_motor_pos(device_x)
    start_y = read_motor_pos(device_y)

    edge_threshold_um = 5.0
    max_extensions = 3
    extend_um = ALIGN_COARSE_RANGE_UM

    # X axis with auto-extend
    print(f"\n  Optimising X axis...")
    x_lo = start_x - range_mm / 2.0
    x_hi = start_x + range_mm / 2.0
    total_n_x = 0
    hist_x = None
    best_x = start_x
    for attempt in range(1 + max_extensions):
        best_x, _best_xv, n_x, hist_x = fast_peak_search(
            device_x, detector, x_lo, x_hi, axis_label="X")
        total_n_x += n_x
        at_lo = (best_x - max(x_lo, STAGE_MIN_MM)) * 1000 < edge_threshold_um
        at_hi = (min(x_hi, STAGE_MAX_MM) - best_x) * 1000 < edge_threshold_um
        at_hard_limit = ((best_x - STAGE_MIN_MM) * 1000 < edge_threshold_um or
                         (STAGE_MAX_MM - best_x) * 1000 < edge_threshold_um)
        if (at_lo or at_hi) and not at_hard_limit:
            if at_lo:
                x_lo -= extend_um / 1000.0
                print(f"  >> Peak at lower edge — extending ({attempt+1}/{max_extensions})")
            else:
                x_hi += extend_um / 1000.0
                print(f"  >> Peak at upper edge — extending ({attempt+1}/{max_extensions})")
        else:
            break

    # Move to best X before scanning Y
    _safe_scan_move(device_x, best_x, "X")
    time.sleep(ALIGN_FAST_SETTLE_S)

    # Y axis with auto-extend
    print(f"\n  Optimising Y axis...")
    y_lo = start_y - range_mm / 2.0
    y_hi = start_y + range_mm / 2.0
    total_n_y = 0
    hist_y = None
    best_y = start_y
    for attempt in range(1 + max_extensions):
        best_y, _best_yv, n_y, hist_y = fast_peak_search(
            device_y, detector, y_lo, y_hi, axis_label="Y")
        total_n_y += n_y
        at_lo = (best_y - max(y_lo, STAGE_MIN_MM)) * 1000 < edge_threshold_um
        at_hi = (min(y_hi, STAGE_MAX_MM) - best_y) * 1000 < edge_threshold_um
        at_hard_limit = ((best_y - STAGE_MIN_MM) * 1000 < edge_threshold_um or
                         (STAGE_MAX_MM - best_y) * 1000 < edge_threshold_um)
        if (at_lo or at_hi) and not at_hard_limit:
            if at_lo:
                y_lo -= extend_um / 1000.0
                print(f"  >> Peak at lower edge — extending ({attempt+1}/{max_extensions})")
            else:
                y_hi += extend_um / 1000.0
                print(f"  >> Peak at upper edge — extending ({attempt+1}/{max_extensions})")
        else:
            break

    # Final position
    _safe_scan_move(device_x, best_x, "X-final")
    _safe_scan_move(device_y, best_y, "Y-final")
    time.sleep(ALIGN_FAST_SETTLE_S * 2)

    final_v, _, final_pw, _ = detector.read_averaged(auto_scale=False)
    final_v = final_v if final_v is not None else 0.0
    total_evals = total_n_x + total_n_y + 1

    correction_x_um = (best_x - start_x) * 1000.0
    correction_y_um = (best_y - start_y) * 1000.0
    v_str = detector.format_reading(final_v, final_pw)

    print(f"\n  {'─'*55}")
    print(f"  FAST PEAK COMPLETE")
    print(f"  Start:      ({start_x:.4f}, {start_y:.4f}) mm")
    print(f"  Aligned to: ({best_x:.4f}, {best_y:.4f}) mm")
    print(f"  Correction: ({correction_x_um:+.1f}, {correction_y_um:+.1f}) µm")
    print(f"  Final intensity: {v_str}")
    print(f"  Total measurements: {total_evals}")
    print(f"  {'─'*55}")

    if live_view is not None:
        live_view.resume_scope_reads()
        live_view.update(cur_x=best_x, cur_y=best_y, status='ADJUSTING')

    return {
        "best_x": best_x,
        "best_y": best_y,
        "peak_v": final_v,
        "n_evals": total_evals,
        "coarse_correction_um": [
            round(correction_x_um, 1),
            round(correction_y_um, 1),
        ],
        "method": "fast_peak",
        "scan_data": {"X": hist_x, "Y": hist_y},
    }


# ================== HILL-CLIMB peak finder ================================

def hill_climb_peak(device, detector, start_mm,
                    initial_step_um=None,
                    max_range_um=None,
                    step_growth=None,
                    step_cap_um=None,
                    axis_label="?",
                    settle_s=None, avg_reads=None):
    """Adaptive hill-climbing peak finder with parabolic finish.

    Algorithm:
      1. Measure at start, start+δ, start-δ  (3 evals)
      2. If centre is ≥ both neighbours -> bracketed. Parabolic fit on those
         3 points, move to vertex, verify. Total 4 evals.
      3. Else climb toward the higher side with doubling-ish step size.
      4. When a measurement drops below the previous one, we've crossed the
         peak -> parabolic fit on last 3, move to vertex, verify.
      5. If total travel exceeds max_range_um, return the best seen so far
         (the wrapper then falls back to a broader search).

    Returns (peak_pos_mm, peak_voltage, n_evals, history).
    """
    if settle_s is None:     settle_s   = ALIGN_FAST_SETTLE_S
    if avg_reads is None:    avg_reads  = ALIGN_FAST_AVG_READS
    if initial_step_um is None: initial_step_um = HILL_CLIMB_INITIAL_STEP_UM
    if max_range_um is None:    max_range_um    = HILL_CLIMB_MAX_RANGE_UM
    if step_growth is None:     step_growth     = HILL_CLIMB_STEP_GROWTH
    if step_cap_um is None:     step_cap_um     = HILL_CLIMB_STEP_CAP_UM

    history = {"positions": [], "voltages": []}

    def measure(pos):
        pos = max(STAGE_MIN_MM, min(STAGE_MAX_MM, pos))
        _safe_scan_move(device, pos, axis_label)
        time.sleep(settle_s)
        if detector is None:
            v = 0.0
        else:
            v, _, _, _ = detector.read_averaged(n=avg_reads, auto_scale=False)
            v = v if v is not None else 0.0
        history["positions"].append(pos)
        history["voltages"].append(v)
        return pos, v

    step_mm = initial_step_um / 1000.0
    step_cap_mm = step_cap_um / 1000.0
    max_range_mm = max_range_um / 1000.0
    lo_limit = max(STAGE_MIN_MM, start_mm - max_range_mm)
    hi_limit = min(STAGE_MAX_MM, start_mm + max_range_mm)

    # ── 3-point probe ──
    x0, I0 = measure(start_mm)
    x_r, I_r = measure(start_mm + step_mm)
    x_l, I_l = measure(start_mm - step_mm)
    n = 3
    print(f"    [{axis_label}] Hill-climb probe @ {start_mm:.4f}:  "
          f"L={_fmt_v(I_l)}  C={_fmt_v(I0)}  R={_fmt_v(I_r)}")

    # Already bracketed at the start? (centre >= both neighbours)
    vmax3 = max(I_l, I0, I_r)
    flat = (vmax3 > 0 and
            (vmax3 - min(I_l, I0, I_r)) / vmax3 * 100.0
                < HILL_CLIMB_FLAT_V_TOL_PCT)
    if (I0 >= I_l and I0 >= I_r) or flat:
        vx = _parabolic_vertex(x_l, x0, x_r, I_l, I0, I_r)
        if vx is not None:
            peak_pos, peak_v = measure(vx)
            n += 1
            if peak_v < vmax3 * 0.98:
                # parabolic vertex produced a lower reading -> keep best probe
                candidates = [(x_l, I_l), (x0, I0), (x_r, I_r)]
                candidates.sort(key=lambda t: -t[1])
                peak_pos, peak_v = candidates[0]
                _safe_scan_move(device, peak_pos, f"{axis_label}-revert")
                time.sleep(settle_s)
        else:
            # parabola rejected -> use best of the 3 probes
            candidates = [(x_l, I_l), (x0, I0), (x_r, I_r)]
            candidates.sort(key=lambda t: -t[1])
            peak_pos, peak_v = candidates[0]
        tag = "bracketed" if not flat else "flat-top"
        print(f"    [{axis_label}] {tag} -> {_fmt_v(peak_v)} at "
              f"{peak_pos:.4f}  ({n} evals)")
        return peak_pos, peak_v, n, history

    # ── Decide climb direction from whichever side is higher ──
    if I_r > I_l:
        direction = +1
        prev_pos, prev_v = x0, I0
        cur_pos, cur_v   = x_r, I_r
    else:
        direction = -1
        prev_pos, prev_v = x0, I0
        cur_pos, cur_v   = x_l, I_l

    # ── Climb with growing step until we pass the peak ──
    cap_n = 25  # safety cap so we never loop forever
    while n < cap_n:
        step_mm = min(step_mm * step_growth, step_cap_mm)
        next_target = cur_pos + direction * step_mm
        if next_target < lo_limit or next_target > hi_limit:
            # Out of range without finding peak -> give up (caller will fall back)
            print(f"    [{axis_label}] climb hit range limit at "
                  f"{cur_pos:.4f}  ({n} evals)  -> best so far "
                  f"{_fmt_v(cur_v)}")
            return cur_pos, cur_v, n, history

        next_pos, next_v = measure(next_target)
        n += 1

        if next_v < cur_v:
            # Just crossed the peak: bracket is (prev_pos, cur_pos, next_pos)
            vx = _parabolic_vertex(prev_pos, cur_pos, next_pos,
                                   prev_v, cur_v, next_v)
            if vx is not None:
                peak_pos, peak_v = measure(vx)
                n += 1
                if peak_v < cur_v * 0.98:
                    peak_pos, peak_v = cur_pos, cur_v
                    _safe_scan_move(device, peak_pos, f"{axis_label}-revert")
                    time.sleep(settle_s)
            else:
                peak_pos, peak_v = cur_pos, cur_v
            dist_um = (cur_pos - start_mm) * 1000.0
            print(f"    [{axis_label}] climbed {dist_um:+.0f} µm -> "
                  f"{_fmt_v(peak_v)} at {peak_pos:.4f}  ({n} evals)")
            return peak_pos, peak_v, n, history

        prev_pos, prev_v = cur_pos, cur_v
        cur_pos, cur_v   = next_pos, next_v

    # Safety cap hit: return best seen
    k = int(np.argmax(history["voltages"]))
    return history["positions"][k], history["voltages"][k], n, history


def _ring_probe(device_x, device_y, detector,
                center_x, center_y, radius_um, n_points,
                settle_s=None, avg_reads=None, label=""):
    """Measure intensity at n_points evenly spaced on a ring of given radius.

    Returns list of (x_mm, y_mm, v_volts, angle_deg).
    """
    import math
    if settle_s is None:  settle_s = ALIGN_FAST_SETTLE_S
    if avg_reads is None: avg_reads = ALIGN_FAST_AVG_READS

    results = []
    for k in range(n_points):
        theta = 2.0 * math.pi * k / n_points
        dx = (radius_um / 1000.0) * math.cos(theta)
        dy = (radius_um / 1000.0) * math.sin(theta)
        x = max(STAGE_MIN_MM, min(STAGE_MAX_MM, center_x + dx))
        y = max(STAGE_MIN_MM, min(STAGE_MAX_MM, center_y + dy))
        _safe_scan_move(device_x, x, f"{label}-X")
        _safe_scan_move(device_y, y, f"{label}-Y")
        time.sleep(settle_s)
        if detector is None:
            v = 0.0
        else:
            v, _, _, _ = detector.read_averaged(n=avg_reads, auto_scale=False)
            v = v if v is not None else 0.0
        results.append((x, y, v, math.degrees(theta)))
    return results


def hill_climb_align(device_x, device_y, detector, live_view,
                     initial_step_um=None,
                     max_range_um=None,
                     fallback_to_fast_peak=True,
                     use_lock_phase=None,
                     lock_radii_um=None,
                     lock_n_points=None,
                     lock_threshold=None,
                     **_ignored):
    """Two-axis hill-climb alignment with optional 2D ring-lock phase.

    Phase 1 (lock): probe circles at expanding radii (default 20 → 80 µm).
      If any ring point exceeds the centre reading by `lock_threshold`×,
      we move there — we've locked onto the peak's bright region. This
      rescues us when the simple ±5 µm 1D probes can't detect gradient
      because both neighbours are in the "off-peak" region.

    Phase 2 (refine): from the locked position, 1D hill-climb on X then Y.

    If either axis hits the range limit without finding a peak, falls back
    to fast_peak_search on that axis.
    """
    if detector is None:
        print("  HILL CLIMB: No oscilloscope.")
        return None

    if initial_step_um is None: initial_step_um = HILL_CLIMB_INITIAL_STEP_UM
    if max_range_um is None:    max_range_um    = HILL_CLIMB_MAX_RANGE_UM
    if use_lock_phase is None:  use_lock_phase  = HILL_CLIMB_USE_LOCK
    if lock_radii_um is None:   lock_radii_um   = HILL_CLIMB_LOCK_RADII_UM
    if lock_n_points is None:   lock_n_points   = HILL_CLIMB_LOCK_N_POINTS
    if lock_threshold is None:  lock_threshold  = HILL_CLIMB_LOCK_THRESHOLD

    print(f"\n  {'─'*55}")
    print(f"  HILL CLIMB  (ring lock + adaptive 1D + parabolic)")
    if use_lock_phase:
        print(f"  Lock: radii {lock_radii_um} µm, "
              f"{lock_n_points} pts/ring, threshold ×{lock_threshold:.2f}")
    print(f"  1D step: {initial_step_um:.1f} µm  |  "
          f"Max range: ±{max_range_um/2:.0f} µm")
    print(f"  Settle: {ALIGN_FAST_SETTLE_S:.2f}s  |  "
          f"Avg reads: {ALIGN_FAST_AVG_READS}")
    print(f"  {'─'*55}")

    if live_view is not None:
        live_view.pause_scope_reads()
    detector._set_vdiv(1.0)
    time.sleep(0.3)

    start_x = read_motor_pos(device_x)
    start_y = read_motor_pos(device_y)
    lock_x, lock_y = start_x, start_y
    lock_evals = 0
    ring_history_x = []
    ring_history_y = []

    # ══════════ PHASE 1: 2D ring lock ══════════
    if use_lock_phase:
        print(f"\n  Lock phase:")
        # Read centre once
        _safe_scan_move(device_x, start_x, "X")
        _safe_scan_move(device_y, start_y, "Y")
        time.sleep(ALIGN_FAST_SETTLE_S)
        V_center, _, _, _ = detector.read_averaged(
            n=ALIGN_FAST_AVG_READS, auto_scale=False)
        V_center = V_center if V_center is not None else 0.0
        lock_evals += 1
        print(f"    centre @ ({start_x:.4f}, {start_y:.4f}): V={_fmt_v(V_center)}")

        # Add centre point to history
        ring_history_x.append(start_x); ring_history_y.append(start_y)

        locked = False
        for R in lock_radii_um:
            ring = _ring_probe(device_x, device_y, detector,
                               start_x, start_y, R, lock_n_points,
                               label="ring")
            lock_evals += len(ring)

            v_vals = [r[2] for r in ring]
            k_max = int(np.argmax(v_vals))
            v_max = v_vals[k_max]
            v_min = min(v_vals)

            for (x, y, v, ang) in ring:
                ring_history_x.append(x); ring_history_y.append(y)

            print(f"    R={R:5.1f} µm  V range {_fmt_v(v_min)} → "
                  f"{_fmt_v(v_max)} at θ={ring[k_max][3]:.0f}°")

            # Lock criterion: max >= centre * threshold
            if V_center > 0 and v_max >= V_center * lock_threshold:
                lock_x = ring[k_max][0]
                lock_y = ring[k_max][1]
                print(f"    LOCKED → ({lock_x:.4f}, {lock_y:.4f}) mm "
                      f"(×{v_max/V_center:.2f} brighter than centre)")
                _safe_scan_move(device_x, lock_x, "X-lock")
                _safe_scan_move(device_y, lock_y, "Y-lock")
                time.sleep(ALIGN_FAST_SETTLE_S)
                locked = True
                break
        if not locked:
            # No ring rose above threshold -- assume we're already on peak
            # Pick the brightest ring point seen (mild nudge toward any gradient)
            if ring_history_x and ring:
                v_best = V_center
                best_xy = (start_x, start_y)
                # Walk through last ring only (smallest radius that gave no lock
                # probably has the best info)
                for (x, y, v, ang) in ring:
                    if v > v_best:
                        v_best = v
                        best_xy = (x, y)
                if best_xy != (start_x, start_y):
                    lock_x, lock_y = best_xy
                    print(f"    no lock — nudging to best ring point "
                          f"({lock_x:.4f}, {lock_y:.4f})  V={_fmt_v(v_best)}")
                    _safe_scan_move(device_x, lock_x, "X-nudge")
                    _safe_scan_move(device_y, lock_y, "Y-nudge")
                    time.sleep(ALIGN_FAST_SETTLE_S)
                else:
                    print(f"    no lock — hill-climbing from start")

    # ══════════ PHASE 2: 1D refine per axis ══════════
    # ── X axis ──
    print(f"\n  X axis...")
    best_x, best_xv, n_x, hist_x = hill_climb_peak(
        device_x, detector, lock_x,
        initial_step_um=initial_step_um, max_range_um=max_range_um,
        axis_label="X")

    # Fall back to fast_peak if climb reached the range limit
    if fallback_to_fast_peak:
        travelled = abs(best_x - start_x) * 1000.0
        if travelled > max_range_um * 0.95:
            print(f"  [X] climb reached {travelled:.0f} µm -- falling back "
                  f"to fast_peak_search on a wider window")
            half = max_range_um / 1000.0
            best_x, best_xv, n_fb, hist_x_fb = fast_peak_search(
                device_x, detector,
                start_x - half, start_x + half, axis_label="X")
            n_x += n_fb
            hist_x = hist_x_fb

    _safe_scan_move(device_x, best_x, "X")
    time.sleep(ALIGN_FAST_SETTLE_S)

    # ── Y axis (from the locked-then-X-refined position) ──
    print(f"\n  Y axis...")
    cur_y_start = read_motor_pos(device_y)
    best_y, best_yv, n_y, hist_y = hill_climb_peak(
        device_y, detector, cur_y_start,
        initial_step_um=initial_step_um, max_range_um=max_range_um,
        axis_label="Y")

    if fallback_to_fast_peak:
        travelled = abs(best_y - cur_y_start) * 1000.0
        if travelled > max_range_um * 0.95:
            print(f"  [Y] climb reached {travelled:.0f} µm -- falling back "
                  f"to fast_peak_search on a wider window")
            half = max_range_um / 1000.0
            best_y, best_yv, n_fb, hist_y_fb = fast_peak_search(
                device_y, detector,
                start_y - half, start_y + half, axis_label="Y")
            n_y += n_fb
            hist_y = hist_y_fb

    _safe_scan_move(device_x, best_x, "X-final")
    _safe_scan_move(device_y, best_y, "Y-final")
    time.sleep(ALIGN_FAST_SETTLE_S * 2)

    final_v, _, final_pw, _ = detector.read_averaged(auto_scale=False)
    final_v = final_v if final_v is not None else 0.0
    total_evals = lock_evals + n_x + n_y + 1

    correction_x_um = (best_x - start_x) * 1000.0
    correction_y_um = (best_y - start_y) * 1000.0
    v_str = detector.format_reading(final_v, final_pw)

    print(f"\n  {'─'*55}")
    print(f"  HILL CLIMB COMPLETE")
    print(f"  Start:      ({start_x:.4f}, {start_y:.4f}) mm")
    print(f"  Aligned to: ({best_x:.4f}, {best_y:.4f}) mm")
    print(f"  Correction: ({correction_x_um:+.1f}, {correction_y_um:+.1f}) µm")
    print(f"  Final intensity: {v_str}")
    print(f"  Total measurements: {total_evals}")
    print(f"  {'─'*55}")

    if live_view is not None:
        live_view.resume_scope_reads()
        live_view.update(cur_x=best_x, cur_y=best_y, status='ADJUSTING')

    return {
        "best_x": best_x, "best_y": best_y,
        "peak_v": final_v, "n_evals": total_evals,
        "coarse_correction_um": [
            round(correction_x_um, 1),
            round(correction_y_um, 1),
        ],
        "method": "hill_climb",
        "scan_data": {"X": hist_x, "Y": hist_y},
    }


def cv_guided_align(device_x, device_y, detector, live_view,
                    pixel_num=0, **_ignored):
    """CV-guided alignment: camera estimates gap position, then GS refines.

    Much faster than blind GS because the camera provides a coarse position
    estimate (~20-50 um accuracy), so GS only needs to search ±50 um instead
    of ±200 um.

    Workflow:
      1. Load pixel-to-stage calibration (M matrix from 'm' command)
      2. Grab camera frame, detect brightest point on electrode (= gap)
      3. Compute pixel offset from beam center to gap → stage correction
      4. Move stage by correction
      5. Run golden section with tight range to refine

    Falls back to standard golden section if CV guidance is unavailable.
    Returns dict with alignment results or None.
    """
    # ── Check prerequisites ──
    calib = load_pixel_stage_calib()
    has_camera = live_view is not None and live_view.available

    if calib is None:
        print("  CV-GUIDED: No pixel-to-stage calibration (run 'm' to create).")
        print("  CV-GUIDED: Falling back to standard golden section.")
        return golden_section_align(device_x, device_y, detector, live_view)

    if not has_camera:
        print("  CV-GUIDED: No camera available. Falling back to GS.")
        return golden_section_align(device_x, device_y, detector, live_view)

    M, p_beam = calib

    # ── Grab frame and detect gap ──
    frame = live_view.grab_frame()
    gap_pos = cv_estimate_gap_position(frame)

    if gap_pos is None:
        print("  CV-GUIDED: Could not detect gap in frame. Falling back to GS.")
        return golden_section_align(device_x, device_y, detector, live_view)

    # ── Compute stage correction ──
    # M maps pixel displacement → stage displacement that caused it.
    # To bring gap to beam: stage_correction = M @ (beam_px - gap_px)
    correction_mm = M @ (p_beam - np.array([gap_pos[0], gap_pos[1]]))

    dist_mm = np.linalg.norm(correction_mm)
    dx_um = correction_mm[0] * 1000.0
    dy_um = correction_mm[1] * 1000.0

    print(f"\n  {'─'*55}")
    print(f"  CV-GUIDED ALIGNMENT")
    print(f"  Gap detected at pixel ({gap_pos[0]:.0f}, {gap_pos[1]:.0f})")
    print(f"  Beam at pixel ({p_beam[0]:.0f}, {p_beam[1]:.0f})")
    print(f"  Correction: ({dx_um:+.1f}, {dy_um:+.1f}) um  |  {dist_mm*1000:.0f} um total")

    if dist_mm > CV_MAX_CORRECTION_MM:
        print(f"  CV-GUIDED: Correction {dist_mm*1000:.0f} um > "
              f"{CV_MAX_CORRECTION_MM*1000:.0f} um limit. Falling back to GS.")
        print(f"  {'─'*55}")
        return golden_section_align(device_x, device_y, detector, live_view)

    # ── Save diagnostic image ──
    save_cv_diagnostic(pixel_num, frame, gap_pos, p_beam, correction_mm)

    # ── Apply correction ──
    cur_x = read_motor_pos(device_x)
    cur_y = read_motor_pos(device_y)
    new_x = cur_x + correction_mm[0]
    new_y = cur_y + correction_mm[1]

    print(f"  Moving: ({cur_x:.4f}, {cur_y:.4f}) -> ({new_x:.4f}, {new_y:.4f}) mm")
    _safe_scan_move(device_x, new_x, "X-cv")
    _safe_scan_move(device_y, new_y, "Y-cv")
    time.sleep(ALIGN_SETTLE_S)

    # ── Run GS with tight range centered on corrected position ──
    print(f"  Refining with GS ±{CV_GUIDED_RANGE_UM/2:.0f} um...")
    print(f"  {'─'*55}")

    result = golden_section_align(device_x, device_y, detector, live_view,
                                  range_um=CV_GUIDED_RANGE_UM)

    if result is not None:
        result["method"] = "cv_guided_gs"
        result["cv_correction_um"] = [round(dx_um, 1), round(dy_um, 1)]
        result["cv_gap_pixel"] = [round(gap_pos[0], 1), round(gap_pos[1], 1)]
        # Total correction = CV correction + GS refinement
        if result.get("coarse_correction_um"):
            gs_corr = result["coarse_correction_um"]
            result["coarse_correction_um"] = [
                round(dx_um + gs_corr[0], 1),
                round(dy_um + gs_corr[1], 1),
            ]

    return result


def auto_align(device_x, device_y, detector, live_view, **_ignored):
    """Run intensity-based auto-alignment using scan-then-refine.

    Two-phase approach per axis:
      1. Coarse scan: sweep ±50um in 5um steps to find the signal
      2. Fine scan: sweep ±5um in 0.5um steps around the coarse peak

    Scans X first, then Y at best X, then optionally X again to correct
    any cross-coupling.

    Returns dict with alignment results or None on failure.
    """
    if detector is None:
        print("  AUTO-ALIGN: No oscilloscope -- cannot align.")
        return None

    print(f"\n  {'─'*55}")
    print(f"  AUTO-ALIGN (scan-then-refine)")
    print(f"  Coarse: ±{ALIGN_COARSE_RANGE_UM/2:.0f} um, "
          f"{ALIGN_COARSE_STEP_UM:.0f} um steps")
    print(f"  Fine:   ±{ALIGN_FINE_RANGE_UM/2:.0f} um, "
          f"{ALIGN_FINE_STEP_UM:.1f} um steps")
    print(f"  Settle: {ALIGN_SETTLE_S:.1f}s per point, "
          f"{ALIGN_AVG_READS} averaged reads")
    print(f"  {'─'*55}")

    # Pause live view scope reads to avoid VISA resource lock conflicts
    # (synchronous: blocks until any in-flight live-view read completes)
    if live_view is not None:
        live_view.pause_scope_reads()

    # Set scope to 1V/div — fixed for entire alignment, no auto-scaling
    detector._set_vdiv(1.0)
    time.sleep(0.5)

    start_x = read_motor_pos(device_x)
    start_y = read_motor_pos(device_y)
    total_points = 0

    # ══════════ PHASE 1: Coarse scans ══════════
    print(f"\n  Phase 1: COARSE SCAN")

    # Coarse X scan
    cx_pos, cx_vol, coarse_best_x, coarse_best_xv = line_scan(
        device_x, detector, start_x,
        ALIGN_COARSE_RANGE_UM, ALIGN_COARSE_STEP_UM, "X-coarse")
    total_points += len(cx_pos)

    # Move to best X, then coarse Y scan
    _safe_scan_move(device_x, coarse_best_x, "X")
    time.sleep(ALIGN_SETTLE_S)

    cy_pos, cy_vol, coarse_best_y, coarse_best_yv = line_scan(
        device_y, detector, start_y,
        ALIGN_COARSE_RANGE_UM, ALIGN_COARSE_STEP_UM, "Y-coarse")
    total_points += len(cy_pos)

    # Move to coarse best Y
    _safe_scan_move(device_y, coarse_best_y, "Y")
    time.sleep(ALIGN_SETTLE_S)

    print(f"\n  Coarse result: X={coarse_best_x:.4f}, Y={coarse_best_y:.4f} mm")

    # ══════════ PHASE 2: Fine scans ══════════
    print(f"\n  Phase 2: FINE SCAN")

    # Fine X scan centered on coarse best
    fx_pos, fx_vol, fine_best_x, fine_best_xv = line_scan(
        device_x, detector, coarse_best_x,
        ALIGN_FINE_RANGE_UM, ALIGN_FINE_STEP_UM, "X-fine")
    total_points += len(fx_pos)

    # Move to fine best X, then fine Y scan
    _safe_scan_move(device_x, fine_best_x, "X")
    time.sleep(ALIGN_SETTLE_S)

    fy_pos, fy_vol, fine_best_y, fine_best_yv = line_scan(
        device_y, detector, coarse_best_y,
        ALIGN_FINE_RANGE_UM, ALIGN_FINE_STEP_UM, "Y-fine")
    total_points += len(fy_pos)

    # ══════════ FINAL POSITION ══════════
    _safe_scan_move(device_x, fine_best_x, "X-final")
    _safe_scan_move(device_y, fine_best_y, "Y-final")
    time.sleep(ALIGN_SETTLE_S * 2)

    # Final validation read (no auto-scale — keep fixed)
    final_v, _, final_pw, _ = detector.read_averaged(auto_scale=False)
    final_v = final_v if final_v is not None else 0.0

    correction_x_um = (fine_best_x - start_x) * 1000.0
    correction_y_um = (fine_best_y - start_y) * 1000.0

    v_str = detector.format_reading(final_v, final_pw)

    print(f"\n  {'─'*55}")
    print(f"  AUTO-ALIGN COMPLETE")
    print(f"  Start:      ({start_x:.4f}, {start_y:.4f}) mm")
    print(f"  Aligned to: ({fine_best_x:.4f}, {fine_best_y:.4f}) mm")
    print(f"  Correction: ({correction_x_um:+.1f}, {correction_y_um:+.1f}) um")
    print(f"  Final intensity: {v_str}")
    print(f"  Total measurements: {total_points}")
    print(f"  {'─'*55}")

    # Resume live view scope reads
    if live_view is not None:
        live_view.resume_scope_reads()
        live_view.update(
            cur_x=fine_best_x, cur_y=fine_best_y,
            status='ADJUSTING',
        )

    return {
        "best_x": fine_best_x,
        "best_y": fine_best_y,
        "peak_v": final_v,
        "n_evals": total_points,
        "coarse_correction_um": [
            round(correction_x_um, 1),
            round(correction_y_um, 1),
        ],
        "method": "line_scan",
        "scan_data": {
            "X-coarse": {"positions": cx_pos, "voltages": cx_vol},
            "Y-coarse": {"positions": cy_pos, "voltages": cy_vol},
            "X-fine": {"positions": fx_pos, "voltages": fx_vol},
            "Y-fine": {"positions": fy_pos, "voltages": fy_vol},
        },
    }


# ==================== Alignment Diagnostics ================================

def save_alignment_log(pixel_num, result):
    """Save per-pixel alignment measurements to CSV for later analysis."""
    os.makedirs(ALIGNDIR, exist_ok=True)
    logfile = os.path.join(ALIGNDIR, f"pix{pixel_num:03d}_align.csv")
    scan_data = result.get("scan_data", {})
    method = result.get("method", "unknown")

    with open(logfile, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["phase", "point", "position_mm", "voltage_v",
                         "voltage_mV", "bracket_lo", "bracket_hi"])

        for phase_name, data in scan_data.items():
            if phase_name == "bracket_check":
                for i, (pos, vol) in enumerate(
                        zip(data["positions"], data["voltages"])):
                    writer.writerow([phase_name, i, f"{pos:.6f}",
                                     f"{vol:.6e}", f"{vol*1000:.3f}", "", ""])
                continue

            positions = data.get("positions", [])
            voltages = data.get("voltages", [])
            brackets = data.get("brackets", [])

            for i, (pos, vol) in enumerate(zip(positions, voltages)):
                blo = f"{brackets[i][0]:.6f}" if i < len(brackets) else ""
                bhi = f"{brackets[i][1]:.6f}" if i < len(brackets) else ""
                writer.writerow([phase_name, i, f"{pos:.6f}",
                                 f"{vol:.6e}", f"{vol*1000:.3f}", blo, bhi])

    print(f"    Log saved: {logfile}")


def plot_alignment_diagnostic(pixel_num, result):
    """Generate diagnostic plot for one pixel's alignment."""
    if not HAVE_MPL:
        return

    os.makedirs(ALIGNDIR, exist_ok=True)
    scan_data = result.get("scan_data", {})
    method = result.get("method", "unknown")
    best_x = result.get("best_x", 0)
    best_y = result.get("best_y", 0)
    peak_v = result.get("peak_v", 0)

    if method == "line_scan":
        _plot_line_scan_diagnostic(pixel_num, scan_data, best_x, best_y, peak_v)
    elif method in ("golden_section", "fast_peak", "hill_climb"):
        # Shared schema: positions/voltages per axis (hill_climb has no brackets)
        _plot_golden_section_diagnostic(pixel_num, scan_data, best_x, best_y,
                                        peak_v)


def _plot_line_scan_diagnostic(pixel_num, scan_data, best_x, best_y, peak_v):
    """4-panel plot: X-coarse, Y-coarse, X-fine, Y-fine profiles."""
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle(f"Pixel {pixel_num} — Line Scan Alignment\n"
                 f"Best: ({best_x:.4f}, {best_y:.4f}) mm, "
                 f"Peak: {peak_v*1000:.2f} mV",
                 fontsize=11, fontweight='bold')

    panels = [
        ("X-coarse", axes[0, 0]),
        ("Y-coarse", axes[0, 1]),
        ("X-fine", axes[1, 0]),
        ("Y-fine", axes[1, 1]),
    ]

    for phase_name, ax in panels:
        data = scan_data.get(phase_name, {})
        positions = data.get("positions", [])
        voltages = data.get("voltages", [])

        if not positions:
            ax.set_title(f"{phase_name} (no data)")
            continue

        pos_um = [(p - positions[len(positions)//2]) * 1000 for p in positions]
        vol_mv = [v * 1000 for v in voltages]

        ax.plot(pos_um, vol_mv, 'b.-', markersize=4, linewidth=1)

        # Mark peak
        if voltages:
            peak_idx = int(np.argmax(voltages))
            ax.plot(pos_um[peak_idx], vol_mv[peak_idx], 'r*',
                    markersize=12, zorder=5)
            ax.axvline(pos_um[peak_idx], color='r', alpha=0.3, linestyle='--')

        ax.set_title(phase_name, fontsize=10)
        ax.set_xlabel("Position offset (um)")
        ax.set_ylabel("Intensity (mV)")
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fname = os.path.join(ALIGNDIR, f"pix{pixel_num:03d}_linescan.png")
    plt.savefig(fname, dpi=120)
    plt.close(fig)
    print(f"    Plot saved: {fname}")


def _plot_golden_section_diagnostic(pixel_num, scan_data, best_x, best_y,
                                     peak_v):
    """2-panel plot: X and Y golden section convergence."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"Pixel {pixel_num} — Golden Section Alignment\n"
                 f"Best: ({best_x:.4f}, {best_y:.4f}) mm, "
                 f"Peak: {peak_v*1000:.2f} mV",
                 fontsize=11, fontweight='bold')

    for ax_idx, axis_name in enumerate(["X", "Y"]):
        ax = axes[ax_idx]
        data = scan_data.get(axis_name, {})
        positions = data.get("positions", [])
        voltages = data.get("voltages", [])
        brackets = data.get("brackets", [])

        if not positions:
            ax.set_title(f"{axis_name} (no data)")
            continue

        # Plot bracket narrowing as shaded regions
        n_brackets = len(brackets)
        for i, (blo, bhi) in enumerate(brackets):
            alpha = 0.08 + 0.12 * (i / max(1, n_brackets - 1))
            ax.axvspan(blo * 1000, bhi * 1000, alpha=alpha,
                       color='blue', zorder=0)

        # Plot measurement points in order (color = measurement order)
        pos_mm_arr = [p * 1000 for p in positions]
        vol_mv = [v * 1000 for v in voltages]
        colors = plt.cm.viridis(np.linspace(0, 1, len(positions)))

        for i, (p, v, c) in enumerate(zip(pos_mm_arr, vol_mv, colors)):
            ax.plot(p, v, 'o', color=c, markersize=6, zorder=3)
            ax.annotate(str(i+1), (p, v), textcoords="offset points",
                        xytext=(3, 3), fontsize=7, color='gray')

        # Mark final best
        if positions and voltages:
            best_idx = int(np.argmax(voltages))
            ax.plot(pos_mm_arr[best_idx], vol_mv[best_idx], 'r*',
                    markersize=15, zorder=5, label='Peak')

        # Also plot bracket check points if available
        bc = scan_data.get("bracket_check", {})
        if bc and axis_name == "X":
            bc_pos = [p * 1000 for p in bc.get("positions", [])]
            bc_vol = [v * 1000 for v in bc.get("voltages", [])]
            ax.plot(bc_pos, bc_vol, 'rs', markersize=8, alpha=0.5,
                    label='Bracket check', zorder=2)

        ax.set_title(f"{axis_name}-axis", fontsize=10)
        ax.set_xlabel("Position (mm x1000)")
        ax.set_ylabel("Intensity (mV)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

    plt.tight_layout()
    fname = os.path.join(ALIGNDIR, f"pix{pixel_num:03d}_golden.png")
    plt.savefig(fname, dpi=120)
    plt.close(fig)
    print(f"    Plot saved: {fname}")


FULL_AUTO_MIN_SIGNAL_MV = 100.0   # Below this, flag pixel and do wider search
FULL_AUTO_HEALTH_CHECK_EVERY = 10  # Run a scope health check every N pixels
FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL = 2   # Retry a failing pixel this many times


def _scope_health_check(detector, live_view, label=""):
    """Probe the scope and attempt recovery if unresponsive.
    Returns True if scope is healthy (or was successfully recovered).
    """
    if detector is None:
        return True
    if live_view is not None:
        live_view.pause_scope_reads()
    try:
        probe = detector.read_voltage()
        if probe is not None:
            return True
        print(f"  [HEALTH{label}] Scope unresponsive -- attempting recovery...")
        ok = detector.recover()
        if not ok:
            # Try once more after a longer pause
            time.sleep(1.5)
            ok = detector.recover()
        return ok
    finally:
        pass  # keep paused; caller decides when to resume


def _do_one_pixel(idx, pixel, pixels, total, n_calibrated_before, flagged,
                  device_x, device_y, detector, live_view, threshold_v):
    """Run the full calibration sequence for a single pixel.

    Raises on hard failure so the caller can attempt recovery+retry.
    Returns True if the pixel completed (regardless of signal threshold);
    the caller increments n_calibrated on True.
    """
    pix = pixel["pixel"]
    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]
    nom_xg = pixel["nominal_x_global"]
    nom_yg = pixel["nominal_y_global"]

    print(f"\n  {'─'*60}")
    print(f"  Pixel {pix}/{total}  [row {pixel['j']}, col {pixel['i']}]  "
          f"|  Done: {n_calibrated_before}/{total}  "
          f"|  Flagged: {len(flagged)}")
    print(f"  Nominal: motor ({nom_xm:.4f}, {nom_ym:.4f}) mm  "
          f"global ({nom_xg:+.4f}, {nom_yg:+.4f}) mm")

    # ── Move to nominal position ──
    print(f"  Moving to nominal...")
    _safe_scan_move(device_x, nom_xm, "X")
    _safe_scan_move(device_y, nom_ym, "Y")
    set_velocity_mode(device_x, label="X")
    set_velocity_mode(device_y, label="Y")
    time.sleep(SETTLE_TIME_S)

    # ── Record arrival (scope paused for exclusive access) ──
    if live_view is not None:
        live_view.pause_scope_reads()

    ax, ay = read_motor_pos_averaged(device_x, device_y)
    arr_v, arr_std, arr_pw, arr_vdiv = detector.read_averaged()
    arr_img = ""
    if live_view is not None and live_view.available:
        arr_img = live_view.capture_image(pix, "arrival")
    record_arrival(pixel, ax, ay,
                   arr_v if arr_v else 0.0,
                   arr_std if arr_std else 0.0,
                   arr_pw, arr_vdiv, arr_img)

    arr_v_safe = arr_v if arr_v is not None else 0.0
    print(f"  Arrival: ({ax:.4f}, {ay:.4f}) mm, "
          f"V={arr_v_safe*1000:.2f} mV")

    # ── Align (fastest-first fallback chain) ──
    result = None
    method = None

    if FULL_AUTO_USE_HILL_CLIMB:
        lock_label = "ring-lock" if HILL_CLIMB_USE_LOCK else "plain"
        print(f"  Using HILL CLIMB ({lock_label})...")
        result = hill_climb_align(device_x, device_y, detector, live_view)
        method = "hill_climb"
    elif FULL_AUTO_USE_FAST:
        print(f"  Using FAST PEAK (coarse GS + parabolic)...")
        result = fast_peak_align(device_x, device_y, detector, live_view)
        method = "fast_peak"
    else:
        print(f"  Using GOLDEN SECTION SEARCH...")
        result = golden_section_align(device_x, device_y, detector, live_view)
        method = "golden_section"
    if live_view is not None:
        live_view.pause_scope_reads()

    peak_v = result["peak_v"] if result else 0.0

    # ── Fallback 1: fast_peak on wider window (if hill-climb gave low signal) ──
    if peak_v < threshold_v and method == "hill_climb":
        print(f"\n  ** Signal {peak_v*1000:.1f} mV < {FULL_AUTO_MIN_SIGNAL_MV:.0f} mV "
              f"after hill-climb — retrying with fast_peak... **")
        result = fast_peak_align(device_x, device_y, detector, live_view)
        method = "fast_peak_retry"
        peak_v = result["peak_v"] if result else 0.0
        if live_view is not None:
            live_view.pause_scope_reads()

    # ── Fallback 2: full GS ──
    if peak_v < threshold_v:
        print(f"\n  ** Signal {peak_v*1000:.1f} mV — retrying with full GS... **")
        result = golden_section_align(device_x, device_y, detector, live_view)
        method = "gs_retry"
        peak_v = result["peak_v"] if result else 0.0
        if live_view is not None:
            live_view.pause_scope_reads()

    # ── Fallback 3: full line scan ──
    if peak_v < threshold_v:
        print(f"\n  ** Signal still {peak_v*1000:.1f} mV — retrying with FULL SCAN **")
        result = auto_align(device_x, device_y, detector, live_view)
        method = "full_scan_retry"
        peak_v = result["peak_v"] if result else 0.0
        if live_view is not None:
            live_view.pause_scope_reads()

    if peak_v < threshold_v:
        print(f"  !! FLAGGED: pixel {pix} - signal {peak_v*1000:.1f} mV "
              f"below threshold !!")
        flagged.append(pix)

    # ── Auto-capture ──
    if result is not None:
        pixel["align_method"] = method
        pixel["align_fine_n_evals"] = result.get("n_evals")
        cc = result.get("coarse_correction_um")
        if cc:
            pixel["align_coarse_correction_x_um"] = cc[0]
            pixel["align_coarse_correction_y_um"] = cc[1]

        try:
            save_alignment_log(pix, result)
        except Exception as e:
            print(f"  WARN: save_alignment_log failed: {e}")
        try:
            plot_alignment_diagnostic(pix, result)
        except Exception as e:
            print(f"  WARN: plot_alignment_diagnostic failed: {e}")

    # Ensure scope is paused for capture
    if live_view is not None:
        live_view.pause_scope_reads()

    capture_calibrated(device_x, device_y, pixel, detector, live_view)

    # ── Apply Arduino voltage at calibrated peak (no-op if matrix offline) ──
    apply_voltage_for_pixel(pixel, detector=detector, live_view=live_view)

    # Resume live view scope reads
    if live_view is not None:
        live_view.resume_scope_reads()

    return True


def full_auto_calibrate(device_x, device_y, pixels, total, start_idx,
                        detector, live_view):
    """Fully automated calibration of all pixels.

    Reliability features:
      - Scope I/O serialized via RLock (no VISA conflicts with live view)
      - Synchronous pause before every alignment read
      - Periodic scope health check every N pixels
      - Per-pixel try/except with recover() + retry on failure
      - Progress auto-saved after every pixel (even failures)
      - Ctrl-C saves progress before exiting

    Strategy:
      - Every pixel: golden section search (fast)
      - If signal below threshold: retry with full scan
      - If still below threshold: flag and continue
      - If a pixel errors out: attempt scope recovery + retry once
      - If still failing: log, flag, and move on (no crash)
    """
    if detector is None:
        print("  FULL AUTO requires oscilloscope. Aborting.")
        return 0

    import traceback

    n_calibrated = sum(1 for p in pixels if p.get("calibrated_x_motor") is not None)
    threshold_v = FULL_AUTO_MIN_SIGNAL_MV / 1000.0
    flagged = []
    errored = []

    print(f"\n  {'='*60}")
    print(f"  FULL AUTO CALIBRATION  (hardened)")
    print(f"  {'='*60}")
    print(f"  Pixels: {total} ({n_calibrated} already done)")
    if FULL_AUTO_USE_HILL_CLIMB:
        lock_tag = "+lock" if HILL_CLIMB_USE_LOCK else "plain"
        strat = f"hill-climb[{lock_tag}] -> fast_peak -> full GS -> full scan"
    elif FULL_AUTO_USE_FAST:
        strat = "fast_peak -> full GS -> full scan"
    else:
        strat = "full GS -> full scan"
    print(f"  Strategy chain: {strat}")
    print(f"  Signal threshold: {FULL_AUTO_MIN_SIGNAL_MV:.0f} mV")
    print(f"  Health check every {FULL_AUTO_HEALTH_CHECK_EVERY} pixels")
    print(f"  Retry on error: {FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL-1}x with scope recovery")
    print(f"  Press Ctrl-C at any time to stop (progress auto-saved)")
    print(f"  {'='*60}\n")

    processed_since_health = 0

    for idx in range(start_idx, total):
        pixel = pixels[idx]

        # Skip already calibrated
        if pixel.get("calibrated_x_motor") is not None:
            continue

        pix = pixel["pixel"]

        # ── Periodic health check ──
        if processed_since_health >= FULL_AUTO_HEALTH_CHECK_EVERY:
            print(f"\n  [HEALTH] Checking scope responsiveness at pixel {pix}...")
            healthy = _scope_health_check(detector, live_view,
                                          label=f"@pix{pix}")
            if not healthy:
                print(f"  [HEALTH] Scope unhealthy -- continuing but flagging.")
            processed_since_health = 0
            if live_view is not None:
                live_view.resume_scope_reads()

        # ── Try the pixel, with one recovery-and-retry on error ──
        success = False
        last_error = None
        for attempt in range(FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL):
            try:
                _do_one_pixel(idx, pixel, pixels, total, n_calibrated,
                              flagged, device_x, device_y, detector,
                              live_view, threshold_v)
                success = True
                break
            except KeyboardInterrupt:
                raise
            except Exception as e:
                last_error = e
                print(f"\n  !! Pixel {pix} attempt "
                      f"{attempt+1}/{FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL} "
                      f"raised {type(e).__name__}: {e}")
                traceback.print_exc()
                if attempt + 1 < FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL:
                    print(f"  Attempting recovery before retry...")
                    # Ensure live view is paused so recovery has exclusive access
                    if live_view is not None:
                        live_view.pause_scope_reads()
                    try:
                        detector.recover()
                    except Exception as rec_e:
                        print(f"  Scope recovery errored: {rec_e}")
                    # Clear any stale partial state on this pixel
                    try:
                        clear_pixel_fully(pixel)
                    except Exception:
                        pass
                    # Resume stages to a safe state
                    try:
                        _safe_scan_move(device_x, pixel["nominal_x_motor"], "X")
                        _safe_scan_move(device_y, pixel["nominal_y_motor"], "Y")
                    except Exception as mv_e:
                        print(f"  Stage re-home errored: {mv_e}")
                    time.sleep(1.5)

        if not success:
            print(f"  !! Pixel {pix} FAILED after "
                  f"{FULL_AUTO_MAX_ATTEMPTS_PER_PIXEL} attempts: {last_error}")
            errored.append(pix)
            flagged.append(pix)
            # Save progress even on failure so we don't lose earlier pixels
            try:
                save_json(pixels, n_calibrated)
            except Exception as save_e:
                print(f"  WARN: save_json after error failed: {save_e}")
            if live_view is not None:
                live_view.resume_scope_reads()
            processed_since_health += 1
            continue

        n_calibrated += 1
        processed_since_health += 1
        try:
            save_json(pixels, n_calibrated)
        except Exception as save_e:
            print(f"  WARN: save_json failed: {save_e}")

        pct = n_calibrated * 100 // total
        print(f"  [{pct}%] {n_calibrated}/{total} done  "
              f"|  flagged: {len(flagged)}  |  errored: {len(errored)}")

    # ── Summary ──
    try:
        save_csv(pixels)
    except Exception as e:
        print(f"  WARN: final save_csv failed: {e}")
    print(f"\n  {'='*60}")
    print(f"  FULL AUTO COMPLETE")
    print(f"  {'='*60}")
    print(f"  Calibrated: {n_calibrated}/{total}")
    if flagged:
        print(f"  Flagged pixels (low signal): {flagged}")
    if errored:
        print(f"  Errored pixels (hard failures): {errored}")
        print(f"  Consider re-running these manually.")
    if not flagged and not errored:
        print(f"  All pixels above {FULL_AUTO_MIN_SIGNAL_MV:.0f} mV threshold.")
    print(f"  {'='*60}")

    return n_calibrated


# ── Camera-based helpers (kept for optional use) ──

def detect_gap_centroid(frame):
    """Detect electrode gap centroid in a camera frame.

    Uses Otsu thresholding on grayscale to find bright gap regions between
    dark electrodes. Returns (cx, cy) in pixel coordinates or None on failure.
    """
    if frame is None:
        return None

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    _, binary = cv2.threshold(enhanced, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    h, w = frame.shape[:2]
    frame_area = h * w
    frame_cx, frame_cy = w / 2.0, h / 2.0

    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 50 or area > 0.5 * frame_area:
            continue
        M_mom = cv2.moments(c)
        if M_mom["m00"] < 1e-6:
            continue
        cx = M_mom["m10"] / M_mom["m00"]
        cy = M_mom["m01"] / M_mom["m00"]
        dist = np.hypot(cx - frame_cx, cy - frame_cy)
        candidates.append((cx, cy, area, dist))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[3])
    return (candidates[0][0], candidates[0][1])


def cv_estimate_gap_position(frame):
    """Detect the electrode gap position in camera pixel coordinates.

    The BTO electrode gap is a ~7um opening where the laser beam passes
    through. In the camera image, the electrode structure appears as a thin
    blue/purple line, and the gap is the brightest point along that line
    (transmitted laser light).

    Detection strategy (in order of reliability):
      1. Find electrode structure via blue-channel extraction
      2. Within the structure region, find the brightest point (laser
         coupling through the gap creates a local intensity maximum)
      3. Refine with weighted centroid around the peak
      4. Fall back to geometric centroid of the structure if flat

    Returns (px_x, px_y) in pixel coordinates, or None if no structure found.
    """
    if frame is None or not HAVE_CAMERA:
        return None

    h, w = frame.shape[:2]

    # ── Extract electrode structure (same pipeline as LiveView) ──
    blue = frame[:, :, 0].astype(np.float32)
    green = frame[:, :, 1].astype(np.float32)
    red = frame[:, :, 2].astype(np.float32)

    blue_excess = np.clip(blue - np.maximum(red, green) * 0.7, 0, None)
    luma = 0.2 * red + 0.3 * green + 0.5 * blue
    combined = np.clip(blue_excess + luma * 0.3, 0, 255).astype(np.uint8)

    blurred = cv2.GaussianBlur(combined, (5, 5), 1.2)
    _, structure_mask = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    structure_mask = cv2.morphologyEx(structure_mask, cv2.MORPH_CLOSE, kernel)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        structure_mask, connectivity=8)
    for i in range(1, n_labels):
        if stats[i, cv2.CC_STAT_AREA] < 100:
            structure_mask[labels == i] = 0

    if np.sum(structure_mask > 0) < 50:
        return None

    # ── Find brightest point along electrode (= laser through gap) ──
    # Weight toward blue channel (electrode colour signature)
    intensity = (0.1 * red + 0.2 * green + 0.7 * blue).astype(np.float32)

    # Dilate mask slightly to include gap edges and scattered light
    dilated = cv2.dilate(structure_mask, kernel, iterations=2)
    masked_intensity = intensity * (dilated > 0).astype(np.float32)

    # Smooth to find the peak *region* rather than a single noisy pixel
    peak_map = cv2.GaussianBlur(masked_intensity, (15, 15), 5.0)

    _, max_val, _, max_loc = cv2.minMaxLoc(peak_map)
    if max_val <= 0:
        return None

    # Check if there's a meaningful bright spot (not just uniform glow)
    mean_in_mask = np.mean(peak_map[dilated > 0]) if np.any(dilated > 0) else 0
    has_clear_peak = max_val > mean_in_mask * 1.3

    if has_clear_peak:
        # Refine with weighted centroid around the peak
        px, py = max_loc  # cv2.minMaxLoc returns (x, y)
        r = 15
        y_lo, y_hi = max(0, py - r), min(h, py + r + 1)
        x_lo, x_hi = max(0, px - r), min(w, px + r + 1)
        patch = peak_map[y_lo:y_hi, x_lo:x_hi]
        if patch.sum() > 0:
            yy, xx = np.mgrid[y_lo:y_hi, x_lo:x_hi]
            total = float(patch.sum())
            refined_x = float(np.sum(xx * patch) / total)
            refined_y = float(np.sum(yy * patch) / total)
            return (refined_x, refined_y)
        return (float(px), float(py))

    # ── Fallback: geometric centroid of largest contour ──
    contours, _ = cv2.findContours(
        structure_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        largest = max(contours, key=cv2.contourArea)
        M_mom = cv2.moments(largest)
        if M_mom["m00"] > 1e-6:
            cx = M_mom["m10"] / M_mom["m00"]
            cy = M_mom["m01"] / M_mom["m00"]
            return (cx, cy)

    return None


def save_cv_diagnostic(pixel_num, frame, gap_pos, p_beam, correction_mm):
    """Save annotated image showing CV gap detection + correction vector."""
    if frame is None or not HAVE_CAMERA:
        return
    os.makedirs(ALIGNDIR, exist_ok=True)

    display = frame.copy()
    h, w = display.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX

    # Draw beam position (frame center) — red crosshair
    bx, by = int(p_beam[0]), int(p_beam[1])
    cv2.drawMarker(display, (bx, by), (0, 0, 255),
                   cv2.MARKER_CROSSHAIR, 25, 2)
    cv2.putText(display, "BEAM", (bx + 15, by - 10),
                font, 0.4, (0, 0, 255), 1)

    # Draw detected gap position — green circle
    gx, gy = int(gap_pos[0]), int(gap_pos[1])
    cv2.circle(display, (gx, gy), 12, (0, 255, 0), 2)
    cv2.putText(display, "GAP", (gx + 15, gy - 10),
                font, 0.4, (0, 255, 0), 1)

    # Arrow from gap to beam (the correction direction)
    cv2.arrowedLine(display, (gx, gy), (bx, by), (0, 255, 255), 2,
                    tipLength=0.2)

    # Correction text
    dx_um = correction_mm[0] * 1000
    dy_um = correction_mm[1] * 1000
    dist_um = np.hypot(dx_um, dy_um)
    cv2.putText(display,
                f"Correction: ({dx_um:+.0f}, {dy_um:+.0f}) um  |  {dist_um:.0f} um",
                (10, h - 15), font, 0.45, (0, 255, 255), 1)

    fname = os.path.join(ALIGNDIR, f"pix{pixel_num:03d}_cv_guide.png")
    cv2.imwrite(fname, display)


def load_pixel_stage_calib():
    """Load persisted pixel-to-stage calibration (M matrix + beam position)."""
    if not os.path.isfile(ALIGN_CALIB_FILE):
        return None
    try:
        with open(ALIGN_CALIB_FILE) as f:
            data = json.load(f)
        M = np.array(data["M"], dtype=np.float64)
        p_beam = np.array(data["p_beam"], dtype=np.float64)
        return M, p_beam
    except Exception as e:
        print(f"  WARN: Could not load pixel-stage calibration: {e}")
        return None


def save_pixel_stage_calib(M, p_beam):
    """Save pixel-to-stage calibration to JSON."""
    os.makedirs(OUTDIR, exist_ok=True)
    data = {
        "M": M.tolist(),
        "p_beam": p_beam.tolist(),
        "calib_date": datetime.now().isoformat(),
        "step_mm": ALIGN_CALIB_STEP_MM,
    }
    with open(ALIGN_CALIB_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Calibration saved to {ALIGN_CALIB_FILE}")


def calibrate_pixel_to_stage(device_x, device_y, live_view):
    """One-time calibration of pixel-to-stage affine transform.

    Moves the stage by known amounts in X and Y, measures the resulting
    pixel displacement of a detected feature, and computes the 2x2 affine
    transform M (mm/pixel) and beam position p_beam.

    Returns (M, p_beam) or None on failure.
    """
    print(f"\n  {'='*60}")
    print(f"  PIXEL-TO-STAGE CALIBRATION")
    print(f"  {'='*60}")
    print(f"  This moves the stage by {ALIGN_CALIB_STEP_MM*1000:.0f} um in X and Y")
    print(f"  to measure how pixels map to stage coordinates.")
    print(f"  Ensure a detectable feature (electrode gap) is visible.")
    print(f"  {'='*60}")

    step = ALIGN_CALIB_STEP_MM

    x0 = read_motor_pos(device_x)
    y0 = read_motor_pos(device_y)
    time.sleep(0.3)
    frame0 = live_view.grab_frame()
    p0 = detect_gap_centroid(frame0)
    if p0 is None:
        print("  ERROR: Cannot detect feature in reference frame.")
        print("  Make sure electrodes are visible and in focus.")
        return None
    print(f"  Reference:  stage ({x0:.4f}, {y0:.4f}) mm,  pixel ({p0[0]:.1f}, {p0[1]:.1f})")

    safe_move_to(device_x, x0 + step, "X-cal")
    time.sleep(0.5)
    frame1 = live_view.grab_frame()
    p1 = detect_gap_centroid(frame1)
    if p1 is None:
        print("  ERROR: Cannot detect feature after X move.")
        safe_move_to(device_x, x0, "X-cal")
        return None
    dp_x = np.array([p1[0] - p0[0], p1[1] - p0[1]])
    print(f"  After X+{step*1000:.0f}um: pixel shift ({dp_x[0]:+.1f}, {dp_x[1]:+.1f}) px")

    safe_move_to(device_x, x0, "X-cal")
    time.sleep(0.3)

    safe_move_to(device_y, y0 + step, "Y-cal")
    time.sleep(0.5)
    frame2 = live_view.grab_frame()
    p2 = detect_gap_centroid(frame2)
    if p2 is None:
        print("  ERROR: Cannot detect feature after Y move.")
        safe_move_to(device_y, y0, "Y-cal")
        return None
    dp_y = np.array([p2[0] - p0[0], p2[1] - p0[1]])
    print(f"  After Y+{step*1000:.0f}um: pixel shift ({dp_y[0]:+.1f}, {dp_y[1]:+.1f}) px")

    safe_move_to(device_y, y0, "Y-cal")
    time.sleep(0.3)

    P = np.column_stack([dp_x, dp_y])
    det = np.linalg.det(P)
    if abs(det) < 1e-6:
        print("  ERROR: Pixel displacements are degenerate (collinear).")
        print(f"  det(P) = {det:.6f}")
        return None

    S = np.array([[step, 0.0], [0.0, step]])
    M = S @ np.linalg.inv(P)

    h, w = frame0.shape[:2]
    p_beam = np.array([w / 2.0, h / 2.0])

    scale_x = np.linalg.norm(M[0, :])
    scale_y = np.linalg.norm(M[1, :])
    print(f"\n  Affine transform M (mm/pixel):")
    print(f"    [{M[0,0]:+.6f}  {M[0,1]:+.6f}]")
    print(f"    [{M[1,0]:+.6f}  {M[1,1]:+.6f}]")
    print(f"  Scale: {scale_x*1000:.2f} um/px (X),  {scale_y*1000:.2f} um/px (Y)")
    print(f"  Beam position: ({p_beam[0]:.0f}, {p_beam[1]:.0f}) px (frame center)")

    save_pixel_stage_calib(M, p_beam)
    return M, p_beam


# ======================== Grid generation ==================================

def build_grid():
    """Build the linspace grid (same order as main scan: j outer, i inner)."""
    half = SCAN_SIZE_MM / 2.0
    xs = np.linspace(SCAN_CENTER_X_MM - half, SCAN_CENTER_X_MM + half,
                     SCAN_POINTS_PER_AXIS)
    ys = np.linspace(SCAN_CENTER_Y_MM - half, SCAN_CENTER_Y_MM + half,
                     SCAN_POINTS_PER_AXIS)

    pixels = []
    pixel_num = 0
    for j, y_global in enumerate(ys):
        for i, x_global in enumerate(xs):
            pixel_num += 1
            x_motor = float(global_to_motor(PyDecimal(str(x_global))))
            y_motor = float(global_to_motor(PyDecimal(str(y_global))))
            pixels.append({
                "pixel": pixel_num,
                "i": i, "j": j,
                # Nominal (programmed) position
                "nominal_x_global": round(float(x_global), 6),
                "nominal_y_global": round(float(y_global), 6),
                "nominal_x_motor": round(x_motor, 6),
                "nominal_y_motor": round(y_motor, 6),
                # Arrived position (where motor actually lands)
                "arrived_x_motor": None,
                "arrived_y_motor": None,
                "arrival_error_x_um": None,
                "arrival_error_y_um": None,
                # Intensity at arrival (before manual adjustment)
                "arrived_voltage_v": None,
                "arrived_voltage_std": None,
                "arrived_power_w": None,
                "arrived_vdiv": None,
                "arrived_image": None,
                # Calibrated position (after manual alignment)
                "calibrated_x_motor": None,
                "calibrated_y_motor": None,
                "calibrated_x_global": None,
                "calibrated_y_global": None,
                # Calibration offsets (calibrated - nominal)
                "dx_um": None, "dy_um": None,
                # Correction applied (calibrated - arrived)
                "correction_x_um": None,
                "correction_y_um": None,
                # Intensity at calibrated position (max transmission)
                "calibrated_voltage_v": None,
                "calibrated_voltage_std": None,
                "calibrated_power_w": None,
                "calibrated_vdiv": None,
                "calibrated_image": None,
                # Intensity improvement
                "voltage_improvement_pct": None,
                # Auto-alignment metadata
                "align_method": None,
                "align_coarse_correction_x_um": None,
                "align_coarse_correction_y_um": None,
                "align_fine_n_evals": None,
                # Voltage-on (Arduino switch matrix) — populated by
                # apply_voltage_for_pixel() right after capture.
                "voltage_on_channel": None,
                "voltage_on_voltage_v": None,
                "voltage_on_voltage_std": None,
                "voltage_on_power_w": None,
                "voltage_on_vdiv": None,
                "voltage_on_dwell_s": None,
                "voltage_on_delta_pct": None,
                # Timestamp
                "timestamp": None,
            })
    return pixels


# ======================== Save / Load ======================================

def save_json(pixels, n_calibrated):
    """Write calibration JSON (auto-saved after every capture)."""
    data = {
        "created_utc": datetime.utcnow().isoformat() + "Z",
        "grid_size": SCAN_POINTS_PER_AXIS,
        "scan_size_mm": SCAN_SIZE_MM,
        "center_mm": float(STAGE_CENTER_MM),
        "n_calibrated": n_calibrated,
        "scope_visa": SCOPE_VISA,
        "pda_gain_db": PDA_GAIN_DB,
        "v_to_w": V_TO_W,
        "backlash_mm": BACKLASH_MM,
        "pixels": pixels,
    }
    os.makedirs(OUTDIR, exist_ok=True)
    with open(JSON_FILE, "w") as f:
        json.dump(data, f, indent=2)


CSV_FIELDS = [
    "pixel", "i", "j",
    "nominal_x_motor", "nominal_y_motor",
    "nominal_x_global", "nominal_y_global",
    "arrived_x_motor", "arrived_y_motor",
    "arrival_error_x_um", "arrival_error_y_um",
    "arrived_voltage_v", "arrived_voltage_std", "arrived_power_w", "arrived_vdiv",
    "arrived_image",
    "calibrated_x_motor", "calibrated_y_motor",
    "calibrated_x_global", "calibrated_y_global",
    "dx_um", "dy_um",
    "correction_x_um", "correction_y_um",
    "calibrated_voltage_v", "calibrated_voltage_std", "calibrated_power_w", "calibrated_vdiv",
    "calibrated_image",
    "voltage_improvement_pct",
    "align_method",
    "align_coarse_correction_x_um", "align_coarse_correction_y_um",
    "align_fine_n_evals",
    "voltage_on_channel", "voltage_on_voltage_v", "voltage_on_voltage_std",
    "voltage_on_power_w", "voltage_on_vdiv",
    "voltage_on_dwell_s", "voltage_on_delta_pct",
    "timestamp",
]


def save_csv(pixels):
    """Write calibration CSV."""
    os.makedirs(OUTDIR, exist_ok=True)
    with open(CSV_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for p in pixels:
            row = {}
            for k in CSV_FIELDS:
                val = p.get(k)
                if isinstance(val, float) and k.endswith("_w"):
                    row[k] = f"{val:.6e}" if val is not None else ""
                else:
                    row[k] = val if val is not None else ""
            writer.writerow(row)


def load_existing():
    """Load existing calibration JSON. Returns (pixels, n_calibrated) or None."""
    if not os.path.isfile(JSON_FILE):
        return None
    try:
        with open(JSON_FILE) as f:
            data = json.load(f)
        return data["pixels"], data["n_calibrated"]
    except Exception as e:
        print(f"  WARN: Could not load {JSON_FILE}: {e}")
        return None


# ==================== Named-run directory helpers ==========================

def set_run_dir(run_name):
    """Redirect all per-run output paths to `<OUTDIR_ROOT>/<run_name>/`.

    Updates module globals so the rest of the code (save_json, save_csv,
    capture_image, alignment diagnostics, etc.) writes into the named run
    folder. ALIGN_CALIB_FILE stays at OUTDIR_ROOT so the camera-to-stage
    mapping is shared across runs.
    """
    global OUTDIR, IMGDIR, ALIGNDIR, JSON_FILE, CSV_FILE
    OUTDIR    = os.path.join(OUTDIR_ROOT, run_name)
    IMGDIR    = os.path.join(OUTDIR, "images")
    ALIGNDIR  = os.path.join(OUTDIR, "alignment_logs")
    JSON_FILE = os.path.join(OUTDIR, "pixel_positions.json")
    CSV_FILE  = os.path.join(OUTDIR, "pixel_positions.csv")
    os.makedirs(OUTDIR, exist_ok=True)


def save_run_heatmaps(pixels, out_dir, name_prefix):
    """Generate intensity + power heatmaps at the end of a calibration run.

    Writes:
      <out_dir>/<name>_aligned_intensity.png  (2x2 panel: aligned grid,
        aligned at actual positions, arrival, improvement %)
      <out_dir>/<name>_aligned_power.png      (log-scale power heatmap)

    Safe to call at any time — skips gracefully if matplotlib is missing
    or no pixels are calibrated yet.
    """
    if not HAVE_MPL:
        print("  matplotlib unavailable -- skipping heatmaps.")
        return

    calibrated = [p for p in pixels if p.get("calibrated_voltage_v") is not None]
    if not calibrated:
        print("  No calibrated pixels -- skipping heatmaps.")
        return

    try:
        from matplotlib.colors import LogNorm
        gs = SCAN_POINTS_PER_AXIS

        V_cal = np.full((gs, gs), np.nan)
        V_arr = np.full((gs, gs), np.nan)
        IMP   = np.full((gs, gs), np.nan)
        P     = np.full((gs, gs), np.nan)
        for p in pixels:
            i, j = p["i"], p["j"]
            if not (0 <= i < gs and 0 <= j < gs):
                continue
            if p.get("calibrated_voltage_v") is not None:
                V_cal[j, i] = p["calibrated_voltage_v"] * 1000.0
            if p.get("arrived_voltage_v") is not None:
                V_arr[j, i] = p["arrived_voltage_v"] * 1000.0
            if p.get("voltage_improvement_pct") is not None:
                IMP[j, i] = p["voltage_improvement_pct"]
            pw = p.get("calibrated_power_w")
            if pw is not None and pw > 0:
                P[j, i] = pw * 1e6

        xs = sorted({round(p["nominal_x_global"], 6) for p in pixels})
        ys = sorted({round(p["nominal_y_global"], 6) for p in pixels})
        xs_arr = np.array(xs)
        ys_arr = np.array(ys)
        dx = (xs_arr[1] - xs_arr[0]) / 2.0 if len(xs_arr) > 1 else 0.5
        dy = (ys_arr[1] - ys_arr[0]) / 2.0 if len(ys_arr) > 1 else 0.5
        extent = [xs_arr[0] - dx, xs_arr[-1] + dx,
                  ys_arr[0] - dy, ys_arr[-1] + dy]

        cx = np.array([p["calibrated_x_global"] for p in calibrated])
        cy = np.array([p["calibrated_y_global"] for p in calibrated])
        cv_mv = np.array([p["calibrated_voltage_v"] * 1000.0 for p in calibrated])

        all_v = np.concatenate([
            V_cal[~np.isnan(V_cal)].ravel(),
            V_arr[~np.isnan(V_arr)].ravel(),
        ])
        vmin = float(np.nanpercentile(all_v, 2)) if all_v.size else 0.0
        vmax = float(np.nanpercentile(all_v, 98)) if all_v.size else 1.0

        fig, axes = plt.subplots(2, 2, figsize=(15, 13))

        ax = axes[0, 0]
        im = ax.imshow(V_cal, origin='lower', extent=extent, aspect='equal',
                       cmap='viridis', interpolation='nearest',
                       vmin=vmin, vmax=vmax)
        ax.set_title(f"Aligned intensity (nominal grid)\n"
                     f"{len(calibrated)}/{len(pixels)} pixels calibrated",
                     fontsize=11, fontweight='bold')
        ax.set_xlabel("X global (mm)"); ax.set_ylabel("Y global (mm)")
        plt.colorbar(im, ax=ax, label="Voltage (mV)")
        # Mark low-signal pixels
        for p in pixels:
            v = p.get("calibrated_voltage_v")
            if v is not None and v * 1000.0 < 100.0:
                ax.plot(p["nominal_x_global"], p["nominal_y_global"],
                        'rx', markersize=9, markeredgewidth=2, zorder=5)
            elif v is None:
                ax.plot(p["nominal_x_global"], p["nominal_y_global"],
                        marker='s', markerfacecolor='none',
                        markeredgecolor='grey',
                        markersize=10, markeredgewidth=1.5, zorder=4)

        ax = axes[0, 1]
        sc = ax.scatter(cx, cy, c=cv_mv, s=110, cmap='viridis',
                        vmin=vmin, vmax=vmax,
                        edgecolors='black', linewidth=0.4)
        ax.set_title(f"Aligned intensity (actual positions)\n"
                     f"mean {cv_mv.mean():.1f} mV, max {cv_mv.max():.1f}, "
                     f"min {cv_mv.min():.1f}", fontsize=11, fontweight='bold')
        ax.set_xlabel("X global (mm)"); ax.set_ylabel("Y global (mm)")
        ax.set_xlim(extent[0], extent[1]); ax.set_ylim(extent[2], extent[3])
        ax.set_aspect('equal'); ax.grid(True, alpha=0.3)
        plt.colorbar(sc, ax=ax, label="Voltage (mV)")

        ax = axes[1, 0]
        im = ax.imshow(V_arr, origin='lower', extent=extent, aspect='equal',
                       cmap='viridis', interpolation='nearest',
                       vmin=vmin, vmax=vmax)
        ax.set_title("Arrival intensity (before alignment)",
                     fontsize=11, fontweight='bold')
        ax.set_xlabel("X global (mm)"); ax.set_ylabel("Y global (mm)")
        plt.colorbar(im, ax=ax, label="Voltage (mV)")

        ax = axes[1, 1]
        abs_max = float(np.nanmax(np.abs(IMP))) if np.any(~np.isnan(IMP)) else 1.0
        im = ax.imshow(IMP, origin='lower', extent=extent, aspect='equal',
                       cmap='RdYlGn', interpolation='nearest',
                       vmin=-abs_max, vmax=abs_max)
        mean_imp = float(np.nanmean(IMP)) if np.any(~np.isnan(IMP)) else 0.0
        ax.set_title(f"Alignment improvement (%)\nmean {mean_imp:+.1f}%",
                     fontsize=11, fontweight='bold')
        ax.set_xlabel("X global (mm)"); ax.set_ylabel("Y global (mm)")
        plt.colorbar(im, ax=ax, label="(V_cal - V_arr) / |V_arr|  (%)")

        fig.suptitle(f"BTO chip — {name_prefix} — transmission intensity",
                     fontsize=14, fontweight='bold', y=0.995)
        plt.tight_layout()

        out_main = os.path.join(out_dir, f"{name_prefix}_aligned_intensity.png")
        plt.savefig(out_main, dpi=150)
        plt.close(fig)
        print(f"  Heatmap saved: {out_main}")

        if np.any(np.isfinite(P)):
            fig, ax = plt.subplots(figsize=(8, 7))
            im = ax.imshow(P, origin='lower', extent=extent, aspect='equal',
                           cmap='inferno', interpolation='nearest',
                           norm=LogNorm(vmin=np.nanmin(P), vmax=np.nanmax(P)))
            ax.set_title(f"{name_prefix} — aligned optical power (log scale)",
                         fontsize=12, fontweight='bold')
            ax.set_xlabel("X global (mm)"); ax.set_ylabel("Y global (mm)")
            plt.colorbar(im, ax=ax, label="Power (µW)")
            plt.tight_layout()
            out_pow = os.path.join(out_dir, f"{name_prefix}_aligned_power.png")
            plt.savefig(out_pow, dpi=150)
            plt.close(fig)
            print(f"  Heatmap saved: {out_pow}")
    except Exception as e:
        print(f"  WARN: Heatmap generation failed: {e}")


def sanitize_run_name(name):
    """Allow alphanumerics, dot, underscore, dash. Collapse spaces to _."""
    name = re.sub(r'\s+', '_', name.strip())
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('_')
    return name


def list_existing_runs():
    """Find existing calibration runs under OUTDIR_ROOT.

    Returns a list of dicts: {name, path, n_calibrated, total, created}.
    Includes legacy-layout files at OUTDIR_ROOT/pixel_positions.json (named
    "(legacy root)") for backwards compatibility.
    """
    runs = []
    if not os.path.isdir(OUTDIR_ROOT):
        return runs

    # Legacy: JSON directly under OUTDIR_ROOT
    legacy_json = os.path.join(OUTDIR_ROOT, "pixel_positions.json")
    if os.path.isfile(legacy_json):
        try:
            with open(legacy_json) as f:
                d = json.load(f)
            runs.append({
                "name": "(legacy root)",
                "path": legacy_json,
                "n_calibrated": d.get("n_calibrated", 0),
                "total": len(d.get("pixels", [])),
                "created": d.get("created_utc", ""),
                "is_legacy": True,
            })
        except Exception:
            pass

    # Subdirectories with pixel_positions.json
    for entry in sorted(os.listdir(OUTDIR_ROOT)):
        sub = os.path.join(OUTDIR_ROOT, entry)
        if not os.path.isdir(sub):
            continue
        jpath = os.path.join(sub, "pixel_positions.json")
        if not os.path.isfile(jpath):
            continue
        try:
            with open(jpath) as f:
                d = json.load(f)
            runs.append({
                "name": entry,
                "path": jpath,
                "n_calibrated": d.get("n_calibrated", 0),
                "total": len(d.get("pixels", [])),
                "created": d.get("created_utc", ""),
                "is_legacy": False,
            })
        except Exception:
            pass
    return runs


def choose_run():
    """Interactive run selection at startup.

    Returns (run_name, mode) where:
      run_name is the folder name under OUTDIR_ROOT (or "" for legacy root)
      mode is one of: "new", "resume", "verify"
    """
    runs = list_existing_runs()

    print(f"\n{'='*W}")
    print("  SELECT CALIBRATION RUN")
    print(f"{'='*W}")

    if runs:
        print("\n  Existing runs in stage_calibration/:\n")
        for i, r in enumerate(runs, 1):
            date_str = r["created"][:19].replace("T", " ") if r["created"] else "?"
            tag = "  [legacy]" if r["is_legacy"] else ""
            print(f"    [{i}] {r['name']}{tag}")
            print(f"         {r['n_calibrated']}/{r['total']} calibrated  "
                  f"({date_str})")
    else:
        print("\n  (no existing runs found)")

    print()
    print("  Options:")
    if runs:
        print(f"    [1-{len(runs)}]       Resume / re-open that run "
              f"(continue calibrating)")
        print(f"    [v<num>]    VERIFY that run (mini-GS at each pixel, no edits)")
        print(f"                e.g.  v1   -> verify run [1]")
    print("    [n]         Start a NEW run (you'll type the name)")
    print("    [q]         Quit")
    print()

    while True:
        choice = input("  > ").strip().lower()
        if not choice:
            continue
        if choice == 'q':
            print("  Exiting.")
            sys.exit(0)
        if choice == 'n':
            while True:
                print()
                print("  " + "="*56)
                print("  >>> TYPE THE NAME FOR THIS NEW RUN <<<")
                print("  (this becomes the folder name, e.g. BTO_chip_A_run3)")
                print("  (data, images, plots all save under that folder)")
                print("  " + "="*56)
                raw = input("  Run name: ")
                name = sanitize_run_name(raw)
                if not name:
                    print("  Name cannot be empty. Try again.")
                    continue
                existing_dir = os.path.join(OUTDIR_ROOT, name)
                if any(r["name"] == name for r in runs) or os.path.isdir(existing_dir):
                    print(f"  WARNING: '{name}' already exists.")
                    over = input("  Overwrite existing run? [y/n]: ").strip().lower()
                    if over != 'y':
                        continue
                return name, "new"
        # Verify mode: "v1", "v2", ...
        if choice.startswith('v') and choice[1:].isdigit():
            idx = int(choice[1:])
            if 1 <= idx <= len(runs):
                r = runs[idx - 1]
                if r["n_calibrated"] == 0:
                    print(f"  Run '{r['name']}' has 0 calibrated pixels — "
                          f"nothing to verify.")
                    continue
                print(f"  VERIFY mode on run: {r['name']}")
                name = "" if r["is_legacy"] else r["name"]
                return name, "verify"
            print(f"  Must be v1..v{len(runs)}.")
            continue
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(runs):
                r = runs[idx - 1]
                print(f"  Resuming run: {r['name']}")
                name = "" if r["is_legacy"] else r["name"]
                return name, "resume"
            print(f"  Must be 1-{len(runs)}, or 'n' / 'q' / 'v<num>'.")
            continue
        print("  Unknown choice. Enter a number, 'v<num>', 'n', or 'q'.")


# ===================== Helpers =============================================

def show_progress(pixels, total):
    """Print calibration progress summary."""
    done = sum(1 for p in pixels if p["calibrated_x_motor"] is not None)
    skipped = sum(1 for p in pixels
                  if p["calibrated_x_motor"] is not None
                  and p.get("dx_um") == 0.0 and p.get("dy_um") == 0.0)
    adjusted = done - skipped
    remaining = total - done

    print(f"\n  {'─'*40}")
    print(f"  Progress: {done}/{total}  ({done*100//total if total else 0}%)")
    print(f"    Adjusted:  {adjusted}")
    print(f"    Skipped:   {skipped}  (kept nominal)")
    print(f"    Remaining: {remaining}")

    dx_vals = [p["dx_um"] for p in pixels
               if p.get("dx_um") is not None and p["dx_um"] != 0.0]
    dy_vals = [p["dy_um"] for p in pixels
               if p.get("dy_um") is not None and p["dy_um"] != 0.0]
    if dx_vals:
        print(f"    dX: {min(dx_vals):+.1f} to {max(dx_vals):+.1f} um  "
              f"(mean {np.mean(dx_vals):+.1f}, std {np.std(dx_vals):.1f})")
    if dy_vals:
        print(f"    dY: {min(dy_vals):+.1f} to {max(dy_vals):+.1f} um  "
              f"(mean {np.mean(dy_vals):+.1f}, std {np.std(dy_vals):.1f})")

    # Intensity stats
    arr_v = [p["arrived_voltage_v"] for p in pixels
             if p.get("arrived_voltage_v") is not None]
    cal_v = [p["calibrated_voltage_v"] for p in pixels
             if p.get("calibrated_voltage_v") is not None]
    if arr_v:
        print(f"    Arrival V:     {np.mean(arr_v)*1000:.2f} mV avg "
              f"(range {min(arr_v)*1000:.2f} - {max(arr_v)*1000:.2f})")
    if cal_v:
        print(f"    Calibrated V:  {np.mean(cal_v)*1000:.2f} mV avg "
              f"(range {min(cal_v)*1000:.2f} - {max(cal_v)*1000:.2f})")

    improv = [p["voltage_improvement_pct"] for p in pixels
              if p.get("voltage_improvement_pct") is not None]
    if improv:
        print(f"    V improvement: {np.mean(improv):+.1f}% avg "
              f"(range {min(improv):+.1f} - {max(improv):+.1f}%)")

    print(f"  {'─'*40}")


def record_arrival(pixel, arrived_x, arrived_y, mean_v, std_v, power_w, vdiv, img_name):
    """Store arrival data (before manual adjustment)."""
    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]

    pixel["arrived_x_motor"] = round(arrived_x, 6)
    pixel["arrived_y_motor"] = round(arrived_y, 6)
    pixel["arrival_error_x_um"] = round((arrived_x - nom_xm) * 1000.0, 1)
    pixel["arrival_error_y_um"] = round((arrived_y - nom_ym) * 1000.0, 1)
    pixel["arrived_voltage_v"] = round(mean_v, 6) if mean_v is not None else None
    pixel["arrived_voltage_std"] = round(std_v, 6) if std_v is not None else None
    pixel["arrived_power_w"] = power_w
    pixel["arrived_vdiv"] = vdiv
    pixel["arrived_image"] = img_name


def capture_calibrated(device_x, device_y, pixel, detector, live_view):
    """Settle + average position + read intensity + capture image at calibrated position."""
    print(f"  Settling...", end="", flush=True)
    wait_for_settle(device_x, device_y)
    print(f" averaging ({CAPTURE_AVERAGES} samples)...", end="", flush=True)
    avg_x, avg_y = read_motor_pos_averaged(device_x, device_y)
    print(f" done.")

    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]
    cap_xg = float(motor_to_global(PyDecimal(str(avg_x))))
    cap_yg = float(motor_to_global(PyDecimal(str(avg_y))))
    dx_um = (avg_x - nom_xm) * 1000.0
    dy_um = (avg_y - nom_ym) * 1000.0

    # Position data
    pixel["calibrated_x_motor"]  = round(avg_x, 6)
    pixel["calibrated_y_motor"]  = round(avg_y, 6)
    pixel["calibrated_x_global"] = round(cap_xg, 6)
    pixel["calibrated_y_global"] = round(cap_yg, 6)
    pixel["dx_um"] = round(dx_um, 1)
    pixel["dy_um"] = round(dy_um, 1)

    # Correction from arrived position
    arr_x = pixel.get("arrived_x_motor")
    arr_y = pixel.get("arrived_y_motor")
    if arr_x is not None and arr_y is not None:
        pixel["correction_x_um"] = round((avg_x - arr_x) * 1000.0, 1)
        pixel["correction_y_um"] = round((avg_y - arr_y) * 1000.0, 1)

    # Intensity at calibrated position
    mean_v, std_v, power_w, vdiv = None, None, None, None
    if detector is not None:
        mean_v, std_v, power_w, vdiv = detector.read_averaged()
    pixel["calibrated_voltage_v"] = round(mean_v, 6) if mean_v is not None else None
    pixel["calibrated_voltage_std"] = round(std_v, 6) if std_v is not None else None
    pixel["calibrated_power_w"] = power_w
    pixel["calibrated_vdiv"] = vdiv

    # Intensity improvement
    arr_v = pixel.get("arrived_voltage_v")
    if arr_v is not None and mean_v is not None and abs(arr_v) > 1e-9:
        pixel["voltage_improvement_pct"] = round((mean_v - arr_v) / abs(arr_v) * 100.0, 1)

    # Camera image from live view
    img_name = ""
    if live_view is not None and live_view.available:
        img_name = live_view.capture_image(pixel["pixel"], "calibrated")
    pixel["calibrated_image"] = img_name

    # Timestamp
    pixel["timestamp"] = datetime.now().isoformat()

    # Update live view status
    if live_view is not None:
        live_view.update(status='CAPTURED')

    # Display
    v_str = detector.format_reading(mean_v, power_w) if detector else "N/A"
    print(f"  >> CAPTURED  motor ({avg_x:.4f}, {avg_y:.4f})  "
          f"dX={dx_um:+.1f}  dY={dy_um:+.1f} um")
    if pixel.get("correction_x_um") is not None:
        print(f"     Correction from arrival: "
              f"({pixel['correction_x_um']:+.1f}, {pixel['correction_y_um']:+.1f}) um")
    print(f"     Intensity: {v_str}")
    if pixel.get("voltage_improvement_pct") is not None:
        print(f"     Improvement: {pixel['voltage_improvement_pct']:+.1f}% vs arrival")

    return avg_x, avg_y


def store_manual_calibrated(pixel, man_x, man_y, detector, live_view):
    """Store manually entered coordinates + read intensity + capture image."""
    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]
    cap_xg = float(motor_to_global(PyDecimal(str(man_x))))
    cap_yg = float(motor_to_global(PyDecimal(str(man_y))))
    dx_um = (man_x - nom_xm) * 1000.0
    dy_um = (man_y - nom_ym) * 1000.0

    pixel["calibrated_x_motor"]  = round(man_x, 6)
    pixel["calibrated_y_motor"]  = round(man_y, 6)
    pixel["calibrated_x_global"] = round(cap_xg, 6)
    pixel["calibrated_y_global"] = round(cap_yg, 6)
    pixel["dx_um"] = round(dx_um, 1)
    pixel["dy_um"] = round(dy_um, 1)

    arr_x = pixel.get("arrived_x_motor")
    arr_y = pixel.get("arrived_y_motor")
    if arr_x is not None and arr_y is not None:
        pixel["correction_x_um"] = round((man_x - arr_x) * 1000.0, 1)
        pixel["correction_y_um"] = round((man_y - arr_y) * 1000.0, 1)

    mean_v, std_v, power_w, vdiv = None, None, None, None
    if detector is not None:
        mean_v, std_v, power_w, vdiv = detector.read_averaged()
    pixel["calibrated_voltage_v"] = round(mean_v, 6) if mean_v is not None else None
    pixel["calibrated_voltage_std"] = round(std_v, 6) if std_v is not None else None
    pixel["calibrated_power_w"] = power_w
    pixel["calibrated_vdiv"] = vdiv

    arr_v = pixel.get("arrived_voltage_v")
    if arr_v is not None and mean_v is not None and abs(arr_v) > 1e-9:
        pixel["voltage_improvement_pct"] = round((mean_v - arr_v) / abs(arr_v) * 100.0, 1)

    img_name = ""
    if live_view is not None and live_view.available:
        img_name = live_view.capture_image(pixel["pixel"], "calibrated")
    pixel["calibrated_image"] = img_name
    pixel["timestamp"] = datetime.now().isoformat()

    v_str = detector.format_reading(mean_v, power_w) if detector else "N/A"
    print(f"  >> ENTERED   motor ({man_x:.4f}, {man_y:.4f})  "
          f"dX={dx_um:+.1f}  dY={dy_um:+.1f} um")
    print(f"     Intensity: {v_str}")


def clear_pixel_calibration(pixel):
    """Reset a pixel's calibrated data to None (keeps arrival data)."""
    for key in ("calibrated_x_motor", "calibrated_y_motor",
                "calibrated_x_global", "calibrated_y_global",
                "dx_um", "dy_um", "correction_x_um", "correction_y_um",
                "calibrated_voltage_v", "calibrated_voltage_std",
                "calibrated_power_w", "calibrated_vdiv", "calibrated_image",
                "voltage_improvement_pct",
                "align_method", "align_coarse_correction_x_um",
                "align_coarse_correction_y_um", "align_fine_n_evals",
                "timestamp"):
        pixel[key] = None


def clear_pixel_fully(pixel):
    """Reset ALL data for a pixel (arrival + calibration)."""
    for key in ("arrived_x_motor", "arrived_y_motor",
                "arrival_error_x_um", "arrival_error_y_um",
                "arrived_voltage_v", "arrived_voltage_std",
                "arrived_power_w", "arrived_vdiv", "arrived_image",
                "calibrated_x_motor", "calibrated_y_motor",
                "calibrated_x_global", "calibrated_y_global",
                "dx_um", "dy_um", "correction_x_um", "correction_y_um",
                "calibrated_voltage_v", "calibrated_voltage_std",
                "calibrated_power_w", "calibrated_vdiv", "calibrated_image",
                "voltage_improvement_pct",
                "align_method", "align_coarse_correction_x_um",
                "align_coarse_correction_y_um", "align_fine_n_evals",
                "timestamp"):
        pixel[key] = None


def read_and_display(device_x, device_y, nom_xm, nom_ym, detector=None):
    """Read current position + intensity, print it."""
    cur_x = read_motor_pos(device_x)
    cur_y = read_motor_pos(device_y)
    dx_um = (cur_x - nom_xm) * 1000.0
    dy_um = (cur_y - nom_ym) * 1000.0
    print(f"  Position:  X={cur_x:.4f}   Y={cur_y:.4f}   "
          f"dX={dx_um:+.1f}  dY={dy_um:+.1f} um")

    if detector is not None:
        mean_v, std_v, power_w, vdiv = detector.read_averaged(auto_scale=False)
        v_str = detector.format_reading(mean_v, power_w)
        vdiv_str = f"{vdiv*1000:.0f}mV/div" if vdiv else "?"
        print(f"  Intensity: {v_str}  [{vdiv_str}]")

    return cur_x, cur_y


# ==================== Non-blocking input helper ============================

def wait_for_input(live_view, prompt="  > "):
    """Wait for user input while keeping the live camera view updating.

    On Windows, cv2.imshow/waitKey MUST run on the main thread, so we run
    input() in a background thread and pump the display here.
    """
    if live_view is not None and live_view.available:
        live_view.get_input(prompt)
        while True:
            live_view.tick()  # Update camera display (~30fps)
            cmd = live_view.poll_input()
            if cmd is not None:
                return cmd.strip().lower()
    else:
        # No live view -- fall back to blocking input
        try:
            return input(prompt).strip().lower()
        except EOFError:
            return "q"


def spin_display(live_view, duration_s):
    """Keep the live view updating for a fixed duration (e.g. during settle)."""
    if live_view is None or not live_view.available:
        time.sleep(duration_s)
        return
    deadline = time.time() + duration_s
    while time.time() < deadline:
        live_view.tick()


def run_with_display(live_view, func, *args, **kwargs):
    """Run a blocking function in a background thread while keeping the
    camera live. Returns the function's return value.

    Exceptions raised by func are captured and re-raised in the caller's
    thread so try/except around run_with_display works as expected.
    Without this, a worker-thread exception would leave done.is_set() False
    forever and the main thread would hang instead of unwinding.
    """
    if live_view is None or not live_view.available:
        return func(*args, **kwargs)

    result = [None]
    exc = [None]
    done = threading.Event()

    def worker():
        try:
            result[0] = func(*args, **kwargs)
        except BaseException as e:
            exc[0] = e
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    while not done.is_set():
        live_view.tick()
    if exc[0] is not None:
        raise exc[0]
    return result[0]


VERIFY_RANGE_UM = 20.0   # mini-GS total range per axis (default ±10 um)
VERIFY_TOL_UM   = 1.0    # mini-GS tolerance


def mini_gs_verify(device_x, device_y, detector, live_view,
                   target_x, target_y,
                   range_um=VERIFY_RANGE_UM, tol_um=VERIFY_TOL_UM):
    """Fast tight golden-section verification around a calibrated point.

    Moves to (target_x, target_y), reads the intensity there, then runs GS
    over ±range_um/2 on each axis to confirm this is the peak.

    Returns dict with target/peak positions + voltages + deltas.
    """
    if live_view is not None:
        live_view.pause_scope_reads()

    # Fixed V/div (peaks ~few V, same scale used by alignment)
    if detector is not None:
        detector._set_vdiv(1.0)
        time.sleep(0.3)

    half_mm = range_um / 2000.0

    # Move to target and read v_at_target
    _safe_scan_move(device_x, target_x, "X-vrfy")
    _safe_scan_move(device_y, target_y, "Y-vrfy")
    time.sleep(ALIGN_SETTLE_S)
    v_at_target = _read_intensity(detector)

    # Mini GS on X
    best_x, _, n_x, _ = golden_section_search(
        device_x, detector,
        target_x - half_mm, target_x + half_mm,
        tol_um=tol_um, axis_label="X-vrfy")

    _safe_scan_move(device_x, best_x, "X")
    time.sleep(ALIGN_SETTLE_S)

    # Mini GS on Y
    best_y, _, n_y, _ = golden_section_search(
        device_y, detector,
        target_y - half_mm, target_y + half_mm,
        tol_um=tol_um, axis_label="Y-vrfy")

    _safe_scan_move(device_x, best_x, "X-final")
    _safe_scan_move(device_y, best_y, "Y-final")
    time.sleep(ALIGN_SETTLE_S)

    v_peak, _, _, _ = detector.read_averaged(auto_scale=False)
    v_peak = v_peak if v_peak is not None else 0.0

    if live_view is not None:
        live_view.resume_scope_reads()

    return {
        "target_x": target_x, "target_y": target_y,
        "peak_x": best_x, "peak_y": best_y,
        "v_at_target": v_at_target,
        "v_peak": v_peak,
        "dx_to_peak_um": round((best_x - target_x) * 1000.0, 2),
        "dy_to_peak_um": round((best_y - target_y) * 1000.0, 2),
        "n_evals": n_x + n_y + 2,
    }


def auto_verify_all(calibrated, device_x, device_y, detector, live_view,
                    outdir, range_um=VERIFY_RANGE_UM, tol_um=VERIFY_TOL_UM):
    """Sweep every calibrated pixel, run mini-GS, save CSV and plots.

    Writes stage_calibration/<run>/verification/verify_<TS>.csv
    and        stage_calibration/<run>/verification/verify_<TS>_plots.png
    """
    verifydir = os.path.join(outdir, "verification")
    os.makedirs(verifydir, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(verifydir, f"verify_{ts}.csv")
    png_path = os.path.join(verifydir, f"verify_{ts}_plots.png")

    n_total = len(calibrated)
    print(f"\n  {'='*60}")
    print(f"  AUTO-VERIFY: {n_total} pixels, mini-GS ±{range_um/2:.0f} µm "
          f"(tol {tol_um:.1f} µm)")
    print(f"  CSV : {csv_path}")
    print(f"  Plot: {png_path}")
    print(f"  Press Ctrl-C at any time (partial CSV is saved on the fly).")
    print(f"  {'='*60}")

    rows = []
    cols = ["pixel", "i", "j",
            "calib_x_motor", "calib_y_motor",
            "nominal_x_global", "nominal_y_global",
            "stored_v_mV",
            "arrival_x_motor", "arrival_y_motor",
            "arrival_err_x_um", "arrival_err_y_um",
            "v_at_target_mV",
            "peak_x_motor", "peak_y_motor",
            "peak_v_mV",
            "dx_to_peak_um", "dy_to_peak_um",
            "v_peak_vs_target_pct",
            "v_target_vs_stored_pct",
            "timestamp"]

    f = open(csv_path, "w", newline="")
    writer = csv.writer(f)
    writer.writerow(cols)
    f.flush()

    try:
        for k, p in enumerate(calibrated):
            pix = p["pixel"]
            tx = p["calibrated_x_motor"]
            ty = p["calibrated_y_motor"]
            stored_v = p.get("calibrated_voltage_v") or 0.0

            print(f"\n  [{k+1}/{n_total}] Pixel {pix:03d} -> "
                  f"({tx:.4f}, {ty:.4f}) mm")

            if live_view is not None:
                live_view.update(pixel=pix, total=n_total, status="VERIFY")

            # Backlash-compensated move to calibrated position
            move_to_motor_with_backlash(device_x, tx, "X")
            move_to_motor_with_backlash(device_y, ty, "Y")
            time.sleep(SETTLE_TIME_S)
            ax, ay = read_motor_pos_averaged(device_x, device_y)
            arr_err_x = (ax - tx) * 1000.0
            arr_err_y = (ay - ty) * 1000.0

            # Mini GS around it
            result = mini_gs_verify(device_x, device_y, detector, live_view,
                                    tx, ty, range_um=range_um, tol_um=tol_um)

            v_target = result["v_at_target"]
            v_peak   = result["v_peak"]
            vtp = ((v_peak - v_target) / abs(v_target) * 100.0) if v_target else 0.0
            vts = ((v_target - stored_v) / abs(stored_v) * 100.0) if stored_v else 0.0

            row = [pix, p["i"], p["j"],
                   tx, ty,
                   p.get("nominal_x_global", ""), p.get("nominal_y_global", ""),
                   round(stored_v * 1000.0, 3),
                   round(ax, 6), round(ay, 6),
                   round(arr_err_x, 2), round(arr_err_y, 2),
                   round(v_target * 1000.0, 3),
                   round(result["peak_x"], 6), round(result["peak_y"], 6),
                   round(v_peak * 1000.0, 3),
                   result["dx_to_peak_um"], result["dy_to_peak_um"],
                   round(vtp, 2), round(vts, 2),
                   datetime.now().isoformat()]
            writer.writerow(row)
            f.flush()
            rows.append(dict(zip(cols, row)))

            flag = ""
            if abs(result["dx_to_peak_um"]) > 3.0 or abs(result["dy_to_peak_um"]) > 3.0:
                flag += " [POS_OFFSET>3µm]"
            if abs(vts) > 20.0:
                flag += " [V_DRIFT>20%]"

            print(f"    arrival err ({arr_err_x:+.1f}, {arr_err_y:+.1f}) um")
            print(f"    V@target {_fmt_v(v_target)}  vs stored "
                  f"{_fmt_v(stored_v)}  ({vts:+.1f}%)")
            print(f"    peak@ ({result['dx_to_peak_um']:+.1f}, "
                  f"{result['dy_to_peak_um']:+.1f}) um, "
                  f"V={_fmt_v(v_peak)}  ({vtp:+.1f}% above target){flag}")

    except KeyboardInterrupt:
        print(f"\n  Auto-verify interrupted. Partial CSV saved ({len(rows)} rows).")
    finally:
        f.close()

    if rows:
        try:
            plot_verification_results(rows, png_path)
        except Exception as e:
            print(f"  WARN: plot failed: {e}")

    # Summary
    if rows:
        dx = np.array([r["dx_to_peak_um"] for r in rows])
        dy = np.array([r["dy_to_peak_um"] for r in rows])
        dist = np.hypot(dx, dy)
        vts = np.array([r["v_target_vs_stored_pct"] for r in rows])
        print(f"\n  {'='*60}")
        print(f"  AUTO-VERIFY SUMMARY ({len(rows)} pixels)")
        print(f"  {'='*60}")
        print(f"  Position offset (calib -> mini-GS peak):")
        print(f"    dx  mean {dx.mean():+.2f}  std {dx.std():.2f}  "
              f"range [{dx.min():+.1f}, {dx.max():+.1f}] um")
        print(f"    dy  mean {dy.mean():+.2f}  std {dy.std():.2f}  "
              f"range [{dy.min():+.1f}, {dy.max():+.1f}] um")
        print(f"    |d| mean {dist.mean():.2f}  max {dist.max():.2f} um")
        print(f"  V drift at calibrated point vs stored:")
        print(f"    mean {vts.mean():+.1f}%  range "
              f"[{vts.min():+.1f}, {vts.max():+.1f}]%")
        bad_pos = [r["pixel"] for r in rows
                   if abs(r["dx_to_peak_um"]) > 3 or abs(r["dy_to_peak_um"]) > 3]
        bad_v   = [r["pixel"] for r in rows
                   if abs(r["v_target_vs_stored_pct"]) > 20]
        if bad_pos:
            print(f"  Pixels with peak offset >3 um: {bad_pos}")
        if bad_v:
            print(f"  Pixels with V drift >20%: {bad_v}")
        print(f"  {'='*60}")

    return csv_path


def plot_verification_results(rows, out_png):
    """Render offset + intensity-ratio heatmaps from an auto-verify sweep."""
    if not HAVE_MPL or not rows:
        return

    # Build arrays keyed by (i, j)
    pix_i = np.array([r["i"] for r in rows])
    pix_j = np.array([r["j"] for r in rows])
    gs = int(max(pix_i.max(), pix_j.max())) + 1

    DX = np.full((gs, gs), np.nan)
    DY = np.full((gs, gs), np.nan)
    DIST = np.full((gs, gs), np.nan)
    VR = np.full((gs, gs), np.nan)   # V drift %
    VT = np.full((gs, gs), np.nan)   # V at target mV
    for r in rows:
        i, j = r["i"], r["j"]
        DX[j, i] = r["dx_to_peak_um"]
        DY[j, i] = r["dy_to_peak_um"]
        DIST[j, i] = float(np.hypot(r["dx_to_peak_um"], r["dy_to_peak_um"]))
        VR[j, i] = r["v_target_vs_stored_pct"]
        VT[j, i] = r["v_at_target_mV"]

    fig, axes = plt.subplots(2, 3, figsize=(17, 10))

    def _heat(ax, data, title, cmap, label, diverge=False):
        finite = data[np.isfinite(data)]
        if finite.size == 0:
            ax.set_title(f"{title} (no data)")
            return
        if diverge:
            lim = float(np.nanmax(np.abs(finite)))
            vmin, vmax = -lim, lim
        else:
            vmin, vmax = float(np.nanmin(finite)), float(np.nanmax(finite))
        im = ax.imshow(data, origin='lower', cmap=cmap, vmin=vmin, vmax=vmax,
                       interpolation='nearest')
        ax.set_title(title, fontsize=11, fontweight='bold')
        ax.set_xlabel("column i")
        ax.set_ylabel("row j")
        plt.colorbar(im, ax=ax, label=label)

    _heat(axes[0, 0], DX, "dX to peak (µm)", 'RdBu_r', "µm", diverge=True)
    _heat(axes[0, 1], DY, "dY to peak (µm)", 'RdBu_r', "µm", diverge=True)
    _heat(axes[0, 2], DIST, "|offset to peak| (µm)", 'magma', "µm")
    _heat(axes[1, 0], VR, "V drift: (V_now - V_stored)/|V_stored| (%)",
          'RdYlGn', "%", diverge=True)
    _heat(axes[1, 1], VT, "V at target, this run (mV)", 'viridis', "mV")

    # Scatter of dx vs dy
    ax = axes[1, 2]
    dxs = np.array([r["dx_to_peak_um"] for r in rows])
    dys = np.array([r["dy_to_peak_um"] for r in rows])
    ax.scatter(dxs, dys, s=24, alpha=0.7)
    ax.axhline(0, color='k', lw=0.5)
    ax.axvline(0, color='k', lw=0.5)
    ax.set_xlabel("dx to peak (µm)")
    ax.set_ylabel("dy to peak (µm)")
    ax.set_title(f"Peak-offset scatter  "
                 f"(mean |d| {np.hypot(dxs, dys).mean():.2f} µm)",
                 fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.set_aspect('equal', adjustable='box')

    fig.suptitle(f"Calibration verification — {len(rows)} pixels",
                 fontsize=13, fontweight='bold')
    plt.tight_layout()
    plt.savefig(out_png, dpi=130)
    plt.close(fig)
    print(f"  Plot saved: {out_png}")


def test_calibrated_positions(device_x, device_y, pixels,
                              detector=None, live_view=None):
    """Step through calibrated positions, verify intensity + peak location.

    Commands:
      [Enter]  Move to next pixel, read position + intensity
      [v]      Mini-GS verify at current pixel (±10 µm)
      [a]      Auto-verify ALL pixels (writes CSV + plots)
      [g]      Goto a specific pixel number
      [q]      Quit test mode
    """
    calibrated = [p for p in pixels if p.get("calibrated_x_motor") is not None]
    if not calibrated:
        print("  No calibrated pixels to test.")
        return

    pix_to_idx = {p["pixel"]: i for i, p in enumerate(calibrated)}
    total = len(calibrated)
    print(f"\n  {'='*60}")
    print(f"  TEST / VERIFY MODE: {total} calibrated pixels")
    print(f"  {'='*60}")
    print("  [Enter] next  |  [v] mini-GS verify  |  [a] auto-verify all  |  "
          "[g] goto  |  [q] quit")

    idx = 0
    while idx < total:
        p = calibrated[idx]
        pix = p["pixel"]
        tx = p["calibrated_x_motor"]
        ty = p["calibrated_y_motor"]
        stored_v = p.get("calibrated_voltage_v") or 0.0
        print(f"\n  Pixel {pix:03d} ({idx+1}/{total}) -> target "
              f"X={tx:.4f}  Y={ty:.4f} mm   stored V={_fmt_v(stored_v)}")
        if live_view is not None:
            live_view.update(pixel=pix, total=total, status="TEST")

        run_with_display(live_view, move_to_motor_with_backlash, device_x, tx, "X")
        run_with_display(live_view, move_to_motor_with_backlash, device_y, ty, "Y")
        spin_display(live_view, SETTLE_TIME_S)

        ax, ay = read_motor_pos_averaged(
            device_x, device_y, n=CAPTURE_AVERAGES, delay=CAPTURE_AVG_DELAY_S)
        err_x = (ax - tx) * 1000.0
        err_y = (ay - ty) * 1000.0
        print(f"  Actual: X={ax:.4f}  Y={ay:.4f} mm "
              f"(err {err_x:+.1f}, {err_y:+.1f} um)")

        if detector is not None:
            if live_view is not None:
                live_view.pause_scope_reads()
            mean_v, std_v, power_w, vdiv = detector.read_averaged()
            if live_view is not None:
                live_view.resume_scope_reads()
            if mean_v is not None:
                drift = ((mean_v - stored_v) / abs(stored_v) * 100.0) if stored_v else 0.0
                print(f"  Scope: {mean_v*1000:.2f} mV  "
                      f"(stored {_fmt_v(stored_v)}, drift {drift:+.1f}%)")

        cmd = wait_for_input(live_view, "  > ").strip().lower()
        if cmd == 'q':
            print("  Exiting test mode.")
            break

        if cmd == 'v':
            print(f"  Running mini-GS verify (±{VERIFY_RANGE_UM/2:.0f} µm)...")
            def _do_verify():
                return mini_gs_verify(device_x, device_y, detector, live_view,
                                      tx, ty)
            result = run_with_display(live_view, _do_verify)
            if result is not None:
                v_target = result["v_at_target"]
                v_peak   = result["v_peak"]
                vtp = ((v_peak - v_target) / abs(v_target) * 100.0) if v_target else 0.0
                vts = ((v_target - stored_v) / abs(stored_v) * 100.0) if stored_v else 0.0
                print(f"  >> peak at ({result['peak_x']:.4f}, "
                      f"{result['peak_y']:.4f}) mm")
                print(f"     offset from calib: "
                      f"({result['dx_to_peak_um']:+.1f}, "
                      f"{result['dy_to_peak_um']:+.1f}) µm")
                print(f"     V target {_fmt_v(v_target)}  "
                      f"(vs stored, {vts:+.1f}%)")
                print(f"     V peak   {_fmt_v(v_peak)}  ({vtp:+.1f}% above target)")
            continue

        if cmd == 'a':
            confirm = wait_for_input(live_view,
                                     f"  Auto-verify ALL {total} pixels? [y/N] > "
                                     ).strip().lower()
            if confirm != 'y':
                print("  Cancelled.")
                continue
            auto_verify_all(calibrated, device_x, device_y,
                            detector, live_view, OUTDIR)
            print("\n  Press Enter to continue stepping, or q to quit.")
            continue

        if cmd == 'g':
            num_str = wait_for_input(live_view,
                                     f"  Go to pixel (1-{max(pix_to_idx)}): ")
            try:
                num = int(num_str)
                if num in pix_to_idx:
                    idx = pix_to_idx[num]
                    continue
                print("  Invalid pixel number.")
            except ValueError:
                print("  Invalid input.")
            continue

        idx += 1


# ==================== Interactive calibration ==============================

def calibrate_pixel(device_x, device_y, pixel, total, n_calibrated, pixels,
                    detector=None, live_view=None):
    """Interactive calibration for one pixel.

    Returns:
        ('captured', None)
        ('skipped', None)
        ('redo', None)
        ('goto', pixel_index)   -- 0-based index
        ('quit', None)
    """
    pix = pixel["pixel"]
    i, j = pixel["i"], pixel["j"]
    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]
    nom_xg = pixel["nominal_x_global"]
    nom_yg = pixel["nominal_y_global"]

    # Update live view with current pixel info
    if live_view is not None:
        live_view.update(pixel=pix, total=total, n_calibrated=n_calibrated,
                         nom_x=nom_xm, nom_y=nom_ym,
                         cur_x=nom_xm, cur_y=nom_ym,
                         err_x_um=0.0, err_y_um=0.0,
                         arrival_v=None, status='MOVING')

    # ── Header ──
    print(f"\n{'='*W}")
    print(f"  Pixel {pix}/{total}  [row {j}, col {i}]  |  "
          f"Calibrated: {n_calibrated}/{total}")
    print(f"  Nominal:  global ({nom_xg:+.4f}, {nom_yg:+.4f}) mm")
    print(f"            motor  ({nom_xm:.4f}, {nom_ym:.4f}) mm")
    print(f"{'='*W}")

    # ── Auto-move with backlash compensation (camera stays live) ──
    def _do_move():
        move_to_motor_with_backlash(device_x, nom_xm, "X")
        move_to_motor_with_backlash(device_y, nom_ym, "Y")
        set_velocity_mode(device_x, label="X")
        set_velocity_mode(device_y, label="Y")

    print(f"  Moving to nominal position...")
    # Run move in background, update position overlay while it travels
    done_evt = threading.Event()
    def _move_worker():
        _do_move()
        done_evt.set()
    threading.Thread(target=_move_worker, daemon=True).start()
    while not done_evt.is_set():
        if live_view is not None and live_view.available:
            cx = read_motor_pos(device_x)
            cy = read_motor_pos(device_y)
            live_view.update(cur_x=cx, cur_y=cy,
                             err_x_um=(cx - nom_xm) * 1000.0,
                             err_y_um=(cy - nom_ym) * 1000.0)
            live_view.tick()
        else:
            time.sleep(0.03)

    # ── Record arrival data (camera stays live during settle) ──
    print(f"  Settling...")
    run_with_display(live_view, wait_for_settle, device_x, device_y)
    ax, ay = read_motor_pos_averaged(device_x, device_y)
    err_x = (ax - nom_xm) * 1000.0
    err_y = (ay - nom_ym) * 1000.0
    print(f"  Arrived:   X={ax:.4f}   Y={ay:.4f}   "
          f"err=({err_x:+.1f}, {err_y:+.1f}) um")

    # Update live view with arrival position
    if live_view is not None:
        live_view.update(cur_x=ax, cur_y=ay,
                         err_x_um=err_x, err_y_um=err_y,
                         status='ADJUSTING')

    # Read intensity at arrival
    arr_v, arr_std, arr_pw, arr_vdiv = None, None, None, None
    if detector is not None:
        arr_v, arr_std, arr_pw, arr_vdiv = detector.read_averaged()
        v_str = detector.format_reading(arr_v, arr_pw)
        vdiv_str = f"{arr_vdiv*1000:.0f}mV/div" if arr_vdiv else "?"
        print(f"  Arrival intensity: {v_str}  [{vdiv_str}]")

    # Update live view with arrival intensity (for improvement tracking)
    if live_view is not None:
        live_view.update(arrival_v=arr_v)

    # Capture arrival image from live view
    arr_img = ""
    if live_view is not None and live_view.available:
        arr_img = live_view.capture_image(pix, "arrival")

    # Store arrival data
    record_arrival(pixel, ax, ay, arr_v, arr_std, arr_pw, arr_vdiv, arr_img)

    # ── Command loop (non-blocking: live view updates while waiting) ──
    while True:
        print(f"{'─'*W}")
        cur_x, cur_y = read_and_display(device_x, device_y, nom_xm, nom_ym, detector)

        # Update live view with current position
        if live_view is not None:
            live_view.update(cur_x=cur_x, cur_y=cur_y,
                             err_x_um=(cur_x - nom_xm) * 1000.0,
                             err_y_um=(cur_y - nom_ym) * 1000.0)

        print(f"  [Enter]=capture  [hc]=HILL CLIMB  [fp]=fast peak  [gs]=golden  "
              f"[a]=full scan  [cv]=CV-guided  [fa]=full auto")
        print(f"  [e]=enter coords  [s]=skip  [r]=redo  [g]=goto  "
              f"[m]=calib pixel-stage  [i]=intensity  [f]=refresh  [p]=progress  [q]=quit")

        cmd = wait_for_input(live_view)

        # ── CAPTURE (Enter or c) ──
        if cmd in ('', 'c'):
            capture_calibrated(device_x, device_y, pixel, detector, live_view)
            if pixel.get("align_method") is None:
                pixel["align_method"] = "manual"
            return ('captured', None)

        # ── MANUAL ENTRY ──
        elif cmd == 'e':
            print(f"  Enter motor coordinates (blank = use current reading above)")
            try:
                x_in = wait_for_input(live_view, "    X motor [mm]: ")
                y_in = wait_for_input(live_view, "    Y motor [mm]: ")
                man_x = float(x_in) if x_in else cur_x
                man_y = float(y_in) if y_in else cur_y
                store_manual_calibrated(pixel, man_x, man_y, detector, live_view)
                if pixel.get("align_method") is None:
                    pixel["align_method"] = "manual"
                return ('captured', None)
            except ValueError as exc:
                print(f"  Invalid number ({exc}). Try again.")
                continue

        # ── SKIP ──
        elif cmd == 's':
            pixel["calibrated_x_motor"]  = nom_xm
            pixel["calibrated_y_motor"]  = nom_ym
            pixel["calibrated_x_global"] = nom_xg
            pixel["calibrated_y_global"] = nom_yg
            pixel["dx_um"] = 0.0
            pixel["dy_um"] = 0.0
            pixel["correction_x_um"] = round((nom_xm - ax) * 1000.0, 1) if ax else 0.0
            pixel["correction_y_um"] = round((nom_ym - ay) * 1000.0, 1) if ay else 0.0
            pixel["calibrated_voltage_v"] = pixel.get("arrived_voltage_v")
            pixel["calibrated_voltage_std"] = pixel.get("arrived_voltage_std")
            pixel["calibrated_power_w"] = pixel.get("arrived_power_w")
            pixel["calibrated_vdiv"] = pixel.get("arrived_vdiv")
            pixel["voltage_improvement_pct"] = 0.0
            pixel["timestamp"] = datetime.now().isoformat()
            print(f"  >> SKIPPED (nominal position kept)")
            return ('skipped', None)

        # ── INTENSITY ONLY (for monitoring while adjusting) ──
        elif cmd == 'i':
            if detector is not None:
                print(f"  Reading intensity (5 samples)...")
                mean_v, std_v, power_w, vdiv = detector.read_averaged()
                v_str = detector.format_reading(mean_v, power_w)
                vdiv_str = f"{vdiv*1000:.0f}mV/div" if vdiv else "?"
                rsd = abs(std_v / mean_v) * 100 if mean_v and std_v else 0
                print(f"  >> {v_str}  [{vdiv_str}]  RSD={rsd:.1f}%")
                if arr_v is not None and mean_v is not None and abs(arr_v) > 1e-9:
                    improv = (mean_v - arr_v) / abs(arr_v) * 100.0
                    print(f"     vs arrival: {improv:+.1f}%")
            else:
                print(f"  Scope not connected")
            continue

        # ── AUTO-ALIGN ──
        elif cmd == 'a':
            if detector is None:
                print("  Auto-align requires oscilloscope. Not available.")
                continue

            def _do_auto_align():
                return auto_align(device_x, device_y, detector, live_view)

            result = run_with_display(live_view, _do_auto_align)

            # Store alignment metadata + diagnostics
            if result is not None:
                pixel["align_method"] = "auto"
                pixel["align_fine_n_evals"] = result.get("n_evals")
                cc = result.get("coarse_correction_um")
                if cc:
                    pixel["align_coarse_correction_x_um"] = cc[0]
                    pixel["align_coarse_correction_y_um"] = cc[1]

                bx = result["best_x"]
                by = result["best_y"]
                if live_view is not None:
                    live_view.update(
                        cur_x=bx, cur_y=by,
                        err_x_um=(bx - nom_xm) * 1000.0,
                        err_y_um=(by - nom_ym) * 1000.0,
                        status='ADJUSTING',
                    )

                save_alignment_log(pix, result)
                plot_alignment_diagnostic(pix, result)

            print(f"  Press Enter to capture, or adjust further.")
            continue

        # ── HILL CLIMB (fastest: adaptive gradient + parabolic) ──
        elif cmd == 'hc':
            if detector is None:
                print("  Hill climb requires oscilloscope. Not available.")
                continue

            def _do_hc_align():
                return hill_climb_align(device_x, device_y, detector, live_view)

            result = run_with_display(live_view, _do_hc_align)

            if result is not None:
                pixel["align_method"] = "hill_climb"
                pixel["align_fine_n_evals"] = result.get("n_evals")
                cc = result.get("coarse_correction_um")
                if cc:
                    pixel["align_coarse_correction_x_um"] = cc[0]
                    pixel["align_coarse_correction_y_um"] = cc[1]
                bx = result["best_x"]; by = result["best_y"]
                if live_view is not None:
                    live_view.update(
                        cur_x=bx, cur_y=by,
                        err_x_um=(bx - nom_xm) * 1000.0,
                        err_y_um=(by - nom_ym) * 1000.0,
                        status='ADJUSTING',
                    )
                save_alignment_log(pix, result)
                plot_alignment_diagnostic(pix, result)
            print(f"  Press Enter to capture, or adjust further.")
            continue

        # ── FAST PEAK (coarse GS + parabolic, ~2.5x faster than gs) ──
        elif cmd == 'fp':
            if detector is None:
                print("  Fast peak requires oscilloscope. Not available.")
                continue

            def _do_fp_align():
                return fast_peak_align(device_x, device_y, detector, live_view)

            result = run_with_display(live_view, _do_fp_align)

            if result is not None:
                pixel["align_method"] = "fast_peak"
                pixel["align_fine_n_evals"] = result.get("n_evals")
                cc = result.get("coarse_correction_um")
                if cc:
                    pixel["align_coarse_correction_x_um"] = cc[0]
                    pixel["align_coarse_correction_y_um"] = cc[1]
                bx = result["best_x"]; by = result["best_y"]
                if live_view is not None:
                    live_view.update(
                        cur_x=bx, cur_y=by,
                        err_x_um=(bx - nom_xm) * 1000.0,
                        err_y_um=(by - nom_ym) * 1000.0,
                        status='ADJUSTING',
                    )
                save_alignment_log(pix, result)
                plot_alignment_diagnostic(pix, result)
            print(f"  Press Enter to capture, or adjust further.")
            continue

        # ── GOLDEN SECTION SEARCH ──
        elif cmd == 'gs':
            if detector is None:
                print("  Golden section requires oscilloscope. Not available.")
                continue

            def _do_gs_align():
                return golden_section_align(device_x, device_y, detector, live_view)

            result = run_with_display(live_view, _do_gs_align)

            if result is not None:
                pixel["align_method"] = "golden_section"
                pixel["align_fine_n_evals"] = result.get("n_evals")
                cc = result.get("coarse_correction_um")
                if cc:
                    pixel["align_coarse_correction_x_um"] = cc[0]
                    pixel["align_coarse_correction_y_um"] = cc[1]

                bx = result["best_x"]
                by = result["best_y"]
                if live_view is not None:
                    live_view.update(
                        cur_x=bx, cur_y=by,
                        err_x_um=(bx - nom_xm) * 1000.0,
                        err_y_um=(by - nom_ym) * 1000.0,
                        status='ADJUSTING',
                    )

                save_alignment_log(pix, result)
                plot_alignment_diagnostic(pix, result)

            print(f"  Press Enter to capture, or adjust further.")
            continue

        # ── CV-GUIDED ALIGNMENT ──
        elif cmd == 'cv':
            if detector is None:
                print("  CV-guided alignment requires oscilloscope. Not available.")
                continue

            def _do_cv_align():
                return cv_guided_align(device_x, device_y, detector,
                                       live_view, pixel_num=pix)

            result = run_with_display(live_view, _do_cv_align)

            if result is not None:
                pixel["align_method"] = result.get("method", "cv_guided_gs")
                pixel["align_fine_n_evals"] = result.get("n_evals")
                cc = result.get("coarse_correction_um")
                if cc:
                    pixel["align_coarse_correction_x_um"] = cc[0]
                    pixel["align_coarse_correction_y_um"] = cc[1]

                bx = result["best_x"]
                by = result["best_y"]
                if live_view is not None:
                    live_view.update(
                        cur_x=bx, cur_y=by,
                        err_x_um=(bx - nom_xm) * 1000.0,
                        err_y_um=(by - nom_ym) * 1000.0,
                        status='ADJUSTING',
                    )

                save_alignment_log(pix, result)
                plot_alignment_diagnostic(pix, result)

            print(f"  Press Enter to capture, or adjust further.")
            continue

        # ── FULL AUTO ──
        elif cmd == 'fa':
            if detector is None:
                print("  Full auto requires oscilloscope. Not available.")
                continue
            print("  Starting FULL AUTO from current pixel onwards...")
            print("  Press Ctrl-C to stop at any time (progress auto-saved).")

            # Find current pixel index in the list
            cur_idx = next((i for i, p in enumerate(pixels)
                            if p["pixel"] == pixel["pixel"]), 0)

            try:
                def _do_full_auto():
                    return full_auto_calibrate(
                        device_x, device_y, pixels, total, cur_idx,
                        detector, live_view)
                n_done = run_with_display(live_view, _do_full_auto)
            except KeyboardInterrupt:
                print(f"\n  Full auto interrupted. Progress saved.")
                n_done = sum(1 for p in pixels
                             if p.get("calibrated_x_motor") is not None)
                save_json(pixels, n_done)
                save_csv(pixels)

            # Return quit to exit the manual loop since full auto handles everything
            return ('quit', None)

        # ── PIXEL-TO-STAGE CALIBRATION ──
        elif cmd == 'm':
            if not (live_view and live_view.available):
                print("  Camera required for pixel-to-stage calibration.")
                continue

            def _do_calib():
                return calibrate_pixel_to_stage(device_x, device_y, live_view)

            result = run_with_display(live_view, _do_calib)
            if result is not None:
                print("  Pixel-to-stage calibration saved successfully.")
            else:
                print("  Pixel-to-stage calibration failed.")
            continue

        # ── REDO ──
        elif cmd == 'r':
            print(f"  >> REDO previous pixel")
            return ('redo', None)

        # ── GOTO ──
        elif cmd == 'g':
            try:
                num_str = wait_for_input(live_view, f"  Go to pixel (1-{total}): ")
                num = int(num_str)
                if 1 <= num <= total:
                    return ('goto', num - 1)
                print(f"  Must be 1-{total}.")
            except (ValueError, EOFError):
                print(f"  Invalid number.")
            continue

        # ── REFRESH ──
        elif cmd == 'f':
            continue

        # ── PROGRESS ──
        elif cmd == 'p':
            show_progress(pixels, total)
            continue

        # ── QUIT ──
        elif cmd == 'q':
            print(f"  >> QUIT requested")
            return ('quit', None)

        else:
            print(f"  Unknown command '{cmd}'. Try again.")
            continue


# ============================= MAIN =======================================

def main():
    print(f"{'='*W}")
    print(f"  STAGE POSITION CALIBRATION TOOL")
    print(f"  (with oscilloscope + camera for ML training data)")
    print(f"{'='*W}")
    print(f"  Grid:        {SCAN_POINTS_PER_AXIS}x{SCAN_POINTS_PER_AXIS} = "
          f"{SCAN_POINTS_PER_AXIS**2} pixels")
    print(f"  Scan area:   {SCAN_SIZE_MM}x{SCAN_SIZE_MM} mm")
    print(f"  Stage center: {STAGE_CENTER_MM} mm (motor coords)")
    print(f"  Backlash:    {BACKLASH_MM} mm compensation")
    print(f"  Capture:     {CAPTURE_AVERAGES} samples averaged, "
          f"{SETTLE_TIME_S}s settle, "
          f"{SETTLE_TOLERANCE_MM*1000:.1f}um tolerance")
    print(f"  Scope:       {SCOPE_VISA}")
    print(f"  Detector:    PDA30B2 @ {PDA_GAIN_DB}dB {PDA_LOAD}")
    print(f"  Camera:      {'cv2 available' if HAVE_CAMERA else 'NOT AVAILABLE'}")
    print(f"{'='*W}")

    # ---- Pick (or create) a named calibration run ----
    # Each run lives in its own folder: stage_calibration/<run_name>/
    # so starting fresh never overwrites a previous run.
    run_name, run_mode = choose_run()  # mode: "new" | "resume" | "verify"
    if run_name:
        set_run_dir(run_name)
    print(f"\n  Output folder: {os.path.abspath(OUTDIR)}/")
    print(f"  Mode: {run_mode.upper()}")

    # ---- Build nominal grid ----
    pixels = build_grid()
    total = len(pixels)
    start_idx = 0
    n_calibrated = 0
    test_only = False

    # ---- Load existing data for resume/verify ----
    if run_mode in ("resume", "verify"):
        existing = load_existing()
        if existing is None:
            print(f"  WARN: No existing calibration found in this run.")
            if run_mode == "verify":
                print(f"  Cannot verify an empty run. Exiting.")
                return
        else:
            saved_pixels, saved_n = existing
            print(f"\n  Loaded existing calibration: {saved_n}/{total} pixels.")
            for sp in saved_pixels:
                idx = sp["pixel"] - 1
                if (0 <= idx < total
                        and sp.get("calibrated_x_motor") is not None):
                    pixels[idx] = sp
                    n_calibrated += 1

            if run_mode == "verify":
                test_only = True
                print(f"  Entering VERIFY mode on {n_calibrated} calibrated pixels.")
            else:  # resume
                for k, p in enumerate(pixels):
                    if p["calibrated_x_motor"] is None:
                        start_idx = k
                        break
                else:
                    print(f"\n  All {total} pixels already calibrated!")
                    save_csv(pixels)
                    print(f"  CSV updated: {CSV_FILE}")
                    choice = input("  Test calibrated positions now? [y/n] > ").strip().lower()
                    if choice != 'y':
                        return
                    test_only = True
                if not test_only:
                    print(f"  Resuming from pixel {start_idx + 1} "
                          f"({n_calibrated} already done)")
    else:
        print(f"\n  Starting fresh run: {run_name}")

    # ---- Initialize hardware ----
    print(f"\n  Initializing Kinesis...")
    DeviceManagerCLI.BuildDeviceList()

    print(f"  X axis:")
    device_x = initialize_device(STAGE_SERIAL_X)
    print(f"  Y axis:")
    device_y = initialize_device(STAGE_SERIAL_Y)

    print(f"\n  Homing both axes...")
    home_device(device_x, STAGE_SERIAL_X)
    home_device(device_y, STAGE_SERIAL_Y)

    # ---- Verify stage works: move to centre (0,0 global = 12.5mm motor) ----
    print(f"\n  Verifying stage: moving to centre (12.5, 12.5)...")
    cx = safe_move_to(device_x, 12.5, "X")
    cy = safe_move_to(device_y, 12.5, "Y")
    print(f"  Centre position: X={cx:.4f}  Y={cy:.4f} mm  "
          f"(expected 12.5000, 12.5000)")
    err_x = abs(cx - 12.5)
    err_y = abs(cy - 12.5)
    if err_x > 0.1 or err_y > 0.1:
        print(f"  WARNING: Stage position error > 100um!")
        print(f"  Check that homing completed and controllers are responding.")
        choice = input("  Continue anyway? [y/n] > ").strip().lower()
        if choice != 'y':
            return

    # ---- Initialize oscilloscope ----
    detector = None
    print(f"\n  Initializing oscilloscope...")
    try:
        detector = ScopeDetector()
        detector.connect()
        # Initial test reading
        test_v = detector.read_voltage()
        if test_v is not None:
            print(f"  Scope test reading: {test_v*1000:.2f} mV")
        else:
            print(f"  WARN: Scope connected but test reading failed")
    except Exception as e:
        print(f"  WARN: Could not connect to scope: {e}")
        print(f"  Continuing without oscilloscope (no intensity data)")
        detector = None

    # ---- Initialize Arduino switch matrix (per-pixel voltage) ----
    global _active_switch_matrix
    _active_switch_matrix = None
    if ARDUINO_ENABLED and HAVE_SWITCH_MATRIX:
        print(f"\n  Initializing Arduino switch matrix on {ARDUINO_PORT}...")
        try:
            _active_switch_matrix = ArduinoSwitchMatrix(
                port=ARDUINO_PORT, baudrate=ARDUINO_BAUD, verbose=False
            )
            if _active_switch_matrix.banner_seen():
                print(f"  Arduino READY — voltage applied at each calibrated "
                      f"peak ({VOLTAGE_DWELL_S:.1f}s dwell, using PIXEL_TO_PIN mapping).")
            else:
                print(f"  WARN: opened {ARDUINO_PORT} but no firmware banner.")
                print(f"  Voltage will still be sent — verify channels manually.")
        except Exception as e:
            print(f"  WARN: Could not open Arduino on {ARDUINO_PORT}: {e}")
            print(f"  Continuing WITHOUT per-pixel voltage application.")
            _active_switch_matrix = None
    elif ARDUINO_ENABLED and not HAVE_SWITCH_MATRIX:
        print(f"\n  WARN: arduino_switch_matrix module not found.")
        print(f"  Calibration will run WITHOUT voltage application.")
    else:
        print(f"\n  Arduino voltage switching DISABLED (ARDUINO_ENABLED=False).")

    # ---- Initialize live camera view ----
    live_view = LiveView(detector=detector)
    print(f"\n  Initializing live camera view...")
    cam_ok = live_view.start()

    print(f"\n  Stage ready. Front panels set to VELOCITY mode.")
    if detector:
        print(f"  Scope ready with smart V/div scaling.")
    if cam_ok:
        print(f"  Live camera + edge detection windows open.")
    if test_only:
        wait_for_input(live_view, "\n  Press Enter to begin test mode...")
        test_calibrated_positions(device_x, device_y, pixels,
                                  detector=detector, live_view=live_view)
    else:
        wait_for_input(live_view, "\n  Press Enter to begin calibration...")

        # ---- Calibration loop ----
        idx = start_idx
        try:
            while idx < total:
                pixel = pixels[idx]

                if pixel["calibrated_x_motor"] is not None:
                    idx += 1
                    continue

                action, data = calibrate_pixel(
                    device_x, device_y, pixel, total, n_calibrated, pixels,
                    detector=detector, live_view=live_view)

                if action == 'captured':
                    apply_voltage_for_pixel(pixel, detector=detector,
                                            live_view=live_view)
                    n_calibrated += 1
                    save_json(pixels, n_calibrated)
                    idx += 1

                elif action == 'skipped':
                    n_calibrated += 1
                    save_json(pixels, n_calibrated)
                    idx += 1

                elif action == 'redo':
                    if idx > 0:
                        prev = pixels[idx - 1]
                        if prev["calibrated_x_motor"] is not None:
                            n_calibrated -= 1
                        clear_pixel_fully(prev)
                        idx -= 1
                    else:
                        print("  (Already at first pixel, cannot go back)")

                elif action == 'goto':
                    target_idx = data
                    target = pixels[target_idx]
                    if target["calibrated_x_motor"] is not None:
                        n_calibrated -= 1
                    clear_pixel_fully(target)
                    idx = target_idx

                elif action == 'quit':
                    # Recount in case full-auto updated pixels directly
                    n_calibrated = sum(1 for p in pixels
                                       if p.get("calibrated_x_motor") is not None)
                    print(f"\n  Saving progress ({n_calibrated}/{total})...")
                    save_json(pixels, n_calibrated)
                    save_csv(pixels)
                    break

        except KeyboardInterrupt:
            print(f"\n\n  [Ctrl-C] Saving ({n_calibrated}/{total})...")
            save_json(pixels, n_calibrated)
            save_csv(pixels)

        else:
            if idx >= total:
                print(f"\n{'='*W}")
                print(f"  CALIBRATION COMPLETE - all {total} pixels captured!")
                print(f"{'='*W}")
                save_json(pixels, n_calibrated)
                save_csv(pixels)

    # ---- Summary ----
    show_progress(pixels, total)

    # ---- Auto-generate intensity heatmaps for this run ----
    heatmap_prefix = run_name if run_name else "calibration"
    print(f"\n  Generating intensity heatmaps...")
    save_run_heatmaps(pixels, OUTDIR, heatmap_prefix)

    print(f"\n  Files saved:")
    print(f"    {os.path.abspath(JSON_FILE)}")
    print(f"    {os.path.abspath(CSV_FILE)}")
    if os.path.isdir(IMGDIR):
        n_imgs = len([f for f in os.listdir(IMGDIR) if f.endswith('.png')])
        print(f"    {os.path.abspath(IMGDIR)}/  ({n_imgs} images)")
    # List heatmap outputs if present
    for suffix in ("_aligned_intensity.png", "_aligned_power.png"):
        p = os.path.join(OUTDIR, f"{heatmap_prefix}{suffix}")
        if os.path.isfile(p):
            print(f"    {os.path.abspath(p)}")

    # ---- Cleanup ----
    print(f"\n  Cleaning up...")
    live_view.stop()
    if detector is not None:
        detector.close()
    if _active_switch_matrix is not None:
        try:
            _active_switch_matrix.close()
            print(f"  Arduino switch matrix closed (all channels OFF).")
        except Exception as e:
            print(f"  WARN: Arduino close failed: {e}")
    for dev in (device_x, device_y):
        try:
            dev.StopPolling()
            dev.Disconnect()
        except Exception:
            pass

    print("  Done.")


if __name__ == "__main__":
    main()
