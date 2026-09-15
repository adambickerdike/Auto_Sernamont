import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import json
import math
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest


AUTO_PATH = _bootstrap.module_path("pockels_full_automation.py")
GUI_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")


def load_functions(path: Path, names: set[str], namespace: dict) -> dict:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    missing = names - {node.name for node in nodes}
    if missing:
        raise AssertionError(f"Missing production functions: {sorted(missing)}")
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"),
        namespace,
    )
    return namespace


AUTO = load_functions(
    AUTO_PATH,
    {
        "sanitize_run_name",
        "substrate_calibration_run_identity",
        "substrate_calibration_alias_filename",
        "save_substrate_calibration_snapshot",
    },
    {
        "Path": Path,
        "json": json,
        "os": os,
        "re": __import__("re"),
        "datetime": datetime,
        "SUBSTRATE_CAL_FILENAME": "substrate_calibration.json",
    },
)

GUI = load_functions(
    GUI_PATH,
    {
        "_finite_float_or_none",
        "substrate_calibration_info",
        "substrate_calibration_sort_key",
        "format_substrate_calibration_label",
        "find_latest_substrate_calibration",
    },
    {
        "Path": Path,
        "json": json,
        "math": math,
        "datetime": datetime,
        "auto": SimpleNamespace(
            SUBSTRATE_CAL_FILENAME="substrate_calibration.json",
            OUTDIR_ROOT="pockels_full_automation",
        ),
        "OUTDIR_ROOT": "pockels_fast_map",
    },
)


class SubstrateCalibrationLabelTests(unittest.TestCase):
    def test_final_snapshot_has_canonical_and_date_chip_run_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "20260901_142300_CHIP-42_initial-scout"
            run_dir.mkdir()
            (run_dir / "run_config.json").write_text(
                json.dumps({"chip_id": "CHIP-42", "run_name": "initial-scout"}),
                encoding="utf-8",
            )
            angles = [float(value) for value in range(9)]
            rows = [{"hwp_deg": value} for value in angles]
            table = {
                f"{value:.3f}": {
                    "q_null_deg": value + 10.0,
                    "a_null_deg": value + 20.0,
                }
                for value in angles
            }
            alias = AUTO["substrate_calibration_alias_filename"](run_dir)
            paths = AUTO["save_substrate_calibration_snapshot"](
                run_dir,
                rows,
                table,
                "substrate_calibration.json",
                alias_filenames=(alias,),
            )

            self.assertEqual(
                alias,
                "20260901_142300_CHIP-42_initial-scout_substrate_calibration.json",
            )
            self.assertEqual(len(paths), 2)
            canonical = json.loads(paths[0].read_text(encoding="utf-8"))
            labelled = json.loads(paths[1].read_text(encoding="utf-8"))
            self.assertEqual(canonical, labelled)
            self.assertEqual(canonical["chip_id"], "CHIP-42")
            self.assertEqual(canonical["run_name"], "initial-scout")
            self.assertEqual(canonical["n_hwp"], 9)
            self.assertEqual(canonical["hwp_angles_deg"], angles)

            label = GUI["format_substrate_calibration_label"](paths[0])
            self.assertIn("Chip CHIP-42", label)
            self.assertIn("Run initial-scout", label)
            self.assertIn("9 HWP", label)
            self.assertIn("raw 0, 1, 2, 3, 4, 5, 6, 7, 8 deg", label)

    def test_find_latest_uses_embedded_calibration_time_not_copy_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "pockels_fast_map" / "older" / "substrate_calibration.json"
            newer = root / "pockels_fast_map" / "newer" / "substrate_calibration.json"
            older.parent.mkdir(parents=True)
            newer.parent.mkdir(parents=True)
            older.write_text(
                json.dumps({"timestamp": "2026-09-01T10:00:00", "n_hwp": 9}),
                encoding="utf-8",
            )
            newer.write_text(
                json.dumps({"timestamp": "2026-09-01T11:00:00", "n_hwp": 9}),
                encoding="utf-8",
            )
            # Make the older calibration look newer on disk, as can happen
            # after copying a historical run folder.
            os.utime(older, (2_000_000_000, 2_000_000_000))
            os.utime(newer, (1_900_000_000, 1_900_000_000))

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                latest = GUI["find_latest_substrate_calibration"]()
            finally:
                os.chdir(previous_cwd)
            self.assertEqual(latest, newer.resolve())


if __name__ == "__main__":
    unittest.main()
