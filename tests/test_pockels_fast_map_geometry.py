import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import json
import math
from pathlib import Path
import unittest

import numpy as np


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
MAPPING_PATH = _bootstrap.module_path("polarisation_lab_mapping_current.json")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def load_production_constant(name):
    for node in SOURCE_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value_node = node.value
            try:
                return ast.literal_eval(value_node)
            except (TypeError, ValueError) as exc:
                raise AssertionError(f"Production constant {name} is not literal") from exc
    raise AssertionError(f"Missing production constant: {name}")


def load_production_functions(*names):
    wanted = set(names)
    nodes = [
        node
        for node in SOURCE_TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    missing = wanted - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")
    namespace = {
        "np": np,
        "wrap360": lambda value: float(value) % 360.0,
        "safe_wrap_move_cost_deg": (
            lambda start, target: abs((float(target) - float(start) + 180.0) % 360.0 - 180.0)
        ),
    }
    code = compile(
        ast.Module(body=nodes, type_ignores=[]),
        str(SOURCE_PATH),
        "exec",
    )
    exec(code, namespace)
    return namespace


PRODUCTION = load_production_functions(
    "calibration_pixel_requires_full_null",
    "shortest_delta_deg",
    "periodic_delta_deg",
    "closest_periodic_equivalent_deg",
    "ordered_slope_targets",
    "learned_readout_triplet_targets",
    "analyser_offset_from_null_deg",
)


class CalibrationNullPolicyTests(unittest.TestCase):
    def test_auto_calibration_pixel_always_requires_full_null(self):
        self.assertTrue(
            PRODUCTION["calibration_pixel_requires_full_null"](
                auto_peak_calibration=True,
                manual_peak_raw=None,
                manual_peak_only=False,
            )
        )

    def test_normal_pixel_keeps_requested_adaptive_policy(self):
        self.assertFalse(
            PRODUCTION["calibration_pixel_requires_full_null"](
                auto_peak_calibration=False,
                manual_peak_raw=None,
                manual_peak_only=False,
            )
        )

    def test_manual_peak_only_diagnostic_does_not_force_map_null(self):
        self.assertFalse(
            PRODUCTION["calibration_pixel_requires_full_null"](
                auto_peak_calibration=False,
                manual_peak_raw={"hwp_deg": 1.0, "qwp_deg": 2.0, "anl_deg": 3.0},
                manual_peak_only=True,
            )
        )


class OpticalGeometryTests(unittest.TestCase):
    def test_analyser_targets_are_exactly_plus_and_minus_45_from_null(self):
        targets = PRODUCTION["ordered_slope_targets"](112.975, 112.975)
        offsets = sorted(
            round(PRODUCTION["analyser_offset_from_null_deg"](112.975, target), 9)
            for _side, target in targets
        )
        self.assertEqual(offsets, [-45.0, 45.0])

    def test_learned_peak_only_selects_order_not_off_quadrature_angle(self):
        targets = PRODUCTION["learned_readout_triplet_targets"](
            27.4,  # old raw peak is -72.6 deg from the current local null
            100.0,
            100.0,
        )
        self.assertEqual([side for side, _target in targets], [
            "learned_zero",
            "learned_anchor",
            "learned_opposite",
        ])
        offsets = [
            PRODUCTION["analyser_offset_from_null_deg"](100.0, target)
            for _side, target in targets
        ]
        self.assertEqual(offsets, [0.0, -45.0, 45.0])

    def test_hwp_y_mark_gives_lab_y_incident_polarisation(self):
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        hwp_y_raw = float(mapping["calibrated_lab_axis_reference"]["raw_setpoints_deg"]["HWP_incident_y"])
        mapping_data = {
            "H_y_raw": hwp_y_raw,
            "s_H": float(mapping["raw_to_lab_formulas"]["signs"]["s_H"]),
        }
        theta_i = (
            90.0
            + 2.0 * mapping_data["s_H"] * (hwp_y_raw - mapping_data["H_y_raw"])
        ) % 180.0
        self.assertAlmostEqual(
            theta_i,
            90.0,
            places=6,
        )

    def test_half_wave_plate_45_degree_axis_shift_rotates_y_to_x(self):
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        raw_points = mapping["calibrated_lab_axis_reference"]["raw_setpoints_deg"]
        hwp_y_raw = float(raw_points["HWP_incident_y"])
        hwp_raw_for_x = float(raw_points["HWP_incident_x"])
        sign = float(mapping["raw_to_lab_formulas"]["signs"]["s_H"])
        theta_i = (90.0 + 2.0 * sign * (hwp_raw_for_x - hwp_y_raw)) % 180.0
        self.assertAlmostEqual(
            theta_i,
            0.0,
            places=6,
        )

    def test_default_nine_point_grid_is_centered_on_verified_raw_peak(self):
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        hwp_y_raw = float(mapping["calibrated_lab_axis_reference"]["raw_setpoints_deg"]["HWP_incident_y"])
        sign = float(mapping["raw_to_lab_formulas"]["signs"]["s_H"])
        centre_raw = float(load_production_constant("DEFAULT_HWP_GRID_CENTER_RAW_DEG"))
        centre_theta_i = (90.0 + 2.0 * sign * (centre_raw - hwp_y_raw)) % 180.0

        theta_grid = centre_theta_i + np.linspace(-90.0, 90.0, 9)
        raw_grid = np.mod(
            hwp_y_raw + sign * (theta_grid - 90.0) / 2.0,
            360.0,
        )

        self.assertAlmostEqual(centre_raw, 7.8951, places=6)
        self.assertAlmostEqual(centre_theta_i, 81.8688, places=6)
        self.assertEqual(len(theta_grid), 9)
        self.assertTrue(np.allclose(np.diff(theta_grid), 22.5))
        self.assertAlmostEqual(raw_grid[4], centre_raw, places=6)
        self.assertTrue(
            np.allclose(
                [((raw - centre_raw + 180.0) % 360.0) - 180.0 for raw in raw_grid],
                [-45.0, -33.75, -22.5, -11.25, 0.0, 11.25, 22.5, 33.75, 45.0],
            )
        )


if __name__ == "__main__":
    unittest.main()
