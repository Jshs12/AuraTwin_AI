"""Telemetry persistence port and SQLAlchemy adapter."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import TelemetryObservationRecord
from backend.schemas.telemetry import TelemetryObservation


class TelemetryRepository(Protocol):
    def add_many(self, observations: list[TelemetryObservation]) -> int: ...
    def list_zone(self, *, organization_id: str, building_id: str, zone_id: str,
                  start_at: datetime | None = None, end_at: datetime | None = None,
                  limit: int = 500) -> list[TelemetryObservation]: ...
    def list_building(self, *, organization_id: str, building_id: str,
                      start_at: datetime | None = None, end_at: datetime | None = None,
                      limit: int = 500) -> list[TelemetryObservation]: ...
    def delete_before(self, cutoff: datetime) -> int: ...


def idempotency_key(observation: TelemetryObservation) -> str:
    # Null source is encoded distinctly without inventing a persisted provider.
    timestamp = observation.observed_at.isoformat()
    material = "|".join((observation.organization_id, observation.building_id,
        observation.zone_id, observation.signal.value, timestamp,
        observation.source if observation.source is not None else "<no-source>"))
    return sha256(material.encode("utf-8")).hexdigest()


class SQLAlchemyTelemetryRepository:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def add_many(self, observations: list[TelemetryObservation]) -> int:
        inserted = 0
        with self.sessions.begin() as session:
            for observation in observations:
                key = idempotency_key(observation)
                exists = session.scalar(select(TelemetryObservationRecord.observation_id)
                    .where(TelemetryObservationRecord.idempotency_key == key))
                if exists is not None:
                    continue
                row = TelemetryObservationRecord(
                    organization_id=UUID(observation.organization_id),
                    building_id=UUID(observation.building_id), floor_id=UUID(observation.floor_id),
                    zone_id=UUID(observation.zone_id), signal=observation.signal.value,
                    value=float(observation.value), unit=observation.unit,
                    observed_at=observation.observed_at, source=observation.source,
                    quality_state=observation.quality_state, simulated=observation.simulated,
                    idempotency_key=key,
                )
                try:
                    # Nested transaction handles concurrent duplicate ingestion while
                    # preserving the outer batch transaction.
                    with session.begin_nested():
                        session.add(row)
                        session.flush()
                    inserted += 1
                except IntegrityError:
                    # A concurrent request already stored this observation key.
                    duplicate = session.scalar(select(TelemetryObservationRecord.observation_id)
                        .where(TelemetryObservationRecord.idempotency_key == key))
                    if duplicate is not None:
                        continue
                    raise
        return inserted

    def list_zone(self, *, organization_id: str, building_id: str, zone_id: str,
                  start_at: datetime | None = None, end_at: datetime | None = None,
                  limit: int = 500) -> list[TelemetryObservation]:
        return self._query(organization_id=organization_id, building_id=building_id,
            zone_id=zone_id, start_at=start_at, end_at=end_at, limit=limit)

    def list_building(self, *, organization_id: str, building_id: str,
                      start_at: datetime | None = None, end_at: datetime | None = None,
                      limit: int = 500) -> list[TelemetryObservation]:
        return self._query(organization_id=organization_id, building_id=building_id,
            start_at=start_at, end_at=end_at, limit=limit)

    def _query(self, *, organization_id: str, building_id: str, zone_id: str | None = None,
               start_at: datetime | None, end_at: datetime | None,
               limit: int) -> list[TelemetryObservation]:
        filters = [TelemetryObservationRecord.organization_id == UUID(organization_id),
                   TelemetryObservationRecord.building_id == UUID(building_id)]
        if zone_id is not None:
            filters.append(TelemetryObservationRecord.zone_id == UUID(zone_id))
        if start_at is not None:
            filters.append(TelemetryObservationRecord.observed_at >= start_at)
        if end_at is not None:
            filters.append(TelemetryObservationRecord.observed_at <= end_at)
        with self.sessions() as session:
            rows = session.scalars(select(TelemetryObservationRecord).where(and_(*filters))
                .order_by(TelemetryObservationRecord.observed_at.desc(),
                          TelemetryObservationRecord.observation_id)
                .limit(limit)).all()
            return [self._observation(row) for row in rows]

    def delete_before(self, cutoff: datetime) -> int:
        """Explicit retention operation; never invoked by app startup."""
        with self.sessions.begin() as session:
            result = session.execute(delete(TelemetryObservationRecord)
                                     .where(TelemetryObservationRecord.observed_at < cutoff))
            return int(result.rowcount or 0)

    @staticmethod
    def _observation(row: TelemetryObservationRecord) -> TelemetryObservation:
        return TelemetryObservation(organization_id=str(row.organization_id),
            building_id=str(row.building_id), floor_id=str(row.floor_id), zone_id=str(row.zone_id),
            signal=row.signal, value=row.value, unit=row.unit, observed_at=row.observed_at,
            source=row.source, quality_state=row.quality_state, simulated=row.simulated,
            ingested_at=row.ingested_at)
