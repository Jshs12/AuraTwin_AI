"""PostgreSQL-compatible persistence foundation; no connection on import."""

from backend.database.config import DatabaseSettings
from backend.database.models import Base

__all__ = ["Base", "DatabaseSettings"]
