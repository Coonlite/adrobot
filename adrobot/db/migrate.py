"""Schema steps for a database file that already has rows.

create_all builds a missing table but never alters one that exists, so every column added or
dropped after the first release is a step here. Each step checks the file itself and does
nothing when the change is already there. PRAGMA user_version records how many steps ran.
One transaction covers all steps, and SQLite rolls DDL back with it.
"""

from collections.abc import Callable

from sqlalchemy import Connection, Engine, text


def _columns(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(text(f"PRAGMA table_info({table})")).all()
    return {str(row[1]) for row in rows}


def _published_status(connection: Connection) -> None:
    names = _columns(connection, "offer_bindings")
    if "published_status" in names or not names:
        return
    connection.execute(
        text(
            "ALTER TABLE offer_bindings "
            "ADD COLUMN published_status VARCHAR NOT NULL DEFAULT 'synced'"
        )
    )
    connection.execute(
        text(
            "UPDATE offer_bindings SET published_status = 'removed' "
            "WHERE status = 'removed' AND unpushed = 0"
        )
    )


def _published_pin(connection: Connection) -> None:
    names = _columns(connection, "offer_bindings")
    if "published_pinned" in names or not names:
        return
    connection.execute(
        text("ALTER TABLE offer_bindings ADD COLUMN published_pinned BOOLEAN NOT NULL DEFAULT 0")
    )
    connection.execute(
        text("UPDATE offer_bindings SET published_pinned = pinned WHERE unpushed = 0")
    )


def _push_journal(connection: Connection) -> None:
    names = _columns(connection, "streams")
    if not names:
        return
    added = {
        "push_state": "VARCHAR NOT NULL DEFAULT 'idle'",
        "push_token": "VARCHAR",
        "push_started_at": "FLOAT",
    }
    for name, ddl in added.items():
        if name not in names:
            connection.execute(text(f"ALTER TABLE streams ADD COLUMN {name} {ddl}"))


def _drop_unused(connection: Connection) -> None:
    """The stream version was bumped by every edit and read by nothing. alias was never read."""
    for table, column in (("streams", "version"), ("campaigns", "alias")):
        if column in _columns(connection, table):
            connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))


STEPS: tuple[Callable[[Connection], None], ...] = (
    _published_status,
    _published_pin,
    _push_journal,
    _drop_unused,
)


def migrate(engine: Engine) -> None:
    with engine.begin() as connection:
        done = int(connection.execute(text("PRAGMA user_version")).scalar_one())
        for step in STEPS[done:]:
            step(connection)
        connection.execute(text(f"PRAGMA user_version = {len(STEPS)}"))
