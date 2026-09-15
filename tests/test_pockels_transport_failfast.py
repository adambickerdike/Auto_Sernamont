import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import io
import math
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest


GUI_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
GUI_SOURCE = GUI_PATH.read_text(encoding="utf-8")
GUI_TREE = ast.parse(GUI_SOURCE)
HYST_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
HYST_SOURCE = HYST_PATH.read_text(encoding="utf-8")
HYST_TREE = ast.parse(HYST_SOURCE)
CAMPAIGN_PATH = _bootstrap.module_path("pockels_campaign.py")
CAMPAIGN_SOURCE = CAMPAIGN_PATH.read_text(encoding="utf-8")
CAMPAIGN_TREE = ast.parse(CAMPAIGN_SOURCE)


def top_level_node(tree, name, node_type=ast.FunctionDef):
    for node in tree.body:
        if isinstance(node, node_type) and node.name == name:
            return node
    raise AssertionError(f"Missing production {node_type.__name__}: {name}")


def load_transport_policy():
    nodes = [
        top_level_node(
            GUI_TREE,
            "CriticalHardwareTransportError",
            ast.ClassDef,
        ),
        top_level_node(GUI_TREE, "is_fatal_hardware_transport_error"),
        top_level_node(GUI_TREE, "hardware_transport_stop_message"),
        top_level_node(GUI_TREE, "raise_if_fatal_hardware_transport_error"),
    ]
    namespace = {}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(GUI_PATH), "exec"),
        namespace,
    )
    return namespace


class HardwareTransportClassificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.production = load_transport_policy()

    def test_wrapped_visa_system_error_is_campaign_fatal(self):
        class VisaIOError(Exception):
            pass

        cause = VisaIOError(
            "VI_ERROR_SYSTEM_ERROR (-1073807360): Unknown system error"
        )
        wrapped = RuntimeError("funcgen CH1 OUTPUT OFF verification failed")
        wrapped.__cause__ = cause

        self.assertTrue(
            self.production["is_fatal_hardware_transport_error"](wrapped)
        )
        with self.assertRaises(
            self.production["CriticalHardwareTransportError"]
        ) as caught:
            self.production["raise_if_fatal_hardware_transport_error"](
                wrapped,
                "Pixel 006 DC hysteresis",
            )
        self.assertIn("automatic hardware recovery is required", str(caught.exception))
        self.assertIs(caught.exception.__cause__, wrapped)

    def test_windows_serial_access_denied_is_campaign_fatal(self):
        error = PermissionError(13, "Access is denied")
        self.assertTrue(
            self.production["is_fatal_hardware_transport_error"](error)
        )

    def test_scope_nan_marker_is_campaign_fatal(self):
        error = RuntimeError(
            "Oscilloscope detector telemetry unavailable at hysteresis point 33/45"
        )
        self.assertTrue(
            self.production["is_fatal_hardware_transport_error"](error)
        )

    def test_recoverable_motor_and_measurement_errors_are_not_transport_loss(self):
        classifier = self.production["is_fatal_hardware_transport_error"]
        self.assertFalse(classifier(ValueError("sensor error")))
        self.assertFalse(classifier(RuntimeError("lock-in output overload")))


class HardwareTransportWiringTests(unittest.TestCase):
    def test_fast_map_requires_verified_funcgen_transitions(self):
        run_node = top_level_node(GUI_TREE, "run_fast_map_for_pixel")
        calls = [
            node
            for node in ast.walk(run_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_funcgen_set_sweep_output"
        ]
        self.assertEqual(
            len(calls),
            4,
            "manual acquisition plus every per-HWP AC transition must be explicit",
        )
        for call in calls:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            self.assertIsInstance(keywords.get("verify_command"), ast.Constant)
            self.assertIs(keywords["verify_command"].value, True)
            self.assertIsInstance(keywords.get("required"), ast.Constant)
            self.assertIs(keywords["required"].value, True)
            self.assertIn("context", keywords)

    def test_fast_map_null_check_rejects_missing_scope_telemetry(self):
        namespace = load_transport_policy()
        namespace.update(
            {
                "np": SimpleNamespace(isfinite=math.isfinite),
                "gui_pixel_checkpoint": lambda: None,
                "prepare_null_scope_scale": lambda _detector: None,
                "_null_scope_range_is_settled": lambda *_args: True,
                "NULL_SCOPE_AUTORANGE_RETRIES": 1,
                "NULL_SCOPE_READ_AVERAGES": 1,
            }
        )
        exec(
            compile(
                ast.Module(
                    body=[top_level_node(GUI_TREE, "read_null_check")],
                    type_ignores=[],
                ),
                str(GUI_PATH),
                "exec",
            ),
            namespace,
        )

        class DeadScope:
            def read_averaged(self, **_kwargs):
                return None, None, None, 0.01

        with self.assertRaisesRegex(
            namespace["CriticalHardwareTransportError"],
            "telemetry unavailable",
        ):
            namespace["read_null_check"](object(), DeadScope())

    def test_nulling_backend_never_converts_scope_loss_to_zero_power(self):
        namespace = {
            "np": SimpleNamespace(isfinite=math.isfinite),
            "NULL_SCOPE_AVG": 2,
        }
        nodes = [
            top_level_node(CAMPAIGN_TREE, "scope_power_w"),
            top_level_node(CAMPAIGN_TREE, "_probe_read"),
        ]
        exec(
            compile(
                ast.Module(body=nodes, type_ignores=[]),
                str(CAMPAIGN_PATH),
                "exec",
            ),
            namespace,
        )

        class DeadScope:
            def read_averaged(self, **_kwargs):
                return None, None, None, 0.01

        for helper in ("scope_power_w", "_probe_read"):
            with self.subTest(helper=helper):
                with self.assertRaisesRegex(RuntimeError, "telemetry unavailable"):
                    namespace[helper](DeadScope())

    def test_fast_map_transport_failures_are_not_downgraded_to_null_flags(self):
        run_node = top_level_node(GUI_TREE, "run_fast_map_for_pixel")
        run_source = ast.get_source_segment(GUI_SOURCE, run_node)
        self.assertIsNotNone(run_source)
        self.assertGreaterEqual(
            run_source.count("raise_if_fatal_hardware_transport_error("),
            4,
        )
        unavailable_at = run_source.index(
            "oscilloscope detector telemetry unavailable after nulling"
        )
        classify_at = run_source.index("classify_null_certification(")
        self.assertLess(unavailable_at, classify_at)

    def test_adaptive_poling_stops_on_transport_loss(self):
        dwell_node = top_level_node(GUI_TREE, "adaptive_poling_dwell")
        output_calls = [
            node
            for node in ast.walk(dwell_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_funcgen_set_sweep_output"
        ]
        self.assertEqual(len(output_calls), 2)
        for call in output_calls:
            keywords = {keyword.arg: keyword.value for keyword in call.keywords}
            self.assertIs(keywords["verify_command"].value, True)
            self.assertIs(keywords["required"].value, True)
        dwell_source = ast.get_source_segment(GUI_SOURCE, dwell_node)
        self.assertIn("raise_if_fatal_hardware_transport_error(", dwell_source)

    def test_gui_scope_export_obeys_measurement_pause(self):
        namespace = {
            "time": SimpleNamespace(time=lambda: 123.0),
            "stage": SimpleNamespace(DARK_VOLTAGE_OFFSET=0.0, V_TO_W=1.0),
            "gui_update_state": lambda **_updates: None,
        }
        exec(
            compile(
                ast.Module(
                    body=[top_level_node(GUI_TREE, "gui_scope_live_read")],
                    type_ignores=[],
                ),
                str(GUI_PATH),
                "exec",
            ),
            namespace,
        )

        class Detector:
            def __init__(self):
                self._lock = threading.RLock()
                self.cur_vdiv = 0.01
                self.reads = 0

            def read_voltage(self, retries=0):
                self.reads += 1
                return 0.2

        detector = Detector()
        live_view = SimpleNamespace(_scope_paused=True)
        read_live = namespace["gui_scope_live_read"]

        self.assertFalse(read_live(detector, live_view=live_view))
        self.assertEqual(
            detector.reads,
            0,
            "GUI camera ticks must make no scope/VISA calls during measurement",
        )

        live_view._scope_paused = False
        self.assertTrue(read_live(detector, live_view=live_view))
        self.assertEqual(detector.reads, 1)

        for caller_name in ("gui_sleep", "install_gui_camera_export"):
            caller = top_level_node(GUI_TREE, caller_name)
            calls = [
                node
                for node in ast.walk(caller)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "gui_scope_live_read"
            ]
            self.assertEqual(len(calls), 1, caller_name)
            keywords = {keyword.arg: keyword.value for keyword in calls[0].keywords}
            self.assertEqual(
                ast.unparse(keywords["live_view"]),
                "live_view",
                f"{caller_name} must forward the pause owner",
            )

    def test_all_component_handlers_rethrow_transport_loss(self):
        main_node = top_level_node(GUI_TREE, "main")
        guard_calls = [
            node
            for node in ast.walk(main_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "raise_if_fatal_hardware_transport_error"
        ]
        self.assertEqual(
            len(guard_calls),
            6,
            "three inline and three follow-up-only component handlers must "
            "stop on dead instrument sessions",
        )

        main_source = ast.unparse(main_node)
        self.assertGreaterEqual(
            main_source.count("is_fatal_hardware_transport_error(exc)"),
            2,
        )
        self.assertEqual(main_source.count("recover_after_pixel_hardware_fault(pix_num, transport_exc)"), 2)
        self.assertNotIn("Restart the GUI and resume this run", main_source)
        self.assertIn("fast_map_sweep_complete(pixel_dir)", main_source)

    def test_rotator_recovery_does_not_hammer_an_access_denied_handle(self):
        move_node = top_level_node(GUI_TREE, "fast_rotator_move_or_fallback")
        guard_calls = [
            node
            for node in ast.walk(move_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "raise_if_fatal_hardware_transport_error"
        ]
        self.assertEqual(len(guard_calls), 3)

    def test_hysteresis_rejects_missing_scope_telemetry_before_lockin_read(self):
        run_node = top_level_node(HYST_TREE, "run_dc_hysteresis_sweep")
        run_source = ast.get_source_segment(HYST_SOURCE, run_node)
        self.assertIsNotNone(run_source)
        self.assertIn(
            "Oscilloscope detector telemetry unavailable",
            run_source,
        )
        self.assertIn("except Exception as scope_exc", run_source)
        self.assertNotIn(
            'p_dc, v_scope = float("nan"), float("nan")',
            run_source,
        )
        scope_check_at = run_source.index(
            "Oscilloscope detector telemetry unavailable"
        )
        lockin_read_at = run_source.index("read_lockin_averaged(", scope_check_at)
        self.assertLess(scope_check_at, lockin_read_at)

    def test_scope_loss_aborts_point_and_safe_ramps_smu_to_zero(self):
        nodes = [
            top_level_node(HYST_TREE, "validate_hysteresis_voltage_limit"),
            top_level_node(HYST_TREE, "_hysteresis_cancel_checkpoint"),
            top_level_node(HYST_TREE, "_hysteresis_interruptible_sleep"),
            top_level_node(HYST_TREE, "_sensitivity_fullscale_v"),
            top_level_node(HYST_TREE, "run_dc_hysteresis_sweep"),
        ]
        ramp_calls = []

        class FakeSMU:
            def __init__(self):
                self.level = 0.0

            def write(self, _command):
                return None

            def check_errors(self):
                return []

            def query(self, command):
                if command == "OUTPut:STATe?":
                    return "1"
                if command == "SOURce:VOLTage:FIXed:LEVel?":
                    return str(self.level)
                if command == "SYSTem:PROTection:CURRent:TRIPped?":
                    return "0"
                raise AssertionError(f"Unexpected SMU query: {command}")

            def set_voltage(self, value):
                self.level = float(value)

            def measure_primary(self):
                return 1e-3

            def measure_secondary(self):
                return self.level

        class DeadScopeDetector:
            def read_power_w_stable(self):
                return float("nan"), float("nan")

        def ramp_to(smu, start_v, end_v):
            ramp_calls.append((float(start_v), float(end_v)))
            smu.level = float(end_v)

        namespace = {
            "os": os,
            "math": math,
            "time": SimpleNamespace(time=lambda: 0.0, sleep=lambda _seconds: None),
            "DC_HYST_VMAX": 40.0,
            "DC_POLING_DWELL_S": 0.0,
            "DC_HYST_MIN_POLING_DWELL_S": 0.0,
            "LOCKIN_SETTLE_S": 0.0,
            "LOCKIN_AVG_READINGS": 1,
            "LOCKIN_READ_DELAY_S": 0.0,
            "DC_HYST_DEFAULT_GRID_PROFILE": "test",
            "DC_HYST_DYNAMIC_RANGE_NOISE_FLOOR_V": 1e-7,
            "FUNCGEN_FREQ_HZ": 30_000.0,
            "DC_RAMP_PRESET_S": 0.0,
            "SMU_RAMP_STEP_V": 100.0,
            "SMU_DC_READBACK_ATTEMPTS": 1,
            "SMU_DC_READBACK_RETRY_S": 0.0,
            "SMU_DC_READBACK_TOLERANCE_V": 0.1,
            "SENS_TABLE_VOLTS": [200e-6],
            "_build_hysteresis_trajectory": (
                lambda *_args, **_kwargs: ([5.0], ["down"], [1])
            ),
            "describe_hysteresis_spacing": (
                lambda *_args, **_kwargs: "test grid"
            ),
            "smu_ensure_output_on": lambda _smu: True,
            "smu_ramp_to": ramp_to,
        }
        exec(
            compile(ast.Module(body=nodes, type_ignores=[]), str(HYST_PATH), "exec"),
            namespace,
        )

        peak = {
            "theta_i": 10.0,
            "vpp": 4.0,
            "anl_angle": 20.0,
            "mag": 1e-6,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Oscilloscope detector telemetry unavailable",
                ):
                    namespace["run_dc_hysteresis_sweep"](
                        FakeSMU(),
                        DeadScopeDetector(),
                        object(),
                        0,
                        tmp_dir,
                        "scope-loss regression",
                        peak,
                        hold_voltage_v=0.0,
                        poling_dwell_s=0.0,
                        min_dwell_s=0.0,
                        run_analysis=False,
                    )

        self.assertEqual(
            ramp_calls,
            [(0.0, 5.0), (5.0, 0.0)],
            "a dead scope must abort before lock-in acquisition and unwind DC",
        )


if __name__ == "__main__":
    unittest.main()
