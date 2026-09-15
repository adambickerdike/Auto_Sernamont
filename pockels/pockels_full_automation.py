#!/usr/bin/env python3
"""
Fully automated Pockels stage campaign.

Top-level orchestration layer that bundles the work of:
  - stage_calibration.py        - Kinesis bring-up, grid handling, full_auto_calibrate
                                  (hill-climb -> fast-peak -> golden-section -> full scan)
  - Pockels_Calibration_2026.py - HWP-dependent QWP/analyser nulling (do_quick_null),
                                  SMU4201 DC poling, Aim-TTi AC drive, lock-in
                                  analyser sweeps, per-HWP plots and summaries
  - arduino_switch_matrix.py    - exclusive channel routing before any voltage hits
                                  a pixel

Phases (each independently DO / LOAD / SKIP):

  Phase 1 - Substrate optical calibration (sample IN, one substrate spot)
      Sweep HWP across the campaign range; at each HWP run do_quick_null to
      record (q_null, a_null, p_null). Saved to substrate_calibration.json.
      This becomes the per-HWP seed table for the per-pixel fast re-null.

  Phase 2 - Stage full-auto calibration (OPTIONAL, default OFF)
      Runs stage.full_auto_calibrate over every pixel as a separate up-front
      pass. Default is to skip this: each pixel is aligned inside Phase 3 just
      before voltage is applied, so a separate alignment pass would be
      redundant work. Enable with --phase-stage do only if you want a stage-
      only calibration run (no measurement) or want to inspect alignment
      diagnostics before the measurement campaign.

  Phase 3 - Per-pixel measurement (the campaign proper)
      For each selected pixel:
        a) Stage move to nominal coords + alignment chain (hill-climb ->
           fast-peak -> golden-section -> full line scan) so we sit on this
           pixel's actual transmission peak.
        b) Arduino route to that pixel's electrode pin.
        c) SMU ramp 0 V -> --poling-voltage, dwell --poling-dwell.
        d) For each HWP angle:
             - AC OFF, fast re-null seeded from the substrate cal at the same
               HWP (handles compositional drift across the chip).
             - AC ON, run the analyser voltage series at this null.
        e) AC OFF, SMU OFF, Arduino OFF, next pixel.

Run from Windows Python, not WSL (Kinesis .NET assemblies).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def configure_text_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_text_streams()

import numpy as np

import analyser_sweep_voltage_series as analyser
import Pockels_Calibration_2026 as pockels
import stage_calibration as stage
from pockels_campaign import fast_renull as campaign_fast_renull
from POL_Chip_Test_Working_2026 import null_qwp_analyzer
from serial_port_resolver import (
    AUTO_PORT,
    is_auto_port,
    list_port_details,
    normalize_com_port,
    resolve_arduino_switch_matrix_port,
    resolve_smu4201_port,
)


CURRENT_PIXEL_TO_PIN = {
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


def install_current_switch_mapping() -> None:
    """Force the current chip-pin -> Arduino switch map into stage routing."""
    values = list(CURRENT_PIXEL_TO_PIN.values())
    if set(CURRENT_PIXEL_TO_PIN) != set(range(1, 101)):
        raise RuntimeError("Current switch mapping must define chip pins 1..100.")
    if set(values) != set(range(1, 101)):
        raise RuntimeError("Current switch mapping must use electrical switches 1..100 exactly once.")

    stage.PIXEL_TO_PIN = dict(CURRENT_PIXEL_TO_PIN)

    def _pixel_to_channel(pix):
        if pix is None or pix < 1 or pix > 100:
            return None
        return stage.PIXEL_TO_PIN.get(int(pix))

    stage.pixel_to_channel = _pixel_to_channel


install_current_switch_mapping()


class FakeLockin:
    """Stand-in for the DSP7230 lock-in for dry-running the campaign without hardware.

    Implements the methods used by Pockels_Calibration_2026 / analyser_sweep_voltage_series:
    connect, apply_safe_startup, get_id, get_sensitivity_volts, set_sensitivity,
    get_overload_byte, get_mag_phase, close. The synthesized magnitude follows a
    Pockels-like sin(2(theta - a_null)) curve scaled by the current Vpp so that
    fit_delta_from_sweep produces non-degenerate output.
    """

    def __init__(self, rot_anl, state):
        self._rot_anl = rot_anl
        self._state = state
        self._sens_idx = 15

    def connect(self):
        print("  [SIM] FakeLockin connected.")

    def apply_safe_startup(self, run_auto_measure=False):
        pass

    def get_id(self):
        return "FakeLockin SIMULATED v1.0"

    def get_sensitivity_volts(self):
        from Lock_In_Mag_Phase_Track import SENS_TABLE_VOLTS
        return SENS_TABLE_VOLTS[self._sens_idx]

    def set_sensitivity(self, idx):
        self._sens_idx = int(idx)

    def get_overload_byte(self):
        return 0

    def get_mag_phase(self):
        try:
            anl = self._rot_anl.get_angle() if self._rot_anl is not None else 0.0
        except Exception:
            anl = 0.0
        if anl is None:
            anl = 0.0
        a_null = float(self._state.get("a_null", 0.0))
        vpp = float(self._state.get("vpp", 1.0))
        delta = math.radians(2.0 * (float(anl) - a_null))
        amp = 0.5e-6 * vpp
        noise = (random.random() - 0.5) * 0.05e-6
        mag = abs(amp * math.sin(delta)) + 0.1e-6 + noise
        phase = (math.degrees(delta)) % 360.0
        return mag, phase

    def close(self):
        pass


class FakeSMU:
    """Stand-in for the SMU4201 when no SMU is connected (dry-run)."""

    def __init__(self):
        self._v = 0.0
        self._out = False

    def idn(self):
        return "FakeSMU SIMULATED v1.0"

    def configure_source_voltage_measure_current(self, *_a, **_kw):
        pass

    def check_errors(self):
        return []

    def set_voltage(self, v):
        self._v = float(v)
        print(f"  [SIM-SMU] set_voltage({self._v:+.3f} V)")

    def output(self, on):
        self._out = bool(on)
        print(f"  [SIM-SMU] output {'ON' if self._out else 'OFF'} at {self._v:+.3f} V")

    def write(self, cmd):
        print(f"  [SIM-SMU] write({cmd!r})")

    def query(self, cmd):
        c = str(cmd).strip().upper()
        if "OUTP" in c and "STAT" in c:
            return "1" if self._out else "0"
        if "VOLT" in c and ("LEVEL" in c or "FIX" in c):
            return f"{self._v:.6f}"
        if c.startswith("SYST") and "HV" in c and "STAT" in c:
            return "ON"
        return "0"

    def close(self):
        self._out = False


def install_funcgen_vpp_spy(state: dict) -> None:
    """Wrap funcgen_send in pockels and analyser modules to track the latest AMPL.

    Used in simulate mode so FakeLockin knows the current Vpp without querying
    the function generator.
    """
    for mod in (pockels, analyser):
        original = getattr(mod, "funcgen_send", None)
        if original is None or getattr(original, "_vpp_spy", False):
            continue

        def make_spy(orig):
            def spy(instr, cmd):
                s = str(cmd).strip().upper()
                if s.startswith("AMPL "):
                    try:
                        state["vpp"] = float(s.split()[1])
                    except Exception:
                        pass
                return orig(instr, cmd)
            spy._vpp_spy = True
            return spy

        mod.funcgen_send = make_spy(original)


OUTDIR_ROOT = "pockels_full_automation"
DEFAULT_AC_VOLTAGES_VPP = [1, 3, 5, 7, 9]
DEFAULT_STAGE_SUBDIR = "stage_alignment"
DEFAULT_PIXELS_SUBDIR = "pockels_pixels"
PROGRESS_JSON = "automation_progress.json"
PIXEL_SUMMARY_CSV = "pixel_measurements.csv"
NULL_SEEDS_JSON = "null_seed_by_hwp.json"

# When the analyser is sitting at the through-sample null after substrate cal,
# the photodiode sees ~uW of leakage and there is no peak to climb. Before any
# stage alignment we offset ANL by this many degrees off the null so the
# photodiode sees a real bright transmission peak; after alignment we return
# ANL to the null so per-HWP fast renull can take over. Same value used by
# pockels_campaign.py.
BRIGHTEN_OFFSET_DEG = 30.0

# Calmer physical motion; reduces inertial overshoot and "sensor error" faults
# from the ELL14 firmware. Matches pockels_campaign.py.
ROTATOR_VELOCITY_PCT = 60


def boot_rotators(rot_hwp, rot_qwp, rot_anl, do_home: bool) -> None:
    """Match pockels_campaign.py's rotator init: home + tare + velocity drop.

    pockels._connect_rotators() deliberately skips homing to preserve a loaded
    calibration. For pockels_full_automation that's the wrong default: the
    substrate cal re-establishes the null anyway, and *not* homing means the
    ELL14 internal counter starts wherever it was left from the previous run,
    drifts, and trips sensor-error faults mid-campaign.

    With do_home=True (the default) every rotator is homed, tared, and velocity
    is dropped to ROTATOR_VELOCITY_PCT, exactly as pockels_campaign.py does at
    startup.
    """
    for rot, name in [(rot_hwp, "HWP"), (rot_qwp, "QWP"), (rot_anl, "ANL")]:
        if do_home:
            try:
                print(f"  [{name}] homing...")
                rot.home(direction=0, settle_s=3.0)
                rot.tare()
            except Exception as e:
                print(f"  [{name}] home/tare failed: {e}")
        try:
            rot.set_velocity(ROTATOR_VELOCITY_PCT)
            print(f"  [{name}] @ {rot.get_angle():.2f} deg, "
                  f"velocity {ROTATOR_VELOCITY_PCT}%")
        except Exception as e:
            print(f"  [{name}] set_velocity({ROTATOR_VELOCITY_PCT}%) failed: {e}")


class StageScopePockelsAdapter:
    """Adapter so one ScopeDetector instance can serve stage and Pockels code."""

    def __init__(self, detector):
        self.detector = detector

    def read_averaged(self, n=2, auto_scale=True):
        if not auto_scale:
            return self.detector.read_averaged(n=n, auto_scale=auto_scale)

        result = (None, None, None, None)
        # ScopeDetector.read_averaged() auto-ranges once per call. After a large
        # optical jump, for example from a 10 mV/div null to a bright HWP point,
        # the first returned average can still be taken on an invalid range.
        # Repeat until the returned average itself fits the current V/div.
        for _ in range(len(stage.SCOPE_VDIV_OPTIONS_MV) + 2):
            result = self.detector.read_averaged(n=n, auto_scale=True)
            mean_v, _std_v, _power_w, vdiv = result
            if self._range_is_settled(mean_v, vdiv):
                break
        return result

    @staticmethod
    def _range_is_settled(mean_v, vdiv):
        if mean_v is None or vdiv is None:
            return True
        if not np.isfinite(mean_v) or not np.isfinite(vdiv) or vdiv <= 0:
            return True

        signal_v = abs(float(mean_v))
        vdiv = float(vdiv)
        options = [float(v) / 1000.0 for v in stage.SCOPE_VDIV_OPTIONS_MV]
        min_vdiv = min(options)
        max_vdiv = max(options)

        too_big = signal_v > vdiv * stage.SMART_RANGE_MULTIPLIER * 0.98
        if too_big and vdiv < max_vdiv:
            return False

        too_small = signal_v < vdiv * stage.SMART_RANGE_MIN_FILL * 0.70
        if too_small and vdiv > min_vdiv:
            return False

        return True

    def read_power_w_stable(self):
        mean_v, _std_v, power_w, _vdiv = self.read_averaged()
        if mean_v is None:
            return float("nan"), float("nan")
        if power_w is None:
            power_w = float("nan")
        return float(power_w), float(mean_v)


def sanitize_run_name(name: str) -> str:
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name.strip())
    return clean.strip("_") or "unnamed"


def parse_float_list(raw: str) -> list[float]:
    vals = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        vals.append(float(part))
    if not vals:
        raise ValueError("empty float list")
    return vals


def parse_pixel_selection(raw: str | None, total: int) -> list[int]:
    if raw is None or raw.strip().lower() in ("", "all"):
        return list(range(1, total + 1))

    selected: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            selected.update(range(lo, hi + 1))
        else:
            selected.add(int(token))

    bad = [p for p in selected if p < 1 or p > total]
    if bad:
        raise ValueError(f"pixel(s) outside 1..{total}: {bad}")
    return sorted(selected)


def configure_shared_measurement_globals(args) -> list[float]:
    voltages = parse_float_list(args.voltages)

    pockels.AC_VOLTAGES_VPP = voltages
    analyser.AC_VOLTAGES_VPP = voltages

    pockels.ANL_START = args.anl_start
    pockels.ANL_STOP = args.anl_stop
    analyser.ANL_START = args.anl_start
    analyser.ANL_STOP = args.anl_stop

    pockels.LOCKIN_SETTLE_S = args.lockin_settle
    pockels.LOCKIN_AVG_READINGS = args.lockin_avg_readings
    pockels.LOCKIN_READ_DELAY_S = args.lockin_read_delay
    analyser.LOCKIN_SETTLE_S = args.lockin_settle
    analyser.LOCKIN_AVG_READINGS = args.lockin_avg_readings
    analyser.LOCKIN_READ_DELAY_S = args.lockin_read_delay

    pockels.FUNCGEN_FREQ_HZ = args.funcgen_freq
    analyser.FUNCGEN_FREQ_HZ = args.funcgen_freq
    pockels.FUNCGEN_SETTLE_S = args.funcgen_settle
    analyser.FUNCGEN_SETTLE_S = args.funcgen_settle

    args.smu_port = normalize_com_port(args.smu_port)
    if not is_auto_port(args.smu_port):
        pockels.SMU_PORT = args.smu_port
    pockels.SMU_COMPLIANCE_A = args.smu_compliance
    pockels.SMU_RAMP_STEP_V = args.smu_ramp_step
    pockels.SMU_RAMP_DWELL_S = args.smu_ramp_dwell

    return voltages


def print_serial_ports(include_bluetooth: bool = False) -> None:
    print("\nDetected serial ports:")
    for line in list_port_details(
        port_labels=serial_port_labels(),
        include_bluetooth=include_bluetooth,
    ):
        print(f"  {line}")


def serial_port_labels() -> dict[str, str]:
    qwp_label = f"QWP rotator, Elliptec addr {getattr(pockels, 'ADDR_QWP', '?')}"
    hwp_label = f"HWP rotator, Elliptec addr {getattr(pockels, 'ADDR_HWP', '?')}"
    analyzer_label = f"Analyzer rotator, Elliptec addr {getattr(pockels, 'ADDR_ANL', '?')}"

    labels = {
        # Physical FTDI serials from this setup. These remain stable even if
        # Windows renumbers COM ports.
        "serial:DP06TVA5A": qwp_label,
        "serial:DP06UFHCA": hwp_label,
        "serial:DP06TV7LA": analyzer_label,
        normalize_com_port(getattr(pockels, "PORT_QWP", None)): qwp_label,
        normalize_com_port(getattr(pockels, "PORT_HWP", None)): hwp_label,
        normalize_com_port(getattr(pockels, "PORT_ANL", None)): analyzer_label,
    }

    funcgen_port = com_port_from_asrl_resource(getattr(analyser, "FUNCGEN_RESOURCE", None))
    if funcgen_port:
        labels[funcgen_port] = "Aim-TTi function generator"
    labels["serial:DA205A77"] = "Aim-TTi function generator"

    return {port: label for port, label in labels.items() if port}


def com_port_from_asrl_resource(resource: str | None) -> str | None:
    if not resource:
        return None
    match = re.search(r"(?i)\bASRL\s*(\d+)\s*::", str(resource))
    if match:
        return f"COM{int(match.group(1))}"
    return normalize_com_port(resource)


def _rotator_ports_for_serial_probe_exclusion() -> list[str | None]:
    return [
        getattr(pockels, "PORT_HWP", None),
        getattr(pockels, "PORT_QWP", None),
        getattr(pockels, "PORT_ANL", None),
    ]


def resolve_instrument_serial_ports(args) -> None:
    """Resolve auto COM-port requests before hardware bring-up."""
    args.arduino_port = normalize_com_port(args.arduino_port)
    args.smu_port = normalize_com_port(args.smu_port)
    args._smu_auto_not_found = False

    print("\nResolving serial ports...")
    rotator_port_exclusions = _rotator_ports_for_serial_probe_exclusion()

    if args.no_arduino:
        print("  Arduino switch matrix: disabled by --no-arduino")
    else:
        arduino_resolution = resolve_arduino_switch_matrix_port(
            args.arduino_port,
            baudrate=args.arduino_baud,
            exclude_ports=rotator_port_exclusions,
        )
        if arduino_resolution.port:
            args.arduino_port = arduino_resolution.port
            print(f"  Arduino switch matrix: {args.arduino_port} ({arduino_resolution.method})")
            print(f"    {arduino_resolution.message}")
        elif args.allow_no_arduino:
            print(f"  [WARN] {arduino_resolution.message}")
            print("  [WARN] Continuing without Arduino switch matrix because --allow-no-arduino was used.")
            args.no_arduino = True
            args.arduino_port = None
        else:
            print_serial_ports()
            raise RuntimeError(
                arduino_resolution.message
                + " Use --arduino-port COM14 to force it, or --list-serial-ports to inspect ports."
            )

    smu_exclusions = list(rotator_port_exclusions)
    if args.arduino_port:
        smu_exclusions.append(args.arduino_port)
    smu_resolution = resolve_smu4201_port(
        args.smu_port,
        baudrate=pockels.SMU_BAUD,
        exclude_ports=smu_exclusions,
    )
    if smu_resolution.port:
        args.smu_port = smu_resolution.port
        print(f"  SMU4201: {args.smu_port} ({smu_resolution.method})")
        print(f"    {smu_resolution.message}")
    elif is_auto_port(args.smu_port):
        args._smu_auto_not_found = True
        if args.require_smu:
            print_serial_ports()
            raise RuntimeError(
                smu_resolution.message
                + " Use --smu-port COM11 to force it, or --list-serial-ports to inspect ports."
            )
        print(f"  [WARN] {smu_resolution.message}")
        print("  [WARN] The run will use FakeSMU unless a manual --smu-port is supplied.")
    else:
        print(f"  SMU4201: {args.smu_port} (manual)")


def make_run_dir(args) -> Path:
    if args.resume_run:
        root_dir = Path(args.resume_run).expanduser().resolve()
        if not root_dir.is_dir():
            raise FileNotFoundError(f"--resume-run does not exist: {root_dir}")
        return root_dir

    run_name = args.run_name
    if not run_name:
        run_name = input("Run name for full automation: ").strip() or "unnamed"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_dir = Path(OUTDIR_ROOT) / f"{timestamp}_{sanitize_run_name(run_name)}"
    root_dir.mkdir(parents=True, exist_ok=True)
    return root_dir.resolve()


def set_stage_output_dir(stage_dir: Path) -> None:
    stage.OUTDIR = str(stage_dir)
    stage.IMGDIR = str(stage_dir / "images")
    stage.ALIGNDIR = str(stage_dir / "alignment_logs")
    stage.JSON_FILE = str(stage_dir / "pixel_positions.json")
    stage.CSV_FILE = str(stage_dir / "pixel_positions.csv")
    os.makedirs(stage.OUTDIR, exist_ok=True)
    os.makedirs(stage.IMGDIR, exist_ok=True)
    os.makedirs(stage.ALIGNDIR, exist_ok=True)


def load_stage_pixels(stage_cal_path: str | None) -> tuple[list[dict], int]:
    pixels = stage.build_grid()
    if not stage_cal_path:
        return pixels, 0

    path = Path(stage_cal_path).expanduser()
    if path.is_dir():
        path = path / "pixel_positions.json"
    with open(path, "r") as f:
        data = json.load(f)

    saved_pixels = data.get("pixels", [])
    for saved in saved_pixels:
        pix = int(saved.get("pixel", 0))
        if 1 <= pix <= len(pixels):
            merged = pixels[pix - 1]
            merged.update(saved)
            pixels[pix - 1] = merged
    n_calibrated = sum(1 for p in pixels if p.get("calibrated_x_motor") is not None)
    return pixels, n_calibrated


def load_resume_state(root_dir: Path) -> tuple[list[dict] | None, list[dict], dict]:
    progress_path = root_dir / PROGRESS_JSON
    if not progress_path.is_file():
        return None, [], {}
    with open(progress_path, "r") as f:
        data = json.load(f)
    pixels = data.get("pixels")
    records = data.get("pixel_records", [])
    seeds = data.get("null_seed_by_hwp", {})
    return pixels, records, seeds


def save_progress(root_dir: Path, pixels: list[dict], records: list[dict], seeds: dict, config: dict) -> None:
    progress = {
        "updated_utc": datetime.utcnow().isoformat() + "Z",
        "config": config,
        "n_pixels": len(pixels),
        "n_measurement_complete": sum(
            1 for p in pixels if p.get("measurement_status") == "complete"
        ),
        "pixels": pixels,
        "pixel_records": records,
        "null_seed_by_hwp": seeds,
    }
    with open(root_dir / PROGRESS_JSON, "w") as f:
        json.dump(progress, f, indent=2)

    if records:
        fields = [
            "pixel", "status", "channel", "started", "completed",
            "calibrated_x_motor", "calibrated_y_motor",
            "smu_poling_voltage_v", "output_dir", "error",
        ]
        with open(root_dir / PIXEL_SUMMARY_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for rec in records:
                writer.writerow({k: rec.get(k, "") for k in fields})

    with open(root_dir / NULL_SEEDS_JSON, "w") as f:
        json.dump(seeds, f, indent=2)


def resolve_optical_calibration(cal_arg: str | None):
    if cal_arg is None:
        cal_arg = "latest"
    if cal_arg.strip().lower() in ("none", "skip", "current"):
        return None

    if cal_arg.strip().lower() == "latest":
        cal_path = pockels._find_latest_calibration_json()
        if cal_path is None:
            print("  [WARN] No usable optical calibration JSON found; using current rotator positions.")
            return None
    else:
        cal_path = cal_arg

    cal = pockels._load_calibration_json(cal_path)
    print(f"  Optical calibration: {cal['source']}")
    return cal


def save_loaded_optical_calibration(root_dir: Path, cal) -> None:
    if cal is None:
        return
    raw = cal.get("raw") or {}
    out = {
        "source": cal.get("source"),
        "hwp_deg": cal.get("hwp_deg"),
        "q_null_deg": cal.get("q_null_deg"),
        "a_null_deg": cal.get("a_null_deg"),
        "p_null_W": cal.get("p_null_W"),
        "qwp_axis_deg": raw.get("QWP_gamma0_fit_deg"),
        "qwp_retardance_deg": raw.get("QWP_delta_deg"),
        "qwp_visibility": raw.get("QWP_visibility"),
        "analyzer_extinction_deg_QWPout": raw.get("Analyzer_extinction_deg_QWPout"),
        "analyzer_parallel_deg_QWPout": raw.get("Analyzer_parallel_deg_QWPout"),
        "extinction_dB_vs_QWPoutMax": raw.get("Extinction_dB_vs_QWPoutMax"),
        "raw": raw,
    }
    with open(root_dir / "loaded_optical_calibration.json", "w") as f:
        json.dump(out, f, indent=2)


SUBSTRATE_CAL_FILENAME = "substrate_calibration.json"
SUBSTRATE_NULL_TOL_DEG = 0.02
SUBSTRATE_NULL_MAX_EVALS = 300
SUBSTRATE_RESCUE_ABS_W = 2.0e-5
SUBSTRATE_RESCUE_RATIO = 8.0
SUBSTRATE_ACCEPT_NULL_MV = 14.5
SUBSTRATE_CERTIFY_MARGIN_MV = 1.50
SUBSTRATE_FIRST_HWP_RETRY_MAX_MV = SUBSTRATE_ACCEPT_NULL_MV
SUBSTRATE_FIRST_HWP_LEGACY_SPAN_DEG = 45.0


def substrate_calibration_run_identity(root_dir: Path) -> dict:
    """Return stable human-readable identity fields for a null-table file."""
    root_dir = Path(root_dir)
    config: dict = {}
    config_path = root_dir / "run_config.json"
    try:
        with config_path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            config.update(loaded)
    except Exception:
        pass

    folder_name = str(root_dir.name)
    run_started_at = ""
    match = re.match(r"^(\d{8}_\d{6})(?:_|$)", folder_name)
    if match:
        try:
            run_started_at = datetime.strptime(
                match.group(1), "%Y%m%d_%H%M%S"
            ).isoformat()
        except ValueError:
            pass

    chip_id = str(config.get("chip_id", "") or "").strip()
    run_name = str(config.get("run_name", "") or "").strip()
    return {
        "run_folder": folder_name,
        "chip_id": chip_id,
        "run_name": run_name,
        "run_started_at": run_started_at,
        "label": folder_name,
    }


def substrate_calibration_alias_filename(root_dir: Path) -> str:
    """Date/chip/run-bearing alias while retaining the canonical filename."""
    folder_label = sanitize_run_name(Path(root_dir).name)
    return f"{folder_label}_{SUBSTRATE_CAL_FILENAME}"


def save_substrate_calibration_snapshot(
    root_dir: Path,
    rows: list[dict],
    table: dict,
    filename: str,
    *,
    alias_filenames: tuple[str, ...] = (),
) -> list[Path]:
    """Write a HWP-level substrate calibration checkpoint without risking partial JSON."""
    root_dir = Path(root_dir)
    identity = substrate_calibration_run_identity(root_dir)
    out = {
        "calibration_type": "per_hwp_substrate_null_seed_table",
        "calibration_label": identity["label"],
        "run_identity": identity,
        "run_folder": identity["run_folder"],
        "chip_id": identity["chip_id"],
        "run_name": identity["run_name"],
        "run_started_at": identity["run_started_at"],
        "timestamp": datetime.now().isoformat(),
        "n_hwp": len(rows),
        "hwp_angles_deg": [r["hwp_deg"] for r in rows],
        "per_hwp": table,
    }
    paths: list[Path] = []
    for output_name in dict.fromkeys((filename, *alias_filenames)):
        path = root_dir / str(output_name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        os.replace(tmp, path)
        paths.append(path)
    return paths


def substrate_null_mV_bad(p_null_mV: float | None) -> bool:
    try:
        value = float(p_null_mV)
    except Exception:
        return True
    limit = float(SUBSTRATE_ACCEPT_NULL_MV) + float(SUBSTRATE_CERTIFY_MARGIN_MV)
    return (not np.isfinite(value)) or value > limit


def substrate_null_mV_marginal(p_null_mV: float | None) -> bool:
    try:
        value = float(p_null_mV)
    except Exception:
        return False
    return (
        np.isfinite(value)
        and float(SUBSTRATE_ACCEPT_NULL_MV) < value
        <= float(SUBSTRATE_ACCEPT_NULL_MV) + float(SUBSTRATE_CERTIFY_MARGIN_MV)
    )


def substrate_null_needs_rescue(
    p_null: float,
    p_ref: float | None,
    p_null_mV: float | None = None,
) -> bool:
    """True when a per-HWP substrate null is too high to trust."""
    if p_null_mV is not None and substrate_null_mV_bad(p_null_mV):
        return True
    if p_null is None or not np.isfinite(p_null):
        return True
    if p_ref is not None and np.isfinite(p_ref):
        threshold = max(SUBSTRATE_RESCUE_ABS_W, SUBSTRATE_RESCUE_RATIO * float(p_ref))
    else:
        threshold = SUBSTRATE_RESCUE_ABS_W
    return float(p_null) > threshold


def read_detector_null_mv(detector_adapter) -> float:
    try:
        _p_check, v_check = detector_adapter.read_power_w_stable()
        return float(v_check) * 1e3
    except Exception:
        return float("nan")


def require_substrate_null_within_limit(
    *,
    hwp_deg: float,
    q_null: float,
    a_null: float,
    p_null: float,
    p_null_mV: float,
) -> None:
    if not substrate_null_mV_bad(p_null_mV):
        return
    raise RuntimeError(
        f"Substrate null failed certification at HWP {float(hwp_deg):.2f} deg: "
        f"q={float(q_null):.2f}, a={float(a_null):.2f}, "
        f"P={float(p_null):.3e} W, Vdet={float(p_null_mV):.2f} mV "
        f"> hard limit {float(SUBSTRATE_ACCEPT_NULL_MV) + float(SUBSTRATE_CERTIFY_MARGIN_MV):.2f} mV "
        f"(target {float(SUBSTRATE_ACCEPT_NULL_MV):.2f} mV). "
        "Not saving this as a trusted HWP seed."
    )


def null_is_better(candidate_p, candidate_mV, current_p, current_mV) -> bool:
    if np.isfinite(candidate_mV) and np.isfinite(current_mV):
        return float(candidate_mV) < float(current_mV)
    if np.isfinite(candidate_p) and np.isfinite(current_p):
        return float(candidate_p) < float(current_p)
    return np.isfinite(candidate_p) and not np.isfinite(current_p)


def substrate_rescue_seed(det, rot_qwp, rot_anl, q_seed, a_seed,
                          log_path=None) -> tuple[float, float, float]:
    """Broad low-resolution search used only when a descent result is suspect."""
    q_offsets = [-45.0, -30.0, -18.0, -9.0, 0.0, 9.0, 18.0, 30.0, 45.0]
    a_offsets = [-45.0, -30.0, -18.0, -9.0, 0.0, 9.0, 18.0, 30.0, 45.0]
    best_q = float(q_seed)
    best_a = float(a_seed)
    best_p = float("inf")
    rows = []

    def measure(q, a):
        try:
            rot_qwp.set_angle(float(q) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_qwp, float(q) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        try:
            rot_anl.set_angle(float(a) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_anl, float(a) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        p, _v = det.read_power_w_stable()
        if p is None or not np.isfinite(p):
            p = float("inf")
        rows.append((float(q), float(a), float(p)))
        return float(p)

    for dq in q_offsets:
        for da in a_offsets:
            q = float(q_seed) + dq
            a = float(a_seed) + da
            p = measure(q, a)
            if p < best_p:
                best_q, best_a, best_p = q, a, p

    if log_path:
        with open(log_path, "w") as f:
            f.write("q_deg,a_deg,P_W\n")
            for q, a, p in rows:
                f.write(f"{q:.4f},{a:.4f},{p:.6e}\n")

    print(f"    [RESCUE] coarse search best q={best_q:.2f}, "
          f"a={best_a:.2f}, P={best_p:.3e} W")
    return float(best_q), float(best_a), float(best_p)


def null_descent(det, rot_qwp, rot_anl, q_seed, a_seed,
                 init_step_deg=5.0,
                 tol_step_deg=0.05,
                 max_evals=200, log_path=None,
                 target_mV=None):
    """Adaptive coordinate descent with an optional detector-mV acceptance stop.

    If target_mV is set, a confirmed detector reading at or below that value is
    accepted immediately. Otherwise stops only when:
      (1) both QWP and ANL step sizes have shrunk below tol_step_deg, AND
      (2) a final certification probe at +/- tol_step_deg in both axes shows
          NO improvement in any of the four directions.

    Both conditions together mean: we're at a point where moving by less than
    tol_step_deg in either axis can't lower the photodiode reading. That is
    the definition of a local minimum within tolerance.

    Hooke-Jeeves logic per axis:
      1. Try +step. If P drops, accept and GROW step by 1.6x (we're making
         progress, take bigger bites toward the minimum).
      2. Else try -step. If P drops, accept and grow step.
      3. Else neither direction helped at this step size -> SHRINK step
         by 0.5x and continue. The next iteration will probe at that
         smaller step, eventually narrowing in on the true minimum.

    Diagonal pattern step: when both axes succeeded in their last move with
    the same sign, the descent is walking diagonally; we take an extra
    "pattern" step in that diagonal direction. This breaks the coordinate-
    descent zigzag near the minimum and converges much faster on tilted
    paraboloids (which the Pockels null surface is).

    Final certification: once both step sizes are below tol_step_deg, probe
    at +/- tol_step_deg on each axis. If any of the 4 probes is lower than
    the current point, descent is NOT converged; resume with that probe as
    new center. If all 4 probes are higher, we have certified a local minimum.

    Returns (q_null, a_null, p_null). Writes a per-probe CSV log if log_path
    is given.
    """
    log_rows = [] if log_path else None
    n_evals = [0]
    best = {"q": float(q_seed), "a": float(a_seed), "p": float("inf"), "mV": float("inf")}
    last_measure = {"q": float("nan"), "a": float("nan"), "p": float("nan"), "mV": float("nan")}
    stopped_by_threshold = [False]
    target_mV = float(target_mV) if target_mV is not None and np.isfinite(float(target_mV)) else None

    def measure(q, a):
        try:
            rot_qwp.set_angle(float(q) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_qwp, float(q) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        try:
            rot_anl.set_angle(float(a) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_anl, float(a) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        p, v = det.read_power_w_stable()
        if p is None or not np.isfinite(p):
            p = float("inf")
        try:
            v_mV = float(v) * 1e3
        except Exception:
            v_mV = float("nan")
        n_evals[0] += 1
        last_measure.update({"q": float(q), "a": float(a), "p": float(p), "mV": float(v_mV)})
        if log_rows is not None:
            log_rows.append((float(q), float(a), float(p), float(v_mV)))
        acceptable_mV = target_mV is not None and np.isfinite(v_mV) and v_mV <= target_mV
        existing_acceptable_mV = (
            target_mV is not None
            and np.isfinite(best.get("mV", float("nan")))
            and float(best["mV"]) <= target_mV
        )
        if p < best["p"] or (acceptable_mV and (not existing_acceptable_mV or v_mV < best["mV"])):
            best["q"], best["a"], best["p"], best["mV"] = float(q), float(a), float(p), float(v_mV)
        return float(p)

    def accept_threshold_if_confirmed():
        if target_mV is None:
            return False
        if not np.isfinite(best.get("mV", float("nan"))) or float(best["mV"]) > target_mV:
            return False
        q_best, a_best = float(best["q"]), float(best["a"])
        measure(q_best, a_best)
        confirm_mV = float(last_measure.get("mV", float("nan")))
        if np.isfinite(confirm_mV) and confirm_mV <= target_mV:
            best["q"], best["a"], best["p"], best["mV"] = (
                q_best,
                a_best,
                float(last_measure["p"]),
                confirm_mV,
            )
            stopped_by_threshold[0] = True
            return True
        # Avoid repeatedly accepting a one-off low noise sample at the same point.
        if abs(float(best["q"]) - q_best) < 1e-9 and abs(float(best["a"]) - a_best) < 1e-9:
            best["p"] = float(last_measure.get("p", best["p"]))
            best["mV"] = confirm_mV
        return False

    def try_axis(axis, q, a, step, p_curr):
        """Returns (q, a, p, new_step, last_dir).
        last_dir is +1 if +step accepted, -1 if -step accepted, 0 if shrink."""
        if axis == "q":
            qp, ap = q + step, a
            qm, am = q - step, a
        else:
            qp, ap = q, a + step
            qm, am = q, a - step

        p_plus = measure(qp, ap)
        if p_plus < p_curr:
            return qp, ap, p_plus, step * 1.6, +1
        p_minus = measure(qm, am)
        if p_minus < p_curr:
            return qm, am, p_minus, step * 1.6, -1
        return q, a, p_curr, step * 0.5, 0

    q, a = float(q_seed), float(a_seed)
    p_curr = measure(q, a)
    if accept_threshold_if_confirmed():
        q, a, p_curr = float(best["q"]), float(best["a"]), float(best["p"])
    step_q = float(init_step_deg)
    step_a = float(init_step_deg)
    last_dir_q = 0
    last_dir_a = 0

    while n_evals[0] < max_evals and not stopped_by_threshold[0]:
        # ---- main descent cycle ----
        # QWP axis
        q, a, p_curr, step_q, last_dir_q = try_axis(
            "q", q, a, step_q, p_curr
        )
        if accept_threshold_if_confirmed():
            q, a, p_curr = float(best["q"]), float(best["a"]), float(best["p"])
            break
        # ANL axis
        q, a, p_curr, step_a, last_dir_a = try_axis(
            "a", q, a, step_a, p_curr
        )
        if accept_threshold_if_confirmed():
            q, a, p_curr = float(best["q"]), float(best["a"]), float(best["p"])
            break

        # ---- pattern (diagonal) step ----
        # If both axes moved in a definite direction this cycle, the
        # descent is walking diagonally. Take an extra combined step in
        # that direction to short-circuit coordinate-descent zigzag.
        if last_dir_q != 0 and last_dir_a != 0:
            pat_step = 0.5 * (step_q + step_a) / 1.6  # use the un-grown size
            qd = q + last_dir_q * pat_step
            ad = a + last_dir_a * pat_step
            p_diag = measure(qd, ad)
            if p_diag < p_curr:
                q, a, p_curr = qd, ad, p_diag
            if accept_threshold_if_confirmed():
                q, a, p_curr = float(best["q"]), float(best["a"]), float(best["p"])
                break

        # ---- convergence test ----
        if step_q < tol_step_deg and step_a < tol_step_deg:
            # Final certification: 4 probes at +/- tol on each axis.
            cert_q_plus = measure(q + tol_step_deg, a)
            cert_q_minus = measure(q - tol_step_deg, a)
            cert_a_plus = measure(q, a + tol_step_deg)
            cert_a_minus = measure(q, a - tol_step_deg)
            cert_min = min(cert_q_plus, cert_q_minus, cert_a_plus, cert_a_minus)
            if cert_min >= p_curr:
                # All 4 directions are uphill or equal -> certified local min.
                # Restore center (just moved off it for the cert probes).
                measure(q, a)
                break
            else:
                # One direction is still downhill at this tolerance: not
                # converged. Resume descent at that point, with step bumped
                # back up so we can keep moving.
                if cert_q_plus == cert_min:
                    q, p_curr = q + tol_step_deg, cert_q_plus
                elif cert_q_minus == cert_min:
                    q, p_curr = q - tol_step_deg, cert_q_minus
                elif cert_a_plus == cert_min:
                    a, p_curr = a + tol_step_deg, cert_a_plus
                else:
                    a, p_curr = a - tol_step_deg, cert_a_minus
                step_q = max(step_q, tol_step_deg * 4.0)
                step_a = max(step_a, tol_step_deg * 4.0)
                continue

        if step_q < tol_step_deg * 0.1 and step_a < tol_step_deg * 0.1:
            # Steps have collapsed below 1/10 of tol on both axes: stop.
            break

    # Final read at the best position seen across the entire descent.
    q, a = best["q"], best["a"]
    p_final = measure(q, a)

    if log_rows is not None and log_path:
        with open(log_path, "w") as f:
            f.write("q_deg,a_deg,P_W,Vdet_mV\n")
            for r in log_rows:
                f.write(f"{r[0]:.4f},{r[1]:.4f},{r[2]:.6e},{r[3]:.3f}\n")

    if stopped_by_threshold[0]:
        print(f"    [DESCENT] {n_evals[0]} probes, accepted Vdet={best['mV']:.2f} mV "
              f"<= {target_mV:.2f} mV")
    else:
        print(f"    [DESCENT] {n_evals[0]} probes, "
              f"converged at step q={step_q:.3f}, a={step_a:.3f} deg "
              f"(tol {tol_step_deg:.3f})")
    return float(q), float(a), float(p_final)


def null_fast_rsm(det, rot_qwp, rot_anl, q_seed, a_seed,
                  span_deg=10.0, max_iter=4, target_p_W=5e-6,
                  log_path=None):
    """Fast 2D null via quadratic Response Surface Method (Newton-like).

    At each iteration:
      1. Probe 6 points around (q, a): center, +/-q, +/-a, +diagonal at span.
      2. Fit P(dq, da) = c0 + c1*dq + c2*da + c3*dq^2 + c4*da^2 + c5*dq*da.
      3. If Hessian is positive-definite: solve grad P = 0 in closed form,
         move there, read. This is one Newton step.
         If Newton step lands lower than the best probe, accept it.
         Otherwise revert to the best probed point.
      4. If Hessian is NOT positive-definite (saddle / cubic dominates):
         take a steepest-descent step toward the best probed point.
      5. Shrink span and iterate. Stop when P drops below target_p_W,
         or when max_iter is exhausted.

    Design intent: reach the same uW null as null_qwp_analyzer in ~7-15 scope
    reads instead of ~80, by using local Hessian information (Newton) instead
    of bisection (golden-section). For a quadratic surface a single Newton
    step is exact - we use a few iterations to handle the cubic corrections.

    Returns (q_null, a_null, p_null) and writes a per-probe CSV trace if
    log_path is given.
    """
    log_rows = [] if log_path else None

    def measure(q, a):
        # Direct set_angle, short settle - no chunking, much faster than
        # safe_move_abs for tiny probe steps. The rotator's own _safe_rot_call
        # still wraps every move so any sensor-error fault is recovered.
        try:
            rot_qwp.set_angle(float(q) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_qwp, float(q) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        try:
            rot_anl.set_angle(float(a) % 360.0, settle_s=0.15)
        except Exception:
            pockels.safe_move_abs(rot_anl, float(a) % 360.0,
                                  settle_s=pockels.MOVE_SETTLE_S)
        p, v = det.read_power_w_stable()
        if log_rows is not None:
            log_rows.append((float(q), float(a), float(p) if p is not None else float("nan")))
        return float(p) if p is not None else float("nan")

    q, a = float(q_seed), float(a_seed)
    span = float(span_deg)
    best_p = float("inf")
    n_evals = 0

    for it in range(max_iter):
        # 6-probe pattern: covers center, +/-q, +/-a, and the diagonal for
        # the cross term c5. Minimum number of probes for the 6-coefficient fit.
        probes = [
            (q,        a       ),
            (q + span, a       ),
            (q - span, a       ),
            (q,        a + span),
            (q,        a - span),
            (q + span, a + span),
        ]
        Ps = []
        for qq, aa in probes:
            Ps.append(measure(qq, aa))
            n_evals += 1

        # Drop any failed probes (NaN) - if too few survive, fall back to
        # the lowest valid probe and shrink span.
        valid = [(p, qq, aa) for p, (qq, aa) in zip(Ps, probes) if np.isfinite(p)]
        if len(valid) < 6:
            if not valid:
                break
            valid.sort()
            best_p, q, a = valid[0]
            span = max(1.5, span * 0.5)
            continue

        # Local linear least-squares: build design matrix in centred coords.
        rows = []
        for (qq, aa), pval in zip(probes, Ps):
            dq = qq - q
            da = aa - a
            rows.append([1.0, dq, da, dq * dq, da * da, dq * da])
        A = np.array(rows)
        b = np.array(Ps)
        try:
            coeffs, *_ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            break
        c0, c1, c2, c3, c4, c5 = (float(x) for x in coeffs)

        # Hessian H = [[2c3, c5], [c5, 2c4]]
        det_H = 4.0 * c3 * c4 - c5 * c5
        pos_def = (c3 > 0.0) and (c4 > 0.0) and (det_H > 0.0)

        i_best_probe = int(np.argmin(Ps))
        p_best_probe = float(Ps[i_best_probe])
        q_best_probe, a_best_probe = probes[i_best_probe]

        if pos_def:
            # Newton step: x_new = x_seed - H^-1 * grad
            #            = (-2c4*c1 + c5*c2)/det_H, (c5*c1 - 2c3*c2)/det_H
            dq_opt = (-2.0 * c4 * c1 + c5 * c2) / det_H
            da_opt = ( c5 * c1 - 2.0 * c3 * c2) / det_H

            # Don't extrapolate further than 3x the probe span - that's where
            # the local quadratic fit stops being trustworthy.
            if abs(dq_opt) > 3.0 * span or abs(da_opt) > 3.0 * span:
                q, a, best_p = q_best_probe, a_best_probe, p_best_probe
            else:
                q_new, a_new = q + dq_opt, a + da_opt
                p_newton = measure(q_new, a_new)
                n_evals += 1
                if np.isfinite(p_newton) and p_newton < p_best_probe:
                    q, a, best_p = q_new, a_new, p_newton
                else:
                    q, a, best_p = q_best_probe, a_best_probe, p_best_probe
        else:
            # Non-PD Hessian: surface looks like a saddle/cubic at this scale.
            # Walk toward the best probed point - that's the steepest-descent
            # direction over this probe set.
            q, a, best_p = q_best_probe, a_best_probe, p_best_probe

        if best_p <= target_p_W:
            # Hit the target extinction; do a tight final refinement.
            if span > 1.0:
                span = max(0.8, span * 0.35)
            else:
                break
        else:
            # Still bright; shrink span by 0.45 (roughly golden-section ratio).
            span = max(0.8, span * 0.45)

    # Final read at the converged position so the returned p_null is honest.
    p_final = measure(q, a)
    if not np.isfinite(p_final):
        p_final = best_p

    if log_rows is not None and log_path:
        with open(log_path, "w") as f:
            f.write("q_deg,a_deg,P_W\n")
            for r in log_rows:
                f.write(f"{r[0]:.4f},{r[1]:.4f},{r[2]:.6e}\n")

    print(f"    [RSM] converged in {n_evals} probes, span->{span:.2f} deg")
    return float(q), float(a), float(p_final)


def run_substrate_optical_calibration(
    root_dir: Path,
    hwp_angles,
    rot_hwp,
    rot_qwp,
    rot_anl,
    detector_adapter,
    fallback_seed: tuple[float, float],
) -> dict:
    """Phase 1: at the current substrate spot, sweep HWP and run the rigorous
    QWP+ANL null at each.

    Uses null_qwp_analyzer (alternating golden-section 1D minimization on QWP
    then ANL with shrinking ±20 deg spans, 3 cycles, then quadratic refinement
    + 3x3 local grid) - this is the wide-range nulling from sample_calibration.py
    Step 3, robust to seeds that are tens of degrees off the true null.

    The output per-HWP table seeds Phase 3's fast 6-probe re-null (do_quick_null),
    which is only valid for sub-degree adjustments and would otherwise stick in
    a local minimum.
    """
    print("\n" + "=" * 72)
    print("PHASE 1 - SUBSTRATE OPTICAL CALIBRATION")
    print("=" * 72)
    q_seed, a_seed = float(fallback_seed[0]), float(fallback_seed[1])
    print(f"  Substrate spot: stage at current position.")
    print(f"  HWP grid: {hwp_angles[0]:.1f} -> {hwp_angles[-1]:.1f} deg "
          f"({len(hwp_angles)} points)")
    print(f"  Initial null seed: q={q_seed:.2f}, a={a_seed:.2f}")
    print(f"  Method: null_descent (adaptive coordinate descent + diagonal pattern step)")
    print(f"  Accepts and moves on once the detector null is confirmed <= "
          f"{SUBSTRATE_ACCEPT_NULL_MV:.1f} mV.")
    print(f"  If not yet acceptable, walks downhill until both step sizes shrink")
    print(f"  below {SUBSTRATE_NULL_TOL_DEG:.2f} deg AND a 4-direction certification")
    print(f"  probe at that tolerance shows no further improvement.")
    print(f"  Rescue: if a null is >{SUBSTRATE_RESCUE_ABS_W:.1e} W or "
          f">{SUBSTRATE_RESCUE_RATIO:.0f}x the previous good null, run a broad")
    print(f"  QWP+ANL seed search and descend again instead of accepting it.")
    if len(hwp_angles) > 1:
        print(f"  First HWP guard: after HWP {hwp_angles[1]:.1f} deg establishes the")
        print(f"  q/a trend, revisit HWP {hwp_angles[0]:.1f} deg if its detector null")
        print(f"  is still >{SUBSTRATE_FIRST_HWP_RETRY_MAX_MV:.1f} mV.")

    nulling_logs_dir = root_dir / "substrate_nulling_logs"
    nulling_logs_dir.mkdir(parents=True, exist_ok=True)

    table: dict = {}
    rows = []
    first_hwp_state = None
    prev_hwp = None
    p_seed_ref = None
    for i_hwp, hwp_deg in enumerate(hwp_angles):
        print(f"\n  [Substrate cal {i_hwp + 1}/{len(hwp_angles)}] "
              f"HWP -> {float(hwp_deg):.2f} deg")
        pockels.safe_move_abs(rot_hwp, float(hwp_deg), settle_s=pockels.MOVE_SETTLE_S)
        time.sleep(0.4)
        log_path = str(nulling_logs_dir / f"hwp_{float(hwp_deg):05.1f}_null_path.csv")

        # Predict the new seed from the measured trend. HWP rotates incident
        # polarisation by 2*delta_HWP. On this setup both the analyser null and
        # the QWP compensation track that rotation closely, so move both seeds
        # forward. Keeping QWP fixed made every later HWP descent start about
        # 15 deg wrong for a 7.5 deg HWP step.
        if i_hwp == 0:
            q_eff_seed = float(q_seed)
            a_eff_seed = float(a_seed)
            init_step = 8.0
            seed_msg = f"loaded cal q={q_eff_seed:.2f}, a={a_eff_seed:.2f}"
        else:
            d_hwp = float(hwp_deg) - float(prev_hwp)
            q_eff_seed = (float(q_seed) + 2.0 * d_hwp) % 360.0
            a_eff_seed = (float(a_seed) + 2.0 * d_hwp) % 360.0
            init_step = 3.0
            seed_msg = (f"q={q_eff_seed:.2f}, a={a_eff_seed:.2f} "
                        f"(predicted: prev q,a + 2*{d_hwp:.1f} deg HWP shift)")

        try:
            print(f"    [DESCENT] seed {seed_msg}, "
                  f"init_step {init_step:.1f} deg, "
                  f"tol {SUBSTRATE_NULL_TOL_DEG:.2f} deg")
            q_null, a_null, p_null = null_descent(
                detector_adapter, rot_qwp, rot_anl,
                q_eff_seed, a_eff_seed,
                init_step_deg=init_step,
                tol_step_deg=SUBSTRATE_NULL_TOL_DEG,
                max_evals=SUBSTRATE_NULL_MAX_EVALS,
                log_path=log_path,
                target_mV=SUBSTRATE_ACCEPT_NULL_MV,
            )
            p_null_mV = read_detector_null_mv(detector_adapter)
            if substrate_null_needs_rescue(p_null, p_seed_ref, p_null_mV):
                rescue_log_path = str(
                    nulling_logs_dir / f"hwp_{float(hwp_deg):05.1f}_rescue_grid.csv"
                )
                print(
                    f"    [RESCUE] suspicious null P={p_null:.3e} W, "
                    f"Vdet={p_null_mV:.2f} mV; running broad seed search"
                )
                q_rescue, a_rescue, _p_rescue = substrate_rescue_seed(
                    detector_adapter, rot_qwp, rot_anl,
                    q_eff_seed, a_eff_seed,
                    log_path=rescue_log_path,
                )
                rescue_descent_log_path = str(
                    nulling_logs_dir / f"hwp_{float(hwp_deg):05.1f}_rescue_null_path.csv"
                )
                q2, a2, p2 = null_descent(
                    detector_adapter, rot_qwp, rot_anl,
                    q_rescue, a_rescue,
                    init_step_deg=9.0,
                    tol_step_deg=SUBSTRATE_NULL_TOL_DEG,
                    max_evals=SUBSTRATE_NULL_MAX_EVALS,
                    log_path=rescue_descent_log_path,
                    target_mV=SUBSTRATE_ACCEPT_NULL_MV,
                )
                p2_mV = read_detector_null_mv(detector_adapter)
                if null_is_better(p2, p2_mV, p_null, p_null_mV):
                    q_null, a_null, p_null, p_null_mV = q2, a2, p2, p2_mV
            if substrate_null_mV_bad(p_null_mV):
                print(
                    f"    [NULL HARD FAIL] Vdet={p_null_mV:.2f} mV remains above "
                    f"{SUBSTRATE_ACCEPT_NULL_MV + SUBSTRATE_CERTIFY_MARGIN_MV:.2f} mV "
                    "after fast rescue; not running slow legacy-wide nulling inside the chip map."
                )
            try:
                rot_qwp.set_angle(float(q_null) % 360.0, settle_s=0.15)
            except Exception:
                pockels.safe_move_abs(
                    rot_qwp,
                    float(q_null) % 360.0,
                    settle_s=pockels.MOVE_SETTLE_S,
                )
            try:
                rot_anl.set_angle(float(a_null) % 360.0, settle_s=0.15)
            except Exception:
                pockels.safe_move_abs(
                    rot_anl,
                    float(a_null) % 360.0,
                    settle_s=pockels.MOVE_SETTLE_S,
                )
            try:
                p_check, v_check = detector_adapter.read_power_w_stable()
                if p_check is not None and np.isfinite(float(p_check)):
                    p_null = float(p_check)
                p_null_mV = float(v_check) * 1e3
            except Exception:
                p_null_mV = float("nan")
            print(f"    [NULL] q={q_null:.2f}, a={a_null:.2f}, "
                  f"P={p_null:.3e} W, Vdet={p_null_mV:.2f} mV")
            if substrate_null_mV_marginal(p_null_mV):
                print(
                    f"    [NULL MARGINAL] Vdet={p_null_mV:.2f} mV is within "
                    f"{SUBSTRATE_CERTIFY_MARGIN_MV:.2f} mV certification noise band; "
                    "accepting without slow rescue."
                )
            require_substrate_null_within_limit(
                hwp_deg=float(hwp_deg),
                q_null=q_null,
                a_null=a_null,
                p_null=p_null,
                p_null_mV=p_null_mV,
            )
        except Exception as e:
            print(f"    [NULL FAILED] {e}")
            raise
        q_seed, a_seed, p_seed_ref = float(q_null), float(a_null), float(p_null)
        prev_hwp = float(hwp_deg)

        key = f"{float(hwp_deg):.3f}"
        entry = {
            "q_null_deg": float(q_null),
            "a_null_deg": float(a_null),
            "p_null_W": float(p_null),
            "p_null_mV": float(p_null_mV),
            "pixel": 0,
            "updated": datetime.now().isoformat(),
        }
        table[key] = entry
        rows.append({"hwp_deg": float(hwp_deg), **entry})

        if i_hwp == 0:
            first_hwp_state = {
                "hwp_deg": float(hwp_deg),
                "key": key,
                "row_index": len(rows) - 1,
            }
        elif (
            i_hwp == 1
            and first_hwp_state is not None
            and np.isfinite(float(table[first_hwp_state["key"]].get("p_null_mV", float("nan"))))
            and float(table[first_hwp_state["key"]]["p_null_mV"]) > SUBSTRATE_FIRST_HWP_RETRY_MAX_MV
        ):
            first_hwp = float(first_hwp_state["hwp_deg"])
            first_key = first_hwp_state["key"]
            first_entry = dict(table[first_key])
            d_hwp_first = float(hwp_deg) - first_hwp
            trend_q_seed = (float(q_null) - 2.0 * d_hwp_first) % 360.0
            trend_a_seed = (float(a_null) - 2.0 * d_hwp_first) % 360.0
            best_q = float(first_entry["q_null_deg"])
            best_a = float(first_entry["a_null_deg"])
            best_p = float(first_entry["p_null_W"])
            best_mV = float(first_entry["p_null_mV"])

            print(
                f"\n  [FIRST HWP RETRY] HWP {first_hwp:.2f} deg null was "
                f"Vdet={best_mV:.2f} mV. Revisit using trend seed from "
                f"HWP {float(hwp_deg):.2f}: q={trend_q_seed:.2f}, a={trend_a_seed:.2f}"
            )
            pockels.safe_move_abs(rot_hwp, first_hwp, settle_s=pockels.MOVE_SETTLE_S)
            time.sleep(0.4)
            retry_log_path = str(nulling_logs_dir / f"hwp_{first_hwp:05.1f}_trend_retry_null_path.csv")
            try:
                q_retry, a_retry, p_retry = null_descent(
                    detector_adapter, rot_qwp, rot_anl,
                    trend_q_seed, trend_a_seed,
                    init_step_deg=3.0,
                    tol_step_deg=SUBSTRATE_NULL_TOL_DEG,
                    max_evals=SUBSTRATE_NULL_MAX_EVALS,
                    log_path=retry_log_path,
                    target_mV=SUBSTRATE_ACCEPT_NULL_MV,
                )
                retry_mV = read_detector_null_mv(detector_adapter)
                print(
                    f"    [FIRST HWP RETRY] q={q_retry:.2f}, a={a_retry:.2f}, "
                    f"P={p_retry:.3e} W, Vdet={retry_mV:.2f} mV"
                )
                if null_is_better(p_retry, retry_mV, best_p, best_mV):
                    best_q, best_a, best_p, best_mV = q_retry, a_retry, p_retry, retry_mV
            except Exception as exc:
                print(f"    [FIRST HWP RETRY FAILED] {exc}")

            if np.isfinite(best_mV) and best_mV > SUBSTRATE_FIRST_HWP_RETRY_MAX_MV:
                legacy_log_path = str(nulling_logs_dir / f"hwp_{first_hwp:05.1f}_legacy_wide_null_path.csv")
                print(
                    f"    [FIRST HWP LEGACY] still Vdet={best_mV:.2f} mV; "
                    "running wide-range null_qwp_analyzer for this first HWP only"
                )
                try:
                    q_legacy, a_legacy, p_legacy = null_qwp_analyzer(
                        detector_adapter,
                        rot_qwp,
                        rot_anl,
                        best_q,
                        best_a,
                        init_span_qwp=SUBSTRATE_FIRST_HWP_LEGACY_SPAN_DEG,
                        init_span_anl=SUBSTRATE_FIRST_HWP_LEGACY_SPAN_DEG,
                        cycles=3,
                        tol_deg=0.8,
                        log_path=legacy_log_path,
                    )
                    q_legacy = float(q_legacy) % 360.0
                    a_legacy = float(a_legacy) % 360.0
                    legacy_mV = read_detector_null_mv(detector_adapter)
                    print(
                        f"    [FIRST HWP LEGACY] q={q_legacy:.2f}, a={a_legacy:.2f}, "
                        f"P={p_legacy:.3e} W, Vdet={legacy_mV:.2f} mV"
                    )
                    if null_is_better(p_legacy, legacy_mV, best_p, best_mV):
                        best_q, best_a, best_p, best_mV = q_legacy, a_legacy, p_legacy, legacy_mV
                except Exception as exc:
                    print(f"    [FIRST HWP LEGACY FAILED] {exc}")

            if null_is_better(best_p, best_mV, first_entry["p_null_W"], first_entry["p_null_mV"]):
                updated = {
                    "q_null_deg": float(best_q),
                    "a_null_deg": float(best_a),
                    "p_null_W": float(best_p),
                    "p_null_mV": float(best_mV),
                    "pixel": 0,
                    "updated": datetime.now().isoformat(),
                    "first_hwp_retry": True,
                }
                table[first_key] = updated
                rows[int(first_hwp_state["row_index"])] = {"hwp_deg": first_hwp, **updated}
                print(
                    f"    [FIRST HWP UPDATED] HWP {first_hwp:.2f} -> "
                    f"q={best_q:.2f}, a={best_a:.2f}, Vdet={best_mV:.2f} mV"
                )
            else:
                print("    [FIRST HWP KEPT] retry did not improve the first HWP null.")

        save_substrate_calibration_snapshot(
            root_dir,
            rows,
            table,
            "substrate_calibration_partial.json",
        )

    alias_filename = substrate_calibration_alias_filename(root_dir)
    saved_paths = save_substrate_calibration_snapshot(
        root_dir,
        rows,
        table,
        SUBSTRATE_CAL_FILENAME,
        alias_filenames=(alias_filename,),
    )
    print(f"\n  Substrate calibration saved -> {root_dir / SUBSTRATE_CAL_FILENAME}")
    for alias_path in saved_paths[1:]:
        print(f"  Labelled alias              -> {alias_path}")
    return table


def load_substrate_optical_calibration(path_arg: str) -> dict:
    """Load a previously-saved substrate_calibration.json into a per-HWP seed table."""
    p = Path(path_arg).expanduser()
    if p.is_dir():
        p = p / SUBSTRATE_CAL_FILENAME
    if not p.is_file():
        raise FileNotFoundError(f"substrate calibration not found: {p}")
    with open(p, "r") as f:
        data = json.load(f)
    table = data.get("per_hwp") or {}
    bad_entries = []
    for key, entry in table.items():
        if not isinstance(entry, dict):
            bad_entries.append((key, "missing entry"))
            continue
        p_null_mV = entry.get("p_null_mV")
        if substrate_null_mV_bad(p_null_mV):
            bad_entries.append((key, p_null_mV))
    if bad_entries:
        preview = ", ".join(
            f"{key}: {value}" for key, value in bad_entries[:5]
        )
        extra = "" if len(bad_entries) <= 5 else f", ... +{len(bad_entries) - 5} more"
        raise RuntimeError(
            f"Refusing substrate calibration with {len(bad_entries)} uncertified HWP nulls "
            f"(hard limit {float(SUBSTRATE_ACCEPT_NULL_MV) + float(SUBSTRATE_CERTIFY_MARGIN_MV):.2f} mV; "
            f"target {float(SUBSTRATE_ACCEPT_NULL_MV):.2f} mV): {preview}{extra}. "
            "Run a fresh substrate calibration or raise --null-check-max-mv deliberately."
        )
    print(f"  Substrate cal loaded ({len(table)} HWP entries) <- {p}")
    return dict(table)


def run_full_stage_calibration(
    pixels,
    device_x,
    device_y,
    detector,
    live_view,
) -> int:
    """Phase 2: stage.full_auto_calibrate over every pixel; persist motor coords."""
    print("\n" + "=" * 72)
    print("PHASE 2 - STAGE FULL-AUTO CALIBRATION")
    print("=" * 72)
    total = len(pixels)
    n = stage.full_auto_calibrate(
        device_x, device_y, pixels, total, 0, detector, live_view
    )
    try:
        stage.save_json(pixels, n)
        stage.save_csv(pixels)
    except Exception as e:
        print(f"  [WARN] saving stage calibration JSON/CSV failed: {e}")
    print(f"  Stage calibration: {n}/{total} pixels with calibrated coords.")
    return n


def move_to_loaded_optical_calibration(rot_hwp, rot_qwp, rot_anl, cal) -> tuple[float, float]:
    if cal is None:
        q_seed = rot_qwp.get_angle() or 0.0
        a_seed = rot_anl.get_angle() or 0.0
        print(f"  Optical null seed from current rotators: QWP={q_seed:.2f}, ANL={a_seed:.2f}")
        return float(q_seed), float(a_seed)

    if cal.get("hwp_deg") is not None:
        print(f"  Moving HWP -> {cal['hwp_deg']:.2f} deg from calibration")
        pockels.safe_move_abs(rot_hwp, float(cal["hwp_deg"]), settle_s=pockels.MOVE_SETTLE_S)
    if cal.get("q_null_deg") is not None:
        print(f"  Moving QWP -> {cal['q_null_deg']:.2f} deg from calibration")
        pockels.safe_move_abs(rot_qwp, float(cal["q_null_deg"]), settle_s=pockels.MOVE_SETTLE_S)
    if cal.get("a_null_deg") is not None:
        print(f"  Moving ANL -> {cal['a_null_deg']:.2f} deg from calibration")
        pockels.safe_move_abs(rot_anl, float(cal["a_null_deg"]), settle_s=pockels.MOVE_SETTLE_S)

    raw = cal.get("raw") or {}
    useful_fields = []
    for label, key, fmt in (
        ("QWP axis", "QWP_gamma0_fit_deg", "{:.2f} deg"),
        ("retardance", "QWP_delta_deg", "{:.1f} deg"),
        ("visibility", "QWP_visibility", "{:.3f}"),
        ("extinction", "Extinction_dB_vs_QWPoutMax", "{:.1f} dB"),
    ):
        value = raw.get(key)
        try:
            useful_fields.append(f"{label}={fmt.format(float(value))}")
        except Exception:
            pass
    if useful_fields:
        print("  Sample-in optical context: " + ", ".join(useful_fields))

    q_seed = cal.get("q_null_deg")
    a_seed = cal.get("a_null_deg")
    if q_seed is None:
        q_seed = rot_qwp.get_angle() or 0.0
    if a_seed is None:
        a_seed = rot_anl.get_angle() or 0.0
    print(f"  Optical null seed: QWP={float(q_seed):.2f}, ANL={float(a_seed):.2f}")
    return float(q_seed), float(a_seed)


def connect_stage_hardware(args):
    print("\nInitializing Kinesis stage...")
    stage.DeviceManagerCLI.BuildDeviceList()
    print("  X axis:")
    device_x = stage.initialize_device(stage.STAGE_SERIAL_X)
    print("  Y axis:")
    device_y = stage.initialize_device(stage.STAGE_SERIAL_Y)

    if not args.skip_stage_home:
        print("\nHoming both stage axes...")
        stage.home_device(device_x, stage.STAGE_SERIAL_X)
        stage.home_device(device_y, stage.STAGE_SERIAL_Y)

    print("\nMoving stage to centre (12.5, 12.5 mm motor coords)...")
    cx = stage.safe_move_to(device_x, 12.5, "X")
    cy = stage.safe_move_to(device_y, 12.5, "Y")
    print(f"  Centre readback: X={cx:.4f}, Y={cy:.4f} mm")
    return device_x, device_y


def connect_stage_scope():
    print("\nInitializing oscilloscope for stage + Pockels reads...")
    detector = stage.ScopeDetector()
    detector.connect()
    test_v = detector.read_voltage()
    if test_v is None:
        raise RuntimeError("Scope connected but the test read failed")
    print(f"  Scope test reading: {test_v * 1000:.2f} mV")
    return detector


def connect_live_view(args, detector):
    if args.no_camera:
        return None
    live_view = stage.LiveView(detector=detector)
    print("\nInitializing live camera view...")
    live_view.start()
    return live_view


def connect_switch_matrix(args):
    if args.no_arduino:
        print("\nArduino switch matrix disabled by --no-arduino.")
        return None
    if not stage.HAVE_SWITCH_MATRIX:
        if args.allow_no_arduino:
            print("\n[WARN] arduino_switch_matrix.py not importable; continuing without matrix.")
            return None
        raise RuntimeError("arduino_switch_matrix.py is not importable")

    print(f"\nInitializing Arduino switch matrix on {args.arduino_port}...")
    matrix = stage.ArduinoSwitchMatrix(
        port=args.arduino_port,
        baudrate=args.arduino_baud,
        verbose=False,
        verify_commands=True,
    )
    if matrix.banner_seen():
        print("  Arduino READY.")
    else:
        print("  [WARN] Arduino opened but firmware banner was not seen.")
    try:
        matrix.turn_all_off()
    except BaseException:
        matrix.close()
        raise
    return matrix


def configure_funcgen_safe(initial_vpp: float = 0.1, funcgen=None):
    """Configure Aim-TTi with CH1 AC sweep output OFF until pixel routing.

    Pockels_Calibration_2026._configure_funcgen() turns CH1 on during setup.
    For the full automation workflow that is the wrong safety ordering: the
    Arduino route must be selected before any AC drive can appear on the chip.
    """
    print("\nConnecting function generator (Aim-TTi)...")
    fg = funcgen if funcgen is not None else pockels.connect_funcgen()
    # Also safe on reconnect: CH1 may have remained on at the lost handle.
    pockels._funcgen_set_sweep_output(
        fg, False, verify_command=True, required=True,
        context="function-generator setup",
    )

    print(f"  Configuring CH{pockels.FUNCGEN_CH_REF} reference: "
          f"{pockels.FUNCGEN_REF_AMPL_VPP} Vpp @ {pockels.FUNCGEN_FREQ_HZ} Hz")
    pockels.funcgen_send(fg, f"CHN {pockels.FUNCGEN_CH_REF}")
    pockels.funcgen_send(fg, "ZLOAD OPEN")
    pockels.funcgen_send(fg, f"FREQ {pockels.FUNCGEN_FREQ_HZ}")
    pockels.funcgen_send(fg, f"AMPL {pockels.FUNCGEN_REF_AMPL_VPP}")
    pockels.funcgen_send(fg, "OUTPUT ON")

    print(f"  Configuring CH{pockels.FUNCGEN_CH_SWEEP} sweep: "
          f"{pockels.FUNCGEN_FREQ_HZ} Hz, output OFF")
    pockels.funcgen_send(fg, f"CHN {pockels.FUNCGEN_CH_SWEEP}")
    pockels.funcgen_send(fg, "ZLOAD OPEN")
    pockels.funcgen_send(fg, f"FREQ {pockels.FUNCGEN_FREQ_HZ}")
    pockels.funcgen_send(fg, f"AMPL {initial_vpp}")
    pockels.funcgen_send(fg, "OUTPUT OFF")
    time.sleep(0.5)
    print("  Function generator OK; sweep channel is OFF.")
    return fg


def smu_set_level_output_on(smu, voltage_v: float) -> None:
    """Set the SMU source level while output is OFF, then enable output."""
    print(f"  [SMU] Setting source level to {voltage_v:+.2f} V with output OFF.")
    smu.set_voltage(float(voltage_v))
    if not pockels.smu_ensure_output_on(smu):
        raise RuntimeError("SMU output did not enable")
    print(f"  [SMU] Output ON at {voltage_v:+.2f} V.")


def smu_output_off(smu, label: str = "") -> None:
    """Disable SMU output without ramping the programmed source level."""
    suffix = f" ({label})" if label else ""
    print(f"  [SMU] Output OFF{suffix}; source level left programmed.")
    smu.output(False)
    time.sleep(0.2)


@contextmanager
def routed_arduino_channel(matrix, channel: int | None, settle_s: float, *, require_off: bool = False):
    if matrix is None:
        yield
        return
    if channel is None:
        raise RuntimeError("No Arduino channel mapped for this pixel")

    print(f"  [Arduino] Routing to channel {channel} before applying SMU/AC voltages...")
    matrix.turn_all_off()
    time.sleep(0.05)
    matrix.switch_to_channel(int(channel))
    time.sleep(settle_s)
    try:
        yield
    finally:
        interrupted = isinstance(sys.exc_info()[1], KeyboardInterrupt)
        print(f"  [Arduino] Disabling channel {channel}...")
        try:
            matrix.turn_all_off()
        except Exception as e:
            print(f"  [WARN] failed to turn Arduino channel off: {e}")
            if require_off and not interrupted:
                raise


def align_and_capture_pixel(
    idx: int,
    pixel: dict,
    pixels: list[dict],
    total: int,
    n_done_before: int,
    device_x,
    device_y,
    detector,
    live_view,
    args,
    rot_anl=None,
    transport_error_handler=None,
) -> dict | None:
    pix = pixel["pixel"]
    nom_xm = pixel["nominal_x_motor"]
    nom_ym = pixel["nominal_y_motor"]
    threshold_v = args.min_signal_mv / 1000.0

    print(f"\n{'─' * 70}")
    print(f"Pixel {pix}/{total}  [row {pixel['j']}, col {pixel['i']}]  "
          f"| measured {n_done_before}/{total}")
    print(f"Nominal motor: X={nom_xm:.4f}, Y={nom_ym:.4f} mm")

    print("  Moving to nominal stage position...")
    stage._safe_scan_move(device_x, nom_xm, "X")
    stage._safe_scan_move(device_y, nom_ym, "Y")
    stage.set_velocity_mode(device_x, label="X")
    stage.set_velocity_mode(device_y, label="Y")
    time.sleep(stage.SETTLE_TIME_S)

    # ── BRIGHTEN: rotate ANL off the through-sample null so the photodiode
    # sees a bright transmission peak for the stage hill-climb. After alignment
    # we rotate ANL back so per-HWP fast renull works from a near-null state. ──
    a_warm = None
    if rot_anl is not None:
        try:
            a_warm = float(rot_anl.get_angle())
            a_bright = a_warm + BRIGHTEN_OFFSET_DEG
            print(f"  [BRIGHTEN] ANL {a_warm:.2f} -> {a_bright:.2f} deg "
                  f"(+{BRIGHTEN_OFFSET_DEG:.0f} off-null) for stage alignment")
            pockels.safe_move_abs(rot_anl, a_bright, settle_s=pockels.MOVE_SETTLE_S)
            time.sleep(0.3)
        except Exception as e:
            if transport_error_handler is not None:
                transport_error_handler(e, f"Pixel {pix:03d} alignment brighten")
            print(f"  [WARN] brighten failed ({e}); aligning at current ANL.")
            a_warm = None

    if live_view is not None:
        live_view.pause_scope_reads()

    arrived_x, arrived_y = stage.read_motor_pos_averaged(device_x, device_y)
    arr_v, arr_std, arr_pw, arr_vdiv = detector.read_averaged()
    arr_img = ""
    if live_view is not None and live_view.available:
        arr_img = live_view.capture_image(pix, "arrival")
    stage.record_arrival(
        pixel,
        arrived_x,
        arrived_y,
        arr_v if arr_v is not None else 0.0,
        arr_std if arr_std is not None else 0.0,
        arr_pw,
        arr_vdiv,
        arr_img,
    )
    print(f"  Arrival: X={arrived_x:.4f}, Y={arrived_y:.4f} mm, "
          f"V={(arr_v or 0.0) * 1000:.2f} mV")

    result = None
    method = None
    if args.realign_stage or pixel.get("calibrated_x_motor") is None:
        print("  Stage alignment: hill-climb -> fast-peak -> golden-section -> full scan")
        result = stage.hill_climb_align(device_x, device_y, detector, live_view)
        method = "hill_climb"

        peak_v = result["peak_v"] if result else 0.0
        if peak_v < threshold_v:
            print(f"  Signal {peak_v * 1000:.1f} mV below {args.min_signal_mv:.0f} mV; trying fast peak.")
            result = stage.fast_peak_align(device_x, device_y, detector, live_view)
            method = "fast_peak_retry"
            peak_v = result["peak_v"] if result else 0.0

        if peak_v < threshold_v:
            print(f"  Signal {peak_v * 1000:.1f} mV still low; trying golden section.")
            result = stage.golden_section_align(device_x, device_y, detector, live_view)
            method = "golden_section_retry"
            peak_v = result["peak_v"] if result else 0.0

        if peak_v < threshold_v:
            print(f"  Signal {peak_v * 1000:.1f} mV still low; trying full line scan.")
            result = stage.auto_align(device_x, device_y, detector, live_view)
            method = "full_scan_retry"
            peak_v = result["peak_v"] if result else 0.0

        if result is not None:
            pixel["align_method"] = method
            pixel["align_fine_n_evals"] = result.get("n_evals")
            correction = result.get("coarse_correction_um")
            if correction:
                pixel["align_coarse_correction_x_um"] = correction[0]
                pixel["align_coarse_correction_y_um"] = correction[1]
            try:
                stage.save_alignment_log(pix, result)
            except Exception as e:
                print(f"  [WARN] save_alignment_log failed: {e}")
            try:
                stage.plot_alignment_diagnostic(pix, result)
            except Exception as e:
                print(f"  [WARN] plot_alignment_diagnostic failed: {e}")
    else:
        print("  Using loaded calibrated stage position without re-aligning.")
        stage._safe_scan_move(device_x, pixel["calibrated_x_motor"], "X")
        stage._safe_scan_move(device_y, pixel["calibrated_y_motor"], "Y")

    if live_view is not None:
        live_view.pause_scope_reads()
    stage.capture_calibrated(device_x, device_y, pixel, detector, live_view)
    if live_view is not None:
        live_view.pause_scope_reads()

    # ── DIM: rotate ANL back to the through-sample null seed so the per-HWP
    # fast renull starts from the cached null position (small adjustment only).
    if rot_anl is not None and a_warm is not None:
        try:
            print(f"  [DIM] ANL back to {a_warm:.2f} deg (near-null seed) "
                  f"before per-HWP renull")
            pockels.safe_move_abs(rot_anl, a_warm, settle_s=pockels.MOVE_SETTLE_S)
            time.sleep(0.2)
        except Exception as e:
            if transport_error_handler is not None:
                transport_error_handler(e, f"Pixel {pix:03d} alignment dim")
            print(f"  [WARN] dim failed ({e}); ANL left bright.")

    return result


def hwp_angles_from_args(args) -> np.ndarray:
    return np.arange(args.hwp_start, args.hwp_stop + 1e-9, args.hwp_step)


def run_pockels_measurement_for_pixel(
    pixel: dict,
    pixel_dir: Path,
    run_label: str,
    hwp_angles: np.ndarray,
    analyzer_angles: np.ndarray,
    voltages: list[float],
    rot_hwp,
    rot_qwp,
    rot_anl,
    detector_adapter: StageScopePockelsAdapter,
    lockin,
    funcgen,
    sens_idx: int,
    null_seed_by_hwp: dict,
    fallback_seed: tuple[float, float],
    t_global_start: float,
    points_done_before: int,
    total_points_all: int,
    live_view,
    sim_state: dict | None = None,
) -> tuple[int, tuple[float, float], int, dict]:
    pixel_dir.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    all_results_by_hwp = {}
    current_q_seed, current_a_seed = fallback_seed
    current_p_ref = 5e-6

    with open(pixel_dir / "pixel_run_info.json", "w") as f:
        json.dump({
            "pixel": pixel["pixel"],
            "row": pixel["j"],
            "col": pixel["i"],
            "stage_calibrated_x_motor": pixel.get("calibrated_x_motor"),
            "stage_calibrated_y_motor": pixel.get("calibrated_y_motor"),
            "hwp_angles_deg": [float(v) for v in hwp_angles],
            "analyzer_angles_deg": [float(v) for v in analyzer_angles],
            "voltages_vpp": voltages,
            "started": datetime.now().isoformat(),
        }, f, indent=2)

    for i_hwp, hwp_deg in enumerate(hwp_angles):
        hwp_key = f"{float(hwp_deg):.3f}"
        if hwp_key in null_seed_by_hwp:
            seed = null_seed_by_hwp[hwp_key]
            current_q_seed = float(seed["q_null_deg"])
            current_a_seed = float(seed["a_null_deg"])
            try:
                current_p_ref = float(seed.get("p_null_W", current_p_ref))
            except Exception:
                current_p_ref = 5e-6
            if not np.isfinite(current_p_ref) or current_p_ref <= 0.0:
                current_p_ref = 5e-6

        print(f"\n{'=' * 70}")
        print(f"Pixel {pixel['pixel']} — HWP {i_hwp + 1}/{len(hwp_angles)}: {hwp_deg:.1f} deg")
        print(f"{'=' * 70}")

        hwp_dir = pixel_dir / f"hwp_{float(hwp_deg):05.1f}deg"
        hwp_dir.mkdir(parents=True, exist_ok=True)

        print(f"  Moving HWP -> {hwp_deg:.2f} deg...")
        pockels.safe_move_abs(rot_hwp, float(hwp_deg), settle_s=pockels.MOVE_SETTLE_S)
        time.sleep(0.5)

        pockels._funcgen_set_sweep_output(funcgen, False)
        print(f"  Fast re-nulling QWP+ANL with AC OFF "
              f"(seed q={current_q_seed:.2f}, a={current_a_seed:.2f}, "
              f"ref P={current_p_ref:.3e} W)...")
        t_null = time.time()
        try:
            log_rows = []
            renull = campaign_fast_renull(
                detector_adapter,
                rot_qwp,
                rot_anl,
                current_q_seed,
                current_a_seed,
                current_p_ref,
                log_rows=log_rows,
            )
            q_null = float(renull["q"])
            a_null = float(renull["a"])
            p_null = float(renull["p_after"])
            print(f"  [NULL] q={q_null:.2f}, a={a_null:.2f}, "
                  f"P={p_null:.3e} W ({time.time() - t_null:.1f}s, "
                  f"{renull['method']}, {renull['n_evals']} reads"
                  f"{', escalated' if renull['escalated'] else ''})")
        except Exception as e:
            print(f"  [NULL FAILED] {e}; using seed positions.")
            q_null, a_null, p_null, log_rows = current_q_seed, current_a_seed, float("nan"), []

        pockels.save_null_info(
            str(hwp_dir / "null_info.json"),
            float(hwp_deg),
            q_null,
            a_null,
            p_null,
            current_q_seed,
            current_a_seed,
            log_rows,
        )
        current_q_seed, current_a_seed = float(q_null), float(a_null)
        if np.isfinite(p_null) and p_null > 0.0:
            current_p_ref = float(p_null)
        if sim_state is not None:
            sim_state["a_null"] = float(a_null)
        null_seed_by_hwp[hwp_key] = {
            "q_null_deg": float(q_null),
            "a_null_deg": float(a_null),
            "p_null_W": float(p_null),
            "pixel": int(pixel["pixel"]),
            "updated": datetime.now().isoformat(),
        }

        print("  Turning AC sweep output ON for analyser-voltage series...")
        pockels._funcgen_set_sweep_output(funcgen, True)
        all_results, sens_idx = pockels.run_voltage_series_at_hwp(
            rot_anl,
            detector_adapter,
            lockin,
            funcgen,
            sens_idx,
            float(a_null),
            analyzer_angles,
            float(hwp_deg),
            run_label,
            str(hwp_dir),
            t_global_start,
            points_done_before,
            total_points_all,
        )
        pockels._funcgen_set_sweep_output(funcgen, False)
        points_done_before += len(voltages) * len(analyzer_angles)
        all_results_by_hwp[float(hwp_deg)] = all_results

        pockels.save_hwp_overlay_plot(str(hwp_dir), all_results, float(hwp_deg), run_label)

        fits_by_vpp = {}
        for vpp, res in all_results.items():
            fit = pockels.fit_delta_from_sweep(res["angle"], res["mag"])
            fits_by_vpp[vpp] = fit
            if fit is not None:
                print(f"  [FIT] {vpp} Vpp: delta={fit['delta_deg']:+.3f} deg, "
                      f"amp={fit['amp'] * 1e6:.2f} uV")

        summary_rows.append({
            "theta_i": float(hwp_deg),
            "q_null": float(q_null),
            "a_null": float(a_null),
            "p_null": float(p_null),
            "fits": fits_by_vpp,
        })
        pockels.save_campaign_summary(str(pixel_dir), summary_rows, run_label)

        with open(pixel_dir / NULL_SEEDS_JSON, "w") as f:
            json.dump(null_seed_by_hwp, f, indent=2)

    peak = pockels.find_peak_response(summary_rows, all_results_by_hwp)
    if peak is not None:
        with open(pixel_dir / "peak_response.json", "w") as f:
            json.dump(peak, f, indent=2)
    return sens_idx, (current_q_seed, current_a_seed), points_done_before, peak or {}


def cleanup_hardware(
    device_x=None,
    device_y=None,
    stage_detector=None,
    live_view=None,
    switch_matrix=None,
    smu=None,
    lockin=None,
    funcgen=None,
    rotators=(),
) -> None:
    print("\nCleaning up hardware...")
    try:
        if funcgen is not None:
            pockels._funcgen_set_sweep_output(funcgen, False)
    except Exception:
        pass
    try:
        pockels.smu_safe_shutdown(smu)
    except Exception:
        pass
    try:
        if switch_matrix is not None:
            switch_matrix.close()
            print("  Arduino switch matrix connection closed.")
    except Exception as e:
        print(f"  [WARN] Arduino close failed: {e}")
    try:
        if live_view is not None:
            live_view.stop()
    except Exception:
        pass
    try:
        if lockin is not None:
            lockin.close()
    except Exception:
        pass
    try:
        if stage_detector is not None:
            stage_detector.close()
    except Exception:
        pass
    for rot in rotators:
        try:
            rot.close()
        except Exception:
            pass
    try:
        if funcgen is not None:
            pockels.funcgen_send(funcgen, "LOCAL")
            funcgen.close()
    except Exception:
        pass
    for dev in (device_x, device_y):
        try:
            if dev is not None:
                dev.StopPolling()
                dev.Disconnect()
        except Exception:
            pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full stage + Arduino + SMU + HWP/analyser Pockels automation"
    )
    parser.add_argument("--run-name", default=None, help="Run name for output folder")
    parser.add_argument("--resume-run", default=None, help="Resume an existing pockels_full_automation run folder")
    parser.add_argument("--stage-cal", default=None, help="Existing stage pixel_positions.json or run folder to load")
    parser.add_argument("--optical-cal", default="latest", help="Optical calibration JSON, 'latest', or 'none'")
    parser.add_argument("--pixels", default="all", help="Pixel list/ranges, e.g. '1-10,15,42' [default all]")
    parser.add_argument("--skip-complete", action="store_true", help="Skip pixels marked measurement_status=complete on resume")
    parser.add_argument("--no-realign-stage", dest="realign_stage", action="store_false",
                        help="Use loaded calibrated stage coordinates without hill-climb realignment")
    parser.set_defaults(realign_stage=True)

    parser.add_argument("--hwp-start", type=float, default=pockels.HWP_START_DEG)
    parser.add_argument("--hwp-stop", type=float, default=pockels.HWP_STOP_DEG)
    parser.add_argument("--hwp-step", type=float, default=pockels.HWP_STEP_DEG)
    parser.add_argument("--anl-start", type=float, default=pockels.ANL_START)
    parser.add_argument("--anl-stop", type=float, default=pockels.ANL_STOP)
    parser.add_argument("--anl-points", type=int, default=pockels.N_ANL_POINTS)
    parser.add_argument("--voltages", default=",".join(str(v) for v in DEFAULT_AC_VOLTAGES_VPP),
                        help="Comma-separated AC Vpp list [default 1,3,5,7,9]")

    parser.add_argument("--poling-voltage", type=float, default=pockels.DC_POLING_V_PHASE_A)
    parser.add_argument("--poling-dwell", type=float, default=pockels.DC_POLING_DWELL_S)
    parser.add_argument("--smu-port", default=AUTO_PORT,
                        help="SMU4201 serial port, COM11 etc, or 'auto' to identify it by *IDN? [default auto]")
    parser.add_argument("--smu-compliance", type=float, default=pockels.SMU_COMPLIANCE_A)
    parser.add_argument("--smu-ramp-step", type=float, default=pockels.SMU_RAMP_STEP_V)
    parser.add_argument("--smu-ramp-dwell", type=float, default=pockels.SMU_RAMP_DWELL_S)

    parser.add_argument("--lockin-settle", type=float, default=pockels.LOCKIN_SETTLE_S)
    parser.add_argument("--lockin-avg-readings", type=int, default=pockels.LOCKIN_AVG_READINGS)
    parser.add_argument("--lockin-read-delay", type=float, default=pockels.LOCKIN_READ_DELAY_S)
    parser.add_argument("--funcgen-freq", type=float, default=pockels.FUNCGEN_FREQ_HZ)
    parser.add_argument("--funcgen-settle", type=float, default=pockels.FUNCGEN_SETTLE_S)

    parser.add_argument("--arduino-port", default=AUTO_PORT,
                        help="Arduino switch-matrix serial port, COM14 etc, or 'auto' to identify it by USB metadata/banner [default auto]")
    parser.add_argument("--arduino-baud", type=int, default=stage.ARDUINO_BAUD)
    parser.add_argument("--arduino-settle", type=float, default=stage.VOLTAGE_SETTLE_S)
    parser.add_argument("--no-arduino", action="store_true", help="Do not use the Arduino switch matrix")
    parser.add_argument("--allow-no-arduino", action="store_true",
                        help="Continue if the Arduino cannot be opened")
    parser.add_argument("--list-serial-ports", action="store_true",
                        help="List detected COM ports and exit")
    parser.add_argument("--show-bluetooth-ports", action="store_true",
                        help="Include Bluetooth serial links in --list-serial-ports output")

    parser.add_argument("--min-signal-mv", type=float, default=stage.FULL_AUTO_MIN_SIGNAL_MV)
    parser.add_argument("--skip-stage-home", action="store_true", help="Do not home the stage at startup")
    parser.add_argument("--no-camera", action="store_true", help="Do not start the camera live view")
    parser.add_argument("--yes", action="store_true", help="Do not prompt before starting the high-voltage campaign")
    parser.add_argument("--simulate-lockin", action="store_true",
                        help="Use a synthetic FakeLockin instead of opening the DSP7230 (for dry-run / no-hardware)")
    parser.add_argument("--require-lockin", action="store_true",
                        help="Treat lock-in connection failure as a hard error (default: auto-fall-back to FakeLockin so the campaign can still run)")
    parser.add_argument("--require-smu", action="store_true",
                        help="Treat SMU4201 connection failure as a hard error (default: auto-fall-back to FakeSMU)")
    parser.add_argument("--no-rotator-home", action="store_true",
                        help="Skip the home+tare on HWP/QWP/ANL at startup (default: home like pockels_campaign.py to keep the ELL14 internal counter inside its valid range)")

    parser.add_argument("--phase-substrate", choices=("do", "load", "skip"), default="do",
                        help="Phase 1: do=run HWP-by-HWP nulling at start, load=read substrate_calibration.json, skip=use legacy --optical-cal seed only")
    parser.add_argument("--substrate-cal", default=None,
                        help="Path (file or run dir) to a previously saved substrate_calibration.json (used with --phase-substrate load)")
    parser.add_argument("--substrate-cal-pixel", type=int, default=1,
                        help="Pixel index (1..N) to align first and run the HWP-by-HWP nulling on; 0 = stay at stage centre, no align [default 1]")

    parser.add_argument("--phase-stage", choices=("do", "load", "skip"), default="skip",
                        help="Phase 2: skip (default)=align each pixel inside the measurement loop just before voltage is applied (preferred); do=run stage.full_auto_calibrate as a separate up-front pass; load=read pixel_positions.json (--stage-cal)")
    parser.add_argument("--phase-measure", choices=("do", "skip"), default="do",
                        help="Phase 3: do=run the per-pixel Pockels measurement campaign, skip=stop after Phase 1/2 (cal-only run)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.list_serial_ports:
        print_serial_ports(include_bluetooth=args.show_bluetooth_ports)
        return 0
    resolve_instrument_serial_ports(args)
    voltages = configure_shared_measurement_globals(args)
    root_dir = make_run_dir(args)
    stage_dir = root_dir / DEFAULT_STAGE_SUBDIR
    pixels_dir = root_dir / DEFAULT_PIXELS_SUBDIR
    stage_dir.mkdir(parents=True, exist_ok=True)
    pixels_dir.mkdir(parents=True, exist_ok=True)
    set_stage_output_dir(stage_dir)

    resumed_pixels, records, resumed_seeds = load_resume_state(root_dir)
    if resumed_pixels is not None:
        pixels = resumed_pixels
        n_stage_calibrated = sum(1 for p in pixels if p.get("calibrated_x_motor") is not None)
        null_seed_by_hwp = resumed_seeds
        print(f"Resuming {root_dir}: {len(records)} pixel measurement records loaded.")
    else:
        pixels, n_stage_calibrated = load_stage_pixels(args.stage_cal)
        null_seed_by_hwp = {}

    total = len(pixels)
    selected_pixels = parse_pixel_selection(args.pixels, total)
    hwp_angles = hwp_angles_from_args(args)
    analyzer_angles = np.linspace(args.anl_start, args.anl_stop, args.anl_points)
    total_points_all = len(selected_pixels) * len(hwp_angles) * len(voltages) * len(analyzer_angles)

    config = {
        "root_dir": str(root_dir),
        "selected_pixels": selected_pixels,
        "stage_cal": args.stage_cal,
        "optical_cal": args.optical_cal,
        "hwp_angles_deg": [float(v) for v in hwp_angles],
        "analyzer_angles_deg": [float(v) for v in analyzer_angles],
        "voltages_vpp": voltages,
        "poling_voltage_v": args.poling_voltage,
        "poling_dwell_s": args.poling_dwell,
        "smu_port": None if getattr(args, "_smu_auto_not_found", False) else args.smu_port,
        "smu_port_auto_not_found": bool(getattr(args, "_smu_auto_not_found", False)),
        "lockin_settle_s": args.lockin_settle,
        "lockin_avg_readings": args.lockin_avg_readings,
        "lockin_read_delay_s": args.lockin_read_delay,
        "funcgen_freq_hz": args.funcgen_freq,
        "arduino_port": None if args.no_arduino else args.arduino_port,
        "pixel_to_pin": getattr(stage, "PIXEL_TO_PIN", {}),
    }
    with open(root_dir / "run_config.json", "w") as f:
        json.dump(config, f, indent=2)

    print("=" * 72)
    print("FULL POCKELS AUTOMATION")
    print("=" * 72)
    print(f"Output dir       : {root_dir}")
    print(f"Pixels selected  : {len(selected_pixels)} / {total}")
    print(f"Stage calibrated : {n_stage_calibrated} / {total} loaded")
    print(f"HWP angles       : {hwp_angles[0]:.1f} -> {hwp_angles[-1]:.1f} deg ({len(hwp_angles)} points)")
    print(f"Analyser sweep   : {args.anl_start:.1f} -> {args.anl_stop:.1f} deg ({len(analyzer_angles)} points)")
    print(f"AC voltages      : {voltages} Vpp")
    print(f"SMU poling       : {args.poling_voltage:+.2f} V after Arduino routing")
    print("SMU port         : "
          + ("auto not found (FakeSMU)" if getattr(args, "_smu_auto_not_found", False) else str(args.smu_port)))
    print(f"Arduino port     : {'disabled' if args.no_arduino else args.arduino_port}")
    print(f"Total LI points  : {total_points_all}")
    print(f"Phase 1 substrate: {args.phase_substrate}"
          + (f"  (file: {args.substrate_cal})" if args.phase_substrate == "load" else "")
          + (f"  (pixel {args.substrate_cal_pixel or 'centre'})" if args.phase_substrate == "do" else ""))
    print(f"Phase 2 stage    : {args.phase_stage}"
          + (f"  (file: {args.stage_cal})" if args.phase_stage == "load" else ""))
    print(f"Phase 3 measure  : {args.phase_measure}")
    print(f"Lock-in          : {'SIMULATED (FakeLockin)' if args.simulate_lockin else 'real DSP7230'}")

    if not args.yes:
        input("\nPress Enter to initialize hardware and start the automated campaign...")

    device_x = device_y = None
    stage_detector = None
    live_view = None
    switch_matrix = None
    smu = None
    lockin = None
    funcgen = None
    rot_hwp = rot_qwp = rot_anl = None
    sens_idx = 15
    points_done = 0

    try:
        device_x, device_y = connect_stage_hardware(args)
        stage_detector = connect_stage_scope()
        live_view = connect_live_view(args, stage_detector)
        detector_adapter = StageScopePockelsAdapter(stage_detector)
        switch_matrix = connect_switch_matrix(args)

        print("\nConnecting optical rotators, function generator, lock-in, and SMU...")
        sim_state = {"vpp": float(min(voltages)), "a_null": 0.0}
        use_fake_lockin = bool(args.simulate_lockin)
        if use_fake_lockin:
            install_funcgen_vpp_spy(sim_state)
        funcgen = configure_funcgen_safe(initial_vpp=min(voltages))
        rot_hwp, rot_qwp, rot_anl = pockels._connect_rotators()

        # Match pockels_campaign.py: home + tare + set velocity 60% on every
        # rotator at startup. Avoids the "sensor error" faults that come from
        # the ELL14 internal counter drifting + the default 100% velocity.
        print(f"\nBooting rotators (home/tare/velocity={ROTATOR_VELOCITY_PCT}%)...")
        boot_rotators(rot_hwp, rot_qwp, rot_anl, do_home=not args.no_rotator_home)
        if use_fake_lockin:
            print("\n[SIM] Using FakeLockin (--simulate-lockin).")
            lockin = FakeLockin(rot_anl, sim_state)
            lockin.connect()
            sens_idx = 15
        else:
            try:
                lockin, sens_idx = pockels._connect_lockin()
            except Exception as e:
                if args.require_lockin:
                    raise
                print(f"\n[WARN] Lock-in connection failed ({type(e).__name__}: {e}).")
                print("[WARN] Falling back to FakeLockin so the campaign can run.")
                print("[WARN] Pass --require-lockin to make this a hard error.")
                install_funcgen_vpp_spy(sim_state)
                lockin = FakeLockin(rot_anl, sim_state)
                lockin.connect()
                sens_idx = 15
                use_fake_lockin = True
        if getattr(args, "_smu_auto_not_found", False):
            print("\n[WARN] SMU auto-detect did not find an SMU4201.")
            print("[WARN] Falling back to FakeSMU so the campaign can run.")
            print("[WARN] Pass --require-smu or --smu-port COM11 to make this a hard hardware run.")
            smu = FakeSMU()
        else:
            try:
                smu = pockels._connect_smu(0.0)
            except Exception as e:
                if args.require_smu:
                    raise
                print(f"\n[WARN] SMU connection failed ({type(e).__name__}: {e}).")
                print("[WARN] Falling back to FakeSMU so the campaign can run.")
                print("[WARN] Pass --require-smu to make this a hard error.")
                smu = FakeSMU()
        smu_output_off(smu, "armed at 0 V until Arduino routing")

        optical_cal = resolve_optical_calibration(args.optical_cal)
        save_loaded_optical_calibration(root_dir, optical_cal)
        fallback_seed = move_to_loaded_optical_calibration(rot_hwp, rot_qwp, rot_anl, optical_cal)

        # ----- PHASE 1: Substrate optical calibration ------------------------
        if args.phase_substrate == "load":
            if args.substrate_cal is None:
                raise RuntimeError("--phase-substrate load requires --substrate-cal <path>")
            loaded_table = load_substrate_optical_calibration(args.substrate_cal)
            null_seed_by_hwp.update(loaded_table)
        elif args.phase_substrate == "do":
            sub_pix = int(args.substrate_cal_pixel or 0)
            if sub_pix > 0:
                if not (1 <= sub_pix <= len(pixels)):
                    raise ValueError(f"--substrate-cal-pixel out of range: {sub_pix}")
                print(f"\n  Substrate cal will use pixel {sub_pix}: align first, "
                      f"then HWP-by-HWP null + compensation.")
                pixel_for_sub = pixels[sub_pix - 1]
                stage.run_with_display(
                    live_view,
                    align_and_capture_pixel,
                    sub_pix - 1,
                    pixel_for_sub,
                    pixels,
                    total,
                    0,
                    device_x,
                    device_y,
                    stage_detector,
                    live_view,
                    args,
                    rot_anl,
                )
                stage.save_json(pixels, sum(1 for p in pixels if p.get("calibrated_x_motor") is not None))
                stage.save_csv(pixels)
            else:
                print("\n  Substrate cal at current stage position (centre, no align).")
            sub_table = stage.run_with_display(
                live_view,
                run_substrate_optical_calibration,
                root_dir, hwp_angles, rot_hwp, rot_qwp, rot_anl,
                detector_adapter, fallback_seed,
            )
            null_seed_by_hwp.update(sub_table)
        else:
            print("\n  PHASE 1 skipped (--phase-substrate skip): per-HWP nulls will be "
                  "found lazily inside the measurement loop, seeded from the legacy "
                  "optical calibration.")

        # ----- PHASE 2: Stage full-auto calibration --------------------------
        if args.phase_stage == "load":
            if args.stage_cal is None:
                print("  [WARN] --phase-stage load with no --stage-cal; nothing to load.")
            else:
                loaded_pixels, n_loaded = load_stage_pixels(args.stage_cal)
                for i, p in enumerate(loaded_pixels):
                    pixels[i].update(p)
                print(f"  Stage cal loaded: {n_loaded}/{len(pixels)} pixels have "
                      f"calibrated motor coords.")
        elif args.phase_stage == "do":
            n_cal = stage.run_with_display(
                live_view,
                run_full_stage_calibration,
                pixels, device_x, device_y, stage_detector, live_view,
            )
            n_stage_calibrated = n_cal
        else:
            print("\n  PHASE 2 skipped (--phase-stage skip): each pixel will be aligned "
                  "lazily inside the measurement loop.")

        save_progress(root_dir, pixels, records, null_seed_by_hwp, config)

        if args.phase_measure == "skip":
            print("\nPHASE 3 skipped (--phase-measure skip). Calibration-only run complete.")
            print(f"Outputs in: {root_dir}")
            return 0

        print("\n" + "=" * 72)
        print("PHASE 3 - PER-PIXEL POCKELS MEASUREMENT")
        print("=" * 72)

        t_global_start = time.time()
        for pix_num in selected_pixels:
            pixel = pixels[pix_num - 1]
            if args.skip_complete and pixel.get("measurement_status") == "complete":
                print(f"\nPixel {pix_num}: already complete; skipping.")
                continue

            record = {
                "pixel": pix_num,
                "status": "started",
                "channel": None,
                "started": datetime.now().isoformat(),
                "completed": "",
                "calibrated_x_motor": "",
                "calibrated_y_motor": "",
                "smu_poling_voltage_v": args.poling_voltage,
                "output_dir": "",
                "error": "",
            }
            records.append(record)
            pixel["measurement_status"] = "started"
            pixel["measurement_started"] = record["started"]
            save_progress(root_dir, pixels, records, null_seed_by_hwp, config)

            try:
                n_done = sum(1 for p in pixels if p.get("measurement_status") == "complete")
                stage.run_with_display(
                    live_view,
                    align_and_capture_pixel,
                    pix_num - 1,
                    pixel,
                    pixels,
                    total,
                    n_done,
                    device_x,
                    device_y,
                    stage_detector,
                    live_view,
                    args,
                    rot_anl,
                )
                stage.save_json(pixels, sum(1 for p in pixels if p.get("calibrated_x_motor") is not None))
                stage.save_csv(pixels)

                channel = stage.pixel_to_channel(pix_num)
                record["channel"] = channel
                pixel["arduino_channel"] = channel
                pixel_dir = pixels_dir / f"pixel_{pix_num:03d}"
                record["output_dir"] = str(pixel_dir)

                if live_view is not None:
                    live_view.pause_scope_reads()

                with routed_arduino_channel(switch_matrix, channel, args.arduino_settle):
                    try:
                        smu_set_level_output_on(smu, args.poling_voltage)
                        print(f"  [SMU] Poling dwell {args.poling_dwell:.1f}s...")
                        time.sleep(args.poling_dwell)

                        run_label = f"Pixel {pix_num:03d}"
                        sens_idx, fallback_seed, points_done, peak = stage.run_with_display(
                            live_view,
                            run_pockels_measurement_for_pixel,
                            pixel,
                            pixel_dir,
                            run_label,
                            hwp_angles,
                            analyzer_angles,
                            voltages,
                            rot_hwp,
                            rot_qwp,
                            rot_anl,
                            detector_adapter,
                            lockin,
                            funcgen,
                            sens_idx,
                            null_seed_by_hwp,
                            fallback_seed,
                            t_global_start,
                            points_done,
                            total_points_all,
                            live_view,
                            sim_state if use_fake_lockin else None,
                        )
                        pixel["peak_response"] = peak
                    finally:
                        print("  [FuncGen] Turning AC sweep output OFF before SMU output OFF.")
                        try:
                            pockels._funcgen_set_sweep_output(funcgen, False)
                        except Exception:
                            pass
                        smu_output_off(smu, "before switching pins")

                pixel["measurement_status"] = "complete"
                pixel["measurement_completed"] = datetime.now().isoformat()
                record["status"] = "complete"
                record["completed"] = pixel["measurement_completed"]
                record["calibrated_x_motor"] = pixel.get("calibrated_x_motor")
                record["calibrated_y_motor"] = pixel.get("calibrated_y_motor")

            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"\n[ERROR] Pixel {pix_num} failed: {e}")
                traceback.print_exc()
                pixel["measurement_status"] = "failed"
                pixel["measurement_error"] = f"{type(e).__name__}: {e}"
                record["status"] = "failed"
                record["error"] = pixel["measurement_error"]
                try:
                    pockels._funcgen_set_sweep_output(funcgen, False)
                except Exception:
                    pass
                try:
                    smu_output_off(smu, "after pixel failure")
                except Exception as smu_e:
                    print(f"  [WARN] SMU output-off after failure failed: {smu_e}")
                try:
                    if switch_matrix is not None:
                        switch_matrix.turn_all_off()
                except Exception:
                    pass
            finally:
                if live_view is not None:
                    try:
                        live_view.resume_scope_reads()
                    except Exception:
                        pass
                save_progress(root_dir, pixels, records, null_seed_by_hwp, config)

        print(f"\nCampaign complete. Data saved in: {root_dir}")
        return 0

    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Saving progress and shutting down safely.")
        save_progress(root_dir, pixels, records, null_seed_by_hwp, config)
        return 130
    finally:
        cleanup_hardware(
            device_x=device_x,
            device_y=device_y,
            stage_detector=stage_detector,
            live_view=live_view,
            switch_matrix=switch_matrix,
            smu=smu,
            lockin=lockin,
            funcgen=funcgen,
            rotators=(r for r in (rot_hwp, rot_qwp, rot_anl) if r is not None),
        )


if __name__ == "__main__":
    raise SystemExit(main())
