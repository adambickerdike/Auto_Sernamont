import _bootstrap  # noqa: F401  # makes the pockels/ package importable
import math
import unittest

from pockels_measurement_analysis import (
    analyse_fast_map_rows,
    dc_malus_fraction_at_psi,
    derive_rotation_observation,
    effective_pockels_coefficient,
    fit_angular_response,
    fit_dc_malus_response,
    fit_rotation_vs_voltage,
    fit_sinusoid_response,
    joint_senarmont_from_fits,
    joint_senarmont_triplet,
    phasor_statistics,
    qwp_retardance_from_parallel_ratio,
)


class PhasorStatisticsTests(unittest.TestCase):
    def test_opposite_phases_cancel_instead_of_rectifying_magnitude(self):
        stats = phasor_statistics([1.0, 1.0], [0.0, 180.0])
        self.assertLess(stats["mean_mag_V"], 1e-12)
        self.assertAlmostEqual(stats["mean_x_V"], 0.0, places=12)

    def test_phase_wrap_is_averaged_in_cartesian_coordinates(self):
        stats = phasor_statistics([1.0, 1.0], [179.0, -179.0])
        self.assertGreater(stats["mean_mag_V"], 0.999)
        self.assertAlmostEqual(abs(stats["mean_phase_deg"]), 180.0, places=9)

    def test_parallel_analyser_qwp_ratio_gives_quarter_wave(self):
        delta_rad, delta_deg = qwp_retardance_from_parallel_ratio(0.5)
        self.assertAlmostEqual(delta_rad, math.pi / 2.0, places=12)
        self.assertAlmostEqual(delta_deg, 90.0, places=12)


class SenarmontNormalisationTests(unittest.TestCase):
    @staticmethod
    def _row(side, beta_deg, derivative, rotation, vpp=4.0):
        signal = derivative * rotation
        return {
            "hwp_deg": 20.0,
            "vpp": vpp,
            "slope_side": side,
            "qwp_readout_offset_from_null_deg": 0.0,
            "anl_offset_from_null_deg": beta_deg,
            "detector_V": 0.11,
            "p_null_mV": 10.0,
            "lockin_net_x_V": signal.real,
            "lockin_net_y_V": signal.imag,
            "lockin_net_x_sem_V": 1e-8,
            "lockin_net_y_sem_V": 1e-8,
        }

    def test_plus_minus_slopes_recover_complex_rotation(self):
        rotation = complex(1.2e-5, -0.8e-5)
        # Vdc - Vnull = 0.1 V at +/-45 => A = 0.2 V.
        plus = self._row("plus45", 45.0, -0.2, rotation)
        minus = self._row("minus45", -45.0, 0.2, rotation)
        observation = derive_rotation_observation([plus, minus])
        self.assertIsNotNone(observation)
        self.assertAlmostEqual(observation["rotation_x_rad_rms"], rotation.real, places=13)
        self.assertAlmostEqual(observation["rotation_y_rad_rms"], rotation.imag, places=13)
        self.assertTrue(observation["paired_slopes"])

    def test_raw_peak_scout_cannot_bias_the_normalized_slope_pair(self):
        rotation = complex(1.2e-5, -0.8e-5)
        plus = self._row("plus45", 45.0, -0.2, rotation)
        minus = self._row("minus45", -45.0, 0.2, rotation)
        raw_peak = self._row("auto_peak", 45.0, -0.2, 100.0 * rotation)
        observation = derive_rotation_observation([plus, minus, raw_peak])
        self.assertAlmostEqual(observation["rotation_x_rad_rms"], rotation.real, places=13)
        self.assertAlmostEqual(observation["rotation_y_rad_rms"], rotation.imag, places=13)
        self.assertEqual(observation["n_slope_rows"], 2)

    def test_detector_ac_dc_transfer_is_applied_before_normalisation(self):
        rotation = complex(1.0e-5, 0.0)
        plus = self._row("plus45", 45.0, -0.2, 2.0 * rotation)
        minus = self._row("minus45", -45.0, 0.2, 2.0 * rotation)
        observation = derive_rotation_observation(
            [plus, minus], detector_ac_gain_over_dc_gain=2.0
        )
        self.assertAlmostEqual(observation["rotation_x_rad_rms"], rotation.real, places=13)

    def test_simultaneous_null_detector_reading_overrides_cached_null(self):
        rotation = complex(1.0e-5, -0.25e-5)
        # The simultaneous 30 mV null gives A=(110-30)/sin^2(45)=160 mV.
        # The deliberately stale p_null_mV value would instead imply 200 mV.
        plus = self._row("plus45", 45.0, -0.16, rotation)
        minus = self._row("minus45", -45.0, 0.16, rotation)
        plus["detector_null_reference_V"] = 0.03
        minus["detector_null_reference_V"] = 0.03
        observation = derive_rotation_observation([plus, minus])
        self.assertAlmostEqual(observation["rotation_x_rad_rms"], rotation.real, places=13)
        self.assertAlmostEqual(observation["rotation_y_rad_rms"], rotation.imag, places=13)

    def test_off_quadrature_or_shifted_qwp_rows_are_not_certified(self):
        rotation = complex(1.0e-5, 0.0)
        off_quadrature = self._row("plus45", 61.0, -0.2, rotation)
        off_quadrature["qwp_readout_offset_from_null_deg"] = 0.0
        shifted_qwp = self._row("minus45", -45.0, 0.2, rotation)
        shifted_qwp["qwp_readout_offset_from_null_deg"] = 3.01
        self.assertIsNone(derive_rotation_observation([off_quadrature]))
        self.assertIsNone(derive_rotation_observation([shifted_qwp]))

    def test_measured_qwp_position_overrides_an_optimistic_target_offset(self):
        row = self._row("plus45", 45.0, -0.2, complex(1e-5, 0.0))
        row.update({
            "q_null_deg": 80.0,
            "qwp_readout_offset_from_null_deg": 0.0,
            "qwp_readout_actual_deg": 83.01,
        })
        self.assertIsNone(derive_rotation_observation([row]))

    def test_failed_s9_or_invalid_lockin_range_rows_are_excluded(self):
        for flag in (
            "s9_operating_point_failed",
            "lockin_range_exceeded",
            "lockin_overload",
        ):
            with self.subTest(flag=flag):
                row = self._row("plus45", 45.0, -0.2, complex(1e-5, 0.0))
                row["quality_flags"] = flag
                self.assertIsNone(derive_rotation_observation([row]))

    def test_voltage_fit_uses_source_rms_and_recovers_intercept(self):
        slope = complex(2.0e-5, -1.0e-5)
        intercept = complex(0.2e-5, 0.1e-5)
        observations = []
        for vrms in (1.0, 2.0, 3.0, 4.0):
            rotation = slope * vrms + intercept
            observations.append({
                "hwp_deg": 20.0,
                "vrms_source": vrms,
                "rotation_x_rad_rms": rotation.real,
                "rotation_y_rad_rms": rotation.imag,
                "rotation_x_sem_rad_rms": 1e-8,
                "rotation_y_sem_rad_rms": 1e-8,
            })
        fit = fit_rotation_vs_voltage(observations)
        self.assertEqual(fit["fit_mode"], "free_intercept")
        self.assertAlmostEqual(fit["rotation_slope_x_rad_per_Vrms"], slope.real, places=13)
        self.assertAlmostEqual(fit["rotation_slope_y_rad_per_Vrms"], slope.imag, places=13)
        self.assertAlmostEqual(fit["voltage_linearity_r_squared"], 1.0, places=12)

    def test_certificate_requires_background_and_both_opposite_slopes(self):
        base = {
            "hwp_deg": 20.0,
            "theta_i_deg": 40.0,
            "vrms_source": 1.0,
            "rotation_x_rad_rms": 1e-5,
            "rotation_y_rad_rms": 0.0,
            "rotation_x_sem_rad_rms": 1e-8,
            "rotation_y_sem_rad_rms": 1e-8,
            "max_null_leakage_fraction": 0.01,
            "max_analyser_quadrature_error_deg": 0.0,
            "max_qwp_offset_from_null_deg": 0.0,
            "geometry_certified": True,
            "background_corrected": True,
            "opposing_slopes": False,
        }
        self.assertFalse(fit_rotation_vs_voltage([base])["geometry_certified"])
        base["opposing_slopes"] = True
        self.assertTrue(fit_rotation_vs_voltage([base])["geometry_certified"])


class EffectiveCoefficientTests(unittest.TestCase):
    def test_coefficient_is_guarded_without_geometry_confirmation(self):
        fit = {"rotation_slope_mag_rad_per_Vrms": 1e-5}
        result = effective_pockels_coefficient(fit, {"geometry_confirmed": False})
        self.assertEqual(result["status"], "not_calculated")
        self.assertEqual(result["reason"], "senarmont_geometry_not_confirmed")

    def test_coefficient_matches_gamma_equals_two_delta_model(self):
        slope = 1.0e-5
        fit = {
            "rotation_slope_mag_rad_per_Vrms": slope,
            "rotation_slope_mag_sem_rad_per_Vrms": 1.0e-7,
            "n_voltages": 3,
            "voltage_linearity_r_squared": 1.0,
        }
        config = {
            "geometry_confirmed": True,
            "sine_drive_confirmed": True,
            "wavelength_nm": 1550.0,
            "film_thickness_nm": 190.0,
            "electrode_gap_um": 10.0,
            "field_correction": 0.942,
            "refractive_index": 2.1,
            "device_vpp_scale": 1.0,
            "detector_ac_gain_over_dc_gain": 1.0,
        }
        result = effective_pockels_coefficient(fit, config)
        expected = (
            2.0 * 1550e-9 * 10e-6 * slope
            / (math.pi * 2.1 ** 3 * 0.942 * 190e-9)
            * 1e12
        )
        self.assertEqual(result["status"], "calculated")
        self.assertAlmostEqual(result["r_eff_abs_pm_per_V"], expected, places=10)

    def test_non_quarter_wave_calibration_blocks_coefficient(self):
        fit = {
            "rotation_slope_mag_rad_per_Vrms": 1e-5,
            "n_voltages": 3,
            "voltage_linearity_r_squared": 1.0,
        }
        config = {
            "geometry_confirmed": True,
            "sine_drive_confirmed": True,
            "qwp_retardance_deg": 70.0,
            "wavelength_nm": 1550.0,
            "film_thickness_nm": 190.0,
            "electrode_gap_um": 10.0,
            "field_correction": 0.942,
            "refractive_index": 2.1,
            "device_vpp_scale": 1.0,
            "detector_ac_gain_over_dc_gain": 1.0,
        }
        result = effective_pockels_coefficient(fit, config)
        self.assertEqual(result["status"], "not_calculated")
        self.assertEqual(result["reason"], "qwp_retardance_outside_senarmont_tolerance")

    def test_end_to_end_rows_fit_programmed_vpp_as_rms(self):
        rotation_slope = complex(1.0e-5, 0.5e-5)
        rows = []
        for vrms in (1.0, 2.0, 3.0):
            vpp = 2.0 * math.sqrt(2.0) * vrms
            rotation = rotation_slope * vrms
            for side, beta, derivative in (
                ("plus45", 45.0, -0.2),
                ("minus45", -45.0, 0.2),
            ):
                rows.append(SenarmontNormalisationTests._row(
                    side, beta, derivative, rotation, vpp=vpp
                ))
        analysis = analyse_fast_map_rows(rows)
        fit = analysis["best_rotation_fit"]
        self.assertEqual(analysis["status"], "ok")
        self.assertAlmostEqual(
            fit["rotation_slope_mag_rad_per_Vrms"], abs(rotation_slope), places=13
        )


class AngularResponseFitTests(unittest.TestCase):
    @staticmethod
    def _fit_row(hwp_deg, theta_i_deg, response):
        return {
            "hwp_deg": float(hwp_deg),
            "theta_i_deg": float(theta_i_deg),
            "rotation_slope_x_rad_per_Vrms": float(response.real),
            "rotation_slope_y_rad_per_Vrms": float(response.imag),
            "rotation_slope_mag_rad_per_Vrms": float(abs(response)),
            "geometry_certified": True,
        }

    def test_signed_two_theta_fit_recovers_exact_response_and_fourfold_magnitude(self):
        offset = complex(6.0e-6, -2.0e-6)
        cosine = complex(20.0e-6, 5.0e-6)
        sine = complex(-7.0e-6, 13.0e-6)
        rows = []
        for index, theta in enumerate(range(0, 180, 15)):
            phase = math.radians(2.0 * theta)
            response = offset + cosine * math.cos(phase) + sine * math.sin(phase)
            rows.append(self._fit_row(80.0 - 3.0 * index, theta, response))

        fit = fit_angular_response(rows)

        self.assertIsNotNone(fit)
        self.assertEqual(fit["status"], "ok")
        self.assertAlmostEqual(fit["r_squared_complex"], 1.0, places=12)
        self.assertAlmostEqual(fit["coefficient_0_x"], offset.real, places=13)
        self.assertAlmostEqual(fit["coefficient_cos_y"], cosine.imag, places=13)
        self.assertAlmostEqual(fit["coefficient_sin_x"], sine.real, places=13)
        self.assertEqual(fit["harmonic"], 2)
        self.assertEqual(fit["magnitude_harmonic"], 4)
        measured_best = max(
            fit["points"],
            key=lambda point: point["response_mag_rad_per_Vrms"],
        )
        self.assertAlmostEqual(
            fit["selected_measured_hwp_deg"], measured_best["hwp_deg"]
        )

    def test_fit_uses_physical_theta_not_unrelated_hwp_motor_coordinate(self):
        theta_values = [0.0, 15.0, 30.0, 45.0, 60.0, 75.0]
        unrelated_hwp_values = [73.0, 4.0, 61.0, 22.0, 47.0, 10.0]
        rows = [
            self._fit_row(
                hwp,
                theta,
                complex(30.0e-6 * math.cos(math.radians(2.0 * theta)), 0.0),
            )
            for hwp, theta in zip(unrelated_hwp_values, theta_values)
        ]

        fit = fit_angular_response(rows)

        self.assertAlmostEqual(fit["r_squared_complex"], 1.0, places=12)
        self.assertEqual(fit["points"][0]["angle_source"], "calibrated_theta_i_deg")
        self.assertAlmostEqual(fit["selected_measured_theta_i_deg"], 0.0)
        self.assertAlmostEqual(fit["selected_measured_hwp_deg"], 73.0)

    def test_uncertified_geometry_is_excluded(self):
        rows = [
            self._fit_row(index, 15.0 * index, complex(index + 1.0, 0.0))
            for index in range(6)
        ]
        rows[0]["geometry_certified"] = False
        rows[1]["geometry_certified"] = False
        self.assertIsNone(fit_angular_response(rows))

    def test_missing_physical_theta_is_not_replaced_with_raw_hwp_guess(self):
        rows = [
            self._fit_row(index, 15.0 * index, complex(index + 1.0, 0.0))
            for index in range(6)
        ]
        for row in rows:
            row["theta_i_deg"] = None
        self.assertIsNone(fit_angular_response(rows))

    def test_low_quality_theory_fit_falls_back_to_measured_normalized_maximum(self):
        theta_values = [0, 20, 40, 60, 80, 100, 120, 140, 160]
        responses = [
            complex(x, y) * 1e-5
            for x, y in (
                (1, 0), (6, 1), (-1, 4), (2, -5), (-3, -2),
                (5, 5), (-6, 1), (1, -4), (3, 2),
            )
        ]
        rows = []
        for index, (theta, response) in enumerate(zip(theta_values, responses)):
            hwp = 70.0 - 3.0 * index
            for side, beta, derivative in (
                ("plus45", 45.0, -0.2),
                ("minus45", -45.0, 0.2),
            ):
                row = SenarmontNormalisationTests._row(
                    side,
                    beta,
                    derivative,
                    response,
                    vpp=2.0 * math.sqrt(2.0),
                )
                row.update({"hwp_deg": hwp, "theta_i_deg": theta})
                rows.append(row)

        analysis = analyse_fast_map_rows(rows)

        self.assertEqual(analysis["angular_response_fit"]["status"], "low_r_squared")
        self.assertEqual(
            analysis["peak_selection_mode"],
            "certified_normalized_measured_max_fallback",
        )
        self.assertAlmostEqual(analysis["best_rotation_fit"]["hwp_deg"], 55.0)


class SinusoidPeakFitTests(unittest.TestCase):
    @staticmethod
    def _synth(psi_list, amplitude=40e-6, phi_deg=7.0, axis_deg=33.0,
               pickup=(3e-6, -2e-6), noise=0.0, seed=0):
        import numpy as np

        rng = np.random.default_rng(seed)
        axis = math.radians(axis_deg)
        e_axis = complex(math.cos(axis), math.sin(axis))
        xs, ys = [], []
        for psi in psi_list:
            s = amplitude * math.sin(math.radians(2.0 * psi + phi_deg))
            z = complex(*pickup) + e_axis * s
            xs.append(z.real + rng.normal(0.0, noise))
            ys.append(z.imag + rng.normal(0.0, noise))
        return psi_list, xs, ys

    def test_exact_recovery(self):
        from pockels_measurement_analysis import fit_sinusoid_response

        psi, xs, ys = self._synth([0.0, 45.0, -45.0, 30.0, 60.0])
        fit = fit_sinusoid_response(psi, xs, ys)
        self.assertAlmostEqual(fit["peak_psi_deg"], (90.0 - 7.0) / 2.0, places=6)
        self.assertAlmostEqual(fit["amplitude_V"], 40e-6, places=10)
        self.assertAlmostEqual(fit["phase_offset_deg"] % 360.0, 7.0, places=5)
        self.assertAlmostEqual(fit["pickup_mag_V"], math.hypot(3e-6, 2e-6),
                               places=10)
        self.assertGreater(fit["r_squared"], 0.999999)
        self.assertLess(fit["quadrature_fraction"], 1e-9)
        # predicted magnitude at the peak must match |P + e*A|
        axis = math.radians(33.0)
        expected = abs(complex(3e-6, -2e-6)
                       + complex(math.cos(axis), math.sin(axis)) * 40e-6)
        self.assertAlmostEqual(fit["predicted_mag_peak_V"], expected, places=10)

    def test_noise_robust_peak_position(self):
        from pockels_measurement_analysis import fit_sinusoid_response

        psi, xs, ys = self._synth([0.0, 45.0, -45.0, 30.0, 60.0],
                                  noise=0.5e-6, seed=3)
        fit = fit_sinusoid_response(psi, xs, ys)
        self.assertAlmostEqual(fit["peak_psi_deg"], 41.5, delta=1.5)
        self.assertAlmostEqual(fit["amplitude_V"], 40e-6, delta=2e-6)

    def test_underdetermined_raises(self):
        from pockels_measurement_analysis import fit_sinusoid_response

        psi, xs, ys = self._synth([0.0, 45.0, -45.0])
        with self.assertRaises(ValueError):
            fit_sinusoid_response(psi, xs, ys)

    def test_degenerate_angles_raise(self):
        from pockels_measurement_analysis import fit_sinusoid_response

        psi, xs, ys = self._synth([45.0, 45.0, 45.0, 45.0])
        with self.assertRaises(ValueError):
            fit_sinusoid_response(psi, xs, ys)

    def test_alt_peak_is_opposite_extremum(self):
        from pockels_measurement_analysis import fit_sinusoid_response

        psi, xs, ys = self._synth([0.0, 45.0, -45.0, 30.0, 60.0, -30.0],
                                  phi_deg=-10.0)
        fit = fit_sinusoid_response(psi, xs, ys)
        self.assertAlmostEqual(fit["peak_psi_deg"], 50.0, places=5)
        self.assertAlmostEqual(fit["alt_peak_psi_deg"], -40.0, places=5)


class DcMalusFitTests(unittest.TestCase):
    def test_joint_fit_uses_dc_half_fringe_and_recovers_rotation(self):
        psi = [0.0, 45.0, 90.0, -45.0]
        null_psi = 3.0
        voltage = [
            0.01 + 0.8 * math.sin(math.radians(value - null_psi)) ** 2
            for value in psi
        ]
        dc_fit = fit_dc_malus_response(psi, voltage)
        delta = complex(12e-6, -5e-6)
        p = complex(2e-6, 1e-6)
        cc = dc_fit["coefficient_cos_V"]
        cs = dc_fit["coefficient_sin_V"]
        ec = 2.0 * cs * delta
        es = -2.0 * cc * delta
        phasors = [
            p
            + ec * math.cos(math.radians(2.0 * value))
            + es * math.sin(math.radians(2.0 * value))
            for value in psi
        ]
        complex_fit = fit_sinusoid_response(
            psi,
            [value.real for value in phasors],
            [value.imag for value in phasors],
        )
        joint = joint_senarmont_from_fits(dc_fit, complex_fit)

        self.assertAlmostEqual(
            dc_fit["plus_quadrature_psi_deg"], null_psi + 45.0, places=10
        )
        self.assertAlmostEqual(
            joint["equivalent_rotation_x_rad_rms"], delta.real, places=12
        )
        self.assertAlmostEqual(
            joint["equivalent_rotation_y_rad_rms"], delta.imag, places=12
        )
        self.assertLess(joint["derivative_residual_fraction"], 1e-10)
        self.assertLess(joint["temporal_rank_fraction"], 1e-10)

    def test_displaced_ac_extremum_is_diagnostic_not_operating_point(self):
        psi = [0.0, 45.0, 90.0, -45.0]
        voltage = [0.01 + 0.8 * math.sin(math.radians(value)) ** 2 for value in psi]
        dc_fit = fit_dc_malus_response(psi, voltage)
        # AC maximum is 35 degrees, deliberately displaced from the DC
        # maximum-slope point at +45 degrees.
        phasors = [
            complex(25e-6 * math.sin(math.radians(2.0 * value + 20.0)), 0.0)
            for value in psi
        ]
        complex_fit = fit_sinusoid_response(
            psi,
            [value.real for value in phasors],
            [value.imag for value in phasors],
        )
        joint = joint_senarmont_from_fits(dc_fit, complex_fit)

        self.assertAlmostEqual(dc_fit["plus_quadrature_psi_deg"], 45.0)
        self.assertAlmostEqual(complex_fit["peak_psi_deg"], 35.0)
        self.assertAlmostEqual(
            joint["ac_extremum_shift_from_dc_quadrature_deg"], 10.0
        )
        self.assertGreater(joint["derivative_residual_fraction"], 0.2)

    def test_triplet_separates_analyser_independent_modulation(self):
        p = complex(3e-6, -2e-6)
        es = complex(20e-6, 5e-6)
        result = joint_senarmont_triplet(
            detector_null_v=0.01,
            detector_plus_v=0.51,
            detector_minus_v=0.51,
            phasor_null=p,
            phasor_plus=p + es,
            phasor_minus=p - es,
        )
        self.assertAlmostEqual(result["analyser_independent_mag_V"], abs(p))
        self.assertLess(result["derivative_residual_fraction"], 1e-12)
        self.assertFalse(result["has_model_redundancy"])

    def test_balanced_s9_design_recovers_dynamic_peak_and_half_fringe(self):
        psi = [0.0, 45.0, -45.0, 30.0, 60.0]
        pickup = complex(1.2e-6, -0.7e-6)
        eo_axis = complex(
            math.cos(math.radians(23.0)), math.sin(math.radians(23.0))
        )
        amplitude = 24e-6
        phase_offset = 6.0
        phasors = [
            pickup
            + eo_axis
            * amplitude
            * math.sin(math.radians(2.0 * value + phase_offset))
            for value in psi
        ]
        dc_null = -3.0
        voltage = [
            0.012 + 0.8 * math.sin(math.radians(value - dc_null)) ** 2
            for value in psi
        ]

        complex_fit = fit_sinusoid_response(
            psi,
            [value.real for value in phasors],
            [value.imag for value in phasors],
        )
        dc_fit = fit_dc_malus_response(psi, voltage)

        self.assertAlmostEqual(complex_fit["peak_psi_deg"], 42.0, places=10)
        self.assertAlmostEqual(dc_fit["plus_quadrature_psi_deg"], 42.0, places=10)
        self.assertAlmostEqual(
            dc_malus_fraction_at_psi(dc_fit, complex_fit["peak_psi_deg"]),
            0.5,
            places=12,
        )

    def test_recovers_extinction_and_half_fringe(self):
        psi = [0.0, 45.0, -45.0, 30.0, 60.0]
        null_psi = 3.0
        v_min = 0.012
        swing = 0.8
        voltage = [
            v_min + swing * math.sin(math.radians(value - null_psi)) ** 2
            for value in psi
        ]

        fit = fit_dc_malus_response(psi, voltage)

        self.assertAlmostEqual(fit["null_psi_deg"], null_psi, places=10)
        self.assertAlmostEqual(fit["fringe_swing_V"], swing, places=10)
        self.assertAlmostEqual(fit["r_squared"], 1.0, places=12)
        self.assertAlmostEqual(
            dc_malus_fraction_at_psi(fit, null_psi + 45.0), 0.5, places=12
        )

    def test_degenerate_dc_angles_are_rejected(self):
        with self.assertRaises(ValueError):
            fit_dc_malus_response([45.0] * 4, [0.5] * 4)


if __name__ == "__main__":
    unittest.main()
