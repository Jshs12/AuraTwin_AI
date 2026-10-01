"""SQLAlchemy engine/session construction, opt-in and easy to replace in tests."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine import make_url
from pathlib import Path
from sqlalchemy.pool import StaticPool

from backend.database.config import DatabaseSettings


def create_database_engine(settings: DatabaseSettings | None = None, *, url: str | None = None) -> Engine:
    config = settings or DatabaseSettings.from_environment()
    database_url = url or config.require_url()
    parsed_url = make_url(database_url)
    connect_args = {"check_same_thread": False} if parsed_url.drivername.startswith("sqlite") else {}
    engine_options = {"pool_pre_ping": config.pool_pre_ping, "echo": False, "connect_args": connect_args}
    if parsed_url.drivername.startswith("sqlite") and parsed_url.database in (None, "", ":memory:"):
        engine_options["poolclass"] = StaticPool
    elif parsed_url.drivername.startswith("sqlite") and parsed_url.database:
        Path(parsed_url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **engine_options)
    if parsed_url.drivername.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
