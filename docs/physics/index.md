# Physics

**What this section is for:** everything you need to defend the measurement in
a viva. It explains what the instrument actually measures, why each equation in
the code is the equation it is, and, just as important, which conclusions the
data cannot support.

The pages are written to be read in order, but each one stands alone and
cross-links to the others. Where a formula appears here it is the formula that
is implemented, and the code is cited so you can check. Where a claim comes
from the literature it carries a numbered citation into
[`../references.md`](../references.md).

---

## What the measurement measures

> We apply an electric field across a thin film of barium titanate; the field
> changes the film's refractive indices; that changes the polarisation of light
> passing through it; and we measure that polarisation change, pixel by pixel
> across a 10 × 10 chip, to map how strong the linear electro-optic response is
> and how the ferroelectric domains switch.

Everything else in the instrument is a consequence of one fact: the
polarisation change is **tiny**, of order microradians. A microradian rotation
cannot be seen by pointing a photodiode at the beam. It has to be converted
into an intensity change at the steepest possible point of a transmission
curve, modulated at a frequency where the laboratory is quiet, and recovered
with a lock-in amplifier. That chain is the whole design.

### The chain, from field to number

| Stage | Physical content | Where it is explained |
| --- | --- | --- |
| $E \to \Delta n$ | Pockels effect; index ellipsoid deformed linearly in the field | [01: The Pockels Effect](01-electro-optics.md) |
| $\Delta n \to \Gamma$ | retardation accumulated over the film thickness $t$ | [01](01-electro-optics.md) |
| $\Gamma \to$ polarisation state | Jones calculus; the state moves on the Poincaré sphere | [02: Polarisation Formalism](02-polarisation.md) |
| polarisation $\to$ intensity | compensated Sénarmont null-slope readout at $\psi = \pm 45^\circ$ | [03: The Null-Slope Sénarmont Readout](03-senarmont-readout.md) |
| intensity $\to$ volts $\to$ rotation $\delta$ | lock-in phasor divided by the measured local Malus slope | [03](03-senarmont-readout.md) |
| $\delta(\theta_i)$ | angular signature; proof that the signal is electro-optic | [04: Angular Dependence](04-incident-polarisation.md) |
| $\delta(V_\mathrm{dc})$ | ferroelectric switching, loops and loop metrics | [05: Ferroelectric Switching](05-ferroelectrics.md) |
| $\delta \to r_\mathrm{eff}$ | only when every absolute calibration is present and every gate passes | [01](01-electro-optics.md), [03](03-senarmont-readout.md) |

### Two things to internalise before anything else

1. **The raw lock-in voltage is not a material property.** It absorbs laser
   power, fibre coupling, focus quality, detector gain, null depth and analyser
   placement, all of which drift. The **normalised rotation** $\delta$ is the
   observable that survives, and it is the correct quantity for comparing
   pixels and compositions.
2. **$r_\mathrm{eff}$ is never an intrinsic tensor element.** It folds in
   texture, domain population, poling history, tensor projection, the electrode
   field distribution, the static retardance of the pixel, the frequency and
   the temperature. The software refuses to print it unless you supply every
   absolute input and every quality gate passes: see
   [03 Step 3](03-senarmont-readout.md#step-3-the-effective-coefficient-and-its-gates).

---

## Reading order

1. **Start with [01: The Pockels Effect](01-electro-optics.md).** It tells you
   what quantity exists to be measured, why a shear tensor component makes the
   answer depend on an angle, and why a perfect single-domain film would give
   no signal at all in this geometry.
2. **Then [02: Polarisation Formalism](02-polarisation.md).** Jones matrices
   and the Poincaré sphere are the language the rest of the section is written
   in. If you already know them, skim to *the Jones chain of this instrument*.
3. **Then [03: The Null-Slope Sénarmont Readout](03-senarmont-readout.md).**
   This is the heart of the instrument and the longest page. Everything in the
   acquisition software is downstream of it.
4. **Then [04: Angular Dependence](04-incident-polarisation.md)** for why the
   half-wave plate is swept, what the polar plot does and does not prove, and
   the counting argument that says why.
5. **Finally [05: Ferroelectric Switching](05-ferroelectrics.md)** for poling,
   hysteresis loops and their metrics.

---

## The pages

| Page | What it covers |
| --- | --- |
| [01: The Pockels Effect](01-electro-optics.md) | Index ellipsoid, inversion symmetry, the explicit Voigt contraction, BaTiO₃ point group $4mm$, $r_{13}/r_{33}/r_{42}$, the eigenvalue problem for the induced axes, which components can be seen at normal incidence, $\Gamma = \pi n^3 r_\mathrm{eff} E t/\lambda$, $V_\pi L$, the coplanar field $E = \alpha V/g$, and why $r_\mathrm{eff}$ is geometry-specific. |
| [02: Polarisation Formalism](02-polarisation.md) | The transverse field from Maxwell, Jones vectors and the global phase, Jones matrices derived from a similarity transform, the coherency matrix, Stokes parameters and their six-intensity definition, the Pauli connection, degree of polarisation, the polarisation ellipse derived, the Poincaré sphere and its $(2\psi, 2\chi)$ mapping, Mueller calculus, Rodrigues, antipodality, the full Jones product of this beamline with its perturbation expansion, and a worked example in the instrument's own numbers. |
| [03: The Null-Slope Sénarmont Readout](03-senarmont-readout.md) | Why the static birefringence is compensated first, where the Sénarmont arrangement comes from, Malus' law, the second-order expansion at the null, the noise model and the real optimum analyser offset, the triplet and the balanced four-point fit, the complex model $Z(\psi) = P + E_1\sin 2\psi + E_2\cos 2\psi$, the DC fringe, the derivative-aligned rotation, the full normalisation chain to $\lvert r_\mathrm{eff}\rvert$, and the uncertainty budget. |
| [04: Angular Dependence](04-incident-polarisation.md) | The $2\theta_i$ dependence derived from the tensor projection, the quadrature between axial and shear mechanisms, the algebra that turns a signed second harmonic into a four-lobed magnitude, the intrinsic ceiling on the magnitude fit, the counting argument for identifiability, the reference-frame argument, and field-direction self-calibration. |
| [05: Ferroelectric Switching](05-ferroelectrics.md) | Ferroelectricity in BaTiO₃ and why $r \propto P_s$, domains and cancellation, the Landau-Devonshire double well and the intrinsic coercive field, why the measured coercive field is orders of magnitude smaller, KAI and nucleation-limited switching behind the stretched exponential, butterfly versus signed loop, the projection and its validity test, the full loop-metric table, the loop-type taxonomy, rate dependence, domain reset, and why an electro-optic loop is not a polarisation loop. |

---

## Citations

Every claim on these pages that comes from the literature carries a numbered
citation of the form [[9]](../references.md#ref-9), resolved on
[`../references.md`](../references.md). Claims that come from this repository
instead link to the module or the documentation page that implements them.

The bibliography is grouped into electro-optics and the Pockels effect,
polarisation optics, ferroelectrics and switching, barium titanate photonics,
measurement technique and instrumentation, and numerical methods, and every
entry says what this repository uses it for.

**[Full bibliography →](../references.md)**

---

## Where to go next

- The bench that implements all of this: [`../experiment/beamline.md`](../experiment/beamline.md),
  [`../experiment/instruments.md`](../experiment/instruments.md),
  [`../experiment/chip.md`](../experiment/chip.md).
- The code that implements the equations:
  [`../software/architecture.md`](../software/architecture.md) and
  [`../software/data-pipeline.md`](../software/data-pipeline.md).
- How to actually run a campaign: [`../guide/quickstart.md`](../guide/quickstart.md)
  and [`../guide/operating.md`](../guide/operating.md).
- Every symbol and flag name used here: [`../reference/glossary.md`](../reference/glossary.md).

---

<div align="center">

[Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [The Pockels effect →](01-electro-optics.md)

</div>
