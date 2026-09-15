# The Null-Slope Sénarmont Readout

**What this page is for:** the heart of the instrument — how a microradian
polarisation rotation becomes a measurable voltage, why the analyser sits at
exactly $\pm45^\circ$ from extinction, what the three readings of the triplet
are for, and the full chain from lock-in volts to a rotation and — only when
every gate passes — to $\lvert r_\mathrm{eff}\rvert$. Read
[02 — Polarisation Formalism](02-polarisation.md) first.

> **Symbol.** From here on $\psi$ is the **analyser offset from that pixel's
> null**, matching the code. It is not the ellipse azimuth of the previous page.

---

## 1. Why the static birefringence must be compensated first

The BTO film is birefringent with no field applied: it has a static retardance
$\Gamma_0$ and an eigenaxis $\theta_s$, both set by thickness, composition,
strain and domain state, and both different at every pixel and at every
incident polarisation. Light therefore leaves the film **elliptically
polarised** — which is fatal for a naive crossed-polariser measurement, because
**a linear analyser cannot extinguish an elliptical state.** Project an ellipse
onto any line and something always survives; the residual at best extinction is
set by the ellipticity and can sit orders of magnitude above the detector floor.

Why that matters is not aesthetic. Leaked light is **unmodulated**, so it adds
shot noise and DC offset while adding no signal. The
$I(\psi) = I_\mathrm{floor} + I_0\sin^2\psi$ model of §3 and the slope
normalisation built on it are valid only if the state reaching the analyser is
linear. And because the normalisation divides by a measured optical swing, a
pixel with a poor null has a different effective swing, so its rotation is not
comparable with its neighbours'.

The acceptance threshold is **14.5 mV** at the detector
(`--null-check-max-mv`) with a **1.5 mV** continuation margin. Pixels above it
are still measured but are flagged and excluded from trusted seeds, peak
selection, HWP fitting and physics analysis. The gate for reporting
$r_\mathrm{eff}$ is stricter: null leakage $\le 5\,\%$ of the optical swing (§9).

---

## 2. What the QWP does geometrically

A quarter-wave plate, set to the right angle, turns an arbitrary elliptical
state back into a **linear** one. Placing it between sample and analyser — the
classic **Sénarmont** configuration — restores the condition that a linear
analyser can extinguish the beam completely.

**In Jones terms**, using the chain of
[02 §7](02-polarisation.md#7-the-jones-chain-of-this-instrument), the QWP angle
is chosen so that

$$
W\!\left(\tfrac{\pi}{2},\, q_\mathrm{null}\right)
W\!\left(\Gamma_0,\, \theta_s\right)
\mathbf{J}_\mathrm{in}
\;\propto\;
\begin{pmatrix}\cos\theta_\mathrm{out}\\ \sin\theta_\mathrm{out}\end{pmatrix},
$$

i.e. compensator times sample leaves a **real** (linear) Jones vector. The
analyser then goes to $\theta_\mathrm{out} + 90^\circ$ and the beam
extinguishes. The pair $(q_\mathrm{null}, a_\mathrm{null})$ is what the
calibration stores per pixel and per HWP angle.

**On the Poincaré sphere**, the sample rotates the state about its own
eigenaxis, lifting it off the equator. The QWP is a quarter-turn about *its*
axis, and rotating the plate carries that axis around the equator; there is
always an orientation whose quarter-turn brings the state back **down onto the
equator**, i.e. back to linear. That is why a *quarter*-wave plate is the right
element: a quarter-turn is exactly enough to reach the equator from anywhere.

> **Why the null pair is not one number.** $\Gamma_0$ and $\theta_s$ vary with
> pixel *and* with incident polarisation, so the correct QWP angle does too.
> The software stores a $(q_\mathrm{null}, a_\mathrm{null})$ pair per HWP angle
> and re-verifies it at every pixel, with adaptive re-nulling when the check
> fails ([`../software/architecture.md`](../software/architecture.md)).

The compensator is only a compensator if it really is a quarter-wave plate at
1550 nm. A parallel-analyser QWP sweep measures that directly: for linear input
and a parallel analyser $I_\mathrm{min}/I_\mathrm{max} = \cos^2(\delta/2)$,
which `qwp_retardance_from_parallel_ratio()` inverts. A retardance outside
$90^\circ \pm 10^\circ$ blocks the $r_\mathrm{eff}$ report.

---

## 3. Malus' law in the compensated frame

With the static birefringence absorbed, the DC transmission versus analyser
offset $\psi$ from the null is Malus-like:

$$
I(\psi) \;=\; I_\mathrm{floor} \;+\; I_0 \sin^{2}\psi .
$$

At $\psi = 0$ the light is extinguished down to the leakage floor
$I_\mathrm{floor}$; at $\psi = 90^\circ$ it is fully transmitted. This is
Malus' $\cos^2$ law referred to the extinction point rather than the
transmission point.

---

## 4. Why $\pm45^\circ$ is the operating point

A field-induced rotation $\delta$ slides the operating point along that curve,
so to first order

$$
\Delta I \;\approx\; \frac{dI}{d\psi}\,\delta,
\qquad
\frac{dI}{d\psi} \;=\; I_0 \sin 2\psi .
$$

![Transmission and its derivative versus analyser offset](../../assets/figures/malus_slope.png)

*Figure 1 — the operating point, in one picture.* The upper trace is
$I(\psi) = I_\mathrm{floor} + I_0\sin^2\psi$, the lower its derivative
$I_0\sin 2\psi$. The marked points at $\psi = \pm45^\circ$ sit where the
transmission is steepest and the derivative reaches $\pm I_0$ — maximum
conversion of polarisation rotation into intensity change. Note where the
derivative is **zero**: at the null ($\psi = 0$) and at full brightness
($\psi = 90^\circ$). Sitting at either is the worst thing you can do, since a
first-order rotation then gives no first-order intensity change at all. Note
too that the derivative has *opposite sign* at $+45^\circ$ and $-45^\circ$;
that reversal is the instrument's strongest self-check.

Three consequences drive the entire software design.

**(1) Read out at $\psi = \pm45^\circ$.** $\lvert\sin 2\psi\rvert$ is maximal
there, so at fixed $\delta$ the lock-in signal is maximal there. Nothing else
is as sensitive.

**(2) The sign flips between $+45^\circ$ and $-45^\circ$.** $\sin 2\psi$ is odd,
so a genuine optical rotation gives opposite-signed responses at the two slope
points — and anything that does *not* flip is not a polarisation rotation. That
covers capacitive or inductive pickup of the 30 kHz drive into the detector
cable, lock-in input or ground loop; residual laser amplitude modulation at the
drive frequency; and total-transmission modulation (electro-absorption,
thermal), which scales the whole curve instead of sliding along it. The
difference of the two readings rejects all of it as common mode; their sum
measures it. A failure raises `plus_minus_same_sign`, and this "does it change
sign?" test is the single most convincing piece of evidence that you are
measuring light and not crosstalk.

**(3) At the null the first-order optical signal vanishes.** $dI/d\psi = 0$
there, so in the ideal scalar model nothing electro-optic survives: whatever
the lock-in reads is by construction *not the signal*. It is the contaminating
background, and it is subtracted.

> **Be precise about what the null reading is.** It is tempting to call it "the
> pickup", and `pickup_mag_V` survives as a backwards-compatible alias. But an
> analyser-independent term can also contain genuine total-transmission
> modulation, residual laser modulation, residual dichroism, QWP retardance
> error or detector offsets, so the current code names it
> **`analyser_independent_mag_V`** — "everything whose size does not depend on
> the analyser angle" — rather than asserting a mechanism.

---

## 5. The triplet readout

Those three readings — null, exactly $+45^\circ$, exactly $-45^\circ$ — are the
**triplet**. Three lock-in acquisitions per incident polarisation buy four
things at once:

| Reading | What it contributes |
| --- | --- |
| null ($\psi = 0$) | the analyser-independent background phasor for subtraction, and the DC null level $V_\mathrm{null}$ used by the normalisation |
| $+45^\circ$ | a maximum-slope signed measurement, plus its DC level |
| $-45^\circ$ | the opposite-slope partner — common-mode rejection and the sign-opposition check — plus its DC level |

The three DC levels also give the **local Malus slope** directly, so no slow
analyser scan is needed to calibrate the conversion — the normalisation is
measured at the same moment and place as the signal, which is what makes the
fast map fast ([`../software/data-pipeline.md`](../software/data-pipeline.md)).

Crucially the triplet has **no residual degrees of freedom** (three complex
readings fix three complex unknowns; three DC readings fix three DC
coefficients), so `joint_senarmont_triplet()` solves it in closed form:

$$
C_0 = \tfrac{1}{2}(V_{+} + V_{-}), \quad
C_s = \tfrac{1}{2}(V_{+} - V_{-}), \quad
C_c = V_{0} - C_0 ,
$$
$$
P = \tfrac{1}{2}(Z_{+} + Z_{-}), \quad
E_1 = \tfrac{1}{2}(Z_{+} - Z_{-}), \quad
E_2 = Z_{0} - P .
$$

This is a *verification calculation*, not a fit, so it reports no $R^2$; its
quality indicators are the derivative-residual fraction and the DC balance
error $\lvert V_+ - V_-\rvert$ relative to the fringe half-swing. On the
calibration pixel a fourth probe at $\psi = 90^\circ$ is added so the model
becomes over-determined and can be fitted and scored.

---

## 6. The modulation on the sphere

![The small EO modulation near the null on the Poincaré sphere](../../assets/figures/poincare_modulation.png)

*Figure 2 — what the drive actually does to the polarisation state.* **Q** is
the compensated, linear state reaching the analyser, on the equator (see
[02 Figure 2](02-polarisation.md#6-the-poincaré-sphere)). The 30 kHz drive
modulates the sample's retardance by $\Gamma_\mathrm{ac}$, rocking the state
along the short arc drawn through **Q** — exaggerated by many orders of
magnitude, since the real excursion is microradians. The analyser's Stokes
direction $\hat{a}$ is the antipode of **Q**, and the detected intensity
$\propto 1 + \hat{a}\cdot\hat{s}$ responds only to the component of the arc
*along* $\hat{a}$. At the null the arc is perpendicular to $\hat{a}$, so the
response is second order — which is why the signal vanishes there. Rotating the
analyser by $\pm45^\circ$ swings $\hat{a}$ by $\pm90^\circ$ of longitude,
aligning it with the arc; the two choices align with **opposite ends** of it,
which is the sign reversal of §4 seen geometrically.

---

## 7. The full complex model

The lock-in returns a **phasor**, not a magnitude: in-phase $X$ and quadrature
$Y$. The phase carries the sign of the electro-optic response — which is what
tells you which way the domains point — so the code always records $X$ and $Y$
and averages in Cartesian space. (Averaging magnitudes rectifies noise and
biases small signals upward. Never do it.) For an ideal rotating linear
analyser and a linear detector, the most general first-harmonic response is

$$
Z(\psi) \;=\; P \;+\; E_1 \sin 2\psi \;+\; E_2 \cos 2\psi ,
$$

with $P, E_1, E_2$ all **complex**. This is the linear-in-the-unknowns form of
the physically motivated $Z(\psi) = P + \hat{e}A\sin(2\psi + \varphi)$, where
$\hat{e}$ is the unit electro-optic phasor direction, $A$ the amplitude and
$\varphi$ the null-placement error. Expanding the sine converts one into the
other, which is why the fit is a plain complex linear least squares over the
design matrix $[\,1,\ \sin 2\psi,\ \cos 2\psi\,]$ — no non-linear optimiser,
no starting guess, no local minima.

### Closed-form solution

`fit_sinusoid_response()` (≥ 4 analyser probes) recovers the single-axis
factorisation analytically. Since $E_1^2 + E_2^2 = (\hat{e}A)^2$ regardless of
$\varphi$,

$$
\hat{e} = e^{i\phi_e}, \qquad
\phi_e = \tfrac{1}{2}\,\mathrm{atan2}\!\left(\mathrm{Im}(E_1^2+E_2^2),\ \mathrm{Re}(E_1^2+E_2^2)\right),
$$

and projecting onto that axis with $a = \mathrm{Re}(E_1\hat{e}^{*})$,
$b = \mathrm{Re}(E_2\hat{e}^{*})$ gives

$$
A = \sqrt{a^2 + b^2}, \qquad
\varphi = \mathrm{atan2}(b, a), \qquad
\psi^{*} = \frac{\pm 90^\circ - \varphi}{2}.
$$

![Complex lock-in response versus analyser offset](../../assets/figures/analyser_response.png)

*Figure 3 — the complex analyser response $Z(\psi) = P + E_1\sin2\psi + E_2\cos2\psi$.*
Both quadratures are plotted against analyser offset with the fitted model
through them. The **offset** of the pair away from zero is $P$, the
analyser-independent term — the part that does not oscillate with $\psi$. The
**oscillating part** is the electro-optic response: a sinusoid in $2\psi$, so it
completes a full cycle over $180^\circ$ of analyser rotation, with its extrema
$\psi^{*}$ at peak and anti-peak. In a well-nulled pixel those land within a
couple of degrees of $\pm45^\circ$; a large displacement means the
null-placement error $\varphi$ is large, i.e. the analyser zero is stale. The
scatter about the curve is the lock-in noise floor for the chosen time constant.

The fit returns three diagnostics that matter more than $R^2$:

| Diagnostic | Meaning | Use |
| --- | --- | --- |
| `quadrature_fraction` | electro-optic energy **off** the fitted single axis | a single scalar rotation must lie on one axis; a large value means two mechanisms with different temporal phases |
| `temporal_rank_fraction` | second singular value over first, for the $2\times2$ matrix of the real and imaginary parts of $(E_1, E_2)$ | tests directly whether $E_1$ and $E_2$ share a common temporal phase — i.e. whether the single-axis factorisation is legitimate at all |
| `analyser_independent_mag_V` | $\lvert P\rvert$ | how much of the reading does not depend on the analyser |

> **Note the epistemic discipline.** The unrestricted three-coefficient fit is
> done *first*; the single-axis factorisation is accepted only if the
> temporal-rank test says the data support it.

---

## 8. The derivative-aligned rotation

### The DC fringe

The DC detector level is fitted independently over the same probes with

$$
D(\psi) \;=\; C_0 \;+\; C_c \cos 2\psi \;+\; C_s \sin 2\psi ,
$$

exactly equivalent to $D(\psi) = V_\mathrm{min} + A\sin^2(\psi-\psi_0)$ but
linear in its coefficients. Inverting gives
$\psi_0 = \tfrac{1}{2}\mathrm{atan2}(-C_s, -C_c)$,
$\psi_\pm = \psi_0 \pm 45^\circ$, and
$V_{\mathrm{min},\mathrm{max}} = C_0 \mp \sqrt{C_c^2+C_s^2}$. Here $\psi_0$ is
the fitted extinction point and $\psi_\pm$ the two maximum-slope (Sénarmont
quadrature) points, where the normalised fringe fraction is exactly $0.5$.

### Projecting the AC response onto the DC derivative

For an ideal scalar Sénarmont rotation the AC response is nothing but the DC
fringe being slid sideways:

$$
Z - P \;=\; \delta\psi \cdot \frac{dD}{d\psi},
\qquad
\frac{dD}{d\psi} = -2C_c\sin 2\psi + 2C_s\cos 2\psi .
$$

Matching coefficients ($E_2 \leftrightarrow 2C_s$, $E_1 \leftrightarrow -2C_c$)
and solving in the least-squares sense gives the closed form implemented in
`joint_senarmont_from_fits()`:

$$
\boxed{\;
\delta\psi \;=\; \frac{2C_s E_2 \;-\; 2C_c E_1}{4\left(C_c^2 + C_s^2\right)},
\qquad
\Gamma \;=\; 2\,\delta\psi .
\;}
$$

$\delta\psi$ is complex, and it is the physical rotation in radians RMS — the
optical throughput has already divided out, because the same $C_c, C_s$ that
scale the numerator also scale the denominator. Whatever part of $(E_1, E_2)$
is **orthogonal** to the derivative direction cannot be a scalar rotation; it
is retained as `derivative_residual_fraction` and treated as contamination.

> **Why $\Gamma = 2\delta\psi$.** In the Sénarmont configuration the compensator
> converts a retardance $\Gamma$ between the sample's eigenaxes into an azimuth
> rotation of the emergent linear state by $\Gamma/2$. The analyser measures
> azimuth, so the retardance is twice the measured rotation. The factor holds
> only when the geometry really is ideal Sénarmont, which is why
> `geometry_confirmed` is an explicit human-set flag, not an assumption.

### Why the operating angle comes from the DC fringe, never the AC extremum

The complex fit returns its own extrema $\psi^{*}$. **They are never followed.**
The operating angle is always the **DC** half-fringe, $\psi_0 \pm 45^\circ$,
because a raw lock-in maximum is not evidence of correct optical bias: any
analyser-independent contamination — pickup, laser modulation,
total-transmission modulation — shifts where $\lvert Z\rvert$ peaks without
moving where the *optical slope* is steepest. Chasing the AC extremum would let
contamination steer the instrument, self-confirmingly, because the contaminated
point looks "strong". The displacement between the two is used instead as a
**diagnostic**, `ac_extremum_shift_from_dc_quadrature_deg`; with
`derivative_residual_fraction` and the DC half-fringe tolerance (0.075
normalised) it reports how contaminated the reading is, rather than silently
moving the measurement onto the contamination.

---

## 9. From lock-in volts to physics

Raw lock-in volts are not a material property: they carry laser power, fibre
coupling, focus quality, detector gain, null depth and analyser placement,
every one of which drifts over a map. This chain removes all of them.

### Step 1 — Malus normalisation

From the DC detector levels at the readout point and at the null,

$$
A_\mathrm{opt} \;=\; \frac{V_\mathrm{dc}(\psi) - V_\mathrm{null}}{\sin^{2}\psi},
\qquad
\frac{dV_\mathrm{dc}}{d\psi} \;=\; -A_\mathrm{opt}\sin 2\psi .
$$

The complex lock-in response is divided first by the detector AC/DC gain ratio
$G_\mathrm{AC}/G_\mathrm{DC}$ — converting the lock-in channel's volts into the
DC channel's equivalent volts — then by this derivative. The result is the
complex RMS **rotation** $\delta$ in radians: dimensionless and throughput
independent. Several slope rows combine by derivative-weighted least squares,
$\delta = \sum_k d_k Z_k \big/ \sum_k d_k^2$.

`derive_rotation_observation()` admits a row only if all of these hold:

| Gate | Value | Rationale |
| --- | --- | --- |
| `slope_side` recognised | `plus45`, `minus45`, `learned_anchor`, `learned_opposite` | the row must be a defined slope point |
| no disqualifying flag | not `lockin_overload`, `lockin_range_exceeded`, `s9_operating_point_failed` | saturated or mis-biased rows are not data |
| QWP offset from null | $\le 3^\circ$ | compensation still valid |
| analyser quadrature error | $\lvert\,\lvert\psi\rvert - 45^\circ\rvert \le 15^\circ$ | still near maximum slope |
| $\sin^2\psi$ | $\ge 0.02$ | never divide by a vanishing denominator |
| optical swing | $> 0$ | a negative swing means a broken null reference |
| background | a measured null phasor **or** genuinely opposing slopes | a single raw phasor cannot distinguish an optical response from pickup |

Each admitted row also yields
$\text{null leakage} = V_\mathrm{null}/(V_\mathrm{null} + A_\mathrm{opt})$,
whose maximum over the group feeds the 5 % gate in Step 3.

> **On the minus sign.** §4 wrote the idealised slope as $+I_0\sin 2\psi$; the
> code carries $-A_\mathrm{opt}\sin 2\psi$, because the rotator encoder's sense
> of increasing $\psi$ is opposite to the idealised model's. The two differ only
> by an overall sign, which propagates into the reported *phase* of $\delta$ and
> not into its magnitude — which is why the material result exposed by the
> module is $\lvert r_\mathrm{eff}\rvert$. Signs are always interpreted
> *relative* to another reading (the $\pm45^\circ$ pair, or the saturation axis
> of a hysteresis loop), never absolutely.

> **This is the correct cross-pixel observable.** Because the optical and
> electronic chain is shared across pixels, normalised rotations are comparable
> from pixel to pixel — and hence across the compositional gradient — with no
> absolute calibration whatsoever. Raw microvolts are not.

### Step 2 — Voltage linearity

$\delta$ is fitted against the device RMS voltage, per HWP angle. For a
zero-offset sine,

$$
V_\mathrm{rms} \;=\; \frac{V_\mathrm{pp}}{2\sqrt{2}} ,
$$

which is why "confirm sine drive" is an explicit checkbox: the conversion is
false for any other waveform and for any drive carrying a DC offset. With
$\ge 3$ voltage levels the fit has a free intercept — itself informative, being
an offset that does not scale with drive — and the gate is $R^2 \ge 0.98$.
Failing it means the response is not linear in field, so it is not purely a
Pockels effect: candidates are a quadratic-electro-optic or electrostrictive
contribution, saturation, or a drive not reaching the device. With fewer than
three levels the slope is still computed but the status becomes
`calculated_linearity_unverified`.

### Step 3 — The effective coefficient and its gates

For confirmed ideal Sénarmont geometry ($\Gamma = 2\delta$) and in-plane field
$E = \alpha V_\mathrm{device}/g$:

$$
\lvert r_{\mathrm{eff}} \rvert
\;=\;
\frac{2\,\lambda\, g}{\pi\, n^{3}\, \alpha\, t}
\left\lvert \frac{d\delta_{\mathrm{rms}}}{dV_{\mathrm{device,rms}}} \right\rvert .
$$

It is emitted **only** when every one of the following holds. If any fails, the
run records exactly which in `r_eff_status` and reports the rotation alone.

| Gate | Requirement | Why it is hard / why it matters |
| --- | --- | --- |
| `geometry_confirmed` | explicitly set | the factor $\Gamma = 2\delta$ is geometry-dependent |
| `sine_drive_confirmed` | explicitly set | $V_\mathrm{pp}/2\sqrt2$ holds only for a zero-offset sine |
| `lab_angle_mapping_trusted` | not `False` | an untrusted $\theta_i$ mapping invalidates the angular physics |
| QWP retardance | within $90^\circ \pm 10^\circ$ | otherwise it is not a Sénarmont compensator |
| $\lambda$ | measured, in nm | enters linearly |
| $t$ (film thickness) | traceable, with an uncertainty | enters linearly |
| $g$ (electrode gap) | **measured**, not nominal | enters linearly |
| $\alpha$ (field correction) | FEM of *this* electrode geometry | not transferable between devices; the largest systematic |
| $n$ | a parameter, not a constant (2.1 is a reasonable default) | enters as $n^3$ |
| $V_\mathrm{device}/V_\mathrm{source}$ | measured at 30 kHz with the device connected | cable and loading losses are real |
| $G_\mathrm{AC}/G_\mathrm{DC}$ | measured | if the detector's 30 kHz and DC transfers differ and you assume 1, everything scales wrongly |
| voltage linearity | $R^2 \ge 0.98$ (with $\ge 3$ levels) | proves the response is linear electro-optic |
| null leakage | $\le 5\,\%$ | proves the Malus model applies |

The propagated uncertainty adds each supplied input's relative standard
deviation in quadrature, with $n$ entering at exponent 3.

> **Why the software would rather print nothing.** A plausible-looking
> $r_\mathrm{eff}$ built on an assumed $\alpha$ is worse than no number,
> because it will be quoted, compared with the literature, and believed.
> Refusing, and naming the missing input, is the honest behaviour.

Even when it is emitted, $\lvert r_\mathrm{eff}\rvert$ is a
**geometry-specific effective coefficient**, never an intrinsic tensor element
— see [01 §8](01-electro-optics.md#8-why-the-answer-is-always-an-effective-coefficient).

---

## Continue

- [04 — Angular Dependence](04-incident-polarisation.md): sweeping the incident
  polarisation, and the physics test it provides.
- [05 — Ferroelectric Switching](05-ferroelectrics.md): sweeping a DC bias instead.
- [`../experiment/instruments.md`](../experiment/instruments.md): the lock-in
  settings (500 ms time constant, 12 dB/octave, 200 µV RMS full scale, fixed
  for a whole map) that set the noise floor assumed here.
- [`../reference/data-schema.md`](../reference/data-schema.md) for the CSV
  columns, and [`../guide/troubleshooting.md`](../guide/troubleshooting.md) for
  what to do when a gate fails.

---

<div align="center">

[← Polarisation formalism](02-polarisation.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Angular dependence →](04-incident-polarisation.md)

</div>
