"""Telemetry persistence port and SQLAlchemy adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlalchemy import and_, select, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import TelemetryObservationRecord
from backend.schemas.telemetry import TelemetryObservation


class TelemetryRepository(Protocol):
    def add_many(self, observations: list[TelemetryObservation]) -> int: ...
    def list_zone(self, *, organization_id: str, building_id: str, zone_id: str,
                  start_at: datetime | None = None, end_at: datetime | None = None,
                  limit: int = 500, signal: str | None = None) -> list[TelemetryObservation]: ...
    def list_building(self, *, organization_id: str, building_id: str,
                      start_at: datetime | None = None, end_at: datetime | None = None,
                      limit: int = 500, floor_id: str | None = None,
                      zone_id: str | None = None, signal: str | None = None) -> list[TelemetryObservation]: ...
    def list_floor(self, *, organization_id: str, building_id: str, floor_id: str,
                   start_at: datetime | None = None, end_at: datetime | None = None,
                   limit: int = 500, zone_id: str | None = None,
                   signal: str | None = None) -> list[TelemetryObservation]: ...
    def aggregate(self, *, organization_id: str, building_id: str,
                   start_at: datetime | None = None, end_at: datetime | None = None,
                   floor_id: str | None = None, zone_id: str | None = None,
                   signal: str | None = None) -> list[dict]: ...
    def delete_before(self, cutoff: datetime) -> int: ...


def idempotency_key(observation: TelemetryObservation) -> str:
    # Null source is encoded distinctly without inventing a persisted provider.
    timestamp = observation.observed_at.astimezone(timezone.utc).isoformat()
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
                  limit: int = 500, signal: str | None = None) -> list[TelemetryObservation]:
        return self._query(organization_id=organization_id, building_id=building_id,
            zone_id=zone_id, start_at=start_at, end_at=end_at, limit=limit, signal=signal)

    def list_building(self, *, organization_id: str, building_id: str,
                      start_at: datetime | None = None, end_at: datetime | None = None,
                      limit: int = 500, floor_id: str | None = None,
                      zone_id: str | None = None, signal: str | None = None) -> list[TelemetryObservation]:
        return self._query(organization_id=organization_id, building_id=building_id,
            start_at=start_at, end_at=end_at, limit=limit, floor_id=floor_id,
            zone_id=zone_id, signal=signal)

    def list_floor(self, *, organization_id: str, building_id: str, floor_id: str,
                   start_at: datetime | None = None, end_at: datetime | None = None,
                   limit: int = 500, zone_id: str | None = None,
                   signal: str | None = None) -> list[TelemetryObservation]:
        return self._query(organization_id=organization_id, building_id=building_id,
            floor_id=floor_id, zone_id=zone_id, start_at=start_at, end_at=end_at,
            limit=limit, signal=signal)

    def _query(self, *, organization_id: str, building_id: str, zone_id: str | None = None,
               start_at: datetime | None, end_at: datetime | None,
               limit: int, floor_id: str | None = None,
               signal: str | None = None) -> list[TelemetryObservation]:
        filters = [TelemetryObservationRecord.organization_id == UUID(organization_id),
                   TelemetryObservationRecord.building_id == UUID(building_id)]
        if zone_id is not None:
            filters.append(TelemetryObservationRecord.zone_id == UUID(zone_id))
        if floor_id is not None:
            filters.append(TelemetryObservationRecord.floor_id == UUID(floor_id))
        if signal is not None:
            filters.append(TelemetryObservationRecord.signal == signal)
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

    def aggregate(self, *, organization_id: str, building_id: str,
                   start_at: datetime | None = None, end_at: datetime | None = None,
                   floor_id: str | None = None, zone_id: str | None = None,
                   signal: str | None = None) -> list[dict]:
        filters = [TelemetryObservationRecord.organization_id == UUID(organization_id),
                   TelemetryObservationRecord.building_id == UUID(building_id)]
        if floor_id is not None:
            filters.append(TelemetryObservationRecord.floor_id == UUID(floor_id))
        if zone_id is not None:
            filters.append(TelemetryObservationRecord.zone_id == UUID(zone_id))
        if signal is not None:
            filters.append(TelemetryObservationRecord.signal == signal)
        if start_at is not None:
            filters.append(TelemetryObservationRecord.observed_at >= start_at)
        if end_at is not None:
            filters.append(TelemetryObservationRecord.observed_at <= end_at)
        query = (select(TelemetryObservationRecord.signal,
                func.count(TelemetryObservationRecord.observation_id),
                func.min(TelemetryObservationRecord.value),
                func.max(TelemetryObservationRecord.value),
                func.avg(TelemetryObservationRecord.value))
            .where(and_(*filters)).group_by(TelemetryObservationRecord.signal))
        with self.sessions() as session:
            return [{"signal": row[0], "count": int(row[1]), "minimum": row[2],
                     "maximum": row[3], "average": float(row[4])}
                    for row in session.execute(query).all()]

    def delete_before(self, cutoff: datetime) -> int:
        """Explicit retention operation; never invoked by app startup."""
        with self.sessions.begin() as session:
            result = session.execute(delete(TelemetryObservationRecord)
                                     .where(TelemetryObservationRecord.observed_at < cutoff))
            return int(result.rowcount or 0)

    @staticmethod
    def _observation(row: TelemetryObservationRecord) -> TelemetryObservation:
        observed_at = row.observed_at
        ingested_at = row.ingested_at
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if ingested_at is not None and (ingested_at.tzinfo is None or ingested_at.utcoffset() is None):
            ingested_at = ingested_at.replace(tzinfo=timezone.utc)
        return TelemetryObservation(organization_id=str(row.organization_id),
            building_id=str(row.building_id), floor_id=str(row.floor_id), zone_id=str(row.zone_id),
            signal=row.signal, value=row.value, unit=row.unit, observed_at=observed_at,
            source=row.source, quality_state=row.quality_state, simulated=row.simulated,
            ingested_at=ingested_at)
