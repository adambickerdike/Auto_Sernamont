import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import ast
import math
from pathlib import Path
import unittest

import numpy as np

from pockels_measurement_analysis import (
    dc_malus_fraction_at_psi,
    joint_senarmont_triplet,
)


SOURCE_PATH = _bootstrap.module_path("pockels_fast_map_gui.py")
SOURCE_TREE = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))


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


PRODUCTION = production_functions(
    "_finite_float_or_none",
    "_first_finite_followup_value",
    "periodic_delta_deg",
    "lockin_complex_from_mag_phase",
    "lockin_peak_metric_value",
    "_phase_for_flip",
    "phase_flip_score_from_rows",
    "side_symmetry_ratio_from_rows",
    "append_quality",
    "append_lockin_range_quality",
    "row_quality_flags",
    "row_has_quality_flag",
    "row_has_angle_failure",
    "_s9_psi_of_row",
    "_s9_raw_phasor_of_row",
    "_s9_quadrature_rows",
    "quick_s9_triplet_certificate",
    namespace={
        "np": np,
        "math": math,
        "LOCKIN_FULLSCALE_WARN_FRACTION": 0.85,
        "LOCKIN_FULLSCALE_FAIL_FRACTION": 0.98,
        "MAX_QWP_OFFSET_FROM_NULL_DEG": 3.0,
        "S9_QUICK_MAX_ANALYSER_QUADRATURE_ERROR_DEG": 2.0,
        "S9_QUICK_MIN_PHASE_FLIP_SCORE": 0.50,
        "S9_QUICK_MIN_SIDE_SYMMETRY_RATIO": 0.50,
        "S9_QUICK_MAX_DC_BALANCE_ERROR": 0.10,
        "S9_LOW_SIGNAL_SNR": 5.0,
        "S9_MAX_NULL_LEAKAGE_FRACTION": 0.05,
        "S9_MAX_DC_NULL_SHIFT_DEG": 2.0,
        "S9_MAX_MIDFRINGE_ERROR": 0.075,
        "S9_MAX_DERIVATIVE_RESIDUAL_FRACTION": 0.20,
        "S9_MAX_TEMPORAL_RANK_FRACTION": 0.15,
        "joint_senarmont_triplet": joint_senarmont_triplet,
        "dc_malus_fraction_at_psi": dc_malus_fraction_at_psi,
    },
)


def triplet_rows(*, minus_phase=190.0, snr=20.0, extra_flag=""):
    common = {
        "q_null_deg": 20.0,
        "qwp_readout_actual_deg": 20.0,
        "a_null_deg": 100.0,
        "anl_null_actual_deg": 100.0,
        "quality_flags": extra_flag,
    }
    p = 1e-6 * complex(
        math.cos(math.radians(35.0)), math.sin(math.radians(35.0))
    )
    plus_eo = 20e-6 * complex(
        math.cos(math.radians(10.0)), math.sin(math.radians(10.0))
    )
    minus_eo = 20e-6 * complex(
        math.cos(math.radians(minus_phase)),
        math.sin(math.radians(minus_phase)),
    )
    z_plus = p + plus_eo
    z_minus = p + minus_eo
    null = {
        **common,
        "anl_actual_deg": 100.0,
        "detector_V": 0.01,
        "lockin_mag_V": abs(p),
        "lockin_phase_deg": math.degrees(math.atan2(p.imag, p.real)),
        "lockin_x_V": p.real,
        "lockin_y_V": p.imag,
    }
    plus = {
        **common,
        "anl_actual_deg": 145.0,
        "detector_V": 0.51,
        "lockin_mag_V": abs(z_plus),
        "lockin_phase_deg": math.degrees(math.atan2(z_plus.imag, z_plus.real)),
        "lockin_x_V": z_plus.real,
        "lockin_y_V": z_plus.imag,
        "lockin_net_mag_V": abs(plus_eo),
        "lockin_net_phase_deg": 10.0,
        "snr_estimate": snr,
    }
    minus = {
        **common,
        "anl_actual_deg": 55.0,
        "detector_V": 0.51,
        "lockin_mag_V": abs(z_minus),
        "lockin_phase_deg": math.degrees(math.atan2(z_minus.imag, z_minus.real)),
        "lockin_x_V": z_minus.real,
        "lockin_y_V": z_minus.imag,
        "lockin_net_mag_V": abs(minus_eo),
        "lockin_net_phase_deg": minus_phase,
        "snr_estimate": snr,
    }
    return null, plus, minus


class LockinRangeCertificateTests(unittest.TestCase):
    def test_near_fullscale_warns_and_at_fullscale_fails(self):
        flags = []
        fraction = PRODUCTION["append_lockin_range_quality"](
            flags, 180e-6, 200e-6
        )
        self.assertAlmostEqual(fraction, 0.9)
        self.assertIn("lockin_range_near_fullscale", flags)
        self.assertNotIn("lockin_range_exceeded", flags)

        flags = []
        fraction = PRODUCTION["append_lockin_range_quality"](
            flags, 200e-6, 200e-6
        )
        self.assertAlmostEqual(fraction, 1.0)
        self.assertIn("lockin_range_near_fullscale", flags)
        self.assertIn("lockin_range_exceeded", flags)


class QuickS9CertificateTests(unittest.TestCase):
    def certify(self, null, plus, minus):
        return PRODUCTION["quick_s9_triplet_certificate"](
            null_reference_row=null,
            slope_rows=[plus, minus],
            a_null=100.0,
        )

    def test_opposite_phase_symmetric_half_fringe_is_certified(self):
        certificate = self.certify(*triplet_rows())
        self.assertEqual(certificate["status"], "certified_triplet")
        self.assertGreater(certificate["phase_flip_score"], 0.99)
        self.assertGreater(certificate["side_symmetry_ratio"], 0.9)
        self.assertAlmostEqual(certificate["dc_balance_error"], 0.0)
        self.assertLess(certificate["derivative_residual_fraction"], 1e-9)
        self.assertLess(certificate["temporal_rank_fraction"], 1e-9)
        self.assertAlmostEqual(certificate["operating_psi_deg"], 45.0)

    def test_same_phase_requires_balanced_fit_instead_of_false_certification(self):
        certificate = self.certify(*triplet_rows(minus_phase=10.0))
        self.assertEqual(certificate["status"], "needs_fit")
        self.assertIn(
            "derivative_alignment_or_symmetry_inconclusive",
            certificate["reason"],
        )

    def test_valid_geometry_at_an_angular_node_does_not_chase_noise(self):
        certificate = self.certify(*triplet_rows(snr=2.0))
        self.assertEqual(certificate["status"], "geometry_only_low_signal")

    def test_lockin_range_failure_is_fail_closed(self):
        certificate = self.certify(
            *triplet_rows(extra_flag="lockin_range_exceeded")
        )
        self.assertEqual(certificate["status"], "failed")
        self.assertIn("lockin_range_invalid", certificate["reason"])


if __name__ == "__main__":
    unittest.main()
