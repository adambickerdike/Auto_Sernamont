import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import json
import math
from pathlib import Path
import tempfile
import unittest


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
    namespace = {"Path": Path, "json": json, "math": math}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


def class_method(class_name, method_name):
    class_node = next(
        node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def string_constants(node):
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    }


PRODUCTION = production_functions(
    "_finite_float_or_none",
    "_read_json_dict",
    "null_seed_table_covers_hwp_angles",
    "resolve_calibration_pixel",
    "ordered_pixel_work_queue",
)


class CalibrationPixelSelectionTests(unittest.TestCase):
    def test_explicit_physical_pixel_is_measured_first(self):
        pixel, selected = PRODUCTION["resolve_calibration_pixel"](
            [4, 8, 55, 80],
            55,
            100,
        )
        self.assertEqual(pixel, 55)
        self.assertEqual(selected, [55, 4, 8, 80])
        queue = PRODUCTION["ordered_pixel_work_queue"](
            selected,
            {2, 80},
            first_pixel=pixel,
        )
        self.assertEqual(queue, [55, 2, 4, 8, 80])

    def test_omitted_pixel_keeps_backward_compatible_first_selection(self):
        pixel, selected = PRODUCTION["resolve_calibration_pixel"](
            [4, 8, 55],
            None,
            100,
        )
        self.assertEqual((pixel, selected), (4, [4, 8, 55]))

    def test_new_run_rejects_calibration_pixel_outside_map_selection(self):
        with self.assertRaisesRegex(ValueError, "not in the chip-map Pixels"):
            PRODUCTION["resolve_calibration_pixel"](
                [4, 8, 55],
                50,
                100,
            )

    def test_resume_can_reinsert_saved_calibration_pixel_for_skip_check(self):
        pixel, selected = PRODUCTION["resolve_calibration_pixel"](
            [8, 55],
            4,
            100,
            include_if_missing=True,
        )
        self.assertEqual((pixel, selected), (4, [4, 8, 55]))


class CalibrationSeedResumeTests(unittest.TestCase):
    def test_empty_or_partial_seed_file_is_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "null_seed_by_hwp.json"
            path.write_text("{}", encoding="utf-8")
            self.assertFalse(
                PRODUCTION["null_seed_table_covers_hwp_angles"](
                    path,
                    [0.0, 10.0],
                )
            )
            path.write_text(
                json.dumps({
                    "0.000": {"q_null_deg": 1.0, "a_null_deg": 2.0},
                }),
                encoding="utf-8",
            )
            self.assertFalse(
                PRODUCTION["null_seed_table_covers_hwp_angles"](
                    path,
                    [0.0, 10.0],
                )
            )

    def test_complete_seed_file_covers_every_requested_angle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "null_seed_by_hwp.json"
            path.write_text(
                json.dumps({
                    "0.000": {"q_null_deg": 1.0, "a_null_deg": 2.0},
                    "10.000": {"q_null_deg": 3.0, "a_null_deg": 4.0},
                }),
                encoding="utf-8",
            )
            self.assertTrue(
                PRODUCTION["null_seed_table_covers_hwp_angles"](
                    path,
                    [0.0, 10.0],
                )
            )


class CalibrationPixelGuiWiringTests(unittest.TestCase):
    def test_gui_exposes_and_passes_physical_calibration_pixel(self):
        make_vars = class_method("FastMapGuiApp", "_make_vars")
        controls = class_method("FastMapGuiApp", "_build_controls")
        child_argv = class_method("FastMapGuiApp", "_build_child_argv")
        resume = class_method("FastMapGuiApp", "_apply_resume_defaults")

        self.assertIn("calibration_pixel", string_constants(make_vars))
        self.assertIn("Calibration pixel", string_constants(controls))
        self.assertIn("--calibration-pixel", string_constants(child_argv))
        self.assertIn("calibration_pixel", string_constants(resume))
        self.assertIn("null_seed_complete", string_constants(resume))


if __name__ == "__main__":
    unittest.main()
