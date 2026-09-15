import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import json
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
MAPPING_PATH = _bootstrap.module_path("polarisation_lab_mapping_current.json")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def production_literal(name):
    for node in SOURCE_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing literal production constant: {name}")


def load_hwp_preset_production():
    names = {
        "_finite_float_or_none",
        "_wrap180",
        "_valid_peak_readout_override",
        "missing_peak_readout_hwp_angles",
        "can_reuse_resume_peak_calibration",
        "lab_theta_i_from_hwp",
        "raw_hwp_from_lab_theta_i",
        "hwp_grid_preset_settings",
        "angle_grid_from_start_stop_step",
        "hwp_sweep_mode_from_args",
        "hwp_center_span_from_args",
        "hwp_center_step_from_args",
        "hwp_input_grid_from_args",
        "hwp_angles_from_args",
    }
    nodes = [
        node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    missing = names - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")

    mapping_file = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    raw_reference = mapping_file["calibrated_lab_axis_reference"]["raw_setpoints_deg"]
    signs = mapping_file["raw_to_lab_formulas"]["signs"]
    namespace = {
        "math": math,
        "np": np,
        "wrap360": lambda value: float(value) % 360.0,
        "LAB_MAPPING_JSON": MAPPING_PATH.name,
        "LAB_ANGLE_MAPPING": {
            "H_y_raw": float(raw_reference["HWP_incident_y"]),
            "s_H": int(signs["s_H"]),
        },
        "HWP_CENTER_SWEEP_SPAN_DEG": production_literal("HWP_CENTER_SWEEP_SPAN_DEG"),
        "HWP_RAW_CENTER_SWEEP_SPAN_DEG": production_literal("HWP_RAW_CENTER_SWEEP_SPAN_DEG"),
        "DEFAULT_HWP_SWEEP_MODE": production_literal("DEFAULT_HWP_SWEEP_MODE"),
        "DEFAULT_HWP_CENTER_SWEEP_POINTS": production_literal("DEFAULT_HWP_CENTER_SWEEP_POINTS"),
        "DEFAULT_HWP_GRID_CENTER_RAW_DEG": production_literal("DEFAULT_HWP_GRID_CENTER_RAW_DEG"),
        "HWP_GRID_PRESET_CURRENT": production_literal("HWP_GRID_PRESET_CURRENT"),
        "HWP_GRID_PRESET_20260515_INTERPOLATED_9": production_literal(
            "HWP_GRID_PRESET_20260515_INTERPOLATED_9"
        ),
        "HWP_GRID_PRESET_20260515": production_literal("HWP_GRID_PRESET_20260515"),
        "HWP_COMPARISON_20260515_RAW_START_DEG": production_literal(
            "HWP_COMPARISON_20260515_RAW_START_DEG"
        ),
        "HWP_COMPARISON_20260515_RAW_STOP_DEG": production_literal(
            "HWP_COMPARISON_20260515_RAW_STOP_DEG"
        ),
        "HWP_COMPARISON_20260515_RAW_STEP_DEG": production_literal(
            "HWP_COMPARISON_20260515_RAW_STEP_DEG"
        ),
        "HWP_COMPARISON_20260515_INTERPOLATED_POINTS": production_literal(
            "HWP_COMPARISON_20260515_INTERPOLATED_POINTS"
        ),
    }
    namespace["DEFAULT_HWP_CENTER_THETA_STEP_DEG"] = (
        namespace["HWP_CENTER_SWEEP_SPAN_DEG"]
        / (namespace["DEFAULT_HWP_CENTER_SWEEP_POINTS"] - 1)
    )
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = load_hwp_preset_production()


class HwpGridPresetTests(unittest.TestCase):
    def test_15_may_interpolated_preset_spans_same_endpoints_with_nine_points(self):
        settings = PRODUCTION["hwp_grid_preset_settings"](
            PRODUCTION["HWP_GRID_PRESET_20260515_INTERPOLATED_9"]
        )
        args = SimpleNamespace(**settings)
        lab_angles = PRODUCTION["hwp_input_grid_from_args"](args)
        raw_angles = PRODUCTION["hwp_angles_from_args"](args)

        expected_raw = np.linspace(0.0, 82.5, 9)
        self.assertEqual(settings["hwp_input_frame"], "lab")
        self.assertEqual(settings["hwp_sweep_mode"], "start-stop")
        self.assertEqual(settings["hwp_points"], 9)
        self.assertAlmostEqual(settings["hwp_start"], 66.0786, places=6)
        self.assertAlmostEqual(settings["hwp_stop"], 231.0786, places=6)
        self.assertAlmostEqual(settings["hwp_step"], 20.625, places=6)
        self.assertTrue(np.allclose(raw_angles, expected_raw, rtol=0.0, atol=1e-6))
        self.assertAlmostEqual(raw_angles[5], 51.5625, places=6)
        self.assertAlmostEqual(lab_angles[5], 169.2036, places=6)

    def test_15_may_preset_uses_current_lab_frame_but_exact_old_raw_positions(self):
        settings = PRODUCTION["hwp_grid_preset_settings"](
            PRODUCTION["HWP_GRID_PRESET_20260515"]
        )
        args = SimpleNamespace(**settings)
        lab_angles = PRODUCTION["hwp_input_grid_from_args"](args)
        raw_angles = PRODUCTION["hwp_angles_from_args"](args)

        expected_raw = np.arange(0.0, 82.5 + 3.75, 7.5)
        self.assertEqual(settings["hwp_input_frame"], "lab")
        self.assertEqual(settings["hwp_sweep_mode"], "start-stop")
        self.assertEqual(settings["hwp_points"], 12)
        self.assertAlmostEqual(settings["hwp_start"], 66.0786, places=6)
        self.assertAlmostEqual(settings["hwp_stop"], 231.0786, places=6)
        self.assertAlmostEqual(settings["hwp_step"], 15.0, places=6)
        self.assertAlmostEqual(lab_angles[7], 171.0786, places=6)
        self.assertTrue(np.allclose(raw_angles, expected_raw, rtol=0.0, atol=1e-6))
        self.assertAlmostEqual(raw_angles[7], 52.5, places=6)

    def test_current_preset_restores_the_calibrated_nine_point_default(self):
        settings = PRODUCTION["hwp_grid_preset_settings"](
            PRODUCTION["HWP_GRID_PRESET_CURRENT"]
        )
        raw_angles = PRODUCTION["hwp_angles_from_args"](SimpleNamespace(**settings))

        self.assertEqual(settings["hwp_input_frame"], "lab")
        self.assertEqual(settings["hwp_sweep_mode"], "centered-180")
        self.assertEqual(settings["hwp_points"], 9)
        self.assertEqual(len(raw_angles), 9)
        self.assertAlmostEqual(raw_angles[4], 7.8951, places=6)

    def test_complete_resume_peak_table_skips_a_second_calibration_pixel(self):
        raw_angles = np.linspace(0.0, 82.5, 9)
        seeds = {
            f"{float(hwp):.3f}": {
                "readout_override": {
                    "qwp_readout_deg": 10.0 + index,
                    "anl_target_deg": 100.0 + index,
                }
            }
            for index, hwp in enumerate(raw_angles)
        }

        can_reuse = PRODUCTION["can_reuse_resume_peak_calibration"](
            resume_state_loaded=True,
            peak_readout_required=True,
            hwp_angles=raw_angles,
            null_seed_by_hwp=seeds,
        )
        self.assertTrue(can_reuse)

        seeds.pop(f"{float(raw_angles[4]):.3f}")
        can_reuse_missing = PRODUCTION["can_reuse_resume_peak_calibration"](
            resume_state_loaded=True,
            peak_readout_required=True,
            hwp_angles=raw_angles,
            null_seed_by_hwp=seeds,
        )
        self.assertFalse(can_reuse_missing)


if __name__ == "__main__":
    unittest.main()
