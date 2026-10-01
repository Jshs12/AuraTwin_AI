from dataclasses import dataclass, field
from datetime import datetime
from .roles import Role


@dataclass(frozen=True)
class User:
    user_id: str
    email: str
    password_hash: str = field(repr=False)
    role: Role
    active: bool = True
    building_ids: frozenset[str] = frozenset()
    organization_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Building:
    building_id: str
    name: str


@dataclass(frozen=True)
class AuditRecord:
    timestamp: datetime
    user_id: str | None
    role: str | None
    action: str
    resource: str
    resource_id: str | None
    building_id: str | None
    success: bool
    metadata: dict
