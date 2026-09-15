import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import csv
from datetime import datetime
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


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
        "np": np,
        "math": math,
        "datetime": datetime,
        "MANUAL_ANCHOR_PEAK_MISS_RATIO": 1.0,
        "MANUAL_ANCHOR_PEAK_MISS_MIN_UV": 0.0,
        "Path": Path,
        "csv": csv,
        "FAST_MAP_CSV": "fast_map.csv",
        "ANALYSER_SWEEP_SOURCE_SIDES": {"auto_peak"},
        "theta_i_from_hwp": lambda value: float(value),
        "legacy_theta_i_from_hwp": lambda value: float(value),
        "qwp_theta_wav_from_raw": lambda value, *_args, **_kwargs: float(value),
        "qwp_axis_lab_from_raw": lambda value: float(value),
        "analyser_lab_from_raw": lambda value: float(value),
        "analyser_theta_ana_from_raw": lambda value, *_args, **_kwargs: float(value),
        "analyse_fast_map_rows": lambda _rows, _config: {},
        "BTO_PHYSICS_CONFIG": {},
        "fit_best_hwp_cos4": lambda _rows: None,
        "pair_rows_for_json": lambda rows: rows,
        "PAIR_ASYMMETRY_WARN_FRACTION": 0.5,
        "lab_angle_mapping_config": lambda: {},
        "periodic_delta_deg": (
            lambda start, end, period: (float(end) - float(start) + 0.5 * float(period))
            % float(period) - 0.5 * float(period)
        ),
        "analyser_offset_from_null_deg": (
            lambda start, end: (float(end) - float(start) + 90.0) % 180.0 - 90.0
        ),
        "MAX_QWP_OFFSET_FROM_NULL_DEG": 3.0,
        "MAX_ANALYSER_QUADRATURE_ERROR_DEG": 15.0,
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


def parser_argument_default(option):
    for node in ast.walk(SOURCE_TREE):
        if not isinstance(node, ast.Call):
            continue
        if not (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            and node.args
        ):
            continue
        try:
            first_arg = ast.literal_eval(node.args[0])
        except (TypeError, ValueError):
            continue
        if first_arg != option:
            continue
        for keyword in node.keywords:
            if keyword.arg == "default":
                return ast.literal_eval(keyword.value)
        raise AssertionError(f"{option} has no explicit default")
    raise AssertionError(f"Missing parser option: {option}")


def production_function_node(name):
    for node in SOURCE_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing production function: {name}")


def statements_contain_raise(statements):
    return any(
        isinstance(child, ast.Raise)
        for statement in statements
        for child in ast.walk(statement)
    )


PRODUCTION = production_functions(
    "null_check_label",
    "classify_null_certification",
)
PEAK_LEARNING = production_functions(
    "row_quality_flags",
    "row_has_quality_flag",
    "row_has_untrusted_null",
    "row_has_angle_failure",
    "learn_readout_override_from_manual_anchor",
)
FOLLOWUP_SELECTION = production_functions(
    "_finite_float_or_none",
    "row_quality_flags",
    "row_has_quality_flag",
    "row_has_untrusted_null",
    "row_has_angle_failure",
    "filter_analyser_points_to_peak_hwp",
    "build_analyser_sweep_points_from_fast_map_csv",
)
SUMMARY_SELECTION = production_functions(
    "_finite_float_or_none",
    "_first_finite_followup_value",
    "lockin_response_value",
    "lockin_raw_response_value",
    "lockin_peak_metric_value",
    "row_quality_flags",
    "row_has_quality_flag",
    "row_has_untrusted_null",
    "row_has_angle_failure",
    "certified_peak_row_from_analysis",
    "summarize_fast_map",
)


class NullCertificationDefaultsTests(unittest.TestCase):
    def test_cli_defaults_define_a_14_5_to_16_mv_marginal_band(self):
        self.assertEqual(parser_argument_default("--null-check-max-mv"), 14.5)
        self.assertEqual(parser_argument_default("--null-certify-margin-mv"), 1.5)


class NullCertificationDecisionTests(unittest.TestCase):
    def classify(self, detector_mv, max_mv=14.5, margin_mv=1.5):
        return PRODUCTION["classify_null_certification"](
            detector_mv,
            max_mv,
            margin_mv,
        )

    def test_value_at_target_is_certified(self):
        self.assertEqual(
            self.classify(14.5),
            ("NULL OK", "", False),
        )

    def test_value_just_above_target_is_marginal_and_continues(self):
        self.assertEqual(
            self.classify(14.500001),
            ("NULL MARGINAL", "marginal_seed_null", False),
        )

    def test_value_at_target_plus_margin_is_still_marginal(self):
        self.assertEqual(
            self.classify(16.0),
            ("NULL MARGINAL", "marginal_seed_null", False),
        )

    def test_value_above_target_plus_margin_is_a_hard_failure(self):
        self.assertEqual(
            self.classify(16.000001),
            ("NULL HIGH", "high_seed_null", True),
        )

    def test_nonfinite_detector_read_is_a_hard_failure(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                label, quality_flag, hard_fail = self.classify(value)
                self.assertTrue(hard_fail)
                self.assertTrue(quality_flag)
                self.assertNotIn(label, {"NULL OK", "NULL MARGINAL"})

    def test_negative_margin_is_clamped_to_zero(self):
        self.assertEqual(
            self.classify(14.5, margin_mv=-2.0),
            ("NULL OK", "", False),
        )
        self.assertEqual(
            self.classify(14.500001, margin_mv=-2.0),
            ("NULL HIGH", "high_seed_null", True),
        )


class CalibrationNullContinuationPolicyTests(unittest.TestCase):
    def test_finite_hard_null_branch_does_not_raise(self):
        run_pixel = production_function_node("run_fast_map_for_pixel")
        hard_limit_branches = [
            node for node in ast.walk(run_pixel)
            if isinstance(node, ast.If)
            and ast.unparse(node.test) == "force_full_calibration_null and null_hard_fail"
        ]
        self.assertEqual(len(hard_limit_branches), 1)
        finite_branch = hard_limit_branches[0].body[0]
        self.assertIsInstance(finite_branch, ast.If)
        self.assertIn("np.isfinite", ast.unparse(finite_branch.test))
        self.assertFalse(statements_contain_raise(finite_branch.body))
        self.assertTrue(statements_contain_raise(finite_branch.orelse))

    def test_calibration_null_exception_no_longer_aborts_chip_loop(self):
        main_node = production_function_node("main")
        fatal_conditions = [
            ast.unparse(node.test)
            for node in ast.walk(main_node)
            if isinstance(node, ast.If)
            and statements_contain_raise(node.body)
        ]
        self.assertFalse(any(
            "CriticalCalibrationNullError" in condition
            for condition in fatal_conditions
        ))
        self.assertFalse(any(
            "CriticalAngleError" in condition
            for condition in fatal_conditions
        ))


class PeakLearningNullQualityTests(unittest.TestCase):
    def test_high_null_row_is_untrusted_for_selection_and_analysis(self):
        self.assertTrue(PEAK_LEARNING["row_has_untrusted_null"]({
            "quality_flags": "high_seed_null;high_null_continued",
        }))
        self.assertFalse(PEAK_LEARNING["row_has_untrusted_null"]({
            "quality_flags": "marginal_seed_null",
        }))
        self.assertTrue(PEAK_LEARNING["row_has_untrusted_null"]({
            "quality_flags": "s9_operating_point_failed",
        }))
        self.assertTrue(PEAK_LEARNING["row_has_untrusted_null"]({
            "quality_flags": "lockin_range_exceeded",
        }))

    def test_high_null_readout_is_not_saved_for_later_pixels(self):
        seeds = {}
        override = PEAK_LEARNING["learn_readout_override_from_manual_anchor"](
            hwp_key="41.645",
            hwp_deg=41.645,
            rows=[{
                "slope_side": "auto_peak",
                "quality_flags": "high_seed_null;high_null_continued",
            }],
            null_seed_by_hwp=seeds,
            pixel_num=1,
            require_standard_comparison=False,
        )
        self.assertIsNone(override)
        self.assertEqual(seeds, {})


class FollowupNullQualityTests(unittest.TestCase):
    def test_certified_triplet_uses_fixed_positive_branch_not_noisy_winner(self):
        common = {
            "hwp_deg": 10.0,
            "vpp": 9.0,
            "q_null_deg": 20.0,
            "qwp_readout_deg": 20.0,
            "qwp_readout_actual_deg": 20.0,
            "a_null_deg": 75.0,
            "anl_null_actual_deg": 75.0,
            "s9_status": "certified_triplet",
            "s9_operating_psi_deg": 45.0,
            "quality_flags": "s9_operating_point_certified",
        }
        plus = {
            **common,
            "slope_side": "plus45",
            "anl_target_deg": 120.0,
            "anl_actual_deg": 120.0,
            "lockin_net_mag_V": 10e-6,
        }
        minus = {
            **common,
            "slope_side": "minus45",
            "anl_target_deg": 30.0,
            "anl_actual_deg": 30.0,
            # Deliberately larger noisy magnitude: it must not win.
            "lockin_net_mag_V": 12e-6,
        }
        selected = SUMMARY_SELECTION["certified_peak_row_from_analysis"](
            [minus, plus],
            {"peak_certificate": {"status": "certified", "hwp_deg": 10.0}},
        )
        self.assertIs(selected, plus)

    def test_summary_peak_ignores_larger_high_null_row(self):
        common = {
            "vpp": 9.0,
            "slope_side": "plus45",
            "p_null_W": 3e-6,
            "power_W": 3e-6,
            "q_null_deg": 20.0,
            "anl_target_deg": 30.0,
            "a_null_deg": 75.0,
        }
        good = {
            **common,
            "hwp_deg": 10.0,
            "lockin_signed_V": 1e-6,
            "lockin_mag_V": 1e-6,
            "quality_flags": "",
        }
        untrusted = {
            **common,
            "hwp_deg": 20.0,
            "lockin_signed_V": 1e-3,
            "lockin_mag_V": 1e-3,
            "quality_flags": "high_seed_null;high_null_continued",
        }

        summary = SUMMARY_SELECTION["summarize_fast_map"](
            {"pixel": 1, "j": 1, "i": 1},
            [good, untrusted],
            [9.0],
        )

        self.assertEqual(summary["best_hwp_deg"], 10.0)
        self.assertEqual(summary["best_mag_hwp_deg"], 10.0)
        self.assertEqual(summary["n_points"], 2)
        self.assertEqual(summary["n_trusted_points"], 1)
        self.assertEqual(summary["n_standard_slope_points"], 2)
        self.assertEqual(summary["n_learned_slope_points"], 0)
        self.assertEqual(summary["n_untrusted_null_points"], 1)
        self.assertIn("high_null_continued", summary["quality_flags"])

    def test_summary_counts_production_triplet_slopes_as_measurements(self):
        common = {
            "hwp_deg": 10.0,
            "vpp": 3.0,
            "p_null_W": 3e-6,
            "power_W": 3e-6,
            "q_null_deg": 20.0,
            "qwp_readout_deg": 20.0,
            "a_null_deg": 75.0,
            "quality_flags": "",
        }
        rows = [
            {
                **common,
                "slope_side": "learned_anchor",
                "anl_target_deg": 120.0,
                "lockin_signed_V": 1e-6,
                "lockin_mag_V": 1e-6,
            },
            {
                **common,
                "slope_side": "learned_opposite",
                "anl_target_deg": 30.0,
                "lockin_signed_V": -1e-6,
                "lockin_mag_V": 1e-6,
            },
        ]

        summary = SUMMARY_SELECTION["summarize_fast_map"](
            {"pixel": 1, "j": 1, "i": 1}, rows, [3.0]
        )

        self.assertEqual(summary["n_points"], 2)
        self.assertEqual(summary["n_trusted_points"], 2)
        self.assertEqual(summary["n_standard_slope_points"], 0)
        self.assertEqual(summary["n_learned_slope_points"], 2)

    def test_csv_followup_fallback_excludes_larger_high_null_row(self):
        fields = [
            "hwp_deg",
            "qwp_readout_deg",
            "q_null_deg",
            "anl_target_deg",
            "a_null_deg",
            "lockin_mag_V",
            "vpp",
            "slope_side",
            "quality_flags",
        ]
        rows = [
            {
                "hwp_deg": "10",
                "qwp_readout_deg": "20",
                "q_null_deg": "19",
                "anl_target_deg": "30",
                "a_null_deg": "29",
                "lockin_mag_V": "0.000001",
                "vpp": "9",
                "slope_side": "auto_peak",
                "quality_flags": "",
            },
            {
                "hwp_deg": "20",
                "qwp_readout_deg": "40",
                "q_null_deg": "39",
                "anl_target_deg": "50",
                "a_null_deg": "49",
                "lockin_mag_V": "0.001",
                "vpp": "9",
                "slope_side": "auto_peak",
                "quality_flags": "high_seed_null;high_null_continued",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "fast_map.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            points = FOLLOWUP_SELECTION[
                "build_analyser_sweep_points_from_fast_map_csv"
            ](Path(tmp), peak_hwp_only=False)

        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["hwp_deg"], 10.0)
        self.assertEqual(points[0]["quality_flags"], "")

    def test_dc_hysteresis_can_explicitly_use_all_high_null_rows_as_flagged_fallback(self):
        fields = [
            "hwp_deg",
            "qwp_readout_deg",
            "q_null_deg",
            "anl_target_deg",
            "a_null_deg",
            "lockin_mag_V",
            "vpp",
            "slope_side",
            "quality_flags",
        ]
        rows = [
            {
                "hwp_deg": "10",
                "qwp_readout_deg": "20",
                "q_null_deg": "19",
                "anl_target_deg": "30",
                "a_null_deg": "29",
                "lockin_mag_V": "0.000001",
                "vpp": "9",
                "slope_side": "auto_peak",
                "quality_flags": "high_null_continued",
            },
            {
                "hwp_deg": "20",
                "qwp_readout_deg": "40",
                "q_null_deg": "39",
                "anl_target_deg": "50",
                "a_null_deg": "49",
                "lockin_mag_V": "0.001",
                "vpp": "9",
                "slope_side": "auto_peak",
                "quality_flags": "high_seed_null;high_null_continued",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "fast_map.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaises(ValueError):
                FOLLOWUP_SELECTION[
                    "build_analyser_sweep_points_from_fast_map_csv"
                ](Path(tmp), peak_hwp_only=False)

            points = FOLLOWUP_SELECTION[
                "build_analyser_sweep_points_from_fast_map_csv"
            ](
                Path(tmp),
                peak_hwp_only=False,
                allow_untrusted_fallback=True,
            )

        self.assertEqual(len(points), 2)
        strongest = max(points, key=lambda point: abs(point["mag"]))
        self.assertEqual(strongest["hwp_deg"], 20.0)
        self.assertTrue(strongest["untrusted_high_null_fallback"])
        self.assertIn("high_null_continued", strongest["quality_flags"])


if __name__ == "__main__":
    unittest.main()
