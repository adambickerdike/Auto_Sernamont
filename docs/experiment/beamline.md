# The Optical Beamline

Every optical element between the laser and the photodiode, in the order the
light meets them. For each one: what it physically is, what it does to the
light, why it sits at that point in the chain, what would go wrong if it were
missing, and how the software drives it when it is motorised.

The companion pages are [Instruments](instruments.md) for the control
interfaces and [polarisation](../physics/02-polarisation.md) for the Jones
algebra behind the statements made here.

---

## 0. The chain at a glance

```text
                                            ┌── camera (imaging path, shares the axis)
                                            │
laser ─► mirror ─► mirror ─► beamsplitter ──┴─► polariser ─► HWP ─► lens ─┐
 1550 nm                       (camera port)     defines      sets θᵢ    focus
 ~7 mW                                           the frame                │
                                                                          ▼
detector ◄─ lens ◄─ analyser ◄─ QWP ◄─ lens ◄──────────────────── BTO chip
 PDA30B2   focus    reads ψ    compensates  re-collimates        ~7 µm gap
```

The polarisation state of the beam changes character three times along this
path: it is *arbitrary* out of the fibre, *linear* after the polariser, made
*elliptical* by the sample's static birefringence, and returned to *linear* by
the quarter-wave plate so the analyser can extinguish it. The figure below
shows the polarisation ellipse at each of those points.

![Polarisation ellipse at each point along the beamline](../../assets/figures/polarisation_ellipses.png)

---

## 1. The laser: Thorlabs KLS1550

**What it is.** A fibre-coupled 1550 nm diode laser in a Thorlabs KCube laser
source, driven over USB through the Kinesis `.NET` API
(`Thorlabs.MotionControl.KCube.LaserSourceCLI`, loaded with `pythonnet`). The
wrapper is [`pockels/kls1550.py`](../../pockels/kls1550.py); the device is found
by scanning the Kinesis device list for serial-number prefix 56, so the
software does not need the serial hard-coded.

**Physics.** A telecom-band diode emitting a single transverse mode into
single-mode fibre. What reaches the bench is a clean Gaussian spatial mode with
an **undefined polarisation state** that drifts with fibre stress and
temperature.

**Why 1550 nm.** It is the telecom C-band, so the measured $r_\mathrm{eff}$ is
the number a real BaTiO₃ modulator would be judged on; BaTiO₃ is transparent
there, so the beam is not heating the film or generating photocarriers that
would screen the applied field; and the wavelength enters the retardance
$\Gamma = \pi n^3 r_\mathrm{eff} E t / \lambda$ directly, so it must be known
and fixed rather than approximate.

**Why ~7 mW.** High enough for good shot-noise-limited signal-to-noise, low
enough to stay clear of photo-induced screening and detector saturation, and
set **in software** (default `--laser-power-mw 7.0`) so two runs taken a month
apart are comparable. Optical power is a recorded run parameter, not a knob.

**Without it / if it drifts.** Every reported optical power scales with it, and
the null depth in millivolts, the acceptance gate for the whole measurement,
moves with it. Emission is switched off by the worker's cleanup path on both
normal exit and safe stop.

---

## 2. The turning mirrors

**What they are.** Two flat mirrors on kinematic mounts at roughly 45°,
unrecorded make and model. The first takes the horizontal beam leaving the
laser and sends it upward; the second sends it horizontally into the top of the
vertical measurement column.

**Physics.** Specular reflection, plus a detail that matters here: at non-normal
incidence a mirror does *not* treat s- and p-polarised components identically
(both the amplitude reflectance and the reflection phase differ), so a linear
state incident at 45° generally emerges slightly elliptical and rotated, and
two mirrors compound the effect.

**Why here.** Purely geometric: the laser is a horizontal bench-mounted source
and the measurement column is vertical, so the chip can lie flat on the stage
and the camera can look straight down at it. It does not matter that the
mirrors disturb the polarisation, because they sit **before** the polariser,
which is exactly why the polariser is not placed at the laser output.

**Without them.** The laser would have to sit physically above the column,
aimed down: mechanically awkward and impossible to align without moving the
source.

---

## 3. The beamsplitter and the alignment camera

**What it is.** A cube beamsplitter at the top of the vertical column, plus a
USB camera above it. Make and model are not recorded in the repository; the
camera is opened by OpenCV (`cv2`), trying the DirectShow → MSMF → default
backends on device indices 0, 1 and 2 in turn.

**Physics.** The cube's internal dielectric interface splits amplitude: part of
the incoming laser is folded down the column, while light coming back *up* from
the chip passes straight through to the camera, so the two paths are co-axial.
A cube splitter is generally polarisation-sensitive, which is one more reason
for the polariser to be downstream of it.

**Why here: this is the important part.** The camera and the measurement beam
**share the same optical axis and the same focusing lens**. That means what the
camera sees in focus is what the beam illuminates. The software exploits this:

- `detect_gap_centroid()` / `cv_estimate_gap_position()` run a matched filter
  tuned to the expected 7 µm gap width (`CV_GAP_WIDTH_UM = 7.0`) to find the
  electrode gap in the image;
- the stored camera→stage transform (`pixel_stage_calib.json`, produced once by
  `calibrate_pixel_to_stage`) converts an image position into a stage move;
- the stage jumps close to the gap before the (slower) optical search begins.
  A proposed correction larger than `CV_MAX_CORRECTION_MM = 0.5 mm` is rejected
  as implausible rather than obeyed.

The camera also records an arrival image and a post-alignment image for every
pixel, which is what lets you go back afterwards and see *why* a pixel failed.

**Without it.** The beam still reaches the chip; the camera is genuinely
optional (`--no-camera`) and alignment then relies purely on climbing the
optical signal. What you lose is the fast initial jump and the visual record.
The cube's cost is a fixed fraction of the laser power lost in each direction,
which is a constant and cancels in every normalised quantity.

---

## 4. Polariser 1: the reference frame

**What it is.** A fixed linear polariser, not motorised, no electronics. In the
current lab mapping its transmission axis is taken to be parallel to lab **y**
(see [`pockels/polarisation_lab_mapping_current.json`](../../pockels/polarisation_lab_mapping_current.json),
field `physical_reference`).

**Physics.** It projects the incoming field onto one axis: whatever drifting
elliptical state the fibre and the mirrors delivered, what leaves is cleanly
linear along the transmission axis, at the cost of the discarded orthogonal
component.

**Why here, and why it is arguably the most important passive element.** It
**cleans the state** (everything upstream scrambles polarisation and drifts
over minutes to hours, whereas downstream the input state is a constant of the
experiment), and it **defines the zero of every angle**. The HWP, QWP and
analyser angles are all expressed relative to this axis, which is why the
arbitrary mechanical home position of each Elliptec rotator does not matter:
`tare()` gives a repeatable *software* zero and the optical calibration refers
everything to the polariser frame, so the encoder offsets cancel.

**Without it.** There is no $\theta_i$. The "incident polarisation angle" that
the entire angular analysis is built on would be undefined and time-varying,
and the null you found ten minutes ago would no longer be a null.

---

## 5. The half-wave plate: setting θᵢ

**What it is.** A half-wave retarder at 1550 nm in a **Thorlabs Elliptec ELL14**
motorised rotation mount: **COM5, bus address 1**.

**Physics.** A birefringent plate of thickness giving exactly half a wave of
retardance, $\delta = \pi$. In Jones terms it reflects the polarisation vector
about its own fast axis. The consequence used here is that rotating the plate
by $\Delta$ rotates the *polarisation* by $2\Delta$, with no change of
intensity and no ellipticity introduced:

$$
\theta_i \approx 2\,\theta_\mathrm{HWP} + \text{offset}.
$$

The current lab mapping makes this concrete:
$\theta_i = \mathrm{wrap}_{180}\!\left(90 + 2\,(H_\mathrm{raw} - 11.9607)\right)$
degrees.

**Why it is here.** The Pockels response of this geometry depends on the angle
between the optical polarisation and the applied in-plane field, because the
coefficient being probed, $r_{42}$, is a *shear* term. Scanning $\theta_i$ finds
the angle of strongest response and, more important for a viva, proves that
what you are measuring has the angular signature of a linear electro-optic
effect rather than a thermal or electrostrictive artefact. See
[electro-optics](../physics/01-electro-optics.md).

**How the software drives it.** Production runs use a 9-point grid spaced
22.5° in $\theta_i$ (only 11.25° of motor rotation per step, because of the
factor of two), centred on the manually verified high-response setpoint
`DEFAULT_HWP_GRID_CENTER_RAW_DEG = 7.8951` raw, which the current mapping puts
at $\theta_i = 81.8688°$ in the lab frame. Moves use the verified fast path or
the chunked accurate path; see [motion control](../software/motion-control.md).

**Without it.** One incident polarisation, no angular data, no four-lobed
magnitude, no signed 2θ fit, and no way to distinguish a Pockels signal from
anything else that happens to modulate at 30 kHz.

---

## 6. The focusing lens, and why the beam must go into the gap

**What it is.** A lens focusing the collimated beam down into the electrode
gap; it is also the objective for the camera's imaging path. Make and model are
not recorded in the repository.

**Physics.** The applied field only exists *between* the electrodes. Light that
misses the gap either hits metal (and is lost) or passes through unbiased film
(and contributes transmission but no signal, diluting the modulation depth).
So the beam has to be small compared with the gap and centred in it.

**What that costs you in depth of focus.** A Gaussian waist $w_0$ has a Rayleigh
range $z_R = \pi w_0^2/\lambda$. At 1550 nm, a 2 µm waist gives
$z_R \approx 8$ µm and a 3 µm waist gives $z_R \approx 18$ µm. Focusing tightly
enough to fit inside a ~7 µm gap therefore buys you a depth of focus measured in
tens of microns, which is why the chip has to sit flat and why focus is a
per-chip setup step rather than something the software adjusts.

**What it implies for alignment tolerance.** The gap is ~7 µm wide, and the
stage grid pitch is 2.5 mm. A nominal grid position is *not* good enough: the
chip is never mounted perfectly square, the die is never exactly on-pitch, and
compositional drift across the chip shifts the best spot pixel to pixel. The
software therefore searches optically at **every** pixel. The numbers that
follow from the gap width:

| Quantity | Value | Why |
| --- | --- | --- |
| Stage position tolerance | 5 µm | ~⅔ of a gap width, the coarsest error that still lands inside |
| Backlash compensation | 20 µm, always approach from the same side | mechanical hysteresis is several times the tolerance |
| Settle criterion | stable within 0.5 µm for 0.5 s | a vibrating stage smears the profile |
| Fine align step | 0.5 µm (fine scan), parabolic refinement below 1 µm | resolves the peak, not just the plateau |
| Hill-climb give-up range | ±200 µm | beyond that you are not near this gap at all |

---

## 7. The chip

Covered in full on [its own page](chip.md). Optically, what matters here is
that the beam waist sits in the plane of the film, inside a ~7 µm in-plane gap
between two coplanar electrodes, and that the film is **statically
birefringent**, which is the entire reason for the next two elements.

---

## 8. The collection lens

**What it is.** A lens after the sample that re-collimates the strongly
diverging beam emerging from the focus. Make and model not recorded.

**Physics and why it is here.** A beam focused tightly enough to fit in a 7 µm
gap diverges fast, and sending that cone straight into a waveplate and a
polariser would be a mistake. **Retardance depends on angle of incidence**: a
quarter-wave plate is quarter-wave *at normal incidence*, so a diverging beam
presents a spread of angles, different rays see slightly different retardance,
and the compensated state is only approximately linear no matter how carefully
you turn the QWP. **Polariser extinction likewise degrades off-axis**: the
deep null the whole measurement depends on is an on-axis, collimated-beam
property. Re-collimating first lets the QWP and analyser work in the regime
they are specified for, so the null can get genuinely deep: the acceptance gate
is **14.5 mV** at the detector with a 1.5 mV continuation margin.

**Without it.** You would never reach an acceptable null, and since the whole
sensitivity of null-slope detection comes from operating near extinction
([Sénarmont readout](../physics/03-senarmont-readout.md)), you would lose most
of the signal-to-noise for no good reason.

---

## 9. The quarter-wave plate: the Sénarmont compensator

**What it is.** A quarter-wave retarder at 1550 nm in an **ELL14** mount:
**COM4, bus address 2**.

**Physics.** $\delta = \pi/2$. It converts linear polarisation into elliptical
and, run backwards, converts a particular ellipse back into linear. Turned to
the right angle it takes the elliptical state emerging from the sample and
returns it to a *linear* state at some angle, which a linear analyser can then
extinguish completely.

**Why it is here: the crucial point.** The BaTiO₃ film is birefringent with
**no field applied**: thickness, composition, strain and domain configuration
all contribute, so light leaves the sample elliptical before a single volt has
been applied. An elliptical state **cannot** be extinguished by a linear
analyser; there is leakage at every analyser angle. Without compensation there
is no deep null, and without a deep null the measurement loses most of its
sensitivity, because the signal-to-background ratio near extinction is what
buys the microvolt-level detection. QWP plus analyser near extinction is the
classic **Sénarmont** configuration.

**Why it cannot be set once.** The static birefringence differs from pixel to
pixel *and* with incident polarisation, so the correct QWP angle is not one
number. The software stores a `(q_null, a_null)` pair for **every HWP angle**
and re-checks it at **every pixel**. That is what the per-HWP re-null in the
measurement loop is doing.

**Without it.** Shallow nulls, a large and pixel-dependent background, and an
electro-optic signal sitting on top of a leakage term that varies with exactly
the same variables you are trying to study.

---

## 10. Polariser 2: the analyser

**What it is.** A second linear polariser on an **ELL14** rotator: **COM8, bus
address 2**. Note that the QWP and the analyser share bus address 2 but sit on
*different* COM ports, so rewiring the Elliptec bus means updating both the
port and the address.

**Physics.** Malus' law. With the compensated state linear, the transmitted
intensity as a function of the analyser offset $\psi$ from the null follows
$T \propto \sin^2\psi$. The derivative, $\mathrm{d}T/\mathrm{d}\psi \propto
\sin 2\psi$, is what converts a small polarisation rotation into a measurable
intensity change.

**The three working points:**

| $\psi$ | Transmission | What it is used for |
| --- | --- | --- |
| 0° (the null) | minimum | The electro-optic signal vanishes here, so this reading measures **everything that is not signal**: electrical pickup, laser amplitude modulation, detector artefacts. It is the background row of the triplet. |
| ±45° | half of maximum | **Maximum slope**, peak sensitivity to a polarisation rotation. The two signs give responses of **opposite sign**, which is the strongest evidence that the signal is a real polarisation rotation and not an intensity artefact. |
| 90° | maximum | Full brightness. Used as a fourth fitted point on the calibration pixel, and (at +45°) to brighten the beam for stage alignment. |

Each measurement point is a **triplet**: local null background, exact +45°, and
exact −45°. See [the analyser response](../physics/03-senarmont-readout.md).

**Without it.** No polarisation-to-intensity conversion at all; the photodiode
is blind to polarisation. The analyser *is* the detector, in the sense that it
is where the physics becomes an electrical signal.

---

## 11. The brighten / dim sequence

This is the one place where the analyser is used for something other than
reading out physics, and it is worth understanding because it is the step most
likely to confuse someone watching the run.

**The problem.** At every pixel the stage must be positioned so the beam is
centred in the electrode gap, and the only position-dependent quantity
available is the transmitted power. But the measurement operating point is the
**null**, where transmitted power is minimised and dominated by leakage and
noise: hill-climbing a near-zero signal whose position dependence is buried in
noise does not work.

**The fix.** Rotate the analyser off the null before aligning:

```text
1. [BRIGHTEN]  ANL  a_null  ->  a_null + 45°     (--stage-brighten-offset, default 45)
2. hill-climb the stage in X and Y on the now-bright transmission peak
     hill-climb -> fast-peak -> golden-section -> full line scan  (each a fallback)
3. capture the calibrated position, images and intensity
4. [DIM]       ANL  a_null + 45°  ->  a_null     (back to the near-null seed)
5. per-HWP fast re-null starts from that seed and only has to make a small
   correction
```

At $\psi = 45°$ the transmission is half its maximum, which is plenty of light
to climb, and the peak in transmitted power versus stage position genuinely
marks the beam sitting in the gap, because that is where the least light is
clipped by the electrodes. Returning to the null seed before the per-HWP
re-null matters too: the re-null is a *local* optimiser, and starting it 45°
off null would make it do far more work, and far more travel, than necessary.

> **Note** The fast-map GUI and CLI default to **+45°**
> (`--stage-brighten-offset 45`). The campaign script
> [`pockels/pockels_campaign.py`](../../pockels/pockels_campaign.py) uses
> **+30°** for the same purpose, on the grounds that it still gives a large
> bright signal while cutting the per-pixel analyser travel to 60° round trip.
> Both are recorded in the run metadata; neither changes the physics, because
> the analyser is returned to the null before anything is measured.

> **Warning** If the brighten move fails, the code logs the failure and
> aligns at the current analyser angle rather than aborting. A run whose log is
> full of `[WARN] brighten failed` has been aligning on a near-null signal, and
> its stage positions should be treated as suspect.

---

## 12. The final focusing lens

**What it is.** A lens between the analyser and the detector. Make and model
not recorded.

**Physics and why it is here.** The PDA30B2's active area is small. The lens
concentrates the whole collimated beam onto it, so the measured power is the
transmitted power, the reading is insensitive to small beam-pointing changes,
and the spot stays well inside the active area.

**Without it.** Part of the beam misses the diode, and *how much* misses
depends on alignment, so a stage move or a waveplate rotation that slightly
steers the beam would look like an intensity change, indistinguishable at the
detector from the polarisation change you are trying to measure.

---

## 13. The detector: Thorlabs PDA30B2

**What it is.** An amplified photodiode with switchable transimpedance gain
(0 to 70 dB in 10 dB steps). Photons → photocurrent → voltage.

| Parameter | Value |
| --- | --- |
| Responsivity $\mathcal{R}$ at 1550 nm | 0.875 A/W |
| Gain setting assumed by the software | **10 dB** into a **Hi-Z** load → $G = 4.75\times10^3$ V/A |
| Conversion | $P[\mathrm{W}] = V / (\mathcal{R}G)$, i.e. `V_TO_W = 1/(0.875 × 4.75e3)` |
| Output goes to | oscilloscope CH1 **and** the lock-in input, simultaneously |

**Why both instruments at once.** The DC level and the 30 kHz modulation are
two components of the *same* signal. Splitting them between a scope that
averages many cycles for the mean and a lock-in that rejects everything except
the reference frequency means neither measurement compromises the other, and
the normalisation (lock-in volts per Malus slope) uses the same photocurrent
that produced the signal.

> **Warning** The gain is a mechanical switch on the housing. If anybody
> moves it, **every optical power in every output file is wrong** by that
> factor. The full gain table lives in `PDA_GAIN_TABLE`; if the hardware really
> changes, update `PDA_GAIN_DB` and `PDA_LOAD` and note it in the run log.

---

## 14. Summary table

| # | Element | Motorised? | Interface | One-line role |
| --- | --- | --- | --- | --- |
| 1 | KLS1550 laser | power only | Kinesis .NET | 1550 nm, ~7 mW, reproducible |
| 2 | Turning mirrors ×2 | no | none | fold horizontal source into vertical column |
| 3 | Beamsplitter + camera | no | OpenCV (camera) | co-axial imaging port for CV-guided alignment |
| 4 | Polariser 1 | no | none | cleans the state, defines the angle zero |
| 5 | Half-wave plate | **yes** | ELL14, COM5 addr 1 | sets $\theta_i$; 2× the motor angle |
| 6 | Focusing lens | no | none | beam into the ~7 µm gap |
| 7 | BTO chip | **on XY stage** | KCubeStepper ×2 | the sample |
| 8 | Collection lens | no | none | re-collimate for the waveplate and analyser |
| 9 | Quarter-wave plate | **yes** | ELL14, COM4 addr 2 | Sénarmont compensator; kills static ellipticity |
| 10 | Analyser | **yes** | ELL14, COM8 addr 2 | polarisation → intensity; null and ±45° |
| 11 | Focusing lens | no | none | all the light onto the diode |
| 12 | PDA30B2 detector | gain switch | BNC → scope + lock-in | intensity → volts |

Continue to [Instruments and Control Interfaces](instruments.md), or step back
to [the physics of the readout](../physics/03-senarmont-readout.md).

---

<div align="center">

[← The experiment](index.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Instruments →](instruments.md)

</div>
