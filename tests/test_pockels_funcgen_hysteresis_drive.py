import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
from pathlib import Path
import types
import unittest


SOURCE_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
SMU_SOURCE_PATH = _bootstrap.module_path("smu4201_iv_sweep.py")
SMU_SOURCE_TREE = ast.parse(SMU_SOURCE_PATH.read_text(encoding="utf-8"))


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
        "FUNCGEN_CH_SWEEP": 1,
        "time": types.SimpleNamespace(sleep=lambda _seconds: None),
    }

    def funcgen_send(instrument, command):
        instrument.write(command)

    def funcgen_set_voltage(instrument, vpp):
        instrument.write(f"AMPL {float(vpp):g}")

    namespace.update(
        {
            "funcgen_send": funcgen_send,
            "funcgen_set_voltage": funcgen_set_voltage,
        }
    )
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = production_functions(
    "_funcgen_query_int",
    "_funcgen_assert_no_execution_error",
    "_funcgen_set_sweep_output",
    "_prepare_hysteresis_ac_drive",
)


def top_level_function(name):
    for node in SOURCE_TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing production function: {name}")


def call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


class FakeTGF3162:
    def __init__(self, *, overload_on_enable=False):
        self.commands = []
        self.channel = 2
        self.execution_error = 0
        self.overload_on_enable = bool(overload_on_enable)

    def write(self, command):
        self.commands.append(command)
        if command.startswith("CHN "):
            self.channel = int(command.split()[1])
        elif command == "OUTPUT ON" and self.overload_on_enable:
            self.execution_error = -80

    def query(self, command):
        self.commands.append(command)
        if command == "CHN?":
            return str(self.channel)
        if command == "EER?":
            result = self.execution_error
            self.execution_error = 0
            return str(result)
        raise AssertionError(f"Unexpected query: {command}")


class HysteresisAcDriveTests(unittest.TestCase):
    def test_final_amplitude_is_programmed_and_output_stays_off(self):
        instrument = FakeTGF3162()

        PRODUCTION["_prepare_hysteresis_ac_drive"](instrument, 9.0)

        writes = [
            command
            for command in instrument.commands
            if command not in {"EER?", "CHN?"}
        ]
        self.assertEqual(
            writes,
            ["CHN 1", "OUTPUT OFF", "AMPL 9"],
        )

    def test_measurement_window_output_overload_is_fatal(self):
        instrument = FakeTGF3162(overload_on_enable=True)
        PRODUCTION["_prepare_hysteresis_ac_drive"](instrument, 9.0)

        with self.assertRaisesRegex(RuntimeError, "-80|overload"):
            PRODUCTION["_funcgen_set_sweep_output"](
                instrument,
                True,
                verify_command=True,
                required=True,
                context="test hysteresis measurement window",
            )


class HysteresisPointSequenceTests(unittest.TestCase):
    def test_entry_forces_ac_off_before_smu_output_is_touched(self):
        run_node = top_level_function("run_dc_hysteresis_sweep")
        calls = [
            node
            for node in ast.walk(run_node)
            if isinstance(node, ast.Call)
        ]
        verified_off_calls = [
            call
            for call in calls
            if call_name(call) == "_funcgen_set_sweep_output"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value is False
            and any(
                keyword.arg == "context"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "hysteresis entry interlock"
                for keyword in call.keywords
            )
        ]
        smu_output_calls = [
            call for call in calls if call_name(call) == "smu_ensure_output_on"
        ]

        self.assertEqual(len(verified_off_calls), 1)
        self.assertEqual(len(smu_output_calls), 1)
        self.assertLess(
            verified_off_calls[0].lineno,
            smu_output_calls[0].lineno,
        )

    def test_dc_dwell_and_smu_snapshot_precede_short_ac_measurement_window(self):
        run_node = top_level_function("run_dc_hysteresis_sweep")
        point_loops = [
            node
            for node in ast.walk(run_node)
            if isinstance(node, ast.For)
            and isinstance(node.iter, ast.Call)
            and call_name(node.iter) == "enumerate"
            and ast.unparse(node.iter)
            == "enumerate(zip(voltages, branches, cycle_nums))"
        ]
        self.assertEqual(len(point_loops), 1)
        point_loop = point_loops[0]

        calls = [
            node
            for node in ast.walk(point_loop)
            if isinstance(node, ast.Call)
        ]
        poling_sleeps = [
            call
            for call in calls
            if call_name(call) == "_hysteresis_interruptible_sleep"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "poling_dwell_s"
        ]
        smu_snapshots = [
            call for call in calls if call_name(call) == "measure_primary"
        ]
        smu_voltage_snapshots = [
            call for call in calls if call_name(call) == "measure_secondary"
        ]
        lockin_reads = [
            call for call in calls if call_name(call) == "read_lockin_averaged"
        ]
        output_calls = [
            call
            for call in calls
            if call_name(call) == "_funcgen_set_sweep_output"
            and len(call.args) >= 2
            and isinstance(call.args[1], ast.Constant)
            and isinstance(call.args[1].value, bool)
        ]
        enables = [call for call in output_calls if call.args[1].value is True]
        disables = [call for call in output_calls if call.args[1].value is False]
        silent_smu_reenables = [
            call
            for call in calls
            if call_name(call) == "smu_ensure_output_on"
        ]

        self.assertEqual(len(poling_sleeps), 1)
        self.assertEqual(len(smu_snapshots), 1)
        self.assertEqual(len(smu_voltage_snapshots), 1)
        self.assertEqual(len(lockin_reads), 1)
        self.assertEqual(len(enables), 1)
        self.assertEqual(len(disables), 1)
        self.assertEqual(
            silent_smu_reenables,
            [],
            "A dropped SMU output must abort, not silently shorten DC poling",
        )
        self.assertLess(poling_sleeps[0].lineno, smu_snapshots[0].lineno)
        self.assertLess(smu_snapshots[0].lineno, enables[0].lineno)
        self.assertLess(smu_voltage_snapshots[0].lineno, enables[0].lineno)
        self.assertLess(enables[0].lineno, lockin_reads[0].lineno)
        self.assertLess(lockin_reads[0].lineno, disables[0].lineno)

        measurement_guards = [
            node
            for node in ast.walk(point_loop)
            if isinstance(node, ast.Try)
            and enables[0] in set(ast.walk(ast.Module(body=node.body, type_ignores=[])))
            and disables[0] in set(
                ast.walk(ast.Module(body=node.finalbody, type_ignores=[]))
            )
        ]
        self.assertEqual(
            len(measurement_guards),
            1,
            "AC enable/read must be protected by a finally block that turns AC off",
        )


class SmuSecondaryReadbackTests(unittest.TestCase):
    def test_secondary_terminal_voltage_is_configured_and_queried(self):
        smu_class = next(
            node
            for node in SMU_SOURCE_TREE.body
            if isinstance(node, ast.ClassDef) and node.name == "SMU4201"
        )
        methods = {
            node.name: node
            for node in smu_class.body
            if isinstance(node, ast.FunctionDef)
        }
        configure_source = ast.unparse(
            methods["configure_source_voltage_measure_current"]
        )
        measure_source = ast.unparse(methods["measure_secondary"])

        self.assertIn(
            "SOURce:VOLTage:MEASure:SECondary VOLTage",
            configure_source,
        )
        self.assertIn(
            "MEASure:SECondary:LIVEdata?",
            measure_source,
        )


if __name__ == "__main__":
    unittest.main()
