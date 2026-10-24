import threading
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from adrobot.db.campaigns import NewStream
from adrobot.db.editor import add_offer
from adrobot.db.engine import configure_sqlite, serialize_writes
from adrobot.db.models import Base, Offer, Stream
from tests.db.helpers import seed_campaign


def test_operators_adding_the_same_new_offer_do_not_collide(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", configure_sqlite)
    serialize_writes(engine)
    Base.metadata.create_all(engine)
    document: dict[str, object] = {"id": 901, "offers": [{"offer_id": 1, "share": 100}]}
    campaign = seed_campaign(engine, 500, "n", (NewStream(901, document, ((1, 100),)),))
    with Session(engine) as session:
        stream = session.scalar(select(Stream.id))
    assert stream is not None

    failures: list[BaseException] = []
    gate = threading.Barrier(8)

    def operator() -> None:
        gate.wait()
        try:
            add_offer(engine, campaign, stream, 2)
        except BaseException as err:  # noqa: BLE001
            failures.append(err)

    threads = [threading.Thread(target=operator) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []
    with Session(engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(Offer).where(Offer.keitaro_id == 2))
            == 1
        )
    engine.dispose()
