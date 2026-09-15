# Polarisation Formalism

**What this page is for:** to fix the language. Jones calculus, Stokes
parameters and the Poincaré sphere are three descriptions of the same thing,
and each one makes a different part of this instrument obvious. The page ends
by writing the complete Jones product of the actual beamline, which is the
object every later calculation perturbs.

If you already know Jones and Stokes, skip to
[§7 The Jones chain of this instrument](#7-the-jones-chain-of-this-instrument).

> **Symbol warning.** On this page $\psi$ is the **azimuth of the polarisation
> ellipse**, the standard usage. On
> [03 — The Null-Slope Sénarmont Readout](03-senarmont-readout.md) and
> throughout the code, $\psi$ is the **analyser offset from the null**. They
> are different angles. Where both appear here the analyser angle is written
> $\psi_A$.

---

## 1. Jones vectors

Take light travelling along $z$, monochromatic and fully polarised. Its
transverse electric field is a two-component complex vector, the **Jones
vector**:

$$
\mathbf{J} = \begin{pmatrix} E_x \\ E_y \end{pmatrix}
           = \begin{pmatrix} A_x \\ A_y\, e^{i\phi} \end{pmatrix},
\qquad \phi = \phi_y - \phi_x .
$$

Only the *relative* phase $\phi$ and the amplitude ratio fix the polarisation
state; a common prefactor is a global phase and an overall intensity.

| State | Jones vector | Condition |
| --- | --- | --- |
| Linear horizontal (H) | $(1,\,0)^\mathsf{T}$ | $A_y = 0$ |
| Linear vertical (V) | $(0,\,1)^\mathsf{T}$ | $A_x = 0$ |
| Linear at $\theta$ | $(\cos\theta,\,\sin\theta)^\mathsf{T}$ | $\phi = 0$ |
| Linear $+45^\circ$ | $\tfrac{1}{\sqrt2}(1,\,1)^\mathsf{T}$ | $\phi = 0$, $A_x = A_y$ |
| Right circular | $\tfrac{1}{\sqrt2}(1,\,i)^\mathsf{T}$ | $\phi = +90^\circ$, $A_x = A_y$ |
| Left circular | $\tfrac{1}{\sqrt2}(1,\,-i)^\mathsf{T}$ | $\phi = -90^\circ$, $A_x = A_y$ |
| Elliptical | general | everything else |

The intensity a photodiode sees is $I = \mathbf{J}^\dagger \mathbf{J}$, up to
detector responsivity and gain
([`../experiment/instruments.md`](../experiment/instruments.md)).

---

## 2. Jones matrices

Every passive, non-depolarising optical element is a $2\times2$ complex matrix
acting on $\mathbf{J}$. Elements compose by matrix multiplication **in reverse
order of traversal**: the first optic the light meets stands rightmost.

### Rotation

$$
R(\theta) = \begin{pmatrix} \cos\theta & -\sin\theta \\ \sin\theta & \cos\theta \end{pmatrix}.
$$

Any element whose natural axis is along $x$ is rotated to angle $\theta$ by the
similarity transform $M(\theta) = R(\theta) M R(-\theta)$ — that one line
generates everything below.

### Retarder

A retarder with fast axis along $x$ and retardance $\delta$ delays one
component relative to the other,
$W(\delta) = \mathrm{diag}\!\left(1,\, e^{i\delta}\right)$. Rotated to
fast-axis angle $\theta$:

$$
W(\delta,\theta) = R(\theta)\,W(\delta)\,R(-\theta) =
\begin{pmatrix}
\cos^2\theta + e^{i\delta}\sin^2\theta & (1 - e^{i\delta})\sin\theta\cos\theta \\
(1 - e^{i\delta})\sin\theta\cos\theta & \sin^2\theta + e^{i\delta}\cos^2\theta
\end{pmatrix}.
$$

Two special cases run this experiment:

- **Half-wave plate**, $\delta = \pi$. Up to a global phase,
  $$
  H(\theta) = \begin{pmatrix} \cos 2\theta & \sin 2\theta \\ \sin 2\theta & -\cos 2\theta \end{pmatrix},
  $$
  which is a **reflection of the polarisation about the fast axis**. Feed it
  linear light at angle $\theta_\mathrm{in}$ and you get linear light at
  $2\theta - \theta_\mathrm{in}$. Rotating the plate by $\Delta$ therefore
  rotates the polarisation by $2\Delta$ — the factor of two that makes a
  $180^\circ$ scan of incident polarisation cost only $90^\circ$ of motor
  travel.
- **Quarter-wave plate**, $\delta = \pi/2$. It converts linear to elliptical
  and, crucially for us, elliptical back to **linear** when its axis is set
  correctly. That is the compensator
  ([03 §2](03-senarmont-readout.md#2-what-the-qwp-does-geometrically)).

### Polariser

An ideal linear polariser transmitting along $x$ is the projector
$P = \mathrm{diag}(1,0)$, so at transmission angle $\theta$

$$
P(\theta) = R(\theta)\begin{pmatrix}1&0\\0&0\end{pmatrix}R(-\theta) =
\begin{pmatrix} \cos^2\theta & \sin\theta\cos\theta \\ \sin\theta\cos\theta & \sin^2\theta \end{pmatrix}.
$$

Acting on linear light at $\theta_\mathrm{in}$ it returns amplitude
$\cos(\theta - \theta_\mathrm{in})$ and intensity
$\cos^2(\theta - \theta_\mathrm{in})$ — **Malus' law**, which reappears in
null-referenced form as $I(\psi) = I_\mathrm{floor} + I_0\sin^2\psi$ on the
next page.

> **Note.** Jones calculus assumes fully polarised, coherent light and
> non-depolarising elements — both hold here. When they do not (partial
> depolarisation by a scattering sample, say) you need Stokes and Mueller
> matrices instead: §3–§6.

---

## 3. Stokes parameters

Jones vectors carry no notion of an incoherent mixture, so they cannot describe
partially polarised light. The **Stokes parameters** can, and they have the
further virtue of being directly measurable intensities:

$$
S_0 = \lvert E_x\rvert^2 + \lvert E_y\rvert^2, \qquad
S_1 = \lvert E_x\rvert^2 - \lvert E_y\rvert^2,
$$
$$
S_2 = 2\,\mathrm{Re}\!\left(E_x^{*}E_y\right), \qquad
S_3 = 2\,\mathrm{Im}\!\left(E_x^{*}E_y\right).
$$

Operationally: $S_0$ is the total intensity, $S_1$ the excess of horizontal over
vertical, $S_2$ the excess of $+45^\circ$ over $-45^\circ$, $S_3$ the excess of
right- over left-circular. Each is a difference of two intensity measurements
you could actually make with a polariser (and, for $S_3$, a quarter-wave plate)
in front of the detector. For fully polarised light
$S_1^2 + S_2^2 + S_3^2 = S_0^2$.

---

## 4. Degree of polarisation

$$
\mathrm{DOP} = \frac{\sqrt{S_1^2 + S_2^2 + S_3^2}}{S_0} \in [0,1].
$$

$\mathrm{DOP} = 1$ is fully polarised (on the sphere of §6), $0$ unpolarised
(at its centre), between partially polarised (inside). Here the DOP should stay
very close to 1 all the way to the analyser. A measurably reduced DOP means
something is depolarising the beam — scattering from a damaged pixel, a spot
straddling an electrode edge, stray light — and a depolarised component cannot
be extinguished by a linear analyser, so it shows up as an irreducible null
floor ([`../guide/troubleshooting.md`](../guide/troubleshooting.md)).

---

## 5. The polarisation ellipse

Traced over one optical cycle, the tip of the real electric field sweeps an
ellipse that two angles describe completely:

| Symbol | Name | Definition | Range |
| --- | --- | --- | --- |
| $\psi$ | **azimuth** | angle of the ellipse's major axis from $x$ | $0 \le \psi < 180^\circ$ |
| $\chi$ | **ellipticity angle** | $\tan\chi = \pm b/a$, the ratio of minor to major semi-axis, signed by handedness | $-45^\circ \le \chi \le 45^\circ$ |

$\chi = 0$ is linear, $\chi = \pm45^\circ$ circular, and the sign of $\chi$ is
the handedness. The Stokes parameters of a fully polarised beam are exactly
these two angles in disguise:

$$
S_1 = S_0\cos 2\chi \cos 2\psi, \qquad
S_2 = S_0\cos 2\chi \sin 2\psi, \qquad
S_3 = S_0 \sin 2\chi ,
$$

and inverting,

$$
\tan 2\psi = \frac{S_2}{S_1}, \qquad \sin 2\chi = \frac{S_3}{S_0}.
$$

![Polarisation ellipse at each point in the beamline](../../assets/figures/polarisation_ellipses.png)

*Figure 1 — the polarisation ellipse at each labelled point along the beam.*
*(a)* **After the polariser**: a straight line along the transmission axis.
This is the reference direction for every angle in the experiment, so it is
drawn horizontal and defines $\psi = 0$. *(b)* **After the half-wave plate**:
still a straight line, but rotated to the incident angle $\theta_i$. The plate
has changed only the azimuth — no ellipticity is introduced, which is exactly
why a HWP and not a rotating polariser is used to set $\theta_i$ (the intensity
is preserved too). *(c)* **After the BTO film**: an ellipse. The film is
statically birefringent even with no field applied, so the two eigencomponents
emerge with a relative phase and the state acquires ellipticity
$\chi \neq 0$. An elliptical state **cannot** be extinguished by a linear
analyser — there is always leakage — which is the problem the next element
solves.
*(d)* **After the quarter-wave plate**: a straight line again. The QWP has been
rotated to cancel exactly the ellipticity the sample introduced, returning the
state to $\chi = 0$ at some azimuth. *(e)* **After the analyser**: a line along
the analyser's own axis, with the amplitude set by the projection. When the
analyser is crossed with (d) the output is extinguished — the **null**. The
tiny field-induced change that the whole instrument exists to measure is a
sub-microradian wobble of the azimuth in panel (d), far too small to draw to
scale.

---

## 6. The Poincaré sphere

Normalise by $S_0$ and plot $(S_1, S_2, S_3)$: fully polarised states live on
the unit sphere, partially polarised states inside it.

| Axis | Positive pole | Negative pole |
| --- | --- | --- |
| $S_1$ | linear horizontal (the polariser axis) | linear vertical |
| $S_2$ | linear $+45^\circ$ | linear $-45^\circ$ |
| $S_3$ | right circular | left circular |

The geometry to remember:

- **The equator is every linear state.** Going once round it corresponds to
  rotating a linear polarisation by $180^\circ$, because the sphere coordinates
  are $(2\psi, 2\chi)$ — longitude is *twice* the azimuth, latitude is *twice*
  the ellipticity angle. A half-wave plate rotated by $\Delta$ therefore moves
  a linear state $4\Delta$ around the equator.
- **The poles are circular.** Everything between is elliptical, with latitude
  increasing with ellipticity.
- **Antipodal points are orthogonal states** — H and V, $\pm45^\circ$, and the
  two circular handednesses are all antipodal pairs.
- **A retarder is a rigid rotation of the sphere**, about the diameter through
  the Stokes point of its own fast eigenstate, through an angle equal to its
  retardance $\delta$. This one rule replaces most waveplate algebra: a HWP is
  a half-turn about an equatorial axis, a QWP a quarter-turn.
- **An analyser is a projection**: the detected intensity is
  $\tfrac{1}{2}S_0(1 + \hat{a}\cdot\hat{s})$, with $\hat{a}$ the analyser's
  Stokes direction and $\hat{s}$ the state. Extinction is exactly antipodality.

![Poincaré-sphere trajectory through the setup](../../assets/figures/poincare_beamline.png)

*Figure 2 — the same beamline as Figure 1, drawn on the Poincaré sphere.* The
state starts at the $+S_1$ pole (**P**, linear along the polariser axis).
The **HWP** carries it along the equator to longitude $2\theta_i$ (**H**) — it
is still linear, just re-aimed. The **sample** is a retarder whose axis is set
by the film's own birefringence, so it rotates the state about that axis by the
static retardance, lifting it **off the equator** to an elliptical state
(**S**); the latitude reached is a direct picture of how much static
birefringence this pixel has, and it differs from pixel to pixel with
thickness, composition, strain and domain state. The **QWP** is then rotated
until its quarter-turn brings the state back **down onto the equator** (**Q**)
— that is what "compensating the static birefringence" means geometrically.
The **analyser** is placed antipodally to **Q**, and the detector sees
extinction. The measurement then consists of stepping a controlled
$\pm90^\circ$ of *longitude* along the equator away from that null (which is
$\pm45^\circ$ of physical analyser rotation) and watching a microradian-scale
wobble of **Q**; that last step is drawn separately in
[Figure 3](03-senarmont-readout.md#6-the-modulation-on-the-sphere).

---

## 7. The Jones chain of this instrument

The beamline, in order of traversal
([`../experiment/beamline.md`](../experiment/beamline.md)):

```text
KLS1550 → polariser → HWP → [ BTO pixel ] → QWP → analyser → PDA30B2
1550 nm    defines      sets    the sample     cancels  converts   light
~7 mW      the frame    θ_i     (static Γ₀)    Γ₀       pol.→int.  → volts
```

Written as a matrix product — remember, **right to left** —

$$
\mathbf{J}_\mathrm{out} \;=\;
\underbrace{P(\psi_A)}_{\text{analyser}}\;
\underbrace{W\!\left(\tfrac{\pi}{2},\, q\right)}_{\text{QWP}}\;
\underbrace{W\!\left(\Gamma_0 + \Gamma(t),\, \theta_s\right)}_{\text{BTO pixel}}\;
\underbrace{H(h)}_{\text{HWP}}\;
\underbrace{P(0)}_{\text{polariser}}\;
\mathbf{J}_\mathrm{in},
$$

and the photodiode reads
$I = \mathbf{J}_\mathrm{out}^\dagger \mathbf{J}_\mathrm{out}$.

Reading the factors from the right:

| Factor | What it does | Fixed or swept |
| --- | --- | --- |
| $P(0)$ | cleans up whatever the fibre did and **defines the frame**: its transmission axis is the zero of every angle in the experiment | fixed, mechanically |
| $H(h)$ | reflects the polarisation about the plate's fast axis, producing linear light at $\theta_i$; the calibrated lab mapping gives $\theta_i$ from $h$, with $\theta_i \approx 2h + \text{offset}$ | swept over the 9-point $\theta_i$ grid |
| $W(\Gamma_0 + \Gamma(t), \theta_s)$ | the sample: a retarder with a large **static** retardance $\Gamma_0$ and eigenaxis $\theta_s$ set by the film, plus the tiny field-induced increment $\Gamma(t) = \Gamma_\mathrm{ac}\cos\omega t$ from [01 §6](01-electro-optics.md#6-from-field-to-retardation) | $\Gamma(t)$ driven at 30 kHz |
| $W(\pi/2, q)$ | the compensator: rotated to $q_\mathrm{null}$ so the product with the sample leaves **linear** light | set per pixel and per HWP angle |
| $P(\psi_A)$ | the readout: $\psi_A = a_\mathrm{null}$ extinguishes; $\psi_A = a_\mathrm{null} \pm 45^\circ$ are the two slope points | stepped through the triplet |

Three structural facts fall out of this product, each of which is a design
decision elsewhere in the instrument:

1. **$\Gamma_0$ and $\theta_s$ are per-pixel unknowns, and they never have to
   be known.** The pair $(q_\mathrm{null}, a_\mathrm{null})$ is found
   empirically for each pixel and each HWP angle, and every reading is defined
   *relative to that null*. Static birefringence and arbitrary encoder zeros
   cancel identically — see
   [04 §6](04-incident-polarisation.md#6-the-reference-frame-argument).
2. **The signal is the derivative of this product with respect to $\Gamma$.**
   Because $\Gamma(t)$ is microradian-scale, only the first-order term matters,
   and its size is set by $\partial I/\partial\psi_A$ at the operating point.
   Maximising that derivative is the entire content of the next page.
3. **Nothing in the chain is intensity-calibrated.** $\mathbf{J}_\mathrm{in}$
   carries the laser power, the fibre coupling and the focus quality, and
   $I \to V$ carries the detector gain. All of it multiplies the whole product,
   so all of it divides out when the lock-in response is normalised by the
   locally measured DC slope
   ([03 §9](03-senarmont-readout.md#step-1--malus-normalisation)).

---

## Continue

- [03 — The Null-Slope Sénarmont Readout](03-senarmont-readout.md): what to do
  with this chain to make a microradian visible.
- [01 — The Pockels Effect](01-electro-optics.md): where $\Gamma(t)$ comes from.
- [`../experiment/beamline.md`](../experiment/beamline.md): the physical optics,
  mounts and rotators that realise each matrix.
- [`../software/instrument-control.md`](../software/instrument-control.md): how
  $h$, $q$ and $\psi_A$ are commanded and verified on the ELL14 rotators.

---

<div align="center">

[← The Pockels effect](01-electro-optics.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Null-slope readout →](03-senarmont-readout.md)

</div>
