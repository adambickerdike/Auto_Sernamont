
"""
stage_xy.py — XY stage controller for two Thorlabs KCube Stepper axes via Kinesis .NET

- Wraps both X and Y axes under one class.
- Provides: connect(), home_both(), set_velocity(), move_to_motor(), move_to_global(), center_on_origin(), get_positions(), close()
- Python <3.8 compatible.
"""

import time
import threading
from decimal import Decimal as PyDecimal, getcontext

# High precision for global<->motor conversions
getcontext().prec = 10

# pythonnet bootstrap for .NET Framework (works with pythonnet 3.x)
import clr_loader
try:
    runtime = clr_loader.get_netfx()
    from pythonnet import set_runtime
    set_runtime(runtime)
except RuntimeError:
    pass

import clr
from typing import Tuple

# You may change these paths if needed (or add to sys.path another way)
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.DeviceManagerCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\Thorlabs.MotionControl.GenericMotorCLI.dll")
clr.AddReference(r"C:\Program Files\Thorlabs\Kinesis\ThorLabs.MotionControl.KCube.StepperMotorCLI.dll")

from Thorlabs.MotionControl.DeviceManagerCLI import DeviceManagerCLI
from Thorlabs.MotionControl.KCube.StepperMotorCLI import KCubeStepper
from System import Decimal as NetDecimal


class StageXY:
    """
    Convenience wrapper for 2× KCubeStepper as a single XY stage.
    Coordinates:
        - Motor axes: [0, 25] mm travel
        - Global axes: [-12.5, +12.5] mm (centered)
    """

    def __init__(self, serial_x: str, serial_y: str, motor_center_mm: float = 12.5, poll_ms: int = 250):
        self.serial_x = serial_x
        self.serial_y = serial_y
        self.center = PyDecimal(str(motor_center_mm))
        self.poll_ms = poll_ms
        self.x = None
        self.y = None

    # ----------------- lifecycle -----------------

    def connect(self) -> None:
        DeviceManagerCLI.BuildDeviceList()

        self.x = KCubeStepper.CreateKCubeStepper(self.serial_x)
        self.y = KCubeStepper.CreateKCubeStepper(self.serial_y)

        self.x.Connect(self.serial_x); time.sleep(0.25)
        self.y.Connect(self.serial_y); time.sleep(0.25)

        self.x.StartPolling(self.poll_ms); self.y.StartPolling(self.poll_ms)
        time.sleep(0.25)

        self.x.EnableDevice(); self.y.EnableDevice()
        time.sleep(0.25)

        # Load config & set default vel/accel
        for dev in (self.x, self.y):
            _ = dev.LoadMotorConfiguration(dev.DeviceID)
            vp = dev.GetVelocityParams()
            vp.MaxVelocity = NetDecimal(2.0)
            vp.Acceleration = NetDecimal(2.0)
            dev.SetVelocityParams(vp)
        time.sleep(0.25)

    def close(self) -> None:
        if self.x is not None:
            try: self.x.StopPolling()
            except: pass
            try: self.x.Disconnect()
            except: pass
        if self.y is not None:
            try: self.y.StopPolling()
            except: pass
            try: self.y.Disconnect()
            except: pass

    # ----------------- helpers -----------------

    def set_velocity(self, vx: float, ax: float, vy: float, ay: float) -> None:
        vp = self.x.GetVelocityParams(); vp.MaxVelocity = NetDecimal(vx); vp.Acceleration = NetDecimal(ax); self.x.SetVelocityParams(vp)
        vp = self.y.GetVelocityParams(); vp.MaxVelocity = NetDecimal(vy); vp.Acceleration = NetDecimal(ay); self.y.SetVelocityParams(vp)
        time.sleep(0.1)

    def home_both(self, timeout_ms: int = 60000) -> None:
        tx = threading.Thread(target=self.x.Home, args=(timeout_ms,))
        ty = threading.Thread(target=self.y.Home, args=(timeout_ms,))
        tx.start(); ty.start(); tx.join(); ty.join()

    # ----------------- coordinate transforms -----------------

    def motor_to_global(self, motor_pos: PyDecimal) -> PyDecimal:
        return PyDecimal(str(motor_pos)) - self.center

    def global_to_motor(self, global_pos: PyDecimal) -> PyDecimal:
        return global_pos + self.center

    # ----------------- motion -----------------

    def move_to_motor(self, x_mm: float, y_mm: float, timeout_ms: int = 60000) -> None:
        """Blocking absolute move in motor coordinates (mm)."""
        tx = threading.Thread(target=self.x.MoveTo, args=(NetDecimal(float(x_mm)), timeout_ms))
        ty = threading.Thread(target=self.y.MoveTo, args=(NetDecimal(float(y_mm)), timeout_ms))
        tx.start(); ty.start(); tx.join(); ty.join()

    def move_to_global(self, gx_mm: float, gy_mm: float, timeout_ms: int = 60000) -> None:
        """Blocking absolute move given global coordinates (mm)."""
        gx = PyDecimal(str(gx_mm)); gy = PyDecimal(str(gy_mm))
        mx = self.global_to_motor(gx); my = self.global_to_motor(gy)
        self.move_to_motor(float(mx), float(my), timeout_ms=timeout_ms)

    def center_on_origin(self, timeout_ms: int = 60000) -> None:
        """Move to (global 0,0)."""
        self.move_to_motor(float(self.center), float(self.center), timeout_ms=timeout_ms)

    # ----------------- readback -----------------

    def get_positions(self):
        """
        Returns:
            motor_x, motor_y, global_x, global_y
        """
        mx = PyDecimal(str(self.x.Position))
        my = PyDecimal(str(self.y.Position))
        gx = self.motor_to_global(mx)
        gy = self.motor_to_global(my)
        return float(mx), float(my), float(gx), float(gy)
