#!/usr/bin/env python3
# --------------------------------------------------------------------
# Main controller – accurate, fast nulling with adaptive averaging
# - HWP compensation seeding: QWP ≈ -ΔHWP, Analyzer ≈ -2ΔHWP
# - Alternating 1D golden-section (Analyzer → QWP) within tight windows
# - Scope on-screen MEAN with adaptive stability (RSD tolerance)
# - Robust Elliptec moves (chunking + single auto-rehome)
# - Concurrent cleaning & homing
# - Optical power: P[W] = V / (0.875 A/W * 1e6 Ω)
# --------------------------------------------------------------------

import time
import threading
from decimal import Decimal as PyDecimal, getcontext
getcontext().prec = 10

import numpy as np
import pyvisa

from stage_xy import StageXY
from elliptec_serial import ElliptecRotator
from kls1550 import KLS1550

# ============================== Config ===============================

# XY stage
SERIAL_X = "26006987"
SERIAL_Y = "26007025"
GRID_SIZE_MM = 25.0
GRID_NUM = 10
MOTOR_CENTER_MM = 12.5

# Laser (optional)
USE_LASER = True
KLS_SERIAL = None            # None -> auto-detect
KLS_POWER_MW = 7.0
LASER_STABILIZE_S = 5.0

# Rotators (Elliptec)
COM4_PORT, COM4_ADDR = "COM4", 2   # QWP
COM5_PORT, COM5_ADDR = "COM5", 1   # HWP
COM8_PORT, COM8_ADDR = "COM8", 2   # Analyzer

# Scope / detector (Tektronix TBS)
SCOPE_VISA = 'USB0::0x0699::0x03C7::C021052::0::INSTR'
SCOPE_SOURCE = 'CH1'

# Stage motion profiles
FAST_V, FAST_A = 2.0, 2.0
SLOW_V, SLOW_A = 0.5, 0.5

# HWP (COM5) angles to visit
COM5_TARGET_ANGLES = [0.0, 22.5, 45.0, 67.5, 90.0]

# Rotator care
CLEAN_CYCLES = 2
HOME_SETTLE_S = 3.0

# Search behaviour
PERIOD_DEG = 180.0
ANL_WIN_INIT = 24.0        # analyzer initial window (deg)
QWP_WIN_INIT = 16.0        # qwp initial window (deg)
ANL_WIN_MIN  = 2.0
QWP_WIN_MIN  = 1.0
GOLD_ITERS   = 16          # points per 1D golden-section pass
ALT_PASSES   = 3           # Analyzer→QWP alternations
QUIET_IMPROVE_W = 5e-12    # early stop if improvement smaller than this

# Motion gentleness (prevents “sensor error”)
NULL_MOVE_SETTLE_S = 0.12  # per-move settle during searches
MAX_CHUNK_DEG = 6.0        # chunk large moves
AUTO_REHOME_ONCE = True

# Optical conversion at 1550 nm
RESP_A_PER_W = 0.875       # A/W
RLOAD_OHM    = 1_000_000.0 # Ω (scope input)
V_TO_W = 1.0 / (RESP_A_PER_W * RLOAD_OHM)   # P[W] = V * V_TO_W

# Detector averaging (deterministic accuracy via adaptive stability)
MEAS_AVG_S_DEFAULT = 0.16  # base window; adaptive read can exceed this if needed
DETECTOR_EXTRA_SETTLE_S = 0.06   # extra settle after each move before reading
STABLE_MIN_SAMPLES = 8           # at least this many MEAN samples
STABLE_RSD_TOL = 0.004           # relative std dev target (~0.4%)
STABLE_MAX_DURATION_S = 0.60     # hard cap per power read
SAMPLE_SLEEP_S = 0.01            # ~100 Hz sampling of MEAN

# Optional DC offset subtraction (laser OFF baseline in volts)
DARK_VOLTAGE_OFFSET = 0.0

# =====================================================================

# -------------------- Console print (thread-safe) ---------------------
print_lock = threading.Lock()
def safe_print(*a, **k):
    with print_lock:
        print(*a, **k)

# ---------------------- Geometry / grid helpers ----------------------
def calculate_grid_positions(num_electrodes: int, grid_size: float):
    if num_electrodes < 2:
        raise ValueError("num_electrodes must be >= 2")
    positions = []
    grid = PyDecimal(str(grid_size))
    spacing = grid / PyDecimal(str(num_electrodes - 1))
    half = grid / PyDecimal('2')
    center = PyDecimal(str(MOTOR_CENTER_MM))
    for row in range(num_electrodes):
        for col in range(num_electrodes):
            gx = -half + PyDecimal(str(col)) * spacing
            gy = -half + PyDecimal(str(row)) * spacing
            mx = gx + center
            my = gy + center
            positions.append((row, col, gx, gy, mx, my))
    return positions

# --------------------------- Rotator helpers -------------------------
def clean_rotator(rot, name, cycles=CLEAN_CYCLES):
    safe_print(f"[{name}] Cleaning cycles: {cycles}")
    for _ in range(cycles):
        rot.shift_angle(+360.0, settle_s=0.6)
        rot.shift_angle(-360.0, settle_s=0.6)
    safe_print(f"[{name}] Cleaning complete.")

def home_rotator(rot, name, direction=0, settle_s=HOME_SETTLE_S):
    safe_print(f"[{name}] Homing (dir={direction})")
    rot.home(direction=direction, settle_s=settle_s)
    rot.tare()
    a = rot.get_angle()
    safe_print(f"[{name}] Homed at {a:.3f}°" if a is not None else f"[{name}] Homed.")

def _shortest_arc_delta(cur, tgt):
    return (tgt - cur + 540.0) % 360.0 - 180.0

def safe_move_abs(rot: ElliptecRotator,
                  target_deg: float,
                  settle_s: float = NULL_MOVE_SETTLE_S,
                  max_chunk_deg: float = MAX_CHUNK_DEG):
    """
    Absolute move with chunking; one auto-rehome on sensor/limit error.
    """
    import math, time as _time
    target = target_deg % 360.0

    def _move_once():
        cur = (rot.get_angle() or 0.0) % 360.0
        delta = _shortest_arc_delta(cur, target)
        n_chunks = max(1, int(math.ceil(abs(delta) / max(1e-6, max_chunk_deg))))
        step = delta / n_chunks
        pos = cur
        for _ in range(n_chunks):
            pos = (pos + step) % 360.0
            rot.set_angle(pos, settle_s=settle_s)
            _time.sleep(0.01)
        rot.set_angle(target, settle_s=settle_s)

    try:
        _move_once()
    except Exception as e:
        if not AUTO_REHOME_ONCE:
            raise
        msg = str(e).lower()
        if ('sensor' in msg or 'limit' in msg):
            try:
                try: rot.stop()
                except: pass
                home_rotator(rot, "AUTO", direction=0, settle_s=max(HOME_SETTLE_S, 2.5))
            except Exception:
                pass
            _move_once()
        else:
            raise

# ====================== Detector (Tektronix TBS) ======================
class DetectorTekTBS:
    """
    Reads on-screen MEAN voltage (MEASU:IMMed:VALue?).
    Falls back to CURVe? scaling if MEASU not available.
    Provides adaptive-stability averaging to meet RSD tolerance.
    """
    def __init__(self, visa_addr=SCOPE_VISA, source=SCOPE_SOURCE):
        self.visa_addr = visa_addr
        self.source = source
        self.rm = None
        self.scope = None
        self.measu_ok = False
        # fallback scaling
        self.npts = None
        self.xincr = self.xzero = self.ymult = self.yzero = self.yoff = None

    def connect(self):
        self.rm = pyvisa.ResourceManager()
        self.scope = self.rm.open_resource(self.visa_addr)
        self.scope.timeout = 2500
        self.scope.read_termination = '\n'
        self.scope.write_termination = None
        self.scope.encoding = 'latin_1'
        self.scope.write('*CLS')
        safe_print("[SCOPE]", self.scope.query('*IDN?').strip())

        # Free-run + loose trigger (prevents stalls)
        self.scope.write(f'SELEct:{self.source} ON')
        self.scope.write('ACQuire:STOPAfter RUNSTop')
        self.scope.write('ACQuire:STATE RUN')
        self.scope.write('TRIGger:A:TYPe EDGE')
        self.scope.write('TRIGger:A:MODe AUTO')
        try:
            self.scope.write('TRIGger:A:EDGE:SOURce LINE')
        except Exception:
            self.scope.write(f'TRIGger:A:EDGE:SOURce {self.source}')

        # Configure immediate MEAN
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
            # fallback fast data transfer
            self.scope.write('HEADER 0')
            self.scope.write(f'DATA:SOURce {self.source}')
            self.scope.write('DATA:ENCdg RIBinary')  # signed int8
            self.scope.write('DATA:WIDth 1')
            self.scope.write('DATA:START 1')
            self._refresh_preamble()

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
        # fallback via CURVe?
        raw = self.scope.query_binary_values('CURVe?', datatype='b', container=np.array)
        v = (raw.astype(np.float64) - self.yoff) * self.ymult + self.yzero
        return float(np.mean(v))

    def configure_for_polarisation_calibration(
        self,
        scale_vdiv=None,
        position=None,
        autoset=False,
        settle_s=1.0,
    ):
        """Optional scope setup used before polarisation calibration scans."""
        if self.scope is None:
            return
        channel = self.source.upper()
        if autoset:
            try:
                self.scope.write("AUTOSet EXECute")
            except Exception:
                try:
                    self.scope.write("AUTOSET EXECUTE")
                except Exception as exc:
                    safe_print(f"[SCOPE] Autoset command failed: {exc}")
            time.sleep(max(0.0, float(settle_s)))
        if scale_vdiv is not None:
            try:
                self.scope.write(f"{channel}:SCAle {float(scale_vdiv):.9g}")
                safe_print(f"[SCOPE] {channel} vertical scale -> {float(scale_vdiv):.6g} V/div")
            except Exception as exc:
                safe_print(f"[SCOPE] Could not set {channel} vertical scale: {exc}")
        if position is not None:
            try:
                self.scope.write(f"{channel}:POSition {float(position):.9g}")
                safe_print(f"[SCOPE] {channel} vertical position -> {float(position):.6g}")
            except Exception as exc:
                safe_print(f"[SCOPE] Could not set {channel} vertical position: {exc}")
        try:
            self.scope.write('ACQuire:STATE RUN')
        except Exception:
            pass
        if not self.measu_ok:
            try:
                self._refresh_preamble()
            except Exception:
                pass

    def read_power_w_stable(self,
                            min_samples: int = STABLE_MIN_SAMPLES,
                            rsd_tol: float = STABLE_RSD_TOL,
                            max_duration_s: float = STABLE_MAX_DURATION_S,
                            extra_settle_s: float = DETECTOR_EXTRA_SETTLE_S,
                            sample_sleep_s: float = SAMPLE_SLEEP_S):
        """
        After an extra settle, sample the scope's on-screen MEAN repeatedly until
        relative std dev (RSD) < rsd_tol or max_duration_s reached.
        Returns (P_mean_W, V_mean).
        """
        import numpy as _np
        if extra_settle_s > 0:
            time.sleep(extra_settle_s)

        vals = []
        t0 = time.perf_counter()
        while True:
            try:
                vm = self._read_mean_v_once()
            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
                time.sleep(sample_sleep_s)
                continue

            vals.append(vm)
            n = len(vals)

            if n >= max(3, min_samples):
                a = _np.array(vals, dtype=_np.float64)
                v_mean = float(_np.mean(a))
                v_std  = float(_np.std(a, ddof=1)) if n > 1 else 0.0
                rsd = (v_std / max(1e-12, abs(v_mean)))
                if rsd <= rsd_tol:
                    v_final = v_mean
                    break

            if (time.perf_counter() - t0) >= max_duration_s:
                v_final = float(np.median(vals))
                break

            time.sleep(sample_sleep_s)

        v_final -= DARK_VOLTAGE_OFFSET
        p_w = v_final * V_TO_W
        return p_w, v_final

    # Backward compatible simple average (not used by nuller)
    def read_power_w_avg(self, duration_s=MEAS_AVG_S_DEFAULT, min_samples=5, sleep_s=0.01):
        vals_v = []
        end_t = time.perf_counter() + max(0.05, duration_s)
        while time.perf_counter() < end_t or len(vals_v) < min_samples:
            try:
                vm = self._read_mean_v_once()
            except Exception:
                if not self.measu_ok:
                    self._refresh_preamble()
                time.sleep(0.01)
                continue
            vals_v.append(vm)
            time.sleep(max(0.0, sleep_s))
        v_mean = float(np.median(vals_v))
        v_mean -= DARK_VOLTAGE_OFFSET
        p_w = v_mean * V_TO_W
        return p_w, v_mean

    def close(self):
        try:
            if self.scope:
                self.scope.close()
        finally:
            if self.rm:
                self.rm.close()

# ======================= Periodic math helpers =======================
def wrap180(x):  # wrap to [0,180)
    return x % PERIOD_DEG

def dist180(a, b):
    return (b - a + PERIOD_DEG/2) % PERIOD_DEG - PERIOD_DEG/2

# ====================== Golden-section 1D minimizer ======================
def golden_minimize(move_fn, measure_fn, center_deg, half_window_deg, iters=GOLD_ITERS):
    """
    Minimize in [center-half, center+half] (wrapped 180°) using golden-section.
    Returns (angle_deg, p_min).
    """
    phi = (1 + 5 ** 0.5) / 2
    invphi = 1 / phi
    invphi2 = invphi ** 2

    def step(a, b, r):
        da = dist180(a, b)
        return wrap180(a + r * da)

    a = wrap180(center_deg - half_window_deg)
    b = wrap180(center_deg + half_window_deg)
    c = step(a, b, invphi2)
    d = step(a, b, invphi)

    move_fn(c); fc, _ = measure_fn()
    move_fn(d); fd, _ = measure_fn()

    for _ in range(iters):
        if fc < fd:
            b, d, fd = d, c, fc
            c = step(a, b, invphi2)
            move_fn(c); fc, _ = measure_fn()
        else:
            a, c, fc = c, d, fd
            d = step(a, b, invphi)
            move_fn(d); fd, _ = measure_fn()

        if abs(dist180(a, b)) <= 0.3:
            break

    return (c, fc) if fc < fd else (d, fd)

# ================== Alternating Analyzer → QWP null ==================
def fast_null(det: DetectorTekTBS,
              rot_qwp: ElliptecRotator,
              rot_anl: ElliptecRotator,
              seed_qwp: float,
              seed_anl: float,
              anl_win_deg=ANL_WIN_INIT,
              qwp_win_deg=QWP_WIN_INIT):
    """
    Start near (seed_qwp, seed_anl). Golden-section on Analyzer in a small window,
    then on QWP in a small window; alternate a few times. Shrink windows each pass.
    Returns (q_opt, a_opt, p_min).
    """
    measure = lambda: det.read_power_w_stable()  # adaptive stability

    def move_anl(a): safe_move_abs(rot_anl, a, settle_s=NULL_MOVE_SETTLE_S)
    def move_qwp(q): safe_move_abs(rot_qwp, q, settle_s=NULL_MOVE_SETTLE_S)

    q_best = wrap180(seed_qwp)
    a_best = wrap180(seed_anl)

    # Park to seeds once
    move_qwp(q_best)
    move_anl(a_best)
    p_best, _ = det.read_power_w_stable()

    a_win = max(ANL_WIN_MIN, anl_win_deg/2)
    q_win = max(QWP_WIN_MIN, qwp_win_deg/2)

    for _ in range(ALT_PASSES):
        a_best, p_a = golden_minimize(move_anl, measure, a_best, a_win, iters=GOLD_ITERS)
        q_best, p_q = golden_minimize(move_qwp, measure, q_best, q_win, iters=GOLD_ITERS)

        p_new = min(p_a, p_q)
        if abs(p_best - p_new) < QUIET_IMPROVE_W:
            p_best = p_new
            break
        p_best = p_new
        a_win = max(ANL_WIN_MIN, a_win * 0.6)
        q_win = max(QWP_WIN_MIN, q_win * 0.6)

    move_qwp(q_best); move_anl(a_best)
    p_final, _ = det.read_power_w_stable()
    return q_best, a_best, p_final

# --------------- Optional analyzer sweep (quick) ----------------
def perform_analyzer_sweep(rot_anl, step_deg=15, settle_s=NULL_MOVE_SETTLE_S, wait_s=0.5):
    safe_print(f"[COM8] Analyzer sweep {step_deg}° steps")
    for angle in range(0, 361, int(step_deg)):
        rot_anl.set_angle(angle, settle_s=settle_s)
        time.sleep(wait_s)
    safe_print("[COM8] Sweep complete.")

# ================================ Main ================================
def main():
    t0 = time.perf_counter()
    stage = rot4 = rot5 = rot8 = kls = det = None

    use_laser = USE_LASER

    # caches
    last_qwp = 0.0
    last_anl = 0.0
    last_hwp = 0.0

    try:
        # ---------- Connect hardware ----------
        stage = StageXY(SERIAL_X, SERIAL_Y, motor_center_mm=MOTOR_CENTER_MM); stage.connect()
        safe_print("[STAGE] Connected.")

        rot4 = ElliptecRotator(port=COM4_PORT, address=COM4_ADDR, verbose=False, settle_time=NULL_MOVE_SETTLE_S); safe_print("[COM4] Connected (QWP).")
        rot5 = ElliptecRotator(port=COM5_PORT, address=COM5_ADDR, verbose=False, settle_time=NULL_MOVE_SETTLE_S); safe_print("[COM5] Connected (HWP).")
        rot8 = ElliptecRotator(port=COM8_PORT, address=COM8_ADDR, verbose=False, settle_time=NULL_MOVE_SETTLE_S); safe_print("[COM8] Connected (Analyzer).")

        det = DetectorTekTBS(visa_addr=SCOPE_VISA, source=SCOPE_SOURCE); det.connect()
        safe_print("[DETECTOR] Tektronix TBS ready (on-screen MEAN, adaptive).")

        if use_laser:
            kls_serial = (KLS1550.find_first() if KLS_SERIAL is None else KLS_SERIAL)
            if not kls_serial:
                safe_print("[LASER] No KLS found; continuing without laser.")
                use_laser = False
            else:
                kls = KLS1550(kls_serial); kls.connect(); kls.set_power_absolute(KLS_POWER_MW)
                safe_print(f"[LASER] Ready (serial={kls_serial}, {KLS_POWER_MW} mW; emission OFF).")

        # ---------- Home all concurrently ----------
        t1 = threading.Thread(target=stage.home_both)
        t2 = threading.Thread(target=home_rotator, args=(rot4, "COM4"))
        t3 = threading.Thread(target=home_rotator, args=(rot5, "COM5"))
        t4 = threading.Thread(target=home_rotator, args=(rot8, "COM8"))
        for t in (t1, t2, t3, t4): t.start()
        for t in (t1, t2, t3, t4): t.join()
        safe_print("✓ All devices homed.")

        # ---------- Clean rotators concurrently ----------
        c2 = threading.Thread(target=clean_rotator, args=(rot4, "COM4", CLEAN_CYCLES))
        c3 = threading.Thread(target=clean_rotator, args=(rot5, "COM5", CLEAN_CYCLES))
        c4 = threading.Thread(target=clean_rotator, args=(rot8, "COM8", CLEAN_CYCLES))
        for t in (c2, c3, c4): t.start()
        for t in (c2, c3, c4): t.join()
        safe_print("✓ Rotator cleaning complete.")

        # ---------- Re-home rotators concurrently ----------
        h2 = threading.Thread(target=home_rotator, args=(rot4, "COM4"))
        h3 = threading.Thread(target=home_rotator, args=(rot5, "COM5"))
        h4 = threading.Thread(target=home_rotator, args=(rot8, "COM8"))
        for t in (h2, h3, h4): t.start()
        for t in (h2, h3, h4): t.join()
        safe_print("✓ Rotators re-homed.")

        # ---------- Stage center ----------
        stage.center_on_origin(); time.sleep(0.2)

        # ---------- Grid positions ----------
        positions = calculate_grid_positions(GRID_NUM, GRID_SIZE_MM)
        prev_row = -1

        # ---------- Main scan ----------
        for idx, (row, col, gx, gy, mx, my) in enumerate(positions, 1):
            if row != prev_row:
                stage.set_velocity(FAST_V, FAST_A, FAST_V, FAST_A)
                prev_row = row
                safe_print(f"\n=== Row {row} (FAST) ===")
            else:
                if col == 0: safe_print(f"\nRow {row} (SLOW)")
                stage.set_velocity(SLOW_V, SLOW_A, SLOW_V, SLOW_A)

            safe_print(f"[{idx:3d}/{GRID_NUM*GRID_NUM}] Move to global ({float(gx):+.5f}, {float(gy):+.5f}) mm")
            stage.move_to_motor(float(mx), float(my))
            _, _, gxr, gyr = stage.get_positions()
            safe_print(f"  Actual global: ({gxr:+.5f}, {gyr:+.5f}) mm")

            # Laser on & stabilize (if used)
            if use_laser and kls is not None:
                try:
                    kls.on()
                    safe_print(f"[LASER] ON; stabilizing {LASER_STABILIZE_S:.1f}s @ {KLS_POWER_MW} mW")
                    time.sleep(LASER_STABILIZE_S)
                except Exception as e:
                    safe_print("[LASER] WARN:", e)

            # ---- HWP sequence at this site ----
            for target_angle in COM5_TARGET_ANGLES:
                safe_print(f"[COM5] Set HWP -> {target_angle:.1f}°")
                rot5.set_angle(target_angle, settle_s=0.45)

                # Predict QWP & Analyzer from ΔHWP (keeps us near extinction)
                delta_hwp = (target_angle - last_hwp)
                pred_qwp = wrap180(last_qwp - delta_hwp)       # ≈ -ΔHWP
                pred_anl = wrap180(last_anl - 2.0 * delta_hwp) # ≈ -2ΔHWP
                safe_move_abs(rot4, pred_qwp, settle_s=0.20)
                safe_move_abs(rot8, pred_anl, settle_s=0.20)
                safe_print(f"[PREDICT] QWP≈{pred_qwp:.2f}°, Analyzer≈{pred_anl:.2f}° (ΔHWP={delta_hwp:+.2f}°)")

                # ===== Null: Analyzer→QWP golden-section alternations =====
                q_opt, a_opt, p_min = fast_null(
                    det=det,
                    rot_qwp=rot4,
                    rot_anl=rot8,
                    seed_qwp=pred_qwp,
                    seed_anl=pred_anl,
                    anl_win_deg=ANL_WIN_INIT,
                    qwp_win_deg=QWP_WIN_INIT
                )

                # Update seeds for next step
                last_qwp, last_anl, last_hwp = q_opt, a_opt, target_angle

                v_equiv_mV = p_min / V_TO_W * 1e3
                safe_print(f"[NULL] QWP={q_opt:.2f}°, Analyzer={a_opt:.2f}°, "
                           f"Pmin={p_min:.3e} W (~{v_equiv_mV:.2f} mV)")

            # Laser off between sites (safety)
            if use_laser and kls is not None:
                try: kls.off()
                except Exception as e: safe_print("[LASER] WARN (OFF):", e)

        # ---------- Wrap up ----------
        safe_print("\nFinal homing and shutdown…")
        if kls is not None:
            try: kls.off(); kls.close()
            except: pass

        # Home/close rotators concurrently
        rh2 = threading.Thread(target=home_rotator, args=(rot4, "COM4"))
        rh3 = threading.Thread(target=home_rotator, args=(rot5, "COM5"))
        rh4 = threading.Thread(target=home_rotator, args=(rot8, "COM8"))
        for t in (rh2, rh3, rh4): t.start()
        for t in (rh2, rh3, rh4): t.join()
        for rot in (rot4, rot5, rot8):
            try: rot.close()
            except: pass

        try:
            stage.center_on_origin()
            stage.close()
        except: pass

        try: det.close()
        except: pass

        safe_print(f"\n✓ ALL DONE in {time.perf_counter() - t0:.1f}s")

    except Exception as e:
        import traceback
        safe_print("❌ ERROR:", e)
        traceback.print_exc()

        # Emergency shutdown
        try:
            if kls is not None:
                try: kls.off()
                except: pass
                try: kls.close()
                except: pass
        except: pass
        for rot in (rot4, rot5, rot8):
            try:
                if rot: rot.close()
            except: pass
        try:
            if stage: stage.close()
        except: pass
        try:
            if det: det.close()
        except: pass

# --------------------------------------------------------------------
if __name__ == "__main__":
    main()
