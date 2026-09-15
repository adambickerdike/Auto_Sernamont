# Operating Guide

This section is the practical half of the documentation: how to install the
software, how to take your first measurement, how to run a full chip campaign,
and what to do when something goes wrong. The physics behind the numbers lives
in [Physics](../physics/index.md); the bench itself is described in
[Experiment](../experiment/index.md).

---

## Who this section is for

You are about to sit down at the rig and produce data that has to survive a
viva. You do not need to be able to derive the Sénarmont relations before you
start, but you do need to be able to tell a real electro-optic signal from
electrical pickup, and this section tells you which columns and which flags to
look at.

The guide assumes:

- you have physical access to the setup (or, for the analysis parts, a run
  folder somebody else produced),
- you can open a Windows terminal in the repository,
- you are willing to spend one hour on a single-pixel shakedown before
  committing seven hours to a chip map.

---

## Recommended reading order

1. **[Installation and Setup](installation.md)**, once per machine. Windows,
   Kinesis, the Python dependencies, the one-time USB stability fix, and how to
   verify that everything is present.
2. **[Quick Start](quickstart.md)**, once per person. Cold lab to one complete,
   trustworthy pixel in about an hour, including how to tell whether it worked.
3. **[Operator Manual](operating.md)**, the reference you keep open while a
   campaign runs. Every setting, the calibration chain, the time budget, the
   follow-up measurements, and how to stop, skip and resume.
4. **[Troubleshooting](troubleshooting.md)**, for when the run stops, the null
   leaks, or the loop looks like a straight line.

Then, for the data itself: [Data Schema](../reference/data-schema.md) tells you
what every file and column means, [CLI Reference](../reference/cli.md) lists
every command-line flag, and [Glossary](../reference/glossary.md) defines every
symbol and term used anywhere in these pages.

| Page | Use it when | Length |
| --- | --- | --- |
| [Installation and Setup](installation.md) | setting up a new measurement PC, or a laptop for analysis only | one-off |
| [Quick Start](quickstart.md) | your first day on the rig, or the first day with a new chip | ~1 h at the bench |
| [Operator Manual](operating.md) | running a real campaign; looking up what a GUI field does | reference |
| [Troubleshooting](troubleshooting.md) | something is wrong and you need it fixed now | reference |

---

## The golden rules

These five rules are not style preferences. Each one exists because breaking it
has already cost somebody a day, a chip, or a dataset.

1. **Always shake down one pixel first.**
   New chip, new day, new code version: run a single pixel end to end and read
   its `fast_map.csv` before you queue eighty-three. A production pixel takes
   ≈ 4.5 to 5 min and the calibration pixel ≈ 8 min, so a shakedown costs ten
   minutes and can save you seven hours of confidently-acquired rubbish. See
   [Quick Start §5](quickstart.md#5-run-one-pixel-the-shakedown).

2. **Always stop with *Request Safe Stop*.**
   It lets the worker finish the point it is on, turn the AC drive off, ramp the
   SMU down, open the switch matrix and write its data. Closing the window or
   killing the process can leave ±40 V sitting on a ~7 µm electrode gap and the
   last pixel's data unwritten. See
   [Operator Manual §8](operating.md#8-stopping-skipping-and-resuming).

3. **Never run with simulated instruments for real data.**
   `--simulate-lockin`, `--allow-fake-lockin`, `--allow-fake-smu` and
   `--allow-no-arduino` exist so motion and timing can be tested without
   hardware. `FakeLockin` synthesises a plausible-looking response from the
   analyser angle, and the numbers it produces are fiction that looks fine.
   Keep *Require real lock-in / SMU / Arduino* ticked, and check
   `"simulated"` in `lockin_configuration.json` before trusting any run.

4. **Keep one hysteresis dwell per campaign.**
   Ferroelectric loop shape is rate-dependent. A loop taken at a 30 s dwell and
   a loop taken at a 10 s dwell are not comparable, and no amount of later
   analysis can make them so. Pick the dwell (the default is 30 s), record it,
   and do not change it mid-campaign. If you want the rate dependence, measure
   it deliberately as its own experiment.

5. **Back up your run folders; they are git-ignored.**
   `pockels_fast_map/`, `pockels_calibration/`, `stage_calibration/` and the
   `calibration_results_*` folders are deliberately excluded from version
   control. Nothing in the repository protects them. Copy each finished run to
   your backup location the same day, together with the chip ID and any notes.

> **A sixth rule, for the physics rather than the hardware:** compare pixels
> with `rotation_slope_rad_per_Vrms`, never with raw lock-in microvolts. Raw
> volts absorb laser power, fibre coupling, focus, detector gain and null depth;
> the normalised rotation divides all of that out. See
> [Operator Manual §12](operating.md#12-campaign-design-guidance).

---

## Where to go from here

- **Understand what you measured:** [Sénarmont readout](../physics/03-senarmont-readout.md)
  explains why the analyser sits at exactly ±45° from the null, and
  [Ferroelectrics](../physics/05-ferroelectrics.md) explains what a butterfly
  loop is telling you about domains.
- **Understand the machine:** [Instruments](../experiment/instruments.md) and
  [Switch matrix](../experiment/switch-matrix.md).
- **Understand the code:** [Architecture](../software/architecture.md),
  [Motion control](../software/motion-control.md),
  [Instrument control](../software/instrument-control.md) and the
  [Data pipeline](../software/data-pipeline.md).

---

<div align="center">

[← The data pipeline](../software/data-pipeline.md) &nbsp;·&nbsp; [Documentation home](../index.md) &nbsp;·&nbsp; [Repository](../../README.md) &nbsp;·&nbsp; [Installation →](installation.md)

</div>
