import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import csv
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


def production_node(name):
    for node in SOURCE_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing production function: {name}")


def production_functions(*names):
    nodes = [production_node(name) for name in names]
    namespace = {
        "Path": Path,
        "csv": csv,
        "json": json,
        "math": math,
        "FAST_MAP_CSV": "fast_map.csv",
        "FAST_MAP_SUMMARY_JSON": "fast_map_summary.json",
        "CHIP_SUMMARY_CSV": "fast_map_all_pixels.csv",
        "fast_map_peak_for_followup": (
            lambda *, pixel, pixel_dir, peak_conditions=None,
            allow_untrusted_fallback=False: dict(peak_conditions or {})
        ),
        "pockels": SimpleNamespace(
            DC_HYSTERESIS_LIVE_JSON="dc_hysteresis_live.json",
        ),
        "auto": SimpleNamespace(
            PROGRESS_JSON="automation_progress.json",
            DEFAULT_PIXELS_SUBDIR="pockels_pixels",
            DEFAULT_STAGE_SUBDIR="stage_alignment",
            SUBSTRATE_CAL_FILENAME="substrate_calibration.json",
            NULL_SEEDS_JSON="null_seed_by_hwp.json",
        ),
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = production_functions(
    "_finite_float_or_none",
    "_gui_int",
    "_read_json_dict",
    "null_seed_table_covers_hwp_angles",
    "load_fast_map_resume_artifacts",
    "fast_map_sweep_complete",
    "dc_hysteresis_sweep_complete",
    "resume_pixel_component_needs",
    "ordered_pixel_work_queue",
    "summarize_fast_map_run",
)


def save_chip_sweep(pixel_dir: Path) -> None:
    pixel_dir.mkdir(parents=True, exist_ok=True)
    with (pixel_dir / "fast_map.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=["hwp_deg", "lockin_mag_V"])
        writer.writeheader()
        writer.writerow({"hwp_deg": "41.645", "lockin_mag_V": "1e-6"})
    (pixel_dir / "fast_map_summary.json").write_text(
        json.dumps({"pixel": int(pixel_dir.name.rsplit("_", 1)[-1])}),
        encoding="utf-8",
    )


def save_hysteresis(pixel_dir: Path, *, live_marker: bool = False) -> None:
    hyst_root = pixel_dir / "dc_hysteresis"
    if live_marker:
        hyst_root.mkdir(parents=True, exist_ok=True)
        (hyst_root / "dc_hysteresis_live.json").write_text(
            json.dumps({
                "complete": True,
                "event": "complete",
                "sweep_stage": "main",
            }),
            encoding="utf-8",
        )
        return
    sweep_dir = hyst_root / "sweep"
    sweep_dir.mkdir(parents=True, exist_ok=True)
    (sweep_dir / "dc_hysteresis.csv").write_text(
        "# saved by an older completed run\n"
        "idx,V_dc_V,LockIn_Mag_V\n"
        "0,40,1e-6\n",
        encoding="utf-8",
    )


class ResumeComponentArtifactTests(unittest.TestCase):
    def test_missing_historical_summary_uses_in_memory_peak_metadata(self):
        peak = {
            "source": "historical_fast_map_raw_magnitude_max",
            "hwp_deg": 75.0,
            "theta_i_deg": 216.0786,
            "theta_i_legacy_deg": 150.0,
            "q_null": 120.0,
            "qwp_angle": 125.0,
            "anl_angle": 32.28,
            "a_null": 347.28,
            "vpp": 9.0,
            "mag": 13.336e-6,
            "slope_side": "learned_anchor",
        }
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_055"
            pixel_dir.mkdir()
            summary, loaded_peak, derived = PRODUCTION[
                "load_fast_map_resume_artifacts"
            ](
                pixel={"pixel": 55, "j": 5, "i": 4},
                pixel_dir=pixel_dir,
                peak_conditions=peak,
            )

        self.assertTrue(derived)
        self.assertEqual(loaded_peak, peak)
        self.assertEqual(summary["pixel"], 55)
        self.assertEqual(summary["best_hwp_deg"], 75.0)
        self.assertAlmostEqual(summary["max_lockin_mag_V"], 13.336e-6)
        self.assertTrue(summary["resume_metadata_only"])

    def test_followup_only_pixel_is_merged_before_later_chip_pixels(self):
        queue = PRODUCTION["ordered_pixel_work_queue"](
            [13, 14, 15],
            set(),
            set(),
            {12, 13, 14, 15},
        )
        self.assertEqual(queue, [12, 13, 14, 15])

        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_012"
            save_chip_sweep(pixel_dir)
            needs = PRODUCTION["resume_pixel_component_needs"](
                pixel_dir,
                chip_sweep_requested=False,
                dc_hysteresis_requested=True,
                skip_complete=True,
            )
        self.assertEqual(
            needs,
            (False, True),
            "Pixel 12 must run hysteresis-only before Pixel 13 without rerunning its chip sweep",
        )

    def test_component_plan_encodes_chip_then_hysteresis_hierarchy(self):
        plan = PRODUCTION["resume_pixel_component_needs"]
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_012"

            self.assertEqual(
                plan(
                    pixel_dir,
                    dc_hysteresis_requested=True,
                    skip_complete=True,
                ),
                (True, True),
                "with neither artifact, chip sweep must run before hysteresis",
            )

            save_chip_sweep(pixel_dir)
            self.assertEqual(
                plan(
                    pixel_dir,
                    dc_hysteresis_requested=True,
                    skip_complete=True,
                ),
                (False, True),
                "a saved chip sweep plus missing hysteresis is hysteresis-only",
            )

            save_hysteresis(pixel_dir, live_marker=True)
            self.assertEqual(
                plan(
                    pixel_dir,
                    dc_hysteresis_requested=True,
                    skip_complete=True,
                ),
                (False, False),
                "both verified artifacts must be skipped",
            )
            self.assertEqual(
                plan(
                    pixel_dir,
                    dc_hysteresis_requested=True,
                    skip_complete=False,
                ),
                (True, True),
                "turning skip-complete off explicitly remeasures both components",
            )

    def test_chip_only_pixel_without_hysteresis_request_is_skipped(self):
        plan = PRODUCTION["resume_pixel_component_needs"]
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_012"
            save_chip_sweep(pixel_dir)
            self.assertEqual(
                plan(
                    pixel_dir,
                    dc_hysteresis_requested=False,
                    skip_complete=True,
                ),
                (False, False),
            )

    def test_completed_calibration_pixel_does_not_block_next_resume_pixel(self):
        """Regression: saved Pixel 2 calibration must not pre-empt Pixel 5."""
        plan = PRODUCTION["resume_pixel_component_needs"]
        with tempfile.TemporaryDirectory() as tmp:
            pixels_dir = Path(tmp)
            save_chip_sweep(pixels_dir / "pixel_002")
            queue = PRODUCTION["ordered_pixel_work_queue"](
                [2, 5, 6],
                first_pixel=2,
            )
            chip_sweeps_needed = [
                pixel
                for pixel in queue
                if plan(
                    pixels_dir / f"pixel_{pixel:03d}",
                    dc_hysteresis_requested=False,
                    skip_complete=True,
                )[0]
            ]

        self.assertEqual(queue, [2, 5, 6])
        self.assertEqual(chip_sweeps_needed, [5, 6])
        self.assertEqual(chip_sweeps_needed[0], 5)

    def test_partial_or_empty_files_do_not_count_as_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            pixel_dir = Path(tmp) / "pixel_012"
            pixel_dir.mkdir()
            (pixel_dir / "fast_map.csv").write_text(
                "hwp_deg,lockin_mag_V\n",
                encoding="utf-8",
            )
            (pixel_dir / "fast_map_summary.json").write_text(
                "{}",
                encoding="utf-8",
            )
            self.assertFalse(PRODUCTION["fast_map_sweep_complete"](pixel_dir))

            hyst_root = pixel_dir / "dc_hysteresis"
            hyst_root.mkdir()
            (hyst_root / "dc_hysteresis_live.json").write_text(
                json.dumps({
                    "complete": False,
                    "event": "point",
                    "sweep_stage": "main",
                }),
                encoding="utf-8",
            )
            self.assertFalse(
                PRODUCTION["dc_hysteresis_sweep_complete"](pixel_dir)
            )


class ResumeRunSummaryTests(unittest.TestCase):
    def test_stale_complete_status_cannot_hide_missing_hysteresis(self):
        summarize = PRODUCTION["summarize_fast_map_run"]
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            pixels_dir = run_dir / "pockels_pixels"
            progress = {
                "config": {
                    "selected_pixels": [11, 12, 13, 14],
                    "post_dc_hysteresis_pixels": [11, 12, 13, 14, 18],
                },
                "pixels": [
                    {"pixel": pix, "measurement_status": "complete"}
                    for pix in (11, 12, 13, 14, 18)
                ],
                "pixel_records": [
                    {"pixel": pix, "status": "complete"}
                    for pix in (11, 12, 13, 14, 18)
                ],
            }
            (run_dir / "automation_progress.json").write_text(
                json.dumps(progress),
                encoding="utf-8",
            )

            pixel_11 = pixels_dir / "pixel_011"
            save_chip_sweep(pixel_11)
            save_hysteresis(pixel_11)

            # Regression case: chip sweep exists and old progress falsely says
            # complete, but there is no hysteresis artifact.
            save_chip_sweep(pixels_dir / "pixel_012")

            # Pixel 13 has no artifacts despite stale "complete" bookkeeping.

            pixel_14 = pixels_dir / "pixel_014"
            save_chip_sweep(pixel_14)
            save_hysteresis(pixel_14, live_marker=True)

            # DC-only selection outside the old chip list: it belongs in the
            # same ordered resume queue because its chip prerequisite exists.
            save_chip_sweep(pixels_dir / "pixel_018")

            summary = summarize(run_dir)

        self.assertEqual(summary["completed_pixels"], [11, 14])
        self.assertEqual(summary["hysteresis_only_pixels"], [12, 18])
        self.assertEqual(summary["remaining_pixels"], [12, 13, 18])
        self.assertEqual(summary["unmeasured_pixels"], [13])
        self.assertEqual(summary["first_incomplete_pixel"], 12)

    def test_main_uses_component_plan_for_the_per_pixel_queue(self):
        main_node = production_node("main")
        plan_calls = [
            node
            for node in ast.walk(main_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "resume_pixel_component_needs"
        ]
        self.assertEqual(len(plan_calls), 1)

        source = ast.unparse(main_node)
        self.assertIn(
            "for seq, pix_num in enumerate(pixel_work_queue, start=1):",
            source,
        )
        self.assertIn(
            "chip_sweep_requested=chip_sweep_requested",
            source,
        )
        self.assertIn("load_fast_map_resume_artifacts", source)
        self.assertNotIn(
            "chip_sweep_needed = True",
            source,
            "main must never override the artifact-based --skip-complete plan "
            "to retry a completed calibration pixel",
        )
        self.assertIn("--skip-complete is authoritative", source)
        self.assertIn("preserved and not rerun", source)
        self.assertNotIn(
            "(pixel_dir / FAST_MAP_SUMMARY_JSON).read_text()",
            source,
        )
        self.assertIn("if chip_sweep_needed:", source)
        self.assertIn("if dc_hysteresis_needed:", source)
        self.assertIn(
            "- numerically_queued_followups",
            source,
            "failed follow-ups must remain resumable instead of being retried "
            "after later pixels",
        )

    def test_dc_done_is_recorded_only_after_artifact_verification(self):
        main_node = production_node("main")
        parents = {}
        for parent in ast.walk(main_node):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent

        done_calls = [
            node
            for node in ast.walk(main_node)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "post_dc_done"
            and node.func.attr == "add"
        ]
        self.assertEqual(len(done_calls), 2)
        for done_call in done_calls:
            enclosing_try = parents.get(done_call)
            while enclosing_try is not None and not isinstance(enclosing_try, ast.Try):
                enclosing_try = parents.get(enclosing_try)
            self.assertIsNotNone(enclosing_try)
            preceding_source = "\n".join(
                ast.unparse(statement)
                for statement in enclosing_try.body
                if statement.lineno < done_call.lineno
            )
            self.assertIn(
                "dc_hysteresis_sweep_complete(pixel_dir)",
                preceding_source,
            )


if __name__ == "__main__":
    unittest.main()
