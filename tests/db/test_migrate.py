from pathlib import Path

from sqlalchemy import Connection, create_engine, text

from adrobot.db.migrate import migrate

OLD_SCHEMA = (
    "CREATE TABLE campaigns (id INTEGER PRIMARY KEY, keitaro_id INTEGER NOT NULL UNIQUE,"
    " alias VARCHAR NOT NULL, name VARCHAR NOT NULL)",
    "CREATE TABLE streams (id INTEGER PRIMARY KEY, campaign_id INTEGER NOT NULL,"
    " keitaro_id INTEGER NOT NULL UNIQUE, version INTEGER NOT NULL, status VARCHAR NOT NULL,"
    " unpushed BOOLEAN NOT NULL, document JSON NOT NULL)",
    "CREATE TABLE offer_bindings (id INTEGER PRIMARY KEY, stream_id INTEGER NOT NULL,"
    " offer_id INTEGER NOT NULL, share INTEGER NOT NULL, pinned BOOLEAN NOT NULL,"
    " status VARCHAR NOT NULL, unpushed BOOLEAN NOT NULL, published_share INTEGER NOT NULL)",
    "INSERT INTO campaigns VALUES (1, 500, 'spring-abc', 'spring')",
    "INSERT INTO streams VALUES (1, 1, 901, 7, 'pending', 1, '{}')",
    "INSERT INTO offer_bindings VALUES (1, 1, 1, 60, 1, 'synced', 0, 60)",
    "INSERT INTO offer_bindings VALUES (2, 1, 2, 0, 0, 'removed', 0, 0)",
    "INSERT INTO offer_bindings VALUES (3, 1, 3, 40, 0, 'pending', 1, 0)",
)


def _columns(connection: Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def test_an_old_file_is_brought_to_the_current_schema_and_keeps_its_rows(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        for statement in OLD_SCHEMA:
            connection.execute(text(statement))

    migrate(engine)
    migrate(engine)

    with engine.connect() as connection:
        assert "alias" not in _columns(connection, "campaigns")
        assert "version" not in _columns(connection, "streams")
        assert {"push_state", "push_token", "push_started_at"} <= _columns(connection, "streams")
        assert {"published_status", "published_pinned"} <= _columns(connection, "offer_bindings")
        assert connection.execute(text("SELECT name FROM campaigns")).scalar_one() == "spring"
        assert connection.execute(text("SELECT status, push_state FROM streams")).one() == (
            "pending",
            "idle",
        )
        rows = connection.execute(
            text("SELECT id, published_status, published_pinned FROM offer_bindings ORDER BY id")
        ).all()
        assert [tuple(row) for row in rows] == [
            (1, "synced", 1),
            (2, "removed", 0),
            (3, "synced", 0),
        ]
    engine.dispose()
