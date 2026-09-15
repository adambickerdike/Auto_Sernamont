"""Predictive, hysteretic DSP7230 range selection for DC hysteresis sweeps.

The controller is intentionally independent of the instrument driver.  It
chooses a sensitivity index before the AC measurement window, while the AC
probe is still off, and the acquisition layer performs the actual write.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


DEFAULT_RANGE_INDICES = (11, 12, 13, 14, 15, 16)  # 5 uV .. 200 uV RMS FS


def _finite_nonnegative(value, fallback=0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(number):
        return float(fallback)
    return max(0.0, number)


def _branch_family(branch: str) -> str:
    """Return ``down`` for down/down1/down2, etc."""
    text = str(branch or "")
    return text.rstrip("0123456789") or text


@dataclass(frozen=True)
class RangeDecision:
    sensitivity_index: int
    predicted_magnitude_v: float
    noise_sigma_v: float
    upper_bound_v: float
    requested_index: int
    changed: bool
    reason: str


class PredictiveHystereticRangeController:
    """Choose a safe DSP7230 range without chasing individual noisy reads.

    Widening is immediate.  Narrowing requires repeated evidence and proceeds
    one range at a time.  The first point is allowed to move directly to the
    range predicted from the already-measured peak response.
    """

    def __init__(
        self,
        sensitivity_table: Mapping[int, float],
        current_index: int,
        *,
        range_indices=DEFAULT_RANGE_INDICES,
        initial_anchor_v: float = 0.0,
        target_fraction: float = 0.60,
        rescue_fraction: float = 0.85,
        safety_sigma: float = 4.0,
        noise_floor_v: float = 0.10e-6,
        narrow_confirmations: int = 2,
        hold_points_after_change: int = 2,
    ) -> None:
        table = {int(k): float(v) for k, v in dict(sensitivity_table).items()}
        indices = tuple(
            index
            for index in sorted({int(value) for value in range_indices})
            if index in table and math.isfinite(table[index]) and table[index] > 0.0
        )
        if not indices:
            raise ValueError("No valid DSP7230 dynamic-ranging indices were supplied.")
        if not 0.0 < float(target_fraction) < float(rescue_fraction) < 1.0:
            raise ValueError("Range target/rescue fractions must satisfy 0 < target < rescue < 1.")

        self.sensitivity_table = table
        self.range_indices = indices
        self.current_index = self._nearest_ladder_index(int(current_index))
        self.initial_anchor_v = _finite_nonnegative(initial_anchor_v)
        self.target_fraction = float(target_fraction)
        self.rescue_fraction = float(rescue_fraction)
        self.safety_sigma = max(0.0, float(safety_sigma))
        self.noise_floor_v = max(0.0, float(noise_floor_v))
        self.narrow_confirmations = max(1, int(narrow_confirmations))
        self.hold_points_after_change = max(0, int(hold_points_after_change))
        self.observations: list[dict] = []
        self._narrow_streak = 0
        self._points_since_change = self.hold_points_after_change

    def _nearest_ladder_index(self, index: int) -> int:
        return min(self.range_indices, key=lambda item: abs(item - int(index)))

    def _position(self, index: int) -> int:
        return self.range_indices.index(self._nearest_ladder_index(index))

    def fullscale_v(self, index: int | None = None) -> float:
        selected = self.current_index if index is None else self._nearest_ladder_index(index)
        return float(self.sensitivity_table[selected])

    def index_for_upper_bound(self, upper_bound_v: float) -> int:
        upper = _finite_nonnegative(upper_bound_v)
        for index in self.range_indices:
            if upper <= self.target_fraction * self.sensitivity_table[index]:
                return index
        return self.range_indices[-1]

    def _recent_noise_sigma(self) -> float:
        finite = [
            _finite_nonnegative(row.get("noise_sigma_v"), self.noise_floor_v)
            for row in self.observations[-4:]
        ]
        return max([self.noise_floor_v, *finite])

    def _predict_magnitude(self, voltage_v: float, branch: str, cycle: int) -> float:
        voltage = float(voltage_v)
        family = _branch_family(branch)
        same_family = [
            row
            for row in self.observations
            if row.get("branch_family") == family
        ]

        # A previously measured occurrence of the exact voltage is especially
        # useful on the return branch or a later cycle.  Keep the larger of the
        # two most recent occurrences as a conservative loop-memory estimate.
        same_voltage = [
            row["magnitude_v"]
            for row in self.observations
            if abs(float(row["voltage_v"]) - voltage) <= 1e-9
        ]

        prediction = None
        if len(same_family) >= 2:
            first, second = same_family[-2], same_family[-1]
            dv = float(second["voltage_v"]) - float(first["voltage_v"])
            if abs(dv) > 1e-12:
                slope = (
                    float(second["magnitude_v"]) - float(first["magnitude_v"])
                ) / dv
                prediction = float(second["magnitude_v"]) + slope * (
                    voltage - float(second["voltage_v"])
                )
                # A single noisy slope must not request a wildly inappropriate
                # range.  Emergency widening remains available after the read.
                local_max = max(
                    float(first["magnitude_v"]),
                    float(second["magnitude_v"]),
                    self.noise_floor_v,
                )
                prediction = min(max(0.0, prediction), 2.0 * local_max)
        elif same_family:
            prediction = float(same_family[-1]["magnitude_v"])
        elif self.observations:
            prediction = float(self.observations[-1]["magnitude_v"])
        else:
            prediction = self.initial_anchor_v

        if same_voltage:
            prediction = max(float(prediction or 0.0), max(same_voltage[-2:]))
        return _finite_nonnegative(prediction)

    def plan(self, voltage_v: float, branch: str, cycle: int) -> RangeDecision:
        predicted = self._predict_magnitude(voltage_v, branch, cycle)
        sigma = self._recent_noise_sigma()
        upper = predicted + self.safety_sigma * sigma
        requested = self.index_for_upper_bound(upper)
        current_pos = self._position(self.current_index)
        requested_pos = self._position(requested)
        selected = self.current_index
        reason = "predictive_hold"

        if not self.observations:
            selected = requested
            reason = "initial_peak_scaled_prediction"
            self._narrow_streak = 0
        elif requested_pos > current_pos:
            selected = requested
            reason = "predictive_widen"
            self._narrow_streak = 0
        elif requested_pos < current_pos:
            self._narrow_streak += 1
            if (
                self._narrow_streak >= self.narrow_confirmations
                and self._points_since_change >= self.hold_points_after_change
            ):
                selected = self.range_indices[current_pos - 1]
                reason = "confirmed_one_step_narrow"
                self._narrow_streak = 0
            else:
                reason = "narrow_pending_hysteresis"
        else:
            self._narrow_streak = 0

        changed = int(selected) != int(self.current_index)
        if changed:
            self.current_index = int(selected)
            self._points_since_change = 0
        return RangeDecision(
            sensitivity_index=int(selected),
            predicted_magnitude_v=float(predicted),
            noise_sigma_v=float(sigma),
            upper_bound_v=float(upper),
            requested_index=int(requested),
            changed=bool(changed),
            reason=reason,
        )

    def emergency_widen_index(
        self,
        measured_magnitude_v: float,
        noise_sigma_v: float,
        *,
        output_overload: bool = False,
    ) -> int:
        measured = _finite_nonnegative(measured_magnitude_v)
        sigma = max(self.noise_floor_v, _finite_nonnegative(noise_sigma_v))
        upper = measured + self.safety_sigma * sigma
        desired = self.index_for_upper_bound(upper)
        current_pos = self._position(self.current_index)
        # A post-read check is allowed to widen only.  Narrowing is decided
        # before a later point and requires the normal multi-point hysteresis.
        desired_pos = max(current_pos, self._position(desired))
        if output_overload or measured >= self.rescue_fraction * self.fullscale_v():
            desired_pos = max(desired_pos, min(current_pos + 1, len(self.range_indices) - 1))
        selected = self.range_indices[desired_pos]
        if selected != self.current_index:
            self.current_index = selected
            self._points_since_change = 0
            self._narrow_streak = 0
        return int(selected)

    def record(
        self,
        voltage_v: float,
        branch: str,
        cycle: int,
        magnitude_v: float,
        noise_sigma_v: float,
    ) -> None:
        self.observations.append(
            {
                "voltage_v": float(voltage_v),
                "branch": str(branch),
                "branch_family": _branch_family(branch),
                "cycle": int(cycle),
                "magnitude_v": _finite_nonnegative(magnitude_v),
                "noise_sigma_v": max(
                    self.noise_floor_v,
                    _finite_nonnegative(noise_sigma_v, self.noise_floor_v),
                ),
            }
        )
        self._points_since_change += 1
