#!/usr/bin/env python3
"""
Robust live magnitude/phase viewer for the Signal Recovery DSP7230 lock-in amplifier.

Single-file script:
- VISA driver
- background acquisition thread
- Tkinter GUI
- embedded matplotlib plots
- robust MP. parsing
- graceful reconnect handling

Install:
    pip install pyvisa matplotlib

Run:
    python untitled0.py

Optional:
    python untitled0.py --resource "TCPIP0::169.254.150.230::50001::SOCKET"
"""

from __future__ import annotations

import argparse
import logging
import math
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Callable

import pyvisa
import tkinter as tk
from tkinter import ttk

import matplotlib
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure


LOGGER = logging.getLogger("dsp7230_live")


# Voltage-mode sensitivity table (SEN index -> full-scale volts)
SENS_TABLE_VOLTS = {
    3: 10e-9,
    4: 20e-9,
    5: 50e-9,
    6: 100e-9,
    7: 200e-9,
    8: 500e-9,
    9: 1e-6,
    10: 2e-6,
    11: 5e-6,
    12: 10e-6,
    13: 20e-6,
    14: 50e-6,
    15: 100e-6,
    16: 200e-6,
    17: 500e-6,
    18: 1e-3,
    19: 2e-3,
    20: 5e-3,
    21: 10e-3,
    22: 20e-3,
    23: 50e-3,
    24: 100e-3,
    25: 200e-3,
    26: 500e-3,
    27: 1.0,
}

MIN_SENS_IDX = min(SENS_TABLE_VOLTS)
MAX_SENS_IDX = max(SENS_TABLE_VOLTS)


class DSP7230Error(Exception):
    """Base exception for DSP7230 communication errors."""


class DSP7230ParseError(DSP7230Error):
    """Raised when an instrument response cannot be parsed."""


@dataclass(slots=True)
class Measurement:
    timestamp: float
    magnitude: float
    phase_deg: float
    overload_byte: Optional[int] = None


class DSP7230:
    """
    Thin, defensive VISA wrapper for the Signal Recovery DSP7230.
    """

    def __init__(
        self,
        resource_name: str,
        *,
        timeout_ms: int = 2000,
        delimiter: str = ",",
    ) -> None:
        self.resource_name = resource_name
        self.timeout_ms = timeout_ms
        self.delimiter = delimiter
        self._rm: Optional[pyvisa.ResourceManager] = None
        self._inst = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            if self._inst is not None:
                return

            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self.resource_name)
            self._inst.timeout = self.timeout_ms

            # DSP7230 socket settings typically used in your code/manual context
            self._inst.read_termination = "\r"
            self._inst.write_termination = "\x00"

            try:
                self._inst.clear()
            except Exception:
                LOGGER.debug("Instrument clear() failed or not supported", exc_info=True)

            LOGGER.info("Connected to %s", self.resource_name)

    def close(self) -> None:
        with self._lock:
            inst = self._inst
            rm = self._rm
            self._inst = None
            self._rm = None

        try:
            if inst is not None:
                inst.close()
        except Exception:
            LOGGER.debug("Error while closing instrument", exc_info=True)

        try:
            if rm is not None:
                rm.close()
        except Exception:
            LOGGER.debug("Error while closing resource manager", exc_info=True)

    def is_connected(self) -> bool:
        return self._inst is not None

    def _require_inst(self):
        if self._inst is None:
            raise DSP7230Error("Instrument is not connected.")
        return self._inst

    def write(self, command: str) -> None:
        with self._lock:
            inst = self._require_inst()
            LOGGER.debug("WRITE %s", command)
            inst.write(command)

    def query_text(self, command: str) -> str:
        with self._lock:
            inst = self._require_inst()
            LOGGER.debug("QUERY %s", command)
            response = inst.query(command)

        if response is None:
            raise DSP7230Error(f"No response for command: {command}")

        response = response.strip()
        LOGGER.debug("RESP  %s", response)
        return response

    def clear_io(self) -> None:
        with self._lock:
            inst = self._require_inst()
            try:
                inst.clear()
            except Exception:
                LOGGER.debug("Instrument clear() failed or not supported", exc_info=True)

    def query_float(self, command: str) -> float:
        values = self._query_numeric_fields(command)
        if len(values) != 1:
            raise DSP7230ParseError(
                f"Expected one numeric value from {command!r}, got {len(values)} fields"
            )
        return values[0]

    def query_int(self, command: str) -> int:
        text = self.query_text(command)
        try:
            return int(text)
        except ValueError as exc:
            raise DSP7230ParseError(
                f"Could not parse int from response {text!r} for command {command!r}"
            ) from exc

    def get_id(self) -> str:
        return self.query_text("IDN")

    def get_magnitude(self) -> float:
        values = self._query_numeric_fields("MAG.")
        if len(values) != 1:
            raise DSP7230ParseError(
                f"MAG. should return exactly one magnitude value, got {len(values)} fields"
            )
        return values[0]

    def get_phase_deg(self) -> float:
        values = self._query_numeric_fields("PHA.")
        if len(values) != 1:
            raise DSP7230ParseError(
                f"PHA. should return exactly one phase value, got {len(values)} fields"
            )
        return values[0]

    @staticmethod
    def _parse_float_robust(text: str) -> float:
        """
        Parse a float defensively.

        Handles normal values like:
            7.95E-08
            -2.937E+01

        Also repairs malformed DSP7230 MP responses seen in practice, e.g.:
            77.95E7.95E-08
            66.8556.855E-07
            77.1757.175E-07

        where the mantissa appears duplicated before the exponent.
        """
        s = text.replace("\x00", "").strip()
        if not s:
            raise DSP7230ParseError(f"Could not parse empty float from {text!r}")
        if "," in s or len(s.split()) > 1:
            raise DSP7230ParseError(f"Expected one float field, got {text!r}")
        if not re.match(r'^[+-]?(?:\d|\.)', s):
            raise DSP7230ParseError(f"Unexpected leading character in numeric response {text!r}")

        # Normal path
        try:
            return float(s)
        except ValueError:
            pass

        # Corrupted replies sometimes duplicate the mantissa before the final
        # exponent, e.g. 77.95E7.95E-08 or 66.8556.855E-07.  The intended DSP7230
        # value is the last scientific mantissa before the final exponent.
        if "E" in s.upper() and "." in s:
            upper = s.upper()
            last_e = upper.rfind("E")
            exponent_text = s[last_e + 1 :]
            prefix = s[:last_e]
            last_dot = prefix.rfind(".")
            if last_dot > 0 and re.fullmatch(r'[+-]?\d+', exponent_text):
                left_digit = prefix[last_dot - 1]
                frac_match = re.match(r'\d+', prefix[last_dot + 1 :])
                if left_digit.isdigit() and frac_match:
                    sign = ""
                    if "-" in prefix[:last_dot]:
                        sign = "-"
                    repaired = f"{sign}{left_digit}.{frac_match.group(0)}E{exponent_text}"
                    try:
                        return float(repaired)
                    except ValueError:
                        pass

        # Case like: 77.95E7.95E-08
        # pattern: <mantissa>E<mantissa>E<exp>
        m = re.fullmatch(r'([+-]?\d+(?:\.\d+)?)E\1E([+-]?\d+)', s)
        if m:
            mantissa_text = m.group(1)
            exponent_text = m.group(2)
            digits = mantissa_text.replace(".", "").replace("+", "").replace("-", "")
            if not digits:
                raise DSP7230ParseError(f"Malformed numeric response: {text!r}")

            sign = "-" if mantissa_text.startswith("-") else ""
            repaired = f"{sign}{digits[0]}.{digits[1:]}E{exponent_text}"
            return float(repaired)

        # Case like: 66.8556.855E-07 or 77.1757.175E-07
        # pattern: AB.CDE + BC.DE style duplication
        m = re.fullmatch(r'([+-]?\d+)(\.\d+)\1?\2E([+-]?\d+)', s)
        if m:
            left = m.group(1)
            frac = m.group(2)
            exponent_text = m.group(3)
            mantissa_text = f"{left}{frac}"
            digits = mantissa_text.replace(".", "").replace("+", "").replace("-", "")
            if not digits:
                raise DSP7230ParseError(f"Malformed numeric response: {text!r}")

            sign = "-" if mantissa_text.startswith("-") else ""
            repaired = f"{sign}{digits[0]}.{digits[1:]}E{exponent_text}"
            return float(repaired)

        # More general salvage:
        # If there are multiple "E"s, try using the last exponent and reconstruct
        if s.count("E") >= 2:
            last_e = s.rfind("E")
            exponent_part = s[last_e + 1 :]

            if re.fullmatch(r'[+-]?\d+', exponent_part):
                prefix = s[:last_e]
                # strip all E and dots/signs to recover digits
                digit_groups = re.findall(r'\d+', prefix)
                joined = "".join(digit_groups)
                if len(joined) >= 2:
                    repaired = f"{joined[0]}.{joined[1:]}E{exponent_part}"
                    try:
                        return float(repaired)
                    except ValueError:
                        pass

        raise DSP7230ParseError(f"Could not parse float from {text!r}")

    @classmethod
    def _parse_numeric_fields(cls, text: str) -> list[float]:
        """Parse one or more numeric fields from a DSP7230 response."""
        s = str(text).replace("\x00", "").strip()
        if not s:
            return []
        raw_parts = [part.strip() for part in s.split(",")]
        if len(raw_parts) == 1:
            raw_parts = s.split()
        values = []
        for part in raw_parts:
            if not part:
                continue
            values.append(cls._parse_float_robust(part))
        return values

    def _query_numeric_fields(self, command: str) -> list[float]:
        text = self.query_text(command)
        try:
            return self._parse_numeric_fields(text)
        except DSP7230ParseError as first_exc:
            LOGGER.debug("Retrying %s after malformed response %r", command, text, exc_info=True)
            try:
                self.clear_io()
            except Exception:
                LOGGER.debug("I/O clear before retry failed", exc_info=True)
            retry_text = self.query_text(command)
            try:
                return self._parse_numeric_fields(retry_text)
            except DSP7230ParseError as second_exc:
                raise DSP7230ParseError(
                    f"Could not parse numeric response for {command!r}; "
                    f"first={text!r}, retry={retry_text!r}"
                ) from second_exc

    def _get_mag_phase_mp(self, command: str = "MP.") -> tuple[float, float]:
        """
        Read magnitude and phase together using MP./MP1.
        """
        values = self._query_numeric_fields(command)
        if len(values) != 2:
            raise DSP7230ParseError(
                f"Expected exactly magnitude,phase from {command}, got {len(values)} fields"
            )

        return values[0], values[1]

    def get_mag_phase(self) -> tuple[float, float]:
        """
        Prefer synchronized MP./MP1. magnitude + phase reads.
        If those are malformed, fall back only to strict single-field MAG./PHA.
        """
        for command in ("MP.", "MP1."):
            try:
                return self._get_mag_phase_mp(command)
            except DSP7230ParseError:
                LOGGER.debug("No valid synchronized response from %s", command, exc_info=True)

        magnitude = self.get_magnitude()
        phase_deg = self.get_phase_deg()
        return magnitude, phase_deg

    def get_overload_byte(self) -> int:
        return self.query_int("N")

    def get_status_byte(self) -> int:
        """Return the DSP7230 status byte (ST), including input overload."""
        return self.query_int("ST")

    def get_sensitivity_volts(self) -> float:
        return self.query_float("SEN.")

    def get_sensitivity_index(self) -> int:
        return self.query_int("SEN")

    def set_sensitivity(self, index: int) -> None:
        if not (MIN_SENS_IDX <= index <= MAX_SENS_IDX):
            raise ValueError(
                f"Sensitivity index must be between {MIN_SENS_IDX} and {MAX_SENS_IDX}."
            )
        self.write(f"SEN {index}")

    def auto_sensitivity(self) -> None:
        self.write("AS")

    def auto_phase(self) -> None:
        self.write("AQN")

    def auto_measure(self) -> None:
        self.write("ASM")

    def set_float_input_shell(self) -> None:
        self.write("FLOAT 1")

    def disable_line_filter(self) -> None:
        self.write("LF 0 0")

    def set_time_constant(self, index: int) -> None:
        if not 0 <= int(index) <= 30:
            raise ValueError("Time-constant index must be between 0 and 30.")
        self.write(f"TC {int(index)}")

    def set_voltage_input_mode(self) -> None:
        self.write("IMODE 0")

    def set_ac_coupling(self) -> None:
        self.write("DCCOUPLE 0")

    def set_automatic_ac_gain(self, enabled: bool = True) -> None:
        self.write(f"AUTOMATIC {1 if enabled else 0}")

    def set_reference_mode_single(self) -> None:
        self.write("REFMODE 0")

    def set_reference_source(self, index: int) -> None:
        """Set 0=internal, 1=external TTL, or 2=external analog reference."""
        if int(index) not in (0, 1, 2):
            raise ValueError("Reference source index must be 0, 1, or 2.")
        self.write(f"IE {int(index)}")

    def get_reference_source_index(self) -> int:
        return self.query_int("IE")

    def disable_synchronous_filter(self) -> None:
        self.write("SYNC 0")

    def disable_fast_output_mode(self) -> None:
        self.write("FASTMODE 0")

    def get_time_constant_seconds(self) -> float:
        """Return the DSP7230 time constant in seconds (``TC.`` query)."""
        return self.query_float("TC.")

    def set_filter_slope(self, index: int) -> None:
        """Set output-filter slope index: 0/1/2/3 = 6/12/18/24 dB/oct."""
        if int(index) not in (0, 1, 2, 3):
            raise ValueError("Filter slope index must be 0, 1, 2, or 3.")
        self.write(f"SLOPE {int(index)}")

    def get_filter_slope_index(self) -> int:
        return self.query_int("SLOPE")

    def get_reference_frequency_hz(self) -> float:
        """Return measured reference frequency; the DSP7230 returns 0 if unlocked."""
        return self.query_float("FRQ.")

    def set_harmonic(self, n: int) -> None:
        """Detect the n-th harmonic of the reference (``REFN n``).

        n=1 is the fundamental (normal operation); n=2 measures the
        second-harmonic (quadratic EO / electrostriction) response.
        """
        if not 1 <= int(n) <= 127:
            raise ValueError("Harmonic number must be between 1 and 127.")
        self.write(f"REFN {int(n)}")

    def get_harmonic(self) -> int:
        return self.query_int("REFN")

    def set_reference_phase_deg(self, phase_deg: float) -> None:
        """Set the reference phase in degrees (floating-point ``REFP.``)."""
        self.write(f"REFP. {float(phase_deg):.4f}")

    def get_reference_phase_deg(self) -> float:
        return self.query_float("REFP.")

    def get_xy(self) -> tuple[float, float]:
        """Synchronized X,Y demodulator outputs in volts (``XY.`` query)."""
        values = self._query_numeric_fields("XY.")
        if len(values) != 2:
            raise DSP7230ParseError(
                f"XY. should return exactly two values, got {len(values)} fields"
            )
        return values[0], values[1]

    def apply_safe_startup(
        self,
        *,
        run_auto_measure: bool = False,
        tc_index: Optional[int] = None,
    ) -> None:
        """
        Conservative startup sequence.
        """
        try:
            self.set_float_input_shell()
        except Exception:
            LOGGER.debug("FLOAT 1 failed", exc_info=True)

        try:
            self.disable_line_filter()
        except Exception:
            LOGGER.debug("LF 0 0 failed", exc_info=True)

        if run_auto_measure:
            try:
                self.auto_measure()
            except Exception:
                LOGGER.debug("ASM failed", exc_info=True)

        if tc_index is not None:
            try:
                self.set_time_constant(tc_index)
            except Exception:
                LOGGER.debug("TC failed", exc_info=True)


def format_voltage(value_v: float) -> str:
    abs_v = abs(value_v)
    if abs_v >= 1.0:
        return f"{value_v:.6f} V"
    if abs_v >= 1e-3:
        return f"{value_v * 1e3:.6f} mV"
    if abs_v >= 1e-6:
        return f"{value_v * 1e6:.6f} µV"
    if abs_v >= 1e-9:
        return f"{value_v * 1e9:.6f} nV"
    return f"{value_v:.3e} V"


def format_phase(value_deg: float) -> str:
    return f"{value_deg:.3f}°"


def format_sensitivity(value_v: float) -> str:
    return format_voltage(value_v).replace(".000000", "")


def nearest_sensitivity_index(sensitivity_v: float) -> int:
    safe_value = max(sensitivity_v, 1e-30)
    return min(
        SENS_TABLE_VOLTS,
        key=lambda idx: abs(math.log10(safe_value) - math.log10(SENS_TABLE_VOLTS[idx])),
    )


def decode_overload_byte(value: Optional[int]) -> str:
    if value is None:
        return "-"

    flags = []
    bit_map = {
        0: "X overload",
        1: "Y overload",
        2: "X2 overload",
        3: "Y2 overload",
        4: "CH1 overload",
        5: "CH2 overload",
        6: "CH3 overload",
        7: "CH4 overload",
    }

    for bit, text in bit_map.items():
        if value & (1 << bit):
            flags.append(text)

    return ", ".join(flags) if flags else "no overload"


class ReaderThread(threading.Thread):
    """
    Background polling thread for the instrument.
    """

    def __init__(
        self,
        instrument: DSP7230,
        out_queue: "queue.Queue[tuple[str, object]]",
        stop_event: threading.Event,
        *,
        poll_interval_s: float = 0.20,
        overload_every: int = 5,
        reconnect_delay_s: float = 2.0,
    ) -> None:
        super().__init__(daemon=True)
        self.instrument = instrument
        self.out_queue = out_queue
        self.stop_event = stop_event
        self.poll_interval_s = max(0.05, poll_interval_s)
        self.overload_every = max(1, overload_every)
        self.reconnect_delay_s = max(0.5, reconnect_delay_s)
        self._read_count = 0

    def _send(self, kind: str, payload: object) -> None:
        try:
            self.out_queue.put_nowait((kind, payload))
        except queue.Full:
            LOGGER.warning("UI queue full; dropping message %s", kind)

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if not self.instrument.is_connected():
                    self._send("status", "Connecting...")
                    self.instrument.connect()
                    self.instrument.apply_safe_startup(run_auto_measure=False)

                    try:
                        idn = self.instrument.get_id()
                        self._send("idn", idn)
                    except Exception:
                        LOGGER.debug("Could not read IDN after connect", exc_info=True)

                    self._send("status", "Connected")

                try:
                    magnitude, phase_deg = self.instrument.get_mag_phase()
                except DSP7230ParseError as exc:
                    LOGGER.warning("Skipping malformed reading: %s", exc)
                    self._send("status", f"Bad reading skipped: {exc}")
                    if self.stop_event.wait(self.poll_interval_s):
                        break
                    continue

                overload = None
                if self._read_count % self.overload_every == 0:
                    try:
                        overload = self.instrument.get_overload_byte()
                    except Exception:
                        LOGGER.debug("Overload read failed", exc_info=True)

                self._read_count += 1

                self._send(
                    "measurement",
                    Measurement(
                        timestamp=time.time(),
                        magnitude=magnitude,
                        phase_deg=phase_deg,
                        overload_byte=overload,
                    ),
                )

            except Exception as exc:
                LOGGER.exception("Read loop transport error")
                self._send("status", f"Disconnected: {exc}")
                self.instrument.close()

                if self.stop_event.wait(self.reconnect_delay_s):
                    break
                continue

            if self.stop_event.wait(self.poll_interval_s):
                break


class App:
    def __init__(
        self,
        root: tk.Tk,
        instrument: DSP7230,
        poll_interval_s: float,
        history_seconds: float,
    ) -> None:
        self.root = root
        self.instrument = instrument
        self.history_seconds = max(5.0, history_seconds)

        self.root.title("DSP7230 Live Magnitude + Phase")
        self.root.geometry("1050x700")
        self.root.minsize(920, 620)

        self.queue: "queue.Queue[tuple[str, object]]" = queue.Queue(maxsize=200)
        self.stop_event = threading.Event()

        self.reader = ReaderThread(
            instrument=self.instrument,
            out_queue=self.queue,
            stop_event=self.stop_event,
            poll_interval_s=poll_interval_s,
        )

        self.times: deque[float] = deque()
        self.magnitudes: deque[float] = deque()
        self.phases: deque[float] = deque()

        self.last_overload: Optional[int] = None
        self.current_sens_idx = 24

        self.status_var = tk.StringVar(value="Starting...")
        self.idn_var = tk.StringVar(value="Instrument: -")
        self.mag_var = tk.StringVar(value="Magnitude: -")
        self.phase_var = tk.StringVar(value="Phase: -")
        self.overload_var = tk.StringVar(value="Overload: -")
        self.sens_var = tk.StringVar(value="Sensitivity: -")

        self._build_ui()
        self._load_initial_sensitivity()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.reader.start()
        self.root.after(100, self.process_queue)
        self.root.after(500, self.refresh_plots)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(outer)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            top,
            textvariable=self.idn_var,
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(top, textvariable=self.status_var).pack(anchor=tk.W)

        values = ttk.Frame(outer)
        values.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(
            values,
            textvariable=self.mag_var,
            font=("TkDefaultFont", 20, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 25))

        ttk.Label(
            values,
            textvariable=self.phase_var,
            font=("TkDefaultFont", 20, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 25))

        ttk.Label(
            values,
            textvariable=self.overload_var,
            font=("TkDefaultFont", 11),
        ).grid(row=1, column=0, sticky="w")

        ttk.Label(
            values,
            textvariable=self.sens_var,
            font=("TkDefaultFont", 11),
        ).grid(row=1, column=1, sticky="w")

        controls = ttk.LabelFrame(outer, text="Controls", padding=10)
        controls.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(
            controls,
            text="Auto Sensitivity",
            command=self.auto_sensitivity,
        ).grid(row=0, column=0, padx=5, pady=5)

        ttk.Button(
            controls,
            text="Auto Phase",
            command=self.auto_phase,
        ).grid(row=0, column=1, padx=5, pady=5)

        ttk.Button(
            controls,
            text="Sensitivity -",
            command=self.sens_down,
        ).grid(row=0, column=2, padx=5, pady=5)

        ttk.Button(
            controls,
            text="Sensitivity +",
            command=self.sens_up,
        ).grid(row=0, column=3, padx=5, pady=5)

        ttk.Button(
            controls,
            text="Reconnect",
            command=self.force_reconnect,
        ).grid(row=0, column=4, padx=5, pady=5)

        figure = Figure(figsize=(10, 5), dpi=100)
        self.ax_mag = figure.add_subplot(211)
        self.ax_phase = figure.add_subplot(212)

        self.ax_mag.set_ylabel("Magnitude (V)")
        self.ax_phase.set_ylabel("Phase (deg)")
        self.ax_phase.set_xlabel("Time (s)")
        self.ax_mag.grid(True)
        self.ax_phase.grid(True)

        (self.mag_line,) = self.ax_mag.plot([], [])
        (self.phase_line,) = self.ax_phase.plot([], [])

        figure.tight_layout()

        self.canvas = FigureCanvasTkAgg(figure, master=outer)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def _load_initial_sensitivity(self) -> None:
        try:
            self.instrument.connect()
            sens_v = self.instrument.get_sensitivity_volts()
            self.current_sens_idx = nearest_sensitivity_index(sens_v)
            self.sens_var.set(
                f"Sensitivity: {format_sensitivity(SENS_TABLE_VOLTS[self.current_sens_idx])}"
            )

            try:
                self.idn_var.set(f"Instrument: {self.instrument.get_id()}")
            except Exception:
                LOGGER.debug("Could not read IDN during startup", exc_info=True)

        except Exception as exc:
            LOGGER.warning("Could not read initial sensitivity: %s", exc)
            self.sens_var.set("Sensitivity: unknown")

        finally:
            self.instrument.close()

    def on_close(self) -> None:
        self.stop_event.set()
        self.instrument.close()
        self.root.after(100, self.root.destroy)

    def auto_sensitivity(self) -> None:
        self._run_command(self.instrument.auto_sensitivity, "Auto sensitivity sent")
        self.root.after(700, self.refresh_sensitivity_from_instrument)

    def auto_phase(self) -> None:
        self._run_command(self.instrument.auto_phase, "Auto phase sent")

    def sens_up(self) -> None:
        if self.current_sens_idx < MAX_SENS_IDX:
            self.current_sens_idx += 1
            self._set_sensitivity(self.current_sens_idx)

    def sens_down(self) -> None:
        if self.current_sens_idx > MIN_SENS_IDX:
            self.current_sens_idx -= 1
            self._set_sensitivity(self.current_sens_idx)

    def _set_sensitivity(self, idx: int) -> None:
        self._run_command(
            lambda: self.instrument.set_sensitivity(idx),
            f"Set sensitivity to {format_sensitivity(SENS_TABLE_VOLTS[idx])}",
        )
        self.sens_var.set(f"Sensitivity: {format_sensitivity(SENS_TABLE_VOLTS[idx])}")

    def refresh_sensitivity_from_instrument(self) -> None:
        def worker() -> None:
            try:
                self.instrument.connect()
                sens_v = self.instrument.get_sensitivity_volts()
                idx = nearest_sensitivity_index(sens_v)
                self.queue.put(("sens_idx", idx))
            except Exception as exc:
                self.queue.put(("status", f"Sensitivity refresh failed: {exc}"))
            finally:
                self.instrument.close()

        threading.Thread(target=worker, daemon=True).start()

    def force_reconnect(self) -> None:
        self.instrument.close()
        self.status_var.set("Reconnect requested")

    def _run_command(self, func: Callable[[], None], status_text: str) -> None:
        def worker() -> None:
            try:
                self.instrument.connect()
                func()
                self.queue.put(("status", status_text))
            except Exception as exc:
                LOGGER.exception("Command failed")
                self.queue.put(("status", f"Command failed: {exc}"))
            finally:
                self.instrument.close()

        threading.Thread(target=worker, daemon=True).start()

    def process_queue(self) -> None:
        while True:
            try:
                kind, payload = self.queue.get_nowait()
            except queue.Empty:
                break

            if kind == "measurement":
                meas = payload
                if isinstance(meas, Measurement):
                    self._handle_measurement(meas)

            elif kind == "status":
                self.status_var.set(str(payload))

            elif kind == "idn":
                self.idn_var.set(f"Instrument: {payload}")

            elif kind == "sens_idx":
                idx = int(payload)
                self.current_sens_idx = idx
                self.sens_var.set(
                    f"Sensitivity: {format_sensitivity(SENS_TABLE_VOLTS[idx])}"
                )

        self.root.after(100, self.process_queue)

    def _handle_measurement(self, meas: Measurement) -> None:
        self.mag_var.set(f"Magnitude: {format_voltage(meas.magnitude)}")
        self.phase_var.set(f"Phase: {format_phase(meas.phase_deg)}")

        if meas.overload_byte is not None:
            self.last_overload = meas.overload_byte

        overload_text = (
            f"Overload byte: {self.last_overload} ({decode_overload_byte(self.last_overload)})"
            if self.last_overload is not None
            else "Overload: unknown"
        )
        self.overload_var.set(overload_text)

        self.times.append(meas.timestamp)
        self.magnitudes.append(meas.magnitude)
        self.phases.append(meas.phase_deg)

        self._trim_history()

    def _trim_history(self) -> None:
        cutoff = time.time() - self.history_seconds
        while self.times and self.times[0] < cutoff:
            self.times.popleft()
            self.magnitudes.popleft()
            self.phases.popleft()

    def refresh_plots(self) -> None:
        if self.times:
            t0 = self.times[0]
            x = [t - t0 for t in self.times]

            self.mag_line.set_data(x, list(self.magnitudes))
            self.phase_line.set_data(x, list(self.phases))

            self.ax_mag.relim()
            self.ax_mag.autoscale_view()

            self.ax_phase.relim()
            self.ax_phase.autoscale_view()

            if x:
                xmin = max(0.0, x[-1] - self.history_seconds)
                xmax = max(self.history_seconds, x[-1])
                self.ax_mag.set_xlim(xmin, xmax)
                self.ax_phase.set_xlim(xmin, xmax)

            self.canvas.draw_idle()

        self.root.after(500, self.refresh_plots)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Live magnitude/phase viewer for DSP7230"
    )
    parser.add_argument(
        "--resource",
        default="TCPIP0::169.254.150.230::50001::SOCKET",
        help="PyVISA resource string for the DSP7230",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.20,
        help="Polling interval in seconds (default: 0.20)",
    )
    parser.add_argument(
        "--history-seconds",
        type=float,
        default=60.0,
        help="Seconds of history to keep on-screen (default: 60)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=2000,
        help="VISA timeout in milliseconds (default: 2000)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    instrument = DSP7230(
        resource_name=args.resource,
        timeout_ms=args.timeout_ms,
        delimiter=",",
    )

    root = tk.Tk()
    App(
        root=root,
        instrument=instrument,
        poll_interval_s=args.poll_interval,
        history_seconds=args.history_seconds,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
