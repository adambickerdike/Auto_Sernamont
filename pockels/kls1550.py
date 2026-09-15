
"""
kls1550.py — Standalone controller for Thorlabs KLS1550 (via Kinesis .NET API)

- Connect, set absolute power, on/off, current/power readbacks (when available)
- Culture-invariant Decimal handling
- Python <3.8 compatible (no walrus)
"""

from __future__ import annotations
import argparse
import os
import sys
import time
from typing import Optional, List

import clr  # type: ignore

DEFAULT_KINESIS_DIRS = [
    r"C:\Program Files\Thorlabs\Kinesis",
    r"C:\Program Files (x86)\Thorlabs\Kinesis",
]


def _ensure_kinesis_loaded(custom_path: Optional[str] = None) -> None:
    root = (
        custom_path
        or os.environ.get("THORLABS_KINESIS_PATH")
        or next((p for p in DEFAULT_KINESIS_DIRS if os.path.isdir(p)), None)
    )
    if not root:
        raise RuntimeError("Cannot find Thorlabs Kinesis. Set THORLABS_KINESIS_PATH or use --kinesis-path.")
    if root not in sys.path:
        sys.path.append(root)

    clr.AddReference("Thorlabs.MotionControl.DeviceManagerCLI")
    clr.AddReference("Thorlabs.MotionControl.KCube.LaserSourceCLI")

    global DeviceManagerCLI, KCubeLaserSource, InputSourceSettings
    from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI
    from Thorlabs.MotionControl.KCube.LaserSourceCLI import (
        KCubeLaserSource,
        InputSourceSettings,
    )


def _to_float(x) -> float:
    try:
        return float(str(x))
    except:
        return float(x)


class KLS1550:
    def __init__(self, serial: str, poll_ms: int = 100):
        _ensure_kinesis_loaded()
        self.serial = serial
        self.poll_ms = poll_ms
        self._kls = KCubeLaserSource.CreateKCubeLaserSource(serial)
        if self._kls is None:
            raise RuntimeError("CreateKCubeLaserSource returned null.")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @staticmethod
    def list_serials(prefix=56) -> List[str]:
        _ensure_kinesis_loaded()
        from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI
        DeviceManagerCLI.BuildDeviceList()
        return list(DeviceManagerCLI.GetDeviceList(prefix))

    @staticmethod
    def find_first(prefix=56):
        items = KLS1550.list_serials(prefix)
        return items[0] if items else None

    def connect(self):
        self._kls.Connect(self.serial)
        self._kls.WaitForSettingsInitialized(5000)
        self._kls.StartPolling(self.poll_ms)
        time.sleep(0.5)
        flags = InputSourceSettings.LaserSourceInputSourceFlags
        self._kls.SetControlSource(flags.SoftwareOnly)

    def close(self, disconnect_device=False):
        try: self.off()
        except: pass
        try: self._kls.StopPolling()
        finally: self._kls.Disconnect(disconnect_device)

    def get_limits(self):
        lim = self._kls.GetLimits()
        return _to_float(lim.MaxPower), _to_float(lim.MaxCurrent)

    def set_power_absolute(self, power_mw: float):
        from System import Decimal
        from System.Globalization import CultureInfo
        dec = Decimal.Parse(str(float(power_mw)), CultureInfo.InvariantCulture)
        self._kls.SetPower(dec)

    def on(self):  self._kls.SetOn()
    def off(self): self._kls.SetOff()

    def get_current(self) -> float:
        status = self._kls.Status
        for field in ("LaserCurrent", "Current", "DriveCurrent", "ActualCurrent"):
            if hasattr(status, field):
                return _to_float(getattr(status, field))
        raise RuntimeError("Could not read current — Status lacks a known field.")

    def get_measured_power(self):
        status = self._kls.Status
        for field in ("LaserPower", "MeasuredPower", "ActualPower", "Power"):
            if hasattr(status, field):
                return _to_float(getattr(status, field))
        return None


if __name__ == "__main__":
    import traceback
    p = argparse.ArgumentParser()
    p.add_argument("--serial", type=str, default=None)
    p.add_argument("--power-abs", type=float, default=7.0)
    p.add_argument("--on-seconds", type=float, default=5.0)
    p.add_argument("--no-on", action="store_true")
    p.add_argument("--kinesis-path", type=str, default=None)
    args = p.parse_args()

    _ensure_kinesis_loaded(args.kinesis_path)
    serial = args.serial or KLS1550.find_first()
    if not serial:
        print("No KLS detected.")
        sys.exit(1)

    try:
        with KLS1550(serial) as kls:
            mp, mi = kls.get_limits()
            print("Max Power:", mp, "mW  Max Current:", mi, "mA")
            kls.set_power_absolute(args.power_abs)
            if not args.no_on:
                print("Laser ON")
                kls.on(); time.sleep(args.on_seconds); kls.off(); print("Laser OFF")
            print("Done.")
    except Exception:
        traceback.print_exc(); sys.exit(2)
