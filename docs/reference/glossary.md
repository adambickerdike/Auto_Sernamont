# Glossary

Every symbol, term and abbreviation used anywhere in this documentation, in one
place. If a page uses a piece of notation without defining it, it is defined
here.

For the files and columns those symbols end up in, see
[Data Schema](data-schema.md); for the flags that set them, see
[CLI Reference](cli.md).

---

## 1. Symbols

Ordered roughly from the beam line inwards: optical angles first, then the
electro-optic response, the device geometry, the lock-in quantities, and finally
the hysteresis and fit quantities.

| Symbol | Meaning | Units |
| --- | --- | --- |
| $\theta_i$ | incident polarisation angle, calibrated lab frame | ° |
| $\theta_\mathrm{HWP}$ | raw half-wave-plate motor angle; $\theta_i \approx 2\theta_\mathrm{HWP} + \text{offset}$ | ° |
| $\theta_i^\mathrm{legacy}$ | the older 2 × HWP convention, retained so historical runs stay comparable | ° |
| $\theta_\mathrm{ana}$ | analyser angle expressed in the calibrated analyser frame | ° |
| $\psi$ | analyser offset **from the null**, the readout variable | ° |
| $\gamma_0$ | QWP fast-axis zero in the polarisation frame | ° |
| $s_H$, $s_Q$, $s_A$ | sign conventions relating each rotator's raw encoder direction to the lab frame | dimensionless |
| `q_null`, `a_null` | QWP and analyser angles that extinguish the beam | ° |
| `p_null` | residual optical power at the null | W (also reported in mV) |
| $V_\mathrm{null}$ | the DC detector level at the null, the second point of the Malus slope | V |
| $\mathrm{d}I/\mathrm{d}\psi$ | Malus slope; maximal at $\psi = \pm 45°$, which is why the readout sits there | W/rad |
| $\delta$ | electro-optic polarisation rotation (complex, RMS) | rad |
| $\delta_\mathrm{rms}$ | the RMS magnitude of that rotation | rad |
| $\delta\psi$ | the operating-angle shift derived from the joint AC/DC fit | rad |
| $\Gamma$ | field-induced retardance; $\Gamma = 2\delta$ in this geometry | rad |
| $r_{ij}$ | electro-optic tensor element, Voigt notation | pm/V |
| $r_\mathrm{eff}$ | geometry-specific **effective** coefficient | pm/V |
| $n$ | refractive index (default 2.1 for BTO at 1550 nm) | dimensionless |
| $t$ | film thickness = optical interaction length | nm |
| $g$ | electrode gap | µm |
| $\alpha$ | FEM electrostatic field correction, $E = \alpha V/g$ | dimensionless |
| $\nu$ | domain-state factor in the null-slope extraction: 0.5 for an unpoled-equivalent state, 1.0 for a fully poled film | dimensionless |
| $E$ | in-plane electric field | V/m |
| $\lambda$ | wavelength (1550 nm) | nm |
| $I_\mathrm{min}$, $I_\mathrm{max}$ | extremes of a transmission sweep; QWP retardance follows from $\delta = 2\arccos\sqrt{I_\mathrm{min}/I_\mathrm{max}}$ | W or V |
| $X$, $Y$ | lock-in in-phase and quadrature outputs | V |
| $\|R\|$, $\phi$ | lock-in magnitude and phase | V, ° |
| $Z(\psi)$ | complex first-harmonic response versus analyser offset | V |
| $P$ | analyser-independent modulation term in $Z(\psi)$ | V |
| $E_1$, $E_2$ | complex $\sin 2\psi$ and $\cos 2\psi$ coefficients of $Z(\psi)$ | V |
| $D(\psi)$ | DC Malus fringe, $C_0 + C_c\cos 2\psi + C_s\sin 2\psi$ | V |
| $C_0$, $C_c$, $C_s$ | the DC Malus fringe coefficients | V |
| $A_\mathrm{opt}$ | Malus amplitude, $(V_\mathrm{dc}(\psi) - V_\mathrm{null})/\sin^2\psi$ | V |
| $S(V)$ | signed hysteresis loop, the phasor projected on the saturation axis | V |
| $Q(V)$ | quadrature residual of that projection | V |
| $V_c^{\pm}$ | coercive voltages | V |
| $E_c$ | coercive field, $\alpha V_c/g$ (1 V/µm = 10 kV/cm) | kV/cm |
| $S_\mathrm{rem}$, $S_\mathrm{sat}$ | remanent and saturated response | V |
| $S_s$, $w$ | saturation amplitude and width of the $\tanh$ branch fit $S = a + bV + S_s\tanh((V-V_c)/w)$ | V, V |
| $a$, $b$ | offset and reversible linear term of that fit | V, V/V |
| $h$ | peak height of $\|dS/dV\|$ on a branch | V/V |
| $\sigma$ | width of the switching-field distribution (`switching_sigma_V`) | V |
| $G$ | leakage conductance of a pixel | S |
| $\tau$, $\beta$ | poling time constant and stretching exponent | s, dimensionless |
| $P_s$ | spontaneous polarisation | C/m² |
| $T_c$ | Curie temperature | °C |
| $f_\mathrm{mod}$ | modulation frequency (30 kHz) | Hz |
| TC | lock-in time constant (500 ms at index 14) | s |
| $G_\mathrm{AC}/G_\mathrm{DC}$ | detector AC-to-DC transfer ratio | dimensionless |
| $V_\mathrm{pp}$ | peak-to-peak drive amplitude | V |
| $V_\mathrm{rms}$ | $V_\mathrm{pp}/(2\sqrt2)$ for a zero-offset sine | V |
| $V_\mathrm{max}$ | symmetric hysteresis endpoint (40 V hard ceiling) | V |
| $V_\mathrm{device}/V_\mathrm{source}$ | measured volts across the electrodes ÷ programmed generator Vpp, at $f_\mathrm{mod}$, with the device connected | dimensionless |
| $I(V)$ | DC-only leakage current versus bias, measured with the AC probe off | A |
| $\mathcal{R}$ | detector responsivity (0.875 A/W at 1550 nm for the PDA30B2) | A/W |
| $Z_t$ | detector transimpedance (4.75 × 10³ V/A at 10 dB gain into Hi-Z) | V/A |
| $N_\mathrm{enc}$ | rotator encoder resolution: 143 360 counts/rev = 398.22 counts/deg | counts/° |
| $f_\mathrm{sat}$ | saturation-tail fraction used to define the tails (`--sat-fraction`, default 0.8) | dimensionless |
| $R^2$ | coefficient of determination of a fit | dimensionless |
| SEM | standard error of the mean, reported alongside every fitted slope | (as the quantity) |
| $\rho$, $p$ | Spearman rank correlation coefficient and its p-value | dimensionless |

---

## 2. Terms

The project's working vocabulary, alphabetically. Several of these words are
used elsewhere in optics or ferroelectrics with a slightly different sense.
Where that is so, the entry says what it means *here*.

| Term | Meaning |
| --- | --- |
| **Adaptive poling** | watching the lock-in during the poling dwell and stopping at the plateau instead of always sleeping the full cap (60 s floor, 180 s cap, 3 quiet intervals under 2 %, ≥ 2 µV signal) |
| **Adaptive re-null** | the default re-null policy: re-optimise QWP/analyser only when the inherited seed leaks. Contrast *seed-only* (never) and *fast-renull* (always) |
| **Analyser** | the output polariser, on a motorised rotator |
| **Analyser-independent term** | the part of the lock-in response that does not vary with analyser angle. Often electrical pickup, but also total-transmission modulation, laser AM, or detector terms, hence the deliberately neutral name |
| **Backlash compensation** | always approaching a target from the same direction so gear play is loaded identically |
| **Bring-up sequence** | the fixed order in which instruments are connected at the start of a run; the teardown reverses it |
| **Brighten** | rotating the analyser off the null so the stage alignment has a bright peak to climb |
| **Butterfly** | the raw $\|R\|$ versus $V_\mathrm{dc}$ curve; the magnitude of the signed loop |
| **Calibration pixel** | the first measured pixel, which does the expensive full per-HWP calibration that later pixels inherit |
| **Centre-dense grid** | the default 45-point hysteresis voltage grid, with levels packed near 0 V where the coercive behaviour is |
| **Chip map** | see *Fast map* |
| **Chip summary** | `fast_map_all_pixels.csv`, one row per pixel; the file to open first |
| **Compensated null branch** | the QWP/analyser pair that extinguishes the light *through the sample*, absorbing its static birefringence |
| **Compliance** | the SMU's current limit (1 mA). A point whose compliance tripped is excluded from every loop metric |
| **Domain reset (depoling)** | the bipolar decaying-amplitude train that randomises the domain state before a virgin-curve measurement |
| **Dwell** | the DC-only hold at each hysteresis voltage step before the AC probe turns on. Sets the loop shape; keep it fixed across a campaign |
| **Extinction ratio** | the bright-to-null transmission ratio; above 25 dB is a healthy null |
| **Fast map** | the screening measurement: 9 HWP × triplet × 1 Vpp per pixel |
| **Frozen** | see *Switchable / frozen* |
| **Gate** | a numerical acceptance threshold that decides whether a derived quantity is emitted at all. A failed gate is recorded by name, not silently ignored |
| **Golden-section search** | the third fallback in the stage alignment chain, used when the hill-climb and fast-peak searches fail |
| **Grid preset** | a stored HWP grid definition; the production default is 9 points at 22.5° spacing in $\theta_i$ |
| **Headless** | running the worker without the GUI, via `--cli` |
| **Hill-climb** | the first-choice stage alignment search; the fallback chain continues fast-peak → golden-section → line scan |
| **Historical follow-up** | adding hysteresis, an AC sweep or an analyser sweep to a run that finished earlier, using that run's saved peak conditions |
| **Imprint** | a hysteresis loop shifted off zero, indicating an internal bias field |
| **Interlock ordering** | the fixed AC/DC/matrix state sequence that guarantees a pixel is routed before any voltage appears, and that all outputs are off before the matrix changes channel |
| **Lab frame** | the calibrated polarisation reference frame in which $\theta_i$ is quoted, as opposed to raw motor degrees |
| **Learned readout** | the per-HWP readout settings inherited from the calibration pixel |
| **Locked-QWP tracking** | the production readout policy: keep the QWP on the compensating null and use only the exact ±45° analyser slopes |
| **Loop closure** | how far the loop fails to return to its starting value at $+V_\mathrm{max}$; a drift and repeatability check |
| **Malus fringe** | the DC transmission versus analyser angle, $D(\psi)$; the operating angle is taken from it |
| **Manual probing** | a mode where the stage aligns each pixel and then pauses so you can land probe needles by hand; Arduino routing is disabled |
| **Normalised rotation** | the Malus-normalised, dimensionless polarisation rotation. The correct observable for comparing pixels, days and setups |
| **Null** | the QWP/analyser combination giving maximum extinction |
| **Null leakage** | residual DC detector signal at the null. **Optical**, not electrical leakage current |
| **Operating point** | the analyser position at which the readout is taken, chosen from the DC Malus fringe, never from the AC extremum |
| **Overload** | the lock-in reporting that a signal exceeded its range. An *input* overload means too much total signal at the front end and cannot be fixed by changing sensitivity; fix the null instead |
| **Peak lock table** | the per-pixel record of the learned per-HWP readout: nulls, readout angles, chosen side, and the certificate fields |
| **Poling kinetics** | the lock-in magnitude sampled every 10 s during the poling dwell, fitted to a stretched exponential to give $\tau$ and $\beta$ |
| **Production pixel** | any pixel after the calibration pixel; it inherits the seeds and runs the fast triplet readout (≈ 4.5 to 5 min) |
| **Provenance header** | the leading `#` comment lines of `dc_hysteresis.csv`, recording every condition the loop was taken under |
| **Overtravel** | deliberately jogging the stage past the nominal 0 to 25 mm limit; the expected exception is suppressed and the target is never clamped |
| **Pickup** | electrical crosstalk from the drive into the detection chain |
| **Pinched loop** | a constricted loop, caused by defect pinning, internal-bias pairs, or antiferroelectric-like behaviour |
| **Poling** | applying a DC bias to align ferroelectric domains |
| **Quadrature fraction** | $\max\|Q\|/\max\|S\|$; above 0.5 the signed projection is meaningless and the loop is `invalid_projection` |
| **Quality flag** | a machine-written note that something about a row is suspect. Quality problems become flags, not exceptions, so the run continues and you decide later |
| **Re-null** | re-optimising QWP/analyser at a pixel because the inherited seed leaks |
| **Resume** | restarting an interrupted run against its existing folder; completed pixels are skipped only when every requested artifact verifies as complete |
| **Run folder** | one timestamped directory per run, holding every file that run produced. Git-ignored; back it up yourself |
| **S9 certificate** | the operating-point certification: two independent models (complex AC, real DC Malus) fitted to the same points and required to agree |
| **Safe stop** | the polite stop: the worker finishes its point, turns AC off, ramps the SMU down, opens the matrix and saves |
| **Seed null** | the `(q_null, a_null)` pair inherited from the substrate table or the calibration pixel, used as the starting point at a new pixel |
| **Sensitivity index** | the DSP7230's discrete voltage-range setting. Index 16 = 200 µV RMS full scale, and it is deliberately held **fixed** for a whole map so every point shares one calibration |
| **Sénarmont** | compensator plus analyser near extinction; the classic small-retardance measurement geometry |
| **Shakedown** | a deliberate single-pixel run, done before every campaign, to prove the whole chain end to end |
| **Slope point** | analyser at ±45° from the null, where $\|dI/d\psi\|$ is maximal |
| **Squareness** | $S_\mathrm{rem}/S_\mathrm{sat}$; how "square" a loop is |
| **Stage calibration** | the optional separate pass that records each pixel's transmission-peak motor coordinates |
| **Substrate calibration** | the per-HWP null seed table |
| **Switch matrix** | the Arduino-driven PhotoMOS relay array that routes the drive to exactly one pixel's electrode pair |
| **Switchable / frozen** | the antisymmetric / symmetric parts of the saturated response |
| **Tare** | setting a rotator's software zero after homing, so commanded angles mean the same thing every run |
| **Transport recovery** | the reconnect-and-verify state machine that keeps a run alive when an instrument drops off the bus |
| **Triplet** | the three-point readout: local null background, +45°, −45° |
| **Verified move** | a motor move whose readback is checked, with one bounded corrective retry; a failure stops the run rather than corrupting data |
| **Virgin curve** | the first branch of a loop started from 0 V after a domain reset |
| **Wake-up** | the first-cycle transient after poling or storage |
| **Worker** | the child process that owns the hardware. The GUI only launches and monitors it; the same file run with `--cli` *is* the worker |
| **Zero-anchor** | a reduced readout that measures the local background and one slope only, dropping the opposite slope. Faster than the triplet, and loses the sign check |

---

## 3. Abbreviations

The short forms that appear in column names, terminal output and figure labels.

| Abbreviation | Expansion | Note |
| --- | --- | --- |
| **HWP** | half-wave plate | sets the incident polarisation $\theta_i$; Elliptec ELL14 on COM5, bus address 1 |
| **QWP** | quarter-wave plate | the Sénarmont compensator; ELL14 on COM4, address 2 |
| **ANL** | analyser | the output polariser; ELL14 on COM8, address 2 |
| **EO** | electro-optic | the linear (Pockels) effect is the one being mapped |
| **FE** | ferroelectric | the switching behaviour probed by the hysteresis loops |
| **SMU** | source-measure unit | Aim-TTi SMU4201: sources voltage, measures current |
| **TC** | time constant | the lock-in's output filter time constant; index 14 = 500 ms |
| **FS** | full scale | the lock-in sensitivity range; index 16 = 200 µV RMS full scale |
| **RMS** | root mean square | the lock-in reports RMS volts, and drive amplitudes are converted as $V_\mathrm{rms} = V_\mathrm{pp}/2\sqrt2$ |
| **AM** | amplitude modulation | laser AM is one of the contributions that lands in the analyser-independent term |
| **SSR** | solid-state relay | the AQV258AX PhotoMOS devices in the switch matrix |
| **GDS** | GDSII layout | the chip's mask layout, shown beside the live map for orientation |
| **FEM** | finite element method | how the field-correction factor $\alpha$ must be obtained |
| **NPLC** | number of power-line cycles | integration time unit used by source-measure instrumentation |
| **VISA** | Virtual Instrument Software Architecture | the instrument I/O standard behind `pyvisa`; resource strings look like `TCPIP0::…::SOCKET` |
| **SCPI** | Standard Commands for Programmable Instruments | the textual command language the scope, generator, SMU and lock-in speak over VISA |
| **RSD** | relative standard deviation | dispersion expressed as a fraction of the mean |
| **MAD** | median absolute deviation | an outlier-resistant dispersion estimate |
| **CV** | coefficient of variation | standard deviation divided by the mean |

---

## See also

- [Physics](../physics/index.md): where most of these symbols come from.
- [Sénarmont readout](../physics/03-senarmont-readout.md): $\psi$, $Z(\psi)$,
  $D(\psi)$, the S9 certificate.
- [Ferroelectrics](../physics/05-ferroelectrics.md): $S(V)$, $V_c$, imprint,
  squareness.
- [Instruments](../experiment/instruments.md): the hardware behind the
  abbreviations.
- [Data Schema](data-schema.md): the columns these symbols are stored in.

---

<div align="center">

[← Data schema](data-schema.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md)

</div>
