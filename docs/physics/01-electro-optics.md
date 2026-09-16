# The Pockels Effect

**What this page is for:** to establish what physical quantity the instrument
is trying to measure. It starts from the index ellipsoid, derives why a
non-centrosymmetric crystal has a linear electro-optic response at all,
contracts the third-rank tensor to Voigt form explicitly, writes down the
BaTiO₃ tensor, solves the eigenvalue problem for the field-induced axes, and
ends at the retardation formula, the standard figures of merit, and the
electrode geometry that every later page depends on.

Prerequisite: undergraduate electromagnetism. No prior exposure to crystal
optics is assumed.

---

## 1. The index ellipsoid

A crystal's linear optical response is completely described by the **index
ellipsoid**, or optical indicatrix [[3]](../references.md#ref-3):

$$
\sum_{i,j} B_{ij}\, x_i x_j \;=\; 1,
\qquad
B_{ij} \;\equiv\; \left(\frac{1}{n^2}\right)_{ij}
\;=\; \varepsilon_0\left(\varepsilon^{-1}\right)_{ij},
\tag{1}
$$

where $\varepsilon_{ij}$ is the dielectric tensor at optical frequency and
$B_{ij}$ is its inverse, the **impermeability tensor**. Both are real and
symmetric in a transparent, non-optically-active crystal, so both have three
orthogonal principal axes and three principal values.

Read (1) geometrically: slice the ellipsoid through the origin with the plane
normal to your propagation direction, and the half-lengths of the resulting
ellipse's principal axes are the two refractive indices of the two
eigenpolarisations, with the axis directions giving those polarisations. In an
isotropic material the ellipsoid is a sphere, both indices are equal and there
is no birefringence. In a uniaxial crystal it is an ellipsoid of revolution,
with the ordinary index $n_o$ across the optic axis and the extraordinary
index $n_e$ along it, so that in the principal frame

$$
B \;=\; \mathrm{diag}\!\left(\frac{1}{n_o^2},\, \frac{1}{n_o^2},\, \frac{1}{n_e^2}\right).
\tag{2}
$$

> **Why this object and not "the refractive index".** Once the material is
> anisotropic, "the index" is not a number: it depends on propagation
> direction *and* polarisation. The ellipsoid is the smallest object carrying
> all of that, and the electro-optic effect is most cleanly written as a
> **deformation of it**, which is why the tensor is defined on $B = 1/n^2$
> rather than on $n$ [[2]](../references.md#ref-2).

---

## 2. Why inversion symmetry matters

In a material **lacking a centre of inversion**, an applied electric field
$\mathbf{E}$ perturbs the impermeability tensor *linearly* in the field. That
is the **Pockels**, or linear electro-optic, effect
[[1]](../references.md#ref-1):

$$
\Delta B_{ij} \;=\; \sum_{k=1}^{3} r_{ijk}\, E_k
\;+\; \sum_{k,l} s_{ijkl}\,E_k E_l \;+\; \dots
\tag{3}
$$

with $r_{ijk}$ the third-rank **electro-optic tensor** and $s_{ijkl}$ the
quadratic (Kerr) tensor.

The inversion-symmetry requirement is not a technicality, and the argument is
worth being able to state in one breath. Suppose the crystal is
centrosymmetric, so that $\mathbf{x} \to -\mathbf{x}$ is a symmetry of the
material. Under that operation the index ellipsoid, a quadratic form, is
unchanged, so $\Delta B_{ij} \to \Delta B_{ij}$; but a polar vector reverses,
so $E_k \to -E_k$. Applying the transformation to (3),

$$
\Delta B_{ij} \;=\; -\sum_k r_{ijk}E_k
\quad\text{and}\quad
\Delta B_{ij} \;=\; +\sum_k r_{ijk}E_k
\qquad\Longrightarrow\qquad
r_{ijk} \equiv 0 .
\tag{4}
$$

All that survives is the quadratic term, which is even in the field and
therefore allowed in any symmetry, and which is very much weaker at accessible
fields [[5]](../references.md#ref-5). The same argument in the language of
nonlinear optics says that $r_{ijk}$ is a low-frequency limit of the
second-order susceptibility $\chi^{(2)}$, which vanishes in centrosymmetric
media for exactly the same reason.

Three consequences follow, and all three are used as experimental tests later.

1. **A linear response is evidence of broken inversion symmetry.** In BaTiO₃
   that symmetry is broken by the ferroelectric distortion, so the Pockels
   response is a direct probe of the polar state
   ([05](05-ferroelectrics.md)).
2. **The response is odd in $P_s$.** Reverse the spontaneous polarisation and
   the sign of the response reverses. That is why multi-domain regions cancel,
   why we pole, and why hysteresis loops exist at all. The underlying reason is
   that in a perovskite the linear coefficient is the quadratic coefficient
   biased by the spontaneous polarisation,
   $r \propto g\,\varepsilon_0\varepsilon_r P_s$
   [[6]](../references.md#ref-6), so $r$ is linear in $P_s$.
3. **The response must be linear in $V$.** A signal that is not linear in
   drive voltage is not, or not purely, a Pockels effect. The analysis enforces
   this with an $R^2 \ge 0.98$ gate
   ([03 §12](03-senarmont-readout.md#12-from-lock-in-volts-to-physics)).

---

## 3. Voigt (contracted) notation

Two symmetries reduce the $3\times3\times3 = 27$ components of $r_{ijk}$ to
18, and Voigt notation is the bookkeeping that makes that visible
[[2]](../references.md#ref-2).

**Step 1: the first two indices are symmetric.** $\Delta B_{ij}$ is a
symmetric tensor because $B$ is, so

$$
r_{ijk} \;=\; r_{jik}
\tag{5}
$$

and the pair $(ij)$ has only six independent values, not nine.

**Step 2: contract the pair onto a single index.** Define the Voigt index
$m = m(ij)$ by the standard table, and set $r_{mk} \equiv r_{ijk}$:

| Voigt index $m$ | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | --- | --- | --- | --- | --- | --- |
| Tensor pair $(ij)$ | $11$ | $22$ | $33$ | $23 = 32$ | $13 = 31$ | $12 = 21$ |
| Meaning | axial | axial | axial | shear | shear | shear |

so that (3) becomes, to first order,

$$
\boxed{\;
\Delta B_m \;=\; \sum_{k=1}^{3} r_{mk}\,E_k ,
\qquad m = 1,\dots,6 .
\;}
\tag{6}
$$

$r$ is therefore a $6\times3$ matrix: six ways of deforming the ellipsoid,
three components of field.

> **Note on the factors of two.** No factor of 2 appears in (6). The
> factor-of-two conventions that complicate the Voigt reduction of the
> elasto-optic and piezoelectric tensors arise because engineering shear
> strains are defined as $\gamma_4 = 2\epsilon_{23}$. $\Delta B$ is not a
> strain and carries no such convention: $\Delta B_4$ *is* $\Delta B_{23}$
> [[2]](../references.md#ref-2). This matters when combining coefficients from
> different sources.

Written out, the deformed indicatrix is

$$
\left(B_1 + \Delta B_1\right)x^2 + \left(B_2 + \Delta B_2\right)y^2 + \left(B_3 + \Delta B_3\right)z^2
+ 2\Delta B_4\, yz + 2\Delta B_5\, xz + 2\Delta B_6\, xy \;=\; 1 .
\tag{7}
$$

Rows 1 to 3 stretch or squash the ellipsoid along its own principal axes. Rows
4 to 6 introduce off-diagonal terms, which **rotate** it. That distinction is
the reason this experiment sweeps a half-wave plate (§5).

---

## 4. Barium titanate: point group 4mm

Below its Curie temperature the tetragonal phase of barium titanate has point
group $4mm$, which is non-centrosymmetric and hence Pockels-active. Applying
the $4mm$ symmetry operations to $r_{mk}$ forces most entries to vanish and
ties the survivors together [[2]](../references.md#ref-2),
[[3]](../references.md#ref-3), leaving three independent non-zero
coefficients:

$$
r \;=\;
\begin{pmatrix}
0 & 0 & r_{13} \\
0 & 0 & r_{13} \\
0 & 0 & r_{33} \\
0 & r_{42} & 0 \\
r_{42} & 0 & 0 \\
0 & 0 & 0
\end{pmatrix}
\tag{8}
$$

with the polar $c$ axis along $x_3$. Note the two constraints the symmetry
imposes and that are easy to miss: $r_{23} = r_{13}$, because the fourfold
axis makes $x_1$ and $x_2$ equivalent, and $r_{51} = r_{42}$, for the same
reason applied to the shears.

| Coefficient | Voigt slot | Character | Bulk magnitude |
| --- | --- | --- | --- |
| $r_{13}$ | rows 1 and 2, column 3 | axial; field along $c$ changes the indices transverse to $c$ | $\sim 10$ to $10^2$ pm/V |
| $r_{33}$ | row 3, column 3 | axial; field along $c$ changes the index along $c$ | $\sim 10$ to $10^2$ pm/V |
| $r_{42}$ | rows 4 and 5 | **shear**; field transverse to $c$ rotates the ellipsoid | $\sim 10^3$ pm/V, by far the largest |

Those magnitudes are single-crystal values [[7]](../references.md#ref-7). For
scale, lithium niobate, the incumbent modulator material, has
$r_{33} \approx 30$ pm/V. The figure of merit that matters is $n^3 r$ (§7):

| Material | $n$ | $r$ used | $n^3 r$ |
| --- | --- | --- | --- |
| LiNbO₃ | $\approx 2.2$ | $r_{33} \approx 30$ pm/V | $\approx 3\times10^2$ pm/V |
| BaTiO₃ | $\approx 2.1$ | $r_{42} \sim 10^3$ pm/V | $\sim 9\times10^3$ pm/V |

So a BaTiO₃ film exploiting $r_{42}$ is worth roughly thirty times a lithium
niobate device of the same length, *if* the film can be grown well and its
domains controlled [[27]](../references.md#ref-27). That conditional is the
research question the instrument exists to answer, and it is why we map 100
pixels across a compositional gradient rather than measuring one spot
([`../experiment/chip.md`](../experiment/chip.md)).

> **Warning.** $n^3 r$ is the *modulation-depth* figure of merit. The
> *energy* figure of merit for a modulator is closer to $n^3 r/\varepsilon_r$,
> and barium titanate has an enormous $\varepsilon_r$. A large $n^3r$ therefore
> does not by itself settle whether a device will be efficient
> [[4]](../references.md#ref-4). This instrument measures the numerator only.

---

## 5. Why the shear coefficient makes the response angle-dependent

### 5.1 The indicatrix in a purely in-plane field

The chip applies a field in the film plane, between coplanar electrodes (§8).
Take the local crystal frame with $c$ along $x_3$ and write the applied field
as $\mathbf{E} = (E_1, E_2, E_3)$. Substituting (8) into (6) gives all six
components at once:

$$
\Delta B_1 = \Delta B_2 = r_{13}E_3, \quad
\Delta B_3 = r_{33}E_3, \quad
\Delta B_4 = r_{42}E_2, \quad
\Delta B_5 = r_{42}E_1, \quad
\Delta B_6 = 0 .
\tag{9}
$$

Equation (9) contains the central structural fact of this measurement:

> **The axial coefficients respond only to $E_3$, the field along $c$. The
> shear coefficient responds only to $E_1$ and $E_2$, the field perpendicular
> to $c$. They are never driven by the same field component.**

### 5.2 The eigenvalue problem for the induced axes

Take the field along $x_1$ and the light along $x_2$, so
$\Delta B_5 = r_{42}E_1$ is the only non-zero perturbation and the section
plane the light sees is $x_1 x_3$. This is the frame used for the shear
mechanism throughout the manual, in the table of §5.3 and in
[06 §4.2](06-material.md#42-the-visibility-rule); the other labelling, light
along $x_1$ with the field along $x_2$, gives $\Delta B_4 = r_{42}E_2$ instead
and is the same physics by the fourfold symmetry. From (7) the impermeability
matrix is

$$
B \;=\;
\begin{pmatrix}
1/n_o^2 & 0 & r_{42}E_1 \\
0 & 1/n_o^2 & 0 \\
r_{42}E_1 & 0 & 1/n_e^2
\end{pmatrix}.
\tag{10}
$$

The $x_2$ axis is untouched and remains an eigenvector with eigenvalue
$1/n_o^2$. The $x_1$ and $x_3$ axes mix, and diagonalising the $2\times2$
block gives the new principal values and the rotation angle $\rho$ of the
principal axes within the $x_1 x_3$ plane:

$$
\lambda_\pm = \frac{1}{2}\left(\frac{1}{n_o^2} + \frac{1}{n_e^2}\right)
\pm \frac{1}{2}\sqrt{\left(\frac{1}{n_o^2} - \frac{1}{n_e^2}\right)^2 + 4\left(r_{42}E_1\right)^2},
\tag{11}
$$

$$
\boxed{\;
\tan 2\rho \;=\; \frac{2\,r_{42}E_1}{\dfrac{1}{n_o^2} - \dfrac{1}{n_e^2}} .
\;}
\tag{12}
$$

Equation (12) is worth simplifying, because the result is memorable. With
$\Delta n = n_o - n_e$ the natural birefringence and $n$ a mean index,

$$
\frac{1}{n_o^2} - \frac{1}{n_e^2} \;=\; \frac{n_e^2 - n_o^2}{n_o^2 n_e^2}
\;\approx\; -\,\frac{2\,\Delta n}{n^{3}},
\qquad\text{so}\qquad
\rho \;\approx\; -\,\frac{\tfrac{1}{2}n^{3}r_{42}E_1}{\Delta n} .
\tag{13}
$$

The induced axis rotation is the **ratio of the field-induced index change to
the natural birefringence**. Barium titanate has a small natural birefringence
and a very large $r_{42}$, so the denominator is small and the numerator is
large: at a field of order $1$ V/µm, with $n^3 r_{42}$ of order $10^4$ pm/V
and $\Delta n$ of order $10^{-2}$, $\rho$ reaches a few tenths of a radian.
An axis rotation of that size from a field of a few volts is the whole reason
$r_{42}$ is interesting.

### 5.3 Which components can be seen at normal incidence

The instrument sends light through the film **along the surface normal**. From
§1, the indices it sees are the semi-axes of the section of the indicatrix cut
by the plane perpendicular to the propagation direction. Take that direction
as $z_\mathrm{lab}$; the relevant section is $z_\mathrm{lab} = 0$, and only the
components of $\Delta B$ *within that plane* can change what the light does at
first order. Combining that with (9) gives a table which settles a great deal.

| Local polar axis $c$ | In-plane field along | $\Delta B$ components in the section plane | What the light sees |
| --- | --- | --- | --- |
| **normal to the film** | any in-plane direction | none: $\Delta B_4, \Delta B_5$ lie out of the section, $\Delta B_6 = 0$ | **nothing at first order** |
| **in the film plane** | along $c$ | $\Delta B_1 = r_{13}E$ and $\Delta B_3 = r_{33}E$, both in the section | the birefringence **magnitude** is modulated, eigenaxes fixed |
| **in the film plane** | perpendicular to $c$, in plane | $\Delta B_5 = r_{42}E$, in the section (field along $x_1$, light along $x_2$, the frame of §5.2) | the in-plane eigenaxes are **rotated** by $\rho$ |

Three things follow, and they are the honest framing of every number this
instrument produces.

1. **A perfect single-domain film with $c$ normal to the surface would give no
   first-order signal at all in this geometry.** Any response measured on such
   a film comes from departures from that ideal: $a$-domains with $c$ in the
   plane, grain tilt, strain gradients, or the spread of ray directions in a
   focused beam. This is a strong statement and it is why the domain and
   texture state is part of the result rather than a nuisance.
2. **An axial response and a shear response are distinguished by what they do,
   not only by how big they are.** Axial terms change the retardance; shear
   terms rotate the eigenaxes. [04](04-incident-polarisation.md) shows that the
   two appear in *quadrature* in the incident-polarisation angle, which is the
   experimental handle on the distinction.
3. **A single incident polarisation reports one arbitrary projection.** Hence
   the half-wave plate sweep.

### 5.4 The $r_c$ combination

For the second row of the table, with the polar axis in the film plane and the
field along it, the two in-plane eigenwaves are polarised along $c$ (index
$n_e$) and perpendicular to it (index $n_o$). Using $\Delta n = -\tfrac{1}{2}
n^3 \Delta B$ from §6, their index changes are
$-\tfrac{1}{2}n_e^3 r_{33}E$ and $-\tfrac{1}{2}n_o^3 r_{13}E$, so the
*difference* that produces retardance is

$$
\Delta n_e - \Delta n_o
= -\tfrac{1}{2}\left(n_e^3 r_{33} - n_o^3 r_{13}\right)E
= -\tfrac{1}{2}\,n_e^3 \underbrace{\left[r_{33} - \left(\frac{n_o}{n_e}\right)^{3} r_{13}\right]}_{\textstyle r_c} E .
\tag{14}
$$

This is the combination quoted by Abel [[31]](../references.md#ref-31),
equation 3.9. The $(n_o/n_e)^3$ weighting is not decoration: it is exactly the
$n^3$ factor of §6 evaluated separately for each eigenwave, so $r_c$ is the
*difference of retardations* accumulated by the two eigenwaves, not a
difference of tensor elements. Comparing (14) with the general form of §6
shows that $n^3 r_\mathrm{eff} = n_e^3 r_c$ in this geometry.

$r_c$ is what a null-slope measurement reports when the axial terms dominate.
When the response is $r_{42}$-dominated the field rotates the in-plane
eigenaxes rather than changing the retardance between them, at leading order
in $r_{42}E/\Delta n$; in the limit of vanishing static birefringence the
same shear instead creates a small retarder with its axes at $\pm45^\circ$ to
the field ([04 §3.2](04-incident-polarisation.md#32-the-two-mechanisms-differentiated)).
Either way the conversion the software applies is then a *definition* rather
than a measurement of a tensor element.
That is precisely why the reported number is always called
$r_\mathrm{eff}$ (§9).

### 5.5 The honest caveat, stated early

> **The angular scan constrains the *projection* of the tensor onto this
> geometry.** It does not by itself separate $r_{42}$ from $r_{13}$ and
> $r_{33}$, and it does not fix the crystal axes. That needs a texture and
> domain model plus independent structural information such as XRD
> [[26]](../references.md#ref-26). The identifiability argument is made
> explicitly in
> [04 §6](04-incident-polarisation.md#6-why-an-angular-scan-alone-is-degenerate).

---

## 6. From field to retardation

### 6.1 The index change, from the perturbed indicatrix

The tensor is defined on $B = 1/n^2$, but what accumulates phase is $n$, so
the two have to be connected. Along a principal direction where the
unperturbed value is $B = 1/n^2$ and the perturbation is $\Delta B$, the new
index is

$$
n(E) = \left(B + \Delta B\right)^{-1/2}
= n\left(1 + n^2\Delta B\right)^{-1/2}
= n\left(1 - \tfrac{1}{2}n^2\Delta B + \tfrac{3}{8}n^4\Delta B^2 - \dots\right),
\tag{15}
$$

so that

$$
\boxed{\;
\Delta n \;=\; -\tfrac{1}{2}\,n^{3}\,\Delta B
\;=\; -\tfrac{1}{2}\,n^{3}\, r_{\mathrm{eff}}\, E
\;+\; \mathcal{O}\!\left(\Delta B^2\right).
\;}
\tag{16}
$$

The expansion parameter is $n^2\Delta B = n^2 r E$. With $n \approx 2.1$,
$r_\mathrm{eff}$ of order $10^2$ pm/V and $E$ of order $10^{-1}$ V/µm, that is
of order $4\times10^{-5}$: the linear term of (16) is accurate to parts in
$10^5$, and the quadratic correction is far below every other uncertainty in
the chain. The linearisation is not the weak step anywhere in this
measurement.

### 6.2 Accumulated retardation

Accumulated over an interaction length $t$, here the BTO film thickness
because the light passes through the film once, the index difference between
the two eigenpolarisations produces an optical phase difference:

$$
\boxed{\;
\Gamma \;=\; \frac{2\pi}{\lambda}\,\Delta n \, t
\;=\; \frac{\pi\, n^{3}\, r_{\mathrm{eff}}\, E\, t}{\lambda} .
\;}
\tag{17}
$$

Two features of (17) drive every design decision downstream.

- **$\Gamma \propto E$, linearly.** Double the drive voltage, double the
  retardation. This is the single most useful diagnostic available, and it is
  enforced as a gate rather than assumed.
- **$\Gamma \propto n^3 r_\mathrm{eff} / \lambda$.** The $n^3$ is why
  high-index materials are attractive. The explicit $\lambda$ is why the
  wavelength must be known and fixed, 1550 nm here, both because BaTiO₃ is
  transparent there and because it is the band a real modulator would work in,
  so the number transfers
  ([`../experiment/beamline.md`](../experiment/beamline.md)).

In the compensated Sénarmont geometry used here, the retardation and the
measured polarisation rotation are related by $\Gamma = 2\delta$. That factor
of two, and the exact conditions under which it holds, are derived in
[03 §11](03-senarmont-readout.md#11-the-derivative-aligned-rotation).

---

## 7. Half-wave voltage and the figures of merit

The standard way to quote an electro-optic device is the voltage that produces
a half-wave of retardance. Setting $\Gamma = \pi$ in (17) with $E = V/g$ over
an interaction length $L$ gives

$$
V_\pi \;=\; \frac{\lambda\, g}{n^{3}\, r_\mathrm{eff}\, L},
\qquad
\boxed{\;V_\pi L \;=\; \frac{\lambda\, g}{n^{3}\, r_\mathrm{eff}}\;}
\tag{18}
$$

The product $V_\pi L$ is the standard figure of merit for a transverse
modulator, because it is independent of length and so compares materials and
electrode geometries rather than devices [[4]](../references.md#ref-4). The
material figure of merit inside it is $n^3 r_\mathrm{eff}$, tabulated in §4.

**This instrument is deliberately a terrible modulator.** Here the interaction
length is the film thickness, $L = t$, of order a few hundred nanometres,
while the gap $g$ is of order 7 µm. Putting $\lambda = 1550$ nm, $g = 7$ µm,
$n = 2.1$ and, purely to fix an order of magnitude, $t \approx 300$ nm,
$r_\mathrm{eff} \approx 100$ pm/V and $\alpha \approx 0.9$ (§8) into

$$
V_\pi \;=\; \frac{\lambda\, g}{\alpha\, n^{3}\, r_\mathrm{eff}\, t}
\tag{19}
$$

gives $V_\pi$ of order $4\times10^{4}$ V. The instrument drives 9 Vpp, so it
operates at about $10^{-4}$ of a half-wave, which is exactly the microradian
regime the readout is built for
([03](03-senarmont-readout.md)).

That is a feature, not a limitation. A waveguide device wins back the factor
$L/t$ of order $10^3$ by propagating *along* the film instead of through it,
but it then folds the mode overlap, the propagation loss and the waveguide
design into the answer. Normal incidence through an unpatterned film isolates
the material response from all of that, at the cost of needing a readout
sensitive to microradians.

---

## 8. The field between coplanar electrodes

The chip carries **coplanar**, in-plane electrode pairs with a gap $g$ of
order 7 µm, not a sandwich structure. Three reasons: the film is only a few
hundred nanometres thick, so a vertical field would need a transparent top
contact and a conducting substrate; the in-plane geometry is the one that
couples to the large shear coefficient (§5.3); and it is the geometry real
integrated BTO modulators use [[27]](../references.md#ref-27).

The cost is that the field is **not** $V/g$. It fringes, it is strongly
non-uniform across the gap and with depth, and the optical mode samples a
weighted average of it. The instrument therefore writes

$$
E \;=\; \frac{\alpha\, V_{\mathrm{device}}}{g},
\tag{20}
$$

where $\alpha$ is an electrostatic correction obtained from a finite-element
model of **this** electrode and illuminated-volume geometry. Formally, $\alpha$
is defined by the mode-weighted average

$$
\alpha \;=\; \frac{g}{V}\,
\frac{\displaystyle\int w(\mathbf{r})\, E_\parallel(\mathbf{r})\, \mathrm{d}^3r}
{\displaystyle\int w(\mathbf{r})\,\mathrm{d}^3r},
\tag{21}
$$

with $w(\mathbf{r})$ the normalised optical intensity distribution and
$E_\parallel$ the field component that couples through (9). Written that way
it is obvious why it does not transfer: it depends on the electrode
cross-section, the gap, the film stack *and* the beam profile.

> **Warning: $\alpha$ is not a transferable material constant.** Picavet and
> co-workers used $\alpha = 0.942$ for one specific 10 µm free-space geometry
> [[29]](../references.md#ref-29); quoting that value for a different device is
> a calculation, not a measurement. Without an FEM-derived $\alpha$ for this
> chip the software reports the normalised rotation only and never
> $r_{\mathrm{eff}}$.

Note also the distinction between $V_\mathrm{source}$ and $V_\mathrm{device}$:
at 30 kHz, through cabling and the switching matrix, the voltage actually
appearing across the electrodes is not the voltage the function generator was
told to produce. The ratio $V_\mathrm{device}/V_\mathrm{source}$ is a separate
measured input
([`../experiment/switch-matrix.md`](../experiment/switch-matrix.md)).

---

## 9. Why the answer is always an *effective* coefficient

Even when every input is supplied and every gate passes, the emitted number is
$\lvert r_{\mathrm{eff}} \rvert$: a **geometry-specific effective
coefficient** that folds in everything below.

| Ingredient | Why it enters |
| --- | --- |
| crystal texture | which tensor components project onto this geometry at all (§5.3) |
| domain population and poling history | the response is odd in $P_s$, so partially cancelling domains reduce it |
| tensor projection | the angle between the optical polarisation, the field, and the crystal axes |
| static retardance $\Gamma_0$ of the pixel | it weights the axial and shear contributions differently ([04 §3](04-incident-polarisation.md#3-the-2theta-dependence-derived)) |
| electrode field distribution | $\alpha$, and the mode-weighted average over a non-uniform field, eq. (21) |
| optical geometry | single pass through a thin film, at normal incidence, in the Sénarmont configuration |
| frequency | 30 kHz: domains can follow, so this is the domain-mediated response, not the clamped lattice response [[25]](../references.md#ref-25) |
| temperature | $T_c$, domain structure and the coefficients themselves are temperature-dependent [[24]](../references.md#ref-24) |

It is therefore never quoted as an intrinsic tensor element of BaTiO₃, and it
is never compared directly against a single-crystal literature value such as
[[7]](../references.md#ref-7) without that caveat attached.

What **is** safely comparable is the **normalised rotation** $\delta$ across
pixels of the same chip. The optical and electronic chain is shared, laser
power, coupling, detector gain and analyser placement are divided out by the
Malus normalisation, and $\alpha$, $t$, $g$ and $n$ are common to every pixel.
That is why the compositional observable here is the normalised rotation, not
raw microvolts.

---

## Further reading

| Topic | Start here |
| --- | --- |
| The original systematic treatment | Pockels [[1]](../references.md#ref-1) |
| Tensor symmetry and the Voigt reduction | Nye [[2]](../references.md#ref-2) |
| Indicatrix, induced axes, the $4mm$ tensor | Yariv and Yeh [[3]](../references.md#ref-3) |
| $V_\pi$, $V_\pi L$ and material figures of merit | Yariv and Yeh [[4]](../references.md#ref-4) |
| Why $r$ vanishes under inversion, and the $\chi^{(2)}$ connection | Boyd [[5]](../references.md#ref-5) |
| Why $r \propto P_s$ in a perovskite | DiDomenico and Wemple [[6]](../references.md#ref-6) |
| Measured BaTiO₃ tensor values | Zgonik and co-workers [[7]](../references.md#ref-7) |
| BaTiO₃ on silicon, and what a credible $r_\mathrm{eff}$ report looks like | Abel and co-workers [[27]](../references.md#ref-27) |
| The $\alpha$ correction and the single-point method | Picavet and co-workers [[29]](../references.md#ref-29) |
| Null-slope metrology and the $r_c$ combination | Abel [[31]](../references.md#ref-31) |

Full bibliography: [`../references.md`](../references.md).

---

## Continue

- [02: Polarisation Formalism](02-polarisation.md): the Jones and Stokes
  language used to track what $\Gamma$ does to the beam.
- [03: The Null-Slope Sénarmont Readout](03-senarmont-readout.md): how a
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
