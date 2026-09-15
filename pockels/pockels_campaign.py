#!/usr/bin/env python3
# --------------------------------------------------------------------
# Pockels Chip Campaign
#
# Unified workflow that combines:
#   1. A ONE-TIME full sample calibration (3-step: analyzer, QWP, null)
#      reusing the logic from sample_calibration.py.
#   2. A 100-point (10x10) stage scan reusing the auto-align fallback
#      chain from stage_calibration.py.
#   3. A FAST per-pixel re-null at each stage position that warm-starts
#      from the previous null (or the sample cal null) and only does a
#      small local adjustment - no sweeps, no extinction remeasurement.
#      Escalates to the full nulling routine only if drift is large.
#
# Output goes to:   pockels_campaign/<run_name>/
#
# See POCKELS_CAMPAIGN.md for algorithm details and tuning guidance.
# --------------------------------------------------------------------

import os, csv, json, time, traceback, threading
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Reuse stage machinery + scope class + camera view + alignment chain.
# Importing stage_calibration also performs Kinesis CLR loads.
import stage_calibration as sc

# Reuse polarisation primitives, rotator class, full calibration steps.
from POL_Chip_Test_Working_2026 import (
    ElliptecRotator,
    MotionLogger,
    safe_move_abs,
    wrap180,
    golden_section_min_1d,
    quad_refine_1d,
    null_qwp_analyzer,
    sweep_angles,
    refine_extremum_1d,
    fit_cos2,
    fit_qwp_cos4,
    save_calibration_pack,
    load_calibration_pack,
    PORT_HWP, ADDR_HWP,
    PORT_ANL, ADDR_ANL,
    PORT_QWP, ADDR_QWP,
    MOVE_SETTLE_S,
    CONFIRM_HWP_FIXED,
    ANL_SWEEP_RANGE, ANL_SWEEP_STEP,
    QWP_SWEEP_RANGE, QWP_SWEEP_STEP,
    REFINE_WINDOW, REFINE_STEP,
    USE_LASER, KLS1550, KLS_SERIAL, KLS_POWER_MW, LASER_STABILIZE_S,
    V_TO_W, RESP_A_PER_W, PDA_GAIN_DB, PDA_LOAD,
)

# ============================== CONFIG ==================================

# Bump when making behaviour changes so stale Spyder/pycache copies are
# visible at startup. If the banner printed at main() start doesn't match
# this string, the module was loaded from cache - restart the kernel.
CAMPAIGN_CODE_VERSION = "2026-04-17_motion_logging_v5"

CAMPAIGN_ROOT = "pockels_campaign"
os.makedirs(CAMPAIGN_ROOT, exist_ok=True)

# If any single rotator faults this many times over the whole campaign,
# abort cleanly - something is genuinely broken, not transient.
MAX_ROTATOR_FAULTS_PER_RUN = 8
_rot_fault_counts = {}  # keyed by rotator port

# Preemptive rotator re-home every N pixels. Even with all moves switched
# to absolute set_angle, the ELL14's internal position counter can still
# wander past a firmware range boundary over hundreds of mr commands.
# Periodically homing+taring both rotators resets the counter to 0 so
# drift cannot accumulate across the whole 100-pixel run. After re-home
# the next pixel's _warm_move_rot uses set_angle to move QWP+ANL back to
# the current warm-null from 0 deg - a single ~2 s move per rotator,
# done in parallel. Total cost: ~3 s per re-home x 5 re-homes = 15 s
# added to a ~18 min run. Worth it.
REHOME_EVERY_N_PIXELS = 20

# ---- Fast re-null tuning ----
# Acceptance: if the starting power at the warm-start angles is within this
# factor of the reference null, we only quad-refine (no GS).
FAST_NULL_ACCEPT_RATIO    = 2.0

# Small-drift: if starting power is between ACCEPT_RATIO and ESCALATE_RATIO of
# ref, we do a short GS on each axis followed by quad refine.
FAST_NULL_ESCALATE_RATIO  = 50.0

# GS search half-span (degrees) for small-drift path.
FAST_NULL_SPAN_DEG        = 3.0

# GS tolerance during small-drift path (degrees). 0.3 deg matches the
# ELL14 spec floor and keeps the search under ~8 iterations.
FAST_NULL_TOL_DEG         = 0.3

# Quad refine step (degrees). 0.5 deg gives 5-point probes spanning +-1.0 deg.
FAST_NULL_QUAD_STEP_DEG   = 0.5

# Full-null fallback params (only fires if drift > ESCALATE_RATIO). These match
# the sample_calibration.py defaults so behaviour is identical on escalation.
FULL_NULL_SPAN_DEG        = 20.0
FULL_NULL_CYCLES          = 3
FULL_NULL_TOL_DEG         = 1.2

# Consider a pixel "nulled" if final power <= this * p_null_reference.
# Used only for logging / flagging; does not abort the run.
NULL_QUALITY_FLAG_RATIO   = 5.0

# V/div used for the null read (fine range). ScopeDetector will still
# auto-scale inside read_averaged if this is too coarse, but setting a
# low default avoids the first reading being clipped.
NULL_VDIV_HINT_V          = 0.010   # 10 mV/div

# Number of averaged scope reads per power measurement during re-null.
# 2 is usually enough near the null; the 2D parabolic fit naturally averages
# out noise across its 6 probes.
NULL_SCOPE_AVG            = 2

# Probe step (deg) for the 2D parabolic null. Step size ~ expected distance
# from true null to minimum captured curvature. Coarse-to-fine: start at this
# step, then re-fit with step/3 to tighten.
NULL_2D_STEP_COARSE_DEG   = 3.0
NULL_2D_STEP_FINE_DEG     = 1.0

# Per-pixel stage auto-align is always ON: every pixel does the full
# brighten -> stage align -> dim -> renull sequence. This is the real
# campaign behaviour; compositional drift across the chip means the
# beam-on-gap position shifts pixel-to-pixel.
SKIP_PER_PIXEL_STAGE_ALIGN = False

# How far to rotate the analyzer off null for stage auto-align. 30 deg gives
# transmission ~ sin^2(2*30) = 75% of peak - plenty for hill_climb - while
# only rotating a third as far as the old +-90 deg brighten/dim. Total
# per-pixel analyzer travel: 60 deg (not 180 deg).
BRIGHTEN_OFFSET_DEG = 30.0


# ============================ SCOPE HELPERS ==============================

def scope_power_w(detector, auto_scale=True, n=NULL_SCOPE_AVG):
    """Read averaged power (W) from the ScopeDetector in stage_calibration.

    Returns a scalar power in W, clipped to non-negative.  Missing/non-finite
    scope telemetry is an acquisition failure, never an optical zero: treating
    a dead scope as a perfect null can steer the rotators using fabricated
    data and allow the campaign to continue on invalid instrument sessions.
    """
    v, std, pw, vdiv = detector.read_averaged(n=n, auto_scale=auto_scale)
    try:
        voltage = float(v)
        power = float(pw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Oscilloscope detector telemetry unavailable during optical nulling"
        ) from exc
    if not (np.isfinite(voltage) and np.isfinite(power)):
        raise RuntimeError(
            "Oscilloscope detector telemetry unavailable during optical nulling"
        )
    return max(0.0, power)


# ================== 2D PARABOLIC NULL (fast: ~7 reads) ==================

# Rotator movement speed tiers (fastest -> most conservative):
#
#   _probe_move_rel  - probe moves inside the parabolic fit. Relative move
#                      (no get_angle query), cached position, no backlash,
#                      no readback, minimal settle. ~100 ms per move.
#   _quick_move_rot  - absolute move for null probes. No backlash, no
#                      readback, short settle, skips redundant. ~150 ms.
#   _warm_move_rot   - warm-start move (previous pixel's null). Single
#                      un-chunked move, no backlash, no readback, short
#                      settle. Skips entirely if < 0.1 deg from target.
#                      Replaces safe_move_abs in fast_renull. ~100-300 ms.
#   _precise_move_rot- final approach to computed optimum. Single backlash
#                      prep + direct approach. No chunking. ~500 ms.
#   _medium_move_rot - brighten/dim (5-180 deg). Single un-chunked move
#                      with backlash prep. ELL14 handles up to 360 deg
#                      in one mr command. ~0.5-1.5 s for 30-90 deg.
#   safe_move_abs    - precision-critical sweeps (Phase 1 only). Full
#                      chunking + backlash + 1.6 s scan pause.
#
# _parallel_move fires two rotators (different COM ports) simultaneously
# via threads. Used inside null_2d_parabolic to cut probe time ~40%.

QUICK_SETTLE_S       = 0.18   # rotator mech settle between null probes.
                              # ELL14 mech settle at end of mr is ~30-50 ms;
                              # 0.18 s gives a visible "pause between flicks"
                              # so the motion looks measured rather than
                              # frenetic, and reads are well clear of the
                              # settling transient.
MEDIUM_SETTLE_S      = 0.25   # brighten/dim and precise-move settle.
ROTATOR_VELOCITY_PCT = 60     # Thorlabs ELL14 velocity as % of max (1-100).
                              # 60% cuts peak angular speed to ~36 deg/s (from
                              # ~60 deg/s at 100%). Physical moves take ~1.7x
                              # longer but are noticeably calmer and less
                              # prone to overshoot / encoder glitching.
WARM_SKIP_TOL_DEG    = 0.10   # warm-start move: skip if within this of target
PRECISE_BACKLASH_DEG = 0.3    # backlash overshoot for final optimum move
MEDIUM_BACKLASH_DEG  = 0.3    # brighten/dim backlash overshoot

# Probe averaging for null_2d_parabolic. n=2 adds ~0.15 s per probe (~1 s
# per fit) but halves the per-probe noise, which directly improves the fit
# quality since 6 probes exactly determine the 6 quadratic coefficients -
# single-probe noise tilts the whole solution.
NULL_PROBE_N         = 2

# If the fit's solved-for optimum has power > this factor of the best
# probed point, we assume the fit missed and trigger the polish step.
NULL_POLISH_TRIGGER_RATIO = 1.3

# Polish step: after a missed fit, do a 4-point cardinal refine at step/3
# centred on the best known point, and take the true minimum. Guards
# against probe noise tilting the quadratic enough that the solved
# optimum extrapolates ~0.5 deg off the real null.
NULL_POLISH_STEP_DIV = 3.0


def _parallel_move(move_a, move_b):
    """Run two rotator moves concurrently on different COM ports.

    Each argument is a zero-arg callable (closure). Starts both as daemon
    threads, joins both before returning. QWP and ANL are on different
    serial ports so there's no bus contention - this cuts probe move time
    ~40% during the 2D parabolic fit.
    """
    err = [None, None]
    def _w(i, fn):
        try: fn()
        except Exception as e: err[i] = e
    t_a = threading.Thread(target=_w, args=(0, move_a), daemon=True)
    t_b = threading.Thread(target=_w, args=(1, move_b), daemon=True)
    t_a.start(); t_b.start()
    t_a.join(); t_b.join()
    if err[0] is not None: raise err[0]
    if err[1] is not None: raise err[1]


# ---------------------------------------------------------------------------
# Rotator fault recovery.
#
# The ELL14 occasionally returns 'sensor error' / 'limit' / 'range' from an
# mr command - usually a transient encoder desync or electrical glitch, not
# a mechanical jam. move_by() retries 3x internally with 300 ms waits; if
# all retries still return a fault, it raises. Mid-campaign this would kill
# the run (the thread exception propagates through _parallel_move up to
# fast_renull's caller).
#
# Recovery matches POL_Chip_Test_Working_2026.safe_move_abs:
#   1. rot.stop() to abort any in-flight move
#   2. rot.home(direction=0)  -> rotator goes to mechanical home index
#   3. rot.tare()             -> same "0" reference as at boot, so all
#                                absolute angles remain valid
#   4. caller retries the move (from the post-home 0-ish position) via
#      absolute set_angle since the cached position is no longer trusted.
# ---------------------------------------------------------------------------

ROT_FAULT_KEYS = ("sensor", "limit", "range", "timeout", "mechanical",
                  "motor", "overcurrent", "thermal", "isolat", "initial")


def _is_rotator_fault(exc):
    """True if this exception looks like a recoverable ELL14 fault.

    Covers the full RESPONSES table in elliptec_serial.py: sensor error,
    motor error, out of range, value out of range, thermal error,
    mechanical timeout, communication timeout, module isolated, motor
    overcurrent, initialization error. All of these are clearable by a
    stop -> home -> tare cycle.
    """
    return any(k in str(exc).lower() for k in ROT_FAULT_KEYS)


def _rot_recover(rot, cache=None, cache_key=None, label=""):
    """Stop -> home -> tare after a rotator fault. Invalidates the cache
    entry so the next move reads the true position instead of the stale
    pre-fault guess. Returns True if recovery succeeded.

    Increments a per-port fault counter. If a single rotator faults more
    than MAX_ROTATOR_FAULTS_PER_RUN times across the whole campaign, give
    up recovery - something is genuinely broken and repeated auto-homing
    will only mask the real problem.
    """
    port = rot.port
    _rot_fault_counts[port] = _rot_fault_counts.get(port, 0) + 1
    tag = f"[ROT {label or port}]"
    if _rot_fault_counts[port] > MAX_ROTATOR_FAULTS_PER_RUN:
        print(f"  {tag} exceeded {MAX_ROTATOR_FAULTS_PER_RUN} faults this "
              f"run - aborting recovery, rotator needs manual attention.")
        return False
    print(f"  {tag} fault recovery #{_rot_fault_counts[port]}: "
          f"stop -> home -> tare...")
    try: rot.stop()
    except Exception: pass
    time.sleep(0.3)
    try:
        rot.home(direction=0, settle_s=3.0)
        rot.tare()
    except Exception as e:
        print(f"  {tag} recovery FAILED during home/tare: {e}")
        return False
    # After home+tare the rotator is at the defined zero. Flush the cache
    # so the next move re-syncs via get_angle() or the absolute set.
    if cache is not None and cache_key is not None:
        cache.pop(cache_key, None)
    print(f"  {tag} recovered to home.")
    return True


def _safe_rot_call(rot, move_fn, abs_retry_fn,
                   cache=None, cache_key=None, label=""):
    """Run `move_fn()`. On a rotator fault, recover (home+tare) and run
    `abs_retry_fn()` which should perform an absolute move (no delta-from-
    cache, since the cache is now invalidated).

    Two-level recovery: if the first retry also hits a rotator fault, we
    run recovery once more and retry again. Only after TWO failed recoveries
    does the exception propagate. This guards against transient faults
    cascading (e.g., rotator throws 'sensor error' during recovery retry).
    Non-fault exceptions always propagate immediately.
    """
    for attempt in range(2):
        try:
            return move_fn() if attempt == 0 else abs_retry_fn()
        except Exception as e:
            if not _is_rotator_fault(e):
                raise
            tag = f"[ROT {label or rot.port}]"
            print(f"  {tag} fault '{e}' on attempt {attempt + 1}/2")
            if not _rot_recover(rot, cache, cache_key, label=label):
                if attempt == 1:
                    raise
                # First recovery failed but try once more from scratch
                continue
    # Final attempt: absolute retry after 2 recoveries
    return abs_retry_fn()


def _probe_move_rel(rot, target_deg, current_cache, cache_key,
                    settle_s=QUICK_SETTLE_S):
    """Relative move from cached position. No get_angle query before the
    move - saves one serial round-trip per probe (~100 ms). Used inside
    null_2d_parabolic where we know the current position exactly.

    On a rotator fault: auto-home + retry via absolute set_angle.
    Falls back to absolute set_angle on cache miss.
    """
    tgt = target_deg % 360.0

    def _do():
        cur = current_cache.get(cache_key)
        if cur is None:
            rot.set_angle(tgt, settle_s=settle_s, read_back=False)
            current_cache[cache_key] = tgt
            return tgt
        delta = ((tgt - cur + 540.0) % 360.0) - 180.0
        if abs(delta) < 0.01:
            return cur
        rot.shift_angle(delta, settle_s=settle_s)
        current_cache[cache_key] = tgt
        return tgt

    def _retry_abs():
        rot.set_angle(tgt, settle_s=settle_s, read_back=False)
        current_cache[cache_key] = tgt
        return tgt

    return _safe_rot_call(rot, _do, _retry_abs,
                          cache=current_cache, cache_key=cache_key,
                          label=cache_key)


def _quick_move_rot(rot, target_deg, current_cache, cache_key,
                    settle_s=QUICK_SETTLE_S):
    """Absolute move for null probes: no backlash, no readback, short
    settle, skip redundant. ~150 ms per move vs ~700 ms for safe_move_abs.

    On a rotator fault: auto-home + retry the same absolute move.
    """
    tgt = target_deg % 360.0
    cur = current_cache.get(cache_key)
    if cur is not None and abs(((tgt - cur + 540.0) % 360.0) - 180.0) < 0.05:
        return cur

    def _do():
        rot.set_angle(tgt, settle_s=settle_s, read_back=False)
        current_cache[cache_key] = tgt
        return tgt

    return _safe_rot_call(rot, _do, _do,
                          cache=current_cache, cache_key=cache_key,
                          label=cache_key)


def _warm_move_rot(rot, target_deg, current_cache=None, cache_key=None):
    """Warm-start move: the previous pixel's null angles.

    Always uses absolute set_angle (drift-safe). The internal get_angle
    probe in set_angle lets us skip the move if already close, but we
    issue the set_angle anyway - it's a single ~30 ms round-trip for a
    tiny/zero delta and avoids any cache staleness.

    On a rotator fault: auto-home + retry.
    """
    tgt = target_deg % 360.0

    def _do():
        rot.set_angle(tgt, settle_s=QUICK_SETTLE_S, read_back=False)
        if current_cache is not None and cache_key is not None:
            current_cache[cache_key] = tgt
        return tgt

    return _safe_rot_call(rot, _do, _do,
                          cache=current_cache, cache_key=cache_key,
                          label=cache_key)


def _precise_move_rot(rot, target_deg, current_cache=None, cache_key=None):
    """Final approach to the solved-for null optimum.

    Uses ABSOLUTE set_angle (not shift_angle) for both the backlash prep
    and the final approach. set_angle queries the rotator's true position
    and issues a bounded mr delta, so cumulative internal-counter drift
    from hundreds of relative moves over the campaign cannot push the
    rotator into an "out of range" state. Costs ~100 ms extra per move
    (one get_angle round-trip) - worth it for campaign robustness.

    Backlash prep still happens (single overshoot below target, then
    approach from below) so mechanical hysteresis is repeatable. ~600 ms.

    On a rotator fault: auto-home + retry the same absolute sequence.
    """
    tgt = target_deg % 360.0
    backlash_target = (tgt - PRECISE_BACKLASH_DEG) % 360.0

    def _do():
        rot.set_angle(backlash_target, settle_s=QUICK_SETTLE_S, read_back=False)
        rot.set_angle(tgt, settle_s=MEDIUM_SETTLE_S, read_back=False)
        if current_cache is not None and cache_key is not None:
            current_cache[cache_key] = tgt
        return tgt

    return _safe_rot_call(rot, _do, _do,
                          cache=current_cache, cache_key=cache_key,
                          label=cache_key)


def _medium_move_rot(rot, target_deg, current_cache=None, cache_key=None):
    """Un-chunked backlash-compensated move for brighten/dim (5-180 deg).

    Uses absolute set_angle (same drift-avoidance rationale as
    _precise_move_rot). ~0.5 s for 30 deg, ~1.5 s for 90 deg.

    On a rotator fault: auto-home + retry.
    """
    target = target_deg % 360.0
    backlash_target = (target - MEDIUM_BACKLASH_DEG) % 360.0

    def _do():
        rot.set_angle(backlash_target, settle_s=MEDIUM_SETTLE_S, read_back=False)
        rot.set_angle(target, settle_s=MEDIUM_SETTLE_S, read_back=False)
        if current_cache is not None and cache_key is not None:
            current_cache[cache_key] = target
        return target

    return _safe_rot_call(rot, _do, _do,
                          cache=current_cache, cache_key=cache_key,
                          label=cache_key)


def _move_pair(rot_qwp, rot_anl, q, a, cache, move_fn=_probe_move_rel):
    """Move QWP and ANL concurrently (different COM ports -> no bus clash)."""
    _parallel_move(
        lambda: move_fn(rot_qwp, q, cache, "qwp"),
        lambda: move_fn(rot_anl, a, cache, "anl"),
    )


def _probe_read(detector, n=1, auto_scale=False):
    """Fast scope read for probe points. auto_scale=False skips the probe
    read that precedes every averaged read - safe during the parabolic fit
    because VDiv was already selected on the first (centre) probe and the
    power excursions within +-1 deg of null are small.

    A missing/non-finite read raises immediately. If a valid read is above
    70% of full scale, the caller should re-enable auto_scale for the next
    probe.
    """
    v, std, pw, vdiv = detector.read_averaged(n=n, auto_scale=auto_scale)
    try:
        voltage = float(v)
        power = float(pw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Oscilloscope detector telemetry unavailable during optical null probe"
        ) from exc
    if not (np.isfinite(voltage) and np.isfinite(power)):
        raise RuntimeError(
            "Oscilloscope detector telemetry unavailable during optical null probe"
        )
    return max(0.0, power), voltage, vdiv


def null_2d_parabolic(detector, rot_qwp, rot_anl, q0, a0,
                      step=NULL_2D_STEP_COARSE_DEG, log_rows=None,
                      cache=None):
    """Fit a 2D quadratic to 6 probes and solve grad P = 0.

    Near the null, P(q, a) is a tilted paraboloid:
        P = F + D*dq + E*da + A*dq^2 + B*da^2 + 2C*dq*da
    6 probes give 6 equations -> closed-form solution for the minimum.

    Speed optimisations:
      - Parallel QWP+ANL moves (different COM ports) via _parallel_move.
      - Relative moves from cached position (no get_angle per probe).
      - Single scope read per probe (n=1); the fit naturally averages 6
        probes, and the verify read uses n=2 for the final value.
      - auto_scale only on the first probe (VDiv set once, then frozen).
      - Final optimum move uses _precise_move_rot (backlash prep) so the
        returned angles are mechanically accurate for the next warm start.

    Cost: 6 probes + 1 verify ~ 7 scope reads, parallel moves ~ 4 settles.
    Typical total time: ~1.5-2 s (was ~7 s serially).

    Falls back to the minimum of the probed points if the Hessian is
    non-positive-definite or the solution extrapolates too far (>3*step).
    """
    # 6 unique probes for the 6-parameter 2D quadratic fit. Order chosen
    # so consecutive probes share an axis (parallel move dominated by the
    # axis that actually changes).
    probes = [
        (0.0, 0.0),
        (+step, 0.0),
        (-step, 0.0),
        (0.0, +step),
        (0.0, -step),
        (+step, +step),
    ]
    if cache is None:
        cache = {}
    dqs, das, ps = [], [], []
    first = True
    for dq, da in probes:
        q = wrap180(q0 + dq)
        a = wrap180(a0 + da)
        _move_pair(rot_qwp, rot_anl, q, a, cache, move_fn=_probe_move_rel)
        # First probe: auto-scale to lock in VDiv; subsequent probes use the
        # same VDiv (power near null is stable within 1 deg).
        p, v, vdiv = _probe_read(detector, n=NULL_PROBE_N, auto_scale=first)
        first = False
        dqs.append(dq); das.append(da); ps.append(p)
        if log_rows is not None:
            log_rows.append(("2D", float(q), float(a), float(p)))

    dqs_a = np.array(dqs); das_a = np.array(das); ps_a = np.array(ps)
    X = np.column_stack([
        np.ones(6), dqs_a, das_a,
        dqs_a ** 2, das_a ** 2, 2 * dqs_a * das_a,
    ])
    fit_ok = False
    try:
        F, D, E, A, B, C = np.linalg.lstsq(X, ps_a, rcond=None)[0]
        # Positive-definite check: minimum exists if A>0 and A*B - C^2 > 0
        det_H = A * B - C * C
        if A <= 0 or det_H <= 0:
            raise ValueError("non-PD Hessian")
        # Solve [[2A, 2C],[2C, 2B]] * [dq, da] = [-D, -E]
        dq_opt, da_opt = np.linalg.solve(
            [[2 * A, 2 * C], [2 * C, 2 * B]], [-D, -E])
        if abs(dq_opt) > 3 * step or abs(da_opt) > 3 * step:
            raise ValueError("extrapolation too far")
        fit_ok = True
    except Exception:
        # Best probed point as fallback
        i = int(np.argmin(ps_a))
        dq_opt, da_opt = float(dqs_a[i]), float(das_a[i])

    q_opt = wrap180(q0 + dq_opt)
    a_opt = wrap180(a0 + da_opt)
    p_min_probed = float(np.min(ps_a))
    # Final move to the solved-for optimum uses backlash prep + auto-scale +
    # averaged read so the returned (q, a, P) is mechanically and
    # photometrically accurate - this seeds the next pixel's warm start.
    _parallel_move(
        lambda: _precise_move_rot(rot_qwp, q_opt, cache, "qwp"),
        lambda: _precise_move_rot(rot_anl, a_opt, cache, "anl"),
    )
    p_opt, _, _ = _probe_read(detector, n=NULL_SCOPE_AVG, auto_scale=True)
    if log_rows is not None:
        log_rows.append(("VERIFY", float(q_opt), float(a_opt), float(p_opt)))

    # --- Robustness: polish step if fit missed or fell back to extrapolation.
    # Trigger: verify power is worse than the best probed point by more than
    # NULL_POLISH_TRIGGER_RATIO, OR the fit fell back to argmin-probe (fit_ok
    # False). Do a 4-point cardinal refinement at step/NULL_POLISH_STEP_DIV
    # centered on the best known point and take the actual minimum.
    triggered = (not fit_ok) or (p_opt > NULL_POLISH_TRIGGER_RATIO * p_min_probed)
    if triggered:
        # Start the polish from whichever is currently better: the fit
        # optimum or the best probed point.
        if p_opt <= p_min_probed:
            q_best, a_best, p_best = q_opt, a_opt, p_opt
        else:
            i_min = int(np.argmin(ps_a))
            q_best = wrap180(q0 + float(dqs_a[i_min]))
            a_best = wrap180(a0 + float(das_a[i_min]))
            # Move there, verify (this also becomes our baseline for polishing).
            _parallel_move(
                lambda: _precise_move_rot(rot_qwp, q_best, cache, "qwp"),
                lambda: _precise_move_rot(rot_anl, a_best, cache, "anl"),
            )
            p_best, _, _ = _probe_read(detector, n=NULL_PROBE_N, auto_scale=False)
            if log_rows is not None:
                log_rows.append(("FALLBACK", float(q_best), float(a_best), float(p_best)))

        # 4 cardinal probes at a finer step around the current best.
        pstep = step / NULL_POLISH_STEP_DIV
        for dq, da in [(+pstep, 0.0), (-pstep, 0.0),
                       (0.0, +pstep), (0.0, -pstep)]:
            q = wrap180(q_best + dq)
            a = wrap180(a_best + da)
            _move_pair(rot_qwp, rot_anl, q, a, cache, move_fn=_probe_move_rel)
            p, _, _ = _probe_read(detector, n=NULL_PROBE_N, auto_scale=False)
            if log_rows is not None:
                log_rows.append(("POLISH", float(q), float(a), float(p)))
            if p < p_best:
                q_best, a_best, p_best = q, a, p

        # Land precisely at whichever angles actually gave the lowest read
        # (backlash-compensated) and re-verify with full averaging.
        _parallel_move(
            lambda: _precise_move_rot(rot_qwp, q_best, cache, "qwp"),
            lambda: _precise_move_rot(rot_anl, a_best, cache, "anl"),
        )
        p_final, _, _ = _probe_read(detector, n=NULL_SCOPE_AVG, auto_scale=True)
        if log_rows is not None:
            log_rows.append(("POLISH_VERIFY", float(q_best), float(a_best), float(p_final)))
        # Only accept the polished result if it's genuinely better (noise
        # could make the final verify slightly higher even at correct angles).
        if p_final < p_opt:
            return q_best, a_best, p_final, p_min_probed
        # Otherwise the original fit optimum was actually fine; return it.
        # We're already at q_best, so move back to q_opt for consistency.
        _parallel_move(
            lambda: _precise_move_rot(rot_qwp, q_opt, cache, "qwp"),
            lambda: _precise_move_rot(rot_anl, a_opt, cache, "anl"),
        )
        p_opt2, _, _ = _probe_read(detector, n=NULL_SCOPE_AVG, auto_scale=True)
        if log_rows is not None:
            log_rows.append(("REVERT", float(q_opt), float(a_opt), float(p_opt2)))
        return q_opt, a_opt, min(p_opt, p_opt2), p_min_probed

    return q_opt, a_opt, p_opt, p_min_probed


def first_null_fast(detector, rot_qwp, rot_anl, q0, a0, log_rows=None):
    """First null: coarse-step 2D parabolic, then fine-step refine.

    ~14 scope reads total. Replaces the old ~80-read null_qwp_analyzer
    path for the pixel-1 reference null. Cache is threaded through so the
    fine pass inherits the coarse pass's position knowledge (no extra
    get_angle queries between passes).
    """
    cache = {}
    q1, a1, p1, p1_min = null_2d_parabolic(
        detector, rot_qwp, rot_anl, q0, a0,
        step=NULL_2D_STEP_COARSE_DEG, log_rows=log_rows, cache=cache)
    # One more pass at finer step to tighten.
    q2, a2, p2, p2_min = null_2d_parabolic(
        detector, rot_qwp, rot_anl, q1, a1,
        step=NULL_2D_STEP_FINE_DEG, log_rows=log_rows, cache=cache)
    # Keep whichever refinement actually gave a deeper null.
    if p2 <= p1:
        return q2, a2, p2
    return q1, a1, p1


# ========================== FAST RE-NULL ================================

def fast_renull(detector, rot_qwp, rot_anl,
                q_start, a_start, p_ref,
                log_rows=None):
    """Per-pixel re-null via a single 2D parabolic fit.

    Near the null the power landscape is a tilted 2D paraboloid, so 6 probe
    reads + 1 verify read find the minimum in closed form.

    Warm-start: _warm_move_rot parallel move on both rotators. Skips if
    already within WARM_SKIP_TOL_DEG (typical for pixels 2-100 since the
    previous pixel's null is ~0.1 deg away). No backlash / no chunking /
    no 1.6 s scan pause - those only matter for precision sweeps. The 2D
    parabolic fit doesn't care if the warm-start lands 0.1 deg off.

    Returns dict with q, a, p_before, p_after, method, n_evals, escalated.
    If the probed minimum is >> p_ref we escalate to the coarse-then-fine
    two-step 2D null which is still much cheaper than the old full null.
    """
    q0 = wrap180(q_start)
    a0 = wrap180(a_start)
    # Parallel warm move: skips entirely if within 0.1 deg of target,
    # else single un-chunked move. ~0.3 s vs ~6 s for safe_move_abs x2.
    cache = {}
    _parallel_move(
        lambda: _warm_move_rot(rot_qwp, q0, cache, "qwp"),
        lambda: _warm_move_rot(rot_anl, a0, cache, "anl"),
    )
    p0 = scope_power_w(detector)
    if log_rows is not None:
        log_rows.append(("START", float(q0), float(a0), float(p0)))

    ref = max(p_ref, 1e-15)
    ratio = p0 / ref

    if ratio <= FAST_NULL_ESCALATE_RATIO:
        q, a, p_final, _ = null_2d_parabolic(
            detector, rot_qwp, rot_anl, q0, a0,
            step=NULL_2D_STEP_FINE_DEG, log_rows=log_rows, cache=cache)
        method = "2d_parabolic"
        escalated = False
    else:
        # Drift large - coarse then fine (~14 reads)
        q1, a1, _, _ = null_2d_parabolic(
            detector, rot_qwp, rot_anl, q0, a0,
            step=NULL_2D_STEP_COARSE_DEG, log_rows=log_rows, cache=cache)
        q, a, p_final, _ = null_2d_parabolic(
            detector, rot_qwp, rot_anl, q1, a1,
            step=NULL_2D_STEP_FINE_DEG, log_rows=log_rows, cache=cache)
        method = "2d_coarse_then_fine"
        escalated = True

    n_evals = len(log_rows) if log_rows is not None else -1
    return dict(q=q, a=a, p_before=p0, p_after=p_final,
                method=method, n_evals=n_evals, escalated=escalated)


class _FakeDetector:
    """Adapter so null_qwp_analyzer() (which expects DetectorTekTBS with
    .read_power_w_stable()) works with stage_calibration.ScopeDetector.
    """
    def __init__(self, sc_detector):
        self._sc = sc_detector

    def read_power_w_stable(self):
        v, std, pw, vdiv = self._sc.read_averaged(n=NULL_SCOPE_AVG, auto_scale=True)
        if pw is None:
            return 0.0, 0.0
        return float(pw), float(v if v is not None else 0.0)


# ===================== FULL 3-STEP CALIBRATION ===========================
# Runs the same 3-step calibration the main script / sample_calibration.py
# uses (analyzer sweep + QWP sweep + null). `phase` switches the prompt text
# and output filename between AIR (sample OUT of beam) and SAMPLE (sample IN).

def run_full_calibration(detector, rot_hwp, rot_qwp, rot_anl, out_dir,
                         phase,  # "air" or "sample"
                         start_anl_for_null=None,
                         start_qwp_for_null=None,
                         live_view=None,
                         motion_logger=None):
    """One 3-step polarisation calibration.

    `phase="air"`    -> prompts say SAMPLE OUT OF BEAM (reference cal).
    `phase="sample"` -> prompts say SAMPLE IN BEAM   (through-sample cal).

    Writes CSVs + JSON into out_dir. Returns the calibration pack dict.

    If `start_anl_for_null` / `start_qwp_for_null` are given, they seed the
    step-3 null as a warm start (useful when running the sample-in cal
    right after an air cal - the angles are close).
    """
    assert phase in ("air", "sample")
    tag = phase.upper()
    sample_state_text = ("SAMPLE OUT OF BEAM" if phase == "air"
                         else "SAMPLE IN BEAM")
    os.makedirs(out_dir, exist_ok=True)

    def read_pw():
        v, std, pw, vdiv = detector.read_averaged(n=NULL_SCOPE_AVG, auto_scale=True)
        return (float(pw) if pw is not None else 0.0,
                float(v)  if v  is not None else 0.0)

    if motion_logger:
        motion_logger.log_event("CAL", f"PHASE_{tag}_START",
                                f"calibration phase={phase}")

    print(f"\n=== {tag} CAL STEP 1: Analyzer sweep (QWP removed) ===")
    _lv_input(live_view,
              f"  Ensure {sample_state_text}, E-FIELD OFF, QWP REMOVED. "
              f"Press Enter...")
    safe_move_abs(rot_hwp, CONFIRM_HWP_FIXED,
                  motion_logger=motion_logger, rot_name="HWP", move_type="CAL_SETUP")
    anl_angles = np.arange(ANL_SWEEP_RANGE[0], ANL_SWEEP_RANGE[1] + 1e-9, ANL_SWEEP_STEP)
    data_anl = _lv_run(live_view, sweep_angles, rot_anl, anl_angles, read_pw,
                       motion_logger=motion_logger, rot_name="ANL")
    _save_csv(os.path.join(out_dir, "analyzer_sweep_QWPout.csv"),
              ["Analyzer_deg", "Power_W", "Volt"], data_anl)
    fit_anl = fit_cos2(data_anl[:, 0], data_anl[:, 1])
    theta_off = fit_anl["theta_off_deg"] % 180.0
    Pmax_fit = fit_anl["Pmax"]
    Pbg_fit  = fit_anl["Pbg"]

    i_min = int(np.argmin(data_anl[:, 1]))
    i_max = int(np.argmax(data_anl[:, 1]))
    a_ref_min, _ = _lv_run(live_view, refine_extremum_1d,
                           rot_anl, float(data_anl[i_min, 0]),
                           REFINE_WINDOW, REFINE_STEP, 'min', read_pw,
                           motion_logger=motion_logger, rot_name="ANL")
    a_ref_max, _ = _lv_run(live_view, refine_extremum_1d,
                           rot_anl, float(data_anl[i_max, 0]),
                           REFINE_WINDOW, REFINE_STEP, 'max', read_pw,
                           motion_logger=motion_logger, rot_name="ANL")
    print(f"  Analyzer extinction: {a_ref_min:.2f} deg")
    print(f"  Analyzer parallel  : {a_ref_max:.2f} deg")

    print(f"\n=== {tag} CAL STEP 2: QWP sweep (QWP inserted) ===")
    _lv_input(live_view,
              f"  Insert QWP (analyzer at parallel). {sample_state_text}. "
              f"Press Enter...")
    safe_move_abs(rot_anl, a_ref_max,
                  motion_logger=motion_logger, rot_name="ANL", move_type="CAL_SETUP")
    qwp_angles = np.arange(QWP_SWEEP_RANGE[0], QWP_SWEEP_RANGE[1] + 1e-9, QWP_SWEEP_STEP)
    data_qwp = _lv_run(live_view, sweep_angles,
                       rot_qwp, qwp_angles, read_pw, period=180.0,
                       motion_logger=motion_logger, rot_name="QWP")
    _save_csv(os.path.join(out_dir, "qwp_sweep_QWPin.csv"),
              ["QWP_deg", "Power_W", "Volt"], data_qwp)
    fit_q = fit_qwp_cos4(data_qwp[:, 0], data_qwp[:, 1])
    gamma0 = fit_q["gamma0_deg"]
    delta_deg = fit_q["delta_est_deg"]
    print(f"  gamma0 (fast-axis zero): {gamma0:.2f} deg")
    print(f"  delta  (retardance)   : {delta_deg:.2f} deg")

    # STEP 3 only runs with sample IN. Nulling with sample OUT is pointless
    # for this workflow - the scan references the sample-in null, not air.
    # For the air phase we still want the sweep values (gamma0 and the
    # analyzer parallel/extinction angles) since they seed the sample null.
    if phase == "sample":
        print(f"\n=== {tag} CAL STEP 3: QWP+Analyzer null (full) ===")
        null_log = os.path.join(out_dir, "full_null_path.csv")
        q0 = start_qwp_for_null if start_qwp_for_null is not None else gamma0
        a0 = start_anl_for_null if start_anl_for_null is not None else a_ref_min
        q_null, a_null, p_null = _lv_run(
            live_view, null_qwp_analyzer,
            _FakeDetector(detector), rot_qwp, rot_anl,
            q0, a0,
            FULL_NULL_SPAN_DEG, FULL_NULL_SPAN_DEG,
            FULL_NULL_CYCLES, FULL_NULL_TOL_DEG,
            null_log,
        )
        ext_dB = 10.0 * np.log10(Pmax_fit / max(p_null, 1e-15) + 1e-15)
        print(f"  NULL: q={q_null:.3f} deg, a={a_null:.3f} deg, "
              f"P={p_null:.3e} W, extinction={ext_dB:.1f} dB")
    else:
        print(f"\n  (Step 3 skipped for air phase - null happens with sample IN)")
        q_null = a_null = p_null = None
        ext_dB = None

    pack = {
        "calibration_type": ("air_Efield_off" if phase == "air"
                             else "sample_in_Efield_off"),
        "HWP_fixed_deg": float(CONFIRM_HWP_FIXED),
        "Analyzer_extinction_deg_QWPout": float(a_ref_min),
        "Analyzer_parallel_deg_QWPout":  float(a_ref_max),
        "Analyzer_theta_off_fit_deg_QWPout": float(theta_off),
        "Analyzer_Pbg_fit_W_QWPout": float(Pbg_fit),
        "QWP_gamma0_fit_deg": float(gamma0),
        "QWP_delta_deg": float(delta_deg),
        "Null_QWP_deg":       float(q_null) if q_null is not None else None,
        "Null_Analyzer_deg":  float(a_null) if a_null is not None else None,
        "Null_Pmin_W":        float(p_null) if p_null is not None else None,
        "Extinction_dB_vs_QWPoutMax": (float(ext_dB) if ext_dB is not None else None),
        "Detector": {
            "PDA_gain_dB": PDA_GAIN_DB,
            "PDA_load": PDA_LOAD,
            "A_per_W": RESP_A_PER_W,
            "V_to_W": V_TO_W,
        },
    }
    json_name = ("air_calibration.json" if phase == "air"
                 else "sample_calibration.json")
    save_calibration_pack(pack, path=os.path.join(out_dir, json_name))
    if motion_logger:
        motion_logger.log_event("CAL", f"PHASE_{tag}_END",
                                f"saved {json_name}")
    return pack


# =========================== CSV HELPERS ================================

def _save_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)

PIXEL_CSV_HEADER = [
    "pixel", "i", "j",
    "nominal_x_global_mm", "nominal_y_global_mm",
    "nominal_x_motor_mm", "nominal_y_motor_mm",
    "aligned_x_motor_mm", "aligned_y_motor_mm",
    "p_bright_W",
    "q_null_deg", "a_null_deg",
    "p_null_W", "p_null_before_W",
    "dq_from_ref_deg", "da_from_ref_deg",
    "renull_method", "renull_n_evals", "renull_escalated",
    "flagged_low_quality",
    "timestamp_utc",
]


def append_pixel_row(csv_path, row_dict):
    """Append one row to the pixel results CSV (create with header if new)."""
    new_file = not os.path.isfile(csv_path)
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(PIXEL_CSV_HEADER)
        w.writerow([row_dict.get(k, "") for k in PIXEL_CSV_HEADER])


def save_renull_log(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "q_deg", "a_deg", "power_W"])
        for r in rows:
            w.writerow(r)


# ============================ HEATMAPS ==================================

def save_campaign_heatmaps(pixel_rows, out_dir, grid_n):
    """Generate P_null, p_bright, and angle-drift heatmaps from pixel rows."""
    if not pixel_rows:
        return
    p_null = np.full((grid_n, grid_n), np.nan)
    p_bright = np.full((grid_n, grid_n), np.nan)
    dq = np.full((grid_n, grid_n), np.nan)
    da = np.full((grid_n, grid_n), np.nan)
    for r in pixel_rows:
        i, j = r["i"], r["j"]
        p_null[j, i] = r["p_null_W"]
        p_bright[j, i] = r["p_bright_W"]
        dq[j, i] = r["dq_from_ref_deg"]
        da[j, i] = r["da_from_ref_deg"]

    for data, name, label in [
        (p_null,   "p_null_heatmap.png",   "P_null (W)"),
        (p_bright, "p_bright_heatmap.png", "P_bright (W)"),
        (dq,       "dq_heatmap.png",       "QWP drift from ref (deg)"),
        (da,       "da_heatmap.png",       "ANL drift from ref (deg)"),
    ]:
        plt.figure()
        plt.imshow(data, origin="lower", aspect="equal")
        plt.colorbar(label=label)
        plt.title(label)
        plt.xlabel("i (column)")
        plt.ylabel("j (row)")
        plt.tight_layout()
        plt.savefig(os.path.join(out_dir, name), dpi=150)
        plt.close()


# ============================== MAIN ====================================

# ------- LiveView-friendly blocking helpers (Windows OpenCV needs tick()
#         to run on the main thread, so every long blocking call must pump it).

def _lv_input(live_view, prompt):
    """input() that keeps the camera ticking while waiting."""
    if live_view is not None and getattr(live_view, "available", False):
        return sc.wait_for_input(live_view, prompt)
    return input(prompt).strip().lower()


def _lv_input_raw(live_view, prompt):
    """Same as _lv_input but preserves case (for path entry etc.)."""
    if live_view is not None and getattr(live_view, "available", False):
        # Pump tick() until user submits something.
        live_view.get_input(prompt)
        while True:
            live_view.tick()
            cmd = live_view.poll_input()
            if cmd is not None:
                return cmd.strip()
    return input(prompt).strip()


def _lv_run(live_view, func, *args, **kwargs):
    """Run blocking `func` in a worker thread; main thread pumps tick()."""
    if live_view is not None and getattr(live_view, "available", False):
        return sc.run_with_display(live_view, func, *args, **kwargs)
    return func(*args, **kwargs)


def _lv_sleep(live_view, seconds):
    """Sleep while keeping the camera ticking."""
    if live_view is not None and getattr(live_view, "available", False):
        sc.spin_display(live_view, seconds)
    else:
        time.sleep(seconds)


def _prompt_run_name():
    raw = input("\n  Campaign run name (blank = timestamp): ").strip()
    if not raw:
        return datetime.now().strftime("%Y%m%d_%H%M%S")
    return "".join(c if (c.isalnum() or c in ("-", "_")) else "_" for c in raw).strip("_")


def _prompt_cal_choice(label, cache_dir, filename_suffix, live_view=None):
    """Generic prompt for loading a cached cal JSON or running fresh."""
    existing = sorted(
        [f for f in os.listdir(cache_dir) if f.endswith(filename_suffix)],
        key=lambda n: os.path.getmtime(os.path.join(cache_dir, n)),
        reverse=True,
    ) if os.path.isdir(cache_dir) else []

    print("\n" + "=" * 60)
    print(label)
    print("=" * 60)
    print(f"  [n] Run a fresh {label.lower()} now")
    print(f"  [l] Load a cached JSON (enter path)")
    if existing:
        print(f"  Recent cached files in {cache_dir}/:")
        for i, f in enumerate(existing[:5], 1):
            print(f"    [{i}] {f}")
    choice = _lv_input(live_view, "  Select [n/l/1-5]: ")
    if choice == "n":
        return None
    if choice == "l":
        path = _lv_input_raw(live_view, "  Path to calibration JSON: ")
        return load_calibration_pack(path)
    if choice.isdigit() and existing:
        idx = int(choice) - 1
        if 0 <= idx < len(existing):
            return load_calibration_pack(os.path.join(cache_dir, existing[idx]))
    print("  Defaulting to: run fresh.")
    return None


def main():
    # Reset per-run rotator fault counter.
    _rot_fault_counts.clear()

    print("=" * 70)
    print(f"  POCKELS CHIP CAMPAIGN    [code: {CAMPAIGN_CODE_VERSION}]")
    print("  Flow:")
    print("    0. Stage home + move to centre")
    print("    1. PRE-SAMPLE SWEEPS (sample OUT): analyzer + QWP sweeps, NO null")
    print("    2. Insert sample, move to pixel 1, stage align")
    print("    3. FIRST NULL at pixel 1 (sample IN, no sweeps)")
    print("       -> q_ref, a_ref, p_ref reference")
    print("    4. 100-pixel scan: align + fast re-null per pixel")
    print("=" * 70)

    run_name = _prompt_run_name()
    run_dir = os.path.join(CAMPAIGN_ROOT, run_name)
    os.makedirs(run_dir, exist_ok=True)
    renull_dir = os.path.join(run_dir, "renull_logs")
    os.makedirs(renull_dir, exist_ok=True)
    pixel_csv = os.path.join(run_dir, "pixel_results.csv")
    pixel_json = os.path.join(run_dir, "pixel_results.json")
    print(f"  Output: {os.path.abspath(run_dir)}")

    # --- Motion logger: one CSV capturing every actuator move ---
    motion_log_path = os.path.join(run_dir, "motion_log.csv")
    motion_logger = MotionLogger(motion_log_path, scan_name=run_name)
    print(f"  Motion log: {os.path.abspath(motion_log_path)}")

    # ---- Rotators ----
    print("\n[ROT] Connecting...")
    rot_hwp = ElliptecRotator(port=PORT_HWP, address=ADDR_HWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_anl = ElliptecRotator(port=PORT_ANL, address=ADDR_ANL,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    rot_qwp = ElliptecRotator(port=PORT_QWP, address=ADDR_QWP,
                              verbose=False, settle_time=MOVE_SETTLE_S)
    for rot, name in [(rot_hwp, "HWP"), (rot_anl, "ANL"), (rot_qwp, "QWP")]:
        rot.home(direction=0, settle_s=3.0)
        rot.tare()
        # Calmer physical motion; reduces inertial overshoot and flicker.
        try:
            rot.set_velocity(ROTATOR_VELOCITY_PCT)
            print(f"  [{name}] homed @ {rot.get_angle():.2f} deg, "
                  f"velocity {ROTATOR_VELOCITY_PCT}%")
        except Exception as e:
            print(f"  [{name}] homed @ {rot.get_angle():.2f} deg "
                  f"(set_velocity failed: {e})")
        motion_logger.log_event(name, "HOME", f"homed and tared")

    # ---- Scope ----
    print("\n[SCOPE] Connecting...")
    detector = sc.ScopeDetector()
    detector.connect()

    # ---- Laser (optional) ----
    laser = None
    if USE_LASER and KLS1550 is not None:
        try:
            serial = KLS1550.find_first() if KLS_SERIAL is None else KLS_SERIAL
            if serial:
                laser = KLS1550(serial)
                laser.connect()
                laser.set_power_absolute(KLS_POWER_MW)
                laser.on()
                print(f"  [LASER] ON @ {KLS_POWER_MW} mW "
                      f"(stabilizing {LASER_STABILIZE_S:.1f}s)")
                time.sleep(LASER_STABILIZE_S)
        except Exception as e:
            print(f"  [LASER] WARN: {e}")

    # ================== PHASE 0: STAGE BRING-UP (home + centre) ================
    # Done FIRST so the stage is at a known position before anything optical.
    # If the Kinesis side is broken you find out before spending 10 min on a cal.
    print("\n" + "#" * 70)
    print("# PHASE 0: STAGE BRING-UP  -  home X/Y + move to centre (12.5, 12.5)")
    print("#" * 70)
    print("\n[STAGE] Connecting Kinesis...")
    sc.DeviceManagerCLI.BuildDeviceList()
    dev_x = sc.initialize_device(sc.STAGE_SERIAL_X)
    dev_y = sc.initialize_device(sc.STAGE_SERIAL_Y)

    print("\n[STAGE] Homing both axes...")
    sc.home_device(dev_x, sc.STAGE_SERIAL_X)
    sc.home_device(dev_y, sc.STAGE_SERIAL_Y)

    print("\n[STAGE] Moving to centre (12.5, 12.5) mm...")
    cx = sc.safe_move_to(dev_x, 12.5, "X")
    cy = sc.safe_move_to(dev_y, 12.5, "Y")
    err_x = abs(cx - 12.5) * 1000.0
    err_y = abs(cy - 12.5) * 1000.0
    print(f"  At ({cx:.4f}, {cy:.4f}) mm  "
          f"(err: {err_x:.0f} um X, {err_y:.0f} um Y)")
    motion_logger.log_event("STAGE", "HOME", "homed X and Y")
    motion_logger.log_move("STAGE_X", "CENTRE", 12.5, cx, "mm")
    motion_logger.log_move("STAGE_Y", "CENTRE", 12.5, cy, "mm")

    # Start LiveView BEFORE the centre-error prompt so the very first
    # _lv_input pumps tick() and the windows actually get frames.
    live_view = sc.LiveView(detector=detector)
    live_view.start()

    if err_x > 100 or err_y > 100:
        print("  WARNING: Stage position error > 100 um after homing!")
        if _lv_input(live_view, "  Continue anyway? [y/n] > ") != "y":
            return

    pixels = sc.build_grid()
    total = len(pixels)
    grid_n = sc.SCAN_POINTS_PER_AXIS
    print(f"\n  Grid: {grid_n}x{grid_n} = {total} pixels, "
          f"{sc.SCAN_SIZE_MM}x{sc.SCAN_SIZE_MM} mm")

    # ================ PHASE 1: PRE-SAMPLE SWEEPS (sample OUT, no null) =========
    # Characterises the bare optical path: analyzer extinction/parallel angles
    # and QWP gamma0. No nulling here (nulling through empty beam is pointless).
    # These values seed the sample-in null so it converges from a good start.
    print("\n" + "#" * 70)
    print("# PHASE 1: PRE-SAMPLE SWEEPS  (sample OUT, QWP analysis only, no null)")
    print("#" * 70)
    air_cal = _prompt_cal_choice(
        label="PRE-SAMPLE SWEEPS (sample OUT)",
        cache_dir="calibration_results_preBTO",
        filename_suffix="_calibration.json",
        live_view=live_view)
    if air_cal is None:
        air_cal = run_full_calibration(
            detector, rot_hwp, rot_qwp, rot_anl,
            out_dir=os.path.join(run_dir, "air_sweeps"),
            phase="air",
            live_view=live_view,
            motion_logger=motion_logger)
        # Cache to shared directory so future runs can load without re-sweeping
        cache_dir = "calibration_results_preBTO"
        os.makedirs(cache_dir, exist_ok=True)
        cache_name = datetime.now().strftime("%d_%m_%Y_%H%M") + "_Cal_Full_calibration.json"
        save_calibration_pack(air_cal, path=os.path.join(cache_dir, cache_name))
        print(f"  Air calibration cached for reuse: {cache_name}")
    else:
        save_calibration_pack(air_cal,
                              path=os.path.join(run_dir, "air_sweeps_used.json"))
        motion_logger.log_event("CAL", "AIR_LOADED", "loaded cached air calibration")
    q_air = float(air_cal["QWP_gamma0_fit_deg"])
    a_air = float(air_cal["Analyzer_extinction_deg_QWPout"])
    a_parallel_air = float(air_cal["Analyzer_parallel_deg_QWPout"])
    print(f"\n  AIR sweeps: gamma0={q_air:.3f} deg, "
          f"a_extinction={a_air:.3f} deg, a_parallel={a_parallel_air:.3f} deg")

    # ================ PHASE 2: Insert sample + align to pixel 1 =================
    print("\n" + "#" * 70)
    print("# PHASE 2: Insert BTO sample, move to pixel 1, stage align")
    print("#" * 70)
    _lv_input(live_view,
              "\n  >>> INSERT the BTO sample at the stage centre, "
              "E-FIELD OFF. QWP is already inserted (from Phase 1). "
              "Press Enter when sample is mounted...")

    # Brighten: QWP at gamma0 (fast axis aligned with polarisation ->
    # passes through like no QWP), analyzer at the parallel angle from
    # Phase 1 -> maximum transmission -> stage align has a real peak.
    # HWP only moves to a precise fixed angle, so keep safe_move_abs for it.
    # QWP + ANL move in parallel (different COM ports) - saves ~1 s.
    safe_move_abs(rot_hwp, CONFIRM_HWP_FIXED,
                  motion_logger=motion_logger, rot_name="HWP", move_type="PHASE2_SETUP")
    _parallel_move(
        lambda: _medium_move_rot(rot_qwp, q_air),
        lambda: _medium_move_rot(rot_anl, a_parallel_air),
    )
    try:
        motion_logger.log_move("QWP", "PHASE2_SETUP", q_air,
                               rot_qwp.get_angle() or q_air, "deg")
        motion_logger.log_move("ANL", "PHASE2_SETUP", a_parallel_air,
                               rot_anl.get_angle() or a_parallel_air, "deg")
    except Exception:
        pass

    p1 = pixels[0]
    print(f"\n  Moving stage from centre to pixel 1 nominal "
          f"({p1['nominal_x_motor']:.4f}, {p1['nominal_y_motor']:.4f}) mm...")
    _lv_run(live_view, sc._safe_scan_move, dev_x, p1["nominal_x_motor"], "X")
    _lv_run(live_view, sc._safe_scan_move, dev_y, p1["nominal_y_motor"], "Y")
    sc.set_velocity_mode(dev_x, "X")
    sc.set_velocity_mode(dev_y, "Y")
    _lv_sleep(live_view, sc.SETTLE_TIME_S)
    motion_logger.log_move("STAGE_X", "PIX1_MOVE", p1["nominal_x_motor"],
                           sc.read_motor_pos(dev_x), "mm")
    motion_logger.log_move("STAGE_Y", "PIX1_MOVE", p1["nominal_y_motor"],
                           sc.read_motor_pos(dev_y), "mm")

    print("\n  Auto-aligning stage to pixel 1 signal...")
    first_align = _lv_run(live_view, sc.fast_peak_align,
                          dev_x, dev_y, detector, live_view)
    if first_align is None:
        print("  [ALIGN] Stage alignment FAILED at pixel 1. Aborting.")
        motion_logger.log_event("STAGE", "ALIGN_FAIL", "pixel 1 alignment failed")
        motion_logger.close()
        return
    p1_ax, p1_ay = first_align["best_x"], first_align["best_y"]
    print(f"  Pixel 1 aligned at ({p1_ax:.4f}, {p1_ay:.4f}) mm")
    motion_logger.log_move("STAGE_X", "PIX1_ALIGN", p1["nominal_x_motor"], p1_ax, "mm")
    motion_logger.log_move("STAGE_Y", "PIX1_ALIGN", p1["nominal_y_motor"], p1_ay, "mm")

    # ================ PHASE 3: FIRST NULL at pixel 1 (sample IN) ===============
    # No sweeps, no QWP insertion prompt - QWP has been in since Phase 1 Step 2.
    # Just do the full null through the sample, warm-started from the Phase 1
    # sweep values.
    print("\n" + "#" * 70)
    print("# PHASE 3: FIRST NULL at pixel 1  (full null, sample IN, no sweeps)")
    print("#" * 70)

    # Scope to fine range for the null read
    try:
        detector._set_vdiv(NULL_VDIV_HINT_V)
        _lv_sleep(live_view, sc.POST_SCALE_CHANGE_SETTLE_S)
    except Exception:
        pass

    # 2D parabolic null: ~14 scope reads, ~5 seconds. Much faster than the
    # old null_qwp_analyzer (3x cycles of separable 1D GS -> ~80 reads).
    null_log_rows = []
    q_ref, a_ref, p_ref = _lv_run(
        live_view, first_null_fast,
        detector, rot_qwp, rot_anl,
        q_air, a_air,
        null_log_rows,
    )
    # Save the path log
    with open(os.path.join(run_dir, "pixel1_null_path.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(["phase", "q_deg", "a_deg", "P_W"])
        for row in null_log_rows:
            w.writerow(row)
    print(f"\n  SAMPLE NULL REFERENCE (pixel 1): "
          f"q={q_ref:.3f} deg, a={a_ref:.3f} deg, P={p_ref:.3e} W "
          f"({len(null_log_rows)} scope reads)")
    motion_logger.log_move("QWP", "FIRST_NULL", q_air, q_ref, "deg",
                           notes=f"P_null={p_ref:.3e} W")
    motion_logger.log_move("ANL", "FIRST_NULL", a_air, a_ref, "deg",
                           notes=f"P_null={p_ref:.3e} W")

    # Persist the reference for reload / provenance
    ref_pack = {
        "calibration_type": "first_null_at_pixel_1",
        "HWP_fixed_deg": float(CONFIRM_HWP_FIXED),
        "Air_QWP_gamma0_deg": float(q_air),
        "Air_Analyzer_extinction_deg": float(a_air),
        "Air_Analyzer_parallel_deg": float(a_parallel_air),
        "Null_QWP_deg": float(q_ref),
        "Null_Analyzer_deg": float(a_ref),
        "Null_Pmin_W": float(p_ref),
        "Detector": {
            "PDA_gain_dB": PDA_GAIN_DB,
            "PDA_load": PDA_LOAD,
            "A_per_W": RESP_A_PER_W,
            "V_to_W": V_TO_W,
        },
    }
    save_calibration_pack(ref_pack,
                          path=os.path.join(run_dir, "first_null_reference.json"))

    # ==================== PHASE 4: 100-pixel scan + re-null ====================
    print("\n" + "#" * 70)
    print("# PHASE 4: 100-pixel scan  -  stage align + fast re-null per pixel")
    print("#" * 70)
    _lv_input(live_view, "  Press Enter to start the scan...")

    pixel_rows = []
    q_warm, a_warm = q_ref, a_ref  # warm-start chain seeds from sample cal
    motion_logger.log_event("SCAN", "PHASE4_START", f"100-pixel scan begins")

    try:
        for idx, pixel in enumerate(pixels):
            pix = pixel["pixel"]
            i, j = pixel["i"], pixel["j"]
            nom_xm = pixel["nominal_x_motor"]
            nom_ym = pixel["nominal_y_motor"]

            print(f"\n{'-' * 70}")
            print(f"  Pixel {pix}/{total}  [row {j}, col {i}]  "
                  f"nom motor ({nom_xm:.4f}, {nom_ym:.4f}) mm")
            motion_logger.log_event("SCAN", "PIXEL_START",
                                    f"pixel {pix}/{total} i={i} j={j}")

            # -- Preemptive rotator re-home every N pixels to keep the ELL14
            #    internal position counter from drifting into a firmware
            #    range boundary over hundreds of mr commands. Cheap insurance.
            if idx > 0 and idx % REHOME_EVERY_N_PIXELS == 0:
                print(f"    [ROT] preemptive re-home (pixel {pix}): "
                      f"resetting QWP+ANL internal counters...")
                def _rehome(rot, name):
                    try:
                        rot.home(direction=0, settle_s=3.0)
                        rot.tare()
                        # Re-apply reduced velocity (some firmware resets it on home).
                        try: rot.set_velocity(ROTATOR_VELOCITY_PCT)
                        except Exception: pass
                        print(f"    [ROT {name}] re-homed.")
                    except Exception as e:
                        print(f"    [ROT {name}] re-home FAILED: {e}")
                _parallel_move(
                    lambda: _rehome(rot_qwp, "QWP"),
                    lambda: _rehome(rot_anl, "ANL"),
                )
                # Move QWP+ANL back to the current warm-null via absolute
                # set_angle (drift-safe). This re-establishes the null
                # starting point for the next pixel's fast_renull.
                print(f"    [ROT] restoring to warm-null "
                      f"(q={q_warm:.3f}, a={a_warm:.3f})...")
                _parallel_move(
                    lambda: rot_qwp.set_angle(q_warm, settle_s=QUICK_SETTLE_S,
                                              read_back=False),
                    lambda: rot_anl.set_angle(a_warm, settle_s=QUICK_SETTLE_S,
                                              read_back=False),
                )
                motion_logger.log_event("QWP", "REHOME",
                                        f"preemptive re-home at pixel {pix}")
                motion_logger.log_event("ANL", "REHOME",
                                        f"preemptive re-home at pixel {pix}")

            # -- Move stage to nominal (reuse stage_calibration's backlash + safety) --
            _lv_run(live_view, sc._safe_scan_move, dev_x, nom_xm, "X")
            _lv_run(live_view, sc._safe_scan_move, dev_y, nom_ym, "Y")
            sc.set_velocity_mode(dev_x, "X")
            sc.set_velocity_mode(dev_y, "Y")
            _lv_sleep(live_view, sc.SETTLE_TIME_S)
            sx = sc.read_motor_pos(dev_x)
            sy = sc.read_motor_pos(dev_y)
            motion_logger.log_move("STAGE_X", "PIXEL_MOVE", nom_xm, sx, "mm",
                                   notes=f"pix{pix}")
            motion_logger.log_move("STAGE_Y", "PIXEL_MOVE", nom_ym, sy, "mm",
                                   notes=f"pix{pix}")

            # Defaults in case alignment is skipped or fails partway
            ax = nom_xm
            ay = nom_ym
            p_bright = 0.0

            if SKIP_PER_PIXEL_STAGE_ALIGN:
                # TEST MODE: skip stage align + brighten/dim. Stage sits at
                # nominal, rotators stay at warm-null, go straight to renull.
                method = "skip_align_test"
                flagged_low = False
                print(f"    [ALIGN] (skipped - test mode)")
            else:
                # -- BRIGHTEN: analyzer += BRIGHTEN_OFFSET_DEG off null so the
                #    stage sees a peak. QWP is already at q_warm from the
                #    previous null, so we DON'T move it (saves ~1 s/pixel).
                a_bright = wrap180(a_warm + BRIGHTEN_OFFSET_DEG)
                _lv_run(live_view, _medium_move_rot, rot_anl, a_bright)
                try:
                    motion_logger.log_move("ANL", "BRIGHTEN", a_bright,
                                           rot_anl.get_angle() or a_bright, "deg",
                                           notes=f"pix{pix}")
                except Exception:
                    motion_logger.log_move("ANL", "BRIGHTEN", a_bright,
                                           a_bright, "deg", notes=f"pix{pix}")

                # -- Stage auto-align: hill-climb first, fall back through
                #    fast_peak -> full GS -> full line scan (same chain as
                #    stage_calibration._do_one_pixel). --
                threshold_v = sc.FULL_AUTO_MIN_SIGNAL_MV / 1000.0
                print(f"    [ALIGN] hill-climb...")
                align = _lv_run(live_view, sc.hill_climb_align,
                                dev_x, dev_y, detector, live_view)
                method = "hill_climb"
                peak_v = (align["peak_v"] if align else 0.0) or 0.0

                if peak_v < threshold_v:
                    print(f"    [ALIGN] hill-climb peak {peak_v*1000:.1f} mV < "
                          f"{sc.FULL_AUTO_MIN_SIGNAL_MV:.0f} mV -> fast_peak...")
                    align = _lv_run(live_view, sc.fast_peak_align,
                                    dev_x, dev_y, detector, live_view)
                    method = "fast_peak_retry"
                    peak_v = (align["peak_v"] if align else 0.0) or 0.0

                if peak_v < threshold_v:
                    print(f"    [ALIGN] fast_peak peak {peak_v*1000:.1f} mV -> full GS...")
                    align = _lv_run(live_view, sc.golden_section_align,
                                    dev_x, dev_y, detector, live_view)
                    method = "gs_retry"
                    peak_v = (align["peak_v"] if align else 0.0) or 0.0

                if peak_v < threshold_v:
                    print(f"    [ALIGN] GS peak {peak_v*1000:.1f} mV -> full scan...")
                    align = _lv_run(live_view, sc.auto_align,
                                    dev_x, dev_y, detector, live_view)
                    method = "full_scan_retry"
                    peak_v = (align["peak_v"] if align else 0.0) or 0.0

                if align is None:
                    print("    [ALIGN] FAILED on all methods - skipping pixel")
                    motion_logger.log_event("STAGE", "ALIGN_FAIL",
                                            f"pix{pix} all methods failed")
                    continue
                ax = align["best_x"]
                ay = align["best_y"]
                p_bright = float(peak_v) * V_TO_W
                flagged_low = peak_v < threshold_v
                print(f"    [ALIGN] {method} -> ({ax:.4f}, {ay:.4f}) mm, "
                      f"P_bright={p_bright:.3e} W"
                      + ("  [LOW SIGNAL]" if flagged_low else ""))
                motion_logger.log_move("STAGE_X", "ALIGN", nom_xm, ax, "mm",
                                       notes=f"pix{pix} {method}")
                motion_logger.log_move("STAGE_Y", "ALIGN", nom_ym, ay, "mm",
                                       notes=f"pix{pix} {method}")

                # -- DIM back: analyzer returns to warm-start null.
                #    Uses _medium_move_rot (same as brighten). --
                _lv_run(live_view, _medium_move_rot, rot_anl, a_warm)
                try:
                    motion_logger.log_move("ANL", "DIM", a_warm,
                                           rot_anl.get_angle() or a_warm, "deg",
                                           notes=f"pix{pix}")
                except Exception:
                    motion_logger.log_move("ANL", "DIM", a_warm,
                                           a_warm, "deg", notes=f"pix{pix}")

            # -- Hint the scope into a fine range for the null read --
            # Skip the costly settle if already at the right range.
            try:
                if getattr(detector, 'cur_vdiv', None) != NULL_VDIV_HINT_V:
                    detector._set_vdiv(NULL_VDIV_HINT_V)
                    _lv_sleep(live_view, sc.POST_SCALE_CHANGE_SETTLE_S)
            except Exception:
                pass

            # -- Fast re-null (warm-start from previous pixel's null) --
            log_rows = []
            try:
                nr = _lv_run(live_view, fast_renull,
                             detector, rot_qwp, rot_anl,
                             q_warm, a_warm, p_ref, log_rows)
            except Exception as e:
                # Renull faulted (typically a rotator sensor error that the
                # single auto-home retry couldn't recover from). Log a
                # FAILED row so the CSV / heatmap have a gap instead of a
                # missing index, then continue to the next pixel. The warm
                # start (q_warm, a_warm) is left untouched so pixel N+1
                # still starts from the last KNOWN GOOD null, not from a
                # failed one.
                print(f"    [RENULL] ERROR: {e}")
                traceback.print_exc()
                save_renull_log(
                    os.path.join(renull_dir, f"pix{pix:03d}_renull_FAILED.csv"),
                    log_rows)
                fail_row = {
                    "pixel": pix, "i": i, "j": j,
                    "nominal_x_global_mm": pixel["nominal_x_global"],
                    "nominal_y_global_mm": pixel["nominal_y_global"],
                    "nominal_x_motor_mm":  nom_xm,
                    "nominal_y_motor_mm":  nom_ym,
                    "aligned_x_motor_mm":  round(ax, 6),
                    "aligned_y_motor_mm":  round(ay, 6),
                    "p_bright_W":  round(p_bright, 12),
                    "q_null_deg":  "",
                    "a_null_deg":  "",
                    "p_null_W":    "",
                    "p_null_before_W": "",
                    "dq_from_ref_deg": "",
                    "da_from_ref_deg": "",
                    "renull_method":    f"FAILED: {type(e).__name__}",
                    "renull_n_evals":   len(log_rows),
                    "renull_escalated": 0,
                    "flagged_low_quality": 1,
                    "timestamp_utc": datetime.utcnow().isoformat() + "Z",
                }
                pixel_rows.append(fail_row)
                append_pixel_row(pixel_csv, fail_row)
                with open(pixel_json, "w") as f:
                    json.dump({"run_name": run_name,
                               "sample_cal_ref": {"q": q_ref, "a": a_ref, "p": p_ref},
                               "pixels": pixel_rows}, f, indent=2)
                motion_logger.log_event("SCAN", "RENULL_FAIL",
                                        f"pix{pix} {type(e).__name__}: {e}")
                continue

            save_renull_log(os.path.join(renull_dir, f"pix{pix:03d}_renull.csv"),
                            log_rows)

            q_new, a_new = nr["q"], nr["a"]
            p_null_new = nr["p_after"]
            q_warm, a_warm = q_new, a_new  # warm-start next pixel from here

            flagged = p_null_new > NULL_QUALITY_FLAG_RATIO * p_ref
            print(f"    [RENULL] {nr['method']} "
                  f"-> q={q_new:.3f} deg, a={a_new:.3f} deg, "
                  f"P0={nr['p_before']:.3e} -> Pf={p_null_new:.3e} W, "
                  f"evals={nr['n_evals']}"
                  + ("  [FLAGGED]" if flagged else ""))
            motion_logger.log_move("QWP", "RENULL", q_warm, q_new, "deg",
                                   notes=f"pix{pix} {nr['method']} P={p_null_new:.3e}")
            motion_logger.log_move("ANL", "RENULL", a_warm, a_new, "deg",
                                   notes=f"pix{pix} {nr['method']} P={p_null_new:.3e}")

            row = {
                "pixel": pix, "i": i, "j": j,
                "nominal_x_global_mm": pixel["nominal_x_global"],
                "nominal_y_global_mm": pixel["nominal_y_global"],
                "nominal_x_motor_mm":  nom_xm,
                "nominal_y_motor_mm":  nom_ym,
                "aligned_x_motor_mm":  round(ax, 6),
                "aligned_y_motor_mm":  round(ay, 6),
                "p_bright_W":  round(p_bright, 12),
                "q_null_deg":  round(q_new, 4),
                "a_null_deg":  round(a_new, 4),
                "p_null_W":    round(p_null_new, 12),
                "p_null_before_W": round(nr["p_before"], 12),
                "dq_from_ref_deg": round(_wrap_delta(q_new - q_ref), 4),
                "da_from_ref_deg": round(_wrap_delta(a_new - a_ref), 4),
                "renull_method":    nr["method"],
                "renull_n_evals":   nr["n_evals"],
                "renull_escalated": int(bool(nr["escalated"])),
                "flagged_low_quality": int(flagged),
                "timestamp_utc": datetime.utcnow().isoformat() + "Z",
            }
            pixel_rows.append(row)
            append_pixel_row(pixel_csv, row)

            # incremental JSON save in case of interruption
            with open(pixel_json, "w") as f:
                json.dump({"run_name": run_name,
                           "sample_cal_ref": {"q": q_ref, "a": a_ref, "p": p_ref},
                           "pixels": pixel_rows}, f, indent=2)

    except KeyboardInterrupt:
        print("\n\n  [Ctrl-C] stopping - saving progress...")

    # ---- Heatmaps + summary ----
    print(f"\n  Generating heatmaps...")
    save_campaign_heatmaps(pixel_rows, run_dir, grid_n)

    # ---- Motion summary ----
    motion_logger.log_event("SCAN", "PHASE4_END",
                            f"{len(pixel_rows)}/{total} pixels completed")
    motion_logger.print_summary()
    motion_logger.close()

    print(f"\n{'=' * 70}")
    print(f"  CAMPAIGN COMPLETE  ({len(pixel_rows)}/{total} pixels)")
    print(f"  Run folder: {os.path.abspath(run_dir)}")
    print(f"  Motion log: {os.path.abspath(motion_log_path)}")
    print(f"{'=' * 70}")

    # ---- Cleanup ----
    live_view.stop()
    try: detector.close()
    except Exception: pass
    for rot in (rot_qwp, rot_hwp, rot_anl):
        try: rot.close()
        except Exception: pass
    for dev in (dev_x, dev_y):
        try:
            dev.StopPolling()
            dev.Disconnect()
        except Exception:
            pass
    if laser is not None:
        try:
            laser.off()
            laser.close()
        except Exception:
            pass
    print("  done.")


def _wrap_delta(dx):
    """Wrap an angle difference to (-90, +90] for reporting."""
    dx = ((dx + 90.0) % 180.0) - 90.0
    if dx == -90.0:
        dx = 90.0
    return dx


if __name__ == "__main__":
    main()
