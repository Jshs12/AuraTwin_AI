"""Bounded SQLAlchemy-backed queue for validated observation envelopes only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import func, select

from backend.database.models import EdgeMessageQueueRecord
from backend.schemas.edge import EdgeObservationEnvelope


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        return value.replace(tzinfo=timezone.utc)
    return value


class DurableObservationQueue:
    """FIFO queue whose pending records survive process restarts.

    The database row stores a strict Pydantic observation envelope, never credentials or commands.
    """

    def __init__(self, sessions, *, edge_id: str, capacity: int, max_attempts: int = 5,
                 retry_base_seconds: float = 1.0, retry_max_seconds: float = 30.0,
                 clock=lambda: datetime.now(timezone.utc)):
        if sessions is None:
            raise ValueError("Durable queue requires a SQLAlchemy session factory")
        self.sessions = sessions
        self.edge_id = edge_id
        self.capacity = capacity
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.clock = clock
        self._lock = RLock()
        self.OVERFLOW_POLICY = "NEWEST_REJECTED"

    def _rows(self, session):
        return session.scalars(select(EdgeMessageQueueRecord)
            .where(EdgeMessageQueueRecord.edge_id == self.edge_id)
            .order_by(EdgeMessageQueueRecord.queue_sequence)).all()

    @property
    def depth(self) -> int:
        with self.sessions() as session:
            return int(session.scalar(select(func.count()).select_from(EdgeMessageQueueRecord)
                .where(EdgeMessageQueueRecord.edge_id == self.edge_id)) or 0)

    def enqueue(self, item: EdgeObservationEnvelope) -> bool:
        with self._lock, self.sessions.begin() as session:
            count = session.scalar(select(func.count()).select_from(EdgeMessageQueueRecord)
                .where(EdgeMessageQueueRecord.edge_id == self.edge_id)) or 0
            if count >= self.capacity:
                return False
            # Same edge/message id is idempotent locally; it cannot create a second queue record.
            existing = session.scalar(select(EdgeMessageQueueRecord.queue_sequence).where(
                EdgeMessageQueueRecord.edge_id == self.edge_id,
                EdgeMessageQueueRecord.message_id == item.message_id))
            if existing is not None:
                return True
            session.add(EdgeMessageQueueRecord(queue_record_id=uuid4(), message_id=item.message_id,
                edge_id=self.edge_id, organization_id=UUID(item.organization_id),
                building_id=UUID(item.building_id), created_at=self.clock(),
                observation_timestamp=item.observation_timestamp,
                payload=item.model_dump(mode="json"), delivery_state="PENDING", attempt_count=0,
                retryable=True))
            return True

    def peek(self, limit: int | None = None) -> tuple[EdgeObservationEnvelope, ...]:
        with self.sessions() as session:
            rows = self._rows(session)
            if limit is not None:
                rows = rows[:max(0, limit)]
            return tuple(EdgeObservationEnvelope.model_validate(row.payload) for row in rows)

    def recover_in_flight(self) -> int:
        now = self.clock()
        recovered = 0
        with self.sessions.begin() as session:
            for row in self._rows(session):
                if row.delivery_state == "IN_FLIGHT":
                    row.delivery_state = "FAILED"
                    row.retryable = row.attempt_count < self.max_attempts
                    row.last_error_reason = "RECOVERED_AFTER_RESTART"
                    row.next_attempt_at = now
                    recovered += 1
        return recovered

    def claim_batch(self, *, limit: int, now: datetime | None = None) -> tuple[EdgeObservationEnvelope, ...]:
        now = now or self.clock()
        claimed: list[EdgeObservationEnvelope] = []
        with self._lock, self.sessions.begin() as session:
            rows = self._rows(session)
            for row in rows:
                eligible_state = row.delivery_state == "PENDING" or (
                    row.delivery_state == "FAILED" and row.retryable)
                if not eligible_state or row.attempt_count >= self.max_attempts:
                    if row.delivery_state in {"PENDING", "FAILED"} and row.attempt_count >= self.max_attempts:
                        row.delivery_state, row.retryable = "FAILED", False
                        row.last_error_reason = row.last_error_reason or "MAX_ATTEMPTS_REACHED"
                    break  # strict FIFO: never skip an ineligible older record
                due = _aware(row.next_attempt_at)
                if due is not None and due > now:
                    break
                row.delivery_state = "IN_FLIGHT"
                row.attempt_count += 1
                row.last_attempt_at = now
                claimed.append(EdgeObservationEnvelope.model_validate(row.payload))
                if len(claimed) >= max(1, limit):
                    break
        return tuple(claimed)

    def acknowledge(self, message_ids: tuple[UUID, ...]) -> int:
        removed = 0
        with self._lock, self.sessions.begin() as session:
            for message_id in message_ids:
                row = session.scalar(select(EdgeMessageQueueRecord).where(
                    EdgeMessageQueueRecord.edge_id == self.edge_id,
                    EdgeMessageQueueRecord.message_id == message_id))
                if row is None:
                    continue
                session.delete(row)
                removed += 1
        return removed

    def fail(self, message_id: UUID, reason: str, *, retryable: bool = True,
             now: datetime | None = None) -> bool:
        now = now or self.clock()
        with self.sessions.begin() as session:
            row = session.scalar(select(EdgeMessageQueueRecord).where(
                EdgeMessageQueueRecord.edge_id == self.edge_id,
                EdgeMessageQueueRecord.message_id == message_id))
            if row is None:
                return False
            can_retry = retryable and row.attempt_count < self.max_attempts
            row.delivery_state = "FAILED"
            row.retryable = can_retry
            row.last_error_reason = reason[:80]
            if can_retry:
                delay = min(self.retry_max_seconds,
                    self.retry_base_seconds * (2 ** max(0, row.attempt_count - 1)))
                row.next_attempt_at = now + timedelta(seconds=delay)
            else:
                row.next_attempt_at = None
            return can_retry

    def retry_failed(self, message_id: UUID, *, now: datetime | None = None) -> bool:
        """Explicit operator-triggered recovery resets the bounded attempt budget."""
        with self.sessions.begin() as session:
            row = session.scalar(select(EdgeMessageQueueRecord).where(
                EdgeMessageQueueRecord.edge_id == self.edge_id,
                EdgeMessageQueueRecord.message_id == message_id))
            if row is None or row.delivery_state != "FAILED":
                return False
            row.delivery_state = "PENDING"
            row.retryable = True
            row.attempt_count = 0
            row.next_attempt_at = now or self.clock()
            return True

    def metrics(self) -> dict:
        with self.sessions() as session:
            rows = self._rows(session)
            return {"depth": len(rows), "oldest": _aware(rows[0].observation_timestamp) if rows else None,
                "newest": _aware(rows[-1].observation_timestamp) if rows else None,
                "retry_count": sum(max(0, row.attempt_count - 1) for row in rows),
                "attempt_count": sum(row.attempt_count for row in rows),
                "last_failure_at": max((_aware(row.last_attempt_at) for row in rows
                    if row.delivery_state == "FAILED" and row.last_attempt_at), default=None),
                "failed": sum(row.delivery_state == "FAILED" for row in rows)}

    def clear(self) -> None:
        with self.sessions.begin() as session:
            for row in self._rows(session):
                session.delete(row)
