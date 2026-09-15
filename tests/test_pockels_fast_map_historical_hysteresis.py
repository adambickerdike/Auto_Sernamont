import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import tempfile
import unittest


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def production_function(name):
    for node in SOURCE_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing production function: {name}")


def production_method(name):
    for node in ast.walk(SOURCE_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing production method: {name}")


def load_peak_selector():
    names = (
        "_finite_float_or_none",
        "row_quality_flags",
        "row_has_quality_flag",
        "row_has_untrusted_null",
        "row_has_angle_failure",
        "_first_finite_followup_value",
        "raw_magnitude_peak_from_fast_map_csv",
    )
    namespace = {
        "Path": Path,
        "csv": csv,
        "math": math,
        "FAST_MAP_CSV": "fast_map.csv",
        "MAX_QWP_OFFSET_FROM_NULL_DEG": 3.0,
        "MAX_ANALYSER_QUADRATURE_ERROR_DEG": 15.0,
    }
    nodes = [production_function(name) for name in names]
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace["raw_magnitude_peak_from_fast_map_csv"]


SELECT_RAW_MAGNITUDE_PEAK = load_peak_selector()


def load_raw_fallback_policy():
    namespace = {}
    node = production_function("legacy_raw_peak_fallback_allowed")
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace["legacy_raw_peak_fallback_allowed"]


RAW_FALLBACK_ALLOWED = load_raw_fallback_policy()


def load_historical_hysteresis_artifact_helpers():
    names = (
        "_finite_float_or_none",
        "_read_json_dict",
        "historical_hysteresis_plan_matches_peak",
        "archive_mismatched_historical_hysteresis",
    )
    namespace = {
        "Path": Path,
        "datetime": datetime,
        "json": json,
        "math": math,
    }
    nodes = [production_function(name) for name in names]
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return (
        namespace["historical_hysteresis_plan_matches_peak"],
        namespace["archive_mismatched_historical_hysteresis"],
    )


PLAN_MATCHES_PEAK, ARCHIVE_MISMATCHED_HYSTERESIS = (
    load_historical_hysteresis_artifact_helpers()
)


FIELDS = [
    "hwp_deg",
    "theta_i_deg",
    "q_null_deg",
    "qwp_readout_deg",
    "a_null_deg",
    "anl_target_deg",
    "vpp",
    "lockin_mag_V",
    "slope_side",
    "quality_flags",
]


def write_rows(pixel_dir: Path, rows: list[dict]) -> None:
    pixel_dir.mkdir(parents=True, exist_ok=True)
    with (pixel_dir / "fast_map.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class HistoricalRawMagnitudePeakTests(unittest.TestCase):
    def test_raw_fallback_is_legacy_only_unless_explicitly_requested(self):
        self.assertTrue(RAW_FALLBACK_ALLOWED({}))
        self.assertTrue(RAW_FALLBACK_ALLOWED({"best_hwp_deg": 10.0}))
        modern_failed = {
            "normalized_peak_certificate": {"status": "invalid"},
        }
        self.assertFalse(RAW_FALLBACK_ALLOWED(modern_failed))
        self.assertTrue(
            RAW_FALLBACK_ALLOWED(
                modern_failed,
                allow_untrusted_fallback=True,
            )
        )

    def test_may_style_rows_choose_largest_geometry_valid_raw_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_031"
            write_rows(
                pixel_dir,
                [
                    {
                        "hwp_deg": 67.5,
                        "theta_i_deg": 135.0,
                        "q_null_deg": 144.419584,
                        "qwp_readout_deg": "",
                        "a_null_deg": 262.400649,
                        "anl_target_deg": 217.400649,
                        "vpp": 9,
                        "lockin_mag_V": 6.7288825e-5,
                        "slope_side": "minus45",
                        "quality_flags": "",
                    },
                    {
                        "hwp_deg": 52.5,
                        "theta_i_deg": 105.0,
                        "q_null_deg": 113.624406,
                        "qwp_readout_deg": "",
                        "a_null_deg": 52.046172,
                        "anl_target_deg": 97.046172,
                        "vpp": 9,
                        "lockin_mag_V": 7.5134e-5,
                        "slope_side": "plus45",
                        "quality_flags": "",
                    },
                    {
                        "hwp_deg": 52.5,
                        "theta_i_deg": 105.0,
                        "q_null_deg": 113.624406,
                        "qwp_readout_deg": "",
                        "a_null_deg": 52.046172,
                        "anl_target_deg": 187.046172,
                        "vpp": 9,
                        "lockin_mag_V": 9.8998e-5,
                        "slope_side": "minus45",
                        "quality_flags": "",
                    },
                ],
            )

            peak = SELECT_RAW_MAGNITUDE_PEAK(
                pixel={"pixel": 31},
                pixel_dir=pixel_dir,
                allow_untrusted_fallback=True,
            )

        self.assertEqual(
            peak["source"],
            "historical_fast_map_geometry_certified_raw_fallback",
        )
        self.assertEqual(peak["selection_metric"], "lockin_mag_V")
        self.assertAlmostEqual(peak["hwp_deg"], 52.5)
        self.assertAlmostEqual(peak["qwp_angle"], 113.624406)
        self.assertAlmostEqual(peak["anl_angle"], 187.046172)
        self.assertAlmostEqual(peak["mag"], 98.998e-6)
        self.assertFalse(peak["untrusted_high_null_fallback"])
        self.assertFalse(peak["quality_flags_ignored_for_selection"])
        self.assertTrue(peak["geometry_certified"])
        self.assertTrue(peak["hysteresis_eligible"])
        self.assertTrue(peak["historical_raw_motor_angles"])

    def test_larger_high_null_row_is_excluded_from_hysteresis_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_009"
            write_rows(
                pixel_dir,
                [
                    {
                        "hwp_deg": 20,
                        "theta_i_deg": 40,
                        "q_null_deg": 70,
                        "qwp_readout_deg": "",
                        "a_null_deg": 90,
                        "anl_target_deg": 135,
                        "vpp": 9,
                        "lockin_mag_V": 1e-5,
                        "slope_side": "plus45",
                        "quality_flags": "",
                    },
                    {
                        "hwp_deg": 30,
                        "theta_i_deg": 60,
                        "q_null_deg": 80,
                        "qwp_readout_deg": "",
                        "a_null_deg": 100,
                        "anl_target_deg": 145,
                        "vpp": 9,
                        "lockin_mag_V": 2e-5,
                        "slope_side": "minus45",
                        "quality_flags": "high_null_continued",
                    },
                ],
            )
            peak = SELECT_RAW_MAGNITUDE_PEAK(
                pixel={"pixel": 9},
                pixel_dir=pixel_dir,
                allow_untrusted_fallback=False,
            )

        self.assertAlmostEqual(peak["hwp_deg"], 20)
        self.assertAlmostEqual(peak["mag"], 10e-6)
        self.assertFalse(peak["untrusted_high_null_fallback"])
        self.assertFalse(peak["quality_flags_ignored_for_selection"])
        self.assertTrue(peak["geometry_certified"])
        self.assertTrue(peak["hysteresis_eligible"])
        self.assertEqual(
            peak["selection_policy"],
            "largest finite raw magnitude after high-null, QWP-compensation, "
            "and analyser-quadrature gates",
        )

    def test_newer_peak_row_keeps_the_saved_qwp_readout_motor_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_012"
            write_rows(
                pixel_dir,
                [{
                    "hwp_deg": 22.5,
                    "theta_i_deg": 45,
                    "q_null_deg": 80,
                    "qwp_readout_deg": 82.5,
                    "a_null_deg": 100,
                    "anl_target_deg": 145,
                    "vpp": 9,
                    "lockin_mag_V": 4e-5,
                    "slope_side": "auto_peak",
                    "quality_flags": "",
                }],
            )
            peak = SELECT_RAW_MAGNITUDE_PEAK(
                pixel={"pixel": 12},
                pixel_dir=pixel_dir,
            )

        self.assertAlmostEqual(peak["q_null"], 80)
        self.assertAlmostEqual(peak["qwp_angle"], 82.5)

    def test_previous_different_peak_hysteresis_is_detected_and_archived(self):
        old_peak = {
            "hwp_deg": 60.0,
            "qwp_angle": 128.74213666020904,
            "anl_angle": 112.06935249412886,
            "mag": 69.422e-6,
        }
        absolute_peak = {
            "hwp_deg": 15.0,
            "qwp_angle": 45.60381665108195,
            "anl_angle": 209.79928922121576,
            "mag": 77.288e-6,
        }
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_002"
            hyst_dir = pixel_dir / "dc_hysteresis"
            hyst_dir.mkdir(parents=True)
            (hyst_dir / "dc_hysteresis_plan.json").write_text(
                json.dumps({"peak": old_peak}),
                encoding="utf-8",
            )
            marker = hyst_dir / "old_attempt.txt"
            marker.write_text("preserve me", encoding="utf-8")

            self.assertTrue(PLAN_MATCHES_PEAK(pixel_dir, old_peak))
            self.assertFalse(PLAN_MATCHES_PEAK(pixel_dir, absolute_peak))
            archived = ARCHIVE_MISMATCHED_HYSTERESIS(pixel_dir)

            self.assertIsNotNone(archived)
            self.assertFalse(hyst_dir.exists())
            self.assertEqual((archived / marker.name).read_text(), "preserve me")

    def test_gui_historical_button_configures_chip_runner_without_hwp_pixels(self):
        method_source = ast.unparse(
            production_method("_launch_historical_hysteresis_only")
        )
        self.assertIn("raw_magnitude_peak_from_fast_map_csv", method_source)
        self.assertIn("self.vars['pixels'].set('none')", method_source)
        self.assertIn("self._start_measurement()", method_source)
        self.assertIn("historical_hysteresis_plan_matches_peak", method_source)
        self.assertIn("archive_mismatched_historical_hysteresis", method_source)

        followup_source = ast.unparse(production_function("fast_map_peak_for_followup"))
        self.assertIn("raw_magnitude_peak_from_fast_map_csv", followup_source)
        move_source = ast.unparse(production_function("move_to_followup_peak_conditions"))
        self.assertIn("historical_raw_motor_angles", move_source)
        self.assertIn("peak.get('qwp_angle')", move_source)

    def test_queued_hysteresis_requires_the_same_certified_peak_as_in_run(self):
        writer_source = ast.unparse(production_method("_write_followup_peak_json"))
        self.assertIn("fast_map_peak_for_followup", writer_source)
        self.assertIn("require_hysteresis", writer_source)
        self.assertIn("hysteresis_eligible", writer_source)

        queue_source = ast.unparse(production_method("_build_followup_jobs"))
        self.assertIn("require_hysteresis=kind == 'dc_hysteresis'", queue_source)


if __name__ == "__main__":
    unittest.main()
