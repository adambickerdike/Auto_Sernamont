# Angular Dependence

**What this page is for:** to explain why the half-wave plate is swept at all,
what the resulting polar plot does and does not prove, and why an experiment
with four individually unknown reference frames nevertheless produces a
well-defined physical angle.

It assumes [01 — The Pockels Effect](01-electro-optics.md) (why a shear tensor
component makes the answer angle-dependent) and
[03 — The Null-Slope Sénarmont Readout](03-senarmont-readout.md) (what a
normalised rotation is).

---

## 1. Why the response depends on the incident polarisation

The applied field lies in the film plane, along the electrode axis. The optical
polarisation lies at an angle $\theta_i$ to it. **Which tensor components
couple depends on that angle**, because the electro-optic effect is a tensor
contraction, not a scalar multiplication.

From [01 §5](01-electro-optics.md#5-why-the-shear-coefficient-makes-the-response-angle-dependent):
axial coefficients ($r_{13}$, $r_{33}$) change the magnitude of the
birefringence without moving the eigenaxes, while the shear coefficient
$r_{42}$ rotates the eigenaxes. The two mechanisms peak at different $\theta_i$
and mix in a way that depends on the film's texture and domain population. A
single-angle measurement therefore reports one arbitrary projection of the
tensor and cannot be interpreted.

Sweeping $\theta_i$ buys three things:

1. **An operating point.** The strongest incident polarisation for *this* pixel,
   which is where the follow-up hysteresis measurement is then run.
2. **A physics test.** A genuine electro-optic signal *must* carry the angular
   signature below. Electrical pickup does not depend on the HWP angle at all,
   so a response that is flat in $\theta_i$ is not optical.
3. **A compositional observable.** The amplitude and phase of the fit encode
   the electro-optic anisotropy, which tracks texture and domain population and
   therefore varies systematically across the chip's compositional gradient.

The half-wave plate is the clean way to sweep $\theta_i$: it rotates the
polarisation without changing the intensity and without introducing
ellipticity, and because a HWP reflects the polarisation about its fast axis, a
motor rotation of $\Delta$ gives a polarisation rotation of $2\Delta$. A
$180^\circ$ scan of $\theta_i$ therefore costs only $90^\circ$ of motor travel.
The production grid is **9 points spaced $22.5^\circ$ in $\theta_i$**
($11.25^\circ$ of motor), centred on the calibrated lab-frame
$\theta_i = 81.8688^\circ$, which corresponds to the manually verified
high-response position HWP(raw) $= 7.8951^\circ$.

---

## 2. The signed model

At each $\theta_i$ the background-corrected complex phasor is normalised by the
measured local Malus slope and by the source $V_\mathrm{rms}$, giving the
normalised rotation slope in rad per volt RMS. Both quadratures are then fitted
**together** with

$$
\delta(\theta_i) \;=\; C_0 \;+\; C_c \cos 2\theta_i \;+\; C_s \sin 2\theta_i ,
$$

where $C_0$, $C_c$ and $C_s$ are **complex**.

Two features of that sentence are load-bearing.

**The coefficients are complex** because the lock-in measures a phasor —
amplitude *and* phase. Fitting the magnitude alone would throw away the phase,
and the phase is where the sign lives. Fitting $X$ and $Y$ simultaneously with
complex coefficients keeps the sign reversal intact.

**The angle is $\theta_i$, not the raw motor angle.** The fit is performed in
the calibrated physical incident-polarisation angle, never in raw HWP encoder
degrees. If the calibrated lab mapping is unavailable,
`fit_angular_response()` **skips the point entirely** rather than
manufacturing a theory-validation plot from a naive
$\theta_i = 2\theta_\mathrm{HWP}$ guess. The mapping lives in
[`polarisation_lab_mapping_current.json`](../../pockels/polarisation_lab_mapping_current.json)
and is applied by `lab_theta_i_from_hwp()`; a missing or untrusted mapping
falls back to the legacy relation, marks the mapping untrusted, and that in
turn blocks any $r_\mathrm{eff}$ report
([03 Step 3](03-senarmont-readout.md#step-3--the-effective-coefficient-and-its-gates)).

---

## 3. Harmonic 2 for the signed response, harmonic 4 for its magnitude

The **signed** linear electro-optic response reverses sign every $90^\circ$ of
incident polarisation, because rotating the polarisation by $90^\circ$ swaps
which eigenaxis leads. A quantity that reverses sign every $90^\circ$ and
returns to itself every $180^\circ$ is a **$2\theta_i$** quantity — hence the
$\cos 2\theta_i$, $\sin 2\theta_i$ basis.

Take the magnitude of that same signed response and the sign is discarded.
Rectifying a sinusoid of period $180^\circ$ gives something of period
$90^\circ$, so over a full $360^\circ$ polar sweep $\lvert\delta\rvert$ shows
**four lobes**, and its leading harmonic is **$4\theta_i$**. That is the
familiar four-lobed polar plot. (With a non-zero $C_0$ the lobes are unequal;
the code fits $\lvert\delta\rvert$ with a
$[\,1,\ \cos 4\theta_i,\ \sin 4\theta_i\,]$ basis, which captures the
leading behaviour.)

They are the same physics, seen through a rectifier.

![Signed two-fold response versus four-lobed magnitude](../../assets/figures/angular_harmonics.png)

*Figure 1 — the same data, two ways.* The left panel plots the **signed**
normalised response against $\theta_i$: a single sinusoid in $2\theta_i$, one
full period over $180^\circ$, crossing zero twice and changing sign each time.
The right panel plots its **magnitude** on polar axes over the full
$360^\circ$: four lobes, because taking $\lvert\cdot\rvert$ folds the negative
half of each period up onto the positive side and so doubles the apparent
harmonic. The zero crossings of the left panel are the nulls *between* the
lobes of the right panel — exactly the same angles. Nothing has been added by
the polar representation; what has been *removed* is the sign, the one piece of
information distinguishing a rotation of one handedness from the other.

### The bug this fixed

> **This is a real bug that was found and fixed, and it is worth understanding.**
> Early v2 runs fitted the **signed complex** response with a $4\theta$ model,
> on the reasoning that "the polar plot has four lobes, so the harmonic is 4".
> That is exactly backwards. A $4\theta$ basis cannot represent a function that
> changes sign every $90^\circ$, so the fit was forced to compromise and
> produced misleadingly low angular $R^2$ values — which then looked like bad
> data or a bad measurement rather than a bad model.
>
> The fix is the module constant
> **`DEFAULT_ANGULAR_HARMONIC = 2`** in
> [`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py).
> The signed complex response is fitted at harmonic 2; the **magnitude**
> receives a *separate* direct $4\theta_i$ fit, reported as
> `r_squared_magnitude`, purely as the Figure-S10-style polar diagnostic.

The practical rule: **fit signed, plot magnitude.** Never fit the thing you
plot.

The fit reports both scores — `r_squared_complex` for the signed $2\theta_i$
model and `r_squared_magnitude` for the $4\theta_i$ magnitude diagnostic —
along with a dense theory curve and a continuous peak angle. It requires at
least 5 distinct, geometry-certified angles, and the validity threshold is
$R^2 \ge 0.80$.

> **Peak selection is deliberately conservative.** Hardware follow-ups use the
> strongest *actually measured* geometry-certified point, not the continuous
> fitted maximum: a smooth model must not move the measurement away from a
> stronger acquired point. If the fit is below $R^2 = 0.80$ the selection falls
> back to the largest geometry-certified normalised measured response — and
> never to a raw lock-in magnitude, because rectified pickup could then set the
> HWP angle.

---

## 4. What the fit is fitted to

It is worth being explicit, because this is a place where it is easy to fit the
wrong quantity and get a beautiful, meaningless plot.

| Fitted | Not fitted |
| --- | --- |
| calibrated $\theta_i$ | raw HWP motor degrees |
| normalised rotation slope, rad per $V_\mathrm{rms}$ | raw lock-in microvolts |
| complex $(X, Y)$ simultaneously | magnitude only |
| geometry-certified rows only | every row that returned a number |

The legacy raw `fit_best_hwp_cos4` is retained in summaries for
backwards-compatible diagnostics only. It is not used for selection.

---

## 5. What a four-lobed polar plot proves — and what it does not

**It proves** that the measured response has the angular symmetry of a linear
electro-optic effect in this geometry. That is a genuine and non-trivial
result: it rules out any contribution that does not depend on the incident
polarisation, which is most of the ways this measurement can go wrong —
electrical pickup, laser amplitude modulation, detector artefacts, thermal
drift. A flat polar plot is a failed measurement, and it fails in a way you can
see at a glance.

**It does not prove**, and cannot prove on its own:

| Claim | Why the polar plot cannot support it |
| --- | --- |
| "the response is $r_{42}$-dominated" | $r_{42}$ and $r_{13}/r_{33}$ both produce a $2\theta_i$ signed response; they differ in where the peak sits relative to the field direction, and the field direction is itself derived from the same data (§7). Absolute magnitude and the slope signs either side of the peak *help* distinguish the regimes, but they do not close the argument. |
| "the crystal $a$/$c$ axes lie at angle $X$" | the lab-frame orientation of the crystal never enters the measurement. Everything is referred to the polariser and to each pixel's own null (§6). |
| "the film has a particular domain configuration" | many texture and domain populations produce indistinguishable angular signatures once averaged over the optical mode. |
| "$r_{42} = $ some number" | separating tensor elements requires a tensor/texture/domain model **plus** independent structural information — XRD, TEM or equivalent. |

Say what the data say: *the angular signature is consistent with a linear
electro-optic response, and its amplitude and phase vary systematically across
the chip.* That is a strong claim. The tensor assignment is a separate paper.

---

## 6. The reference-frame argument

This is the part that most often worries a reader, so it is worth setting out
carefully. **Four quantities in this experiment are individually unknown:**

1. the BaTiO₃ crystal axes;
2. the electrode direction relative to those axes;
3. the electrode direction in the lab frame;
4. each rotator's encoder zero — the ELL14 mechanical home index is **not**
   aligned with the waveplate's fast axis, and the offset is arbitrary per unit.

None of the four is ever measured, and none of them needs to be, because every
quantity the instrument reports is *relative*.

**The polariser defines the frame.** Its transmission axis is the zero of every
angle in the experiment. The three-step optical calibration expresses every
other optic in that frame:

| Calibration output | Meaning |
| --- | --- |
| `a_ref_min`, `a_ref_max` | analyser encoder angles for extinction / parallel with the polariser |
| `gamma0` | QWP encoder angle with its fast axis parallel to the polarisation |
| `q_null`, `a_null`, `p_null` | the sample-in null pair and its residual power |
| `HWP_fixed` | HWP encoder reference for the starting polarisation |

Unknowns 3 and 4 are absorbed here: a rotator's arbitrary encoder zero becomes
a fitted offset in the polarisation frame, and the electrode direction in the
lab never appears because no lab-frame angle is ever used.

**Every readout is relative to that pixel's own null.** The analyser offset
$\psi$ is measured from `a_null`, and the QWP from `q_null`, both re-verified
per pixel. Static birefringence — the per-pixel $\Gamma_0$ and $\theta_s$ that
unknowns 1 and 2 would be needed to predict — cancels identically, because it
is what defines the null in the first place
([03 §2](03-senarmont-readout.md#2-what-the-qwp-does-geometrically)).

**What survives is a difference of angles, which is frame-independent.** The
physically meaningful quantity is the angle between the optical polarisation
and the applied field. Both are expressed in the polariser frame, so their
difference is well defined even though neither's absolute lab orientation is.

---

## 7. Self-calibrating the field direction

That leaves exactly one non-optical unknown that the optical calibration cannot
reach: the **direction of the applied E field** in the polarisation frame. It
is measured, not assumed, by the simplest possible experiment — **sweep the HWP
with the AC drive on and find the lock-in peak.**

Let the peak occur at HWP encoder angle $\varphi_\mathrm{peak}$, so the
incident polarisation there is $2\varphi_\mathrm{peak}$ relative to the
polariser. Then:

| If the response is | The peak occurs at | So the field lies along |
| --- | --- | --- |
| $r_{42}$-dominated (shear) | $\theta_i = 45^\circ$ to the field | polarisation-frame angle $2\varphi_\mathrm{peak} - 45^\circ$ |
| $r_{13}/r_{33}$-dominated (axial) | $\theta_i = 0^\circ$ to the field | polarisation-frame angle $2\varphi_\mathrm{peak}$ |

Absolute magnitude, and the signs of the slopes at $\pm45^\circ$ either side of
the peak, help distinguish the two regimes. But — as in §5 — the final tensor
assignment always needs independent structural information. What the sweep
*does* give unconditionally is a reproducible, data-derived field direction in
the same frame as everything else, which is all the measurement requires.

> **Note.** This is self-calibration in the strict sense: the quantity is
> extracted from the same measurement it is needed for, using a symmetry
> argument rather than an external standard. It is robust precisely because the
> angular signature it relies on is the one thing pickup cannot fake.

---

## Continue

- [05 — Ferroelectric Switching](05-ferroelectrics.md): what happens at the
  selected peak angle when a DC bias is swept.
- [01 — The Pockels Effect](01-electro-optics.md): the tensor origin of the
  angular dependence.
- [`../experiment/beamline.md`](../experiment/beamline.md): the HWP, its
  rotator and the calibration procedure.
- [`../software/motion-control.md`](../software/motion-control.md): how the
  9-point $\theta_i$ grid is commanded and verified on the ELL14.
- [`../reference/data-schema.md`](../reference/data-schema.md): the
  `theta_i_deg`, `hwp_deg` and angular-fit columns.

---

<div align="center">

[← Null-slope readout](03-senarmont-readout.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Ferroelectric switching →](05-ferroelectrics.md)

</div>
