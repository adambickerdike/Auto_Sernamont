import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
FOLLOWUP_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
FOLLOWUP_TREE = ast.parse(FOLLOWUP_PATH.read_text(encoding="utf-8"))


def production_constant(name):
    for node in SOURCE_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing production constant: {name}")


def followup_constant(name):
    for node in FOLLOWUP_TREE.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"Missing follow-up constant: {name}")


def production_functions(*names, namespace):
    wanted = set(names)
    nodes = [
        node
        for node in SOURCE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    missing = wanted - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


def followup_functions(*names, namespace):
    wanted = set(names)
    nodes = [
        node
        for node in FOLLOWUP_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    missing = wanted - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing follow-up functions: {sorted(missing)}")
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(FOLLOWUP_PATH), "exec"),
        namespace,
    )
    return namespace


class FakeLockin:
    def __init__(self, overload=0):
        self.overload = overload
        self.calls = []

    def get_overload_byte(self):
        return self.overload

    def set_voltage_input_mode(self):
        self.calls.append(("voltage_input_mode",))

    def set_ac_coupling(self):
        self.calls.append(("ac_coupling",))

    def set_automatic_ac_gain(self, enabled):
        self.calls.append(("automatic_ac_gain", enabled))

    def set_reference_mode_single(self):
        self.calls.append(("reference_mode_single",))

    def set_reference_source(self, index):
        self.calls.append(("reference_source", index))

    def disable_synchronous_filter(self):
        self.calls.append(("synchronous_filter_disabled",))

    def disable_fast_output_mode(self):
        self.calls.append(("fast_output_disabled",))

    def set_sensitivity(self, index):
        self.calls.append(("sensitivity", index))

    def set_time_constant(self, index):
        self.calls.append(("time_constant", index))

    def set_filter_slope(self, index):
        self.calls.append(("filter_slope", index))


class FastMapDefaultTests(unittest.TestCase):
    def test_requested_gui_defaults_are_literal_production_defaults(self):
        self.assertEqual(
            production_constant("DEFAULT_PIXEL_SELECTION"),
            "1-6,11-16,21-26,31-37,41-48,51-100",
        )
        self.assertEqual(production_constant("DEFAULT_LOCKIN_SETTLE_S"), 1.5)
        self.assertEqual(production_constant("DEFAULT_LOCKIN_AVG_READINGS"), 4)
        self.assertEqual(production_constant("DEFAULT_LOCKIN_READ_DELAY_S"), 1.5)
        self.assertIsNone(production_constant("DEFAULT_HYSTERESIS_VOLTAGE_STEP_V"))
        self.assertEqual(
            production_constant("DEFAULT_HYSTERESIS_GRID_PROFILE"),
            "center_dense",
        )
        self.assertEqual(production_constant("DEFAULT_FAST_MAP_AC_VPP"), 9.0)
        self.assertEqual(production_constant("DEFAULT_HYSTERESIS_AC_VPP"), 4.0)
        self.assertFalse(
            production_constant("DEFAULT_HYSTERESIS_DYNAMIC_LOCKIN_RANGE")
        )
        self.assertEqual(followup_constant("DC_HYST_DEFAULT_AC_VPP"), 4.0)
        self.assertEqual(production_constant("DEFAULT_LEARNED_READOUT_MODE"), "triplet")
        self.assertEqual(production_constant("DEFAULT_LOCKIN_SENSITIVITY_INDEX"), 16)

    def test_adaptive_sensitivity_optimizer_is_not_present(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("optimize_lockin_sensitivity", source)
        self.assertNotIn("set_automatic_ac_gain(True)", source)
        self.assertIn('"--fixed-lockin-sensitivity-index"', source)
        self.assertNotIn('["--hyst-ac-vpp", "4.0"]', source)


class FixedLockinRangeTests(unittest.TestCase):
    def setUp(self):
        self.sweep_globals = {"check_overload": object()}
        exec("def run_single_sweep():\n    pass\n", self.sweep_globals)
        self.pockels = SimpleNamespace(
            SENS_TABLE_VOLTS={16: 200e-6},
            LOCKIN_SETTLE_S=2.0,
            LOCKIN_READ_DELAY_S=2.0,
            LOCKIN_AVG_READINGS=4,
            check_overload=object(),
            run_single_sweep=self.sweep_globals["run_single_sweep"],
        )
        self.namespace = production_functions(
            "check_fixed_lockin_overload",
            "install_fixed_lockin_range_policy",
            "lockin_filter_slope_index",
            "lockin_reference_source_index",
            "configure_lockin_for_fast_map",
            namespace={
                "pockels": self.pockels,
                "DSP7230_TIME_CONSTANT_SECONDS": {14: 0.5},
                "time": SimpleNamespace(sleep=lambda _seconds: None),
            },
        )

    def test_overload_is_reported_without_a_sensitivity_write(self):
        lockin = FakeLockin(overload=0x03)
        with contextlib.redirect_stdout(io.StringIO()):
            index, overloaded = self.namespace["check_fixed_lockin_overload"](
                lockin, 14
            )
        self.assertEqual(index, 14)
        self.assertTrue(overloaded)
        self.assertFalse(
            [call for call in lockin.calls if call[0] == "sensitivity"]
        )

    def test_configuration_sets_200uv_once_and_disables_automatic_gain(self):
        lockin = FakeLockin()
        args = SimpleNamespace(
            lockin_tc_index=14,
            lockin_filter_slope_db=12,
            lockin_sensitivity_index=16,
            lockin_reference_source="external-analog",
            funcgen_freq=30000.0,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.namespace["configure_lockin_for_fast_map"](
                lockin, args, simulated=False
            )
        self.assertEqual(
            [call for call in lockin.calls if call[0] == "sensitivity"],
            [("sensitivity", 16)],
        )
        self.assertIn(("automatic_ac_gain", False), lockin.calls)
        self.assertEqual(result["sensitivity_mode"], "fixed")
        self.assertEqual(result["sensitivity_fullscale_v_requested"], 200e-6)
        self.assertIs(
            self.pockels.check_overload,
            self.namespace["check_fixed_lockin_overload"],
        )
        self.assertIs(
            self.sweep_globals["run_single_sweep"].__globals__["check_overload"],
            self.namespace["check_fixed_lockin_overload"],
        )


class StandaloneFollowupFixedRangeTests(unittest.TestCase):
    def test_followup_worker_uses_the_same_fixed_50uv_policy(self):
        sweep_globals = {"check_overload": object()}
        exec("def run_single_sweep():\n    pass\n", sweep_globals)
        namespace = followup_functions(
            "_check_fixed_lockin_overload",
            "configure_fixed_lockin_range",
            namespace={
                "SENS_TABLE_VOLTS": {14: 50e-6},
                "run_single_sweep": sweep_globals["run_single_sweep"],
                "time": SimpleNamespace(sleep=lambda _seconds: None),
                "check_overload": object(),
            },
        )
        lockin = FakeLockin(overload=0x03)
        with contextlib.redirect_stdout(io.StringIO()):
            index = namespace["configure_fixed_lockin_range"](lockin, 3, 14)
            checked_index, overloaded = namespace["_check_fixed_lockin_overload"](
                lockin, index
            )
        fixed_checker = namespace["_check_fixed_lockin_overload"]
        self.assertEqual((checked_index, overloaded), (14, True))
        self.assertEqual(
            [call for call in lockin.calls if call[0] == "sensitivity"],
            [("sensitivity", 14)],
        )
        self.assertIn(("automatic_ac_gain", False), lockin.calls)
        self.assertIs(namespace["check_overload"], fixed_checker)
        self.assertIs(
            sweep_globals["run_single_sweep"].__globals__["check_overload"],
            fixed_checker,
        )


if __name__ == "__main__":
    unittest.main()
