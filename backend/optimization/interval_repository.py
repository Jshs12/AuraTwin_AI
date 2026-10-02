"""SQLAlchemy persistence adapter for optimization intervals."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import OptimizationIntervalRecord
from backend.schemas.optimization_interval import OptimizationInterval


_FIELDS = (
    "status", "started_at", "ended_at", "starting_occupancy", "ending_occupancy",
    "starting_occupancy_observed_at", "ending_occupancy_observed_at", "previous_setpoint",
    "optimized_setpoint", "setpoint_observed_at", "starting_temperature", "ending_temperature",
    "starting_temperature_observed_at", "ending_temperature_observed_at", "duration_seconds",
    "starting_energy_kwh", "starting_energy_observed_at", "ending_energy_kwh",
    "ending_energy_observed_at", "energy_unit", "energy_consumed_kwh", "energy_status",
    "energy_reason_code", "energy_source", "energy_quality", "energy_simulated",
    "tariff_rate_per_kwh", "currency", "starting_tariff_observed_at", "ending_tariff_observed_at",
    "tariff_source", "tariff_quality", "tariff_simulated", "cost_consumed", "cost_status",
    "cost_reason_code", "quality_state", "source", "simulated", "reason",
)


class SQLAlchemyOptimizationIntervalRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    @staticmethod
    def _uuid(value: str) -> UUID | None:
        try:
            return UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _model(row: OptimizationIntervalRecord) -> OptimizationInterval:
        values = {name: getattr(row, name) for name in _FIELDS}
        for name, value in values.items():
            if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
                values[name] = value.replace(tzinfo=timezone.utc)
        values.update(interval_id=str(row.interval_id), organization_id=str(row.organization_id),
            building_id=str(row.building_id), floor_id=str(row.floor_id),
            database_zone_id=str(row.zone_id), zone_id=row.zone_key)
        return OptimizationInterval.model_validate(values)

    @staticmethod
    def _row(interval: OptimizationInterval) -> OptimizationIntervalRecord:
        values = interval.model_dump()
        return OptimizationIntervalRecord(
            interval_id=UUID(interval.interval_id), organization_id=UUID(interval.organization_id),
            building_id=UUID(interval.building_id), floor_id=UUID(interval.floor_id),
            zone_id=UUID(interval.database_zone_id), zone_key=interval.zone_id,
            **{name: values[name] for name in _FIELDS},
        )

    def active(self, database_zone_id: str) -> OptimizationInterval | None:
        zone_uuid = self._uuid(database_zone_id)
        if zone_uuid is None:
            return None
        with self.sessions() as session:
            row = session.scalar(select(OptimizationIntervalRecord).where(
                OptimizationIntervalRecord.zone_id == zone_uuid,
                OptimizationIntervalRecord.status == "ACTIVE"))
            return self._model(row) if row else None

    def history(self, database_zone_id: str, *, limit: int = 100) -> list[OptimizationInterval]:
        zone_uuid = self._uuid(database_zone_id)
        if zone_uuid is None:
            return []
        with self.sessions() as session:
            rows = session.scalars(select(OptimizationIntervalRecord).where(
                OptimizationIntervalRecord.zone_id == zone_uuid,
                OptimizationIntervalRecord.status == "COMPLETED").order_by(
                    OptimizationIntervalRecord.started_at.desc(),
                    OptimizationIntervalRecord.interval_id).limit(limit)).all()
            return [self._model(row) for row in rows]

    def create(self, interval: OptimizationInterval) -> OptimizationInterval:
        try:
            with self.sessions.begin() as session:
                active = session.scalar(select(OptimizationIntervalRecord).where(
                    OptimizationIntervalRecord.zone_id == UUID(interval.database_zone_id),
                    OptimizationIntervalRecord.status == "ACTIVE"))
                if active is not None:
                    return self._model(active)
                row = self._row(interval)
                session.add(row)
                session.flush()
                return self._model(row)
        except IntegrityError:
            # A second worker may have persisted the unique active zone first.
            active = self.active(interval.database_zone_id)
            if active is not None:
                return active
            raise

    def update(self, interval: OptimizationInterval) -> OptimizationInterval:
        interval_uuid = self._uuid(interval.interval_id)
        if interval_uuid is None:
            raise ValueError("Optimization interval identifier is invalid")
        with self.sessions.begin() as session:
            row = session.get(OptimizationIntervalRecord, interval_uuid)
            if row is None:
                raise ValueError("Optimization interval was not found")
            for name in _FIELDS:
                setattr(row, name, getattr(interval, name))
            session.flush()
            return self._model(row)

    def get(self, interval_id: str) -> OptimizationInterval | None:
        interval_uuid = self._uuid(interval_id)
        if interval_uuid is None:
            return None
        with self.sessions() as session:
            row = session.get(OptimizationIntervalRecord, interval_uuid)
            return self._model(row) if row else None
