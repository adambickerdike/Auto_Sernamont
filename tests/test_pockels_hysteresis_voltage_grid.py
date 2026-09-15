import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np


SOURCE_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
GUI_SOURCE_TEXT = _bootstrap.module_path("pockels_fast_map_gui.py").read_text(
    encoding="utf-8"
)


def production_constant(name):
    for node in SOURCE_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing production constant: {name}")


def production_functions(*names):
    wanted = set(names)
    nodes = [
        node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    missing = wanted - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")
    namespace = {
        "math": math,
        "np": np,
        "DC_HYST_CENTER_DENSE_ABS_LEVELS_V": production_constant(
            "DC_HYST_CENTER_DENSE_ABS_LEVELS_V"
        ),
        "DC_HYST_COARSE_STEP": production_constant("DC_HYST_COARSE_STEP"),
        "DC_HYST_FINE_STEP": production_constant("DC_HYST_FINE_STEP"),
        "DC_HYST_VMAX": production_constant("DC_HYST_VMAX"),
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = production_functions(
    "validate_hysteresis_voltage_limit",
    "validate_reset_voltage_window",
    "_voltage_grid_uniform",
    "_build_center_dense_hysteresis_points",
    "_resolve_fine_windows",
    "_build_hybrid_hysteresis_points",
    "describe_hysteresis_spacing",
    "_build_hysteresis_trajectory",
    "_hyst_kwargs_from_cli",
)


POSITIVE_DESCENDING = [
    40.0,
    30.0,
    25.0,
    20.0,
    15.0,
    12.5,
    10.0,
    7.5,
    5.0,
    2.5,
    1.25,
    0.0,
]
FULL_DOWN_LEG = POSITIVE_DESCENDING + [
    -1.25,
    -2.5,
    -5.0,
    -7.5,
    -10.0,
    -12.5,
    -15.0,
    -20.0,
    -25.0,
    -30.0,
    -40.0,
]
FULL_UP_LEG_WITHOUT_DUPLICATE_TURN = FULL_DOWN_LEG[-2::-1]


class CenterDenseVoltageGridTests(unittest.TestCase):
    def test_standard_levels_exactly_match_requested_symmetric_range(self):
        points = PRODUCTION["_build_center_dense_hysteresis_points"](40.0)

        expected = np.asarray(
            [-v for v in POSITIVE_DESCENDING[:-1]]
            + list(reversed(POSITIVE_DESCENDING)),
            dtype=float,
        )
        np.testing.assert_array_equal(points, expected)
        self.assertEqual(len(points), 23)

    def test_one_saturated_cycle_has_the_exact_45_point_order(self):
        voltages, branches, cycles = PRODUCTION[
            "_build_hysteresis_trajectory"
        ](
            40.0,
            start_from_zero=False,
            cycles=1,
        )

        expected = FULL_DOWN_LEG + FULL_UP_LEG_WITHOUT_DUPLICATE_TURN
        np.testing.assert_array_equal(voltages, np.asarray(expected, dtype=float))
        self.assertEqual(len(voltages), 45)
        self.assertEqual(list(branches), ["sat"] + ["down"] * 22 + ["up"] * 22)
        self.assertEqual(list(cycles), [1] * 45)
        self.assertEqual(list(voltages).count(-40.0), 1)
        self.assertEqual(list(voltages).count(40.0), 2)

    def test_multiple_cycles_repeat_without_duplicate_turning_points(self):
        voltages, branches, cycles = PRODUCTION[
            "_build_hysteresis_trajectory"
        ](
            40.0,
            start_from_zero=False,
            cycles=2,
        )

        self.assertEqual(len(voltages), 89)
        self.assertEqual(branches.count("down1"), 22)
        self.assertEqual(branches.count("up1"), 22)
        self.assertEqual(branches.count("down2"), 22)
        self.assertEqual(branches.count("up2"), 22)
        self.assertEqual(cycles.count(1), 45)
        self.assertEqual(cycles.count(2), 44)

    def test_numeric_step_remains_an_explicit_uniform_override(self):
        voltages, _branches, _cycles = PRODUCTION[
            "_build_hysteresis_trajectory"
        ](
            40.0,
            step=5.0,
            start_from_zero=False,
            cycles=1,
        )

        self.assertEqual(len(voltages), 33)
        self.assertNotIn(1.25, voltages)
        self.assertIn(5.0, voltages)

    def test_standard_spacing_is_identified_in_operator_text(self):
        description = PRODUCTION["describe_hysteresis_spacing"](None)
        self.assertIn("centre-dense standard", description)
        self.assertIn("1.25", description)
        self.assertIn("40", description)

        reduced = PRODUCTION["describe_hysteresis_spacing"](None, 30.0)
        self.assertIn("30", reduced)
        self.assertNotIn("40", reduced)

    def test_production_hysteresis_and_reset_limits_are_40_v(self):
        self.assertEqual(production_constant("DC_HYST_VMAX"), 40.0)
        self.assertEqual(production_constant("RESET_VMAX"), 40.0)
        self.assertEqual(
            PRODUCTION["validate_hysteresis_voltage_limit"](-40.0),
            40.0,
        )
        with self.assertRaisesRegex(ValueError, r"hard \+/-40 V"):
            PRODUCTION["validate_hysteresis_voltage_limit"](50.0)
        self.assertEqual(
            PRODUCTION["validate_reset_voltage_window"](40.0, 0.05),
            (40.0, 0.05),
        )
        with self.assertRaisesRegex(ValueError, r"hard \+/-40 V"):
            PRODUCTION["validate_reset_voltage_window"](40.0, 50.0)
        with self.assertRaisesRegex(ValueError, "cannot exceed Vmax"):
            PRODUCTION["validate_reset_voltage_window"](20.0, 30.0)

    def test_adjustable_vmax_reaches_cli_and_both_gui_launch_paths(self):
        kwargs = PRODUCTION["_hyst_kwargs_from_cli"](
            SimpleNamespace(hyst_vmax=30.0),
        )
        self.assertEqual(kwargs["vmax_v"], 30.0)
        self.assertIn('"followup_hyst_vmax": tk.StringVar', GUI_SOURCE_TEXT)
        self.assertIn('"--post-dc-hysteresis-vmax"', GUI_SOURCE_TEXT)
        self.assertIn('"--hyst-vmax"', GUI_SOURCE_TEXT)
        self.assertIn(
            '"post_dc_hysteresis_vmax_v": "followup_hyst_vmax"',
            GUI_SOURCE_TEXT,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
