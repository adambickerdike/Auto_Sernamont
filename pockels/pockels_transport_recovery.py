"""Reconnect a fast-map hardware bundle between pixels, with no data replay.

This module imports no instrument drivers so the recovery sequence can be
tested without connecting to the laboratory hardware.
"""

from __future__ import annotations

import math
import re


class HardwareRecoveryExhausted(RuntimeError):
    """An explicitly configured recovery attempt limit was reached."""


def serial_port_identities(ports):
    """Snapshot USB identity before a disconnect can renumber a COM port."""
    return {
        str(p.device).upper(): {
            name: getattr(p, name, None)
            for name in ("serial_number", "location", "vid", "pid")
        }
        for p in ports
    }


def recovery_serial_port(port, identities, ports):
    """Follow a unique USB identity; never guess between identical adapters."""
    identity = identities.get(str(port).upper(), {})
    candidates = list(ports)
    for field in ("serial_number", "location"):
        if not identity.get(field):
            continue
        matches = [
            p for p in candidates
            if getattr(p, field, None) == identity[field]
            and getattr(p, "vid", None) == identity.get("vid")
            and getattr(p, "pid", None) == identity.get("pid")
        ]
        if len(matches) == 1:
            return str(matches[0].device)
        if matches:
            candidates = matches
        else:
            raise RuntimeError(f"USB identity for {port} is absent or ambiguous")
    if identity.get("serial_number") or identity.get("location"):
        raise RuntimeError(f"USB identity for {port} is absent or ambiguous")
    return port


class FastMapHardwareRecovery:
    """Own all replacement sessions until they have passed verification.

    ``hardware`` is updated immediately after each allocation, including on
    failure, so partially opened sessions are always closed before retrying.
    All old VISA owners are closed before any new one is opened: driver close
    methods may close a shared ResourceManager and invalidate other sessions.
    A wait callback keeps GUI stop requests responsive during backoff.
    """

    def __init__(self, hardware, *, args, auto, pockels, stage, voltages,
                 configure_lockin, simulated_lockin, sim_state, wait,
                 checkpoint, report, port_identities=None, list_ports=None):
        self.hardware = hardware
        self.args = args
        self.auto = auto
        self.pockels = pockels
        self.stage = stage
        self.voltages = voltages
        self.configure_lockin = configure_lockin
        self.simulated_lockin = simulated_lockin
        self.simulated_smu = isinstance(hardware.get("smu"), auto.FakeSMU)
        self.sim_state = sim_state
        self.wait = wait
        self.checkpoint = checkpoint
        self.report = report
        self.port_identities = port_identities or {}
        self.list_ports = list_ports
        self.matrix_enabled = hardware.get("switch_matrix") is not None
        resource = getattr(hardware.get("funcgen"), "resource_name", None)
        self.funcgen_resource = resource if isinstance(resource, str) else None
        self.rotator_offsets = {
            key: getattr(hardware.get(key), "_offset", 0)
            for key in ("rot_hwp", "rot_qwp", "rot_anl")
        }

    def _port(self, port):
        if self.list_ports is None:
            return port
        return recovery_serial_port(port, self.port_identities, self.list_ports())

    def close(self):
        hw = self.hardware
        # Attempt electrical isolation before any route changes or movement.
        for key, action in (
            ("funcgen", lambda fg: self.pockels._funcgen_set_sweep_output(fg, False)),
            ("smu", lambda smu: smu.output(False)),
        ):
            if hw.get(key) is not None:
                try:
                    action(hw[key])
                except Exception as exc:
                    self.report("shutdown_warning", instrument=key, error=str(exc))
        for key in ("switch_matrix", "smu", "rot_hwp", "rot_qwp", "rot_anl",
                    "funcgen", "lockin", "stage_detector"):
            obj = hw.get(key)
            hw[key] = None
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    self.report("close_warning", instrument=key, error=str(exc))
        for key in ("device_x", "device_y"):
            obj = hw.get(key)
            hw[key] = None
            if obj is not None:
                # Disconnect must still run when stopping polling fails.
                for method in ("StopPolling", "Disconnect"):
                    try:
                        getattr(obj, method)()
                    except Exception as exc:
                        self.report("close_warning", instrument=key, error=str(exc))

    def reconnect(self):
        hw, pockels, stage = self.hardware, self.pockels, self.stage
        self.checkpoint()
        resource = self.funcgen_resource
        isolation_errors = []
        try:
            match = re.fullmatch(r"ASRL(\d+)::INSTR", resource or "", flags=re.IGNORECASE)
            if match:
                port = self._port(f"COM{match.group(1)}")
                resource = f"ASRL{str(port)[3:]}::INSTR"
            hw["funcgen"] = (
                pockels.connect_funcgen(resource_name=resource)
                if resource else pockels.connect_funcgen()
            )
            pockels._funcgen_set_sweep_output(
                hw["funcgen"], False, verify_command=True, required=True,
                context="transport recovery before routing or motion",
            )
        except Exception as exc:
            isolation_errors.append(exc)
        self.checkpoint()
        try:
            if self.simulated_smu:
                hw["smu"] = self.auto.FakeSMU()
            else:
                hw["smu"] = pockels.SMU4201(
                    port=self._port(self.args.smu_port), baud=pockels.SMU_BAUD, timeout=2.0,
                )
                pockels._connect_smu(0.0, smu=hw["smu"], enable_output=False)
            self._verify_smu_off()
        except Exception as exc:
            isolation_errors.append(exc)

        # A reconnect resets the Arduino. Never replay the failed E<n> toggle:
        # establish and acknowledge ALL OFF before selecting a later pixel.
        self.checkpoint()
        if self.matrix_enabled:
            try:
                hw["switch_matrix"] = stage.ArduinoSwitchMatrix(
                    port=self._port(self.args.arduino_port), baudrate=self.args.arduino_baud,
                    verbose=False, verify_commands=True,
                )
                hw["switch_matrix"].turn_all_off()
            except Exception as exc:
                isolation_errors.append(exc)
        # Try all three isolation paths even if one device stays offline; a
        # failed FG reconnect must not leave a now-reachable SMU driving bias.
        # Only ALL OFF is sent to the matrix here, never a channel selection.
        if isolation_errors:
            raise RuntimeError(
                "Hardware isolation incomplete: " + "; ".join(str(exc) for exc in isolation_errors)
            ) from isolation_errors[0]

        self.auto.configure_funcgen_safe(min(self.voltages), funcgen=hw["funcgen"])
        self.checkpoint()
        hw["stage_detector"] = stage.ScopeDetector()
        hw["stage_detector"].connect()
        self._verify_scope()

        for key, port, address in (
            ("rot_hwp", pockels.PORT_HWP, pockels.ADDR_HWP),
            ("rot_qwp", pockels.PORT_QWP, pockels.ADDR_QWP),
            ("rot_anl", pockels.PORT_ANL, pockels.ADDR_ANL),
        ):
            self.checkpoint()
            rot = pockels.ElliptecRotator(
                port=self._port(port), address=address, verbose=False,
                settle_time=pockels.MOVE_SETTLE_S,
            )
            hw[key] = rot
            if not self.args.no_rotator_home:
                rot.home(direction=0, settle_s=3.0)
                # home() only sends a command. Require actual completion.
                for _ in range(30):
                    self.checkpoint()
                    status = rot.status
                    if status == "ok":
                        break
                    if status != "busy":
                        raise RuntimeError(f"{key} home failed: {status}")
                    self.wait(1.0)
                else:
                    raise RuntimeError(f"{key} home did not finish")
                raw = float(rot.get_raw_angle())
                if not math.isfinite(raw) or min(raw % 360, (-raw) % 360) > 0.5:
                    raise RuntimeError(f"{key} home readback is {raw!r} degrees")
            # Keep the run's angle coordinate system, including manual raw
            # calibration runs which explicitly disable homing/taring.
            rot._offset = self.rotator_offsets[key]
            rot.set_velocity(self.auto.ROTATOR_VELOCITY_PCT)
            self._verify_rotator(key)

        self.checkpoint()
        if self.simulated_lockin:
            hw["lockin"] = self.auto.FakeLockin(hw["rot_anl"], self.sim_state)
            hw["lockin"].connect()
        else:
            hw["lockin"] = pockels.DSP7230(
                resource_name=pockels.LOCKIN_RESOURCE, timeout_ms=pockels.LOCKIN_TIMEOUT_MS,
            )
            hw["lockin"].connect()
            hw["lockin"].apply_safe_startup(run_auto_measure=False)
            if not str(hw["lockin"].get_id() or "").strip():
                raise RuntimeError("Lock-in identity readback missing after reconnect")
        configuration = self.configure_lockin(
            hw["lockin"], self.args, simulated=self.simulated_lockin,
        )

        # Reopen the Kinesis polling sessions too after a possible shared-hub
        # reset. The following pixel performs its normal positioning/alignment.
        stage.DeviceManagerCLI.BuildDeviceList()
        for key, serial in (("device_x", stage.STAGE_SERIAL_X), ("device_y", stage.STAGE_SERIAL_Y)):
            self.checkpoint()
            hw[key] = stage.KCubeStepper.CreateKCubeStepper(serial)
            stage.initialize_device(serial, device=hw[key])
            if not self.args.skip_stage_home:
                hw[key].Home(60000)
            if not math.isfinite(stage.read_motor_pos(hw[key])):
                raise RuntimeError(f"{key} position readback missing after reconnect")

        # A late connection/configuration failure can invalidate an earlier
        # session; recheck the whole measurement path before declaring success.
        self.checkpoint()
        pockels._funcgen_set_sweep_output(
            hw["funcgen"], False, verify_command=True, required=True,
            context="transport recovery final verification",
        )
        self._verify_smu_off()
        if self.matrix_enabled:
            hw["switch_matrix"].turn_all_off()
        self._verify_scope()
        for key in self.rotator_offsets:
            self._verify_rotator(key)
        if not self.simulated_lockin:
            if not str(hw["lockin"].get_id() or "").strip():
                raise RuntimeError("Lock-in identity readback missing after recovery")
        return configuration

    def _verify_smu_off(self):
        smu = self.hardware["smu"]
        smu.output(False)
        if not self.simulated_smu:
            state = str(smu.query("OUTPut:STATe?")).strip().upper()
            if state not in ("0", "OFF"):
                raise RuntimeError(f"SMU output OFF could not be verified: {state!r}")

    def _verify_scope(self):
        voltage = self.hardware["stage_detector"].read_voltage()
        if voltage is None or not math.isfinite(float(voltage)):
            raise RuntimeError("Oscilloscope detector telemetry unavailable after reconnect")

    def _verify_rotator(self, key):
        angle = self.hardware[key].get_angle()
        if angle is None or not math.isfinite(float(angle)):
            raise RuntimeError(f"{key} angle readback missing after reconnect")

    def run(self):
        limit = int(getattr(self.args, "transport_recovery_attempts", 0))
        delay = float(getattr(self.args, "transport_recovery_delay", 5.0))
        attempt = 0
        while True:
            self.checkpoint()
            self.close()
            attempt += 1
            self.report("waiting", attempt=attempt, delay_s=delay)
            self.wait(delay)
            self.checkpoint()
            self.report("reconnecting", attempt=attempt)
            try:
                configuration = self.reconnect()
            except Exception as exc:
                self.report("attempt_failed", attempt=attempt,
                            error=f"{type(exc).__name__}: {exc}")
                self.close()
                if limit > 0 and attempt >= limit:
                    raise HardwareRecoveryExhausted(
                        f"Hardware still unavailable after {attempt} recovery attempts"
                    ) from exc
                delay = min(60.0, max(1.0, delay * 2.0))
            except BaseException:
                self.close()
                raise
            else:
                self.report("recovered", attempt=attempt)
                return configuration
