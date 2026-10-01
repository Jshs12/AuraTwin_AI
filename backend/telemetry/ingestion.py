"""Resolves configured points, validates provenance/quality, then reuses telemetry persistence."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import (BuildingRecord, DeviceRecord, FloorRecord,
    IntegrationRecord, PointMappingRecord, ZoneRecord)
from backend.schemas.data_quality import QualityState
from backend.schemas.provider_observation import ProviderObservation, ObservationIngestionResult
from backend.schemas.telemetry import TelemetryObservation, TelemetrySignal
from backend.services.data_quality import DataQualityGate


class RuntimeObservationConsumer:
    """Optional boundary for explicitly designated current inputs; historical data never updates runtime state."""
    def apply_current_observation(self, *, organization_id: str, building_id: str, floor_id: str,
                                  zone_id: str, zone_key: str, signal: str, value: float,
                                  observed_at, source: str, simulated: bool,
                                  quality_state: QualityState) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class ResolvedPoint:
    organization_id: str
    building_id: str
    floor_id: str
    zone_id: str
    zone_key: str
    zone_capacity: int
    signal: TelemetrySignal
    unit: str


class ProviderObservationIngestionService:
    """No public arbitrary-write route: trusted provider adapters call this boundary."""

    def __init__(self, sessions: sessionmaker[Session], telemetry_service,
                 data_quality: DataQualityGate | None = None,
                 runtime_consumer: RuntimeObservationConsumer | None = None):
        self.sessions = sessions
        self.telemetry_service = telemetry_service
        self.data_quality = data_quality or DataQualityGate()
        self.runtime_consumer = runtime_consumer

    @staticmethod
    def _uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

    def resolve(self, observation: ProviderObservation) -> tuple[ResolvedPoint | None, str | None]:
        integration_id = self._uuid(observation.integration_id)
        device_id = self._uuid(observation.device_id)
        point_id = self._uuid(observation.point_mapping_id)
        if None in (integration_id, device_id, point_id):
            return None, "IDENTIFIER_INVALID"
        query = (select(IntegrationRecord, DeviceRecord, PointMappingRecord, ZoneRecord, FloorRecord, BuildingRecord)
            .join(DeviceRecord, DeviceRecord.integration_id == IntegrationRecord.integration_id)
            .join(PointMappingRecord, PointMappingRecord.device_id == DeviceRecord.device_id)
            .join(ZoneRecord, ZoneRecord.zone_id == PointMappingRecord.zone_id)
            .join(FloorRecord, FloorRecord.floor_id == ZoneRecord.floor_id)
            .join(BuildingRecord, BuildingRecord.building_id == IntegrationRecord.building_id))
        with self.sessions() as session:
            rows = session.execute(query.where(
                IntegrationRecord.integration_id == integration_id,
                DeviceRecord.device_id == device_id,
                PointMappingRecord.point_mapping_id == point_id,
            )).all()
        if len(rows) != 1:
            return None, "MAPPING_OR_OWNERSHIP_NOT_FOUND"
        integration, device, point, zone, floor, building = rows[0]
        # Explicit owner chain checks; no zone inference from device names or identifiers.
        if (integration.archived_at is not None or device.archived_at is not None
                or zone.archived_at is not None or floor.archived_at is not None
                or building.archived_at is not None or integration.status != "CONFIGURED"
                or device.status != "CONFIGURED"):
            return None, "CONFIGURATION_INACTIVE"
        if point.mapping_status != "CONFIRMED":
            return None, f"MAPPING_{point.mapping_status}"
        if (point.zone_id is None or device.integration_id != integration.integration_id
                or integration.building_id != building.building_id
                or floor.building_id != integration.building_id):
            return None, "OWNERSHIP_CHAIN_INVALID"
        try:
            signal = TelemetrySignal(point.logical_signal)
        except ValueError:
            return None, "SIGNAL_NOT_PERSISTABLE"
        if not point.readable:
            return None, "POINT_NOT_READABLE"
        if point.data_type.lower() not in {"number", "numeric", "float", "integer", "int"}:
            return None, "POINT_DATA_TYPE_UNSUPPORTED"
        return ResolvedPoint(str(building.organization_id), str(building.building_id),
            str(floor.floor_id), str(zone.zone_id), zone.zone_key, zone.capacity,
            signal, point.unit or self._default_unit(signal)), None

    @staticmethod
    def _default_unit(signal: TelemetrySignal) -> str:
        return {TelemetrySignal.OCCUPANCY: "people", TelemetrySignal.TEMPERATURE: "°C",
            TelemetrySignal.POWER: "kW", TelemetrySignal.ENERGY: "kWh",
            TelemetrySignal.COST: "currency", TelemetrySignal.TARIFF_RATE: "currency/kWh",
            TelemetrySignal.COOLING_SETPOINT: "°C"}[signal]

    @staticmethod
    def _unit_is_compatible(signal: TelemetrySignal, unit: str) -> bool:
        accepted = {
            TelemetrySignal.OCCUPANCY: {"people", "person", "count"},
            TelemetrySignal.TEMPERATURE: {"°c", "c", "celsius"},
            TelemetrySignal.POWER: {"kw"}, TelemetrySignal.ENERGY: {"kwh"},
            TelemetrySignal.COST: set(), TelemetrySignal.TARIFF_RATE: set(),
            TelemetrySignal.COOLING_SETPOINT: {"°c", "c", "celsius"},
        }
        normalized = unit.strip()
        if signal == TelemetrySignal.COST:
            return bool(re.fullmatch(r"[A-Za-z]{3}", normalized))
        if signal == TelemetrySignal.TARIFF_RATE:
            return bool(re.fullmatch(r"[A-Za-z]{3}/kWh", normalized, re.IGNORECASE))
        return normalized.lower() in accepted[signal]

    def ingest(self, observation: ProviderObservation) -> ObservationIngestionResult:
        resolved, rejection = self.resolve(observation)
        if resolved is None:
            return ObservationIngestionResult(accepted=False, reason_code=rejection,
                observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=observation.quality_state)
        if not self._unit_is_compatible(resolved.signal, resolved.unit):
            return ObservationIngestionResult(accepted=False, signal=resolved.signal.value,
                zone_id=resolved.zone_id, observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=QualityState.INVALID,
                reason_code="POINT_UNIT_INCOMPATIBLE")
        quality_signal = {TelemetrySignal.POWER: "power_kw", TelemetrySignal.COST: "energy_cost",
                          TelemetrySignal.TARIFF_RATE: "tariff_rate"}.get(resolved.signal, resolved.signal.value)
        if resolved.signal == TelemetrySignal.COOLING_SETPOINT:
            quality_signal = "setpoint"
        freshness_signal = {TelemetrySignal.POWER: "energy", TelemetrySignal.COST: "energy",
                            TelemetrySignal.TARIFF_RATE: "tariff",
                            TelemetrySignal.COOLING_SETPOINT: "setpoint"}.get(resolved.signal, resolved.signal.value)
        if resolved.signal == TelemetrySignal.OCCUPANCY and not observation.value.is_integer():
            return ObservationIngestionResult(accepted=False, signal=resolved.signal.value,
                zone_id=resolved.zone_id, observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=QualityState.INVALID,
                reason_code="OCCUPANCY_COUNT_NOT_INTEGER")
        minimum = 0 if resolved.signal in {TelemetrySignal.OCCUPANCY, TelemetrySignal.POWER,
            TelemetrySignal.ENERGY, TelemetrySignal.COST, TelemetrySignal.TARIFF_RATE} else None
        maximum = resolved.zone_capacity if resolved.signal == TelemetrySignal.OCCUPANCY else None
        assessed = self.data_quality.assess(quality_signal, observation.value,
            source=observation.source, observation_timestamp=observation.observed_at,
            simulated=observation.simulated, numeric=True, minimum=minimum, maximum=maximum,
            freshness_signal=freshness_signal)
        quality = assessed.state
        reason = assessed.reason_code
        if observation.quality_state is not None and observation.quality_state != QualityState.VALID:
            quality = observation.quality_state
            reason = f"UPSTREAM_{quality.value}"
        if quality != QualityState.VALID:
            return ObservationIngestionResult(accepted=False, signal=resolved.signal.value,
                zone_id=resolved.zone_id, observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=quality, reason_code=reason or quality.value)
        if observation.runtime_input and self.runtime_consumer is None:
            return ObservationIngestionResult(accepted=False, signal=resolved.signal.value,
                zone_id=resolved.zone_id, observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=quality,
                reason_code="RUNTIME_CONSUMER_UNAVAILABLE")
        telemetry_observation = TelemetryObservation(
            organization_id=resolved.organization_id, building_id=resolved.building_id,
            floor_id=resolved.floor_id, zone_id=resolved.zone_id, signal=resolved.signal,
            value=float(observation.value), unit=resolved.unit,
            observed_at=observation.observed_at.astimezone(timezone.utc), source=observation.source,
            quality_state=quality.value, simulated=observation.simulated)
        try:
            inserted = self.telemetry_service.persist_observation(telemetry_observation)
        except Exception:
            return ObservationIngestionResult(accepted=False, signal=resolved.signal.value,
                zone_id=resolved.zone_id, observed_at=observation.observed_at, source=observation.source,
                simulated=observation.simulated, quality_state=quality, reason_code="PERSISTENCE_FAILED")
        runtime_applied = False
        reason_code = None
        if observation.runtime_input and self.runtime_consumer is not None:
            if resolved.signal not in {TelemetrySignal.OCCUPANCY, TelemetrySignal.TEMPERATURE,
                                       TelemetrySignal.COOLING_SETPOINT}:
                reason_code = "SIGNAL_HISTORICAL_ONLY"
            else:
                runtime_applied = bool(self.runtime_consumer.apply_current_observation(
                    organization_id=resolved.organization_id, building_id=resolved.building_id,
                    floor_id=resolved.floor_id, zone_id=resolved.zone_id, zone_key=resolved.zone_key,
                    signal=resolved.signal.value, value=float(observation.value),
                    observed_at=observation.observed_at, source=observation.source,
                    simulated=observation.simulated, quality_state=quality))
                if not runtime_applied:
                    reason_code = "RUNTIME_STATE_REJECTED"
        else:
            reason_code = "DUPLICATE" if inserted == 0 else None
        return ObservationIngestionResult(accepted=True, persisted=inserted > 0, duplicate=inserted == 0,
            signal=resolved.signal.value, zone_id=resolved.zone_id, observed_at=observation.observed_at,
            source=observation.source, simulated=observation.simulated, quality_state=quality,
            reason_code=reason_code, runtime_input_applied=runtime_applied)
