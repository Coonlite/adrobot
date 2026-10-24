from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import StaticPool

from adrobot.db.engine import configure_sqlite
from adrobot.db.models import Base


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()
