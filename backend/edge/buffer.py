"""Small bounded FIFO buffer with explicit reject-newest overflow semantics."""
from collections import deque
from threading import RLock
from uuid import UUID

from backend.schemas.edge import EdgeObservationEnvelope


class BoundedObservationBuffer:
    OVERFLOW_POLICY = "NEWEST_REJECTED"

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Buffer capacity must be positive")
        self.capacity = capacity
        self._items: deque[EdgeObservationEnvelope] = deque()
        self._lock = RLock()

    @property
    def depth(self) -> int:
        with self._lock:
            return len(self._items)

    def enqueue(self, item: EdgeObservationEnvelope) -> bool:
        with self._lock:
            if len(self._items) >= self.capacity:
                return False
            self._items.append(item)
            return True

    def peek(self, limit: int | None = None) -> tuple[EdgeObservationEnvelope, ...]:
        with self._lock:
            count = len(self._items) if limit is None else max(0, limit)
            return tuple(list(self._items)[:count])

    def acknowledge(self, message_ids: tuple[UUID, ...]) -> int:
        """Remove only a successfully transmitted FIFO prefix."""
        removed = 0
        with self._lock:
            for message_id in message_ids:
                if not self._items or self._items[0].message_id != message_id:
                    break
                self._items.popleft()
                removed += 1
        return removed

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
