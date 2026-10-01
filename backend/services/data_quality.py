"""Deterministic signal quality checks; policies are explicit and configurable."""

import math
import os
from datetime import datetime, timezone
from typing import Any, Mapping

from backend.core.time import utc_now
from backend.schemas.data_quality import QualityState, SignalQualityAssessment, ZoneDataQualityReport


class DataQualityGate:
    """Classifies signal values without inferring production limits or sensor freshness."""

    def __init__(self, *, max_age_seconds: Mapping[str, float] | None = None,
                 ranges: Mapping[str, tuple[float | None, float | None]] | None = None,
                 future_clock_skew_seconds: float | None = None):
        self.max_age_seconds = dict(
            max_age_seconds if max_age_seconds is not None else self._age_policy_from_environment())
        self.ranges = dict(ranges if ranges is not None else self._range_policy_from_environment())
        try:
            self.future_clock_skew_seconds = (
                float(future_clock_skew_seconds) if future_clock_skew_seconds is not None
                else float(os.getenv("DATA_QUALITY_FUTURE_CLOCK_SKEW_SECONDS", "0"))
            )
        except (TypeError, ValueError):
            self.future_clock_skew_seconds = 0.0
        if not math.isfinite(self.future_clock_skew_seconds) or self.future_clock_skew_seconds < 0:
            self.future_clock_skew_seconds = 0.0
        if any(not math.isfinite(float(age)) or float(age) < 0 for age in self.max_age_seconds.values()):
            raise ValueError("Data-quality maximum ages must be finite and non-negative.")
        for low, high in self.ranges.values():
            if ((low is not None and not math.isfinite(float(low)))
                    or (high is not None and not math.isfinite(float(high)))
                    or (low is not None and high is not None and float(low) > float(high))):
                raise ValueError("Data-quality ranges must be finite and ordered.")

    @staticmethod
    def _age_policy_from_environment() -> dict[str, float]:
        # No default sensor freshness limits: deployments must configure them.
        policy = {}
        for signal, env_name in (("occupancy", "OCCUPANCY"), ("temperature", "TEMPERATURE"),
                                 ("setpoint", "SETPOINT"), ("energy", "ENERGY"), ("tariff", "TARIFF")):
            raw = os.getenv(f"DATA_QUALITY_MAX_AGE_{env_name}_SECONDS")
            if raw is None:
                continue
            try:
                age = float(raw)
                policy[signal] = age
            except ValueError:
                raise ValueError(f"Invalid data-quality age configuration for {signal}.") from None
        return policy

    @staticmethod
    def _range_policy_from_environment() -> dict[str, tuple[float | None, float | None]]:
        policy = {}
        for signal, env_name in (("temperature", "TEMPERATURE"), ("power_kw", "POWER_KW"),
                                 ("energy", "ENERGY"), ("energy_cost", "ENERGY_COST"),
                                 ("tariff_rate", "TARIFF_RATE"), ("setpoint", "SETPOINT")):
            low = os.getenv(f"DATA_QUALITY_{env_name}_MIN")
            high = os.getenv(f"DATA_QUALITY_{env_name}_MAX")
            if low is None and high is None:
                continue
            try:
                policy[signal] = (float(low) if low else None,
                                  float(high) if high else None)
            except ValueError:
                raise ValueError(f"Invalid data-quality range configuration for {signal}.") from None
        return policy

    def assess(self, signal: str, value: Any, *, source: str = "unknown",
               observation_timestamp: datetime | None = None, simulated: bool = False,
               required: bool = True, now: datetime | None = None,
               minimum: float | None = None, maximum: float | None = None,
               numeric: bool = False, freshness_signal: str | None = None) -> SignalQualityAssessment:
        """Apply missing, structural, numeric, range, then freshness checks."""
        def result(state: QualityState, reason: str | None = None):
            return SignalQualityAssessment(signal=signal, state=state, source=source or "unknown",
                                           observation_timestamp=observation_timestamp,
                                           simulated=bool(simulated), reason_code=reason)

        if value is None:
            return result(QualityState.MISSING, "VALUE_MISSING" if required else "OPTIONAL_VALUE_MISSING")
        if numeric:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return result(QualityState.INVALID, "VALUE_NOT_NUMERIC")
            if not math.isfinite(float(value)):
                return result(QualityState.INVALID, "VALUE_NOT_FINITE")
            configured_min, configured_max = self.ranges.get(signal, (None, None))
            minimum = configured_min if minimum is None else minimum
            maximum = configured_max if maximum is None else maximum
            if minimum is not None and float(value) < minimum:
                return result(QualityState.OUT_OF_RANGE, "BELOW_CONFIGURED_MINIMUM")
            if maximum is not None and float(value) > maximum:
                return result(QualityState.OUT_OF_RANGE, "ABOVE_CONFIGURED_MAXIMUM")
        if observation_timestamp is None:
            return result(QualityState.MISSING, "OBSERVATION_TIMESTAMP_MISSING" if required else "OPTIONAL_VALUE_MISSING")
        if observation_timestamp.tzinfo is None or observation_timestamp.utcoffset() is None:
            return result(QualityState.INVALID, "OBSERVATION_TIMESTAMP_NOT_TIMEZONE_AWARE")

        # Timestamp provenance is supplied separately from snapshot/read time.
        # No age threshold is guessed; configured limits enable STALE decisions.
        age_limit = self.max_age_seconds.get(freshness_signal or signal)
        reference = now or utc_now()
        if reference.tzinfo is None or reference.utcoffset() is None:
            reference = reference.replace(tzinfo=timezone.utc)
        age = (reference.astimezone(timezone.utc) - observation_timestamp.astimezone(timezone.utc)).total_seconds()
        if age < -self.future_clock_skew_seconds:
            return result(QualityState.INVALID, "OBSERVATION_TIMESTAMP_IN_FUTURE")
        if age_limit is None:
            return result(QualityState.VALID)
        if age > age_limit:
            return result(QualityState.STALE, "OBSERVATION_EXCEEDS_MAX_AGE")
        return result(QualityState.VALID)

    def assess_zone_state(self, state, *, now: datetime | None = None) -> ZoneDataQualityReport:
        """Assess fields used by workflow/control, plus advisory telemetry inputs."""
        occ = state.occupancy
        occ_source = getattr(occ, "source", None) or state.occupancy_source
        occ_simulated = bool(getattr(occ, "simulated", False)) or "mock" in occ_source or "simulation" in occ_source
        assessments = {
            "occupancy": self.assess("occupancy", occ.people_count, source=occ_source,
                observation_timestamp=occ.observed_at, simulated=occ_simulated, numeric=True,
                minimum=0, maximum=occ.capacity),
            "temperature": self.assess("temperature", state.temperature,
                source=state.temperature_source, observation_timestamp=state.temperature_observed_at,
                simulated=state.temperature_simulated, numeric=True),
            "energy_power": self.assess("power_kw", state.energy.power_kw,
                source=state.energy.source, observation_timestamp=state.energy.observed_at,
                simulated=state.energy.is_simulated, numeric=True, minimum=0),
            "energy": self.assess("energy", state.energy.energy_kwh,
                source=state.energy.source, observation_timestamp=state.energy.observed_at,
                simulated=state.energy.is_simulated, numeric=True, minimum=0),
            "energy_cost": self.assess("energy_cost", state.energy.cost,
                source=state.energy.source, observation_timestamp=state.energy.observed_at,
                simulated=state.energy.is_simulated, numeric=True, minimum=0),
            "tariff": self.assess("tariff_rate", state.tariff.rate_per_kwh,
                source=state.tariff.source, observation_timestamp=state.tariff.observed_at,
                simulated=state.tariff.simulated, numeric=True, minimum=0),
            "hvac_setpoint": self.assess("setpoint", state.hvac_status.present_value,
                source=state.hvac_status.provider,
                observation_timestamp=(state.hvac_status.setpoint_observed_at or state.hvac_status.observed_at),
                simulated=state.hvac_status.simulated, numeric=True),
        }
        if occ.occupancy_state == "UNKNOWN" and occ.capacity == 0 and occ.observed_at is None:
            assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                "state": QualityState.MISSING, "reason_code": "OCCUPANCY_NOT_OBSERVED"})
        elif occ.capacity <= 0:
            assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                "state": QualityState.INVALID, "reason_code": "CAPACITY_NOT_POSITIVE"})
        elif assessments["occupancy"].state == QualityState.VALID:
            expected = occ.people_count / occ.capacity * 100.0
            if (isinstance(occ.occupancy_percentage, bool)
                    or not isinstance(occ.occupancy_percentage, (int, float))
                    or not math.isfinite(float(occ.occupancy_percentage))):
                assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                    "state": QualityState.INVALID, "reason_code": "OCCUPANCY_PERCENTAGE_INVALID"})
            elif not 0 <= float(occ.occupancy_percentage) <= 100:
                assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                    "state": QualityState.OUT_OF_RANGE, "reason_code": "OCCUPANCY_PERCENTAGE_OUT_OF_RANGE"})
            elif abs(expected - float(occ.occupancy_percentage)) > 0.05:
                assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                    "state": QualityState.INVALID, "reason_code": "OCCUPANCY_PERCENTAGE_INCONSISTENT"})
            elif (occ.occupancy_state not in {"EMPTY", "LOW", "MEDIUM", "HIGH"}
                  or (occ.people_count == 0) != (occ.occupancy_state == "EMPTY")):
                assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                    "state": QualityState.INVALID, "reason_code": "OCCUPANCY_STATE_INCONSISTENT"})
            else:
                expected_level = ("EMPTY" if expected == 0 else "LOW" if expected < 30
                                  else "MEDIUM" if expected < 70 else "HIGH")
                if occ.occupancy_state != expected_level:
                    assessments["occupancy"] = assessments["occupancy"].model_copy(update={
                        "state": QualityState.INVALID, "reason_code": "OCCUPANCY_STATE_INCONSISTENT"})
        return ZoneDataQualityReport(signals=assessments)

    @staticmethod
    def critical_failures(report: ZoneDataQualityReport) -> dict:
        # Energy and tariff inform advisory context but are not control interlocks.
        return report.critical_failures(("occupancy", "temperature", "hvac_setpoint"))
