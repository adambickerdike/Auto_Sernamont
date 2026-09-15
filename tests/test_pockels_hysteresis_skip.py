import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import io
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


GUI_SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
GUI_SOURCE_TREE = ast.parse(GUI_SOURCE_PATH.read_text(encoding="utf-8"))
HYST_SOURCE_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
HYST_SOURCE_TREE = ast.parse(HYST_SOURCE_PATH.read_text(encoding="utf-8"))


def top_level_node(tree, name, node_type=ast.FunctionDef):
    for node in tree.body:
        if isinstance(node, node_type) and node.name == name:
            return node
    raise AssertionError(f"Missing production {node_type.__name__}: {name}")


def compile_nodes(path, nodes, namespace=None):
    namespace = dict(namespace or {})
    exec(
        compile(ast.Module(body=list(nodes), type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace


def load_skip_class():
    namespace = compile_nodes(
        GUI_SOURCE_PATH,
        [top_level_node(GUI_SOURCE_TREE, "SkipPixelRequested", ast.ClassDef)],
    )
    return namespace["SkipPixelRequested"]


class SkipSignalTests(unittest.TestCase):
    def test_skip_signal_is_not_caught_by_broad_exception_handlers(self):
        skip_type = load_skip_class()

        self.assertTrue(issubclass(skip_type, BaseException))
        self.assertFalse(issubclass(skip_type, Exception))


class TaggedSkipRequestTests(unittest.TestCase):
    def setUp(self):
        self.namespace = compile_nodes(
            GUI_SOURCE_PATH,
            [
                top_level_node(GUI_SOURCE_TREE, "gui_clear_skip_request"),
                top_level_node(GUI_SOURCE_TREE, "gui_skip_requested"),
            ],
            {"json": json},
        )

    def test_stale_tag_is_cleared_but_matching_tag_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            skip_path = Path(tmp_dir) / ".gui_skip_pixel_requested"
            self.namespace["GUI_SKIP_FILE"] = skip_path
            self.namespace["GUI_STATE"] = {"current_pixel": 12}

            skip_path.write_text(
                json.dumps({"current_pixel": 11}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(self.namespace["gui_skip_requested"]())
            self.assertFalse(skip_path.exists(), "stale request must be consumed")

            skip_path.write_text(
                json.dumps({"current_pixel": 12}),
                encoding="utf-8",
            )
            self.assertTrue(self.namespace["gui_skip_requested"]())
            self.assertTrue(
                skip_path.exists(),
                "matching request remains until gui_raise_skip_request consumes it",
            )


class HysteresisCancellationTests(unittest.TestCase):
    def test_cancel_signal_escapes_and_interrupted_sweep_safe_ramps_to_zero(self):
        skip_type = load_skip_class()
        nodes = [
            top_level_node(HYST_SOURCE_TREE, "validate_hysteresis_voltage_limit"),
            top_level_node(HYST_SOURCE_TREE, "_hysteresis_cancel_checkpoint"),
            top_level_node(HYST_SOURCE_TREE, "_hysteresis_interruptible_sleep"),
            top_level_node(HYST_SOURCE_TREE, "_sensitivity_fullscale_v"),
            top_level_node(HYST_SOURCE_TREE, "run_dc_hysteresis_sweep"),
        ]
        ramp_calls = []
        cancel_calls = []

        class FakeSMU:
            def __init__(self):
                self.levels = []

            def write(self, _command):
                return None

            def check_errors(self):
                return []

            def query(self, command):
                if command == "OUTPut:STATe?":
                    return "1"
                raise AssertionError(f"Unexpected SMU query before cancellation: {command}")

            def set_voltage(self, value):
                self.levels.append(float(value))

        smu = FakeSMU()

        def ramp_to(_smu, start_v, end_v):
            ramp_calls.append((float(start_v), float(end_v)))

        def cancel_at_first_point_wait():
            cancel_calls.append(len(cancel_calls) + 1)
            if len(cancel_calls) == 2:
                raise skip_type("operator skipped pixel")

        namespace = {
            "os": os,
            "math": math,
            "time": SimpleNamespace(time=lambda: 0.0, sleep=lambda _seconds: None),
            "DC_POLING_DWELL_S": 0.0,
            "DC_HYST_MIN_POLING_DWELL_S": 0.0,
            "LOCKIN_SETTLE_S": 0.0,
            "LOCKIN_AVG_READINGS": 1,
            "LOCKIN_READ_DELAY_S": 0.0,
            "DC_HYST_VMAX": 5.0,
            "DC_HYST_FINE_LIMIT": 5.0,
            "FUNCGEN_FREQ_HZ": 30_000.0,
            "DC_RAMP_PRESET_S": 0.0,
            "SMU_SLEW_RATE_V_PER_MS": 0.1,
            "SMU_RAMP_STEP_V": 100.0,
            "OVERLOAD_SETTLE_S": 0.0,
            "FUNCGEN_SETTLE_S": 0.0,
            "SENS_TABLE_VOLTS": [1.0],
            "_build_hysteresis_trajectory": (
                lambda *_args, **_kwargs: ([5.0], ["down"], [1])
            ),
            "describe_hysteresis_spacing": lambda *_args, **_kwargs: "test grid",
            "smu_ensure_output_on": lambda _smu: True,
            "smu_ramp_to": ramp_to,
        }
        production = compile_nodes(HYST_SOURCE_PATH, nodes, namespace)

        peak = {
            "theta_i": 10.0,
            "vpp": 9.0,
            "anl_angle": 20.0,
            "mag": 1e-6,
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(skip_type):
                    production["run_dc_hysteresis_sweep"](
                        smu,
                        object(),
                        object(),
                        0,
                        tmp_dir,
                        "skip regression",
                        peak,
                        hold_voltage_v=0.0,
                        poling_dwell_s=0.0,
                        start_from_zero=False,
                        cycles=1,
                        fine_windows=[],
                        run_analysis=False,
                        cancel_callback=cancel_at_first_point_wait,
                    )

        self.assertEqual(cancel_calls, [1, 2])
        self.assertEqual(
            ramp_calls,
            [(0.0, 5.0), (5.0, 0.0)],
            "an interrupted live sweep must unwind to zero before propagating skip",
        )

    def test_gui_and_adaptive_recon_forward_the_cancel_callback(self):
        gui_run = top_level_node(
            GUI_SOURCE_TREE,
            "run_post_fast_map_dc_hysteresis_sweep",
        )
        gui_calls = [
            call
            for call in ast.walk(gui_run)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "run_dc_hysteresis_sweep"
        ]
        self.assertEqual(len(gui_calls), 1)
        gui_keywords = {keyword.arg: keyword.value for keyword in gui_calls[0].keywords}
        self.assertEqual(
            ast.unparse(gui_keywords["cancel_callback"]),
            "gui_pixel_checkpoint",
        )

        core_run = top_level_node(HYST_SOURCE_TREE, "run_dc_hysteresis_sweep")
        recursive_calls = [
            call
            for call in ast.walk(core_run)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "run_dc_hysteresis_sweep"
        ]
        self.assertEqual(len(recursive_calls), 1)
        recursive_keywords = {
            keyword.arg: keyword.value for keyword in recursive_calls[0].keywords
        }
        self.assertEqual(
            ast.unparse(recursive_keywords["cancel_callback"]),
            "cancel_callback",
        )
        self.assertEqual(
            ast.unparse(recursive_keywords["vmax_v"]),
            "hyst_vmax",
        )


class FollowupSkipPolicyTests(unittest.TestCase):
    def test_operator_skipped_pixels_are_removed_from_every_pending_followup_set(self):
        main_node = top_level_node(GUI_SOURCE_TREE, "main")
        assignments = {}
        for node in ast.walk(main_node):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {
                    "pending_analyser_pixels",
                    "pending_ac_pixels",
                    "pending_dc_pixels",
                }:
                    assignments[target.id] = node.value

        self.assertEqual(
            set(assignments),
            {
                "pending_analyser_pixels",
                "pending_ac_pixels",
                "pending_dc_pixels",
            },
        )
        for name, expression in assignments.items():
            with self.subTest(name=name):
                referenced_names = {
                    node.id
                    for node in ast.walk(expression)
                    if isinstance(node, ast.Name)
                }
                self.assertIn("operator_skipped_pixels", referenced_names)
                self.assertTrue(
                    any(isinstance(node, ast.Sub) for node in ast.walk(expression)),
                    f"{name} must subtract operator-skipped pixels",
                )

        skip_record_calls = [
            call
            for call in ast.walk(main_node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "operator_skipped_pixels"
            and call.func.attr == "add"
        ]
        self.assertEqual(len(skip_record_calls), 1)


if __name__ == "__main__":
    unittest.main()
