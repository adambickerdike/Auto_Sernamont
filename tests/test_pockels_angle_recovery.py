"""Exercise production angle recovery without loading Windows/Kinesis drivers."""

import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import contextlib
import copy
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np
import serial

from test_pockels_transport_failfast import GUI_PATH, GUI_TREE, load_transport_policy, top_level_node


def production_angles():
    ns = load_transport_policy()
    constants = {"ANGLE_READBACK_SAMPLES", "ANGLE_READBACK_DELAY_S",
                 "ANGLE_RECOVERY_RETRIES", "ANGLE_RECOVERY_PAUSE_S"}
    for node in GUI_TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in constants:
                    ns[target.id] = ast.literal_eval(node.value)
    ns.update(
        np=np, gui_pixel_checkpoint=Mock(), gui_sleep=Mock(), gui_update_state=Mock(),
        pockels=SimpleNamespace(
            MOVE_SETTLE_S=0.1,
            angular_error=lambda a, b: abs((a - b + 180) % 360 - 180),
            safe_move_wrap=lambda rot, target, **kwargs: rot.set_angle(target, **kwargs),
        ),
    )
    names = {
        "wrap360", "shortest_delta_deg", "_gui_float", "_rotator_float_or_nan",
        "_call_rotator_move_variants", "rotator_direct_set_angle", "rotator_safe_move",
        "rotator_read_angle_once", "rotator_verified_readback", "rotator_verified_move_result",
        "rotator_move_needs_boundary_safe_path", "fast_rotator_move_or_fallback",
        "verify_renull_landing", "require_verified_move", "CriticalAngleError", "SkipPixelRequested",
    }
    nodes = [node for node in GUI_TREE.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    assert {node.name for node in nodes} == names
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(GUI_PATH), "exec"), ns)
    return ns


class Rotator:
    def __init__(self, angle, misses=0):
        self.angle = angle
        self.moves = []
        self.misses = misses

    def get_angle(self):
        return self.angle

    def set_angle(self, target, **kwargs):
        self.moves.append(target)
        if self.misses > 0:
            self.misses -= 1
        else:
            self.angle = target

    def shift_angle(self, delta, **kwargs):
        self.set_angle(self.angle + delta, **kwargs)


class AngleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.ns = production_angles()
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def test_reported_qwp_miss_is_corrected_without_failing_the_pixel(self):
        qwp, anl = Rotator(139.42), Rotator(78.5)
        q_move, a_move = self.ns["verify_renull_landing"](
            qwp, anl, 136.65, 78.5, 0.5, label="adaptive null",
        )
        self.assertEqual(qwp.moves, [136.65])
        self.assertEqual(anl.moves, [])
        self.assertTrue(q_move["ok"])
        self.assertTrue(q_move["corrected"])
        self.assertTrue(a_move["ok"])
        self.assertAlmostEqual(q_move["actual_deg"], 136.65)

    def test_in_tolerance_motor_is_not_moved_again(self):
        qwp, anl = Rotator(136.65), Rotator(78.5)
        results = self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="fast null")
        self.assertTrue(all(move["ok"] for move in results))
        self.assertFalse(qwp.moves or anl.moves)

    def test_analyser_miss_is_corrected_as_well(self):
        qwp, anl = Rotator(136.65), Rotator(81.3)
        _, move = self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="fast null")
        self.assertEqual(anl.moves, [78.5])
        self.assertTrue(move["ok"])

    def test_bounded_retries_recover_a_motor_that_initially_misses_twice(self):
        qwp, anl = Rotator(139.42, misses=2), Rotator(78.5)
        move, _ = self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="adaptive null")
        self.assertTrue(move["ok"])
        self.assertEqual(len(qwp.moves), 3)
        self.assertEqual(move["recovery_attempts"], 1)

    def test_stuck_motor_remains_invalid_after_bounded_retries(self):
        qwp, anl = Rotator(139.42, misses=100), Rotator(78.5)
        with self.assertRaisesRegex(self.ns["CriticalAngleError"], "QWP adaptive null"):
            self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="adaptive null")
        self.assertEqual(len(qwp.moves), 2 + self.ns["ANGLE_RECOVERY_RETRIES"])
        self.assertEqual(qwp.angle, 139.42)

    def test_transport_loss_during_correction_is_not_retried_on_dead_handle(self):
        qwp, anl = Rotator(139.42), Rotator(78.5)
        qwp.set_angle = Mock(side_effect=serial.SerialException("WriteFile failed (Access is denied)"))
        with self.assertRaises(self.ns["CriticalHardwareTransportError"]):
            self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="adaptive null")
        qwp.set_angle.assert_called_once()

    def test_stop_and_skip_interrupt_local_recovery(self):
        for signal in (KeyboardInterrupt, self.ns["SkipPixelRequested"]):
            qwp, anl = Rotator(139.42), Rotator(78.5)
            self.ns["gui_pixel_checkpoint"].side_effect = signal("operator request")
            with self.subTest(signal=signal.__name__), self.assertRaises(signal):
                self.ns["verify_renull_landing"](qwp, anl, 136.65, 78.5, 0.5, label="adaptive null")
            self.assertEqual(qwp.moves, [])

    def test_verification_rejects_early_good_reads_followed_by_drift(self):
        rot = SimpleNamespace(get_angle=Mock(side_effect=[136.65, 136.65, 139.42]))
        move = self.ns["rotator_verified_move_result"](rot, 136.65, 0.5)
        self.assertFalse(move["ok"])
        self.assertEqual(move["actual_deg"], 139.42)

    def test_stale_first_read_is_tolerated_after_consecutive_good_reads(self):
        rot = SimpleNamespace(get_angle=Mock(side_effect=[139.42, 136.65, 136.65]))
        move = self.ns["rotator_verified_move_result"](rot, 136.65, 0.5)
        self.assertTrue(move["ok"])
        self.assertEqual(move["readback_ok_samples"], 2)

    def test_a_single_good_read_does_not_certify_missing_telemetry(self):
        rot = SimpleNamespace(get_angle=Mock(side_effect=[None, None, 136.65]))
        self.assertFalse(self.ns["rotator_verified_move_result"](rot, 136.65, 0.5)["ok"])

    def test_correction_preserves_the_safe_path_at_wrap_boundary(self):
        rot = Rotator(359.0, misses=1)
        safe_move = Mock(side_effect=lambda rot, target, *args, **kwargs: rot.set_angle(target))
        self.ns["rotator_safe_move"] = safe_move
        self.ns["rotator_direct_set_angle"] = Mock(side_effect=AssertionError("unsafe direct retry"))
        move = self.ns["fast_rotator_move_or_fallback"](rot, 1.0, fast=True, verify=True)
        self.assertTrue(move["ok"])
        self.assertEqual(safe_move.call_count, 2)


class AngleRecoveryWiringTests(unittest.TestCase):
    def test_adaptive_and_fast_null_paths_use_verified_landing(self):
        run = top_level_node(GUI_TREE, "run_fast_map_for_pixel")
        calls = [node for node in ast.walk(run) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "verify_renull_landing"]
        self.assertEqual({kw.value.value for node in calls for kw in node.keywords if kw.arg == "label"},
                         {"adaptive null", "fast null"})
        adaptive_try = next(node for node in ast.walk(run) if isinstance(node, ast.Try)
                            and any(isinstance(stmt, ast.Assign)
                                    and isinstance(stmt.value, ast.Call)
                                    and isinstance(stmt.value.func, ast.Name)
                                    and stmt.value.func.id == "verify_renull_landing"
                                    and any(kw.arg == "label" and kw.value.value == "adaptive null"
                                            for kw in stmt.value.keywords)
                                    for stmt in node.body))
        # The detector check must occur after a corrected landing. Returning
        # the optimizer's power from the old angle would invalidate the null.
        check = next(node for node in ast.walk(adaptive_try) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "read_null_check")
        landing = next(node for node in calls if node.lineno < check.lineno)
        self.assertLess(landing.lineno, check.lineno)

    def test_every_followup_unwinds_angle_fault_before_the_next_component(self):
        main = top_level_node(GUI_TREE, "main")
        candidates = [node for node in ast.walk(main) if isinstance(node, ast.Try)
                      and any(isinstance(handler.type, ast.Tuple)
                              and "CriticalAngleError" in ast.unparse(handler.type)
                              for handler in node.handlers)]
        self.assertEqual(len(candidates), 6)
        ns = production_angles()
        for source in candidates:
            tree = ast.parse("try:\n    raise CriticalAngleError('missed target')\nexcept Exception:\n    pass\n")
            tree.body[0].handlers = copy.deepcopy(source.handlers)
            with self.subTest(line=source.lineno), self.assertRaises(ns["CriticalAngleError"]):
                exec(compile(ast.fix_missing_locations(tree), "followup_angle_recovery", "exec"), ns)


if __name__ == "__main__":
    unittest.main()
