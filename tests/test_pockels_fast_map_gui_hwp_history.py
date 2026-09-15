import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
from pathlib import Path
import time
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
        "time": time,
        "theta_i_from_hwp": lambda hwp: (2.0 * float(hwp)) % 180.0,
    }
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE_PATH), "exec"),
        namespace,
    )
    return namespace


PRODUCTION = production_functions(
    "_gui_float",
    "_gui_int",
    "lockin_history_key_value",
    "record_lockin_history_row",
    "best_available_hwp_response",
    "unwrap_hwp_sweep_degrees",
)


class HwpHistoryTests(unittest.TestCase):
    def test_saved_auto_peak_rows_populate_all_nine_graph_points(self):
        history = {}
        angles = [
            322.8951,
            334.1451,
            345.3951,
            356.6451,
            7.8951,
            19.1451,
            30.3951,
            41.6451,
            52.8951,
        ]
        magnitudes_uv = [
            72.856,
            56.790,
            15.671,
            66.634,
            101.669,
            76.578,
            19.057,
            43.447,
            81.941,
        ]
        for angle, magnitude_uv in zip(angles, magnitudes_uv):
            stored = PRODUCTION["record_lockin_history_row"](
                history,
                {
                    "pixel": 1,
                    "hwp_deg": angle,
                    "theta_i_deg": (2.0 * angle) % 180.0,
                    "vpp": 9.0,
                    "slope_side": "auto_peak",
                    "lockin_mag_V": magnitude_uv * 1e-6,
                    "lockin_signed_V": -magnitude_uv * 1e-6,
                    "lockin_phase_deg": 153.0,
                },
            )
            self.assertTrue(stored)

        sweep = history[(1, 9.0)]
        self.assertEqual(len(sweep), 9)
        self.assertAlmostEqual(
            sweep[round(7.8951, 6)]["auto_peak"]["mag_uV"],
            101.669,
            places=6,
        )
        self.assertEqual(
            [record["_order"] for record in sweep.values()],
            list(range(9)),
        )

    def test_wrapped_raw_hwp_grid_is_monotonic_in_acquisition_order(self):
        unwrapped = PRODUCTION["unwrap_hwp_sweep_degrees"](
            [322.8951, 334.1451, 345.3951, 356.6451, 7.8951, 19.1451, 52.8951]
        )

        self.assertTrue(all(b > a for a, b in zip(unwrapped, unwrapped[1:])))
        self.assertAlmostEqual(unwrapped[4], 367.8951, places=6)
        self.assertAlmostEqual(unwrapped[-1], 412.8951, places=6)

    def test_mixed_learned_and_plus_minus_rows_keep_one_response_per_hwp(self):
        history = {}
        rows = (
            (322.8951, "learned_anchor", 44.925),
            (334.1451, "plus45", 36.300),
            (334.1451, "minus45", 46.205),
            (345.3951, "plus45", 1.021),
            (345.3951, "minus45", 13.425),
        )
        for angle, side, magnitude_uv in rows:
            PRODUCTION["record_lockin_history_row"](
                history,
                {
                    "pixel": 2,
                    "hwp_deg": angle,
                    "vpp": 9.0,
                    "slope_side": side,
                    "lockin_mag_V": magnitude_uv * 1e-6,
                    "lockin_signed_V": magnitude_uv * 1e-6,
                    "lockin_phase_deg": 0.0,
                    # A failed/high-null certificate must not suppress the
                    # raw live HWP trace.
                    "quality_flags": "high_null_continued;s9_operating_point_failed",
                },
            )

        records = list(history[(2, 9.0)].values())
        selected = [
            PRODUCTION["best_available_hwp_response"](record)
            for record in records
        ]
        self.assertEqual(len(selected), 3)
        self.assertEqual([item["key"] for item in selected], [
            "learned_anchor",
            "minus45",
            "minus45",
        ])
        self.assertEqual(
            [round(item["mag_uV"], 3) for item in selected],
            [44.925, 46.205, 13.425],
        )


if __name__ == "__main__":
    unittest.main()
