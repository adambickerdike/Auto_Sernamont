# The Pockels Effect

**What this page is for:** to establish what physical quantity the instrument
is trying to measure. It starts from the index ellipsoid, derives why a
non-centrosymmetric crystal has a linear electro-optic response at all, writes
down the BaTiO₃ tensor explicitly, and ends at the retardation formula and the
electrode geometry that every later page depends on.

Prerequisite: undergraduate electromagnetism. No prior exposure to
crystal optics is assumed.

---

## 1. The index ellipsoid

A crystal's linear optical response is completely described by the **index
ellipsoid** (the optical indicatrix):

$$
\sum_{i,j} \left(\frac{1}{n^2}\right)_{ij} x_i x_j = 1 .
$$

Read it geometrically: slice the ellipsoid through the origin with the plane
normal to your propagation direction, and the half-lengths of the resulting
ellipse's principal axes are the two refractive indices of the two
eigenpolarisations. In an isotropic material the ellipsoid is a sphere, both
indices are equal and there is no birefringence; in a uniaxial crystal it is an
ellipsoid of revolution, with the ordinary index $n_o$ across the optic axis
and the extraordinary index $n_e$ along it.

> **Why this object and not "the refractive index".** Once the material is
> anisotropic, "the index" is not a number — it depends on propagation
> direction *and* polarisation. The ellipsoid is the smallest object carrying
> all of that, and the electro-optic effect is most cleanly written as a
> **deformation of it** — which is why the tensor is defined on $(1/n^2)$
> rather than on $n$.

---

## 2. Why inversion symmetry matters

In a material **lacking a centre of inversion**, an applied electric field
$\mathbf{E}$ perturbs the indicatrix coefficients *linearly* in the field. That
is the **Pockels** (linear electro-optic) effect:

$$
\Delta\!\left(\frac{1}{n^2}\right)_{i} \;=\; \sum_{j=1}^{3} r_{ij}\, E_j ,
\qquad i = 1,\dots,6 ,
$$

with $r_{ij}$ the **electro-optic tensor** in contracted (Voigt) notation.

The inversion-symmetry requirement is not a technicality, and the argument is
worth being able to state in one breath. If the crystal is centrosymmetric then
$\mathbf{x} \to -\mathbf{x}$ is a symmetry of the material; it sends
$\mathbf{E} \to -\mathbf{E}$ while leaving the index ellipsoid (a quadratic
form) unchanged. The constitutive relation must therefore hold with **both**
signs of the right-hand side at once, which forces $r_{ij} = 0$ identically.
All that survives is the quadratic (Kerr) term $\propto E^2$, very much weaker
at accessible fields.

Three consequences follow, and all three are used as experimental tests later:

1. **A linear response is evidence of broken inversion symmetry.** In BaTiO₃
   that symmetry is broken by the ferroelectric distortion, so the Pockels
   response is a direct probe of the polar state ([05](05-ferroelectrics.md)).
2. **The response is odd in $P_s$.** Reverse the spontaneous polarisation and
   the sign of the response reverses — which is why multi-domain regions
   cancel, why we pole, and why hysteresis loops exist at all.
3. **The response must be linear in $V$.** A signal that is not linear in drive
   voltage is not (purely) a Pockels effect. The analysis enforces this with an
   $R^2 \ge 0.98$ gate
   ([03](03-senarmont-readout.md#step-2--voltage-linearity)).

---

## 3. Voigt (contracted) notation

$\Delta(1/n^2)_{ij}$ is a symmetric $3\times3$ tensor, so it has six
independent components, not nine. Voigt notation packs them into one index:

| Voigt index $i$ | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| Tensor pair $(jk)$ | $11$ | $22$ | $33$ | $23 = 32$ | $13 = 31$ | $12 = 21$ |
| Meaning | axial | axial | axial | shear | shear | shear |

So $r$ is a $6\times3$ matrix: six ways of deforming the ellipsoid, three
components of field. Indices 1–3 stretch or squash it along its own principal
axes; indices 4–6 **rotate** it by introducing off-diagonal terms. That
distinction is the reason this experiment sweeps a half-wave plate (§5).

---

## 4. Barium titanate: point group 4mm

Below its Curie temperature the tetragonal phase of barium titanate has point
group $4mm$ — non-centrosymmetric, hence Pockels-active. Crystal symmetry
forces most of the tensor to vanish and ties the survivors together, leaving
three independent non-zero coefficients:

$$
r =
\begin{pmatrix}
0 & 0 & r_{13} \\
0 & 0 & r_{13} \\
0 & 0 & r_{33} \\
0 & r_{42} & 0 \\
r_{42} & 0 & 0 \\
0 & 0 & 0
\end{pmatrix}
$$

| Coefficient | Voigt slot | Character | Bulk magnitude |
| --- | --- | --- | --- |
| $r_{13}$ | rows 1 and 2, column 3 | axial; field along $c$ changes the indices transverse to $c$ | $\sim 10$–$10^2$ pm/V |
| $r_{33}$ | row 3, column 3 | axial; field along $c$ changes the index along $c$ | $\sim 10$–$10^2$ pm/V |
| $r_{42}$ | rows 4 and 5 | **shear**; field transverse to $c$ rotates the ellipsoid | $\sim 10^3$ pm/V — by far the largest |

For scale: lithium niobate, the incumbent modulator material, has
$r_{33} \approx 30$ pm/V. A BaTiO₃ $r_{42}$ of order $10^3$ pm/V is more than an
order of magnitude better — *if* the film can be grown well and its domains
controlled. That conditional is the research question the instrument exists to
answer, and it is why we map 100 pixels across a compositional gradient rather
than measuring one spot ([`../experiment/chip.md`](../experiment/chip.md)).

---

## 5. Why the shear coefficient makes the response angle-dependent

Voigt rows 4 and 5 populate the **off-diagonal** entries of
$\Delta(1/n^2)_{ij}$. An off-diagonal term does not lengthen or shorten the
ellipsoid's axes; it **tilts** them. Concretely, with the polar $c$ axis along
$z$ and a field $E_2$ applied transverse to it, row 4 gives

$$
\Delta\!\left(\frac{1}{n^2}\right)_{4} = \Delta\!\left(\frac{1}{n^2}\right)_{23} = r_{42} E_2 ,
$$

which mixes the $y$ and $z$ axes of the ellipsoid. The new eigenpolarisations
are rotated relative to the old ones.

The experimental consequence is decisive:

- An **axial** coefficient ($r_{13}$, $r_{33}$) changes the birefringence
  magnitude without moving the eigenaxes. Light polarised along an eigenaxis
  keeps its state; the effect is maximal when the polarisation is decomposed
  equally onto both eigenaxes.
- A **shear** coefficient rotates the eigenaxes, so its effect on the output
  state depends on the angle $\theta_i$ between the incident optical
  polarisation and the in-plane field — and it maximises at a *different*
  angle from the axial terms.

This is why the instrument never measures "the response" at one incident
polarisation. It sweeps the half-wave plate across a grid of $\theta_i$ and
fits the angular signature, which both finds the strongest operating point and
serves as a physics test that the signal really is electro-optic
([04](04-incident-polarisation.md)) — electrical pickup has no angular
signature at all.

> **The honest caveat, stated early.** The angular scan constrains the
> *projection* of the tensor onto this geometry. It does **not** by itself
> separate $r_{42}$ from $r_{13}/r_{33}$, and it does not fix the crystal axes.
> That needs a texture/domain model plus independent structural information
> (XRD or similar). See [04 §What a four-lobed plot proves](04-incident-polarisation.md#5-what-a-four-lobed-polar-plot-proves--and-what-it-does-not).

### The $r_c$ combination

For a $c$-axis-oriented film probed with an in-plane field, the commonly used
effective combination (Abel, eq. 3.9) is

$$
r_c \;=\; r_{33} \;-\; \left(\frac{n_o}{n_e}\right)^{3} r_{13},
$$

with $n_o$ and $n_e$ the ordinary and extraordinary indices. The $(n_o/n_e)^3$
weighting is not decoration: it is exactly the $n^3$ factor of §6 evaluated for
each eigenpolarisation, so the combination is the *difference of retardations*
accumulated by the two eigenwaves. $r_c$ is what a null-slope measurement on a $c$-oriented film reports when the
axial terms dominate; when the response is $r_{42}$-dominated the projection is
different again — which is precisely why the reported number is always called
$r_\mathrm{eff}$.

---

## 6. From field to retardation

A field-induced index change of magnitude

$$
\Delta n \;=\; -\tfrac{1}{2}\, n^{3}\, r_{\mathrm{eff}}\, E
$$

follows from differentiating $\Delta(1/n^2) = -2\,\Delta n / n^3$. Accumulated
over an interaction length $t$ — here the BTO film thickness, because the light
passes through the film once — it produces an optical phase difference between
the two eigenpolarisations:

$$
\Gamma \;=\; \frac{2\pi}{\lambda}\, \Delta n \, t
\;=\; \frac{\pi\, n^{3}\, r_{\mathrm{eff}}\, E\, t}{\lambda} .
$$

Two features of this expression drive every design decision downstream.

- **$\Gamma \propto E$, linearly.** Double the drive voltage, double the
  retardation. This is the single most useful diagnostic available, and it is
  enforced as a gate rather than assumed.
- **$\Gamma \propto n^3 r_\mathrm{eff} / \lambda$.** The $n^3$ is why
  high-index materials are attractive. The explicit $\lambda$ is why the
  wavelength must be known and fixed — 1550 nm here, both because BaTiO₃ is
  transparent there and because it is the band a real modulator would work in,
  so the number transfers
  ([`../experiment/beamline.md`](../experiment/beamline.md)).

In the compensated Sénarmont geometry used here, the retardation and the
measured polarisation rotation are related by $\Gamma = 2\delta$; that factor
of two is derived in [03](03-senarmont-readout.md#8-the-derivative-aligned-rotation).

---

## 7. The field between coplanar electrodes

The chip carries **coplanar** (in-plane) electrode pairs with a gap $g$ of
order 7 µm, not a sandwich structure: the film is only a few hundred
nanometres thick, so a vertical field would need a transparent top contact and
a conducting substrate; the in-plane geometry couples to the large shear
coefficient for a $c$-textured film; and it is the geometry real integrated BTO
modulators use.

The cost is that the field is **not** $V/g$. It fringes, it is strongly
non-uniform across the gap and with depth, and the optical mode samples a
weighted average of it. The instrument therefore writes

$$
E \;=\; \frac{\alpha\, V_{\mathrm{device}}}{g},
$$

where $\alpha$ is an electrostatic correction obtained from a finite-element
model of **this** electrode and illuminated-volume geometry.

> **Warning: $\alpha$ is not a transferable material constant.** It is a
> property of a particular electrode cross-section, gap, film stack and beam
> profile. Picavet *et al.* used $\alpha = 0.942$ for one specific 10 µm
> free-space geometry; quoting that value for a different device is a
> calculation, not a measurement. Without an FEM-derived $\alpha$ for this chip
> the software reports the normalised rotation only and never
> $r_{\mathrm{eff}}$.

Note also the distinction between $V_\mathrm{source}$ and $V_\mathrm{device}$:
at 30 kHz, through cabling and the switching matrix, the voltage actually
appearing across the electrodes is not the voltage the function generator was
told to produce. The ratio $V_\mathrm{device}/V_\mathrm{source}$ is a separate
measured input
([`../experiment/switch-matrix.md`](../experiment/switch-matrix.md)).

---

## 8. Why the answer is always an *effective* coefficient

Even when every input is supplied and every gate passes, the emitted number is
$\lvert r_{\mathrm{eff}} \rvert$ — a **geometry-specific effective
coefficient** that folds in:

| Ingredient | Why it enters |
| --- | --- |
| crystal texture | which tensor components project onto this geometry at all |
| domain population and poling history | the response is odd in $P_s$, so partially cancelling domains reduce it |
| tensor projection | the angle between the optical polarisation, the field, and the crystal axes |
| electrode field distribution | $\alpha$, and the mode-weighted average over a non-uniform field |
| optical geometry | single pass through a thin film, in the Sénarmont configuration |
| frequency | 30 kHz: domains can follow, so this is the domain-mediated response, not the clamped lattice response |
| temperature | $T_c$, domain structure and the coefficients themselves are temperature-dependent |

It is therefore never quoted as an intrinsic tensor element of BaTiO₃, and it
is never compared directly against a single-crystal literature value without
that caveat attached.

What **is** safely comparable is the **normalised rotation** $\delta$ across
pixels of the same chip: the optical and electronic chain is shared, laser
power, coupling, detector gain and analyser placement are divided out by the
Malus normalisation, and $\alpha$, $t$, $g$, $n$ are common to every pixel.
That is why the compositional observable here is the normalised rotation, not
raw microvolts.

---

## Continue

- [02 — Polarisation Formalism](02-polarisation.md): the Jones and Stokes
  language used to track what $\Gamma$ does to the beam.
- [03 — The Null-Slope Sénarmont Readout](03-senarmont-readout.md): how a
  microradian of $\delta$ becomes a measurable voltage, and the full chain back
  to $\lvert r_\mathrm{eff}\rvert$.
- [`../experiment/chip.md`](../experiment/chip.md): the electrode geometry,
  gap and pixel layout that set $g$ and $\alpha$.
- [`../reference/glossary.md`](../reference/glossary.md): every symbol on this
  page, with its units and the CSV column that carries it.

---

<div align="center">

[← Physics](index.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Polarisation formalism →](02-polarisation.md)

</div>
