"""Maps privacy-minimized ZoneState observations into the persistence port."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.core.events import EventTrace
from backend.schemas.state import ZoneState
from backend.schemas.telemetry import TelemetryObservation, TelemetrySignal
from backend.schemas.telemetry import TelemetryAggregation
from backend.telemetry.repository import TelemetryRepository


@dataclass(frozen=True)
class TelemetryRetentionPolicy:
    days: int | None = None

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None):
        values = os.environ if environ is None else environ
        raw = values.get("TELEMETRY_RETENTION_DAYS")
        if raw is None or not raw.strip():
            return cls(days=None)
        try:
            days = int(raw)
        except ValueError:
            raise ValueError("TELEMETRY_RETENTION_DAYS must be a positive integer when configured.") from None
        if days <= 0:
            raise ValueError("TELEMETRY_RETENTION_DAYS must be a positive integer when configured.")
        return cls(days=days)

    def cutoff(self, *, now: datetime) -> datetime | None:
        if self.days is None:
            return None
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Retention reference time must be timezone-aware.")
        return now.astimezone(timezone.utc) - timedelta(days=self.days)


def telemetry_query_max_limit(environ: dict[str, str] | None = None) -> int:
    values = os.environ if environ is None else environ
    raw = values.get("TELEMETRY_QUERY_MAX_LIMIT", "500")
    try:
        limit = int(raw)
    except (ValueError, TypeError):
        raise ValueError("TELEMETRY_QUERY_MAX_LIMIT must be a positive integer.") from None
    if limit <= 0:
        raise ValueError("TELEMETRY_QUERY_MAX_LIMIT must be a positive integer.")
    return limit


class TelemetryPersistenceService:
    def __init__(self, repository: TelemetryRepository, configuration_repository,
                 retention: TelemetryRetentionPolicy | None = None):
        self.repository = repository
        self.configuration_repository = configuration_repository
        self.retention = retention or TelemetryRetentionPolicy.from_environment()

    def persist_zone_state(self, state: ZoneState) -> int:
        try:
            scope = self.configuration_repository.telemetry_scope(state.zone.zone_id)
        except Exception as exc:
            EventTrace.log_event("TELEMETRY_PERSISTENCE_FAILED", state.zone.zone_id,
                "telemetry_persistence", {"error_type": type(exc).__name__}, status="FAILED")
            return 0

        if scope is None:
            return 0
        quality = state.data_quality.signals
        candidates = (
            (TelemetrySignal.OCCUPANCY, state.occupancy.people_count, "people",
             state.occupancy.observed_at, state.occupancy.source, state.occupancy.simulated, "occupancy"),
            (TelemetrySignal.TEMPERATURE, state.temperature, "°C",
             state.temperature_observed_at, state.temperature_source,
             state.temperature_simulated, "temperature"),
            (TelemetrySignal.POWER, state.energy.power_kw, "kW",
             state.energy.observed_at, state.energy.source, state.energy.is_simulated, "energy_power"),
            (TelemetrySignal.ENERGY, state.energy.energy_kwh, "kWh",
             state.energy.observed_at, state.energy.source, state.energy.is_simulated, "energy"),
            (TelemetrySignal.COST, state.energy.cost, state.energy.tariff.currency,
             state.energy.observed_at, state.energy.source, state.energy.is_simulated, "energy_cost"),
            (TelemetrySignal.TARIFF_RATE, state.tariff.rate_per_kwh,
             f"{state.tariff.currency}/kWh", state.tariff.observed_at,
             state.tariff.source, state.tariff.simulated, "tariff"),
        )
        observations = []
        for signal, value, unit, observed_at, source, simulated, quality_name in candidates:
            # A snapshot/generated timestamp is not evidence of observation time.
            if observed_at is None or observed_at.tzinfo is None or observed_at.utcoffset() is None:
                continue
            assessment = quality.get(quality_name)
            observations.append(TelemetryObservation(
                organization_id=scope["organization_id"], building_id=scope["building_id"],
                floor_id=scope["floor_id"], zone_id=scope["database_zone_id"],
                signal=signal, value=float(value), unit=unit, observed_at=observed_at,
                source=None if not source or source == "unknown" else source,
                quality_state=assessment.state.value if assessment is not None else None,
                simulated=bool(simulated) if simulated is not None else None,
            ))
        try:
            return self.repository.add_many(observations)
        except Exception as exc:
            # Persistence is observational only; the existing quality and safety
            # gates remain in the decision path. Do not leak database details.
            EventTrace.log_event("TELEMETRY_PERSISTENCE_FAILED", state.zone.zone_id,
                "telemetry_persistence", {"error_type": type(exc).__name__}, status="FAILED")
            return 0

    def persist_observation(self, observation: TelemetryObservation) -> int:
        """Persist one resolved observation only when every supplied scope ID matches configuration."""
        scope = self.configuration_repository.telemetry_scope(observation.zone_id)
        if scope is None or any((observation.organization_id != scope["organization_id"],
            observation.building_id != scope["building_id"], observation.floor_id != scope["floor_id"],
            observation.zone_id != scope["database_zone_id"])):
            raise ValueError("Observation scope does not match persistent zone ownership")
        return self.repository.add_many([observation])

    def find_persisted_boundary(self, *, zone_id: str, signal: TelemetrySignal, value: float,
                                unit: str, observed_at: datetime, source: str,
                                quality_state: str, simulated: bool) -> TelemetryObservation | None:
        """Return an exact tenant-scoped persisted observation matching a runtime boundary.

        A ZoneState snapshot alone is not evidence that telemetry was persisted.
        This lookup deliberately requires the observation timestamp and its
        provenance/quality/unit to match the record in the existing telemetry store.
        """
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            return None
        scope = self.configuration_repository.telemetry_scope(zone_id)
        if scope is None:
            return None
        candidates = self.repository.list_zone(
            organization_id=scope["organization_id"], building_id=scope["building_id"],
            zone_id=scope["database_zone_id"], start_at=observed_at, end_at=observed_at,
            limit=25, signal=signal.value)
        normalized_at = observed_at.astimezone(timezone.utc)
        for observation in candidates:
            if (observation.observed_at.astimezone(timezone.utc) == normalized_at
                    and abs(observation.value - float(value)) <= 1e-9
                    and observation.unit == unit and observation.source == source
                    and observation.quality_state == quality_state
                    and observation.simulated is simulated):
                return observation
        return None

    def list_zone(self, *, organization_id: str, building_id: str, zone_id: str,
                  start_at: datetime | None = None, end_at: datetime | None = None,
                  limit: int = 500, signal: str | None = None):
        return self.repository.list_zone(organization_id=organization_id, building_id=building_id,
            zone_id=zone_id, start_at=start_at, end_at=end_at, limit=limit, signal=signal)

    def list_building(self, *, organization_id: str, building_id: str,
                      start_at: datetime | None = None, end_at: datetime | None = None,
                      limit: int = 500, floor_id: str | None = None,
                      zone_id: str | None = None, signal: str | None = None):
        return self.repository.list_building(organization_id=organization_id, building_id=building_id,
            start_at=start_at, end_at=end_at, limit=limit, floor_id=floor_id,
            zone_id=zone_id, signal=signal)

    def list_floor(self, *, organization_id: str, building_id: str, floor_id: str,
                   start_at: datetime | None = None, end_at: datetime | None = None,
                   limit: int = 500, zone_id: str | None = None,
                   signal: str | None = None):
        return self.repository.list_floor(organization_id=organization_id,
            building_id=building_id, floor_id=floor_id, start_at=start_at,
            end_at=end_at, limit=limit, zone_id=zone_id, signal=signal)

    def aggregate(self, *, organization_id: str, building_id: str,
                  start_at: datetime | None = None, end_at: datetime | None = None,
                  floor_id: str | None = None, zone_id: str | None = None,
                  signal: str | None = None):
        return [TelemetryAggregation.model_validate(item) for item in self.repository.aggregate(
            organization_id=organization_id, building_id=building_id,
            start_at=start_at, end_at=end_at, floor_id=floor_id,
            zone_id=zone_id, signal=signal)]

    def cleanup_expired(self, *, now: datetime) -> int:
        cutoff = self.retention.cutoff(now=now)
        return self.repository.delete_before(cutoff) if cutoff is not None else 0
