import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
from pathlib import Path
import unittest

from pockels_lockin_ranging import PredictiveHystereticRangeController


TABLE = {
    11: 5e-6,
    12: 10e-6,
    13: 20e-6,
    14: 50e-6,
    15: 100e-6,
    16: 200e-6,
}


class PredictiveRangeControllerTests(unittest.TestCase):
    def test_first_point_uses_scaled_peak_anchor_directly(self):
        controller = PredictiveHystereticRangeController(
            TABLE,
            16,
            initial_anchor_v=10.2e-6,
        )
        decision = controller.plan(50.0, "sat", 1)

        self.assertEqual(decision.sensitivity_index, 13)  # 20 uV FS
        self.assertEqual(decision.reason, "initial_peak_scaled_prediction")
        self.assertLess(decision.upper_bound_v, 0.60 * TABLE[13])

    def test_narrowing_requires_two_points_and_moves_one_step(self):
        controller = PredictiveHystereticRangeController(
            TABLE,
            13,
            initial_anchor_v=8e-6,
        )
        first = controller.plan(20.0, "down", 1)
        controller.record(20.0, "down", 1, 2.0e-6, 0.1e-6)
        pending = controller.plan(10.0, "down", 1)
        controller.record(10.0, "down", 1, 1.5e-6, 0.1e-6)
        narrowed = controller.plan(5.0, "down", 1)

        self.assertEqual(first.sensitivity_index, 13)
        self.assertEqual(pending.sensitivity_index, 13)
        self.assertEqual(pending.reason, "narrow_pending_hysteresis")
        self.assertEqual(narrowed.sensitivity_index, 12)
        self.assertEqual(narrowed.reason, "confirmed_one_step_narrow")

    def test_emergency_check_never_narrows(self):
        controller = PredictiveHystereticRangeController(TABLE, 14)
        selected = controller.emergency_widen_index(0.2e-6, 0.1e-6)
        self.assertEqual(selected, 14)

    def test_emergency_overload_widens_at_least_one_step(self):
        controller = PredictiveHystereticRangeController(TABLE, 11)
        selected = controller.emergency_widen_index(
            1.0e-6,
            0.1e-6,
            output_overload=True,
        )
        self.assertEqual(selected, 12)

    def test_representative_loop_uses_low_ranges_then_widens_before_rail(self):
        controller = PredictiveHystereticRangeController(
            TABLE,
            16,
            initial_anchor_v=10.2e-6,
        )
        curve_uV = [
            (50.0, 9.9), (40.0, 8.9), (30.0, 7.8), (20.0, 6.2),
            (15.0, 5.1), (10.0, 3.7), (5.0, 2.4), (2.5, 1.3),
            (0.0, 0.8), (-2.5, 0.6), (-5.0, 0.2), (-10.0, 0.8),
            (-15.0, 1.8), (-20.0, 2.9), (-30.0, 5.0), (-40.0, 6.7),
            (-50.0, 8.2),
        ]
        selected = []
        for voltage, magnitude_uV in curve_uV:
            decision = controller.plan(voltage, "down", 1)
            selected.append((voltage, decision.sensitivity_index))
            controller.record(
                voltage,
                "down",
                1,
                magnitude_uV * 1e-6,
                0.1e-6,
            )

        self.assertIn(11, [index for _, index in selected])  # 5 uV near zero
        self.assertGreaterEqual(selected[-1][1], 13)  # >=20 uV at -50 V


class GuiPlumbingTests(unittest.TestCase):
    def test_checkbox_and_both_explicit_cli_modes_are_present(self):
        source = _bootstrap.module_path("pockels_fast_map_gui.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        default_value = None
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name)
                and target.id == "DEFAULT_HYSTERESIS_DYNAMIC_LOCKIN_RANGE"
                for target in node.targets
            ):
                default_value = ast.literal_eval(node.value)
                break

        self.assertFalse(default_value)
        self.assertIn("Dynamic lock-in range during hysteresis", source)
        self.assertIn("--post-dc-hysteresis-dynamic-lockin-range", source)
        self.assertIn("--post-dc-hysteresis-fixed-lockin-range", source)
        self.assertIn("--hyst-dynamic-lockin-range", source)
        self.assertIn("--hyst-fixed-lockin-range", source)


if __name__ == "__main__":
    unittest.main()
