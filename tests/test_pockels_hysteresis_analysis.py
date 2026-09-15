#!/usr/bin/env python3
"""Unit tests for pockels_hysteresis_analysis (synthetic loops, no hardware).

Run:  python3 test_pockels_hysteresis_analysis.py
"""

import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import math
import os
import tempfile
import unittest

import numpy as np

from pockels_hysteresis_analysis import (
    analyse_loop_file,
    detect_coercive_windows,
    load_dc_hysteresis_csv,
    saturation_phase_deg,
    signed_projection,
)

VMAX = 50.0
STEP = 2.5


def _grid():
    return np.round(np.arange(-VMAX, VMAX + STEP / 2, STEP), 6)


def _branch_S(V, going_up, *, s_sat, width, imprint, frozen, w,
              hysterons=None, linear_slope=0.0):
    """Hysteron model: ascending branch switches at v_c_plus, descending at
    v_c_minus. hysterons=[(s_sat, width, imprint, w), ...] superposes
    several hysterons (two with opposite internal bias -> pinched loop);
    linear_slope adds a reversible (loop-free) linear response."""
    V = np.asarray(V, float)
    if hysterons is None:
        hysterons = [(s_sat, width, imprint, w)]
    S = np.full(V.shape, float(frozen)) + float(linear_slope) * V
    for (s_i, width_i, imprint_i, w_i) in hysterons:
        v_c = imprint_i + (width_i / 2.0 if going_up else -width_i / 2.0)
        S = S + s_i * np.tanh((V - v_c) / w_i)
    return S


def synth_rows(*, s_sat=40e-6, width=16.0, imprint=3.0, frozen=0.0,
               w=1.5, phi0_deg=37.0, pickup=(0.0, 0.0), noise=0.0,
               cycles=1, seed=0, hysterons=None, linear_slope=0.0):
    """Build (V, branch, cycle, X, Y) arrays along the engine trajectory."""
    rng = np.random.default_rng(seed)
    pts = _grid()
    leg_down = pts[pts < VMAX][::-1]
    leg_up = pts[pts > -VMAX]

    kw = dict(s_sat=s_sat, width=width, imprint=imprint, frozen=frozen, w=w,
              hysterons=hysterons, linear_slope=linear_slope)
    V, branch, cyc, S = [], [], [], []
    # lead-in: saturated at +Vmax
    V.append(VMAX)
    branch.append("sat")
    cyc.append(1)
    S.append(_branch_S([VMAX], True, **kw)[0])
    for c in range(1, cycles + 1):
        sfx = str(c) if cycles > 1 else ""
        V.extend(leg_down)
        branch.extend(["down" + sfx] * len(leg_down))
        cyc.extend([c] * len(leg_down))
        S.extend(_branch_S(leg_down, False, **kw))
        V.extend(leg_up)
        branch.extend(["up" + sfx] * len(leg_up))
        cyc.extend([c] * len(leg_up))
        S.extend(_branch_S(leg_up, True, **kw))

    V = np.array(V)
    S = np.array(S)
    phi = math.radians(phi0_deg)
    X = S * math.cos(phi) + pickup[0] + rng.normal(0, noise, len(S))
    Y = S * math.sin(phi) + pickup[1] + rng.normal(0, noise, len(S))
    return V, branch, np.array(cyc), X, Y


def write_csv(path, V, branch, cyc, X, Y, *, v2_columns=True,
              tripped=None, i_smu=None, metadata=None, p_dc=None,
              lockin_input_overload=None, lockin_output_overload=None):
    R = np.hypot(X, Y)
    phase = np.degrees(np.arctan2(Y, X))
    tripped = np.zeros(len(V), int) if tripped is None else tripped
    i_smu = np.full(len(V), 1e-9) if i_smu is None else i_smu
    p_dc = np.full(len(V), 1e-3) if p_dc is None else np.asarray(p_dc, float)
    lockin_input_overload = (
        np.zeros(len(V), int)
        if lockin_input_overload is None else np.asarray(lockin_input_overload, int)
    )
    lockin_output_overload = (
        np.zeros(len(V), int)
        if lockin_output_overload is None else np.asarray(lockin_output_overload, int)
    )
    with open(path, "w") as f:
        f.write("# Run=synthetic\n")
        f.write(f"# DC_HYST_VMAX={VMAX}\n")
        for key, value in (metadata or {}).items():
            f.write(f"# {key}={value}\n")
        if v2_columns:
            f.write("idx,t_s,branch,V_dc_V,P_dc_W,LockIn_Mag_V,"
                    "LockIn_Phase_deg,LockIn_Sens_V,LockIn_Mag_Std_V,"
                    "LockIn_Phase_Std_deg,N_ok,cycle,LockIn_X_V,LockIn_Y_V,"
                    "SMU_I_A,SMU_V_read_V,compliance_tripped,AC_Vpp,"
                    "LockIn_Input_Overload,LockIn_Output_Overload_Final\n")
            for i in range(len(V)):
                f.write(f"{i},{i * 5.0:.3f},{branch[i]},{V[i]:.4f},"
                        f"{p_dc[i]:.6e},{R[i]:.6e},{phase[i]:.4f},"
                        f"{1e-4:.6e},{1e-8:.6e},{0.5:.4f},5,"
                        f"{cyc[i]},{X[i]:.6e},{Y[i]:.6e},"
                        f"{i_smu[i]:.6e},{V[i]:.4f},{tripped[i]},3.0,"
                        f"{lockin_input_overload[i]},"
                        f"{lockin_output_overload[i]}\n")
        else:  # legacy v1 column set: mag/phase only, no cycle column
            f.write("idx,t_s,branch,V_dc_V,P_dc_W,LockIn_Mag_V,"
                    "LockIn_Phase_deg,LockIn_Sens_V,LockIn_Mag_Std_V,"
                    "LockIn_Phase_Std_deg,N_ok\n")
            for i in range(len(V)):
                f.write(f"{i},{i * 41.0:.3f},{branch[i]},{V[i]:.4f},"
                        f"{1e-3:.6e},{R[i]:.6e},{phase[i]:.4f},"
                        f"{1e-4:.6e},{1e-8:.6e},{0.5:.4f},15\n")
    return path


class TestSignedProjection(unittest.TestCase):
    def test_phase_reference_and_projection(self):
        V, branch, cyc, X, Y = synth_rows(phi0_deg=37.0)
        phi = saturation_phase_deg(V, X, Y)
        self.assertAlmostEqual(phi, 37.0, delta=1.0)
        S, Q = signed_projection(X, Y, phi)
        self.assertGreater(np.max(S), 30e-6)
        self.assertLess(np.max(np.abs(Q)), 1e-6)

    def test_calibrated_phase_axis_is_reused_without_extra_measurements(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(phi0_deg=37.0)
            write_csv(
                p,
                V,
                branch,
                cyc,
                X,
                Y,
                metadata={"EO_lockin_reference_phase_deg": 38.0},
            )
            result = analyse_loop_file(p)
            self.assertEqual(
                result["phase_reference_source"],
                "calibrated_chip_sweep_eo_axis",
            )
            self.assertAlmostEqual(result["phi_ref_deg"], 38.0, places=6)
            self.assertAlmostEqual(
                result["phase_reference_axis_delta_deg"], 1.0, delta=0.1
            )

    def test_existing_dc_power_rows_report_half_fringe_departure(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(phi0_deg=37.0)
            p_dc = 0.5e-3 + 0.2e-3 * np.sin(np.radians(V * 2.0))
            write_csv(
                p,
                V,
                branch,
                cyc,
                X,
                Y,
                p_dc=p_dc,
                metadata={
                    "DC_fringe_null_power_W": 0.0,
                    "DC_fringe_quadrature_power_W": 0.5e-3,
                    "DC_fringe_bright_power_W": 1.0e-3,
                },
            )
            result = analyse_loop_file(p)
            p_metrics = result["metrics"]["p_dc"]
            self.assertGreater(p_metrics["max_half_fringe_error"], 0.15)
            self.assertIn(
                "dc_quadrature_departure",
                result["classification"]["modifiers"],
            )

    def test_lockin_overload_rows_are_marked_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(phi0_deg=37.0)
            input_overload = np.zeros(len(V), int)
            output_overload = np.zeros(len(V), int)
            input_overload[3] = 1
            output_overload[7] = 1
            write_csv(
                p,
                V,
                branch,
                cyc,
                X,
                Y,
                lockin_input_overload=input_overload,
                lockin_output_overload=output_overload,
            )
            data = load_dc_hysteresis_csv(p)
            self.assertEqual(int(data["lockin_invalid"].sum()), 2)
            result = analyse_loop_file(p)
            self.assertGreaterEqual(
                result["metrics"]["n_lockin_overload_excluded"],
                2,
            )


class TestIdealLoop(unittest.TestCase):
    def _analyse(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(**kw)
            write_csv(p, V, branch, cyc, X, Y)
            return analyse_loop_file(p)

    def test_recovers_loop_parameters(self):
        res = self._analyse(s_sat=40e-6, width=16.0, imprint=3.0,
                            w=1.5, phi0_deg=37.0)
        m = res["metrics"]
        self.assertAlmostEqual(m["v_c_plus_V"], 11.0, delta=0.6)
        self.assertAlmostEqual(m["v_c_minus_V"], -5.0, delta=0.6)
        self.assertAlmostEqual(m["loop_width_V"], 16.0, delta=1.0)
        self.assertAlmostEqual(m["imprint_V"], 3.0, delta=0.6)
        self.assertGreater(m["s_rem_pos_V"], 35e-6)
        self.assertLess(m["s_rem_neg_V"], -35e-6)
        self.assertAlmostEqual(m["switchable_V"], 40e-6, delta=2e-6)
        self.assertAlmostEqual(m["frozen_V"], 0.0, delta=2e-6)
        self.assertGreater(m["squareness_pos"], 0.9)
        self.assertLess(abs(m["sat_asymmetry"]), 0.05)
        self.assertLess(m["quadrature_fraction"], 0.05)
        self.assertAlmostEqual(res["phi_ref_deg"] % 360.0, 37.0, delta=2.0)
        self.assertGreater(m["loop_area_V2"], 0.0)

    def test_tanh_fit_agrees(self):
        res = self._analyse(s_sat=40e-6, width=16.0, imprint=3.0, w=1.5)
        fit_up = res["metrics"]["tanh_fit_up"]
        if fit_up is not None:  # scipy present
            self.assertAlmostEqual(fit_up["v_c_V"], 11.0, delta=0.5)
            self.assertGreater(fit_up["r_squared"], 0.99)

    def test_frozen_component(self):
        res = self._analyse(s_sat=40e-6, frozen=10e-6, width=16.0, imprint=0.0)
        m = res["metrics"]
        self.assertAlmostEqual(m["switchable_V"], 40e-6, delta=3e-6)
        self.assertAlmostEqual(m["frozen_V"], 10e-6, delta=3e-6)
        self.assertGreater(m["sat_asymmetry"], 0.1)

    def test_sign_convention_flip(self):
        # phi0 in the third quadrant: analysis must flip so S(+Vmax) > 0
        res = self._analyse(phi0_deg=210.0)
        m = res["metrics"]
        self.assertGreater(m["s_sat_pos_V"], 30e-6)
        self.assertAlmostEqual(m["v_c_plus_V"], 11.0, delta=0.8)

    def test_noise_robustness(self):
        res = self._analyse(noise=1e-6, seed=42)
        m = res["metrics"]
        self.assertAlmostEqual(m["v_c_plus_V"], 11.0, delta=1.5)
        self.assertAlmostEqual(m["v_c_minus_V"], -5.0, delta=1.5)


class TestLegacyCsv(unittest.TestCase):
    def test_v1_magphase_only(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows()
            write_csv(p, V, branch, cyc, X, Y, v2_columns=False)
            data = load_dc_hysteresis_csv(p)
            self.assertTrue(np.allclose(data["X"], X, atol=1e-9))
            res = analyse_loop_file(p)
            self.assertAlmostEqual(res["metrics"]["v_c_plus_V"], 11.0,
                                   delta=0.6)


class TestMultiCycle(unittest.TestCase):
    def test_last_cycle_used_and_wakeup_reported(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            # cycle 1: width 20; cycle 2: width 14 (wake-up narrowing)
            V1, b1, c1, X1, Y1 = synth_rows(width=20.0, cycles=1)
            V2, b2, c2, X2, Y2 = synth_rows(width=14.0, cycles=1)
            b1 = ["sat" if b == "sat" else b + "1" for b in b1]
            b2 = [b + "2" for b in b2 if b != "sat"]
            V2, X2, Y2 = V2[1:], X2[1:], Y2[1:]  # drop duplicate sat lead
            V = np.concatenate([V1, V2])
            X = np.concatenate([X1, X2])
            Y = np.concatenate([Y1, Y2])
            branch = list(b1) + list(b2)
            cyc = np.concatenate([c1, np.full(len(V2), 2, int)])
            write_csv(p, V, branch, cyc, X, Y)
            res = analyse_loop_file(p)
            self.assertEqual(res["metrics"]["cycle"], 2)
            self.assertAlmostEqual(res["metrics"]["loop_width_V"], 14.0,
                                   delta=1.0)
            self.assertEqual(res["n_cycles_analysed"], 2)
            self.assertAlmostEqual(
                res["cycle_to_cycle"]["loop_width_change_V"], -6.0, delta=1.5)


class TestRobustness(unittest.TestCase):
    def test_tripped_points_excluded(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows()
            tripped = np.zeros(len(V), int)
            for k in (5, 6, 30):  # poison a few rows
                tripped[k] = 1
                X[k], Y[k] = 5e-3, -5e-3
            write_csv(p, V, branch, cyc, X, Y, tripped=tripped)
            res = analyse_loop_file(p)
            m = res["metrics"]
            self.assertAlmostEqual(m["v_c_plus_V"], 11.0, delta=0.8)
            self.assertEqual(m["n_tripped_excluded"], 3)

    def test_leakage_fit(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows()
            G_true = 2e-9  # 2 nS
            i_smu = G_true * V + 1e-10
            write_csv(p, V, branch, cyc, X, Y, i_smu=i_smu)
            res = analyse_loop_file(p)
            leak = res["metrics"]["leakage"]
            self.assertIsNotNone(leak)
            self.assertAlmostEqual(leak["conductance_S"], G_true,
                                   delta=0.2e-9)


class TestClassification(unittest.TestCase):
    def _analyse(self, gap_um=None, alpha=1.0, **kw):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(**kw)
            write_csv(p, V, branch, cyc, X, Y)
            return analyse_loop_file(p, gap_um=gap_um, field_correction=alpha)

    def test_square_loop(self):
        res = self._analyse(w=1.5, noise=0.3e-6)
        self.assertEqual(res["classification"]["primary"],
                         "ferroelectric_square")
        self.assertNotIn("imprinted", res["classification"]["modifiers"])

    def test_slanted_loop(self):
        # w comparable to the half-width: remanence tanh(5/8) ~ 0.55 of sat
        res = self._analyse(w=8.0, width=10.0, imprint=0.0, noise=0.3e-6)
        self.assertEqual(res["classification"]["primary"],
                         "ferroelectric_slanted")

    def test_imprinted_modifier(self):
        res = self._analyse(imprint=10.0, width=16.0, noise=0.3e-6)
        self.assertIn("imprinted", res["classification"]["modifiers"])
        self.assertTrue(res["classification"]["primary"]
                        .startswith("ferroelectric"))

    def test_pinched_loop(self):
        res = self._analyse(
            hysterons=[(20e-6, 10.0, +10.0, 1.5), (20e-6, 10.0, -10.0, 1.5)],
            noise=0.3e-6)
        self.assertEqual(res["classification"]["primary"], "pinched")
        self.assertGreaterEqual(
            res["classification"]["reasons"]["n_opening_humps"], 2)

    def test_linear_no_hysteresis(self):
        res = self._analyse(s_sat=0.0, linear_slope=0.8e-6, noise=0.2e-6)
        self.assertEqual(res["classification"]["primary"],
                         "linear_no_hysteresis")

    def test_no_response(self):
        res = self._analyse(s_sat=0.0, noise=0.15e-6)
        self.assertEqual(res["classification"]["primary"], "no_response")

    def test_frozen_response(self):
        res = self._analyse(s_sat=0.3e-6, frozen=25e-6, noise=0.1e-6)
        self.assertEqual(res["classification"]["primary"], "frozen_response")

    def test_normal_loop_not_pinched_with_noise(self):
        res = self._analyse(w=1.5, noise=1e-6, seed=7)
        self.assertTrue(res["classification"]["primary"]
                        .startswith("ferroelectric"))

    def test_coercive_field_conversion(self):
        res = self._analyse(gap_um=7.0, alpha=0.94, noise=0.2e-6)
        fields = res["fields"]
        # Vc+ = +11 V -> E = 0.94*11/7 = 1.477 V/um = 14.77 kV/cm
        self.assertAlmostEqual(fields["e_c_plus_kV_per_cm"], 14.77, delta=1.0)
        self.assertAlmostEqual(fields["e_imprint_kV_per_cm"],
                               0.94 * 3.0 / 7.0 * 10.0, delta=1.0)


class TestCompositionalExtractables(unittest.TestCase):
    def _analyse(self, **kw):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(**kw)
            write_csv(p, V, branch, cyc, X, Y)
            return analyse_loop_file(p)

    def test_quadratic_eo_slope_recovered(self):
        # hysteron + reversible linear term (field-induced quadratic EO)
        res = self._analyse(linear_slope=0.3e-6, noise=0.05e-6)
        m = res["metrics"]
        self.assertAlmostEqual(m["quad_eo_slope_V_per_V"], 0.3e-6,
                               delta=0.05e-6)
        # corrected switchable = pure hysteron amplitude (linear term removed)
        self.assertAlmostEqual(m["switchable_corrected_V"], 40e-6, delta=3e-6)
        # ratio = |b|*Vmax/|switchable_corrected| = 0.3e-6*50/40e-6 = 0.375
        self.assertAlmostEqual(m["quad_eo_ratio"], 0.375, delta=0.08)

    def test_quad_slope_near_zero_without_linear_term(self):
        res = self._analyse(noise=0.05e-6)
        self.assertAlmostEqual(res["metrics"]["quad_eo_slope_V_per_V"], 0.0,
                               delta=0.05e-6)

    def test_switching_sigma_tracks_disorder(self):
        sharp = self._analyse(w=1.5, noise=0.1e-6)["metrics"]
        broad = self._analyse(w=6.0, width=20.0, noise=0.1e-6)["metrics"]
        self.assertIsNotNone(sharp["switching_sigma_V"])
        self.assertIsNotNone(broad["switching_sigma_V"])
        self.assertGreater(broad["switching_sigma_V"],
                           sharp["switching_sigma_V"])
        # distribution mean should sit near the coercive voltage
        up = broad["switching_slope_up"]
        self.assertAlmostEqual(up["mean_V"], broad["v_c_plus_V"], delta=3.0)

    def test_butterfly_contrast_low_for_clean_switching(self):
        res = self._analyse(w=1.5, noise=0.2e-6)
        m = res["metrics"]
        self.assertIsNotNone(m["butterfly_min_over_sat"])
        self.assertLess(m["butterfly_min_over_sat"], 0.35)

    def test_transition_width_tracks_w(self):
        narrow = self._analyse(w=1.5, noise=0.05e-6)["metrics"]
        wide = self._analyse(w=6.0, width=20.0, noise=0.05e-6)["metrics"]
        self.assertIsNotNone(narrow["transition_width_25_75_V"])
        self.assertIsNotNone(wide["transition_width_25_75_V"])
        self.assertGreater(wide["transition_width_25_75_V"],
                           narrow["transition_width_25_75_V"])
        # analytic: width = 2*atanh(0.5)*w = 1.0986*w
        self.assertAlmostEqual(wide["transition_width_25_75_V"],
                               1.0986 * 6.0, delta=2.5)

    def test_phase_intermediate_fraction_small_for_abrupt_flip(self):
        res = self._analyse(w=1.5, noise=0.2e-6)
        m = res["metrics"]
        frac = m["phase_intermediate_fraction_up"]
        if frac is not None:  # needs enough points in the coercive window
            self.assertLess(frac, 0.4)

    def test_poling_kinetics_fit(self):
        import numpy as np
        from pockels_hysteresis_analysis import fit_poling_kinetics

        rng = np.random.default_rng(5)
        t = np.arange(0.0, 181.0, 10.0)
        tau_true, beta_true = 35.0, 0.8
        m = 40e-6 - (40e-6 - 5e-6) * np.exp(-(t / tau_true) ** beta_true)
        m = m + rng.normal(0, 0.2e-6, len(t))
        fit = fit_poling_kinetics(t, m)
        self.assertAlmostEqual(fit["tau_s"], tau_true, delta=8.0)
        self.assertAlmostEqual(fit["beta"], beta_true, delta=0.25)
        self.assertGreater(fit["r_squared"], 0.99)

    def test_poling_kinetics_underdetermined_raises(self):
        from pockels_hysteresis_analysis import fit_poling_kinetics

        with self.assertRaises(ValueError):
            fit_poling_kinetics([0, 10, 20], [1e-6, 2e-6, 3e-6])


class TestAdaptiveWindows(unittest.TestCase):
    def test_windows_bracket_true_coercive_voltages(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "dc_hysteresis.csv")
            V, branch, cyc, X, Y = synth_rows(width=16.0, imprint=3.0)
            write_csv(p, V, branch, cyc, X, Y)
            windows = detect_coercive_windows(p, half_width_v=7.5, vmax=VMAX)
            self.assertEqual(len(windows), 2)
            (lo1, hi1), (lo2, hi2) = windows
            self.assertLess(lo1, -5.0)
            self.assertGreater(hi1, -5.0)
            self.assertLess(lo2, 11.0)
            self.assertGreater(hi2, 11.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
