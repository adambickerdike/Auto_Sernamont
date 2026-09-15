# Numerical Methods and Algorithms

**What this page is for:** the mathematics inside the code. For every
non-trivial algorithm in the package it states the problem, gives the
mathematics, says why *this* method and not an alternative, quotes its cost or
convergence rate, names the constants the code actually uses, and links to the
implementation. The physics those algorithms serve is in
[Theory of the Instrument](../physics/07-instrument-theory.md); the
operational recipe for the motion searches is in
[Motion Control](motion-control.md). This page is the layer in between.

---

## Contents

| # | Algorithm | Where it lives |
| --- | --- | --- |
| 1 | [Phasor averaging and rectification bias](#1-phasor-averaging-and-rectification-bias) | `phasor_statistics()` |
| 2 | [Linear least squares in closed form](#2-linear-least-squares-in-closed-form) | `fit_sinusoid_response()`, `fit_dc_malus_response()` |
| 3 | [The derivative-aligned projection](#3-the-derivative-aligned-projection) | `joint_senarmont_from_fits()` |
| 4 | [Two-dimensional null searching](#4-two-dimensional-null-searching) | `null_descent()`, `null_2d_parabolic()`, `null_fast_rsm()` |
| 5 | [One-dimensional peak finding](#5-one-dimensional-peak-finding) | `golden_section_search()`, `hill_climb_peak()` |
| 6 | [Robust statistics](#6-robust-statistics) | `read_averaged()`, `read_power_w_stable()` |
| 7 | [Nonlinear curve fitting](#7-nonlinear-curve-fitting) | `_tanh_branch_fit()`, `fit_poling_kinetics()` |
| 8 | [Differentiation and interpolation in the loop analysis](#8-numerical-differentiation-and-interpolation-in-the-loop-analysis) | `pockels_hysteresis_analysis.py` |
| 9 | [The predictive lock-in range controller](#9-the-predictive-lock-in-range-controller) | `PredictiveHystereticRangeController` |
| 10 | [The lock-in as a synchronous detector](#10-the-lock-in-as-a-synchronous-detector) | DSP7230 configuration |
| 11 | [Numerical hygiene](#11-numerical-hygiene) | everywhere |

---

## 1. Phasor averaging and rectification bias

### 1.1 The problem

The DSP7230 returns a magnitude and a phase. Averaging $n$ repeated readings
looks like a choice between averaging $R$ or averaging $(X, Y)$. It is not a
choice: **the mean of magnitudes is a biased estimator of the magnitude of the
mean**, and the bias is worst exactly where the physics is most interesting.

### 1.2 The Rice distribution and the size of the bias

Model one reading as a true phasor $\mu$ plus circular complex Gaussian noise
of variance $\sigma^2$ per quadrature. Then $R = \lvert Z\rvert$ follows the
Rice distribution, derived by Rice [[49]](../references.md#ref-49):

$$
f(R) = \frac{R}{\sigma^2}\,
\exp\!\left(-\frac{R^2 + A^2}{2\sigma^2}\right) I_0\!\left(\frac{RA}{\sigma^2}\right),
\qquad A = \lvert\mu\rvert .
\qquad (1)
$$

Two limits of equation (1) are exact and are all that is needed here:

$$
\mathbb{E}[R]\big|_{A = 0} = \sigma\sqrt{\tfrac{\pi}{2}} = 1.2533\,\sigma ,
\qquad
\mathbb{E}[R]\big|_{A \gg \sigma} \simeq A + \frac{\sigma^2}{2A},
\qquad
\mathbb{E}[R^2] = A^2 + 2\sigma^2 \ \text{exactly}.
\qquad (2)
$$

The first of these is the killer. **When the true signal is zero, the average
of magnitudes is $1.25\sigma$, not zero**, and no amount of averaging reduces
it: it is a bias, not a variance.

### 1.3 Why this matters at a coercive point

Consider a hysteresis branch crossing its coercive voltage, where the true
signed response passes through zero, with a per-quadrature noise of
$\sigma = 0.5\ \mu$V and a saturation magnitude of $5\ \mu$V.

| Estimator | Value at the coercive point | Consequence |
| --- | --- | --- |
| mean of magnitudes | $1.2533 \times 0.5 = 0.63\ \mu$V | a floor at 12.5 % of saturation that never reaches zero |
| magnitude of the mean | $\to 0$ as $n$ grows | crosses zero cleanly |
| signed projection $S = X\cos\varphi_\mathrm{ref} + Y\sin\varphi_\mathrm{ref}$ | unbiased, changes sign | the coercive voltage is a **zero crossing**, so it can be interpolated |

A rectified loop has a *minimum*, not a zero, so the coercive voltage becomes
unmeasurable and the loop acquires a spurious pinch. This is the entire reason
the code records $X$ and $Y$ on every point and why
[`signed_projection()`](../../pockels/pockels_hysteresis_analysis.py) exists.
See [05 Ferroelectric Switching](../physics/05-ferroelectrics.md) for the
physical reading of the signed loop.

### 1.4 Covariance propagation into magnitude and phase

[`phasor_statistics()`](../../pockels/pockels_measurement_analysis.py)
converts each $(R_k, \phi_k)$ to Cartesian form, averages there, and then
propagates the sample covariance by the delta method. Let

$$
\hat{u} = \frac{(\bar{x}, \bar{y})}{\lvert\bar{Z}\rvert}
\quad\text{(radial)},
\qquad
\hat{t} = \frac{(-\bar{y}, \bar{x})}{\lvert\bar{Z}\rvert}
\quad\text{(tangential)},
\qquad
\Sigma = \begin{pmatrix}\sigma_x^2 & \sigma_{xy}\\ \sigma_{xy} & \sigma_y^2\end{pmatrix}.
\qquad (3)
$$

For any smooth $g(X,Y)$ the delta method gives
$\mathrm{Var}[g] = \nabla g^{\mathsf{T}}\Sigma\,\nabla g$. With
$g = \sqrt{X^2+Y^2}$ the gradient is $\hat{u}$; with $g = \mathrm{atan2}(Y,X)$
it is $\hat{t}/\lvert\bar{Z}\rvert$. Hence

$$
\sigma_R^2 = \hat{u}^{\mathsf{T}}\Sigma\,\hat{u}
= u_x^2\sigma_x^2 + u_y^2\sigma_y^2 + 2u_xu_y\sigma_{xy},
\qquad (4)
$$

$$
\sigma_\phi^2 = \frac{\hat{t}^{\mathsf{T}}\Sigma\,\hat{t}}{\lvert\bar{Z}\rvert^2}
= \frac{u_y^2\sigma_x^2 + u_x^2\sigma_y^2 - 2u_xu_y\sigma_{xy}}{\lvert\bar{Z}\rvert^2},
\qquad (5)
$$

and the standard errors of the means are these divided by $\sqrt{n}$. Equations
(4) and (5) are the code line for line. The sample covariance uses `ddof=1`,
the unbiased convention.

> **The degenerate case is handled explicitly.** When $\lvert\bar{Z}\rvert = 0$
> the radial and tangential directions are undefined, so the code falls back to
> $\tfrac{1}{2}(\sigma_x^2 + \sigma_y^2)$ for both, which is the rotational
> average of the covariance and the only orientation-free choice available.
> With $n = 1$ every dispersion is NaN rather than 0, because one sample
> carries no information about spread. Cite Press and co-workers,
> *Numerical Recipes* [[43]](../references.md#ref-43), for the delta method.

---

## 2. Linear least squares in closed form

### 2.1 Why a sinusoidal model is a linear model

The analyser response is physically motivated as

$$
Z(\psi) = P + \hat{e}\,A\,\sin(2\psi + \varphi),
\qquad (6)
$$

which is **nonlinear** in the amplitude $A$ and the null-placement error
$\varphi$. Expanding the sine turns it into

$$
Z(\psi) = P + \underbrace{(A\cos\varphi)}_{E_s}\sin 2\psi
        + \underbrace{(A\sin\varphi)}_{E_c}\cos 2\psi ,
\qquad (7)
$$

which is **linear** in $(P, E_s, E_c)$. The nonlinearity lives entirely in the
*known* regressor $\psi$, not in the unknowns. That single observation removes
the need for a starting guess, an iteration count, a convergence test and any
possibility of a local minimum. The same trick converts
$V_\mathrm{dc}(\psi) = V_\mathrm{min} + A\sin^2(\psi - \psi_0)$ into
$C_0 + C_c\cos 2\psi + C_s\sin 2\psi$.

### 2.2 The design matrix and the normal equations

For $N$ analyser probes at offsets $\psi_k$,

$$
\mathbf{M} = \begin{pmatrix}
1 & \sin 2\psi_1 & \cos 2\psi_1 \\
\vdots & \vdots & \vdots \\
1 & \sin 2\psi_N & \cos 2\psi_N
\end{pmatrix},
\qquad
\mathbf{M}^{\mathsf{T}}\mathbf{M}\,\mathbf{c} = \mathbf{M}^{\mathsf{T}}\mathbf{z}.
\qquad (8)
$$

The code does **not** form $\mathbf{M}^{\mathsf{T}}\mathbf{M}$. It calls
`numpy.linalg.lstsq`, which solves equation (8) through the singular value
decomposition, at the cost of squaring the condition number only once rather
than twice. For the underlying theory see Lawson and Hanson
[[44]](../references.md#ref-44) and Golub and Van Loan
[[45]](../references.md#ref-45). The right-hand side $\mathbf{z}$ is complex while $\mathbf{M}$ is
real, so the solve is exactly equivalent to two real solves sharing one
pseudo-inverse; writing it complex keeps the lock-in phase attached to the
coefficients instead of being discarded and re-derived.

Rank is checked before the solve: `np.linalg.matrix_rank(design) < 3` raises
`Analyser probe angles are degenerate for the fit` rather than returning a
meaningless minimum-norm answer.

### 2.3 The coefficient of determination, and what it is used for

$$
R^2 = 1 - \frac{\sum_k \lvert z_k - \hat{z}_k\rvert^2}{\sum_k \lvert z_k - \bar{z}\rvert^2}.
\qquad (9)
$$

| Fit | Gate on $R^2$ | Constant |
| --- | --- | --- |
| rotation versus drive voltage | $\ge 0.98$ with three or more levels | hard-coded in `effective_pockels_coefficient()` |
| signed angular response versus $\theta_i$ | $\ge 0.80$ | `DEFAULT_ANGULAR_MIN_R_SQUARED` |
| complex analyser response | reported, not gated | the gates are the two residual fractions of §3 |
| exact triplet | `nan` | three equations, three unknowns, so there is nothing to score |

### 2.4 The condition-number argument for the balanced four-point design

The calibration pixel probes the four offsets
$\psi \in \{0^\circ, +45^\circ, -45^\circ, +90^\circ\}$.
Substituting into equation (8) gives

$$
\mathbf{M}^{\mathsf{T}}\mathbf{M} = \mathrm{diag}(4,\,2,\,2),
\qquad
\kappa(\mathbf{M}) = \sqrt{2} = 1.414 .
\qquad (10)
$$

The three columns are **exactly orthogonal**, which is the best a design can
be: the three coefficients are estimated independently, and the noise on one
does not leak into another. Compare a clustered design over the same number of
points:

| Probe set | $\kappa(\mathbf{M})$ | Error amplification |
| --- | --- | --- |
| $\{0, +45, -45, +90\}$ | **1.41** | none worth naming |
| $\{0, 10, 20, 30\}$ | 31.3 | 22 times worse |
| $\{0, 5, 10, 15\}$ | 129.7 | 92 times worse |

The clustered designs are near-collinear in the constant and $\cos 2\psi$
columns, so the fit cannot tell a DC offset from a slow cosine. The balanced
set is not a convenience; it is the condition-number optimum, and it is also
the set that puts two probes exactly on the maximum-slope points.

For a production pixel the triplet $\{0, +45, -45\}$ leaves
$\kappa(\mathbf{M}) = 2.414$ with three equations and three unknowns, so
`joint_senarmont_triplet()` inverts it in closed form:

$$
C_0 = \tfrac{1}{2}(V_+ + V_-), \quad
C_s = \tfrac{1}{2}(V_+ - V_-), \quad
C_c = V_0 - C_0 ,
\qquad (11)
$$

and identically for the complex phasors. This is a **verification
calculation**, not a fit, which is why it reports no $R^2$ and why its quality
indicators are the residual fractions of §3 and the DC balance error
$\lvert V_+ - V_-\rvert / \text{half-swing}$.

---

## 3. The derivative-aligned projection

### 3.1 The projection

For an ideal scalar Sénarmont rotation, the AC response is nothing but the DC
fringe sliding sideways:

$$
Z - P = \delta\psi \cdot \frac{dD}{d\psi},
\qquad
\frac{dD}{d\psi} = -2C_c\sin 2\psi + 2C_s\cos 2\psi .
\qquad (12)
$$

Collect the coefficients of $\{\cos 2\psi, \sin 2\psi\}$ into a real
two-vector $\mathbf{d} = (2C_s,\ -2C_c)$ and the fitted AC coefficients into a
**complex** two-vector $\mathbf{e} = (E_c,\ E_s)$. Equation (12) says
$\mathbf{e} = \delta\psi\,\mathbf{d}$, so the least-squares solution is the
orthogonal projection

$$
\boxed{\;
\delta\psi = \frac{\mathbf{d}\cdot\mathbf{e}}{\mathbf{d}\cdot\mathbf{d}}
= \frac{2C_sE_c - 2C_cE_s}{4\left(C_c^2 + C_s^2\right)} ,
\qquad \Gamma = 2\,\delta\psi .
\;}
\qquad (13)
$$

Because $\mathbf{d}$ is **real** and $\mathbf{e}$ is complex, the projection
coefficient is complex, and it carries the electro-optic phase through
untouched. The optical throughput cancels exactly: the same $C_c, C_s$ scale
the numerator and the denominator.

### 3.2 Two residual diagnostics, and what each one tests

**Derivative-orthogonal residual.** Whatever part of $\mathbf{e}$ is
perpendicular to $\mathbf{d}$ cannot be a scalar rotation:

$$
\rho_\perp
= \frac{\lVert \mathbf{e} - \delta\psi\,\mathbf{d}\rVert}{\lVert\mathbf{e}\rVert}
= \sin\angle(\mathbf{e},\mathbf{d}).
\qquad (14)
$$

This is reported as `derivative_residual_fraction`. It is the sine of the angle between the measured response and the measured
derivative direction, so it is zero for a perfect scalar rotation and 1 for a
response entirely orthogonal to the slope.

**Temporal-rank residual.** Build the real $2\times2$ matrix

$$
T = \begin{pmatrix}\mathrm{Re}\,E_c & \mathrm{Re}\,E_s \\
                   \mathrm{Im}\,E_c & \mathrm{Im}\,E_s\end{pmatrix},
\qquad
\rho_T = \frac{\varsigma_2}{\varsigma_1},
\qquad (15)
$$

with $\varsigma_1 \ge \varsigma_2$ the singular values
[[45]](../references.md#ref-45), reported as `temporal_rank_fraction`. If $E_c$ and $E_s$
share a single temporal phase, $E_c = \hat{e}\,a$ and $E_s = \hat{e}\,b$ with
$a, b$ real, then

$$
T = \begin{pmatrix}\mathrm{Re}\,\hat{e}\\ \mathrm{Im}\,\hat{e}\end{pmatrix}
    \begin{pmatrix}a & b\end{pmatrix},
\qquad (16)
$$

an outer product, hence exactly rank 1 and $\varsigma_2 = 0$. **A non-zero
value is direct evidence that two mechanisms with different temporal phases are
present**, which is precisely the condition under which the single-axis
factorisation of equation (6) is not legitimate. The code therefore performs
the unrestricted three-coefficient fit **first** and accepts the factorisation
only if this test permits it.

### 3.3 Recovering the electro-optic axis

`fit_sinusoid_response()` needs the common phase $\hat{e}$ without knowing
$\varphi$. Since $E_s^2 + E_c^2 = \hat{e}^2 A^2$ regardless of $\varphi$,

$$
\hat{e} = e^{i\phi_e},
\qquad
\phi_e = \tfrac{1}{2}\,\mathrm{atan2}\!\bigl(\mathrm{Im}(E_s^2+E_c^2),\ \mathrm{Re}(E_s^2+E_c^2)\bigr),
\qquad (17)
$$

and projecting with $a = \mathrm{Re}(E_s\hat{e}^{*})$,
$b = \mathrm{Re}(E_c\hat{e}^{*})$ gives $A = \sqrt{a^2+b^2}$ and
$\varphi = \mathrm{atan2}(b,a)$. The **quadrature fraction** is the part that
survives the projection off the axis,
$\sqrt{a_\perp^2 + b_\perp^2}/A$ with
$a_\perp = \mathrm{Im}(E_s\hat{e}^{*})$.

---

## 4. Two-dimensional null searching

### 4.1 The problem

Minimise the detected power $P(q, a)$ over the quarter-wave plate angle $q$ and
the analyser angle $a$. From equation (7) of
[Theory of the Instrument](../physics/07-instrument-theory.md#14-the-master-intensity-equation),
near the null $P$ is quadratic in the angular offsets, so the landscape is a
**tilted two-dimensional paraboloid**. But:

- there is no analytical gradient, only measurements;
- each evaluation costs two motor moves and an averaged scope read, of order
  0.2 to 1 s;
- each evaluation is noisy, and near a deep null the fractional noise is large
  because the signal is small.

### 4.2 Why a derivative-free direct search

A finite-difference gradient would cost two extra evaluations per iteration and
would amplify read noise by $1/h$ for a step $h$. A **direct search** uses only
*comparisons* of function values, so a common multiplicative gain error or a
slowly drifting baseline cannot mislead it. Hooke and Jeeves
[[40]](../references.md#ref-40) is the canonical pattern search;
Torczon [[51]](../references.md#ref-51) proved global convergence to a
stationary point for this family under a rational-lattice step rule. The main alternatives were rejected
for concrete reasons:

| Alternative | Why not |
| --- | --- |
| Finite-difference gradient descent | noise amplification $1/h$; two extra evaluations per step |
| Nelder and Mead simplex [[50]](../references.md#ref-50) | no convergence guarantee, and the simplex can collapse onto a line on a noisy surface |
| Brent's method [[41]](../references.md#ref-41) | one-dimensional; needs a bracket, which in two dimensions is the hard part |
| Exhaustive grid | the rescue path uses exactly this, at 81 evaluations, but it is far too slow per pixel |

### 4.3 `null_descent`: Hooke and Jeeves with certification

[`pockels_full_automation.py`](../../pockels/pockels_full_automation.py).

**Exploratory move, per axis.** Try $+\mathrm{step}$. If $P$ drops, accept and
**grow** the step by 1.6. Otherwise try $-\mathrm{step}$; if that drops, accept
and grow. If neither helps, **shrink** the step by 0.5 and continue.

**Pattern (diagonal) move.** If both axes moved with a definite sign in the
same cycle, take an extra step of
$\tfrac{1}{2}(\mathrm{step}_q + \mathrm{step}_a)/1.6$ along that diagonal.
This is the "pattern" half of Hooke and Jeeves, and the reason it matters here is quantitative: plain coordinate
descent on a quadratic with Hessian condition number $\kappa$ reduces the error
by only $\bigl((\kappa-1)/(\kappa+1)\bigr)^2$ per sweep, which is close to 1
for an elongated valley. The valley of the Sénarmont null **is** elongated,
because the QWP and analyser angles are strongly correlated near extinction.
The pattern step follows the valley instead of zigzagging across it.

**Certification.** Convergence is not declared when the steps get small. Once
both steps are below `tol_step_deg`, the code probes $\pm\mathrm{tol}$ on each
axis, four extra evaluations. If none of the four improves, the point is a
certified local minimum *within tolerance*. If one does, the descent resumes
from it with both steps reset to $4\times\mathrm{tol}$.

| Constant | Value | Role |
| --- | --- | --- |
| `init_step_deg` | 5.0° | first exploratory step |
| `tol_step_deg` | 0.05° | certification radius |
| growth factor | 1.6 | step multiplier on success |
| shrink factor | 0.5 | step multiplier on failure |
| `max_evals` | 200 | hard bound on evaluations |
| `target_mV` | 14.5 mV in production | early acceptance, **re-confirmed by a second read** |

The early-acceptance re-read exists so a single low noise sample cannot end the
search: `accept_threshold_if_confirmed()` measures again at the candidate and
only stops if the confirmation is also at or below the threshold.

**Cost.** The step schedule is geometric, so reducing 5° to 0.05° needs about
$\log_2(100) = 6.6$ shrink events, seven in practice, each costing two probes,
plus the successful moves and four certification probes. In practice the descent
converges in a few tens of evaluations, capped at 200.

### 4.4 The rescue grid

A pattern search converges to a **local** minimum, and the Sénarmont null
surface is periodic in both angles with several minima. When a descent result
looks suspect, `substrate_rescue_seed()` sweeps a $9\times9$ grid of offsets
$\{-45, -30, -18, -9, 0, 9, 18, 30, 45\}$ degrees on each axis, 81
evaluations. The offsets are deliberately **non-uniform**, denser near the
centre, because the prior says the null is probably near the seed and the outer
points exist only to catch a gross error.

### 4.5 `null_2d_parabolic`: a quadratic response surface solved exactly

[`pockels_campaign.py`](../../pockels/pockels_campaign.py). Near the null,
model

$$
P(\Delta q, \Delta a) = F + D\,\Delta q + E\,\Delta a
+ A\,\Delta q^2 + B\,\Delta a^2 + 2C\,\Delta q\,\Delta a .
\qquad (18)
$$

Six coefficients, so six probes: the centre, $\pm s$ on each axis, and one
diagonal at $(+s, +s)$ to pin the cross term. The diagonal probe is what makes
the design determined; without it $C$ is unidentifiable.

The stationary point solves $\nabla P = 0$:

$$
\begin{pmatrix}2A & 2C\\ 2C & 2B\end{pmatrix}
\begin{pmatrix}\Delta q^{*}\\ \Delta a^{*}\end{pmatrix}
= \begin{pmatrix}-D\\ -E\end{pmatrix},
\qquad
\text{minimum iff } A > 0 \text{ and } AB - C^2 > 0 .
\qquad (19)
$$

**For a genuinely quadratic surface one Newton step is exact**, which is why
this replaces roughly 80 scope reads with about 7. The guards matter as much
as the solve:

| Guard | Constant | What it prevents |
| --- | --- | --- |
| positive-definite Hessian test | $A>0$, $AB-C^2>0$ | stepping to a saddle or a maximum |
| trust region | $\lvert\Delta\rvert \le 3s$ | extrapolating outside the region the six probes actually sampled |
| polish trigger | `NULL_POLISH_TRIGGER_RATIO = 1.3` | accepting a verify read 30 % worse than the best probe |
| polish step | $s/$`NULL_POLISH_STEP_DIV` $= s/3$ | four cardinal probes to recover from a missed fit |
| final landing | backlash-compensated move plus averaged read | the returned angles seed the next pixel's warm start |

| Constant | Value |
| --- | --- |
| `NULL_2D_STEP_COARSE_DEG` | 3.0° |
| `NULL_2D_STEP_FINE_DEG` | 1.0° |
| `NULL_PROBE_N` | 2 reads per probe |
| `NULL_SCOPE_AVG` | 2 reads on the verify |
| `FAST_NULL_ESCALATE_RATIO` | 50 |
| `WARM_SKIP_TOL_DEG` | 0.10° |

`first_null_fast()` runs coarse then fine, about 14 reads. `fast_renull()`
warm-starts (skipping the move entirely if already within 0.10°), reads the
power, and chooses: if $P/P_\mathrm{ref} \le 50$ a single fine pass is enough,
otherwise it escalates to coarse then fine. Auto-scaling is applied on the
first probe only and then frozen, because a range change mid-fit rescales the
six probe values and destroys the quadratic.

`null_fast_rsm()` in `pockels_full_automation.py` is the same idea in
least-squares form, with a steepest-descent fallback when the Hessian is not
positive definite, `span` shrinking between iterations, `max_iter = 4` and
`target_p_W = 5e-6`.

---

## 5. One-dimensional peak finding

The alignment problem is the opposite sign: maximise transmitted power over one
stage axis, with the analyser rotated off the null so the detector sees a bright
peak. The operational sequence and the fallback chain are in
[Motion Control §5](motion-control.md#5-stage-alignment-algorithms); what
follows is why each method converges the way it does.

### 5.1 Golden-section search

`golden_section_search()` in
[`stage_calibration.py`](../../pockels/stage_calibration.py). Assume only that
the profile is **unimodal** on $[a,b]$. Place two interior probes at

$$
x_1 = a + \rho(b-a), \qquad x_2 = b - \rho(b-a),
\qquad
\rho = 2 - \varphi = \frac{3-\sqrt5}{2} = 0.381966 .
\qquad (20)
$$

Discard the outer sub-interval on the losing side. The magic of $\rho$ is that
the surviving interior point lands exactly where it is needed for the next
iteration, so **each iteration after the first costs one new evaluation** and
reduces the interval by

$$
r = 1 - \rho = \varphi - 1 = \frac{1}{\varphi} = 0.618034 ,
\qquad (21)
$$

where $\varphi$ is the golden ratio, which on this page is not the
null-placement phase of §2.1.

Kiefer [[39]](../references.md#ref-39) proved this is minimax-optimal among
sequential methods that assume no more than unimodality. The evaluation count
to reach tolerance $\epsilon$ from an initial width $W$ is

$$
n_\mathrm{evals} = 2 + \left\lceil \frac{\ln(W/\epsilon)}{\ln(1/r)} \right\rceil
= 2 + \left\lceil \frac{\ln(W/\epsilon)}{0.4812} \right\rceil .
\qquad (22)
$$

| $W$ | $\epsilon$ | Evaluations |
| --- | --- | --- |
| 400 µm (`ALIGN_COARSE_RANGE_UM`) | 1 µm | 15 |
| 400 µm | 5 µm (`ALIGN_FAST_TOL_UM`) | 12 |
| 20 µm | 1 µm | 9 |

The search runs with the careful settings, `ALIGN_SETTLE_S = 0.5` s and
`ALIGN_AVG_READS = 5`, and it backs off by `BACKLASH_MM = 0.02` mm before the
first probe so that every subsequent approach is from the same direction.

### 5.2 Parabolic vertex refinement

Golden section converges **linearly** at $r = 0.618$. Inverse parabolic
interpolation through three points converges **superlinearly**, with order
equal to the real root of $x^3 = x^2 + 1$, that is $1.3247$; this is the
classical result behind Brent's method
[[41]](../references.md#ref-41) and *Numerical Recipes*
[[43]](../references.md#ref-43). So the right strategy is to use golden section
only where the linear rate is cheap, then switch. `fast_peak_search()` runs
golden section to a loose 5 µm and takes one parabolic step, reaching below
1 µm in about 10 evaluations instead of about 16.

`_parabolic_vertex(x0,x1,x2,y0,y1,y2)` uses the Lagrange form directly:

$$
A = \frac{x_2(y_1-y_0) + x_1(y_0-y_2) + x_0(y_2-y_1)}{(x_0-x_1)(x_0-x_2)(x_1-x_2)},
\qquad
x_\mathrm{vertex} = -\frac{B}{2A},
\qquad (23)
$$

with $B$ the matching first-order coefficient. It returns `None`, not a
number, in three cases: the denominator is below $10^{-18}$ (collinear or
duplicated abscissae), $\lvert A\rvert < 10^{-18}$ or $A \ge 0$ (a flat line, or
a convex parabola whose stationary point is a **minimum** and not the peak
being sought), or the vertex lies outside the bracket. The last is the
important one: a vertex outside the bracket is an **extrapolation**, and an
extrapolation from three noisy points is not evidence of anything.

> **The 0.98 revert rule.** Even an in-bracket vertex is a prediction. The code
> always physically **measures** at the vertex, and if that reading is below
> $0.98 \times$ the best genuine probe it moves back to the probe. The
> invariant is that **a search can never end at a position it has not measured,
> and never at a position worse than one it has already visited.**

### 5.3 Adaptive hill-climb with geometric step growth

`hill_climb_peak()` is bracket **expansion** followed by parabolic finish, the
same two-phase structure as `mnbrak` in *Numerical Recipes*
[[43]](../references.md#ref-43), whose expansion factor is the golden ratio
1.618. The code uses 1.6.

1. Probe at the start and at $\pm 5$ µm (`HILL_CLIMB_INITIAL_STEP_UM`), three
   evaluations.
2. If the centre is at least as high as both neighbours, **or** all three lie
   within `HILL_CLIMB_FLAT_V_TOL_PCT = 0.5 %` of each other, fit the parabola
   and finish. Four evaluations total.
3. Otherwise climb toward the higher side, multiplying the step by
   `HILL_CLIMB_STEP_GROWTH = 1.6` each move, capped at
   `HILL_CLIMB_STEP_CAP_UM = 50` µm.
4. The first reading that falls below its predecessor brackets the peak; fit
   the parabola on the last three points and finish.

Geometric growth is what makes this cheap. Starting at 5 µm with factor 1.6 and
a 50 µm cap, the cumulative travel is 8, 20.8, 41.3, 74.0, 124.0, 174.0,
224.1 µm, so **seven steps are enough to cross the entire
`HILL_CLIMB_MAX_RANGE_UM = 200` µm search range**. A fixed 5 µm step would need
40. A hard cap of 25 evaluations guarantees termination regardless.

### 5.4 The ring-lock escape from a flat region

The failure mode a one-dimensional search cannot see: the beam sits on a
plateau, both $\pm 5$ µm neighbours read the same as the centre within the read
noise, and the search reports "already at the peak". The plateau is not the
peak; it is the off-gap background.

`_ring_probe()` samples `HILL_CLIMB_LOCK_N_POINTS = 8` points evenly spaced on
a circle of radius $r$, trying `HILL_CLIMB_LOCK_RADII_UM = [20, 80]` µm in
order, and moves to the best if it reads at least
`HILL_CLIMB_LOCK_THRESHOLD = 1.15` times the centre.

The mathematics is a finite-radius directional derivative. For a locally linear
profile $I(\mathbf{r}) \approx I_c + \nabla I\cdot\mathbf{r}$, the maximum over
the ring is $I_c + \lvert\nabla I\rvert r$, so the test fires when

$$
\lvert\nabla I\rvert \;\ge\; \frac{0.15\,I_c}{r}
\quad\Longrightarrow\quad
\begin{cases}
0.75\ \% \text{ per µm at } r = 20\ \text{µm},\\
0.19\ \% \text{ per µm at } r = 80\ \text{µm}.
\end{cases}
\qquad (24)
$$

**Increasing the radius by four makes the same threshold four times more
sensitive to a shallow gradient**, which is exactly why the 20 µm ring is tried
first and the 80 µm ring second. A ring is also genuinely two-dimensional: it
detects a gradient **perpendicular** to the axis currently being scanned, which
alternating one-dimensional searches cannot see until they switch axes.

---

## 6. Robust statistics

### 6.1 Median absolute deviation

The estimator is

$$
\mathrm{MAD} = \mathrm{median}_i\bigl(\lvert x_i - \mathrm{median}(x)\rvert\bigr).
\qquad (25)
$$

Under normality $\mathrm{MAD} \to \sigma\,\Phi^{-1}(0.75) = 0.6745\,\sigma$, so
the **consistency factor** that makes it an unbiased estimator of $\sigma$ is

$$
\hat{\sigma} = \frac{\mathrm{MAD}}{0.6745} = 1.4826 \times \mathrm{MAD}.
\qquad (26)
$$

Cite Rousseeuw and Croux [[46]](../references.md#ref-46) for the MAD and its
alternatives. The reason to use it at all is the **breakdown point**: the
median and the MAD tolerate up to 50 % contamination, while the mean and the
standard deviation have a breakdown point of 0, meaning a single bad sample can
move them arbitrarily far.

`ScopeDetector.read_averaged()` in `stage_calibration.py` keeps samples with
$\lvert x - \mathrm{median}\rvert \le 2.5\,\mathrm{MAD}$, and
`MAD_K = 2.5` in `POL_Chip_Test_Working_2026.py` does the same. In $\sigma$
units the clip is at

$$
2.5 \times 0.6745 = 1.686\,\sigma
\quad\Longrightarrow\quad
2\Phi(-1.686) = 9.2\ \%\ \text{of clean samples rejected}.
\qquad (27)
$$

That is aggressive, and deliberately so. The contamination being defended
against is a scope read that caught a trigger glitch or a mid-sequence range
change, which sits many $\sigma$ out; discarding 9 % of good samples to remove
it is an excellent trade when $n$ is between 3 and 5.

> **The filter can never reduce the sample below three.** The code writes
> `cleaned = arr[keep] if np.sum(keep) >= 3 else arr`. If the rule would leave
> fewer than three samples, the **raw** array is used instead. A genuinely
> broad distribution must not be mistaken for a set of outliers.

### 6.2 The relative-standard-deviation stopping rule

`DetectorTekTBS.read_power_w_stable()` in `POL_Chip_Test_Working_2026.py`
collects samples until

$$
\mathrm{RSD} = \frac{\hat{\sigma}}{\lvert\hat{\mu}\rvert} \le \mathrm{tol}
\qquad\text{or}\qquad
t \ge t_\mathrm{max}.
\qquad (28)
$$

| Constant | Careful path | Scan path |
| --- | --- | --- |
| minimum samples | `STABLE_MIN_SAMPLES = 18` | `SCAN_STABLE_MIN_SAMPLES = 40` |
| RSD tolerance | `STABLE_RSD_TOL = 0.0030` | `SCAN_STABLE_RSD_TOL = 0.05` |
| maximum duration | `STABLE_MAX_DURATION_S = 2.2` s | `SCAN_STABLE_MAX_DURATION_S = 3.0` s |
| sample spacing | `SAMPLE_SLEEP_S = 0.020` s | 0.020 s |

Two honest observations about this rule.

1. **It bounds the spread of the population, not the precision of the mean.**
   The confidence-based criterion would be
   $\hat{\sigma}/(\lvert\hat{\mu}\rvert\sqrt{n}) \le \mathrm{tol}$, which at
   $n = 18$ is $\sqrt{18} = 4.24$ times looser. The rule as written is the
   **more conservative** of the two, so it never stops too early; it can stop
   later than necessary.
2. **The scan tolerance is loose on purpose.** At 5 % the condition is
   essentially always satisfied, so the loop runs to its 40-sample minimum
   every time. The scan wants a fixed, large sample count for comparability
   across pixels, not an adaptive one.

A separate suspicion test re-takes the measurement when
`SUSPICIOUS_RSD_THRESHOLD = 0.15` or `SUSPICIOUS_RANGE_RATIO = 5.0` is
exceeded, up to `MAX_RETRY_ATTEMPTS = 2`, and
`DISCARD_FIRST_N_SAMPLES = 5` throws away the settling transient.

---

## 7. Nonlinear curve fitting

Both fits below use `scipy.optimize.curve_fit`
[[48]](../references.md#ref-48), that is Levenberg and Marquardt damped least
squares [[42]](../references.md#ref-42) when unbounded and a trust-region
reflective method when bounded. Unlike §2 these models genuinely **are** nonlinear in
their parameters, so an initial guess is required and a local minimum is
possible.

### 7.1 The tanh branch model

`_tanh_branch_fit()` in
[`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py):

$$
S(V) = a + bV + s_s\,\tanh\!\left(\frac{V - V_c}{w}\right).
\qquad (29)
$$

| Parameter | Meaning | Initial guess in the code |
| --- | --- | --- |
| $a$ | offset of the branch | 0.0 |
| $b$ | field-induced linear background, the quadratic electro-optic term under DC bias | 0.0 |
| $s_s$ | saturation amplitude | $(\max S - \min S)/2$ |
| $V_c$ | coercive voltage | `_best_zero_crossing(Vb, Sb)`, else 0.0 |
| $w$ | transition width | 2.0 V |

Requires at least six finite points; `maxfev = 20000`; parameter uncertainties
from $\sqrt{\mathrm{diag}(\mathrm{pcov})}$, of which `v_c_err_V` is reported.

**Why a tanh and not something else.** A ferroelectric branch is the *integral*
of the switching-field distribution, and $\tanh$ is the cumulative distribution
function of a logistic density, $\mathrm{sech}^2$. So equation (29) is exactly
the statement "a logistic distribution of coercive fields, on a linear
background", which is the Preisach hysteron picture
[[23]](../references.md#ref-23) with one particular kernel. A Gaussian distribution of coercive fields would give an error
function instead; over a measured branch the two are almost indistinguishable,
and $\tanh$ is cheaper to evaluate and better conditioned. The parameter $w$ is
therefore a **disorder width**, not a fitting nuisance.

> **The degenerate branch is handled, not hidden.** A near-linear branch can
> have a perfectly good best fit whose parameter covariance is not
> identifiable, because $s_s$ and $w$ trade off. The code catches the resulting
> `OptimizeWarning`, reports the uncertainties as NaN, and returns the fit, so
> a traceback-looking warning never lands in an acquisition log.

### 7.2 The stretched-exponential poling model

`fit_poling_kinetics()`:

$$
M(t) = M_\infty - (M_\infty - M_0)\,
\exp\!\left[-\left(\frac{t}{\tau}\right)^{\beta}\right].
\qquad (30)
$$

Initial guess $[\,M(t_N),\ M(t_1),\ \max(t_N/3,\ 1.0),\ 1.0\,]$; bounds
$\tau \in [10^{-3}, 10^{5}]$ s and $\beta \in [0.2, 2.0]$ with $M_\infty, M_0$
free; at least five points; `maxfev = 20000`.

**Why a stretched exponential is the signature of a distribution of relaxation
times.** A single relaxation process gives $e^{-t/\tau}$. A *distribution*
$\rho(\tau')$ of independent processes gives

$$
\int_0^\infty \rho(\tau')\,e^{-t/\tau'}\,d\tau' ,
\qquad (31)
$$

and the form $\exp[-(t/\tau)^\beta]$ with $0 < \beta < 1$ is exactly the
Laplace transform of a one-sided stable distribution of rates. So $\beta$ is
not a shape nuisance: **$\beta = 1$ says one relaxation time, and $\beta < 1$
says a spread of them**, with smaller $\beta$ meaning a broader spread. This is
the Kohlrausch form [[52]](../references.md#ref-52), brought into dielectric
relaxation by Williams and Watts [[22]](../references.md#ref-22). In a
ferroelectric film the spread comes from a distribution of local pinning
energies for domain walls; see Tagantsev, Cross and Fousek
[[26]](../references.md#ref-26) and Damjanovic
[[25]](../references.md#ref-25). It is a different statement from the Ishibashi
and Takagi [[20]](../references.md#ref-20) Avrami kinetics used to discuss
switching, and the two should not be conflated.

Two practical points.

- **The upper bound on $\beta$ is 2.0, not 1.0.** Letting the fit report
  $\beta > 1$, a sharper-than-exponential onset, is diagnostic information; a
  clamp at 1.0 would silently hide it.
- **$\tau$ is not the mean relaxation time.** For equation (30) the correct
  mean is $\langle\tau\rangle = (\tau/\beta)\,\Gamma(1/\beta)$. Comparing raw
  $\tau$ across pixels with different $\beta$ compares different things.

---

## 8. Numerical differentiation and interpolation in the loop analysis

### 8.1 Zero crossing by linear interpolation

`_best_zero_crossing()` finds every index with $S_iS_{i+1} < 0$ and
interpolates:

$$
V_c = V_i - S_i\,\frac{V_{i+1}-V_i}{S_{i+1}-S_i}.
\qquad (32)
$$

A real branch can have several sign changes, from noise near zero or from a
genuinely pinched loop. The code scores each candidate by the local slope
$\lvert\Delta S/\Delta V\rvert$ and takes the **steepest**. That choice is an
error-propagation argument, not an aesthetic one: an additive offset error
$\Delta S$ displaces the crossing by

$$
\frac{\partial V_c}{\partial S_\mathrm{offset}} = \frac{1}{\lvert dS/dV\rvert},
\qquad (33)
$$

so the steepest crossing is the one least sensitive to a baseline error. Exact
zeros are also accepted, scored $\infty$ when interior and 0 at an endpoint so
that an endpoint zero never wins.

`_interp_at()` evaluates $S$ at a given voltage with `np.interp` after an
explicit sort, and returns `None` outside the branch range. **It never
extrapolates**, which is why a remanence value is simply absent rather than
invented when a branch does not reach zero volts.

### 8.2 The baseline-corrected branch derivative and its moments

`_switching_distribution()` builds the switching-field distribution:

1. Sort by $V$.
2. **Collapse duplicate voltages.** The 45-point centre-dense hysteresis grid
   has overlapping coarse and fine sections, so `np.unique(np.round(V, 6))`
   groups them and the $S$ values within each group are averaged. Feeding
   duplicated abscissae to a derivative would produce infinities.
3. Differentiate with `np.gradient(S, V)`, which is second-order accurate by
   central differences in the interior and second-order one-sided at the ends,
   **on a non-uniform grid**. That last property is essential: the grid is
   deliberately non-uniform.
4. Subtract the baseline slope from `_tail_linear_slope()`, a degree-1
   `np.polyfit` of $S$ against $V$ in each saturation tail, averaged over the
   two tails. This removes the quadratic electro-optic background derived in
   [Theory of the Instrument §2](../physics/07-instrument-theory.md#2-harmonic-structure-and-where-the-second-harmonic-comes-from),
   which under a DC bias is linear in that bias.

The moments are then taken with weights $w_k = \lvert d_k\rvert$ where
$\lvert d_k\rvert \ge 0.1\max\lvert d\rvert$, and zero otherwise:

$$
\langle V\rangle = \frac{\sum_k w_kV_k}{\sum_k w_k},
\quad
\sigma^2 = \frac{\sum_k w_k(V_k - \langle V\rangle)^2}{\sum_k w_k},
\quad
\mathrm{skew} = \frac{\sum_k w_k(V_k-\langle V\rangle)^3}{\sigma^3\sum_k w_k}.
\qquad (34)
$$

**The 10 % weight floor is what makes the third moment usable.** Skewness
weights by $(V - \langle V\rangle)^3$, so without the floor the saturation-tail
noise, which sits furthest from the mean, would dominate the answer entirely.
The peak position, height and FWHM are found by linear interpolation of
$\lvert d\rvert$ at half maximum on each side of the peak index.

### 8.3 Loop area and loop opening

The loop area is a composite trapezoidal integral,
`_trapezoid = np.trapezoid or np.trapz`, a shim for the numpy 2.x rename. The
composite trapezoid error per interval is $O(h^3 f'')$, so it concentrates
where the curvature is largest. On the centre-dense hysteresis grid that is
exactly where $h$ is smallest, so the non-uniform grid is performing error
control as well as resolving the coercive region.

`_loop_opening()` computes $\Delta S(V) = S_\mathrm{down} - S_\mathrm{up}$ by
interpolating both branches onto a common 121-point linear grid spanning their
overlap, then smooths with a 5-point boxcar
(`np.convolve(opening, np.ones(5)/5, mode="same")`). The boxcar is a zero-phase
moving average, so it suppresses single-point noise humps without shifting the
positions of real ones, which matters because `_opening_pinch_info()` counts
humps above $0.3\times$ the peak to decide whether a loop is pinched. The
`mode="same"` convolution biases the first and last two grid samples low; that
is acceptable because the pinch test only looks at interior humps.

---

## 9. The predictive lock-in range controller

[`pockels_lockin_ranging.py`](../../pockels/pockels_lockin_ranging.py). Used by
the DC hysteresis sweep only. **The fast map deliberately does not use it**: it
holds sensitivity index 16, 200 µV RMS full scale, fixed for the whole map,
because a range change between pixels would rescale the readings and destroy
cross-pixel comparability.

### 9.1 The problem, as a decision rule

Before each hysteresis point, with the AC probe still off, choose a DSP7230
sensitivity index such that the reading neither clips nor sits at 1 % of full
scale. The controller never sees the reading it is ranging for; it must
**predict** it.

**State:** the observation list (voltage, branch family, cycle, magnitude,
noise sigma), the current ladder index, a narrow-streak counter, and a
points-since-change counter.

**Prediction**, in order of preference:

1. Linear extrapolation from the last two observations in the same branch
   family, clamped to at most twice the larger of those two magnitudes, so one
   noisy slope cannot request an absurd range.
2. The last observation in the same branch family.
3. The last observation of any kind.
4. The initial anchor, which is the already-measured peak response.

**Loop memory.** If the exact same voltage has been visited before, on the
return branch or in a later cycle, the prediction is raised to the larger of
the two most recent occurrences. Taking the **larger** is the conservative
choice for an estimator that must not clip.

**Decision.** With $\hat{m}$ the prediction and $\sigma$ the recent noise,

$$
\text{upper} = \hat{m} + k_\sigma\,\sigma,
\qquad
\text{index} = \min\{\,i : \text{upper} \le f_\mathrm{target}\cdot \mathrm{FS}_i\,\},
\qquad (35)
$$

with the safety factor $k_\sigma = 4.0$ (`safety_sigma`), the fill fraction
$f_\mathrm{target} = 0.60$ (`target_fraction`), a noise floor of 0.10 µV, and a
ladder of indices 11 to 16 spanning 5 µV to 200 µV RMS full scale.

### 9.2 The asymmetry, and why it is correct

| Direction | Rule |
| --- | --- |
| **Widen** | immediate, by as many steps as needed, no confirmation |
| **Narrow** | `narrow_confirmations = 2` consecutive requests **and** `hold_points_after_change = 2` points since the last change, then exactly **one** step |

This is a hysteretic, Schmitt-trigger style rule, and the asymmetry is forced
by the **asymmetry of the loss function**:

- Being one range too **wide** costs resolution. The reading is still valid,
  still on scale, and can be re-ranged at the next point. The error is
  **bounded and recoverable**.
- Being one range too **narrow** clips. The point is lost, and it is lost in
  the worst possible way: a clipped magnitude looks like a plateau, and a
  plateau in a hysteresis loop looks exactly like **saturation**. The error is
  **unbounded and silently misleading**.

An estimator whose two error directions cost that differently must not be
symmetric. Requiring repeated evidence before narrowing also prevents chatter
between two adjacent ranges when the predicted magnitude sits near a threshold,
which would otherwise rescale alternate points of a loop.

### 9.3 The post-read rescue

`emergency_widen_index()` runs *after* the read and can only widen:
`desired_pos = max(current_pos, ...)`. It forces at least one extra step when
the instrument flagged an output overload, or when the measured magnitude
reached `rescue_fraction = 0.85` of full scale. Narrowing is never decided
here; it always goes back through the multi-point hysteresis of §9.2.

---

## 10. The lock-in as a synchronous detector

### 10.1 Mixing and low-pass filtering

With the RMS convention the DSP7230 uses, an input
$v(t) = \sqrt2\,V_\mathrm{rms}\cos(\omega t + \phi)$ multiplied by
$\sqrt2\cos\omega_\mathrm{ref}t$ and low-passed gives

$$
X = V_\mathrm{rms}\cos\phi, \qquad Y = V_\mathrm{rms}\sin\phi ,
\qquad (36)
$$

because $2\cos(\omega t + \phi)\cos\omega t = \cos\phi + \cos(2\omega t+\phi)$
and the low-pass removes the second term. Everything at a frequency
$\omega \ne \omega_\mathrm{ref}$ is translated to
$\lvert\omega - \omega_\mathrm{ref}\rvert$ and rejected.

**The lock-in is therefore a bandpass filter of width $2B_\mathrm{ENBW}$
centred exactly on $\omega_\mathrm{ref}$, synthesised at DC.** A physical
bandpass of 0.5 Hz width at 30 kHz would need $Q = 60\,000$, which is not
buildable; at DC the same filter is two resistors and two capacitors.

### 10.2 Equivalent noise bandwidth

For white input noise of one-sided PSD $S_v$ near $\omega_\mathrm{ref}$, the
mixer product has autocorrelation $R_m(\tau) = R_n(\tau)\cos\omega_\mathrm{ref}\tau$,
so both sidebands fold to baseband and the baseband one-sided PSD is again
$S_v$. After the low-pass,

$$
\sigma_X^2 = \sigma_Y^2 = S_v\,B_\mathrm{ENBW}.
\qquad (37)
$$

For $n$ cascaded identical single-pole sections of time constant $\tau$,
integrating $\lvert H(f)\rvert^2$ gives

$$
B_\mathrm{ENBW} = \frac{1}{4\tau},\quad \frac{1}{8\tau},\quad \frac{3}{32\tau},
\quad \frac{5}{64\tau}
\qquad\text{for } n = 1,2,3,4 .
\qquad (38)
$$

The instrument runs time-constant index 14 ($\tau = 500$ ms) at 12 dB per
octave, which is $n = 2$, so $B_\mathrm{ENBW} = 0.25$ Hz. See the DSP7230
manual [[36]](../references.md#ref-36) and, for the general treatment, Meade
[[32]](../references.md#ref-32) and Scofield
[[33]](../references.md#ref-33). The
resulting rotation noise floor is worked through in
[Theory of the Instrument §4](../physics/07-instrument-theory.md#4-noise-budget).

### 10.3 Where "two time constants times the filter order" comes from

The step response of $n$ cascaded identical poles, and its relation to the
time constant and the filter order, is set out by Scofield
[[33]](../references.md#ref-33):

$$
s(t) = 1 - e^{-x}\sum_{k=0}^{n-1}\frac{x^k}{k!},
\qquad x = \frac{t}{\tau}.
\qquad (39)
$$

The code's `minimum_lockin_settle_s()` waits $2\times\mathrm{TC}\times n$,
which for $\tau = 0.5$ s and $n = 2$ is 2 s, hence $x = 4$:

| Wait | $x$ | Residual from equation (39) |
| --- | --- | --- |
| $2\tau n$ = 2 s | 4 | $e^{-4}(1+4) = 9.2\ \%$ |
| $5\tau n$ = 5 s | 10 | $e^{-10}(1+10) = 0.05\ \%$ |

So the rule is a **deliberate 9 % compromise**, not an exact settling
criterion. It is defensible because the residual is a transient common to every
point and biased toward the previous reading, so the analyser probes are always
taken in a fixed order; and because a 5 s settle would add hours to a map. Where
accuracy matters more than throughput the code escalates:
`Pockels_Calibration_2026.py` raises the settle to
$\max(\text{configured},\ 5\,\tau)$
and uses `LOCKIN_SETTLE_S = 7.0` s, against 4.0 s in
`analyser_sweep_voltage_series.py`.

A corollary that is easy to get wrong: **samples spaced closer than the
settling time are not independent**, so averaging $n$ of them does not reduce
the noise by $\sqrt{n}$. That is why `read_lockin_averaged()` spaces reads by
`LOCKIN_READ_DELAY_S` rather than reading as fast as the interface allows.

---

## 11. Numerical hygiene

### 11.1 Decimals, not floats, into the .NET stage API

```python
def py_to_net_decimal(py_dec):
    return NetDecimal.Parse(str(py_dec))
```

`System.Decimal` is a base-10 fixed-point type; a Python `float` is binary64.
The value 12.3 is not exactly representable in binary64, so a round trip
through a `double` carries a representation error into the motion command. Going
through the **string** form of a Python `Decimal` keeps the value exact in base
10, which is why `STAGE_CENTER_MM = PyDecimal('12.5')` is a `Decimal` and not a
literal float.

The physical size of the error is irrelevant, far below a nanometre. The reason
to care is **accumulation**: read a position, convert to float, add an offset,
convert back, repeat for thousands of moves, and the comparison
`abs(pos - target) < tol` starts behaving unpredictably right at the tolerance
boundary, which is exactly where a verification check lives.

### 11.2 Angle wrapping

| Helper | Formula | Range | Used for |
| --- | --- | --- | --- |
| `wrap180` | $(x + 180)\ \mathrm{mod}\ 360 - 180$ | $(-180, 180]$ | rotator targets during null searches |
| `_wrap_half` | $(x + 90)\ \mathrm{mod}\ 180 - 90$ | $(-90, 90]$ | fitted analyser angles |
| `_periodic_delta_deg` | $(v - r + p/2)\ \mathrm{mod}\ p - p/2$ | $(-p/2, p/2]$ | differences of angles, $p = 180$ |

The period is **180 and not 360** for every analyser and QWP quantity, because
a linear polariser at $a$ and at $a + 180^\circ$ is the same optic. Getting this
wrong shows up as a spurious $180^\circ$ jump in a fitted null angle.

Two details that are easy to miss:

- `_wrap_half` maps exactly $-90$ to $+90$, so the interval really is half-open
  and a fitted angle can never be reported twice under two names.
- The one-line modulo form is correct for negative inputs **because Python's
  `%` returns a non-negative result for a positive modulus**. The same
  expression in C would need a sign fix-up.

**The 0 and 360 boundary** needs its own handling. The analyser parked at a
null near $360^\circ$ can report 359.99, 360.00 or 0.00, all the same physical
position. `run_single_sweep()` detects a readback above 359.5 or below 0.5 and
nudges to $5^\circ$ first, because otherwise the wrap-safe move computes the
long way round.

### 11.3 Filtered arrays for rows excluded by quality flags

Quality exclusion is done with boolean masks applied at the point of use, never
by deleting rows:

```python
good  = (data["tripped"] == 0) & (data["lockin_invalid"] == 0) & np.isfinite(V)
m_down = good & (base == "down") & (cyc == cycle_number)
```

Two consequences follow, and both are deliberate. The excluded rows stay in the
CSV and in the loaded arrays, so **the analysis can be re-run later under a
different flag policy without re-measuring anything**; and the number of
excluded points is itself recoverable as a diagnostic rather than being lost.

The same discipline appears in the pure analysis module, where every fit
applies its own finite filter before touching the data
(`ok = np.isfinite(psi) & np.isfinite(z.real) & np.isfinite(z.imag)`), so no
fit can be handed NaNs by a careless caller. `finite_float()` returns `None`
rather than NaN precisely so that the caller has to make an explicit decision.

> **Why boolean filtering rather than `numpy.ma`.** A masked array propagates
> its mask through arithmetic, which is convenient but makes it easy to lose
> track of *why* a value is masked. Filtering at the call site keeps the reason
> local, visible and greppable.

### 11.4 Weighted least squares that can only inflate an uncertainty

`_fit_component()` in `pockels_measurement_analysis.py` fits with weights
$1/\sigma_k^2$, takes the covariance as
$\mathrm{pinv}(\mathbf{M}^{\mathsf{T}}\mathbf{W}\mathbf{M})$, and then scales it
by

$$
\max\!\left(1,\ \chi^2_\nu\right),
\qquad
\chi^2_\nu = \frac{1}{\nu}\sum_k\left(\frac{r_k}{\sigma_k}\right)^{2}.
\qquad (40)
$$

This is the standard convention: **if the scatter is worse than the quoted
uncertainties imply, inflate the error bar; never deflate it**. The `max(1, ...)`
is what enforces the "never" [[43]](../references.md#ref-43). Using `pinv`
rather than `inv` means a rank-deficient normal matrix degrades to the
minimum-norm solution instead of raising, and the single-point case
($n = 1$) falls back to $\sigma_\mathrm{slope} = \lvert\sigma_1/x_1\rvert$
rather than pretending to a covariance it cannot have.

### 11.5 Sort before you interpolate

`np.interp` requires an increasing abscissa and gives silently wrong answers
otherwise. Every call site sorts explicitly (`order = np.argsort(Vb)`) rather
than assuming that acquisition order is voltage order, which on a
centre-dense hysteresis grid with interleaved coarse and fine sections it
certainly is not.

---

## Continue

- [Theory of the Instrument](../physics/07-instrument-theory.md): the physics
  these algorithms implement, the transfer function and the noise budget.
- [Motion Control](motion-control.md): the operational recipe for the searches
  of §4 and §5, with the fallback chain and the diagnostic logging.
- [Instrument Control and Timing](instrument-control.md): how one lock-in point
  is actually taken, and the wall-clock budget that the settling rule of §10.3
  is trading against.
- [The Data Pipeline](data-pipeline.md): what happens to these numbers once
  they reach disk.
- [`../references.md`](../references.md): the full bibliography.

---

<div align="center">

[← The data pipeline](data-pipeline.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Operating guide →](../guide/index.md)

</div>
