"""SQLAlchemy engine/session construction, opt-in and easy to replace in tests."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.database.config import DatabaseSettings


def create_database_engine(settings: DatabaseSettings | None = None, *, url: str | None = None) -> Engine:
    config = settings or DatabaseSettings.from_environment()
    database_url = url or config.require_url()
    engine = create_engine(database_url, pool_pre_ping=config.pool_pre_ping, echo=False)
    if database_url.startswith("sqlite:"):
        @event.listens_for(engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
