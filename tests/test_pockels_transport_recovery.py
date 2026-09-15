import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import copy
from datetime import datetime
import io
from pathlib import Path
from queue import Queue
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import serial

from arduino_switch_matrix import ArduinoSwitchMatrix
from pockels_transport_recovery import (
    FastMapHardwareRecovery,
    HardwareRecoveryExhausted,
    recovery_serial_port,
    serial_port_identities,
)
from test_pockels_transport_failfast import GUI_TREE, load_transport_policy, top_level_node


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.objects = []
        self.scope_readings = [0.5]
        self.connect_failures = 0
        self.args = SimpleNamespace(
            smu_port="COM11", arduino_port="COM12", arduino_baud=9600,
            no_rotator_home=False, skip_stage_home=False,
            transport_recovery_attempts=3, transport_recovery_delay=5.0,
        )

        class SimulatedSMU:
            pass

        def device(key, **kwargs):
            obj = Mock(name=key)
            obj._offset = 12
            obj.status = "ok"
            obj.get_raw_angle.return_value = 0.0
            obj.get_angle.return_value = 2.0
            obj.get_id.return_value = "DSP7230"
            obj.query.return_value = "OFF"
            obj.output.side_effect = lambda on: self.events.append((key, "output", on))
            obj.turn_all_off.side_effect = lambda: self.events.append((key, "all_off"))
            obj.close.side_effect = lambda: self.events.append((key, "close"))
            obj.connect.side_effect = lambda: self.events.append((key, "connect"))
            obj.Home.side_effect = lambda _timeout: self.events.append((key, "home"))
            obj.home.side_effect = lambda **_kwargs: self.events.append((key, "home"))
            obj.StopPolling.side_effect = lambda: self.events.append((key, "stop_polling"))
            obj.Disconnect.side_effect = lambda: self.events.append((key, "disconnect"))
            if key == "scope":
                obj.read_voltage.side_effect = lambda: self.scope_readings.pop(0) if len(self.scope_readings) > 1 else self.scope_readings[0]
            self.objects.append((key, obj))
            self.events.append((key, "allocated"))
            return obj

        self.device = device

        def connect_fg(**kwargs):
            if self.connect_failures:
                self.connect_failures -= 1
                raise PermissionError(13, "Access is denied")
            return device("fg")

        self.pockels = SimpleNamespace(
            connect_funcgen=Mock(side_effect=connect_fg),
            _funcgen_set_sweep_output=Mock(side_effect=lambda _fg, on, **kw: self.events.append(("fg", "off", kw.get("required", False)))),
            SMU4201=Mock(side_effect=lambda **kw: device("smu", **kw)),
            _connect_smu=Mock(side_effect=lambda *a, **kw: self.events.append(("smu", "setup", kw["enable_output"]))),
            ElliptecRotator=Mock(side_effect=lambda **kw: device(kw["port"])),
            DSP7230=Mock(side_effect=lambda **kw: device("lockin")),
            SMU_BAUD=9600, PORT_HWP="COM4", ADDR_HWP=0,
            PORT_QWP="COM5", ADDR_QWP=0, PORT_ANL="COM6", ADDR_ANL=0,
            MOVE_SETTLE_S=0.1, LOCKIN_RESOURCE="TCPIP::lockin", LOCKIN_TIMEOUT_MS=2000,
        )
        self.stage = SimpleNamespace(
            ArduinoSwitchMatrix=Mock(side_effect=lambda **kw: device("matrix")),
            ScopeDetector=Mock(side_effect=lambda: device("scope")),
            DeviceManagerCLI=SimpleNamespace(BuildDeviceList=Mock()),
            KCubeStepper=SimpleNamespace(CreateKCubeStepper=Mock(side_effect=lambda serial: device(serial))),
            initialize_device=Mock(), read_motor_pos=Mock(return_value=12.5),
            STAGE_SERIAL_X="X", STAGE_SERIAL_Y="Y",
        )
        self.auto = SimpleNamespace(
            FakeSMU=SimulatedSMU, FakeLockin=Mock(), ROTATOR_VELOCITY_PCT=50,
            configure_funcgen_safe=Mock(),
        )
        self.hardware = {
            key: device("old_" + key)
            for key in ("device_x", "device_y", "stage_detector", "switch_matrix",
                        "smu", "lockin", "funcgen", "rot_hwp", "rot_qwp", "rot_anl")
        }
        self.old_objects = list(self.objects)
        self.report = Mock()
        self.wait = Mock()
        self.checkpoint = Mock()
        self.configure_lockin = Mock(return_value={"sensitivity_index_effective": 16})
        self.recovery = FastMapHardwareRecovery(
            self.hardware, args=self.args, auto=self.auto, pockels=self.pockels,
            stage=self.stage, voltages=[9.0], configure_lockin=self.configure_lockin,
            simulated_lockin=False, sim_state={}, wait=self.wait,
            checkpoint=self.checkpoint, report=self.report,
        )

    def test_shared_disconnect_reopens_every_session_and_checks_outputs_before_motion(self):
        configuration = self.recovery.run()
        self.assertEqual(configuration["sensitivity_index_effective"], 16)
        first_open = self.events.index(("fg", "allocated"))
        for key, obj in self.old_objects:
            if key.endswith(("device_x", "device_y")):
                obj.Disconnect.assert_called_once()
            else:
                obj.close.assert_called_once()
                self.assertLess(self.events.index((key, "close")), first_open)
        matrix_open = self.events.index(("matrix", "allocated"))
        self.assertLess(self.events.index(("fg", "off", True)), matrix_open)
        self.assertLess(self.events.index(("smu", "setup", False)), matrix_open)
        self.assertLess(self.events.index(("matrix", "all_off")), self.events.index(("COM4", "home")))
        self.stage.ArduinoSwitchMatrix.assert_called_once_with(
            port="COM12", baudrate=9600, verbose=False, verify_commands=True,
        )
        self.pockels._connect_smu.assert_called_once_with(0.0, smu=self.hardware["smu"], enable_output=False)
        self.configure_lockin.assert_called_once_with(self.hardware["lockin"], self.args, simulated=False)
        for key in ("rot_hwp", "rot_qwp", "rot_anl"):
            self.assertEqual(self.hardware[key]._offset, 12)
        self.assertEqual(self.report.call_args.args, ("recovered",))

    def test_access_denied_twice_then_recovery_succeeds(self):
        self.connect_failures = 2
        self.recovery.run()
        self.assertEqual([call.args[0] for call in self.wait.call_args_list], [5.0, 10.0, 20.0])
        self.assertEqual(self.pockels.connect_funcgen.call_count, 3)
        self.assertEqual(self.report.call_args.kwargs["attempt"], 3)

    def test_default_unlimited_wait_survives_more_than_three_failures(self):
        self.args.transport_recovery_attempts = 0
        self.connect_failures = 7
        self.recovery.run()
        self.assertEqual(self.pockels.connect_funcgen.call_count, 8)
        self.assertEqual([c.args[0] for c in self.wait.call_args_list], [5, 10, 20, 40, 60, 60, 60, 60])

    def test_missing_scope_telemetry_closes_partial_bundle_and_retries(self):
        self.scope_readings = [None, 0.5]
        self.recovery.run()
        scopes = [obj for key, obj in self.objects if key == "scope"]
        self.assertEqual(len(scopes), 2)
        scopes[0].close.assert_called_once()
        scopes[1].close.assert_not_called()
        self.assertEqual(self.report.call_args.kwargs["attempt"], 2)

    def test_final_readback_failure_retries_instead_of_reporting_success(self):
        self.scope_readings = [0.5, None, 0.5]
        self.recovery.run()
        self.assertEqual(self.pockels.connect_funcgen.call_count, 2)
        self.assertEqual(sum(c.args[0] == "recovered" for c in self.report.call_args_list), 1)

    def test_limit_exhaustion_does_not_provide_dead_hardware_to_next_pixel(self):
        self.connect_failures = 10
        with self.assertRaises(HardwareRecoveryExhausted):
            self.recovery.run()
        self.assertTrue(all(obj is None for obj in self.hardware.values()))
        self.assertFalse(any(c.args[0] == "recovered" for c in self.report.call_args_list))
        self.auto.FakeLockin.assert_not_called()

    def test_stop_during_backoff_propagates_without_opening(self):
        self.wait.side_effect = KeyboardInterrupt("Stop requested")
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.run()
        self.pockels.connect_funcgen.assert_not_called()
        self.assertTrue(all(obj is None for obj in self.hardware.values()))

    def test_stop_during_partial_reconnect_closes_new_sessions(self):
        self.stage.ScopeDetector.side_effect = KeyboardInterrupt("Stop requested")
        with self.assertRaises(KeyboardInterrupt):
            self.recovery.run()
        self.assertTrue(all(obj is None for obj in self.hardware.values()))
        [obj for key, obj in self.objects if key == "fg"][0].close.assert_called_once()

    def test_manual_raw_angle_and_no_home_preferences_are_preserved(self):
        self.args.no_rotator_home = True
        self.args.skip_stage_home = True
        self.recovery.run()
        for key in ("rot_hwp", "rot_qwp", "rot_anl"):
            self.hardware[key].home.assert_not_called()
            self.assertEqual(self.hardware[key]._offset, 12)
        self.hardware["device_x"].Home.assert_not_called()

    def test_failed_off_verification_prevents_routing_and_movement(self):
        self.pockels._funcgen_set_sweep_output.side_effect = RuntimeError("USB disconnected")
        with self.assertRaises(HardwareRecoveryExhausted):
            self.recovery.run()
        self.pockels._connect_smu.assert_called()
        for key, matrix in self.objects:
            if key == "matrix":
                matrix.turn_all_off.assert_called()
                matrix.switch_to_channel.assert_not_called()
        self.pockels.ElliptecRotator.assert_not_called()

    def test_disconnect_still_called_if_stop_polling_fails(self):
        old_x = self.hardware["device_x"]
        old_x.StopPolling.side_effect = RuntimeError("device disconnected")
        self.recovery.run()
        old_x.Disconnect.assert_called_once()

    def test_function_generator_follows_renumbered_visa_serial_port(self):
        old = SimpleNamespace(device="COM3", serial_number="FG123", location="1-3", vid=1, pid=2)
        new = SimpleNamespace(device="COM20", serial_number="FG123", location="1-3", vid=1, pid=2)
        self.recovery.funcgen_resource = "ASRL3::INSTR"
        self.recovery.port_identities = serial_port_identities([old])
        self.recovery.list_ports = lambda: [new]
        self.recovery.run()
        self.pockels.connect_funcgen.assert_called_once_with(resource_name="ASRL20::INSTR")

    def test_an_unresponsive_rotator_prevents_success(self):
        self.args.transport_recovery_attempts = 1
        self.pockels.ElliptecRotator.side_effect = lambda **kw: Mock(
            status="ok", get_raw_angle=Mock(return_value=0.0),
            get_angle=Mock(return_value=None),
        )
        with self.assertRaises(HardwareRecoveryExhausted):
            self.recovery.run()
        self.assertFalse(any(c.args[0] == "recovered" for c in self.report.call_args_list))
        self.assertTrue(all(obj is None for obj in self.hardware.values()))

    def test_existing_fake_lockin_is_rebound_to_the_new_analyser(self):
        self.recovery.simulated_lockin = True
        self.recovery.run()
        self.auto.FakeLockin.assert_called_once_with(self.hardware["rot_anl"], self.recovery.sim_state)
        self.pockels.DSP7230.assert_not_called()
        self.configure_lockin.assert_called_once_with(self.hardware["lockin"], self.args, simulated=True)


class SerialIdentityTests(unittest.TestCase):
    def port(self, device, serial_number="ABC", location="1-2"):
        return SimpleNamespace(device=device, serial_number=serial_number, location=location, vid=1, pid=2)

    def test_renumbered_port_is_found_by_identity(self):
        identities = serial_port_identities([self.port("COM12")])
        self.assertEqual(recovery_serial_port("COM12", identities, [self.port("COM18")]), "COM18")

    def test_reused_com_number_does_not_connect_to_another_device(self):
        identities = serial_port_identities([self.port("COM12")])
        with self.assertRaisesRegex(RuntimeError, "absent or ambiguous"):
            recovery_serial_port("COM12", identities, [self.port("COM12", serial_number="OTHER")])

    def test_duplicate_adapter_serials_resolve_by_location(self):
        identities = serial_port_identities([self.port("COM12")])
        self.assertEqual(recovery_serial_port("COM12", identities, [self.port("COM18", location="1-3"), self.port("COM19")]), "COM19")


class ArduinoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.matrix = ArduinoSwitchMatrix.__new__(ArduinoSwitchMatrix)
        self.matrix._ser = Mock(is_open=True)
        self.matrix._reader_error = None
        self.matrix._lock = threading.Lock()
        self.matrix._rx_queue = Queue()
        self.matrix.verify_commands = True
        self.matrix.active_channel = 0
        self.matrix.active_pin = 0
        self.matrix.port = "COM12"

    def test_dead_reader_fails_before_another_write(self):
        self.matrix._reader_error = PermissionError(13, "Access is denied")
        with self.assertRaisesRegex(serial.SerialException, "reader failed"):
            self.matrix.turn_all_off()
        self.matrix._ser.write.assert_not_called()

    def test_all_off_requires_firmware_ack_and_does_not_flush(self):
        def write(data):
            self.matrix._rx_queue.put("-> ALL channels have been turned OFF.")
            return len(data)
        self.matrix._ser.write.side_effect = write
        self.matrix.turn_all_off()
        self.matrix._ser.flush.assert_not_called()
        self.assertEqual(self.matrix.active_channel, 0)

    def test_toggle_write_failure_is_never_replayed(self):
        self.matrix._ser.write.side_effect = serial.SerialException("WriteFile failed (Access is denied)")
        with self.assertRaises(serial.SerialException):
            self.matrix.switch_to_channel(62)
        self.matrix._ser.write.assert_called_once_with(b"E62\n")
        self.assertEqual(self.matrix.active_channel, 0)

    def test_no_ack_keeps_channel_state_unknown_and_raises(self):
        self.matrix._ser.write.side_effect = lambda data: len(data)
        with patch("arduino_switch_matrix.time.monotonic", side_effect=range(0, 100, 3)):
            with self.assertRaisesRegex(serial.SerialException, "acknowledgement timeout"):
                self.matrix.switch_to_channel(62)
        self.assertEqual(self.matrix.active_channel, 0)

    def test_raw_channel_ack_and_toggle_off_ack(self):
        for reply, expected_state in (("-> Electrical Switch 62", 62), ("-> Matrix Disabled (All channels OFF).", 0)):
            def write(data):
                self.matrix._rx_queue.put(reply)
                return len(data)
            self.matrix._ser.write.side_effect = write
            self.matrix.switch_to_channel(62)
            self.assertEqual(self.matrix.active_channel, expected_state)

    def test_closed_port_is_classified_for_campaign_recovery(self):
        self.matrix._ser.is_open = False
        with self.assertRaises(serial.SerialException) as caught:
            self.matrix.turn_all_off()
        self.assertTrue(load_transport_policy()["is_fatal_hardware_transport_error"](caught.exception))

    def test_serial_write_timeout_is_classified_for_campaign_recovery(self):
        classifier = load_transport_policy()["is_fatal_hardware_transport_error"]
        self.assertTrue(classifier(serial.SerialTimeoutException("Write timeout")))


def load_function(path, name, namespace):
    tree = ast.parse(_bootstrap.module_path(path).read_text(encoding="utf-8"))
    node = top_level_node(tree, name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace[name]


class ShutdownRecoveryTests(unittest.TestCase):
    def test_both_output_shutdowns_are_attempted_and_loss_is_raised(self):
        ns = load_transport_policy()
        smu = Mock()
        smu.query.return_value = "OFF"
        ns.update(sys=sys, gui_update_state=Mock(), auto=SimpleNamespace(smu_output_off=Mock()),
                  pockels=SimpleNamespace(_funcgen_set_sweep_output=Mock(side_effect=serial.SerialException("WriteFile failed"))))
        shutdown = load_function("pockels_fast_map_gui.py", "ensure_fast_map_outputs_off", ns)
        with self.assertRaises(ns["CriticalHardwareTransportError"]):
            shutdown(object(), smu, "end of pixel")
        ns["auto"].smu_output_off.assert_called_once_with(smu, "end of pixel")
        smu.query.assert_called_once_with("OUTPut:STATe?")

    def test_smu_still_on_readback_prevents_continuation(self):
        ns = load_transport_policy()
        smu = Mock()
        smu.query.return_value = "ON"
        ns.update(sys=sys, gui_update_state=Mock(), auto=SimpleNamespace(smu_output_off=Mock()),
                  pockels=SimpleNamespace(_funcgen_set_sweep_output=Mock()))
        shutdown = load_function("pockels_fast_map_gui.py", "ensure_fast_map_outputs_off", ns)
        with self.assertRaisesRegex(ns["CriticalHardwareTransportError"], "SMU output OFF readback"):
            shutdown(object(), smu, "end of pixel")

    def test_keyboard_stop_is_not_replaced_by_cleanup_failure(self):
        ns = load_transport_policy()
        ns.update(sys=sys, gui_update_state=Mock(), auto=SimpleNamespace(smu_output_off=Mock()),
                  pockels=SimpleNamespace(_funcgen_set_sweep_output=Mock(side_effect=serial.SerialException("USB gone"))))
        shutdown = load_function("pockels_fast_map_gui.py", "ensure_fast_map_outputs_off", ns)
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                try:
                    raise KeyboardInterrupt("Stop")
                finally:
                    shutdown(object(), Mock(), "end of pixel")

    def test_route_cleanup_failure_is_forwarded_without_replaying_channel(self):
        routed = load_function("pockels_full_automation.py", "routed_arduino_channel", {
            "contextmanager": contextlib.contextmanager, "sys": sys,
            "time": SimpleNamespace(sleep=lambda _: None),
        })
        matrix = Mock()
        matrix.turn_all_off.side_effect = [None, serial.SerialException("Access is denied")]
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(serial.SerialException):
                with routed(matrix, 62, 0, require_off=True):
                    pass
        matrix.switch_to_channel.assert_called_once_with(62)

    def test_recovery_smu_setup_never_enables_output(self):
        smu = Mock()
        smu.idn.return_value = "SMU4201"
        smu.check_errors.return_value = []
        smu.query.return_value = "OFF"
        connect = load_function("Pockels_Calibration_2026.py", "_connect_smu", {
            "SMU_PORT": "COM11", "SMU_COMPLIANCE_A": 0.001,
            "SMU_NPLC": 1.0, "SMU_SLEW_RATE_V_PER_MS": 1.0,
            "_smu_set_hv": lambda *_: "ON",
        })
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(connect(0.0, smu=smu, enable_output=False), smu)
        self.assertTrue(smu.output.call_args_list)
        self.assertTrue(all(call.args == (False,) for call in smu.output.call_args_list))
        smu.set_voltage.assert_called_once_with(0.0)


class CampaignContinuationTests(unittest.TestCase):
    def test_real_pixel_handlers_save_failure_recover_and_measure_next_pixel(self):
        self._exercise_handlers("transport")

    def test_real_pixel_handlers_recover_angle_failure_and_measure_next_pixel(self):
        self._exercise_handlers("angle")

    def _exercise_handlers(self, failure_kind):
        main = top_level_node(GUI_TREE, "main")
        # Execute each actual campaign exception handler in a two-pixel queue.
        # This exercises the control flow, not just the presence of a call.
        handlers = [node for node in ast.walk(main) if isinstance(node, ast.ExceptHandler)
                    and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                            and call.func.id == "recover_after_pixel_hardware_fault"
                            for call in ast.walk(node))]
        self.assertEqual(len(handlers), 2)
        for handler in handlers:
            for saved_map in (False, True):
                with self.subTest(handler=handler.lineno, saved_map=saved_map):
                    ns = load_transport_policy()
                    events = []
                    records = []
                    pixels = []

                    def measure(pix):
                        if pix == 85:
                            if failure_kind == "angle":
                                raise ns["CriticalAngleError"](
                                    "QWP adaptive null angle verification failed "
                                    "(target=136.65 deg, actual=139.42 deg, err=2.774 deg)"
                                )
                            raise serial.SerialException("WriteFile failed (PermissionError(13, 'Access is denied.'))")
                        events.append(("measure", pix))

                    def recover(pix, exc):
                        events.append(("recover", pix))
                        self.assertIn(ns["pixel"]["measurement_status"], ("failed", "partial"))

                    ns.update(
                        datetime=datetime, traceback=SimpleNamespace(print_exc=lambda: None),
                        fast_map_sweep_complete=lambda _: saved_map,
                        pockels=SimpleNamespace(_funcgen_set_sweep_output=lambda *a: None),
                        auto=SimpleNamespace(smu_output_off=lambda *a: None),
                        gui_update_state=lambda **kw: None, smu=None, funcgen=None,
                        switch_matrix=None, pixel_dir=None,
                        CriticalAngleError=type("CriticalAngleError", (RuntimeError,), {}),
                        CriticalNullError=type("CriticalNullError", (RuntimeError,), {}),
                        recover_after_pixel_hardware_fault=recover, measure=measure,
                        records=records, pixels=pixels,
                    )
                    tree = ast.parse("for pix_num in (85, 86):\n    pixel = {}\n    record = {}\n    pixels.append(pixel)\n    records.append(record)\n    try:\n        measure(pix_num)\n    except Exception:\n        pass\n")
                    tree.body[0].body[-1].handlers = [copy.deepcopy(handler)]
                    with contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(ast.fix_missing_locations(tree), "campaign_recovery_test", "exec"), ns)
                    self.assertEqual(events, [("recover", 85), ("measure", 86)])
                    self.assertIn(
                        "angle verification failed" if failure_kind == "angle" else "WriteFile failed",
                        pixels[0]["measurement_error"],
                    )
                    if saved_map:
                        self.assertEqual(pixels[0]["measurement_status"], "partial")


if __name__ == "__main__":
    unittest.main()
