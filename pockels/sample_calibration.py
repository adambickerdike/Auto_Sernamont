#!/usr/bin/env python3
# --------------------------------------------------------------------
# Sample Calibration Script
#
# Runs the same 3-step polarization calibration as the main script,
# but WITH the BTO sample in the beam (E-field OFF).
#
# This measures the polarization state *through* the sample so that
# subsequent scans can be referenced to the sample-in null.
#
# Steps:
#   1) QWP removed, sample in beam: Analyzer sweep → cos² fit
#   2) QWP inserted, sample in beam: QWP sweep → γ0, δ
#   3) QWP + Analyzer null through sample → full extinction
#   4) Health check
#   5) Save sample_calibration.json
# --------------------------------------------------------------------

import _bootstrap  # noqa: F401  # pin CWD to the repo root so data is shared

# Import everything from the main script (constants, classes, functions)
from POL_Chip_Test_Working_2026 import *

import argparse
import os, json, time, threading
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt

import stage_calibration as stage

# Output folder for sample-in calibration (separate from air calibration)
SAMPLE_OUTDIR = "calibration_results_withSample"
os.makedirs(SAMPLE_OUTDIR, exist_ok=True)

GUI_ALIGNMENT_FILE = None
GUI_ALIGNMENT_STATE = {}
GUI_ALIGNMENT_TRACKER = {
    "active": False,
    "installed": False,
    "pixel": None,
    "phase": "",
    "last_x_mm": None,
    "last_y_mm": None,
    "last_axis": "",
    "point_index": 0,
}


def sanitize_calibration_name(name: str) -> str:
    clean = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(name).strip()).strip("_")
    return clean or datetime.now().strftime("%Y%m%d_%H%M%S")


class SampleCameraExporter:
    """Small camera frame exporter for the GUI-owned sample calibration view."""

    def __init__(self, frame_file: str, interval_s: float = 0.20):
        self.frame_file = os.path.abspath(frame_file)
        self.interval_s = float(interval_s)
        self._camera = None
        self._thread = None
        self._stop = threading.Event()
        self._last_export_warning_s = 0.0
        self._suppressed_export_warnings = 0

    def _open_camera(self):
        if not getattr(stage, "HAVE_CAMERA", False):
            print("[CAMERA] OpenCV unavailable; GUI camera disabled.")
            return None
        cv2 = stage.cv2
        backends = [
            ("DirectShow", cv2.CAP_DSHOW),
            ("MSMF", cv2.CAP_MSMF),
            ("Default", cv2.CAP_ANY),
        ]
        for idx in (0, 1, 2):
            for name, backend in backends:
                try:
                    print(f"[CAMERA] Trying camera {idx} ({name})...", end="", flush=True)
                    cap = cv2.VideoCapture(idx, backend)
                    if cap.isOpened():
                        ret, frame = cap.read()
                        if ret and frame is not None and frame.size > 0:
                            print(f" OK ({frame.shape[1]}x{frame.shape[0]})")
                            return cap
                        print(" opened but no frame")
                        cap.release()
                    else:
                        print(" not found")
                except Exception as exc:
                    print(f" error: {exc}")
        print("[CAMERA] No working camera found; GUI camera disabled.")
        return None

    def start(self) -> bool:
        self._camera = self._open_camera()
        if self._camera is None:
            return False
        os.makedirs(os.path.dirname(self.frame_file), exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[CAMERA] GUI live camera export -> {self.frame_file}")
        return True

    def _run(self):
        cv2 = stage.cv2
        while not self._stop.is_set():
            tmp = None
            try:
                ret, frame = self._camera.read()
                if ret and frame is not None:
                    tmp = (
                        f"{self.frame_file}.{os.getpid()}."
                        f"{threading.get_ident()}.{time.time_ns()}.tmp.png"
                    )
                    if not cv2.imwrite(tmp, frame):
                        raise RuntimeError(f"cv2.imwrite failed for {tmp}")
                    replaced = False
                    last_exc = None
                    for _attempt in range(5):
                        try:
                            os.replace(tmp, self.frame_file)
                            replaced = True
                            break
                        except PermissionError as exc:
                            last_exc = exc
                            time.sleep(0.035)
                        except OSError as exc:
                            last_exc = exc
                            if getattr(exc, "winerror", None) == 5:
                                time.sleep(0.035)
                                continue
                            raise
                    if not replaced:
                        raise last_exc or RuntimeError("camera frame replace failed")
            except Exception as exc:
                now = time.time()
                self._suppressed_export_warnings += 1
                if now - self._last_export_warning_s >= 5.0:
                    extra = (
                        f" ({self._suppressed_export_warnings} frame(s) skipped)"
                        if self._suppressed_export_warnings > 1 else ""
                    )
                    print(f"[CAMERA] Export warning: {exc}{extra}")
                    self._last_export_warning_s = now
                    self._suppressed_export_warnings = 0
                if tmp:
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except Exception:
                        pass
            time.sleep(self.interval_s)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None


def gui_write_alignment_state(**updates):
    if GUI_ALIGNMENT_FILE is None:
        return
    GUI_ALIGNMENT_STATE.update(updates)
    GUI_ALIGNMENT_STATE["updated"] = datetime.now().isoformat()
    try:
        folder = os.path.dirname(os.path.abspath(GUI_ALIGNMENT_FILE))
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = f"{GUI_ALIGNMENT_FILE}.{os.getpid()}.{time.time_ns()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(GUI_ALIGNMENT_STATE, f, indent=2)
        os.replace(tmp, GUI_ALIGNMENT_FILE)
    except Exception:
        pass


def gui_alignment_reset(pixel, nominal_x_mm=None, nominal_y_mm=None):
    GUI_ALIGNMENT_STATE.clear()
    GUI_ALIGNMENT_STATE.update({
        "active": True,
        "pixel": int(pixel),
        "nominal_x_mm": nominal_x_mm,
        "nominal_y_mm": nominal_y_mm,
        "points": [],
        "best": None,
        "started": datetime.now().isoformat(),
    })
    GUI_ALIGNMENT_TRACKER.update({
        "active": True,
        "pixel": int(pixel),
        "phase": "sample calibration alignment",
        "last_x_mm": nominal_x_mm,
        "last_y_mm": nominal_y_mm,
        "last_axis": "",
        "point_index": 0,
    })
    gui_write_alignment_state()


def gui_alignment_add_point(voltage_v, *, label=""):
    if not GUI_ALIGNMENT_TRACKER.get("active"):
        return
    x_mm = GUI_ALIGNMENT_TRACKER.get("last_x_mm")
    y_mm = GUI_ALIGNMENT_TRACKER.get("last_y_mm")
    if x_mm is None and y_mm is None:
        return
    try:
        v = float(voltage_v)
    except Exception:
        v = float("nan")
    GUI_ALIGNMENT_TRACKER["point_index"] = int(GUI_ALIGNMENT_TRACKER.get("point_index", 0)) + 1
    point = {
        "index": int(GUI_ALIGNMENT_TRACKER["point_index"]),
        "x_mm": x_mm,
        "y_mm": y_mm,
        "axis": GUI_ALIGNMENT_TRACKER.get("last_axis", ""),
        "label": label or GUI_ALIGNMENT_TRACKER.get("phase", ""),
        "voltage_v": v,
        "voltage_mV": v * 1000.0 if np.isfinite(v) else float("nan"),
        "timestamp": time.time(),
    }
    points = GUI_ALIGNMENT_STATE.setdefault("points", [])
    points.append(point)
    if len(points) > 800:
        del points[:len(points) - 800]
    best = GUI_ALIGNMENT_STATE.get("best")
    if best is None or (np.isfinite(v) and v > float(best.get("voltage_v", -float("inf")))):
        GUI_ALIGNMENT_STATE["best"] = point
    gui_write_alignment_state(active=True)


def install_gui_alignment_export(detector, alignment_file):
    global GUI_ALIGNMENT_FILE
    GUI_ALIGNMENT_FILE = os.path.abspath(alignment_file) if alignment_file else None
    if GUI_ALIGNMENT_FILE is None:
        return

    if not GUI_ALIGNMENT_TRACKER.get("installed"):
        original_move = stage._safe_scan_move

        def gui_safe_scan_move(device, target_mm, label=""):
            result = original_move(device, target_mm, label)
            if GUI_ALIGNMENT_TRACKER.get("active"):
                upper_label = str(label).upper()
                axis = (
                    "X" if upper_label.startswith("X") or upper_label.endswith("-X") or "-X-" in upper_label
                    else "Y" if upper_label.startswith("Y") or upper_label.endswith("-Y") or "-Y-" in upper_label
                    else ""
                )
                try:
                    pos = float(result)
                except Exception:
                    try:
                        pos = float(target_mm)
                    except Exception:
                        pos = None
                if axis == "X":
                    GUI_ALIGNMENT_TRACKER["last_x_mm"] = pos
                elif axis == "Y":
                    GUI_ALIGNMENT_TRACKER["last_y_mm"] = pos
                if axis:
                    GUI_ALIGNMENT_TRACKER["last_axis"] = axis
                GUI_ALIGNMENT_TRACKER["phase"] = str(label)
                gui_write_alignment_state(
                    active=True,
                    current_label=str(label),
                    current_x_mm=GUI_ALIGNMENT_TRACKER.get("last_x_mm"),
                    current_y_mm=GUI_ALIGNMENT_TRACKER.get("last_y_mm"),
                )
            return result

        stage._safe_scan_move = gui_safe_scan_move
        GUI_ALIGNMENT_TRACKER["installed"] = True

    original_read_averaged = getattr(detector, "read_averaged", None)
    if original_read_averaged is None or getattr(original_read_averaged, "_gui_alignment_export", False):
        return

    def gui_read_averaged(*args, **kwargs):
        result = original_read_averaged(*args, **kwargs)
        if GUI_ALIGNMENT_TRACKER.get("active"):
            try:
                voltage_v = result[0]
            except Exception:
                voltage_v = None
            if voltage_v is not None:
                gui_alignment_add_point(voltage_v)
        return result

    gui_read_averaged._gui_alignment_export = True
    detector.read_averaged = gui_read_averaged


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Sample-in optical calibration")
    parser.add_argument("--cal-name", default=None, help="Calibration folder/name. If omitted, prompt interactively.")
    parser.add_argument("--gui-camera-frame-file", default=None, help="Optional PNG path for GUI live camera export.")
    parser.add_argument("--gui-alignment-file", default=None, help="Optional JSON path for GUI live stage-alignment export.")
    parser.add_argument("--no-camera", action="store_true", help="Disable GUI camera frame export.")
    parser.add_argument("--no-plot-windows", action="store_true", help="Save plots without opening external plot windows.")
    parser.add_argument("--alignment-min-signal-mv", type=float, default=float(stage.FULL_AUTO_MIN_SIGNAL_MV),
                        help="Advisory stage-alignment retry threshold in detector mV.")
    parser.add_argument("--alignment-hard-min-mv", type=float, default=5.0,
                        help="Abort sample calibration if fast alignment stays below this detector level in mV.")
    parser.add_argument("--allow-full-alignment-scan", action="store_true",
                        help="Permit the slow exhaustive full line-scan rescue if fast alignment is below threshold.")
    return parser.parse_args(argv)


def align_to_first_pixel(
    target_pixel=1,
    *,
    min_signal_mV=None,
    hard_min_signal_mV=5.0,
    allow_full_scan=False,
    gui_alignment_file=None,
):
    """Phase 0: bring up the Kinesis stage, move to pixel `target_pixel`, and
    run the fast hill-climb -> fast-peak -> golden alignment chain
    so the beam is sitting on a transparent electrode pixel BEFORE the 3-step
    sample calibration starts.

    Opens its own ScopeDetector for alignment and closes it before returning,
    so the rest of sample_calibration.py can open DetectorTekTBS on the same
    physical scope without VISA contention.

    Returns (device_x, device_y, calibrated_x, calibrated_y) so the caller can
    disconnect the stage cleanly at the end of the run.
    """
    print("\n" + "=" * 60)
    print(f"PHASE 0 - STAGE BRING-UP + ALIGN TO PIXEL {target_pixel}")
    print("=" * 60)

    print("\nInitializing Kinesis stage...")
    stage.DeviceManagerCLI.BuildDeviceList()
    print("  X axis:")
    device_x = stage.initialize_device(stage.STAGE_SERIAL_X)
    print("  Y axis:")
    device_y = stage.initialize_device(stage.STAGE_SERIAL_Y)

    print("\nHoming both stage axes...")
    stage.home_device(device_x, stage.STAGE_SERIAL_X)
    stage.home_device(device_y, stage.STAGE_SERIAL_Y)

    print("\nMoving stage to centre (12.5, 12.5 mm motor coords)...")
    stage.safe_move_to(device_x, 12.5, "X")
    stage.safe_move_to(device_y, 12.5, "Y")

    pixels = stage.build_grid()
    if not (1 <= target_pixel <= len(pixels)):
        raise ValueError(f"target_pixel out of range: {target_pixel}")
    pix = pixels[target_pixel - 1]
    nom_x = pix["nominal_x_motor"]
    nom_y = pix["nominal_y_motor"]
    print(f"\n  Pixel {target_pixel}: nominal motor ({nom_x:.4f}, {nom_y:.4f}) mm")
    print(f"  Moving to nominal stage position...")
    stage._safe_scan_move(device_x, nom_x, "X")
    stage._safe_scan_move(device_y, nom_y, "Y")
    stage.set_velocity_mode(device_x, label="X")
    stage.set_velocity_mode(device_y, label="Y")
    time.sleep(stage.SETTLE_TIME_S)

    print("\n  Opening ScopeDetector for stage alignment...")
    align_det = stage.ScopeDetector()
    align_det.connect()
    test_v = align_det.read_voltage()
    print(f"  Scope test reading: {(test_v or 0.0) * 1000:.2f} mV")
    install_gui_alignment_export(align_det, gui_alignment_file)
    gui_alignment_reset(target_pixel, nom_x, nom_y)
    if test_v is not None:
        gui_alignment_add_point(test_v, label="initial")

    if min_signal_mV is None:
        min_signal_mV = float(stage.FULL_AUTO_MIN_SIGNAL_MV)
    threshold_v = float(min_signal_mV) / 1000.0
    hard_min_v = max(0.0, float(hard_min_signal_mV)) / 1000.0
    chain = "hill-climb -> fast-peak -> golden"
    if allow_full_scan:
        chain += " -> full scan"
    else:
        chain += " (full scan disabled)"
    print(f"\n  Alignment chain: {chain}")
    print(f"  Retry threshold: {float(min_signal_mV):.1f} mV; hard minimum: {float(hard_min_signal_mV):.1f} mV")

    def keep_best(best_result, best_method, candidate, candidate_method):
        if not candidate:
            return best_result, best_method
        if not best_result or float(candidate.get("peak_v", 0.0)) > float(best_result.get("peak_v", 0.0)):
            return candidate, candidate_method
        return best_result, best_method

    result = stage.hill_climb_align(device_x, device_y, align_det, None)
    method = "hill_climb"
    peak_v = result["peak_v"] if result else 0.0
    if peak_v < threshold_v:
        print(f"  Signal {peak_v * 1000:.1f} mV below threshold; trying fast peak.")
        candidate = stage.fast_peak_align(device_x, device_y, align_det, None)
        result, method = keep_best(result, method, candidate, "fast_peak")
        peak_v = result["peak_v"] if result else 0.0
    if peak_v < threshold_v:
        print(f"  Signal {peak_v * 1000:.1f} mV still low; trying golden section.")
        candidate = stage.golden_section_align(device_x, device_y, align_det, None)
        result, method = keep_best(result, method, candidate, "golden_section")
        peak_v = result["peak_v"] if result else 0.0
    if peak_v < threshold_v and allow_full_scan:
        print(f"  Signal {peak_v * 1000:.1f} mV still low; trying full line scan.")
        candidate = stage.auto_align(device_x, device_y, align_det, None)
        result, method = keep_best(result, method, candidate, "full_scan")
        peak_v = result["peak_v"] if result else 0.0
    elif peak_v < threshold_v:
        print(
            f"  Signal {peak_v * 1000:.1f} mV still below {float(min_signal_mV):.1f} mV, "
            "but full line scan is disabled for fast sample calibration."
        )
        print("  Continuing with the best fast alignment; use --allow-full-alignment-scan for exhaustive rescue.")

    if peak_v < hard_min_v:
        raise RuntimeError(
            f"Stage alignment only reached {peak_v * 1000:.2f} mV, below hard minimum "
            f"{float(hard_min_signal_mV):.2f} mV."
        )

    if result and "best_x" in result and "best_y" in result:
        best_x = float(result["best_x"])
        best_y = float(result["best_y"])
        cur_x, cur_y = stage.read_motor_pos_averaged(device_x, device_y)
        if abs(cur_x - best_x) > 0.0005 or abs(cur_y - best_y) > 0.0005:
            print(f"  Returning to retained best alignment: ({best_x:.4f}, {best_y:.4f}) mm")
            stage._safe_scan_move(device_x, best_x, "X-best")
            stage._safe_scan_move(device_y, best_y, "Y-best")
            time.sleep(stage.SETTLE_TIME_S)

    cal_x, cal_y = stage.read_motor_pos_averaged(device_x, device_y)
    print(f"\n  Aligned via {method}: motor ({cal_x:.4f}, {cal_y:.4f}) mm, "
          f"peak {peak_v * 1000:.2f} mV")
    best = GUI_ALIGNMENT_STATE.get("best") or {}
    gui_write_alignment_state(
        active=False,
        completed=datetime.now().isoformat(),
        final_x_mm=float(cal_x),
        final_y_mm=float(cal_y),
        final_voltage_mV=best.get("voltage_mV"),
    )
    GUI_ALIGNMENT_TRACKER["active"] = False

    print("  Closing ScopeDetector to free the VISA bus for DetectorTekTBS...")
    try:
        align_det.close()
    except Exception:
        pass
    time.sleep(0.3)

    return device_x, device_y, cal_x, cal_y, method, peak_v


def disconnect_stage(device_x, device_y):
    for dev, label in ((device_x, "X"), (device_y, "Y")):
        try:
            if dev is not None:
                dev.StopPolling()
                dev.Disconnect()
                print(f"  - Stage {label} disconnected")
        except Exception:
            pass


def main(argv=None):
    args = parse_args(argv)
    print("=" * 60)
    print("SAMPLE CALIBRATION - BTO IN BEAM, E-FIELD OFF")
    print("=" * 60)

    if args.cal_name:
        cal_name = sanitize_calibration_name(args.cal_name)
        print(f"Calibration name: {cal_name}")
    else:
        cal_name = input(
            "\nEnter name for this sample calibration "
            "(e.g., 'chip1_sample_cal', 'BTO_postmount'): "
        ).strip()
        if not cal_name:
            cal_name = datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"No name entered, using timestamp: {cal_name}")
        else:
            cal_name = sanitize_calibration_name(cal_name)
            print(f"Calibration name: {cal_name}")

    cal_subdir = os.path.join(SAMPLE_OUTDIR, cal_name)
    os.makedirs(cal_subdir, exist_ok=True)
    print(f"\nAll files will be saved to:")
    print(f"  {os.path.abspath(cal_subdir)}")
    print("=" * 60)

    camera_exporter = None
    if args.gui_camera_frame_file and not args.no_camera:
        camera_exporter = SampleCameraExporter(args.gui_camera_frame_file)
        camera_exporter.start()
    elif args.no_camera:
        print("[CAMERA] GUI live camera export disabled.")

    # ==================== Phase 0: Stage bring-up + electrode alignment ====================
    device_x = device_y = None
    aligned_x = aligned_y = float("nan")
    align_method = None
    align_peak_v = 0.0
    try:
        device_x, device_y, aligned_x, aligned_y, align_method, align_peak_v = (
            align_to_first_pixel(
                target_pixel=1,
                min_signal_mV=args.alignment_min_signal_mv,
                hard_min_signal_mV=args.alignment_hard_min_mv,
                allow_full_scan=args.allow_full_alignment_scan,
                gui_alignment_file=args.gui_alignment_file,
            )
        )
    except Exception as e:
        print(f"\n[ERROR] Stage alignment failed: {e}")
        print("       Aborting — calibration through an un-aligned beam is meaningless.")
        gui_write_alignment_state(active=False, failed=datetime.now().isoformat(), error=str(e))
        GUI_ALIGNMENT_TRACKER["active"] = False
        disconnect_stage(device_x, device_y)
        raise

    # Persist alignment result alongside the calibration outputs
    with open(os.path.join(cal_subdir, "stage_alignment.json"), "w") as f:
        json.dump({
            "target_pixel": 1,
            "aligned_x_motor_mm": float(aligned_x),
            "aligned_y_motor_mm": float(aligned_y),
            "method": align_method,
            "peak_v": float(align_peak_v),
            "alignment_min_signal_mV": float(args.alignment_min_signal_mv),
            "alignment_hard_min_mV": float(args.alignment_hard_min_mv),
            "allow_full_alignment_scan": bool(args.allow_full_alignment_scan),
            "timestamp": datetime.now().isoformat(),
        }, f, indent=2)

    # ==================== Hardware Init ====================

    # Rotators
    print("\nConnecting rotators...")
    rot_hwp = ElliptecRotator(port=PORT_HWP, address=ADDR_HWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_anl = ElliptecRotator(port=PORT_ANL, address=ADDR_ANL,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_qwp = ElliptecRotator(port=PORT_QWP, address=ADDR_QWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)

    print("Homing rotators...")
    for rot, name in [(rot_hwp, "HWP"), (rot_anl, "ANL"), (rot_qwp, "QWP")]:
        rot.home(direction=0, settle_s=3.0)
        rot.tare()
        print(f"[{name}] Home @ {rot.get_angle():.2f}\u00b0")

    # Detector
    det = DetectorTekTBS(SCOPE_VISA, SCOPE_SOURCE)
    det.connect()

    # Stage was already brought up + aligned to pixel 1 in Phase 0 (above).

    # Laser (optional)
    laser = None
    if USE_LASER and KLS1550 is not None:
        try:
            serial = KLS1550.find_first() if KLS_SERIAL is None else KLS_SERIAL
            if serial:
                laser = KLS1550(serial)
                laser.connect()
                laser.set_power_absolute(KLS_POWER_MW)
                laser.on()
                print(f"[LASER] ON @ {KLS_POWER_MW} mW; stabilizing {LASER_STABILIZE_S:.1f}s")
                time.sleep(LASER_STABILIZE_S)
            else:
                print("[LASER] Not found; continuing without laser control.")
        except Exception as e:
            print("[LASER] WARN:", e)

    # ==================== STEP 1: Analyzer Sweep (QWP removed, sample in) ====================
    print("\n=== STEP 1: QWP REMOVED \u2192 Analyzer sweep THROUGH SAMPLE ===")
    print(f"  Stage is at pixel 1 ({aligned_x:.4f}, {aligned_y:.4f}) mm, "
          f"peak {align_peak_v*1000:.2f} mV via {align_method}.")
    input(
        "REMOVE the QWP from the beam (sample stays in, E-FIELD OFF). "
        "Then press Enter..."
    )

    safe_move_abs(rot_hwp, CONFIRM_HWP_FIXED)
    print(f"[HWP] {CONFIRM_HWP_FIXED:.2f}\u00b0")

    anl_angles = np.arange(ANL_SWEEP_RANGE[0], ANL_SWEEP_RANGE[1] + 1e-9, ANL_SWEEP_STEP)
    data_anl = sweep_angles(rot_anl, anl_angles, det.read_power_w_stable)
    save_csv(os.path.join(cal_subdir, "analyzer_sweep_QWPout_sample.csv"),
             ["Analyzer_deg", "Power_W", "Volt"], data_anl)

    fit_anl = fit_cos2(data_anl[:, 0], data_anl[:, 1])
    anl_theta_off = fit_anl["theta_off_deg"] % 180.0
    Pmax_fit = fit_anl["Pmax"]
    Pbg_fit = fit_anl["Pbg"]

    i_max = int(np.argmax(data_anl[:, 1]))
    i_min = int(np.argmin(data_anl[:, 1]))
    anl_coarse_max = float(data_anl[i_max, 0])
    anl_coarse_min = float(data_anl[i_min, 0])
    a_ref_min, p_ref_min = refine_extremum_1d(
        rot_anl, anl_coarse_min, REFINE_WINDOW, REFINE_STEP, 'min',
        det.read_power_w_stable
    )
    a_ref_max, p_ref_max = refine_extremum_1d(
        rot_anl, anl_coarse_max, REFINE_WINDOW, REFINE_STEP, 'max',
        det.read_power_w_stable
    )

    print(f"[ANALYZER|QWP-out|SAMPLE] MIN @ {a_ref_min:.2f}\u00b0 : {p_ref_min:.3e} W")
    print(f"[ANALYZER|QWP-out|SAMPLE] MAX @ {a_ref_max:.2f}\u00b0 : {p_ref_max:.3e} W")
    print(f"[ANALYZER|QWP-out|SAMPLE] \u03b8_off (fit) = {anl_theta_off:.2f}\u00b0, Pbg = {Pbg_fit:.3e} W")

    plt.figure()
    tt = np.linspace(0, 180, 721)
    model = fit_anl["Pmax"] * (np.cos(np.deg2rad(tt - anl_theta_off)) ** 2) + Pbg_fit
    plt.plot(data_anl[:, 0], data_anl[:, 1] * 1e3, 'o-', ms=3, label="data")
    plt.plot(tt, model * 1e3, '-', lw=1.4, label="cos\u00b2 fit")
    plt.axvline(a_ref_min, ls='--', label=f"min {a_ref_min:.2f}\u00b0")
    plt.axvline(a_ref_max, ls='--', label=f"max {a_ref_max:.2f}\u00b0")
    plt.title(
        f"Analyzer sweep THROUGH SAMPLE (QWP removed) @ HWP={CONFIRM_HWP_FIXED:.2f}\u00b0\n"
        f"Pbg={Pbg_fit:.2e} W, \u03b8_off={anl_theta_off:.2f}\u00b0"
    )
    plt.xlabel("Analyzer angle (deg)")
    plt.ylabel("Detected power (mW)")
    plt.grid(True)
    plt.legend()
    plt.savefig(os.path.join(cal_subdir, "analyzer_sweep_QWPout_sample.png"), dpi=150)

    # Optional HWP sweep characterisation
    hwp_angles = np.arange(HWP_SWEEP_RANGE[0], HWP_SWEEP_RANGE[1] + 1e-9, HWP_SWEEP_STEP)
    safe_move_abs(rot_anl, a_ref_max)
    data_hwp = sweep_angles(rot_hwp, hwp_angles, det.read_power_w_stable)
    save_csv(os.path.join(cal_subdir, "hwp_sweep_QWPout_sample.csv"),
             ["HWP_deg", "Power_W", "Volt"], data_hwp)
    plt.figure()
    plt.plot(data_hwp[:, 0], data_hwp[:, 1] * 1e3, 'o-', ms=3)
    plt.title(f"HWP sweep THROUGH SAMPLE (QWP removed) @ Analyzer={a_ref_max:.2f}\u00b0")
    plt.grid(True)
    plt.xlabel("HWP angle (deg)")
    plt.ylabel("Detected power (mW)")
    plt.savefig(os.path.join(cal_subdir, "hwp_sweep_QWPout_sample.png"), dpi=150)

    # ==================== STEP 2: QWP Sweep (sample still in) ====================
    print("\n=== STEP 2: INSERT QWP \u2192 QWP sweep THROUGH SAMPLE ===")
    input(
        "Insert the QWP (before analyzer). SAMPLE STILL IN BEAM, E-FIELD STILL OFF. "
        "Then press Enter..."
    )
    safe_move_abs(rot_anl, a_ref_max)

    qwp_angles = np.arange(QWP_SWEEP_RANGE[0], QWP_SWEEP_RANGE[1] + 1e-9, QWP_SWEEP_STEP)
    data_qwp = sweep_angles(rot_qwp, qwp_angles, det.read_power_w_stable, period=180.0)
    save_csv(os.path.join(cal_subdir, "qwp_sweep_QWPin_sample.csv"),
             ["QWP_deg", "Power_W", "Volt"], data_qwp)

    fit_q = fit_qwp_cos4(data_qwp[:, 0], data_qwp[:, 1])
    gamma0 = fit_q["gamma0_deg"]
    Imax = fit_q["Imax"]
    Imin = fit_q["Imin"]
    vis = fit_q["visibility"]
    delta_deg = fit_q["delta_est_deg"]
    r = Imin / max(1e-12, Imax)

    print("\n[QWP calibration] (QWP in, SAMPLE in, E-field OFF)")
    print(f"  \u03b30 (fast-axis zero): {gamma0:.2f}\u00b0")
    print(f"  \u03b4 (retardance)     : {delta_deg:.2f}\u00b0   (ideal 90\u00b0)")
    print(f"  Imax / Imin        : {Imax:.3e} W / {Imin:.3e} W (r={r:.3f}), visibility={vis:.3f}")

    gfit = np.linspace(0, 180.0, 721)
    g = np.deg2rad(gfit)
    Ifit = fit_q["A"] + fit_q["B"] * np.cos(4 * g) + fit_q["C"] * np.sin(4 * g)
    plt.figure()
    plt.plot(data_qwp[:, 0], data_qwp[:, 1] * 1e3, 'o', ms=3, label="data")
    plt.plot(gfit, Ifit * 1e3, '-', label="fit")
    plt.axvline(gamma0, ls='--', label=f"\u03b30 = {gamma0:.2f}\u00b0")
    plt.title(f"QWP sweep THROUGH SAMPLE @ Analyzer={a_ref_max:.2f}\u00b0")
    plt.xlabel("QWP angle (deg)")
    plt.ylabel("Detected power (mW)")
    plt.grid(True)
    plt.legend()
    plt.savefig(os.path.join(cal_subdir, "qwp_sweep_fit_sample.png"), dpi=150)

    # ==================== STEP 3: Null (QWP + Analyzer) through sample ====================
    print("\n=== STEP 3: QWP + Analyzer nulling THROUGH SAMPLE ===")
    null_log = os.path.join(cal_subdir, "nulling_path_sample.csv")
    q_null, a_null, p_null = null_qwp_analyzer(
        det, rot_qwp, rot_anl,
        start_qwp_deg=gamma0,
        start_anl_deg=a_ref_min,
        init_span_qwp=20.0,
        init_span_anl=20.0,
        cycles=3, tol_deg=1.2,
        log_path=null_log
    )
    print(f"[NULL|SAMPLE] QWP_null={q_null:.2f}\u00b0, Analyzer_null={a_null:.2f}\u00b0, Pmin={p_null:.3e} W")

    ext_ratio_linear = Pmax_fit / max(p_null, 1e-15)
    ext_ratio_dB = 10.0 * np.log10(ext_ratio_linear + 1e-15)
    print(f"[EXTINCTION|SAMPLE] P_max(QWP-out) vs P_null(QWP+ANL): ~{ext_ratio_dB:.1f} dB")

    # ==================== STEP 4: Analyzer sweep at null QWP ====================
    # QWP stays fixed at null position, sweep analyzer through 0-180°
    print(f"\n=== STEP 4: Analyzer sweep @ QWP_null={q_null:.2f}° (QWP fixed) ===")
    anl_sweep_angles = np.arange(0.0, 180.0 + 1e-9, 2.0)
    anl_sweep_data = sweep_angles(rot_anl, anl_sweep_angles, det.read_power_w_stable)
    save_csv(os.path.join(cal_subdir, "analyzer_sweep_at_null_QWP.csv"),
             ["Analyzer_deg", "Power_W", "Volt"], anl_sweep_data)

    # Plot
    plt.figure()
    plt.plot(anl_sweep_data[:, 0], anl_sweep_data[:, 1] * 1e3, 'o-', ms=3)
    plt.axvline(a_null, ls='--', color='r', label=f"null = {a_null:.2f}°")
    plt.title(f"Analyzer sweep @ QWP_null = {q_null:.2f}° (through sample)")
    plt.xlabel("Analyzer angle (deg)")
    plt.ylabel("Detected power (mW)")
    plt.grid(True)
    plt.legend()
    plt.savefig(os.path.join(cal_subdir, "analyzer_sweep_at_null_QWP.png"), dpi=150)
    print(f"[SWEEP] Saved analyzer sweep CSV + plot")

    # Return analyzer to null — QWP never moved
    safe_move_abs(rot_anl, a_null)
    time.sleep(0.5)
    p_back, _ = det.read_power_w_stable()
    print(f"[SWEEP] Analyzer back to null {a_null:.2f}°, scope reads {p_back:.3e} W")

    # ==================== Build Calibration Pack ====================
    cal_pack = {
        "calibration_type": "sample_in_Efield_off",
        "calibration_name": cal_name,

        "HWP_fixed_deg": float(CONFIRM_HWP_FIXED),
        "Analyzer_extinction_deg_QWPout": float(a_ref_min),
        "Analyzer_parallel_deg_QWPout": float(a_ref_max),
        "Analyzer_theta_off_fit_deg_QWPout": float(anl_theta_off),
        "Analyzer_Pbg_fit_W_QWPout": float(Pbg_fit),

        "QWP_gamma0_fit_deg": float(gamma0),
        "QWP_delta_deg": float(delta_deg),
        "QWP_visibility": float(vis),
        "QWP_fit_coeffs": {
            "A": float(fit_q["A"]),
            "B": float(fit_q["B"]),
            "C": float(fit_q["C"]),
        },

        "Null_QWP_deg": float(q_null),
        "Null_Analyzer_deg": float(a_null),
        "Null_Pmin_W": float(p_null),
        "Extinction_dB_vs_QWPoutMax": float(ext_ratio_dB),

        "Detector": {
            "PDA_gain_dB": PDA_GAIN_DB,
            "PDA_load": PDA_LOAD,
            "A_per_W": RESP_A_PER_W,
            "V_to_W": V_TO_W,
            "Dark_offset_V": DARK_VOLTAGE_OFFSET,
        },
        "Scope": {
            "Vdiv_levels": VDIV_LEVELS,
            "Thresh_down": THRESH_DOWN,
            "Thresh_up": THRESH_UP,
        },
        "Laser": {
            "Power_mW": KLS_POWER_MW,
            "Wavelength_nm": 1550.0,
        },
    }

    # Save null summary text
    with open(os.path.join(cal_subdir, "null_summary_sample.txt"), "w") as f:
        f.write(f"QWP_null = {q_null:.2f} deg\n")
        f.write(f"Analyzer_null = {a_null:.2f} deg\n")
        f.write(f"Pmin = {p_null:.3e} W\n")
        f.write(f"Extinction_dB_vs_QWPoutMax = {ext_ratio_dB:.2f} dB\n")
        f.write(f"Calibration type: sample in beam, E-field OFF\n")

    # Save calibration JSON
    cal_file_path = os.path.join(cal_subdir, "sample_calibration.json")
    save_calibration_pack(cal_pack, path=cal_file_path)
    print(f"\n[CAL] Sample calibration saved: {cal_file_path}")

    # Also save a copy in the top-level sample output folder for easy discovery
    top_level_path = os.path.join(SAMPLE_OUTDIR, f"{cal_name}_sample_calibration.json")
    save_calibration_pack(cal_pack, path=top_level_path)

    # ==================== Verify null (rotators haven't moved since step 3) ====================
    # NO further motor movements after nulling — rotators stay exactly where
    # null_qwp_analyzer() left them.
    p_verify, _ = det.read_power_w_stable()
    print(f"\n[VERIFY] Scope reads {p_verify:.3e} W at null  "
          f"(step 3 found {p_null:.3e} W)")
    print(f"  HWP = {rot_hwp.get_angle():.2f}\u00b0, "
          f"QWP = {rot_qwp.get_angle():.2f}\u00b0, "
          f"ANL = {rot_anl.get_angle():.2f}\u00b0  \u2190 NULL (untouched)")

    # ==================== Summary ====================
    print(f"\n{'=' * 60}")
    print("SAMPLE CALIBRATION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Calibration name: {cal_name}")
    print(f"Output directory: {os.path.abspath(cal_subdir)}")
    print(f"\nKey results (sample in beam, E-field OFF):")
    print(f"  Analyzer extinction (QWP out): {a_ref_min:.2f}\u00b0")
    print(f"  Analyzer parallel   (QWP out): {a_ref_max:.2f}\u00b0")
    print(f"  QWP \u03b30                        : {gamma0:.2f}\u00b0")
    print(f"  Null QWP                      : {q_null:.2f}\u00b0")
    print(f"  Null Analyzer                 : {a_null:.2f}\u00b0")
    print(f"  Extinction ratio              : {ext_ratio_dB:.1f} dB")
    print(f"\nFiles saved:")
    print(f"  - {cal_name}/sample_calibration.json")
    print(f"  - {cal_name}/analyzer_sweep_QWPout_sample.csv + .png")
    print(f"  - {cal_name}/hwp_sweep_QWPout_sample.csv + .png")
    print(f"  - {cal_name}/qwp_sweep_QWPin_sample.csv + .png")
    print(f"  - {cal_name}/qwp_sweep_fit_sample.png")
    print(f"  - {cal_name}/nulling_path_sample.csv")
    print(f"  - {cal_name}/analyzer_sweep_at_null_QWP.csv + .png")
    print(f"  - {cal_name}/null_summary_sample.txt")
    print(f"{'=' * 60}")

    if args.no_plot_windows:
        input("\nSAMPLE CALIBRATION COMPLETE. Review the GUI plots, then press Enter to cleanup hardware...")
    else:
        plt.show(block=False)
        plt.pause(0.5)
        input("\nSAMPLE CALIBRATION COMPLETE. Plots are displayed. Press Enter to close plots and cleanup hardware...")
        plt.close('all')

    # ==================== Cleanup ====================
    print("\n[CLEANUP] Shutting down hardware...")
    try:
        if USE_LASER and laser is not None:
            laser.off()
            laser.close()
        print("  - Laser OFF")
    except Exception:
        pass
    try:
        det.close()
        print("  - Detector closed")
    except Exception:
        pass
    for rot in (rot_qwp, rot_hwp, rot_anl):
        try:
            rot.close()
        except Exception:
            pass
    print("  - Rotators closed")
    disconnect_stage(device_x, device_y)
    if camera_exporter is not None:
        camera_exporter.stop()
        print("  - GUI camera export stopped")

    print("[CLEANUP] Done. Sample calibration finished.")


if __name__ == "__main__":
    main()
