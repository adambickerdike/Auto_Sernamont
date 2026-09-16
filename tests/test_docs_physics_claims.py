"""Numerical checks of physics claims made in the documentation.

Four statements in ``docs/physics/04-incident-polarisation.md`` and
``docs/physics/07-instrument-theory.md`` are asserted in prose and verified
here by direct calculation, so the text cannot drift away from what the Jones
calculus actually gives.

1. The Senarmont conversion is exact when the incident polarisation bisects
   the film's static eigenaxes: with the input at 45 degrees to the sample
   axes, a retardance change dGamma moves the compensated azimuth by exactly
   dGamma/2, for any static retardance Gamma_0 (04 section 3.3, 07 section 1.3).
2. Away from 45 degrees the conversion carries the geometric factor
   sin(2u) cos(Gamma_0) / sqrt(1 - sin^2(2u) sin^2(Gamma_0)) (07 equation 6),
   and the response is monotonic in sin(2u), so an axial pixel peaks at 45
   degrees exactly, whatever Gamma_0 is.
3. The shear mechanism survives the limit of zero static birefringence: the
   product 2 rho sin(Gamma_0) of the induced axis rotation and the static
   retardance tends to 2 pi n^3 r42 E t / lambda as the static birefringence
   goes to zero (04 equation 8a, 01 equation 13).
4. The signed angular response is a pure second harmonic only as Gamma_0 goes
   to zero; the leading correction is a sixth harmonic of relative amplitude
   sin^2(Gamma_0) / 8 (04 section 3.2).

The Jones implementation below is self contained and follows the conventions
of ``tools/_polarisation.py``: Jones vector [Ex, Ey], propagation along +z,
time dependence exp(-i omega t), angles measured from the x axis, and the
normalised Stokes vector (S1, S2, S3) with Poincare longitude 2 psi and
latitude 2 chi.

Derivatives are taken by a central difference with dGamma = 1e-4 rad. A
forward difference would carry a relative error of order dGamma times the
curvature of chi(Gamma), about 1e-5 at u = 30 degrees, which the 1e-6
tolerance below would reject; the central difference cancels that term.
"""

import _bootstrap  # provides module_path() and the package directory
import math
import unittest

import numpy as np


# ------------------------------------------------------------------ Jones
def retarder(retardance, fast_axis):
    """Linear retarder: ``retardance`` of phase (rad), fast axis at ``fast_axis`` (rad)."""
    c, s = math.cos(fast_axis), math.sin(fast_axis)
    rot = np.array([[c, -s], [s, c]], dtype=complex)
    core = np.diag([np.exp(-0.5j * retardance), np.exp(+0.5j * retardance)])
    return rot @ core @ rot.conj().T


def quarter_wave_plate(fast_axis):
    return retarder(math.pi / 2.0, fast_axis)


def polariser(axis):
    c, s = math.cos(axis), math.sin(axis)
    return np.array([[c * c, c * s], [c * s, s * s]], dtype=complex)


def linear_state(theta):
    return np.array([math.cos(theta), math.sin(theta)], dtype=complex)


def stokes(jones):
    """Normalised (S1, S2, S3) of a fully polarised Jones vector."""
    ex, ey = jones
    s0 = abs(ex) ** 2 + abs(ey) ** 2
    cross = ex * np.conj(ey)
    return np.array([abs(ex) ** 2 - abs(ey) ** 2, 2.0 * cross.real, 2.0 * cross.imag]) / s0


def azimuth(jones):
    """Ellipse azimuth psi (rad) from tan(2 psi) = S2 / S1."""
    s1, s2, _ = stokes(jones)
    return 0.5 * math.atan2(s2, s1)


def intensity(jones):
    return float(np.vdot(jones, jones).real)


def qwp_null_angle(jones_out):
    """Quarter-wave-plate angle that makes ``jones_out`` linear, found numerically.

    After a quarter-wave plate at angle q the third Stokes component of the
    output is a smooth periodic function of q with simple zeros. The zero is
    bracketed by a coarse scan and then found by bisection to machine
    precision, so nothing about the compensator angle is assumed.
    """
    def s3_after(q):
        return stokes(quarter_wave_plate(q) @ jones_out)[2]

    grid = np.linspace(0.0, math.pi, 181)
    values = [s3_after(q) for q in grid]
    for lo, hi, f_lo, f_hi in zip(grid[:-1], grid[1:], values[:-1], values[1:]):
        if f_lo == 0.0:
            return float(lo)
        if f_lo * f_hi >= 0.0:
            continue
        lo, hi = float(lo), float(hi)
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            f_mid = s3_after(mid)
            if f_mid == 0.0 or hi - lo < 1e-15:
                return mid
            if f_lo * f_mid < 0.0:
                hi = mid
            else:
                lo, f_lo = mid, f_mid
        return 0.5 * (lo + hi)
    raise AssertionError("no compensating quarter-wave-plate angle found")


def pixel_output(theta_in, gamma):
    """State leaving a film of retardance ``gamma`` whose eigenaxis is along x,
    illuminated by linear light at ``theta_in`` from that axis."""
    return retarder(gamma, 0.0) @ linear_state(theta_in)


def find_null(theta_in, gamma0):
    """(QWP angle, analyser angle) that extinguish the pixel, both numerical."""
    out = pixel_output(theta_in, gamma0)
    q_null = qwp_null_angle(out)
    compensated = quarter_wave_plate(q_null) @ out
    a_null = azimuth(compensated) + 0.5 * math.pi
    return q_null, a_null, compensated


def compensated_azimuth_shift(theta_in, gamma0, q_null, d_gamma):
    """Change of the compensated azimuth when the film retardance changes by
    ``d_gamma`` with the quarter-wave plate locked at ``q_null``, as a central
    difference over plus and minus ``d_gamma``, reduced modulo pi."""
    plus = azimuth(quarter_wave_plate(q_null) @ pixel_output(theta_in, gamma0 + d_gamma))
    minus = azimuth(quarter_wave_plate(q_null) @ pixel_output(theta_in, gamma0 - d_gamma))
    return 0.5 * math.remainder(plus - minus, math.pi)


def geometric_factor(u, gamma0):
    """d(compensated azimuth)/d(Gamma) from 07 equation (6)."""
    s = math.sin(2.0 * u)
    return 0.5 * s * math.cos(gamma0) / math.sqrt(1.0 - s * s * math.sin(gamma0) ** 2)


def induced_axis_rotation(n, dn_s, r42, field):
    """Exact rotation of the in-plane eigenaxes of the impermeability matrix
    diag(1/n1^2, 1/n2^2) + r42 E [[0, 1], [1, 0]] with n1 - n2 = ``dn_s``,
    read off its eigenvectors."""
    n1, n2 = n + 0.5 * dn_s, n - 0.5 * dn_s
    b = np.array([[1.0 / n1 ** 2, r42 * field], [r42 * field, 1.0 / n2 ** 2]])
    _, vectors = np.linalg.eigh(b)
    v = vectors[:, 0]                       # the eigenvector that grew out of x
    return abs(math.remainder(math.atan2(v[1], v[0]), math.pi))


D_GAMMA = 1e-4


class SenarmontConversionTests(unittest.TestCase):
    def test_null_pair_extinguishes_the_beam(self):
        for gamma0 in (0.1, 0.5, 1.0, 1.4):
            q_null, a_null, _ = find_null(math.radians(45.0), gamma0)
            residual = intensity(polariser(a_null) @ quarter_wave_plate(q_null)
                                 @ pixel_output(math.radians(45.0), gamma0))
            self.assertLess(residual, 1e-24, msg=f"gamma0={gamma0}")

    def test_conversion_is_exactly_half_at_45_degrees_for_any_static_retardance(self):
        # 04 section 3.3, 07 section 1.3: the compensated azimuth moves by
        # dGamma/2 whatever Gamma_0 is, not only when Gamma_0 is small.
        theta_in = math.radians(45.0)
        for gamma0 in (0.1, 0.5, 1.0, 1.4):
            q_null, _, _ = find_null(theta_in, gamma0)
            shift = compensated_azimuth_shift(theta_in, gamma0, q_null, D_GAMMA)
            self.assertAlmostEqual(abs(shift) / (0.5 * D_GAMMA), 1.0, delta=1e-6,
                                   msg=f"gamma0={gamma0}")

    def test_conversion_off_45_degrees_follows_the_general_factor(self):
        # 07 equation (6) at u = 30 degrees, Gamma_0 = 0.5 rad.
        theta_in, gamma0 = math.radians(30.0), 0.5
        q_null, _, _ = find_null(theta_in, gamma0)
        shift = compensated_azimuth_shift(theta_in, gamma0, q_null, D_GAMMA)
        expected = geometric_factor(theta_in, gamma0) * D_GAMMA
        self.assertAlmostEqual(abs(shift) / abs(expected), 1.0, delta=1e-6)
        # and the general factor is genuinely below one half here
        self.assertLess(abs(expected) / (0.5 * D_GAMMA), 0.9)

    def test_axial_response_peaks_at_45_degrees_for_any_static_retardance(self):
        # 04 section 3.3: the factor is monotonic in sin(2u), so the measured
        # peak of an axial pixel sits exactly where the conversion is exact.
        for gamma0 in (0.1, 0.5, 1.0, 1.4):
            angles = np.radians(np.linspace(0.0, 90.0, 181))
            response = [abs(geometric_factor(u, gamma0)) for u in angles]
            self.assertAlmostEqual(math.degrees(angles[int(np.argmax(response))]), 45.0)
            rising = np.diff(response[:91])
            self.assertTrue(np.all(rising > 0.0), msg=f"gamma0={gamma0}")
            self.assertAlmostEqual(max(response), 0.5, places=12)


class ShearAmplitudeTests(unittest.TestCase):
    def test_shear_amplitude_is_finite_as_static_birefringence_vanishes(self):
        # 04 equation (8a): 2 rho sin(Gamma_0) tends to 2 pi n^3 r42 E t / lambda
        # as the static birefringence of the pixel goes to zero.
        n, r42, field = 2.3, 1.0e-9, 100.0          # index, m/V, V/m
        thickness, wavelength = 2.0e-6, 1550.0e-9   # m
        limit = 2.0 * math.pi * n ** 3 * r42 * field * thickness / wavelength
        deviations = []
        for dn_s in (1.0e-1, 1.0e-2, 1.0e-3):        # reduced by a factor 100
            rho = induced_axis_rotation(n, dn_s, r42, field)
            gamma0 = 2.0 * math.pi * dn_s * thickness / wavelength
            shear_amplitude = 2.0 * rho * math.sin(gamma0)
            deviations.append(abs(shear_amplitude / limit - 1.0))
        self.assertGreater(deviations[0], 0.05)      # not yet in the limit at Gamma_0 near 0.8 rad
        self.assertLess(deviations[1], deviations[0])
        self.assertLess(deviations[2], deviations[1])
        self.assertLess(deviations[2], 0.01)         # within 1 percent once Gamma_0 is small

    def test_axis_rotation_matches_equation_13_of_chapter_01(self):
        # |rho| = n^3 r42 E / (2 dn_s) while rho is small.
        n, r42, field, dn_s = 2.3, 1.0e-9, 100.0, 1.0e-2
        rho = induced_axis_rotation(n, dn_s, r42, field)
        self.assertAlmostEqual(rho / (0.5 * n ** 3 * r42 * field / dn_s), 1.0, delta=1e-3)


class HarmonicContentTests(unittest.TestCase):
    @staticmethod
    def sixth_over_second_harmonic(gamma0):
        """Ratio of the sin(6 theta) to the sin(2 theta) Fourier amplitude of
        the exact axial response of 04 equation (11)."""
        theta = np.linspace(0.0, math.pi, 4096, endpoint=False)
        f = np.array([geometric_factor(t, gamma0) for t in theta])
        b2 = 2.0 * np.mean(f * np.sin(2.0 * theta))
        b6 = 2.0 * np.mean(f * np.sin(6.0 * theta))
        return abs(b6 / b2)

    def test_sixth_harmonic_has_relative_amplitude_sin_squared_gamma_over_eight(self):
        # 04 section 3.2: leading correction to the pure 2 theta response.
        for gamma0_deg, tolerance in ((10.0, 0.05), (30.0, 0.25)):
            gamma0 = math.radians(gamma0_deg)
            ratio = self.sixth_over_second_harmonic(gamma0)
            leading = math.sin(gamma0) ** 2 / 8.0
            self.assertAlmostEqual(ratio / leading, 1.0, delta=tolerance, msg=f"gamma0={gamma0_deg}")
        self.assertGreater(self.sixth_over_second_harmonic(math.radians(30.0)), 0.025)
        self.assertLess(self.sixth_over_second_harmonic(math.radians(30.0)), 0.045)


class DocumentedDefaultsTests(unittest.TestCase):
    def test_refractive_index_default_is_quoted_from_the_code(self):
        # 03 Step 3 quotes the argparse line verbatim so the default appears
        # once, taken from the code; this keeps the quotation honest.
        line = 'parser.add_argument("--bto-refractive-index", type=float, default=2.1)'
        source = _bootstrap.module_path("pockels_fast_map_gui.py").read_text(encoding="utf-8")
        self.assertIn(line, source)
        page = (_bootstrap.PACKAGE_DIR.parent / "docs" / "physics"
                / "03-senarmont-readout.md").read_text(encoding="utf-8")
        self.assertIn(line, page)


if __name__ == "__main__":
    unittest.main()
