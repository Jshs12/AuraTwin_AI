"""Process-local optimization interval lifecycle aligned with runtime state lifetime."""
from datetime import datetime
from uuid import uuid4

from backend.core.events import EventTrace
from backend.core.time import utc_now
from backend.schemas.data_quality import QualityState
from backend.schemas.optimization_interval import OptimizationInterval
from backend.schemas.state import ZoneState


class OptimizationIntervalService:
    """Hold one validated setpoint per zone until occupancy count changes.

    This process-local service matches the current monitoring/control mode state.
    It does not reconstruct ZoneState from historical telemetry. Interval energy
    is reported only as consumption when both boundary values are valid and from
    compatible provenance; it is never represented as savings.
    """

    def __init__(self):
        self._active: dict[str, OptimizationInterval] = {}
        self._completed: dict[str, list[OptimizationInterval]] = {}
        self._baselines: dict[str, tuple[float, bool, str, datetime, float, str, str, bool, datetime]] = {}
        self._history_limit = 100

    def active(self, zone_id: str) -> OptimizationInterval | None:
        return self._active.get(zone_id)

    def history(self, zone_id: str) -> list[OptimizationInterval]:
        return list(reversed(self._completed.get(zone_id, [])))

    def start(self, state: ZoneState, previous_setpoint: float, optimized_setpoint: float, *,
              organization_id: str | None = None, building_id: str | None = None,
              floor_id: str | None = None) -> OptimizationInterval:
        zone_id = state.zone.zone_id
        previous = self._active.get(zone_id)
        if previous is not None:
            self._close(previous, state, "REPLACED_BY_NEW_VALIDATED_COMMAND")
            self._archive(previous)
        now = utc_now()
        assessment = state.data_quality.signals.get("energy")
        tariff_assessment = state.data_quality.signals.get("tariff")
        energy_valid = assessment is not None and assessment.state == QualityState.VALID
        tariff_valid = tariff_assessment is not None and tariff_assessment.state == QualityState.VALID
        interval = OptimizationInterval(
            interval_id=str(uuid4()), organization_id=organization_id,
            building_id=building_id, floor_id=floor_id, zone_id=zone_id, started_at=now,
            starting_occupancy=state.occupancy.people_count,
            starting_temperature=state.temperature, previous_setpoint=previous_setpoint,
            optimized_setpoint=optimized_setpoint, occupancy_observed_at=state.occupancy.observed_at,
            source=state.occupancy.source,
            simulated=bool(state.occupancy.simulated or state.hvac_status.simulated
                           or state.energy.is_simulated or state.tariff.simulated),
            energy_provenance=state.energy.source if energy_valid else None,
        )
        self._active[zone_id] = interval
        if (energy_valid and tariff_valid and state.energy.observed_at is not None
                and state.tariff.observed_at is not None):
            self._baselines[interval.interval_id] = (state.energy.energy_kwh, state.energy.is_simulated,
                state.energy.source, state.energy.observed_at, state.tariff.rate_per_kwh, state.tariff.currency,
                state.tariff.source, state.tariff.simulated, state.tariff.observed_at)
        EventTrace.log_event("OPTIMIZATION_STARTED", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "occupancy": interval.starting_occupancy,
            "previous_setpoint": previous_setpoint, "optimized_setpoint": optimized_setpoint,
            "simulated": interval.simulated})
        EventTrace.log_event("OPTIMIZATION_HOLDING", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "next_evaluation": "NEXT_MEANINGFUL_OCCUPANCY_OBSERVATION",
            "setpoint": optimized_setpoint})
        return interval

    def observe(self, state: ZoneState) -> tuple[bool, OptimizationInterval | None]:
        interval = self._active.get(state.zone.zone_id)
        if interval is None:
            return True, None
        occupancy_quality = state.data_quality.signals.get("occupancy")
        if occupancy_quality is None or occupancy_quality.state != QualityState.VALID:
            # Bad/absent occupancy cannot form the next meaningful observation
            # boundary; keep holding and let normal recommendation quality gates
            # reject any separate attempt to act.
            return False, interval
        if state.occupancy.people_count == interval.starting_occupancy:
            return False, interval
        self._close(interval, state, "OCCUPANCY_CHANGED")
        self._active.pop(state.zone.zone_id, None)
        self._archive(interval)
        EventTrace.log_event("OCCUPANCY_CHANGED", state.zone.zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "previous_count": interval.starting_occupancy,
            "current_count": interval.ending_occupancy})
        EventTrace.log_event("OPTIMIZATION_COMPLETED", state.zone.zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "duration_seconds": interval.duration_seconds,
            "energy_consumed_kwh": interval.energy_consumed_kwh, "cost_consumed": interval.cost_consumed,
            "simulated": interval.simulated})
        return True, interval

    def _archive(self, interval: OptimizationInterval) -> None:
        history = self._completed.setdefault(interval.zone_id, [])
        history.append(interval)
        if len(history) > self._history_limit:
            del history[:-self._history_limit]

    def _close(self, interval: OptimizationInterval, state: ZoneState, reason: str) -> None:
        now = utc_now()
        interval.ended_at = now
        interval.ending_occupancy = state.occupancy.people_count
        interval.ending_temperature = state.temperature
        interval.duration_seconds = max(0.0, (now - interval.started_at).total_seconds())
        interval.status = "COMPLETED"
        interval.reason = reason
        baseline = self._baselines.pop(interval.interval_id, None)
        energy_quality = state.data_quality.signals.get("energy")
        tariff_quality = state.data_quality.signals.get("tariff")
        if (baseline and energy_quality and energy_quality.state == QualityState.VALID
                and tariff_quality and tariff_quality.state == QualityState.VALID):
            (start_value, start_simulated, start_source, start_observed, rate, currency,
             tariff_source, tariff_simulated, tariff_observed) = baseline
            if (state.energy.observed_at and state.energy.observed_at > start_observed
                    and state.energy.source == start_source and state.energy.is_simulated == start_simulated
                    and state.tariff.observed_at and state.tariff.observed_at > tariff_observed
                    and state.tariff.source == tariff_source and state.tariff.simulated == tariff_simulated
                    and state.tariff.rate_per_kwh == rate
                    and state.tariff.currency == currency):
                delta = state.energy.energy_kwh - start_value
                if delta >= 0:
                    interval.energy_consumed_kwh = round(delta, 5)
                    interval.tariff_rate_per_kwh = rate
                    interval.currency = currency
                    interval.cost_consumed = round(delta * rate, 5)
                    interval.energy_provenance = start_source
        EventTrace.log_event("ENERGY_IMPACT_CALCULATED" if interval.energy_consumed_kwh is not None
            else "ENERGY_IMPACT_UNAVAILABLE", state.zone.zone_id, "optimization_interval", {
                "interval_id": interval.interval_id, "energy_consumed_kwh": interval.energy_consumed_kwh,
                "cost_consumed": interval.cost_consumed, "simulated": interval.simulated})
