# The Experiment

This section describes the physical instrument: the optics the light passes
through, the instruments that drive and read it, the chip under test, and the
electronics that put a voltage across one electrode pair out of a hundred.
If you want the theory, start with [the physics section](../physics/01-electro-optics.md);
if you want to press "Run", start with [the operator guide](../guide/troubleshooting.md).

---

## 1. What the instrument is

It is a **Sénarmont polarimeter with an automated sample stage**. A 1550 nm
beam is prepared in a known linear polarisation state, focused into the ~7 µm
gap between a pair of coplanar electrodes on a BaTiO₃ thin film, and analysed
after the sample by a quarter-wave plate and a rotating analyser held near
extinction. A voltage applied across that electrode pair changes the
refractive index of the film through the linear electro-optic (Pockels)
effect; that changes the polarisation state of the transmitted beam; the
analyser converts the polarisation change into an intensity change; a
photodiode converts the intensity change into volts.

Everything else in the setup exists to make that measurement **repeatable at
one hundred different places on one chip, unattended, for hours**:

- a motorised XY stage that puts any of the 100 pixels in the beam,
- three motorised rotation mounts (half-wave plate, quarter-wave plate,
  analyser) so no angle is ever set by hand,
- a camera looking down the same optical axis as the beam, so the software can
  see the electrode gap it is trying to hit,
- a 100-channel solid-state switching matrix so exactly one electrode pair is
  ever energised,
- and two parallel readout channels — an oscilloscope used as a slow DC
  voltmeter for the null and the Malus slope, and a lock-in amplifier that
  picks the 30 kHz modulation out of the noise.

---

## 2. The block diagram

![Optical and electrical schematic of the Pockels measurement setup](../../assets/setup_schematic.png)

**Reading the diagram.** Follow the red line — that is the 1550 nm beam.

1. **Laser (right).** The fibre-coupled **KLS1550** diode source emits at
   1550 nm, software-set to ~7 mW. It is mounted horizontally at bench level.
2. **Two turning mirrors.** The beam leaves the laser travelling left, is
   folded upwards by the lower mirror and then left again by the upper mirror,
   which delivers it horizontally into the top of the vertical column. The
   mirrors exist purely to get the beam from a horizontal source into a
   vertical measurement column with a manageable footprint.
3. **Beamsplitter (BS).** The cube at the top of the column folds the laser
   downward into the measurement path. It is also the **camera port**: the
   pale blue cone in the diagram is the imaging path, which travels *up* from
   the chip, through the same focusing lens, and straight through the cube to
   the **camera** at the very top. Beam and camera therefore share an axis —
   which is what makes "move the stage to where the camera sees the gap"
   a meaningful instruction.
4. **Polariser 1.** A fixed linear polariser. Everything upstream of it
   (fibre, mirrors, beamsplitter) can do whatever it likes to the polarisation;
   downstream of it the state is clean, linear, and **defines the zero of every
   angle in the experiment**.
5. **λ/2 WP.** The motorised half-wave plate. Rotating it by Δ rotates the
   incident linear polarisation by 2Δ, which is how the incident angle
   $\theta_i$ is scanned across the 9-point production grid.
6. **Lens.** Focuses the beam into the electrode gap. The spot has to be
   comfortably smaller than the ~7 µm gap, which is what sets the sub-micron
   alignment requirement on the stage.
7. **The chip.** Drawn in the diagram as a film (green) carrying two coplanar
   electrodes (gold), one grounded on the left, one driven from the right. The
   real chip carries 100 such pairs, and the whole die rides on the XY stage.
8. **Lens.** Re-collimates the diverging beam after the focus so the waveplate
   and analyser downstream see a near-parallel beam.
9. **λ/4 WP.** The motorised quarter-wave plate — the Sénarmont compensator.
   It undoes the *static* birefringence of the film so the light arriving at
   the analyser is linear again and can actually be extinguished.
10. **Polariser 2.** The motorised analyser. Its angle relative to the
    compensated null is the measurement variable: at the null it reads
    background, at ±45° it sits on the steepest part of the transmission curve.
11. **Lens → Detector.** A final lens concentrates the beam onto the small
    active area of the **PDA30B2** amplified photodiode.

Now follow the thin black lines — that is the electrical path.

- The **SMU** (top right) sources the DC bias: the poling voltage and the DC
  sweep used for hysteresis loops.
- The **Signal Generator** supplies the 30 kHz AC drive on CH1, and a separate
  0.5 Vpp copy of the same 30 kHz on CH2 that goes to the **Lock-in** as its
  external reference.
- The **bias tee** sums them: the inductor passes DC from the SMU while
  blocking AC, the capacitor passes AC from the generator while blocking DC, so
  a single coaxial line carries `DC + AC` to the chip.
- That single line feeds the **100-channel switching matrix** (not drawn — the
  schematic shows the one electrode pair the matrix has selected). The matrix
  connects the drive line to exactly one of the 100 electrode pairs and leaves
  the other 99 open.
- The detector output fans out to **two** instruments at once: the
  **Oscilloscope**, which reads the mean (DC) level, and the **Lock-in**, which
  demodulates the 30 kHz component against the CH2 reference.

> **Note** — the schematic shows one electrode pair because that is what the
> measurement sees. The mapping from "pixel 46" to "the one relay that must
> close" is the job of the [switching matrix](switch-matrix.md), and getting it
> wrong is the most dangerous failure mode in the system.

---

## 3. The two readout channels

| | Oscilloscope channel | Lock-in channel |
| --- | --- | --- |
| **Instrument** | Tektronix TBS, CH1, USB-VISA | Signal Recovery DSP7230, Ethernet socket |
| **Measures** | mean detector level (DC) | amplitude and phase at 30 kHz |
| **Used for** | null depth, stage alignment, the local Malus slope | the electro-optic signal itself |
| **Typical level** | millivolts to volts | microvolts |
| **Why both** | the DC level calibrates volts-per-radian | the AC channel is where the physics is |

The DC channel is what turns a lock-in voltage into a polarisation rotation:
the lock-in tells you how big the modulation is, the scope tells you how steep
the transmission curve was at the point you measured it. Neither is useful
alone. See [Sénarmont readout](../physics/03-senarmont-readout.md).

---

## 4. What happens at one pixel

A compressed version of the per-pixel loop, to orient you before you read the
detail pages:

1. Move the stage to the nominal pixel position (2.5 mm grid).
2. Rotate the analyser +45° off the null so the detector sees a bright peak.
3. Hill-climb the stage in X and Y to maximise transmission — that peak *is*
   the beam sitting centred in the electrode gap.
4. Rotate the analyser back to the null seed.
5. Route the switching matrix to this pixel's electrode pair.
6. Pole the film with DC from the SMU.
7. For each of 9 half-wave-plate angles: re-null the QWP/analyser pair, then
   measure the lock-in triplet — background at the null, and the exact ±45°
   slope points.
8. All voltages off, matrix open, move to the next pixel.

A production pixel takes about 4.5–5 minutes; a full 83-pixel default
selection takes 6–7 hours.

---

## 5. Where to go next

| Page | What it covers |
| --- | --- |
| [The Optical Beamline](beamline.md) | Every optical element in order: what it is, the physics it performs, why it sits where it does, and what breaks without it. Includes the focusing-into-the-gap argument and the brighten/dim alignment trick. |
| [Instruments and Control Interfaces](instruments.md) | Every instrument and how the software talks to it: the exact SCPI and serial sequences, the ranging and settling rules, the deliberate quirks, the connection map and the safety limits. |
| [The BaTiO₃ Chip](chip.md) | The 10 × 10 array, the coplanar electrode geometry and why it was chosen, the pixel numbering and the display flip, and the full pixel → switch table. |
| [The 100-Channel Switching Matrix](switch-matrix.md) | The Arduino, the cascaded shift registers, the PhotoMOS relays, the wire protocol, the boot handshake, and the triple-copy mapping hazard. |

Related reading elsewhere in the site:
[polarisation basics](../physics/02-polarisation.md) ·
[motion control](../software/motion-control.md) ·
[instrument control](../software/instrument-control.md) ·
[troubleshooting](../guide/troubleshooting.md) ·
[glossary](../reference/glossary.md)

---

<div align="center">

[← Ferroelectric switching](../physics/05-ferroelectrics.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [The optical beamline →](beamline.md)

</div>
