"""
Aim-TTi SMU4201 I-V sweep: -5 V to +5 V in 200 mV steps.
At each step the SMU sources voltage and measures current.

Connection: USB virtual COM (COM11), 9600 8N1, terminator \r\n.
Reference: SMU4000 Series Programming Manual, Issue 1.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import serial

# ---- User-configurable ------------------------------------------------------
PORT          = "COM11"
BAUD          = 9600
V_START       = -5.0      # V
V_STOP        =  5.0      # V
V_STEP        =  0.2      # V (200 mV)
I_COMPLIANCE  =  0.1      # A (100 mA — change to suit the DUT)
NPLC          =  1.0      # power-line cycles per measurement
SETTLE_S      =  0.2     # extra settle after the SMU's own delay
PRESWEEP_S    =  5.0      # wait after OUTPUT ON for the on-screen banner to clear
OUT_DIR       = Path(r"D:\Pockels_Setup\Control\smu4201_sweeps")
# ----------------------------------------------------------------------------


class SMU4201:
    """Thin SCPI wrapper around the Aim-TTi SMU4201 over USB-CDC serial."""

    def __init__(self, port: str = PORT, baud: int = BAUD, timeout: float = 2.0):
        self.ser = serial.Serial(
            port, baud, timeout=timeout, write_timeout=timeout,
            bytesize=8, parity="N", stopbits=1,
            rtscts=False, dsrdtr=False,
        )
        try:
            time.sleep(0.1)
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except BaseException:
            self.ser.close()
            raise

    # --- low-level ----------------------------------------------------------
    def write(self, cmd: str) -> None:
        data = (cmd + "\r\n").encode("ascii")
        if self.ser.write(data) != len(data):
            raise serial.SerialException("Incomplete SMU serial write")

    def query(self, cmd: str) -> str:
        self.write(cmd)
        line = self.ser.readline()
        return line.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        try:
            self.restore_safe_defaults()
        finally:
            self.ser.close()

    # --- helpers ------------------------------------------------------------
    def check_errors(self) -> list[str]:
        errs = []
        for _ in range(20):
            r = self.query("SYSTem:ERRor?")
            if not r or r.startswith("0,") or r.startswith('+0,') or "No error" in r:
                break
            errs.append(r)
        return errs

    def idn(self) -> str:
        return self.query("*IDN?")

    # --- session setup ------------------------------------------------------
    def configure_source_voltage_measure_current(
        self, compliance_a: float, nplc: float
    ) -> None:
        # Reset, then move to Source-Voltage / Steady.  Keep current as the
        # primary result and actual terminal voltage as the secondary result
        # so combined DC/AC workflows can distinguish a programmed setpoint
        # from the voltage the SMU is really measuring.
        self.write("*CLS")
        self.write("*RST")
        time.sleep(0.6)                                      # *RST takes a moment
        self.write("SYSTem:FUNCtion:MODE SOURCEVOLTage")
        self.write("SOURce:VOLTage:TERMinals 2WIRe")         # deterministic terminal config
        self.write("SOURce:VOLTage:SHAPe FIXed")
        self.write("SOURce:VOLTage:SHAPe:COUNt 1")           # one shape per "run"
        self.write("SOURce:VOLTage:SHAPe:TRIGger OFF")       # don't wait for an external trigger
        self.write("SOURce:VOLTage:MEASure:PRIMary CURRent")
        self.write("SOURce:VOLTage:MEASure:SECondary VOLTage")
        # Infinite measure count keeps the SMU continuously sampling while
        # the script steps the source — this is what keeps the front-panel
        # "Run" light on for the whole sweep.
        self.write("SOURce:VOLTage:MEASure:COUNt:INFinite ON")
        self.write(f"SOURce:VOLTage:CURRent:LIMit {compliance_a:.6g}")
        self.write(f"SOURce:VOLTage:FIXed:APERture:NPLCycles {nplc:.3f}")
        self.write("SOURce:VOLTage:FIXed:LEVel 0")           # start at 0 V
        self.write("SOURce:VOLTage:DELay:AUTO ON")           # auto settling delay

    def restore_safe_defaults(self) -> None:
        """Leave the SMU in a state that the front panel handles cleanly."""
        try:
            self.write("OUTPut:STATe OFF")
            self.write("SOURce:VOLTage:FIXed:LEVel 0")
            self.write("SOURce:VOLTage:MEASure:COUNt:INFinite OFF")
            self.write("SOURce:VOLTage:MEASure:COUNt 1")
        except Exception:
            pass

    def set_voltage(self, v: float) -> None:
        self.write(f"SOURce:VOLTage:FIXed:LEVel {v:.6f}")

    def output(self, on: bool) -> None:
        self.write(f"OUTPut:STATe {'ON' if on else 'OFF'}")

    def measure_primary(self) -> float:
        raw = self.query("MEASure:PRIMary:LIVEdata?")
        try:
            return float(raw.split(",")[0])                  # tolerate "<val>,<unit>"
        except ValueError:
            return float("nan")

    def measure_secondary(self) -> float:
        raw = self.query("MEASure:SECondary:LIVEdata?")
        try:
            return float(raw.split(",")[0])                  # tolerate "<val>,<unit>"
        except ValueError:
            return float("nan")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"iv_sweep_{stamp}.csv"

    voltages = np.round(
        np.arange(V_START, V_STOP + V_STEP / 2, V_STEP), 6
    )
    n = len(voltages)

    smu = SMU4201()
    try:
        idn = smu.idn()
        print(f"Connected: {idn}")
        print(f"Sweep:     {V_START:+.3f} V → {V_STOP:+.3f} V, "
              f"{V_STEP*1000:.1f} mV step  ({n} points)")
        print(f"Compliance: {I_COMPLIANCE*1000:.3f} mA   NPLC: {NPLC}")
        print(f"Output CSV: {csv_path}\n")

        smu.configure_source_voltage_measure_current(I_COMPLIANCE, NPLC)
        errs = smu.check_errors()
        if errs:
            print("Setup errors:", errs)

        smu.output(True)
        time.sleep(0.3)
        out_state = smu.query("OUTPut:STATe?")
        mode = smu.query("SYSTem:FUNCtion:MODE?")
        prim = smu.query("SOURce:VOLTage:MEASure:PRIMary?")
        print(f"Output state: {out_state}    Mode: {mode}    Primary: {prim}")
        if out_state.strip() not in ("1", "ON"):
            raise RuntimeError(
                f"Output failed to enable (state={out_state!r}). Aborting sweep."
            )

        # Hold off — the SMU shows a "Counts / Shapes = 1" banner on its
        # front panel for a few seconds after enabling the output.
        print(f"Output ON — waiting {PRESWEEP_S:.1f} s for front-panel "
              f"banner to clear before starting sweep...")
        for remaining in range(int(PRESWEEP_S), 0, -1):
            print(f"  starting in {remaining}...", end="\r", flush=True)
            time.sleep(1.0)
        # absorb any sub-second remainder
        time.sleep(PRESWEEP_S - int(PRESWEEP_S))
        print(" " * 30, end="\r")
        print("Sweep starting.\n")

        rows: list[tuple[float, float, float]] = []
        t0 = time.time()
        for i, v in enumerate(voltages):
            smu.set_voltage(float(v))
            time.sleep(SETTLE_S)
            i_meas = smu.measure_primary()
            t = time.time() - t0
            rows.append((t, float(v), i_meas))
            print(f"  [{i+1:>3}/{n}] V = {v:+7.3f} V    I = {i_meas:+.6e} A")

        smu.set_voltage(0.0)
        smu.output(False)

        errs = smu.check_errors()
        if errs:
            print("\nPost-sweep errors:", errs)

    finally:
        smu.close()

    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "voltage_V", "current_A"])
        w.writerows(rows)
    print(f"\nSaved {len(rows)} points → {csv_path}")

    try:
        import matplotlib.pyplot as plt
        V = np.array([r[1] for r in rows])
        I = np.array([r[2] for r in rows]) * 1000.0   # mA
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(V, I, "o-", lw=1.2, ms=4)
        ax.axhline(0, color="k", lw=0.5)
        ax.axvline(0, color="k", lw=0.5)
        ax.set_xlabel("Voltage (V)")
        ax.set_ylabel("Current (mA)")
        ax.set_title(f"SMU4201 I-V sweep  ({stamp})")
        ax.grid(True, alpha=0.3)
        png_path = csv_path.with_suffix(".png")
        fig.tight_layout()
        fig.savefig(png_path, dpi=120)
        print(f"Saved plot   → {png_path}")
        plt.show()
    except ImportError:
        pass


if __name__ == "__main__":
    main()
