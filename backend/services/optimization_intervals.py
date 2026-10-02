"""Persistent optimization interval lifecycle with validated telemetry attribution."""

from datetime import datetime
from uuid import uuid4

from backend.core.events import EventTrace
from backend.core.time import utc_now
from backend.schemas.data_quality import QualityState
from backend.schemas.optimization_interval import OptimizationInterval
from backend.schemas.state import ZoneState
from backend.schemas.telemetry import TelemetrySignal


class OptimizationIntervalService:
    """Hold one validated setpoint until a valid occupancy count changes.

    The application injects the SQLAlchemy repository and existing telemetry
    persistence service. A memory-only mode remains available to isolated unit
    tests/adapters and is never used by the application wiring.
    """

    def __init__(self, repository=None, telemetry_service=None, configuration_repository=None):
        self.repository = repository
        self.telemetry_service = telemetry_service
        self.configuration_repository = configuration_repository
        self._active: dict[str, OptimizationInterval] = {}
        self._completed: dict[str, list[OptimizationInterval]] = {}
        self._baselines: dict[str, tuple[float, bool, str, datetime, float, str, str, bool, datetime]] = {}
        self._history_limit = 100

    def _scope(self, zone_id: str) -> dict | None:
        if self.configuration_repository is None:
            return None
        return self.configuration_repository.telemetry_scope(zone_id)

    def active(self, zone_id: str) -> OptimizationInterval | None:
        if self.repository is not None:
            scope = self._scope(zone_id)
            return self.repository.active(scope["database_zone_id"]) if scope else None
        return self._active.get(zone_id)

    def history(self, zone_id: str) -> list[OptimizationInterval]:
        if self.repository is not None:
            scope = self._scope(zone_id)
            return self.repository.history(scope["database_zone_id"]) if scope else []
        return list(reversed(self._completed.get(zone_id, [])))

    def _energy_boundary(self, state: ZoneState):
        assessment = state.data_quality.signals.get("energy")
        if (assessment is None or assessment.state != QualityState.VALID
                or state.energy.observed_at is None or self.telemetry_service is None):
            return None
        return self.telemetry_service.find_persisted_boundary(
            zone_id=state.zone.zone_id, signal=TelemetrySignal.ENERGY,
            value=state.energy.energy_kwh, unit="kWh", observed_at=state.energy.observed_at,
            source=state.energy.source, quality_state=assessment.state.value,
            simulated=state.energy.is_simulated)

    def _tariff_boundary(self, state: ZoneState):
        assessment = state.data_quality.signals.get("tariff")
        tariff = state.tariff
        if (assessment is None or assessment.state != QualityState.VALID
                or tariff.observed_at is None or self.telemetry_service is None):
            return None
        return self.telemetry_service.find_persisted_boundary(
            zone_id=state.zone.zone_id, signal=TelemetrySignal.TARIFF_RATE,
            value=tariff.rate_per_kwh, unit=f"{tariff.currency}/kWh",
            observed_at=tariff.observed_at, source=tariff.source,
            quality_state=assessment.state.value, simulated=tariff.simulated)

    def start(self, state: ZoneState, previous_setpoint: float, optimized_setpoint: float, *,
              organization_id: str | None = None, building_id: str | None = None,
              floor_id: str | None = None, started_at: datetime | None = None) -> OptimizationInterval:
        zone_id = state.zone.zone_id
        scope = self._scope(zone_id) if self.repository is not None else None
        if self.repository is not None:
            if scope is None:
                raise ValueError("Cannot persist optimization interval without configured zone ownership")
            organization_id, building_id, floor_id = (
                scope["organization_id"], scope["building_id"], scope["floor_id"])
        previous = self.active(zone_id)
        if previous is not None:
            self._close(previous, state, "REPLACED_BY_NEW_VALIDATED_COMMAND")
            if self.repository is not None:
                self.repository.update(previous)
            else:
                self._active.pop(zone_id, None)
                self._completed.setdefault(zone_id, []).append(previous)
        now = started_at or utc_now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Optimization interval start time must be timezone-aware")
        occupancy_quality = state.data_quality.signals.get("occupancy")
        energy_assessment = state.data_quality.signals.get("energy")
        tariff_assessment = state.data_quality.signals.get("tariff")
        energy = self._energy_boundary(state) if self.repository is not None else None
        tariff = self._tariff_boundary(state) if self.repository is not None else None
        # An observation before the successful control timestamp cannot serve as
        # the interval's starting boundary; leave attribution unavailable.
        if energy is not None and energy.observed_at < now:
            energy = None
        if tariff is not None and tariff.observed_at < now:
            tariff = None
        interval = OptimizationInterval(
            interval_id=str(uuid4()), organization_id=organization_id, building_id=building_id,
            floor_id=floor_id, zone_id=zone_id,
            database_zone_id=scope["database_zone_id"] if scope else None,
            started_at=now, starting_occupancy=state.occupancy.people_count,
            starting_occupancy_observed_at=state.occupancy.observed_at,
            previous_setpoint=previous_setpoint, optimized_setpoint=optimized_setpoint,
            setpoint_observed_at=(state.hvac_status.setpoint_observed_at or state.hvac_status.observed_at),
            starting_temperature=state.temperature,
            starting_temperature_observed_at=state.temperature_observed_at,
            starting_energy_kwh=energy.value if energy else None,
            starting_energy_observed_at=energy.observed_at if energy else None,
            energy_unit=energy.unit if energy else "kWh",
            energy_status="PENDING" if energy else "UNAVAILABLE",
            energy_reason_code=None if energy else (
                "START_BOUNDARY_PRECEDES_INTERVAL" if state.energy.observed_at
                and state.energy.observed_at.tzinfo is not None and state.energy.observed_at < now
                else "START_BOUNDARY_UNAVAILABLE"),
            energy_source=energy.source if energy else None,
            energy_quality=energy.quality_state if energy else (
                energy_assessment.state.value if energy_assessment else None),
            energy_simulated=energy.simulated if energy else None,
            tariff_rate_per_kwh=tariff.value if tariff else None,
            currency=(tariff.unit.removesuffix("/kWh") if tariff else None),
            starting_tariff_observed_at=tariff.observed_at if tariff else None,
            tariff_source=tariff.source if tariff else None,
            tariff_quality=tariff.quality_state if tariff else (
                tariff_assessment.state.value if tariff_assessment else None),
            tariff_simulated=tariff.simulated if tariff else None,
            cost_status="PENDING" if tariff and energy else "UNAVAILABLE",
            cost_reason_code=None if tariff and energy else (
                "START_BOUNDARY_PRECEDES_INTERVAL" if (
                    (state.energy.observed_at and state.energy.observed_at.tzinfo is not None
                     and state.energy.observed_at < now)
                    or (state.tariff.observed_at and state.tariff.observed_at.tzinfo is not None
                        and state.tariff.observed_at < now))
                else "TARIFF_COVERAGE_UNAVAILABLE"),
            quality_state=occupancy_quality.state.value if occupancy_quality else "MISSING",
            source=state.occupancy.source, simulated=bool(
                state.occupancy.simulated or state.hvac_status.simulated
                or state.energy.is_simulated or state.tariff.simulated),
        )
        if self.repository is not None:
            saved = self.repository.create(interval)
            if saved.interval_id != interval.interval_id:
                return saved
            interval = saved
        else:
            self._active[zone_id] = interval
            if energy_assessment and energy_assessment.state == QualityState.VALID \
                    and tariff_assessment and tariff_assessment.state == QualityState.VALID \
                    and state.energy.observed_at and state.tariff.observed_at:
                self._baselines[interval.interval_id] = (
                    state.energy.energy_kwh, state.energy.is_simulated, state.energy.source,
                    state.energy.observed_at, state.tariff.rate_per_kwh, state.tariff.currency,
                    state.tariff.source, state.tariff.simulated, state.tariff.observed_at)
        EventTrace.log_event("OPTIMIZATION_STARTED", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "organization_id": organization_id,
            "building_id": building_id, "occupancy": interval.starting_occupancy,
            "previous_setpoint": previous_setpoint, "optimized_setpoint": optimized_setpoint,
            "simulated": interval.simulated})
        EventTrace.log_event("OPTIMIZATION_HOLDING", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "next_evaluation": "NEXT_MEANINGFUL_OCCUPANCY_OBSERVATION",
            "setpoint": optimized_setpoint})
        return interval

    def observe(self, state: ZoneState) -> tuple[bool, OptimizationInterval | None]:
        interval = self.active(state.zone.zone_id)
        if interval is None:
            return True, None
        occupancy_quality = state.data_quality.signals.get("occupancy")
        if occupancy_quality is None or occupancy_quality.state != QualityState.VALID:
            return False, interval
        if state.occupancy.people_count == interval.starting_occupancy:
            return False, interval
        self._close(interval, state, "OCCUPANCY_CHANGED")
        if self.repository is not None:
            interval = self.repository.update(interval)
        else:
            self._active.pop(state.zone.zone_id, None)
            history = self._completed.setdefault(state.zone.zone_id, [])
            history.append(interval)
            if len(history) > self._history_limit:
                del history[:-self._history_limit]
        EventTrace.log_event("OCCUPANCY_CHANGED", state.zone.zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "previous_count": interval.starting_occupancy,
            "current_count": interval.ending_occupancy})
        EventTrace.log_event("OPTIMIZATION_COMPLETED", state.zone.zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "duration_seconds": interval.duration_seconds,
            "energy_consumed_kwh": interval.energy_consumed_kwh, "cost_consumed": interval.cost_consumed,
            "simulated": interval.simulated})
        EventTrace.log_event("SAVINGS_BASELINE_UNAVAILABLE", state.zone.zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "reason_code": "NO_VALIDATED_COMPARISON_BASELINE"})
        return True, interval

    def close_active(self, zone_id: str, state: ZoneState,
                     reason: str = "DEMO_COMPLETED") -> OptimizationInterval | None:
        """Close an interval from an actual state sample before simulated reset.

        Attribution still runs through `_close`; this method only provides the
        lifecycle boundary needed when a demo ends without another occupancy
        change. The caller must persist the current state before invoking it.
        """
        if state.zone.zone_id != zone_id:
            raise ValueError("Optimization interval state must match its zone.")
        interval = self.active(zone_id)
        if interval is None:
            return None
        self._close(interval, state, reason)
        if self.repository is not None:
            interval = self.repository.update(interval)
        else:
            self._active.pop(zone_id, None)
            history = self._completed.setdefault(zone_id, [])
            history.append(interval)
            if len(history) > self._history_limit:
                del history[:-self._history_limit]
        EventTrace.log_event("OPTIMIZATION_COMPLETED", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "duration_seconds": interval.duration_seconds,
            "energy_consumed_kwh": interval.energy_consumed_kwh,
            "cost_consumed": interval.cost_consumed, "simulated": interval.simulated,
            "reason": reason})
        EventTrace.log_event("SAVINGS_BASELINE_UNAVAILABLE", zone_id, "optimization_interval", {
            "interval_id": interval.interval_id, "reason_code": "NO_VALIDATED_COMPARISON_BASELINE"})
        return interval

    def _close(self, interval: OptimizationInterval, state: ZoneState, reason: str) -> None:
        now = utc_now()
        ending_occupancy = state.occupancy.observed_at
        if ending_occupancy and ending_occupancy.tzinfo is not None:
            now = ending_occupancy
        interval.ended_at = max(now, interval.started_at)
        interval.ending_occupancy = state.occupancy.people_count
        interval.ending_occupancy_observed_at = state.occupancy.observed_at
        interval.ending_temperature = state.temperature
        interval.ending_temperature_observed_at = state.temperature_observed_at
        interval.duration_seconds = max(0.0, (interval.ended_at - interval.started_at).total_seconds())
        interval.status = "COMPLETED"
        interval.reason = reason

        if self.repository is not None:
            self._close_persisted_attribution(interval, state)
        else:
            self._close_memory_attribution(interval, state)
        EventTrace.log_event(
            "ENERGY_IMPACT_CALCULATED" if interval.energy_status == "AVAILABLE" else "ENERGY_IMPACT_UNAVAILABLE",
            state.zone.zone_id, "optimization_interval", {
                "interval_id": interval.interval_id, "energy_consumed_kwh": interval.energy_consumed_kwh,
                "energy_status": interval.energy_status, "reason_code": interval.energy_reason_code,
                "simulated": interval.simulated})
        EventTrace.log_event(
            "COST_IMPACT_CALCULATED" if interval.cost_status == "AVAILABLE" else "IMPACT_UNAVAILABLE",
            state.zone.zone_id, "optimization_interval", {
                "interval_id": interval.interval_id, "cost_consumed": interval.cost_consumed,
                "cost_status": interval.cost_status, "reason_code": interval.cost_reason_code,
                "simulated": interval.simulated})

    def _close_persisted_attribution(self, interval: OptimizationInterval, state: ZoneState) -> None:
        ending_energy = self._energy_boundary(state)
        ending_tariff = self._tariff_boundary(state)
        if ending_energy is not None:
            interval.ending_energy_kwh = ending_energy.value
            interval.ending_energy_observed_at = ending_energy.observed_at
        if ending_tariff is not None:
            interval.ending_tariff_observed_at = ending_tariff.observed_at

        start_energy_at = interval.starting_energy_observed_at
        if interval.starting_energy_kwh is None:
            interval.energy_status = "UNAVAILABLE"
            interval.energy_reason_code = "START_BOUNDARY_UNAVAILABLE"
        elif ending_energy is None:
            interval.energy_status = "UNAVAILABLE"
            interval.energy_reason_code = "ENDING_BOUNDARY_UNAVAILABLE"
        elif start_energy_at is None or ending_energy.observed_at <= start_energy_at:
            interval.energy_status = "INVALID"
            interval.energy_reason_code = "ENERGY_TIMESTAMPS_NOT_ORDERED"
        elif (ending_energy.unit != interval.energy_unit or ending_energy.source != interval.energy_source
                or ending_energy.simulated != interval.energy_simulated):
            interval.energy_status = "INVALID"
            interval.energy_reason_code = "ENERGY_PROVENANCE_INCOMPATIBLE"
        elif ending_energy.quality_state != QualityState.VALID.value:
            interval.energy_status = "INVALID"
            interval.energy_reason_code = "ENDING_ENERGY_QUALITY_INVALID"
        elif ending_energy.value < interval.starting_energy_kwh:
            interval.energy_status = "INVALID"
            interval.energy_reason_code = "CUMULATIVE_ENERGY_DECREASED"
        else:
            interval.energy_consumed_kwh = round(ending_energy.value - interval.starting_energy_kwh, 5)
            interval.energy_status = "AVAILABLE"
            interval.energy_reason_code = None

        start_tariff_at = interval.starting_tariff_observed_at
        if interval.energy_status != "AVAILABLE":
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "VALID_ENERGY_UNAVAILABLE"
            return
        if interval.tariff_rate_per_kwh is None or start_tariff_at is None:
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "START_TARIFF_BOUNDARY_UNAVAILABLE"
            return
        if ending_tariff is None:
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "ENDING_TARIFF_BOUNDARY_UNAVAILABLE"
            return
        if ending_tariff.observed_at <= start_tariff_at:
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "TARIFF_COVERAGE_UNAVAILABLE"
            return
        if (ending_tariff.value != interval.tariff_rate_per_kwh
                or ending_tariff.unit != f"{interval.currency}/kWh"
                or ending_tariff.source != interval.tariff_source
                or ending_tariff.simulated != interval.tariff_simulated
                or ending_tariff.quality_state != QualityState.VALID.value):
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "TARIFF_CHANGED_OR_INCOMPATIBLE"
            return

        # Any observed rate change invalidates flat-rate attribution. Energy
        # cannot be apportioned across tariff segments without aligned meters.
        scope = self._scope(state.zone.zone_id)
        if scope is None:
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "TARIFF_COVERAGE_UNAVAILABLE"
            return
        tariff_rows = self.telemetry_service.list_zone(
            organization_id=scope["organization_id"], building_id=scope["building_id"],
            zone_id=scope["database_zone_id"], start_at=start_tariff_at,
            end_at=ending_tariff.observed_at, limit=501, signal=TelemetrySignal.TARIFF_RATE.value)
        if len(tariff_rows) > 500:
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "TARIFF_COVERAGE_TOO_LARGE_TO_VALIDATE"
            return
        expected_unit = f"{interval.currency}/kWh"
        if any(row.quality_state != QualityState.VALID.value or row.unit != expected_unit
               or row.value != interval.tariff_rate_per_kwh or row.source != interval.tariff_source
               or row.simulated != interval.tariff_simulated for row in tariff_rows):
            interval.cost_status = "UNAVAILABLE"
            interval.cost_reason_code = "TARIFF_CHANGED_DURING_INTERVAL"
            return
        interval.cost_consumed = round(interval.energy_consumed_kwh * interval.tariff_rate_per_kwh, 5)
        interval.cost_status = "AVAILABLE"
        interval.cost_reason_code = None

    def _close_memory_attribution(self, interval: OptimizationInterval, state: ZoneState) -> None:
        baseline = self._baselines.pop(interval.interval_id, None)
        energy_quality = state.data_quality.signals.get("energy")
        tariff_quality = state.data_quality.signals.get("tariff")
        if (baseline and energy_quality and energy_quality.state == QualityState.VALID
                and tariff_quality and tariff_quality.state == QualityState.VALID):
            start, simulated, source, start_at, rate, currency, tariff_source, tariff_simulated, tariff_at = baseline
            if (state.energy.observed_at and state.energy.observed_at > start_at
                    and state.energy.source == source and state.energy.is_simulated == simulated
                    and state.tariff.observed_at and state.tariff.observed_at > tariff_at
                    and state.tariff.source == tariff_source and state.tariff.simulated == tariff_simulated
                    and state.tariff.rate_per_kwh == rate and state.tariff.currency == currency):
                delta = state.energy.energy_kwh - start
                if delta >= 0:
                    interval.energy_consumed_kwh = round(delta, 5)
                    interval.energy_status = "AVAILABLE"
                    interval.energy_reason_code = None
                    interval.tariff_rate_per_kwh = rate
                    interval.currency = currency
                    interval.cost_consumed = round(delta * rate, 5)
                    interval.cost_status = "AVAILABLE"
                    interval.cost_reason_code = None
                    return
        interval.energy_status = "UNAVAILABLE"
        interval.energy_reason_code = "INSUFFICIENT_VALIDATED_TELEMETRY"
        interval.cost_status = "UNAVAILABLE"
        interval.cost_reason_code = "INSUFFICIENT_VALIDATED_TELEMETRY"
