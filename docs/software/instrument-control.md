# Instrument Control and Timing

**What this page is for:** how a single number gets measured. The exact
sequence behind one lock-in point, the statistical reason phasors are averaged
the way they are, every instrument command sequence with its verification step,
and where the wall-clock hours of a chip map actually go.

---

## Contents

1. [The anatomy of one lock-in point](#1-the-anatomy-of-one-lock-in-point)
2. [Why phasors are averaged in X/Y](#2-why-phasors-are-averaged-in-xy)
3. [The fixed-range policy](#3-the-fixed-range-policy)
4. [Optional predictive ranging (hysteresis only)](#4-optional-predictive-ranging-hysteresis-only)
5. [Function-generator gating](#5-function-generator-gating)
6. [SMU sequences](#6-smu-sequences)
7. [Switch-matrix routing](#7-switch-matrix-routing)
8. [Electrical safety ordering](#8-electrical-safety-ordering)
9. [Adaptive poling](#9-adaptive-poling)
10. [The timing budget](#10-the-timing-budget)

---

## 1. The anatomy of one lock-in point

A "point" is one (pixel, HWP angle, analyser position, drive amplitude)
combination. It costs about twelve seconds, and this is where every one of them
goes.

```text
1.  Move the analyser to the target angle          (verified readback, motion-control.md §3.2)
2.  Enable the function-generator drive channel    (verified: CHN?, then EER?)
3.  Settle:  max(--lockin-settle, 2 × TC × filter order)
              = 2.0 s at the production defaults
4.  Take N = --lockin-avg-readings samples (default 4), spaced by
    --lockin-read-delay (auto-raised to 2.0 s) so the samples are
    approximately independent. Each sample is one `MP.` query returning
    magnitude and phase together.
5.  Check the overload byte before and after the burst
6.  Average in X/Y (Cartesian), never in magnitude
7.  Record magnitude, phase, X, Y, their standard deviations and standard
    errors, the X/Y covariance, the full-scale fraction and the sample count
8.  Disable the drive channel
```

**Step 3 is not a guess.** `minimum_lockin_settle_s()` computes the DSP7230's
own step-settling recommendation, $2 \times \mathrm{TC} \times \text{order}$,
where the order is the filter-slope index plus one. The production defaults are
time-constant index 14 (0.5 s) and a 12 dB/oct filter, so order 2, giving
2.0 s. If the operator asks for less, `configure_fast_globals()` **silently
raises it** and says so on the console. The same value is applied to the sample
spacing, because two samples taken closer together than the settling time are
not independent draws: they are the same filter state read twice, and averaging
them would understate the uncertainty rather than reduce it.

The lock-in is additionally refused any time-constant index below 8 (5 ms),
because reproducible fast-output mode is deliberately disabled.

---

## 2. Why phasors are averaged in X/Y

`phasor_statistics()` in
[`pockels_measurement_analysis.py`](../../pockels/pockels_measurement_analysis.py)
converts every $(|M|, \phi)$ sample into a complex number, averages the real
and imaginary parts separately, and only then derives a magnitude and phase
from the mean.

$$\bar{Z} = \frac{1}{N}\sum_k |M_k| e^{i\phi_k}, \qquad |\bar{Z}| = \sqrt{\bar{X}^2 + \bar{Y}^2}$$

**Never** the other way round:

$$\overline{|M|} = \frac{1}{N}\sum_k |M_k| \qquad \text{(wrong)}$$

> **Warning: averaging magnitudes rectifies noise.**
> Magnitude is $\sqrt{X^2+Y^2}$, a strictly positive-definite function of two
> noisy quantities. Each individual $|M_k|$ therefore has a **positive bias**:
> noise in $X$ and $Y$ can only ever push the magnitude *up*, never down. The
> mean of the magnitudes is biased upward by roughly the noise level, and no
> amount of extra averaging removes it; more samples converge to the biased
> value, not the true one.
>
> Averaging the phasor first is unbiased, because $X$ and $Y$ noise is
> zero-mean and cancels in the sum. The magnitude derived from the averaged
> phasor converges correctly to zero when the true signal is zero.
>
> **Where this matters most is at a coercive point.** In a ferroelectric
> hysteresis loop the true electro-optic response passes *through zero* and
> changes sign as the domains reverse. Magnitude averaging turns that genuine
> zero crossing into a shallow non-zero minimum, blunting the very feature the
> measurement exists to find: the coercive voltage would be mis-located and
> the loop would look artificially "pinched". The sign information is the
> physics, and only the phasor carries it.

What `phasor_statistics()` returns is correspondingly complete: `mean_x_V`,
`mean_y_V`, `mean_mag_V`, `mean_phase_deg`, `signed_V`, the per-component
standard deviations and standard errors, the **X/Y covariance**
(`cov_xy_V2`, `cov_mean_xy_V2`), and delta-method magnitude and phase
uncertainties obtained by rotating the covariance matrix into radial and
tangential components along the mean phasor,
$\sigma_{|Z|}^2 = u_x^2\sigma_x^2 + u_y^2\sigma_y^2 + 2u_xu_y\sigma_{xy}$ with
$\hat{u} = \bar{Z}/|\bar{Z}|$. Keeping the covariance rather than two
independent variances matters because a correlated drift in $X$ and $Y$, a
laser power drift for example, is *radial*: it inflates the magnitude
uncertainty but barely touches the phase.

`read_lockin_averaged()` in
[`analyser_sweep_voltage_series.py`](../../pockels/analyser_sweep_voltage_series.py)
is the acquisition-side wrapper: it retries non-finite reads up to
$4N$ attempts, then hands the collected samples to `phasor_statistics()` and
returns both the statistics and the raw sample lists, which are written to
`fast_map_raw_samples.csv`.

---

## 3. The fixed-range policy

The chip map holds the lock-in sensitivity **fixed** for the entire run at
index 16, **200 µV RMS full scale**, with automatic AC gain disabled.

This is a measurement-integrity decision, not a convenience. A range change
mid-map rescales the instrument's own gain and offset, and two pixels measured
on two different ranges are not directly comparable at the part-per-thousand
level a compositional study needs. Index 16 was chosen because the July/early
August 9 Vpp calibration data reached 98 µV on a 50 µV full-scale range; 200 µV
keeps the strongest expected analyser probes inside the input range while
retaining ample resolution for the weak ones.

`install_fixed_lockin_range_policy()` goes further: it *replaces* the
range-changing `check_overload()` helper that
[`Pockels_Calibration_2026.py`](../../pockels/Pockels_Calibration_2026.py) uses
elsewhere, for this process only, so that the in-run AC and DC follow-ups
cannot quietly change the range either.

`configure_lockin_for_fast_map()` then applies a **write-only** configuration:

```text
set_voltage_input_mode()          set_sensitivity(16)
set_ac_coupling()                 set_time_constant(14)
set_automatic_ac_gain(False)      set_filter_slope(12 dB/oct)
set_reference_mode_single()
set_reference_source(external analog)
disable_synchronous_filter()
disable_fast_output_mode()
```

and records `readback_queries_skipped: ["TC.", "SLOPE", "IE", "SEN", "FRQ."]`
in `lockin_configuration.json`. Those strict configuration queries are
deliberately **not** issued: some DSP7230 firmware/transport combinations return
an empty string to them even when the write succeeded, and aborting a
seven-hour run over a cosmetic query is the wrong trade. This is the
"fail open on telemetry" principle in its purest form: the *configuration*
readback is telemetry, the *measurement* is physics.

Rows are then graded on every read:

| Condition | Result | Flag |
| --- | --- | --- |
| ≥ 85 % of full scale (`LOCKIN_FULLSCALE_WARN_FRACTION`) | warned | `lockin_range_near_fullscale` |
| ≥ 98 % of full scale (`LOCKIN_FULLSCALE_FAIL_FRACTION`) | **invalid** | `lockin_range_exceeded` |
| Overload byte set | **invalid** | `lockin_overload` |

Invalid rows are still written to the CSV (nothing is discarded), but they are
in `DISQUALIFYING_ROW_FLAGS` and are excluded automatically from every fit. See
[The Data Pipeline](data-pipeline.md#5-the-quality-flag-system).

---

## 4. Optional predictive ranging (hysteresis only)

A hysteresis loop is the one place where a fixed range is genuinely awkward: the
signal sweeps from saturation down through zero at the coercive point and back,
spanning two orders of magnitude within a single 45-point sweep.
`PredictiveHystereticRangeController` in
[`pockels_lockin_ranging.py`](../../pockels/pockels_lockin_ranging.py) is the
opt-in answer, disabled by default.

It chooses among the sensitivity ladder 5, 10, 20, 50, 100, 200 µV full scale
(indices 11 to 16) using the already-certified peak response, the current branch,
and prior points measured at matching voltages on the same branch family. The
rules are deliberately asymmetric:

- **Target ~60 % of full scale** (`target_fraction`), with a rescue threshold at
  **85 %** (`rescue_fraction`). The requested index is the narrowest range whose
  60 % point still exceeds the predicted upper bound, where the upper bound is
  the prediction plus `safety_sigma = 4` times the recent noise estimate.
- **Widening is immediate. Narrowing needs two consecutive low-range decisions**
  (`narrow_confirmations = 2`) and proceeds **one step at a time**. Widening
  late costs you a clipped point; narrowing early costs you a clipped point
  too, so the controller only ever narrows on repeated evidence, and then
  cautiously.
- **Planned changes happen with the AC drive off**, before the existing DC
  dwell, so they cost no extra time at all: the range write is hidden inside
  30 s of poling that was going to happen anyway.
- **An unexpectedly high averaged reading is discarded and re-acquired** on a
  wider range. The discarded window is *kept* in
  `dc_hysteresis_raw_samples.csv` and explicitly tagged
  `Discarded_For_Range_Rescue`, so the audit trail is complete.
- **A hold period** (`hold_points_after_change = 2`) prevents the controller
  from oscillating between two adjacent ranges.
- **The fixed range is restored when the loop finishes.**

Every decision is recorded in `dc_hysteresis.csv` in the `LockIn_Range_*`
column family: the prediction, the noise prior, the upper bound, the requested
index, the reason string, whether the range actually changed, and whether a
rescue re-read occurred.

---

## 5. Function-generator gating

The Aim-TTi TGF3162 drives the device on CH1 and feeds the lock-in's external
reference on CH2. Only CH1 is ever gated.

```python
_funcgen_set_sweep_output(fg, on, verify_command=True, required=True, context=...)
```

```text
1. Query EER?   once, to clear and expose any stale execution error, so that a
                later EER? unambiguously belongs to THIS transition
2. CHN 1        select the drive channel
3. Query CHN?   must read back 1
4. OUTPUT ON | OFF
5. Query EER?   must be 0
                (−80 means the generator disabled its own output because of an
                 output-voltage overload, i.e. the load misbehaved)
6. Sleep 0.2 s
```

> **Note**
> The TGF3162 has **no documented OUTPUT-state query.** You cannot simply ask
> it whether the output is on. This select-then-verify-then-check-error
> sequence is the strongest confirmation the instrument makes available: it
> proves the command reached the right channel and that the instrument raised
> no execution error while acting on it. With `required=True` a failure
> **raises**, which escalates to hardware recovery rather than silently leaving
> the chip driven.

The **reference** channel (CH2, 0.5 Vpp at 30 kHz) is configured once at
startup and left on for the whole session, because the lock-in needs a
continuous external reference and losing it mid-run would unlock the
demodulator. Both channels are configured with `ZLOAD OPEN`.

**Amplitude changes are made with the output off.** `_prepare_hysteresis_ac_drive()`
forces a verified `OUTPUT OFF`, clears `EER?`, programmes the new amplitude,
and re-checks `EER?`, because changing 9 Vpp → 4 Vpp while the output is live
can upset this instrument. `configure_funcgen_safe()` applies the same ordering
at bring-up and on every reconnect: CH1 is forced off *first*, because after a
lost handle it may well have remained on.

---

## 6. SMU sequences

The Aim-TTi SMU4201 sources voltage and measures current, with a hard
$\pm 40$ V ceiling (`DC_HYST_VMAX`) and a 1 mA compliance limit
(`SMU_COMPLIANCE_A`).

### 6.1 Session setup

```text
*CLS ; *RST ; (0.6 s, *RST takes a moment)
SYSTem:FUNCtion:MODE SOURCEVOLTage
SOURce:VOLTage:TERMinals 2WIRe               deterministic terminal config
SOURce:VOLTage:SHAPe FIXed
SOURce:VOLTage:SHAPe:COUNt 1
SOURce:VOLTage:SHAPe:TRIGger OFF             never wait for an external trigger
SOURce:VOLTage:MEASure:PRIMary   CURRent
SOURce:VOLTage:MEASure:SECondary VOLTage
SOURce:VOLTage:MEASure:COUNt:INFinite ON     keep sampling while we step the source
SOURce:VOLTage:CURRent:LIMit <compliance>
SOURce:VOLTage:FIXed:APERture:NPLCycles 1.0
SOURce:VOLTage:FIXed:LEVel 0                 start at 0 V
SOURce:VOLTage:DELay:AUTO ON
```

The two `MEASure` assignments are the important ones.
`MEASure:PRIMary:LIVEdata?` returns the current; `MEASure:SECondary:LIVEdata?`
returns the **actual terminal voltage**. Having the real terminal voltage
available as a live query is what makes the per-point electrical audit in §6.4
possible at all.

### 6.2 Turning on safely

```python
smu.set_voltage(V)      # programme the level while the OUTPUT is still OFF
smu.output(True)        # only then enable
```

Never the other way round. Enabling the output at a stale level would apply the
*previous* pixel's voltage to the newly routed pixel for however long it takes
the next command to arrive.

### 6.3 Ramping and slew limiting

Voltage changes move in **5 V chunks with a 0.2 s dwell**
(`SMU_RAMP_STEP_V`, `SMU_RAMP_DWELL_S`), and the instrument's slew rate is
capped at `SMU_SLEW_RATE_V_PER_MS = 50.0` V/ms rather than using its maximum.

The reason is capacitive: $i = C\,\mathrm{d}V/\mathrm{d}t$. The device's own
capacitance turns a fast edge into a large transient current, which trips the
1 mA compliance limit and aborts the point. At 50 V/ms and $C \approx 1$ nF the
transient is about 50 µA, comfortably inside compliance, while a 50 V swing in
1 ms is still millisecond-scale, far slower than ferroelectric switching, which
is sub-microsecond. The limit costs nothing physically.

### 6.4 The per-point electrical audit

At every hysteresis point, **while the AC drive is still off**:

```text
output state      ← OUTPut:STATe?                     must be ON, else abort
programmed level  ← SOURce:VOLTage:FIXed:LEVel?       must match the request to 0.01 V
measured current  ← MEASure:PRIMary:LIVEdata?         DC-only leakage
measured voltage  ← MEASure:SECondary:LIVEdata?       actual terminal voltage
compliance flag   ← SYSTem:PROTection:CURRent:TRIPped?
```

with up to `SMU_DC_READBACK_ATTEMPTS = 3` attempts,
`SMU_DC_READBACK_RETRY_S = 0.1` s apart, requiring *all three* numeric
readbacks to come back finite together.

> **Warning: why a 0.5 V mismatch aborts the point before AC turns on.**
> If the measured terminal voltage differs from the request by more than
> `SMU_DC_READBACK_TOLERANCE_V = 0.5` V, or if the telemetry is still
> unavailable after three attempts, the point is **rejected** and the sweep
> raises, with the AC drive still off.
>
> This is the check that catches a broken bond wire, an open probe, a lifted
> contact or a shorted pixel. The failure signature of all of them is the same:
> the SMU programmes $+40$ V and the terminals read something else entirely,
> because the current cannot flow where the software thinks it is flowing. If
> the measurement continued, the lock-in would faithfully record a real optical
> phasor, at an unknown applied field. That row would enter the loop, shift
> the apparent coercive voltage, and be indistinguishable from physics.
>
> The abort happens **before AC** for two independent reasons. First, the
> current reading must be DC-only: a 30 kHz probe running through the device
> contaminates the leakage number that this same audit records. Second, if the
> electrical path is broken there is no reason to energise the drive at all.
> Catching the fault one step earlier turns a silently wrong loop into a loud,
> obvious abort.

### 6.5 Domain reset: `reset_domains_pulsed()`

Bipolar depoling, modelled on the alternating-field erase step in the
ferroelectric literature: an envelope decaying exponentially from
`RESET_VMAX = 40` V to `RESET_VMIN = 0.05` V over `RESET_AMP_STEPS = 30`
amplitudes, with `RESET_CYCLES_PER_AMP = 200` cycles of $+V_n$ then $-V_n$ at
each, 5 ms per half-cycle (about 12 000 bipolar reversals in ~30 s), slew-limited
as in §6.3.

`OUTPut` stays **on** throughout and the pulses are software-stepped with
`set_voltage()` rather than using `SHAPe PULSe` with output toggling. Every
fresh `OUTPut:STATe ON` triggers the SMU's multi-second "Counts/Shapes"
front-panel banner, during which the rails are not fully established, and that
would otherwise corrupt the first few hundred pulses of every reset.

---

## 7. Switch-matrix routing

Routing is always done through a context manager, so the channel cannot be left
closed by an early return or an exception:

```python
with routed_arduino_channel(matrix, channel, settle_s, require_off=...):
    ...   # the entire measurement block for this pixel
# the channel is guaranteed off on exit, including on exception
```

On entry it sends `turn_all_off()`, waits 50 ms, selects the channel, and waits
`settle_s`. On exit, in a `finally`, it sends `turn_all_off()` again. With
`require_off=True` a failure to confirm the shutdown re-raises, unless the
process is unwinding from a `KeyboardInterrupt`, in which case it warns instead
of masking the interrupt.

```text
pixel (1..100) ──PIXEL_TO_ELECTRICAL_SWITCH──▶ electrical switch (1..100)
        "E<switch>\n" → Arduino Nano
                      → 7 × TLC59282 cascaded shift registers (112 bits)
                      → AQV258AX PhotoMOS SSR → one electrode pair
```

Switching is **exclusive in firmware**: selecting a channel deselects every
other one, and re-selecting the currently active channel toggles it off.
`"0\n"` opens everything. With `verify_commands=True` the Python wrapper waits
up to 2 s for the exact expected acknowledgement line, either
`"-> Electrical Switch N"` or `"-> ALL channels have been turned OFF."`, and
raises on an `Error:` line or on timeout.

> **Warning: the mapping is checked at import time.**
> The pixel-to-switch mapping is duplicated in three places:
> `arduino_switch_matrix.PIXEL_TO_ELECTRICAL_SWITCH`,
> `stage_calibration.PIXEL_TO_PIN` and the firmware array in
> [`firmware/switch_matrix/switch_matrix.ino`](../../firmware/switch_matrix/switch_matrix.ino).
> `pockels_fast_map_gui.py` therefore asserts two spot checks the moment it is
> imported:
> ```python
> if stage.pixel_to_channel(46) != 5 or stage.pixel_to_channel(85) != 62:
>     raise RuntimeError("Fast-map switch mapping is stale: expected pixel 46 -> 5 and pixel 85 -> 62.")
> ```
> and refuses to start if they disagree. A stale map would apply up to 40 V to
> the *wrong pixel* while labelling the data with the right one. That is the
> worst failure mode this system has, because it is completely invisible in the
> output. Full details in [The Switch Matrix](../experiment/switch-matrix.md).

---

## 8. Electrical safety ordering

Almost every rule in this section is an ordering rule. Collected in one place:

| Rule | Why |
| --- | --- |
| Function generator is brought up with CH1 **off**; only the CH2 reference is enabled | Nothing can be driven before a pixel is routed |
| SMU is armed at **0 V with the output disabled** at bring-up | The first enable cannot apply a stale level |
| **Route the Arduino channel, then** enable the SMU | The voltage must never exist before its destination does |
| `set_voltage()` **then** `output(True)`, never the reverse | A stale programmed level would reach the new pixel |
| **Amplitude changes with the drive output off** | The TGF3162 can fault on a live amplitude change |
| **AC off during every DC poling dwell and every electrical audit** | A 30 kHz probe contaminates the DC leakage current |
| The point is **aborted before AC turns on** if the electrical audit fails | A wrong field with a good optical read is worse than no read |
| On transport recovery: **isolate (FG off, SMU off, matrix all-off) before any routing or motion** | Recovery must not energise anything it has not verified |
| On transport recovery: **never replay the failed `E<n>` selection** | An Arduino that just reset must be put into a known ALL-OFF state first |
| Teardown lives in `finally` blocks and context managers, never on the happy path | It must run on the exception path too |

---

## 9. Adaptive poling

Each pixel is poled at $+40$ V (`--poling-voltage`, default
`DC_POLING_V_PHASE_A = 40.0`) before its angular sweep. A fixed dwell must be
chosen for the *slowest* pixel on the chip, which wastes minutes on every fast
one. `adaptive_poling_dwell()` measures instead of assuming.

**The physics.** At a fixed small AC dither, the 1$f$ lock-in magnitude tracks
the net switched polarisation in the illuminated gap. When it stops growing,
poling is complete. There is no need to guess.

**The algorithm.**

```text
setup:  the analyser sits at the first-HWP null seed, where the Malus slope is
        ~zero, so it is moved to  a_seed + 45°  (verified) for the monitor and
        returned afterwards; the monitor drive amplitude is enabled with a
        verified OUTPUT ON and the funcgen settle is honoured

loop, every --adaptive-poling-poll-s = 10 s until --poling-dwell = 180 s:
    check the fixed-range overload state
    read 3 averaged lock-in samples, 0.05 s apart
    log (elapsed, magnitude, phase, sensitivity index)
    if the range changed, or the read was non-finite:  reset the streak
    if |ΔM| < max(2 % × |M|, 0.05 µV):  streak += 1   else  streak = 0

early stop requires ALL THREE:
    streak ≥ --adaptive-poling-stable-count = 3      (3 quiet intervals)
    elapsed ≥ --adaptive-poling-min-s      = 60 s    (a hard floor)
    |M|     ≥ --adaptive-poling-min-signal-uv = 2 µV (a real signal)
```

The three-way conjunction is the whole design. A **plateau alone is not
enough**: a dead pad, a pixel with no electrical contact, or one that is simply
too weak to see also produces a beautifully flat trace. Requiring a minimum
signal level means low-signal pixels always receive the **full** 180 s dwell,
so a weak result can never be blamed on an early stop. Requiring a 60 s floor
prevents a slow-starting pixel from being cut short during its initial
transient.

Every failure mode degrades to the safe behaviour: any exception in the monitor
falls back to a plain fixed sleep for the *remaining* time, and the `finally`
block always forces a verified `OUTPUT OFF` and always attempts the analyser
return.

**The free measurement.** The samples are written to `poling_kinetics.csv`
(`t_s`, `lockin_mag_V`, `lockin_phase_deg`, sensitivity index). Because those
points describe how the switched polarisation grows under a constant field,
`fit_poling_kinetics()` in
[`pockels_hysteresis_analysis.py`](../../pockels/pockels_hysteresis_analysis.py)
can fit the stretched-exponential (Kohlrausch) form

$$M(t) = M_\infty + (M_0 - M_\infty)\,\exp\!\left[-\left(t/\tau\right)^{\beta}\right]$$

and return `tau_s`, `beta`, `m0_V`, `m_inf_V` and `r_squared` per pixel. The
characteristic switching time $\tau$ and the stretching exponent $\beta$ (a
direct measure of the *width* of the local switching-time distribution, and so
of disorder) are obtained at **zero additional measurement cost**, purely
because the poling dwell was instrumented instead of slept through. The
compositional report joins $\tau$ and $\beta$ against the loop metrics; see
[Ferroelectric Switching](../physics/05-ferroelectrics.md).

---

## 10. The timing budget

![Where the wall-clock time goes](../../assets/figures/timing_breakdown.png)

### One lock-in point: about 12 s

| Step | Time |
| --- | --- |
| Analyser move + verified readback | ~1 to 2 s |
| Function generator on (verified) | ~0.4 s |
| Settle ($2 \times \mathrm{TC} \times$ order) | 2.0 s |
| 4 samples × 2.0 s spacing | 8.0 s |
| Function generator off | ~0.4 s |
| **Total** | **≈ 12 s** |

Two thirds of that is the lock-in's own filter, and it is irreducible at this
time constant: the samples must be spaced by the settling time to be
independent, and the settling time is set by the noise bandwidth you need.

### One production pixel: about 4.5 to 5 min

| Step | Time |
| --- | --- |
| Stage move + hill-climb alignment | ~20 to 40 s |
| Arduino routing + SMU ramp | ~5 s |
| Adaptive poling | 60 to 180 s (usually ~60 to 90 s) |
| 9 HWP × (verified move + null check) | ~60 s |
| 9 HWP × 3 lock-in points × ~12 s | ~200 s* |
| Teardown | ~5 s |
| **Total** | **≈ 4.5 to 5 min** |

\* the null/background read shares the analyser position with the previous
point, so it is cheaper than a full 12 s.

The **calibration pixel** adds the forced 2-D null at every HWP angle, plus the
balanced four-point fit and its confirmation, roughly **8 min** in total.

### One hysteresis loop: about 29 min

| Step | Time |
| --- | --- |
| DC ramp + preset (`DC_RAMP_PRESET_S`) | ~1 to 2 s |
| DC-only poling dwell (`DC_POLING_DWELL_S`) | 30 s |
| Electrical audit | ~1 s |
| AC on + settle + averaging | ~6 s |
| **Per point** | **≈ 39 s** |
| **45-point single cycle** | **≈ 29 min** |
| **100 pixels** | **≈ 49 h** |

The 45 points come from the centre-dense grid: 12 absolute levels
(40, 30, 25, 20, 15, 12.5, 10, 7.5, 5, 2.5, 1.25, 0 V) give 23 signed levels,
traversed $+40 \to -40 \to +40$ as 23 + 22 points.

### Where the hours go

| Activity | Wall clock |
| --- | --- |
| One lock-in point | ~12 s |
| One production pixel (9 HWP × triplet) | ~4.5 to 5 min |
| The calibration pixel | ~8 min |
| **83-pixel chip map** (the default selection) | **~6 to 7 h** |
| One 45-point hysteresis loop | ~29 min |
| **100 hysteresis loops** | **~49 h** |

Three quarters of a production pixel is poling and lock-in settling: that is,
waiting for physics, not for software. This is the honest reason the map uses
9 HWP × 3 analyser points rather than a full analyser sweep: a naive complete
plan of 100 pixels × 7 HWP × 5 voltages × 18 analyser angles is 63 000 lock-in
points, which is hundreds of hours. The fast map spends 27 acquisitions per
pixel and invests the saved time in validation and hysteresis, on the pixels
the map itself identifies as worth it.

### Known speed levers, not currently implemented

- DSP7230 curve-buffer burst reads (roughly 2× on read time).
- Reusing the coercive window across pixels (currently manual, via the
  fixed-window flag).
- A 7-point HWP grid, once the signed $2\theta$ fit is sufficiently validated.

---

**Next:** [The Data Pipeline](data-pipeline.md) for what happens to these
numbers once they are on disk. For the instruments themselves see
[Instruments](../experiment/instruments.md); for the day-to-day procedure see
[Operating the Instrument](../guide/operating.md); every flag named on this
page is listed in the [CLI Reference](../reference/cli.md).

---

<div align="center">

[← Motion control](motion-control.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [The data pipeline →](data-pipeline.md)

</div>
