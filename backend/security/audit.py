from collections import deque
from datetime import datetime, timezone
from threading import RLock
from .models import AuditRecord


class AuditService:
    def __init__(self, capacity: int = 2000):
        self._records = deque(maxlen=capacity)
        self._lock = RLock()

    def record(self, *, user_id=None, role=None, action, resource, resource_id=None,
               building_id=None, success=True, metadata=None):
        # Metadata is deliberately allow-listed at call sites; never pass request headers/tokens.
        row = AuditRecord(datetime.now(timezone.utc), user_id, role, action, resource,
                          resource_id, building_id, bool(success), dict(metadata or {}))
        with self._lock: self._records.append(row)
        return row

    def list_records(self):
        with self._lock: return list(self._records)
