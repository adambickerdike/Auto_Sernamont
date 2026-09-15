# Theory of the Instrument

**What this page is for:** the complete analytical model of *this* bench, from
the Jones vector leaving the polariser to the number written into the CSV.
Pages [01](01-electro-optics.md) to [05](05-ferroelectrics.md) build the
physics one concept at a time. This page assembles the whole chain, writes the
transfer function as a product of named factors, computes the noise floor from
the instrument's real numbers, and states what the measurement cannot decide.

> **Prerequisites.** [02 Polarisation Formalism](02-polarisation.md) for Jones
> matrices, and [03 The Null-Slope Sénarmont Readout](03-senarmont-readout.md)
> for the operating point. This page uses the same convention as 03 and as the
> code: $\psi$ is the **analyser offset from that pixel's null**.

---

## Contents

1. [The forward model](#1-the-forward-model)
2. [Harmonic structure, and where the second harmonic comes from](#2-harmonic-structure-and-where-the-second-harmonic-comes-from)
3. [The transfer function from applied volts to detected volts](#3-the-transfer-function-from-applied-volts-to-detected-volts)
4. [Noise budget](#4-noise-budget)
5. [Why 30 kHz](#5-why-30-khz)
6. [Systematic errors](#6-systematic-errors)
7. [Error propagation into the effective coefficient](#7-error-propagation-into-the-effective-coefficient)
8. [Degeneracies: what the instrument cannot decide](#8-degeneracies-what-the-instrument-cannot-decide)

---

## 1. The forward model

### 1.1 Symbols

| Symbol | Meaning | Where it is set |
| --- | --- | --- |
| $h$ | half-wave plate lab angle | ELL14 on COM5, address 1 |
| $\theta_i$ | azimuth of the linear state leaving the HWP | calibrated from $h$ |
| $\theta_s$ | azimuth of the sample's slow eigenaxis | the film, per pixel |
| $u = \theta_i - \theta_s$ | input azimuth measured from the sample axis | derived |
| $\Gamma_0$ | static retardance of the pixel | the film, per pixel |
| $\gamma(t)$ | field-induced retardance increment | the drive |
| $\delta = \gamma/2$ | optical rotation of the compensated state | derived |
| $q$ | quarter-wave plate lab angle | ELL14 on COM4, address 2 |
| $a$ | analyser lab angle | ELL14 on COM8, address 2 |
| $\psi = a - a_\mathrm{null}$ | analyser offset from the pixel's null | the triplet |
| $I_0$ | optical swing (bright minus dark) at the detector | measured per point |
| $I_\mathrm{leak}$ | irreducible leakage at the null | measured per point |

### 1.2 Propagating the Jones vector

The beamline, right to left as a matrix product
([02 §14](02-polarisation.md#14-the-jones-chain-of-this-instrument)), is

$$
\mathbf{J}_\mathrm{out} = P(a)\,W\!\left(\tfrac{\pi}{2},q\right)
W\!\left(\Gamma_0 + \gamma(t),\,\theta_s\right) H(h)\, P(0)\,\mathbf{J}_\mathrm{in}.
\qquad (1)
$$

Work in the sample's own eigenframe, so that $\theta_s = 0$ and the light
arriving at the film is linear at azimuth $u$. Writing the retarder as
$\mathrm{diag}(1, e^{i\Gamma})$ with $\Gamma = \Gamma_0 + \gamma(t)$, the state
leaving the film is

$$
\mathbf{J}_1 = \begin{pmatrix}\cos u \\ e^{i\Gamma}\sin u\end{pmatrix}.
\qquad (2)
$$

Its Stokes vector follows immediately from the definitions of
[02 §6](02-polarisation.md#6-the-stokes-parameters):

$$
S_1 = \cos 2u, \qquad
S_2 = \sin 2u\,\cos\Gamma, \qquad
S_3 = \sin 2u\,\sin\Gamma .
\qquad (3)
$$

Equation (3) is the single most useful line on this page. As $\Gamma$ changes,
the state runs around a circle of radius $\sin 2u$ in the $(S_2, S_3)$ plane,
at fixed $S_1$. **The radius of that circle, $\sin 2u$, is the geometric gain
of the whole instrument.** It is maximal when the incident polarisation
bisects the sample's eigenaxes, $u = 45^\circ$, and it vanishes when the input
lies along an eigenaxis, because then the film has nothing to retard.

### 1.3 What the quarter-wave plate does, algebraically

Take the ideal Sénarmont case $u = 45^\circ$. After removing a global phase,
$\mathbf{J}_1 \propto (e^{-i\Gamma/2},\, e^{+i\Gamma/2})^\mathsf{T}$.
Resolve it onto the $\pm45^\circ$ basis
$\mathbf{e}_\pm = (\hat{x} \pm \hat{y})/\sqrt2$:

$$
A_+ = \tfrac{1}{2}\!\left(e^{-i\Gamma/2} + e^{+i\Gamma/2}\right) = \cos\tfrac{\Gamma}{2},
\qquad
A_- = \tfrac{1}{2}\!\left(e^{-i\Gamma/2} - e^{+i\Gamma/2}\right) = -i\sin\tfrac{\Gamma}{2}.
\qquad (4)
$$

Now put the quarter-wave plate with its fast axis along $\mathbf{e}_+$, that
is, parallel to the incident polarisation. In this basis it is
$\mathrm{diag}(1, i)$, and

$$
\mathbf{J}_2 = \begin{pmatrix}\cos(\Gamma/2) \\ -i\cdot i\,\sin(\Gamma/2)\end{pmatrix}
             = \begin{pmatrix}\cos(\Gamma/2) \\ \sin(\Gamma/2)\end{pmatrix}.
\qquad (5)
$$

The compensator has converted the ellipse back to a **line**, at azimuth
$\Gamma/2$ from the incident direction. That is the classical Sénarmont
compensator [[30]](../references.md#ref-30), and it is where the factor of
two in $\Gamma = 2\delta\psi$ comes from
([03 §11](03-senarmont-readout.md#11-the-derivative-aligned-rotation)). The
construction is in Born and Wolf, *Principles of Optics*
[[11]](../references.md#ref-11), and the electro-optic application in Yariv and
Yeh, *Optical Waves in Crystals* [[3]](../references.md#ref-3).

For general $u$, equation (3) says the arc length swept per unit $\Gamma$ is
$\sin 2u$ rather than $1$, and the compensated azimuth changes by half of it:

$$
\boxed{\;\frac{d\psi_\mathrm{out}}{d\Gamma} = \tfrac{1}{2}\sin 2u
\;\;\Longrightarrow\;\;
\Gamma = \frac{2\,\delta\psi}{\sin 2u}. \;}
\qquad (6)
$$

The code carries $\Gamma = 2\,\delta\psi$, which is equation (6) **with
$\sin 2u = 1$ assumed**. That assumption is exactly what the operator asserts
by setting `geometry_confirmed`, and it is why the flag is a human decision
rather than an inference
([03 §12](03-senarmont-readout.md#12-from-lock-in-volts-to-physics)).

### 1.4 The master intensity equation

Let $a_\mathrm{null}$ be the analyser angle that extinguishes the pixel, so
$a_\mathrm{null} = \Gamma_0/2 + 90^\circ$ in the frame of equation (5), and let
$\psi = a - a_\mathrm{null}$. With $\Gamma = \Gamma_0 + \gamma(t)$ and
$\delta = \gamma/2$, Malus' law applied to equation (5) gives

$$
\boxed{\;
I(\psi, t) \;=\; I_\mathrm{leak} \;+\; I_0\,\sin^{2}\!\bigl(\psi - \delta(t)\bigr).
\;}
\qquad (7)
$$

Everything else on this page is an expansion of equation (7). Note what has
already disappeared: $\Gamma_0$, $\theta_s$, the encoder zeros of all three
rotators, and every constant optical loss. They are absorbed into
$a_\mathrm{null}$, $q_\mathrm{null}$, $I_\mathrm{leak}$ and $I_0$, all four of
which are measured per pixel. That is the reference-frame argument of
[04, the reference-frame argument](04-incident-polarisation.md) in one
equation.

### 1.5 Expansion to first order in the field-induced rotation

Taylor-expanding equation (7) in $\delta$, which is of order microradians
while $\psi$ is of order radians:

$$
\sin^2(\psi - \delta) = \sin^2\psi \;-\; \delta\,\sin 2\psi \;+\; \delta^2\cos 2\psi
\;+\; \mathcal{O}(\delta^3).
\qquad (8)
$$

Add the two contaminants that are always present on a real bench: a fractional
modulation $m(t)$ of the **total** transmitted intensity (electro-absorption,
thermal lensing, residual laser amplitude modulation) and an additive
electrical term $p(t)$ picked up after the photodiode. Then

$$
V(\psi,t) = \bigl[1 + m(t)\bigr]\Bigl(V_\mathrm{leak}
+ V_0\bigl[\sin^2\psi - \delta\sin 2\psi + \delta^2\cos 2\psi\bigr]\Bigr) + p(t),
\qquad (9)
$$

where the optical quantities are now in detector volts,
$V = \mathcal{R}\,G\,I$, with $\mathcal{R} = 0.875$ A/W the responsivity at
1550 nm and $G = 4.75\times10^{3}$ V/A the transimpedance at the 10 dB, Hi-Z setting
([[37]](../references.md#ref-37),
[`../experiment/instruments.md`](../experiment/instruments.md)).

Using $\sin^2\psi = \tfrac{1}{2}(1 - \cos 2\psi)$ and collecting terms in
$\{1, \sin 2\psi, \cos 2\psi\}$ reproduces the model that the code fits:

$$
V(\psi,t) = \underbrace{\mathcal{P}(t)}_{\text{constant in }\psi}
\;+\; \underbrace{\mathcal{E}_1(t)}_{\times\,\sin 2\psi}\sin 2\psi
\;+\; \underbrace{\mathcal{E}_2(t)}_{\times\,\cos 2\psi}\cos 2\psi ,
\qquad (10)
$$

$$
\mathcal{P} = p + (1+m)\!\left(V_\mathrm{leak} + \tfrac{V_0}{2}\right), \qquad
\mathcal{E}_1 = -(1+m)\,V_0\,\delta , \qquad
\mathcal{E}_2 = (1+m)\,V_0\!\left(\delta^2 - \tfrac{1}{2}\right).
\qquad (10\mathrm{a})
$$

At DC, equation (10a) reduces to $\mathcal{E}_2 = -V_0/2$, which is exactly
the DC fringe coefficient $C_c$ that the code fits. The useful statement hiding
in equation (10a) is this.

> **The odd-parity theorem.** Of everything in equation (9), **only the
> polarisation rotation $\delta$ produces a term odd in $\psi$.** Additive
> pickup $p$ is constant in $\psi$. Total-transmission modulation $m$
> multiplies $V_\mathrm{leak} + V_0\sin^2\psi$, whose expansion contains only a
> constant and a $\cos 2\psi$ term. The second-order optical term is
> $\cos 2\psi$. Nothing else is proportional to $\sin 2\psi$.
>
> This is the whole justification for the $\pm45^\circ$ triplet. The
> half-difference $\tfrac{1}{2}(Z_+ - Z_-)$ isolates $\mathcal{E}_1$ and throws
> away $\mathcal{P}$ and $\mathcal{E}_2$ exactly, with no model fitting and no
> assumption about the contaminants beyond their parity.

---

## 2. Harmonic structure, and where the second harmonic comes from

### 2.1 The drive

The function generator applies
$V_\mathrm{dev}(t) = \sqrt{2}\,V_\mathrm{rms}\cos\omega t$
with $\omega/2\pi = 30$ kHz, so the in-plane field is
$E(t) = \sqrt2 E_\mathrm{rms}\cos\omega t$ with
$E_\mathrm{rms} = \alpha V_\mathrm{rms}/g$
([01 §8](01-electro-optics.md#8-the-field-between-coplanar-electrodes)).
Three material responses contribute to the retardance, and they sort
themselves by harmonic:

| Mechanism | Field dependence | Harmonics produced |
| --- | --- | --- |
| Pockels (linear electro-optic), $r_\mathrm{eff}$ | $\propto E$ | $1f$ only |
| Kerr (quadratic electro-optic), $s_\mathrm{eff}$ | $\propto E^2$ | DC and $2f$ |
| Electrostriction plus the elasto-optic coupling | $\propto E^2$ | DC and $2f$ |
| Converse piezoelectric strain plus elasto-optic coupling | $\propto E$ | $1f$ |

Because $\cos^2\omega t = \tfrac{1}{2}(1 + \cos 2\omega t)$, every response
that is quadratic in the field lands at DC and at $2f$, and **contributes
nothing at $1f$**. So

$$
\gamma(t) = \gamma_\mathrm{dc} + \gamma_1\cos\omega t + \gamma_2\cos 2\omega t,
\qquad
\gamma_1 \propto r_\mathrm{eff}E_\mathrm{rms}, \qquad
\gamma_2 \propto s_\mathrm{eff}E_\mathrm{rms}^2 .
\qquad (11)
$$

### 2.2 The three harmonics of the detector signal

Substituting $\delta = \gamma/2$ into equation (8) and collecting powers of
$\cos\omega t$ gives the full harmonic decomposition. Writing
$\delta_k = \gamma_k/2$:

| Harmonic | Coefficient of $\sin 2\psi$ | Coefficient of $\cos 2\psi$ | Constant in $\psi$ |
| --- | --- | --- | --- |
| DC | $-V_0\,\delta_\mathrm{dc}$ | $V_0\bigl(\delta_\mathrm{dc}^2 + \tfrac{1}{2}\delta_1^2 + \tfrac{1}{2}\delta_2^2\bigr)$ | $V_\mathrm{leak} + \tfrac{V_0}{2}$ |
| $1f$ | $-V_0\,\delta_1$ | $V_0\,\delta_1\bigl(2\delta_\mathrm{dc} + \delta_2\bigr)$ | $p_1 + m_1\bigl(V_\mathrm{leak} + \tfrac{V_0}{2}\bigr)$ |
| $2f$ | $-V_0\,\delta_2$ | $V_0\bigl(\tfrac{1}{2}\delta_1^2 + 2\delta_\mathrm{dc}\delta_2\bigr)$ | $p_2 + m_2\bigl(V_\mathrm{leak} + \tfrac{V_0}{2}\bigr)$ |

Four readings of this table matter.

1. **The $1f$ signal is purely Pockels to first order.** The quadratic
   mechanisms cannot reach $1f$ at all unless a DC bias is also present. This
   is the reason the map is a $1f$ measurement.
2. **Any $\cos 2\psi$ content at $1f$ is second order or contamination.** The
   term $2\delta_\mathrm{dc}\delta_1$ is the signature of a stale null: if the
   analyser zero is wrong by $\Delta\psi$, that acts exactly like
   $\delta_\mathrm{dc} = \Delta\psi$. So the ratio
   $\lvert\mathcal{E}_2/\mathcal{E}_1\rvert \approx 2\lvert\Delta\psi\rvert$ is
   a direct, calibrated read-out of null staleness. At $\Delta\psi = 0.1^\circ$
   it is $3.5\times10^{-3}$.
3. **At the null the $1f$ signal vanishes and the $2f$ signal does not.**
   Setting $\psi = 0$ leaves $\tfrac{1}{2}V_0\delta_1^2$ at $2f$: the square of
   the very rotation being measured, appearing at twice the frequency. This
   is the classical crossed-polariser second-harmonic response, and it is an
   independent route to $\delta_1$ that needs no slope calibration. The present
   software does not use it; it would need the lock-in referenced to $2f$.
4. **A DC bias resurrects the quadratic term at $1f$.** With
   $E = E_\mathrm{dc} + \sqrt2 E_\mathrm{rms}\cos\omega t$, the square contains
   $2\sqrt2 E_\mathrm{dc}E_\mathrm{rms}\cos\omega t$, so the quadratic
   mechanisms contribute a $1f$ component **proportional to the DC bias**. That
   is precisely the linear background that the hysteresis analysis removes from
   the saturation tails as `quad_eo_slope_V_per_V`
   ([05](05-ferroelectrics.md),
   [`../software/algorithms.md`](../software/algorithms.md#8-numerical-differentiation-and-interpolation-in-the-loop-analysis)).
   It is also why the AC drive must have no DC offset, which is what the
   "confirm sine drive" checkbox asserts.

> **The one quadratic mechanism that does not sort itself out.** The converse
> piezoelectric effect is *linear* in $E$, so the strain it produces, and the
> index change that strain produces through the elasto-optic tensor, both
> appear at $1f$ and are indistinguishable from the primary Pockels effect in
> this measurement. What the instrument reports is therefore the **unclamped**
> (constant-stress) coefficient in whatever mechanical boundary condition the
> film is in at 30 kHz, not the clamped (constant-strain) one. The distinction
> is standard in ferroelectrics; see Lines and Glass
> [[24]](../references.md#ref-24) and Damjanovic
> [[25]](../references.md#ref-25).

---

## 3. The transfer function from applied volts to detected volts

Chaining sections 1 and 2 gives the complete small-signal response. With the
analyser at the $+45^\circ$ slope point, so $\sin 2\psi = 1$,

$$
\boxed{\;
V_\mathrm{lockin}^\mathrm{rms}
\;=\;
\underbrace{\frac{G_\mathrm{AC}}{G_\mathrm{DC}}}_{\text{detector transfer}}
\cdot
\underbrace{A_\mathrm{opt}}_{\text{Malus slope}}
\cdot
\underbrace{\frac{\pi n^3 \alpha\, t}{2\lambda g}\,r_\mathrm{eff}}_{\text{material and geometry}}
\cdot
\underbrace{k_\mathrm{div}}_{\text{voltage division}}
\cdot
\underbrace{\frac{V_\mathrm{pp}}{2\sqrt2}}_{\text{source}} .
\;}
\qquad (12)
$$

Inverting equation (12) for $r_\mathrm{eff}$ gives exactly the expression
implemented in `effective_pockels_coefficient()`
([`pockels/pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py)).
Each factor separately:

| # | Factor | Symbol | Units | Value or source | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Source RMS amplitude | $V_\mathrm{pp}/2\sqrt2$ | V | 9 Vpp map, 4 Vpp hysteresis probe, at 30 kHz | **known**, set by the TGF3162 |
| 2 | Division between source and device at 30 kHz | $k_\mathrm{div}$ | dimensionless | `--bto-device-vpp-scale` | **unmeasured**, gates $r_\mathrm{eff}$ |
| 3 | Electrostatic factor of the coplanar gap | $\alpha$ | dimensionless | FEM of *this* electrode geometry | **unmeasured**, gates $r_\mathrm{eff}$ |
| 4 | Electrode gap | $g$ | m | about 7 µm nominal; must be measured | measured optically, per chip |
| 5 | Electro-optic tensor projection | $r_\mathrm{eff}$ | m/V | the unknown | the answer |
| 6 | Refractive index, entering as $n^3$ | $n$ | dimensionless | 2.1 default, a parameter not a constant | assumed |
| 7 | Optical interaction length | $t$ | m | film thickness, traceable with an uncertainty | **unmeasured**, gates $r_\mathrm{eff}$ |
| 8 | Vacuum wavelength | $\lambda$ | m | 1550 nm | **known** |
| 9 | Sénarmont conversion | $\Gamma = 2\delta$ | dimensionless | equation (6) with $\sin 2u = 1$ | asserted by `geometry_confirmed` |
| 10 | Malus slope at the operating point | $A_\mathrm{opt}$ | V, equivalently V/rad at the slope point | $\bigl(V_\mathrm{dc} - V_\mathrm{null}\bigr)/\sin^2\psi$ | **measured in situ, every point** |
| 11 | Detector responsivity | $\mathcal{R}$ | A/W | 0.875 at 1550 nm | **known** |
| 12 | Transimpedance gain | $G$ | V/A | $4.75\times10^{3}$ at 10 dB, Hi-Z | **known** |
| 13 | Detector AC over DC transfer at 30 kHz | $G_\mathrm{AC}/G_\mathrm{DC}$ | dimensionless | `--bto-detector-ac-gain-over-dc-gain` | **unmeasured**, gates $r_\mathrm{eff}$ |
| 14 | Lock-in conversion | 1 | V/V | DSP7230 reports RMS volts, 200 µV full scale, fixed | **known** [[36]](../references.md#ref-36), [[32]](../references.md#ref-32) |

Two structural observations.

**Factor 10 is the reason the instrument works at all.** Factors 1, 11, 12 and
every optical loss in the beamline multiply both the AC numerator and the DC
denominator, so dividing by the locally measured Malus slope removes them
identically. Laser power drift, fibre coupling, focus quality and detector gain
never enter the normalised rotation
([03 §12](03-senarmont-readout.md#12-from-lock-in-volts-to-physics)). This is the
null-slope metrology of Abel's thesis [[31]](../references.md#ref-31), sections
3.3 to 3.4, and of the single-point quadrature method of Picavet and co-workers
[[29]](../references.md#ref-29).

**Four factors are unmeasured, and they are exactly the four that gate the
absolute coefficient.** $k_\mathrm{div}$, $\alpha$, $t$ and
$G_\mathrm{AC}/G_\mathrm{DC}$ are all multiplicative and none of them cancels.
Until each is supplied with a value and an uncertainty, `r_eff_status` reports
`missing_or_nonpositive_inputs` and names them. The **rotation** $\delta$ is
unaffected by all four, which is why $\delta$ and not $r_\mathrm{eff}$ is the
cross-pixel observable.

---

## 4. Noise budget

> **Warning**
> Everything in this section is a **calculation from datasheet quantities and
> first principles**. It is not a measured noise floor. For the argument about
> where the optimum analyser offset sits once noise is included, see
> [03 §7](03-senarmont-readout.md#7-noise-and-the-real-optimum-operating-point). Section 4.5 says what
> measurement would establish the real one, and nothing here should be quoted
> as an instrument specification until that measurement exists.

### 4.1 The operating point in physical units

Take the same illustrative pixel as
[03 §7.4](03-senarmont-readout.md#7-noise-and-the-real-optimum-operating-point):
a full optical swing of 1 V, a null level of a few mV, so at the
$\pm45^\circ$ point $V_\mathrm{dc} \approx 0.5$ V and
$A_\mathrm{opt} \approx 1.0$ V/rad. The scope-to-power conversion the code
uses is $1/(\mathcal{R}G) = 1/(0.875 \times 4750) = 2.406\times10^{-4}$ W/V,
so

$$
P_\mathrm{det} = 120\ \mu\mathrm{W}, \qquad
I_\mathrm{ph} = \mathcal{R}P_\mathrm{det} = 1.05\times10^{-4}\ \mathrm{A}.
\qquad (13)
$$

That is 1.7 % of the roughly 7 mW leaving the source, which is a reasonable
throughput for a polariser, a half-wave plate, a film on a substrate, a
quarter-wave plate and an analyser in series. Every number below scales with
this choice, so treat them as an order of magnitude, not a specification.

### 4.2 The white-noise terms

| Source | Expression | Value at equation (13) |
| --- | --- | --- |
| Shot noise on the photocurrent, Saleh and Teich [[34]](../references.md#ref-34) | $\sqrt{2qI_\mathrm{ph}}\;\cdot G$ | $5.80\times10^{-12}\ \mathrm{A/\sqrt{Hz}} \to 27.6\ \mathrm{nV/\sqrt{Hz}}$ |
| Johnson noise of the transimpedance resistor, Horowitz and Hill [[35]](../references.md#ref-35) | $\sqrt{4k_BTR_f}$ | 8.8 nV/$\sqrt{\mathrm{Hz}}$ at $R_f = 4.75$ kΩ, 295 K |
| Lock-in input noise | $e_n$ | see the DSP7230 manual [[36]](../references.md#ref-36); negligible below about 10 nV/$\sqrt{\mathrm{Hz}}$ |
| Laser relative intensity noise | $V_\mathrm{dc}\sqrt{\mathrm{RIN}}$ | equals the shot term when RIN is $-145$ dB/Hz |

The Johnson figure assumes the 10 dB gain setting is realised by a feedback
resistance of about 4.75 kΩ, consistent with the factor-of-two drop between
the Hi-Z and 50 Ω entries in the gain table. Flag it as an assumption, not a
datasheet reading.

Adding in quadrature, with $e_n = 10$ nV/$\sqrt{\mathrm{Hz}}$ as a placeholder
and RIN below the shot floor:

$$
e_\mathrm{tot} = \sqrt{27.6^2 + 8.8^2 + 10.0^2}\ \mathrm{nV/\sqrt{Hz}}
= 30.6\ \mathrm{nV/\sqrt{Hz}} .
\qquad (14)
$$

The measurement is therefore **shot-noise dominated**: 81 % of the noise power
is photon statistics. That is the correct place to be, and it means the only
way to improve the white floor is more light on the detector.

### 4.3 Equivalent noise bandwidth of the lock-in

The lock-in multiplies by the reference and low-passes. For white input noise
of one-sided power spectral density $S_v$ near $\omega$, the baseband noise
density after the mixer is also $S_v$, so each quadrature has variance

$$
\sigma_X^2 = \sigma_Y^2 = S_v \cdot B_\mathrm{ENBW}.
\qquad (15)
$$

For $n$ cascaded single-pole sections each of time constant $\tau$, see Meade
[[32]](../references.md#ref-32) and Scofield
[[33]](../references.md#ref-33),

$$
B_\mathrm{ENBW} = \frac{1}{4\tau},\ \frac{1}{8\tau},\ \frac{3}{32\tau},\ \frac{5}{64\tau}
\quad\text{for } n = 1, 2, 3, 4 .
\qquad (16)
$$

The instrument runs time-constant index 14, $\tau = 500$ ms, at 12 dB per
octave, which is $n = 2$. Hence

$$
B_\mathrm{ENBW} = \frac{1}{8 \times 0.5\ \mathrm{s}} = 0.25\ \mathrm{Hz},
\qquad
\sigma_X = 30.6\ \mathrm{nV/\sqrt{Hz}} \times \sqrt{0.25\ \mathrm{Hz}}
= 15.3\ \mathrm{nV}.
\qquad (17)
$$

### 4.4 The rotation noise floor

Dividing by the Malus slope of section 4.1 converts volts into radians:

$$
\sigma_\delta = \frac{\sigma_X}{A_\mathrm{opt}}
= \frac{15.3\ \mathrm{nV}}{1.0\ \mathrm{V/rad}}
= 1.5\times10^{-8}\ \mathrm{rad}
\;\approx\; \mathbf{0.015\ \mu rad}\ \text{RMS per settled reading}.
\qquad (18)
$$

This agrees with the shot-only estimate of order 10 nrad in
[03 §13](03-senarmont-readout.md#13-error-propagation-and-the-uncertainty-budget),
the difference being the detector and lock-in terms added here. Either way a
microradian signal sits about **70 times above the calculated floor**. The
practical floor is therefore set by drift and by pickup, not by photons, which
is why the instrument spends its effort on re-nulling and on the sign-reversal
test rather than on averaging.

Expressed as a coefficient, using equation (12) at 9 Vpp
($V_\mathrm{rms} = 3.18$ V), $\lambda = 1550$ nm, $g = 7$ µm, $n = 2.1$,
$\alpha = 1$ and a **placeholder** $t = 100$ nm:

$$
\left.\frac{d\delta}{dV_\mathrm{rms}}\right|_\mathrm{noise}
= 4.8\times10^{-9}\ \mathrm{rad/V}
\;\;\Longrightarrow\;\;
\lvert r_\mathrm{eff}\rvert_\mathrm{floor} \approx 0.036\ \mathrm{pm/V}.
\qquad (19)
$$

The thickness is a placeholder because the repository deliberately has no
default for it. Scale equation (19) as $1/t$.

### 4.5 What would establish the real floor

Three measurements, none of which needs the sample to be driven:

1. **Static repeatability.** Park at the $+45^\circ$ operating point with the
   drive **off** and the reference on, and record 200 lock-in phasors. The
   standard deviation of $X$ and $Y$ is the honest single-reading noise. Compare
   with equation (17).
2. **Allan deviation versus averaging time.** Repeat with the samples
   time-stamped and compute $\sigma_A(\tau_\mathrm{avg})$. The white-noise
   region falls as $\tau_\mathrm{avg}^{-1/2}$; the minimum is the optimal
   averaging time; the rise beyond it is the drift the re-nulling is fighting.
   This single plot settles how long a point should dwell.
3. **Dark and blocked-beam controls.** Repeating (1) with the beam blocked
   isolates electrical pickup from optical noise, and repeating it with the
   drive on but the switch matrix open isolates radiated pickup from conducted
   pickup.

---

## 5. Why 30 kHz

The drive frequency is the one free parameter that trades four constraints
against each other. Each bound below is a calculation, not a preference.

| Constraint | Bound | Reason |
| --- | --- | --- |
| Above the $1/f$ corner | $f \gg$ a few kHz | detector and laser flicker noise fall as $1/f$; at 30 kHz the spectrum is white, which is what section 4 assumed |
| Away from mains and its harmonics | $f \gg 50\ \mathrm{Hz}$ | 30 kHz is the 600th harmonic of 50 Hz, by which point the mains spectrum carries no usable power; and the 0.25 Hz ENBW of equation (17) rejects anything more than 0.25 Hz away in any case |
| Above acoustic and mechanical resonances | $f \gg 1$ kHz | stage, mount and table resonances live below about 1 kHz, so mechanical microphony cannot reach the carrier |
| Below any transmission-line effect in the electrodes | $f \ll c/(L\sqrt{\varepsilon_\mathrm{eff}})$ | see below |
| Slow enough for domain walls to follow | $f \ll$ the wall relaxation spectrum | see below |

**The electrodes are electrically tiny.** For a coplanar pair on a dielectric
substrate with effective permittivity $\varepsilon_\mathrm{eff} \approx 10$,
the guided wavelength at 30 kHz is

$$
\lambda_g = \frac{c}{f\sqrt{\varepsilon_\mathrm{eff}}}
= \frac{3\times10^8}{3\times10^4 \times 3.16} \approx 3\ \mathrm{km},
\qquad (20)
$$

against an electrode structure of order a millimetre. The device is
$3\times10^{-7}$ of a wavelength long, so it is a **lumped capacitor**, the
field pattern is electrostatic, and the factor $\alpha$ of equation (12) is a
solution of Laplace's equation rather than of a wave equation. That is what
makes a single FEM value of $\alpha$ meaningful at all.

**The cable does not load the drive.** A metre of coaxial cable is about 100 pF,
whose impedance at 30 kHz is $1/(2\pi f C) = 53$ kΩ. Against the generator's
50 Ω source this is a division of about 1 part in $10^{3}$. The reason
$k_\mathrm{div}$ must nonetheless be measured is not the cable; it is the
PhotoMOS relay on-resistance and any series protection in the switch matrix
([`../experiment/switch-matrix.md`](../experiment/switch-matrix.md)).

**Domain walls still respond.** The extrinsic, domain-wall contribution to the
dielectric and electro-optic response of a ferroelectric has a broad
distribution of relaxation times extending well above 30 kHz; see Damjanovic
[[25]](../references.md#ref-25) and Tagantsev, Cross and Fousek
[[26]](../references.md#ref-26). Pushing the carrier into the MHz range would
progressively clamp out that contribution and change what $r_\mathrm{eff}$
means. Staying at 30 kHz keeps the measurement in the same regime as the DC
hysteresis loops it is compared against.

---

## 6. Systematic errors

Each entry gives the mechanism, its size at this bench, and the thing that
controls it.

### 6.1 Rotator angle error

The ELL14 encoder is 143 360 counts per revolution, so one count is
$360/143360 = 0.00251^\circ$, and the specified accuracy is $\pm0.05^\circ$
$= 8.7\times10^{-4}$ rad
([[38]](../references.md#ref-38),
[`../software/motion-control.md`](../software/motion-control.md#22-encoder-arithmetic-and-the-sign-convention)).

The effect on the **slope** is second order, and this is the deepest reason for
choosing $\pm45^\circ$:

$$
\frac{d}{d\psi}\sin 2\psi = 2\cos 2\psi = 0 \quad\text{at }\psi = 45^\circ
\;\;\Longrightarrow\;\;
\frac{\Delta(\sin 2\psi)}{\sin 2\psi} = -2\varepsilon^2 .
\qquad (21)
$$

At $\varepsilon = 8.7\times10^{-4}$ rad that is $1.5\times10^{-6}$, utterly
negligible. The operating point is not merely the steepest point of the Malus
curve; it is also the point where the steepness is **stationary**, so angle
error cannot leak into gain.

The effect on the **fringe fraction** used to infer $A_\mathrm{opt}$ is first
order:

$$
\frac{\Delta(\sin^2\psi)}{\sin^2\psi}\bigg|_{45^\circ}
= \frac{\sin 2\psi}{\sin^2\psi}\,\varepsilon = 2\varepsilon = 0.17\ \%.
\qquad (22)
$$

**Control:** every move is read back and verified to 0.08°, with backlash
compensation and up to three retries on the accurate path.

### 6.2 Imperfect quarter-wave retardance

Let the compensator have retardance $\pi/2 + \epsilon_Q$. Repeating equation
(5) with $\mathrm{diag}(1, e^{i(\pi/2 + \epsilon_Q)})$ gives
$\mathbf{J}_2 = (\cos(\Gamma/2),\ e^{i\epsilon_Q}\sin(\Gamma/2))^\mathsf{T}$,
which is elliptical with

$$
\sin 2\chi = \sin\Gamma_0 \,\sin\epsilon_Q
\;\;\Longrightarrow\;\;
\chi \approx \tfrac{1}{2}\epsilon_Q \sin\Gamma_0 .
\qquad (23)
$$

The azimuth is unchanged to first order, so the **scale factor
$\Gamma = 2\delta\psi$ is correct to second order in $\epsilon_Q$**; what the
error costs is null depth, since an ellipse cannot be extinguished:

$$
\frac{I_\mathrm{min}}{I_\mathrm{max}} = \tan^2\chi
\approx \left(\tfrac{1}{2}\epsilon_Q\right)^2 \quad\text{at } \sin\Gamma_0 = 1 .
\qquad (24)
$$

At the software's tolerance $\epsilon_Q = 10^\circ$ this is 0.76 % leakage,
comfortably inside the 5 % gate; conversely a 5 % leakage would need
$\epsilon_Q \approx 25^\circ$. The $90^\circ \pm 10^\circ$ tolerance is
therefore the stricter of the two tests, which is the right way round.

**Control:** `qwp_retardance_from_parallel_ratio()` measures it from a
parallel-analyser sweep, $I_\mathrm{min}/I_\mathrm{max} = \cos^2(\delta/2)$,
and a retardance outside tolerance blocks the $r_\mathrm{eff}$ report.

### 6.3 Residual ellipticity and finite null depth

Acceptance is 14.5 mV at the detector. Against the $A_\mathrm{opt} = 1.0$ V of
section 4.1 the leakage fraction is

$$
\frac{V_\mathrm{null}}{V_\mathrm{null} + A_\mathrm{opt}}
= \frac{0.0145}{1.0145} = 1.43\ \%,
\qquad (25)
$$

three and a half times inside the 5 % physics gate. Leakage does not bias $\delta$ to
first order, because the code divides by the measured swing rather than the
measured level; what it costs is signal-to-noise, since the leaked light
carries shot noise but no modulation.

### 6.4 Analyser extinction ratio

A real analyser of extinction ratio $\mathrm{ER}$ contributes an irreducible
floor $V_0/\mathrm{ER}$. At $\mathrm{ER} = 10^4$ and $V_0 = 1$ V this is
0.1 mV, which is 145 times below the 14.5 mV acceptance. **The null floor at
this bench is set by residual ellipticity and by depolarisation, not by the
analyser.** A pixel that cannot reach 14.5 mV is telling you about the film
(scattering, a damaged pixel, a spot straddling an electrode edge), not about
the optics.

### 6.5 Detector nonlinearity

Write the detector response as $V = a I (1 + \zeta I/I_\mathrm{ref})$. Then
both the AC response and the DC slope pick up the same factor
$a(1 + 2\zeta I/I_\mathrm{ref})$, and the ratio taken in
`derive_rotation_observation()` cancels it:

$$
\delta = \frac{V_\mathrm{ac}}{dV_\mathrm{dc}/d\psi}
= \frac{a(1 + 2\zeta I/I_\mathrm{ref})\,\Delta I}{a(1 + 2\zeta I/I_\mathrm{ref})\,dI/d\psi}
+ \mathcal{O}(\zeta^2).
\qquad (26)
$$

**A smooth nonlinearity cancels to first order precisely because the
normalisation is measured at the same operating point as the signal.** The
residual is the curvature, and at 120 µW the PDA30B2 is far from saturation.

### 6.6 Lock-in phase error

An error $\phi_e$ in the reference phase rotates the whole phasor,
$Z \to Ze^{i\phi_e}$. The code carries $X$ and $Y$ through every stage and
reports $\lvert\delta\rvert$, so the **magnitude is invariant**. Only the
reported phase moves, and phase is always interpreted relative to another
reading: the $\pm45^\circ$ pair, or the saturation axis of a hysteresis loop
via `saturation_phase_deg()`. Had the code used the in-phase channel alone, a
$5^\circ$ reference error would have cost 0.4 % of amplitude.

### 6.7 Drive voltage division

Unmeasured, and multiplicative. Section 5 shows the cable contributes about
0.1 %, so the real risk is the relay path and any series resistance. **Control:**
measure $V_\mathrm{device}$ at 30 kHz with the device connected and the matrix
routed, not on the bench with the chip unplugged.

### 6.8 Thermal and mechanical drift over a run

A full map is a multi-hour run. Two things drift.

- **The null angle.** The film's static retardance $\Gamma_0$ is temperature
  dependent, so $a_\mathrm{null} = \Gamma_0/2 + 90^\circ$ walks. From section
  2.2, a stale null of $\Delta\psi$ appears as
  $\lvert\mathcal{E}_2/\mathcal{E}_1\rvert = 2\Delta\psi$, and from equation
  (22) it costs $2\Delta\psi$ in the inferred swing. At $0.1^\circ$ both are
  below 0.4 %. **Control:** a per-pixel null check against 14.5 mV, with
  adaptive re-nulling when it fails.
- **The beam position on the gap.** The stage and the chip move relative to one
  another. **Control:** a per-pixel stage re-alignment before every
  measurement, which is why `SKIP_PER_PIXEL_STAGE_ALIGN` is `False`.

---

## 7. Error propagation into the effective coefficient

From equation (12), $r_\mathrm{eff}$ is a pure product of powers:

$$
r_\mathrm{eff} \;=\; \frac{2\lambda g}{\pi\,\alpha\,t}\; n^{-3}\;
\frac{s}{k_\mathrm{div}},
\qquad
s \equiv \left\lvert\frac{d\delta_\mathrm{rms}}{dV_\mathrm{source,rms}}\right\rvert .
\qquad (27)
$$

For $y = \prod_j x_j^{\,p_j}$ the relative variance is the sum of squared
relative uncertainties weighted by the squared exponents, so

$$
\boxed{\;
\left(\frac{\sigma_{r}}{r}\right)^{2}
=
\left(\frac{\sigma_{s}}{s}\right)^{2}
+\left(\frac{\sigma_{\lambda}}{\lambda}\right)^{2}
+\left(\frac{\sigma_{g}}{g}\right)^{2}
+\left(\frac{\sigma_{t}}{t}\right)^{2}
+\left(\frac{\sigma_{\alpha}}{\alpha}\right)^{2}
+\left(\frac{\sigma_{k}}{k_\mathrm{div}}\right)^{2}
+\left(\frac{\sigma_{G}}{G_\mathrm{AC}/G_\mathrm{DC}}\right)^{2}
+\,9\left(\frac{\sigma_{n}}{n}\right)^{2}.
\;}
\qquad (28)
$$

Equation (28) is what `effective_pockels_coefficient()` implements, term for
term; the same budget is tabulated from the readout side in
[03 §13](03-senarmont-readout.md#13-error-propagation-and-the-uncertainty-budget). The code stores the exponent alongside each input and squares
`exponent * std / nominal`; every exponent is 1 except the refractive index,
which carries **3**, because $r \propto n^{-3}$.

| Input | Exponent in equation (28) | Config key for its standard deviation |
| --- | --- | --- |
| rotation slope $s$ | 1 | from the fit, `rotation_slope_mag_sem_rad_per_Vrms` |
| wavelength $\lambda$ | 1 | `wavelength_std_nm` |
| film thickness $t$ | 1 | `film_thickness_std_nm` |
| electrode gap $g$ | 1 | `electrode_gap_std_um` |
| field correction $\alpha$ | 1 | `field_correction_std` |
| device voltage scale $k_\mathrm{div}$ | 1 | `device_vpp_scale_std` |
| detector AC over DC gain | 1 | `detector_ac_gain_over_dc_gain_std` |
| refractive index $n$ | **3** | `refractive_index_std` |

Three properties of this implementation are worth stating explicitly.

1. **A term is included only if its uncertainty is supplied.** The returned
   `uncertainty_terms_included` list names exactly which terms contributed, so
   a quoted $\sigma_r$ can never be mistaken for a complete budget when it is
   not one.
2. **The refractive index is the cheapest way to be badly wrong.** A 5 %
   uncertainty on $n$ contributes $3 \times 0.05 = 15$ % to
   $\sigma_r/r$, three times what the same 5 % on the gap would contribute.
   Assuming $n = 2.1$ for a strained, graded, multi-domain film is a stronger
   assumption than it looks.
3. **The dominant term will be $\alpha$.** The electrostatic factor of a
   coplanar gap is not transferable between geometries and is not close to 1;
   it must come from a finite-element solution of *this* electrode pattern,
   with its own uncertainty. Until it does, the run reports the rotation and
   refuses the coefficient, which is the correct behaviour
   ([03 §12](03-senarmont-readout.md#12-from-lock-in-volts-to-physics)).

---

## 8. Degeneracies: what the instrument cannot decide

A measurement that reports one number per pixel per angle cannot separate
everything that contributes to that number. These are the degeneracies, and
each one names the extra information that would break it.

| Degeneracy | Why they are inseparable | What breaks it |
| --- | --- | --- |
| $r_\mathrm{eff}$, $\alpha$ and $t$ | equation (12) contains only the product $\alpha\, t\, r_\mathrm{eff}$ | independent measurement of $\alpha$ (FEM) and $t$ (ellipsometry, cross-section) |
| Primary (clamped) Pockels effect versus converse piezoelectric strain read through the elasto-optic tensor | both are linear in $E$, both at $1f$ | frequency dependence through a mechanical resonance, or a clamped reference sample |
| Retardance modulation versus modulation of the eigenaxis angle $\theta_s$ | both rotate the compensated azimuth, so both appear in $\mathcal{E}_1$ | the $\theta_i$ sweep: they have different angular signatures ([04](04-incident-polarisation.md)) |
| Geometric gain $\sin 2u$ versus the tensor projection $r_\mathrm{eff}(\theta_i)$ | $u = \theta_i - \theta_s$, so both are functions of $2\theta_i$ and both land in the same harmonic | an independent measurement of $\theta_s$, for example a crossed-polariser extinction map with no field |
| The absolute sign of $\delta$ | rotator encoder sense and lock-in reference phase each flip it | only relative signs are ever used: the $\pm45^\circ$ pair, or a loop's saturation axis |
| A small intrinsic coefficient versus cancellation between antiparallel domains | the beam averages a **signed** quantity over its spot | poling history and the shape of the hysteresis loop ([05](05-ferroelectrics.md)); alternating-field erase as in Eltes and co-workers [[28]](../references.md#ref-28) |
| $\Gamma_0$ modulo $2\pi$ | the null fixes the azimuth, not the order of the fringe | a spectral or thickness-resolved measurement; the instrument does not need it, because equation (7) never contains $\Gamma_0$ |

> **The honest summary.** This instrument measures, very well, a **normalised
> optical rotation per volt** at a stated frequency, incident polarisation,
> poling history and temperature. It measures that quantity with an internal
> calibration that removes the optical throughput entirely, and with a parity
> test that removes electrical pickup entirely. Everything beyond that, meaning
> every step from the rotation to a number in pm/V, rests on inputs the
> instrument does not itself measure. That is why the software reports
> $\delta$ freely and $\lvert r_\mathrm{eff}\rvert$ only under a gate.

---

## Continue

- [`../software/algorithms.md`](../software/algorithms.md): the numerical
  methods that implement every equation on this page, with their convergence
  behaviour and their constants.
- [03 The Null-Slope Sénarmont Readout](03-senarmont-readout.md): the readout
  in operational detail, and the full gate table.
- [04 Angular Dependence](04-incident-polarisation.md): the $\theta_i$ sweep
  that breaks the axis-rotation degeneracy of section 8.
- [`../experiment/instruments.md`](../experiment/instruments.md): the
  responsivity, gain, time constant and filter order that section 4 assumed.
- [`../references.md`](../references.md): the full bibliography.

---

<div align="center">

[← Ferroelectric switching](05-ferroelectrics.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [The experiment →](../experiment/index.md)

</div>
