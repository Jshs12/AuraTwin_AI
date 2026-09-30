"""Explicit database configuration. Secrets are excluded from repr/logging."""

from dataclasses import dataclass, field
import os


@dataclass(frozen=True)
class DatabaseSettings:
    url: str | None = field(default=None, repr=False)
    pool_pre_ping: bool = True

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "DatabaseSettings":
        values = os.environ if environ is None else environ
        url = values.get("DATABASE_URL", "").strip() or None
        return cls(url=url)

    def require_url(self) -> str:
        if not self.url:
            raise RuntimeError("DATABASE_URL must be configured before creating a database engine.")
        return self.url
