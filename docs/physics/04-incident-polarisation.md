# Angular Dependence

**What this page is for:** to derive, rather than assert, why the response
depends on the incident polarisation; to show algebraically why the *signed*
response is a second harmonic while its *magnitude* is a fourth; to state
precisely which parameters an angular scan can and cannot determine; and to
explain why an experiment with four individually unknown reference frames
nevertheless produces a well-defined physical angle.

It assumes [01: The Pockels Effect](01-electro-optics.md), in particular the
table of which tensor components can be seen at normal incidence
([01 §5.3](01-electro-optics.md#53-which-components-can-be-seen-at-normal-incidence)),
and [03: The Null-Slope Sénarmont Readout](03-senarmont-readout.md), in
particular the result that the readout measures the change in the output
state's ellipticity
([03 §3](03-senarmont-readout.md#3-where-the-sénarmont-arrangement-comes-from)).

---

## 1. Why the response depends on the incident polarisation

The applied field lies in the film plane, along the electrode axis. The
optical polarisation lies at an angle $\theta_i$ to it. **Which tensor
components couple, and how strongly what they do shows up, depends on that
angle**, because the electro-optic effect is a tensor contraction and the
readout is a projection, neither of which is a scalar multiplication.

From [01 §5](01-electro-optics.md#5-why-the-shear-coefficient-makes-the-response-angle-dependent):
axial coefficients ($r_{13}$, $r_{33}$) change the *magnitude* of the
birefringence without moving the eigenaxes, while the shear coefficient
$r_{42}$ *rotates* the eigenaxes. §3 shows that these two produce angular
signatures in quadrature with one another, and mix in a way that depends on the
film's texture, its domain population and its static retardance. A
single-angle measurement therefore reports one arbitrary projection of the
tensor and cannot be interpreted on its own.

Sweeping $\theta_i$ buys three things.

1. **An operating point.** The strongest incident polarisation for *this*
   pixel, which is where the follow-up hysteresis measurement is then run.
2. **A physics test.** A genuine electro-optic signal *must* carry the angular
   signature derived in §3. Electrical pickup does not depend on the HWP angle
   at all, so a response that is flat in $\theta_i$ is not optical.
3. **A compositional observable.** The amplitude and phase of the fit encode
   the electro-optic anisotropy, which tracks texture and domain population and
   therefore varies systematically across the chip's compositional gradient.

The half-wave plate is the clean way to sweep $\theta_i$: by
[02 §4.5](02-polarisation.md#45-the-two-special-cases-that-run-this-experiment)
it rotates the polarisation without changing the intensity and without
introducing ellipticity, and because it *reflects* the polarisation about its
fast axis, a motor rotation of $\Delta$ gives a polarisation rotation of
$2\Delta$. A $180^\circ$ scan of $\theta_i$ therefore costs only $90^\circ$ of
motor travel.

The production grid is **9 points spaced $22.5^\circ$ in $\theta_i$**, that is
$11.25^\circ$ of motor or exactly 4480 encoder counts per step, centred on the
calibrated lab-frame $\theta_i = 81.8688^\circ$, which corresponds to the
manually verified high-response position HWP(raw) $= 7.8951^\circ$. The grid
walks exactly once around the Poincaré equator
([02 §15.2](02-polarisation.md#152-the-hwp-grid-closes-the-equator-exactly)).

---

## 2. The signed model

At each $\theta_i$ the background-corrected complex phasor is normalised by the
measured local Malus slope and by the source $V_\mathrm{rms}$, giving the
normalised rotation slope in rad per volt RMS. Both quadratures are then fitted
**together** with

$$
\delta(\theta_i) \;=\; C_0 \;+\; C_c \cos 2\theta_i \;+\; C_s \sin 2\theta_i ,
\tag{1}
$$

where $C_0$, $C_c$ and $C_s$ are **complex**.

Two features of that sentence are load-bearing.

**The coefficients are complex** because the lock-in measures a phasor,
amplitude *and* phase. Fitting the magnitude alone would throw away the phase,
and the phase is where the sign lives. Fitting $X$ and $Y$ simultaneously with
complex coefficients keeps the sign reversal intact
([03 §10](03-senarmont-readout.md#10-the-full-complex-model)).

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
([03 Step 3](03-senarmont-readout.md#step-3-the-effective-coefficient-and-its-gates)).

---

## 3. The $2\theta$ dependence, derived

Equation (1) is not a curve-fitting convenience. It is the exact form the
response must take, and this section derives it from the tensor projection of
[01](01-electro-optics.md) and the readout geometry of
[03](03-senarmont-readout.md).

### 3.1 The pixel as a single retarder

Model the illuminated volume as one linear retarder with static retardance
$\Gamma_0$ and in-plane eigenaxis at lab angle $\theta_f$. Let the incident
polarisation be linear at $\theta_i$, and define

$$
\Theta \;\equiv\; \theta_i - \theta_f
\tag{2}
$$

as the angle between the incident polarisation and the film's own static
eigenaxis. On the Poincaré sphere, build the orthonormal frame
$\left\{\hat{\mathbf{n}}_f,\ \hat{\mathbf{q}},\ \hat{\mathbf{e}}_3\right\}$
with $\hat{\mathbf{n}}_f$ the retarder axis (equatorial, at longitude
$2\theta_f$), $\hat{\mathbf{q}} = \hat{\mathbf{e}}_3\times\hat{\mathbf{n}}_f$
and $\hat{\mathbf{e}}_3$ the polar axis. The incident state, being linear and
equatorial, is

$$
\hat{\mathbf{s}}_\mathrm{in} \;=\; \cos 2\Theta\;\hat{\mathbf{n}}_f
\;+\; \sin 2\Theta\;\hat{\mathbf{q}} .
\tag{3}
$$

Applying Rodrigues' formula
([02 §12](02-polarisation.md#12-a-retarder-is-a-rotation-rodrigues-written-out))
for a rotation by $\Gamma_0$ about $\hat{\mathbf{n}}_f$, and noting
$\hat{\mathbf{n}}_f\times\hat{\mathbf{q}} = \hat{\mathbf{e}}_3$,

$$
\hat{\mathbf{s}}_\mathrm{out} \;=\;
\cos 2\Theta\;\hat{\mathbf{n}}_f
\;+\; \sin 2\Theta\left(\cos\Gamma_0\;\hat{\mathbf{q}} + \sin\Gamma_0\;\hat{\mathbf{e}}_3\right),
\tag{4}
$$

so the third Stokes component of the light leaving the pixel, which by
[03 §3](03-senarmont-readout.md#3-where-the-sénarmont-arrangement-comes-from)
is the only thing the Sénarmont readout responds to, is

$$
\boxed{\;
s_3^\mathrm{out} \;=\; \sin 2\chi_o \;=\; \sin 2\Theta \,\sin\Gamma_0 .
\;}
\tag{5}
$$

The measured rotation is $\delta = -\tfrac{1}{2}\,\delta(2\chi_o)$, that is

$$
\delta \;=\; -\,\frac{\delta s_3^\mathrm{out}}{2\cos 2\chi_o},
\qquad
\cos 2\chi_o = \sqrt{1 - \sin^2 2\Theta\,\sin^2\Gamma_0}.
\tag{6}
$$

### 3.2 The two mechanisms, differentiated

The field perturbs (5) in exactly two ways, corresponding to the two rows of
the table in
[01 §5.3](01-electro-optics.md#53-which-components-can-be-seen-at-normal-incidence).

**Axial mechanism.** The birefringence magnitude changes,
$\Gamma_0 \to \Gamma_0 + \Delta\Gamma$, with the eigenaxes fixed:

$$
\delta s_3^\mathrm{out} \;=\; \frac{\partial s_3^\mathrm{out}}{\partial \Gamma_0}\,\Delta\Gamma
\;=\; \Delta\Gamma\;\cos\Gamma_0\;\sin 2\Theta .
\tag{7}
$$

**Shear mechanism.** The eigenaxes rotate, $\theta_f \to \theta_f + \rho$, with
$\rho$ given by [01 eq. (13)](01-electro-optics.md#52-the-eigenvalue-problem-for-the-induced-axes), and the magnitude fixed. Since $\Theta = \theta_i-\theta_f$,
this is $\Theta \to \Theta - \rho$:

$$
\delta s_3^\mathrm{out} \;=\; -\rho\,\frac{\partial s_3^\mathrm{out}}{\partial \Theta}
\;=\; -\,2\rho\;\sin\Gamma_0\;\cos 2\Theta
\;\equiv\; -\,\Gamma_s\,\cos 2\Theta ,
\tag{8}
$$

which defines the **shear amplitude** $\Gamma_s \equiv 2\rho\sin\Gamma_0$. It
is the right variable because its two factors pull in opposite directions as
the static birefringence of the pixel disappears. By 01 eq. (13) the axis
rotation $\lvert\rho\rvert \approx \tfrac{1}{2}n^3 r_{42}E/\Delta n_s$
diverges as $\Delta n_s \to 0$, while $\Gamma_0 = 2\pi\Delta n_s t/\lambda$
vanishes, and the product stays finite:

$$
\Gamma_s \;\xrightarrow[\;\Gamma_0 \ll 1\;]{}\; \frac{2\pi\, n^3 r_{42} E\, t}{\lambda},
\qquad\text{independent of } \Delta n_s .
\tag{8a}
$$

Physically, a shear perturbation applied to a film with no static
birefringence does not rotate an existing retarder; it *creates* a small one,
with eigenaxes at $\pm45^\circ$ to the field and retardance (8a), and that
retarder is fully visible at normal incidence. Equation (8a) is verified
numerically from the exact eigenvectors of the impermeability matrix in
[`tests/test_docs_physics_claims.py`](../../tests/test_docs_physics_claims.py).

Putting (7) and (8) into (6):

$$
\boxed{\;
\delta(\theta_i) \;=\; \frac{-1}{2\cos 2\chi_o}
\Big[\;\underbrace{\Delta\Gamma\,\cos\Gamma_0\,\sin 2\Theta}_{\text{axial}}
\;-\; \underbrace{\Gamma_s\,\cos 2\Theta}_{\text{shear}}\;\Big],
\qquad \Gamma_s = 2\rho\sin\Gamma_0 .
\;}
\tag{9}
$$

Equation (9) is the central result of this page, and five things follow from
it directly.

| Result | Where it comes from in (9) |
| --- | --- |
| **The signed response is a second harmonic in $\theta_i$, to leading order in $\Gamma_0$.** | both terms are $\sin 2\Theta$ or $\cos 2\Theta$, and $\Theta = \theta_i - \theta_f$; the prefactor $1/\cos 2\chi_o$ adds higher harmonics that vanish as $\Gamma_0 \to 0$ (see below) |
| **The two mechanisms are exactly in quadrature.** | $\sin 2\Theta$ against $\cos 2\Theta$: they peak $45^\circ$ apart in $\theta_i$ |
| **The shear mechanism survives zero static retardance.** | its weight is $\Gamma_s = 2\rho\sin\Gamma_0$, and (8a) says the product stays finite as $\Gamma_0 \to 0$: the shear perturbation then creates a small retarder with its axes at $\pm45^\circ$ to the field instead of rotating an existing one |
| **The axial mechanism is invisible at quarter-wave static retardance.** | its weight is $\cos\Gamma_0$, which vanishes at $\Gamma_0 = 90^\circ$ |
| **The relative weight of the two depends on $\Gamma_0$, which varies pixel to pixel.** | the $\cos\Gamma_0$ and $\Gamma_s = 2\rho\sin\Gamma_0$ weights |

The last row is a caveat that is easy to miss and impossible to remove: two
pixels with identical tensors but different static retardances will report
different mixtures of the same two mechanisms. It is one more entry in the
list of reasons the reported coefficient is an *effective* one
([01 §9](01-electro-optics.md#9-why-the-answer-is-always-an-effective-coefficient)).

The first row is exact only as $\Gamma_0 \to 0$. The prefactor
$1/\cos 2\chi_o = 1/\sqrt{1 - \sin^2 2\Theta\,\sin^2\Gamma_0}$ in (9) depends
on $\Theta$, so the signed response acquires higher odd harmonics; the leading
one is a $6\theta_i$ component of relative amplitude $\sin^2\Gamma_0/8$ to
leading order, about 3 percent at $\Gamma_0 = 30^\circ$ (3.7 percent exactly,
computed in [`tests/test_docs_physics_claims.py`](../../tests/test_docs_physics_claims.py)). A high
`r_squared_complex` on the $2\theta_i$ model is therefore itself evidence that
$\Gamma_0$ is small at that pixel.

### 3.3 Recovering the ideal Sénarmont limit

Set $\Theta = 45^\circ$ in (9). The shear term vanishes identically, because
$\cos 2\Theta = 0$, and $\cos 2\chi_o = \sqrt{1 - \sin^2\Gamma_0} = \lvert\cos\Gamma_0\rvert$,
so

$$
\delta \;=\; -\tfrac{1}{2}\,\frac{\cos\Gamma_0}{\lvert\cos\Gamma_0\rvert}\,\Delta\Gamma
\qquad\Longleftrightarrow\qquad
\lvert\Gamma\rvert \;=\; 2\lvert\delta\rvert ,
\tag{10}
$$

for **every** static retardance, not only a small one. The sign flips as
$\Gamma_0$ passes through $90^\circ$, where the output state is circular and
the null pair is degenerate; the magnitude does not change. This is the
classical Sénarmont theorem, and it is worth seeing why it is exact. With the
incident polarisation bisecting the film's static eigenaxes, (4) reads
$\hat{\mathbf{s}}_\mathrm{out} = \cos\Gamma_0\,\hat{\mathbf{q}} + \sin\Gamma_0\,\hat{\mathbf{e}}_3$:
the output state sits on the great-circle meridian through $\hat{\mathbf{q}}$
at latitude exactly $\Gamma_0$, and a retardance change moves it along that
meridian to latitude exactly $\Gamma_0 + \Delta\Gamma$. By
[03 §3](03-senarmont-readout.md#3-where-the-sénarmont-arrangement-comes-from)
the compensator converts a latitude change into an equal longitude change,
and longitude is twice azimuth, so the compensated azimuth moves by exactly
$\Delta\Gamma/2$. Chapter 07 reaches the same result in Jones form
([07 §1.3](07-instrument-theory.md#13-what-the-quarter-wave-plate-does-algebraically)),
and [`tests/test_docs_physics_claims.py`](../../tests/test_docs_physics_claims.py)
checks it numerically from the Jones chain for $\Gamma_0$ up to 1.4 rad.
Equation (10) is the conversion the software applies
([03 §11.2](03-senarmont-readout.md#112-projecting-the-ac-response-onto-the-dc-derivative)).

Outside $\Theta = 45^\circ$ the conversion carries the geometric factor

$$
\frac{2\delta}{\Delta\Gamma} \;=\; \frac{\cos\Gamma_0 \sin 2\Theta}{\cos 2\chi_o}
\;=\; \frac{\cos\Gamma_0\,\sin 2\Theta}{\sqrt{1 - \sin^2 2\Theta\,\sin^2\Gamma_0}},
\tag{11}
$$

whose magnitude is at most 1 and can be far smaller. Note the direction of
the bias: the factor is never greater than one, so an uncorrected conversion
always **under**-reports $\Delta\Gamma$ and hence $r_\mathrm{eff}$. Two
consequences follow for how the operating point is chosen.

1. **An axial-dominated pixel peaks at $\Theta = 45^\circ$ exactly.** The
   response (11) is monotonic in $\sin 2\Theta$ whatever $\Gamma_0$ is, so
   the HWP angle at which the measured response peaks is the angle at which
   (10) is exact. Choosing the peak lands on the exact point, and the test
   file checks the monotonicity too.
2. **What `geometry_confirmed` asserts** is therefore not that $\Gamma_0$ is
   small. It asserts that the incident polarisation bisects the film's static
   eigenaxes and that the response there is axial. The residual approximation
   in (10) is **mechanism mixing**: a shear contribution $\Gamma_s$ shifts the
   measured peak away from $\Theta = 45^\circ$ and adds, in quadrature, a term
   that (10) would misread as retardance. That is a statement about the
   pixel's tensor projection ([§6](#6-why-an-angular-scan-alone-is-degenerate)),
   not about its static birefringence.

---

## 4. Harmonic 2 for the signed response, harmonic 4 for its magnitude

§3 derived the harmonic-2 structure of the signed response from the physics.
This section derives the harmonic-4 structure of its magnitude from algebra,
because the relation between the two is the single most common source of
confusion in reading these plots.

### 4.1 The algebra

Take (1) and form the squared magnitude
$\lvert\delta\rvert^2 = \delta\,\delta^{*}$:

$$
\lvert\delta\rvert^2 = \lvert C_0\rvert^2 + \lvert C_c\rvert^2\cos^2 2\theta
+ \lvert C_s\rvert^2\sin^2 2\theta
+ 2\mathrm{Re}\!\left(C_0^{*}C_c\right)\cos 2\theta
+ 2\mathrm{Re}\!\left(C_0^{*}C_s\right)\sin 2\theta
+ 2\mathrm{Re}\!\left(C_c^{*}C_s\right)\sin 2\theta\cos 2\theta .
$$

Using $\cos^2 2\theta = \tfrac{1}{2}(1+\cos4\theta)$,
$\sin^2 2\theta = \tfrac{1}{2}(1-\cos4\theta)$ and
$\sin2\theta\cos2\theta = \tfrac{1}{2}\sin4\theta$, this sorts into harmonics:

$$
\lvert\delta\rvert^2 \;=\;
\underbrace{\lvert C_0\rvert^2 + \tfrac{1}{2}\left(\lvert C_c\rvert^2 + \lvert C_s\rvert^2\right)}_{\text{harmonic }0}
\;+\; \underbrace{2\mathrm{Re}\!\left(C_0^{*}C_c\right)\cos 2\theta + 2\mathrm{Re}\!\left(C_0^{*}C_s\right)\sin 2\theta}_{\text{harmonic }2,\ \propto\, C_0}
$$
$$
\;+\; \underbrace{\tfrac{1}{2}\left(\lvert C_c\rvert^2 - \lvert C_s\rvert^2\right)\cos 4\theta
+ \mathrm{Re}\!\left(C_c^{*}C_s\right)\sin 4\theta}_{\text{harmonic }4}.
\tag{12}
$$

Equation (12) says three exact things.

1. **Squaring a second harmonic produces a fourth.** That is the whole origin
   of the four-lobed polar plot: nothing has been added, the sign has been
   removed.
2. **The second-harmonic content of the magnitude is proportional to $C_0$
   alone.** If the offset vanishes, the lobes are exactly equal; a non-zero
   $C_0$ is *exactly* what makes them unequal, and nothing else does.
3. **The harmonic-4 amplitude measures the anisotropy of the two complex
   coefficients**, through $\lvert C_c\rvert^2 - \lvert C_s\rvert^2$ and
   $\mathrm{Re}(C_c^{*}C_s)$.

### 4.2 Why the magnitude fit has a ceiling below $R^2 = 1$

The code fits $\lvert\delta\rvert$, not $\lvert\delta\rvert^2$, with a
$\left[\,1,\ \cos 4\theta_i,\ \sin 4\theta_i\,\right]$ basis. Take the ideal
single-mechanism case, $C_0 = 0$ with $C_c$ and $C_s$ sharing a temporal
phase, so that

$$
\delta(\theta) = e^{i\gamma}R\cos\!\left(2\theta - 2\theta_0\right),
\qquad
\lvert\delta(\theta)\rvert = R\left\lvert\cos\left(2\theta-2\theta_0\right)\right\rvert .
\tag{13}
$$

The Fourier series of a rectified cosine is

$$
\left\lvert\cos u\right\rvert = \frac{2}{\pi} + \frac{4}{\pi}\sum_{k=1}^{\infty}
\frac{(-1)^{k+1}}{4k^2-1}\cos 2ku ,
\tag{14}
$$

so with $u = 2(\theta-\theta_0)$ the magnitude contains harmonics
$4\theta, 8\theta, 12\theta, \dots$, with amplitudes in the ratio
$1 : 0.20 : 0.086 : \dots$. A pure $4\theta$ basis therefore cannot reproduce
$\lvert\delta\rvert$ exactly even for perfect noiseless data. Comparing the
explained variance $\tfrac{1}{2}a_4^2$ with the total
$\tfrac{1}{2} - 4/\pi^2$ gives the ceiling

$$
R^2_\mathrm{magnitude,\ max} \;=\;
\frac{\tfrac{1}{2}\left(4/3\pi\right)^2}{\tfrac{1}{2} - 4/\pi^2} \;\approx\; 0.95
\tag{15}
$$

for a densely sampled ideal response. **`r_squared_magnitude` around 0.95 is
therefore a perfect result, not a slightly disappointing one**, and it is one
more reason the gate is applied to the signed fit rather than to the magnitude
one. With the 9-point grid the exact ceiling shifts slightly, because the
residual harmonics alias onto the sampled ones.

### 4.3 The picture

![Signed two-fold response versus four-lobed magnitude](../../assets/figures/angular_harmonics.png)

*Figure 1. The same data, two ways.* The left panel plots the **signed**
normalised response against $\theta_i$: a single sinusoid in $2\theta_i$, one
full period over $180^\circ$, crossing zero twice and changing sign each time.
The right panel plots its **magnitude** on polar axes over the full
$360^\circ$: four lobes, because taking $\lvert\cdot\rvert$ folds the negative
half of each period up onto the positive side and so doubles the apparent
harmonic, exactly as eq. (12) says. The zero crossings of the left panel are
the nulls *between* the lobes of the right panel, at exactly the same angles.
Nothing has been added by the polar representation; what has been *removed* is
the sign, the one piece of information distinguishing a rotation of one
handedness from the other.

### 4.4 The bug this fixed

> **This is a real bug that was found and fixed, and it is worth
> understanding.** Early v2 runs fitted the **signed complex** response with a
> $4\theta$ model, on the reasoning that "the polar plot has four lobes, so the
> harmonic is 4". Equation (9) shows that is exactly backwards. A $4\theta$
> basis cannot represent a function that changes sign every $90^\circ$, so the
> fit was forced to compromise and produced misleadingly low angular $R^2$
> values, which then looked like bad data or a bad measurement rather than a
> bad model.
>
> The fix is the module constant
> **`DEFAULT_ANGULAR_HARMONIC = 2`** in
> [`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py).
> The signed complex response is fitted at harmonic 2; the **magnitude**
> receives a *separate* direct $4\theta_i$ fit, reported as
> `r_squared_magnitude`, purely as the polar diagnostic, with the ceiling of
> (15) in mind.

The practical rule: **fit signed, plot magnitude.** Never fit the thing you
plot.

The fit reports both scores, `r_squared_complex` for the signed $2\theta_i$
model and `r_squared_magnitude` for the $4\theta_i$ magnitude diagnostic, along
with a dense theory curve and a continuous peak angle. It requires at least 5
distinct, geometry-certified angles, and the validity threshold is
$R^2 \ge 0.80$.

> **Peak selection is deliberately conservative.** Hardware follow-ups use the
> strongest *actually measured* geometry-certified point, not the continuous
> fitted maximum: a smooth model must not move the measurement away from a
> stronger acquired point. If the fit is below $R^2 = 0.80$ the selection falls
> back to the largest geometry-certified normalised measured response, and
> never to a raw lock-in magnitude, because rectified pickup could then set the
> HWP angle.

---

## 5. What the fit is fitted to

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

## 6. Why an angular scan alone is degenerate

**What a four-lobed polar plot proves** is that the measured response has the
angular symmetry of a linear electro-optic effect in this geometry. That is a
genuine and non-trivial result: it rules out any contribution that does not
depend on the incident polarisation, which is most of the ways this
measurement can go wrong, including electrical pickup, laser amplitude
modulation, detector artefacts and thermal drift. A flat polar plot is a failed
measurement, and it fails in a way you can see at a glance.

**What it does not prove** can now be stated as a counting argument rather
than as a list of misgivings.

### 6.1 The counting argument

Suppose both mechanisms respond instantaneously to the same drive, so
$C_c$ and $C_s$ in (1) share a temporal phase $\gamma$. Then, with $C_0$ set
aside as an offset that (12) shows is separately identifiable,

$$
\delta(\theta_i) \;=\; e^{i\gamma}\,R\,\cos\!\left(2\theta_i - 2\theta_0\right).
\tag{16}
$$

The angular scan therefore yields exactly **three** real numbers, of which
$\gamma$ is the lock-in reference phase and carries no geometric information.
**Two geometrically meaningful numbers come out: an amplitude $R$ and a
preferred angle $\theta_0$.**

Now count what goes in. Equation (9) contains

| Parameter | Meaning |
| --- | --- |
| $a$ | axial amplitude, $\propto n_e^3 r_c E_\parallel t$ |
| $s$ | shear amplitude, $\propto \Gamma_s = 2\rho\sin\Gamma_0$, which tends to $2\pi n^3 r_{42}E_\perp t/\lambda$ for small $\Gamma_0$, eq. (8a) |
| $\theta_f$ | the film's static in-plane eigenaxis |
| $\Gamma_0$ | the static retardance of this pixel |
| $\theta_E$ | the field direction in the polarisation frame |

Five unknowns, two observables. The map from parameters to data is five to two
and is therefore **generically degenerate by three**: a three-parameter family
of physically distinct films reproduces any measured $(R, \theta_0)$ exactly.
No amount of angular data at a single pixel, a single static retardance and a
single electrode orientation can break it, because more angles only measure
$(R, \theta_0)$ better.

| Claim | Why the polar plot cannot support it |
| --- | --- |
| "the response is $r_{42}$-dominated" | $a$ and $s$ enter (9) in quadrature, so they trade off against $\theta_f$ and $\Gamma_0$ within the three-parameter degeneracy |
| "the crystal $a$ and $c$ axes lie at angle $X$" | $\theta_f$ is not separately observable, and the lab-frame orientation of the crystal never enters the measurement (§7) |
| "the film has a particular domain configuration" | many texture and domain populations produce indistinguishable angular signatures once averaged over the optical mode |
| "$r_{42} = {}$ some number" | separating tensor elements requires a tensor, texture and domain model **plus** independent structural information such as XRD or TEM [[26]](../references.md#ref-26) |

### 6.2 What would close it

The counting argument is constructive, which is more useful than a warning.

1. **$\Gamma_0$ and $\theta_f$ are in principle already measured.** The null
   pair $(q_\mathrm{null}, a_\mathrm{null})$ determines the output state
   $\hat{\mathbf{s}}_\mathrm{out}$ completely, by (4) of
   [03](03-senarmont-readout.md#3-where-the-sénarmont-arrangement-comes-from)
   for the quarter-wave plate angle and the azimuth for the analyser angle.
   Two measured angles fix the two free parameters of a point on the sphere,
   and (4) above then determines $(\Gamma_0, \theta_f)$ up to discrete
   ambiguities. With nine incident angles the system is heavily
   over-determined. **The software records the null pair per pixel and per HWP
   angle but does not currently invert it for $(\Gamma_0, \theta_f)$;** doing
   so is the obvious next step and needs no new hardware.
2. **$\theta_E$ can be pinned by the chip layout.** The electrode direction is
   fixed by lithography, so one registration of the chip frame against the
   polariser frame fixes $\theta_E$ for every pixel
   ([`../experiment/chip.md`](../experiment/chip.md)). A device carrying two
   electrode orientations would supply it internally.
3. **That leaves two unknowns, $a$ and $s$, against two observables**, which is
   identifiable.

Even then, $a$ and $s$ are *effective* amplitudes averaged over the domain
population and the depth of the film, so a tensor assignment still requires
independent structural information. What the data support without any of this
is a strong statement in its own right: *the angular signature is consistent
with a linear electro-optic response, and its amplitude and phase vary
systematically across the chip.* The tensor assignment is a separate paper.

---

## 7. The reference-frame argument

This is the part that most often worries a reader, so it is worth setting out
carefully. **Four quantities in this experiment are individually unknown:**

1. the BaTiO₃ crystal axes;
2. the electrode direction relative to those axes;
3. the electrode direction in the lab frame;
4. each rotator's encoder zero, since the ELL14 mechanical home index is
   **not** aligned with the waveplate's fast axis and the offset is arbitrary
   per unit [[38]](../references.md#ref-38).

None of the four is ever measured, and none of them needs to be, because every
quantity the instrument reports is *relative*.

**The polariser defines the frame.** Its transmission axis is the zero of every
angle in the experiment. The three-step optical calibration expresses every
other optic in that frame:

| Calibration output | Meaning |
| --- | --- |
| `a_ref_min`, `a_ref_max` | analyser encoder angles for extinction and for parallel with the polariser |
| `gamma0` | QWP encoder angle with its fast axis parallel to the polarisation (an encoder reference, not the static retardance $\Gamma_0$) |
| `q_null`, `a_null`, `p_null` | the sample-in null pair and its residual power |
| `HWP_fixed` | HWP encoder reference for the starting polarisation |

Unknowns 3 and 4 are absorbed here: a rotator's arbitrary encoder zero becomes
a fitted offset in the polarisation frame, and the electrode direction in the
lab never appears because no lab-frame angle is ever used.

**Every readout is relative to that pixel's own null.** The analyser offset
$\psi$ is measured from `a_null`, and the QWP from `q_null`, both re-verified
per pixel. Static birefringence, the per-pixel $\Gamma_0$ and $\theta_f$ that
unknowns 1 and 2 would be needed to predict, cancels identically, because it is
what defines the null in the first place
([03 §4](03-senarmont-readout.md#4-what-the-qwp-does-geometrically)).

**What survives is a difference of angles, which is frame-independent.** In
(9) the only angles that appear are $\Theta = \theta_i - \theta_f$ and, through
$\theta_f$, the orientation of the field relative to the crystal. Both are
differences. Adding a constant to every angle in the experiment leaves the
measured response unchanged, which is exactly the statement that the choice of
frame is arbitrary.

---

## 8. Self-calibrating the field direction

That leaves exactly one non-optical unknown that the optical calibration cannot
reach: the **direction of the applied E field** in the polarisation frame. It
is measured, not assumed, by the simplest possible experiment: **sweep the HWP
with the AC drive on and find the lock-in peak.**

Let the peak occur at the calibrated incident polarisation
$\theta_{i,\mathrm{peak}}$, read through the lab mapping of §2 and never from
the raw HWP encoder angle: the naive $2\theta_\mathrm{HWP}$ is not used
anywhere in the analysis, and a missing mapping blocks the physics rather than
substituting for it. Equation (9) then gives the peak position for each
limiting case.

| If the response is | The signal goes as | The peak occurs at | So the field lies along |
| --- | --- | --- | --- |
| axial-dominated, $r_{13}$ and $r_{33}$ | $\sin 2\left(\theta_i - \theta_f\right)$ with $\theta_f = \theta_E$ | $\theta_i = 45^\circ$ to the field | polarisation-frame angle $\theta_{i,\mathrm{peak}} - 45^\circ$ |
| shear-dominated, $r_{42}$ | $\cos 2\left(\theta_i - \theta_f\right)$ with $\theta_f = \theta_E \pm 90^\circ$ | $\theta_i = 0^\circ$ or $90^\circ$ to the field | polarisation-frame angle $\theta_{i,\mathrm{peak}}$ |

> **Note: this table is the reverse of a rule of thumb that is sometimes
> quoted, and the derivation is the reason.** A change in the *magnitude* of a
> retardance has most effect when the input is split equally between the two
> eigenaxes, at $45^\circ$; a *rotation* of the eigenaxes has most effect when
> the input lies along one of them, at $0^\circ$. Equations (7) and (8) are
> that statement. The connection to the field then follows from
> [01 §5.3](01-electro-optics.md#53-which-components-can-be-seen-at-normal-incidence):
> the axial mechanism is driven by the field component **along** the local
> polar axis, so its eigenaxis and the field coincide, while the shear
> mechanism is driven by the component **perpendicular** to it, so they are
> $90^\circ$ apart. The limit of vanishing static retardance confirms the
> table from the other side: by (8a) the shear perturbation then creates a
> small retarder with its axes at $\pm45^\circ$ to the field, and by (7) a
> retardance change is seen best with the input at $45^\circ$ to the
> retarder's own axes, that is, along or across the field.

Absolute magnitude, and the signs of the slopes at $\pm45^\circ$ either side of
the peak, help distinguish the two regimes. But, as §6 showed by counting, the
final tensor assignment always needs independent structural information. What
the sweep *does* give unconditionally is a reproducible, data-derived field
direction in the same frame as everything else, which is all the measurement
requires.

> **Note.** This is self-calibration in the strict sense: the quantity is
> extracted from the same measurement it is needed for, using a symmetry
> argument rather than an external standard. It is robust precisely because the
> angular signature it relies on is the one thing pickup cannot fake.

---

## Further reading

| Topic | Start here |
| --- | --- |
| Tensor contraction and point-group reduction | Nye [[2]](../references.md#ref-2) |
| Induced axes and the indicatrix under a field | Yariv and Yeh [[3]](../references.md#ref-3) |
| Measured BaTiO₃ tensor values behind the two mechanisms | Zgonik and co-workers [[7]](../references.md#ref-7) |
| The sphere geometry used throughout §3 | Poincaré [[9]](../references.md#ref-9) |
| Why domain and texture models are needed to go further | Tagantsev, Cross and Fousek [[26]](../references.md#ref-26) |
| Complex linear least squares and what $R^2$ means here | Lawson and Hanson [[44]](../references.md#ref-44) |

Full bibliography: [`../references.md`](../references.md).

---

## Continue

- [05: Ferroelectric Switching](05-ferroelectrics.md): what happens at the
  selected peak angle when a DC bias is swept.
- [01: The Pockels Effect](01-electro-optics.md): the tensor origin of the
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
