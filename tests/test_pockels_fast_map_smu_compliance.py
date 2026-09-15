import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np


FAST_MAP_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
FAST_MAP_TREE = ast.parse(FAST_MAP_PATH.read_text(encoding="utf-8"))
CALIBRATION_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
CALIBRATION_TREE = ast.parse(CALIBRATION_PATH.read_text(encoding="utf-8"))


def literal_assignment(tree, name):
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing assignment: {name}")


def production_functions(*names, namespace):
    wanted = set(names)
    nodes = [
        node
        for node in FAST_MAP_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    missing = wanted - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(FAST_MAP_PATH), "exec"),
        namespace,
    )
    return namespace


def class_method(tree, class_name, method_name):
    class_node = next(
        node
        for node in tree.body
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


class SmuComplianceConversionTests(unittest.TestCase):
    def setUp(self):
        self.production = production_functions(
            "_finite_float_or_none",
            "validate_smu_compliance_a",
            "smu_compliance_a_from_ma",
            namespace={"math": math},
        )

    def test_shared_hardware_default_is_one_milliamp(self):
        self.assertEqual(
            literal_assignment(CALIBRATION_TREE, "SMU_COMPLIANCE_A"),
            1e-3,
        )

    def test_gui_milliamps_are_converted_to_instrument_amperes(self):
        convert = self.production["smu_compliance_a_from_ma"]
        self.assertAlmostEqual(convert("1"), 0.001)
        self.assertAlmostEqual(convert("2.5"), 0.0025)

    def test_non_positive_or_non_finite_compliance_is_rejected(self):
        convert = self.production["smu_compliance_a_from_ma"]
        for value in ("", "abc", "0", "-1", "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    convert(value)


class SmuComplianceRuntimeTests(unittest.TestCase):
    def test_fast_map_applies_validated_value_before_hardware_setup(self):
        pockels = SimpleNamespace()
        calls = []

        class FakeAuto:
            def configure_shared_measurement_globals(self, args):
                calls.append(float(args.smu_compliance))
                pockels.SMU_COMPLIANCE_A = float(args.smu_compliance)
                return [9.0]

        namespace = production_functions(
            "_finite_float_or_none",
            "validate_smu_compliance_a",
            "configure_fast_globals",
            namespace={
                "math": math,
                "np": np,
                "auto": FakeAuto(),
                "pockels": pockels,
                "AUTO_PEAK_REFINE_MODE": "two-point",
                "BTO_PHYSICS_CONFIG": {},
                "minimum_lockin_settle_s": lambda *_args: 0.0,
                "bto_physics_config_from_args": lambda _args: {},
            },
        )
        args = SimpleNamespace(
            smu_compliance=0.0025,
            peak_refine_mode="two-point",
            stage_brighten_offset=45.0,
            substrate_null_tol=0.05,
            substrate_null_max_evals=220,
            null_check_max_mv=14.5,
            null_certify_margin_mv=1.5,
            lockin_tc_index=14,
            lockin_filter_slope_db=12,
            lockin_settle=2.0,
            lockin_read_delay=2.0,
            lockin_avg_readings=4,
            funcgen_settle=0.2,
        )

        namespace["configure_fast_globals"](args)

        self.assertEqual(calls, [0.0025])
        self.assertEqual(pockels.SMU_COMPLIANCE_A, 0.0025)

        args.smu_compliance = 0.0
        with self.assertRaises(ValueError):
            namespace["configure_fast_globals"](args)
        self.assertEqual(calls, [0.0025])


class SmuComplianceGuiWiringTests(unittest.TestCase):
    def test_setting_reaches_main_and_queued_followup_workers(self):
        for method_name in ("_build_child_argv", "_build_followup_jobs"):
            with self.subTest(method=method_name):
                method = class_method(FAST_MAP_TREE, "FastMapGuiApp", method_name)
                self.assertIn("--smu-compliance", string_constants(method))

    def test_setting_is_visible_and_restored_from_run_metadata(self):
        make_vars = class_method(FAST_MAP_TREE, "FastMapGuiApp", "_make_vars")
        controls = class_method(FAST_MAP_TREE, "FastMapGuiApp", "_build_controls")
        resume = class_method(FAST_MAP_TREE, "FastMapGuiApp", "_apply_resume_defaults")

        self.assertIn("smu_compliance_ma", string_constants(make_vars))
        self.assertIn("SMU compliance [mA]", string_constants(controls))
        self.assertIn("smu_compliance_A", string_constants(resume))
        self.assertIn("smu_compliance_mA", string_constants(resume))

    def test_standalone_followup_backend_accepts_the_setting(self):
        main_node = next(
            node
            for node in CALIBRATION_TREE.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        )
        self.assertIn("--smu-compliance", string_constants(main_node))


if __name__ == "__main__":
    unittest.main()
