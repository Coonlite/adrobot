from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session
from sqlalchemy.pool import ConnectionPoolEntry

from adrobot.db.migrate import migrate
from adrobot.db.models import Base

DATA_DIR = Path("data")
DEFAULT_URL = f"sqlite:///{DATA_DIR / 'adrobot.db'}"


def configure_sqlite(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def serialize_writes(engine: Engine) -> None:
    """Take SQLite's write lock when a write transaction starts, not when its first write runs.

    Without this, two editor actions that read, then write, race: the loser either fails
    with a unique-key or stale-row error, or waits on a lock it can no longer win. Under
    BEGIN IMMEDIATE the second action waits (busy_timeout) and then reads the committed rows.
    Plain sessions still start deferred, so reads never queue behind a write.
    """

    @event.listens_for(engine, "connect")
    def _manual_transactions(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry):
        dbapi_connection.isolation_level = None  # type: ignore[attr-defined]

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        immediate = bool(connection.get_execution_options().get("immediate"))
        connection.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def write_session(engine: Engine) -> Session:
    """A session whose transaction holds the write lock from its first statement."""
    return Session(engine.execution_options(immediate=True))


def make_engine(url: str = DEFAULT_URL) -> Engine:
    """One engine per process. The data folder and any missing tables are created."""
    if url == DEFAULT_URL:
        DATA_DIR.mkdir(exist_ok=True)
    engine = create_engine(url)
    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    migrate(engine)
    return engine
