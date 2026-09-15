import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import io
from pathlib import Path
import types
import unittest


SOURCE_PATH = _bootstrap.module_path("Pockels_Calibration_2026.py")
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
        "time": types.SimpleNamespace(sleep=lambda _seconds: None),
        "smu_ramp_to": lambda smu, _v_from, _v_to: smu.set_voltage(0.0),
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = production_functions("_smu_set_hv", "smu_safe_shutdown")


class FakeSMU:
    def __init__(self, error_batches, hv_state="0", fail_hv_write=False):
        self.error_batches = list(error_batches)
        self.hv_state = str(hv_state)
        self.fail_hv_write = bool(fail_hv_write)
        self.commands = []

    def check_errors(self):
        if not self.error_batches:
            return []
        return self.error_batches.pop(0)

    def write(self, command):
        self.commands.append(("write", command))
        if command == "SYSTem:MODE:HV:STATe OFF" and self.fail_hv_write:
            raise RuntimeError("simulated HV write failure")

    def query(self, command):
        self.commands.append(("query", command))
        if command == "SOURce:VOLTage:FIXed:LEVel?":
            return "0"
        if command == "OUTPut:STATe?":
            return "0"
        if command == "SYSTem:MODE:HV:STATe?":
            return self.hv_state
        raise AssertionError(f"Unexpected query: {command}")

    def output(self, on):
        self.commands.append(("output", bool(on)))

    def set_voltage(self, voltage):
        self.commands.append(("voltage", float(voltage)))

    def close(self):
        self.commands.append(("close",))


class SmuShutdownTests(unittest.TestCase):
    def test_stale_queue_is_not_mislabeled_as_hv_off_failure(self):
        smu = FakeSMU(
            [[
                '103,"Data out of range"',
                '104,"Command execution error"',
            ], []]
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            PRODUCTION["smu_safe_shutdown"](smu)

        text = output.getvalue()
        self.assertIn("queued before HV-state OFF", text)
        self.assertIn("not caused by the HV command", text)
        self.assertNotIn("HV-state OFF errors", text)
        self.assertLess(
            smu.commands.index(
                ("write", "SOURce:VOLTage:MEASure:COUNt:INFinite OFF")
            ),
            smu.commands.index(("write", "SYSTem:MODE:HV:STATe OFF")),
        )

    def test_true_hv_off_error_and_failed_readback_remain_visible(self):
        smu = FakeSMU([[], ['104,"Command execution error"']], hv_state="1")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            PRODUCTION["smu_safe_shutdown"](smu)

        text = output.getvalue()
        self.assertIn("HV-state OFF errors", text)
        self.assertIn("HV mode remained enabled", text)

    def test_hv_off_exception_is_visible_and_connection_still_closes(self):
        smu = FakeSMU([[]], fail_hv_write=True)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            PRODUCTION["smu_safe_shutdown"](smu)

        self.assertIn("HV-state OFF failed during shutdown", output.getvalue())
        self.assertIn(("close",), smu.commands)


if __name__ == "__main__":
    unittest.main()
