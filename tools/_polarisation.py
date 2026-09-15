"""Jones and Stokes helpers used by the figure scripts.

Conventions
-----------
Jones vector ``[Ex, Ey]`` for light propagating along +z, time dependence
``exp(-i omega t)``.  Angles are measured from the x axis (the polariser's
transmission axis, which defines the polarisation frame of the instrument).

Stokes vector ``(S0, S1, S2, S3)`` normalised so that ``S0 = 1`` for fully
polarised light:

    S1 = |Ex|^2 - |Ey|^2        linear horizontal / vertical
    S2 = 2 Re(Ex conj(Ey))      linear +45 / -45
    S3 = 2 Im(Ex conj(Ey))      right / left circular

On the Poincare sphere a point sits at azimuth ``2*psi`` and elevation
``2*chi``, where ``psi`` is the ellipse azimuth and ``chi`` its ellipticity
angle (``tan chi`` = minor/major axis ratio, signed by handedness).
"""

from __future__ import annotations

import numpy as np


# ------------------------------------------------------------------ Jones --
def polariser(theta_deg: float) -> np.ndarray:
    """Ideal linear polariser with its transmission axis at ``theta_deg``."""
    t = np.deg2rad(theta_deg)
    c, s = np.cos(t), np.sin(t)
    return np.array([[c * c, c * s], [c * s, s * s]], dtype=complex)


def retarder(retardance_deg: float, fast_axis_deg: float) -> np.ndarray:
    """Linear retarder: ``retardance_deg`` of phase, fast axis at the given angle."""
    d = np.deg2rad(retardance_deg)
    a = np.deg2rad(fast_axis_deg)
    c, s = np.cos(a), np.sin(a)
    rot = np.array([[c, -s], [s, c]], dtype=complex)
    core = np.array([[np.exp(-0.5j * d), 0], [0, np.exp(+0.5j * d)]], dtype=complex)
    return rot @ core @ rot.conj().T


def half_wave_plate(angle_deg: float) -> np.ndarray:
    return retarder(180.0, angle_deg)


def quarter_wave_plate(angle_deg: float) -> np.ndarray:
    return retarder(90.0, angle_deg)


def linear_state(theta_deg: float) -> np.ndarray:
    t = np.deg2rad(theta_deg)
    return np.array([np.cos(t), np.sin(t)], dtype=complex)


# ----------------------------------------------------------------- Stokes --
def stokes(jones: np.ndarray) -> np.ndarray:
    ex, ey = jones
    s0 = abs(ex) ** 2 + abs(ey) ** 2
    s1 = abs(ex) ** 2 - abs(ey) ** 2
    s2 = 2.0 * np.real(ex * np.conj(ey))
    s3 = 2.0 * np.imag(ex * np.conj(ey))
    return np.array([s0, s1, s2, s3], dtype=float)


def normalised_stokes(jones: np.ndarray) -> np.ndarray:
    s = stokes(jones)
    if s[0] <= 0:
        return np.array([0.0, 0.0, 0.0])
    return s[1:] / s[0]


def ellipse_parameters(jones: np.ndarray) -> tuple[float, float, float]:
    """Return (azimuth_deg, ellipticity_angle_deg, axial_ratio_minor_over_major)."""
    s0, s1, s2, s3 = stokes(jones)
    if s0 <= 0:
        return 0.0, 0.0, 0.0
    psi = 0.5 * np.arctan2(s2, s1)
    chi = 0.5 * np.arcsin(np.clip(s3 / s0, -1.0, 1.0))
    return float(np.rad2deg(psi)), float(np.rad2deg(chi)), float(abs(np.tan(chi)))


def ellipse_trace(jones: np.ndarray, n: int = 400) -> tuple[np.ndarray, np.ndarray]:
    """The real electric-field locus traced over one optical period."""
    phase = np.linspace(0.0, 2.0 * np.pi, n)
    ex = np.real(jones[0] * np.exp(1j * phase))
    ey = np.real(jones[1] * np.exp(1j * phase))
    return ex, ey


def poincare_point(jones: np.ndarray) -> np.ndarray:
    return normalised_stokes(jones)


def retarder_axis(fast_axis_deg: float) -> np.ndarray:
    """Poincare-sphere rotation axis of a linear retarder (equatorial)."""
    a = np.deg2rad(2.0 * fast_axis_deg)
    return np.array([np.cos(a), np.sin(a), 0.0])


def rotate_about(axis: np.ndarray, angle_deg: float, point: np.ndarray) -> np.ndarray:
    """Rodrigues rotation of a Stokes point about a sphere axis."""
    k = axis / np.linalg.norm(axis)
    th = np.deg2rad(angle_deg)
    return (point * np.cos(th)
            + np.cross(k, point) * np.sin(th)
            + k * np.dot(k, point) * (1.0 - np.cos(th)))


def retarder_arc(fast_axis_deg: float, retardance_deg: float,
                 start: np.ndarray, n: int = 120) -> np.ndarray:
    """The path a state follows on the sphere as it traverses a retarder."""
    axis = retarder_axis(fast_axis_deg)
    angles = np.linspace(0.0, retardance_deg, n)
    return np.array([rotate_about(axis, a, start) for a in angles])
