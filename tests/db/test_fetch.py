from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from adrobot.db.campaigns import NewStream
from adrobot.db.editor import apply_fetch, load_editor
from adrobot.db.models import Offer, OfferBinding
from tests.db.helpers import seed_campaign


def _seed(engine: Engine) -> int:
    document: dict[str, object] = {
        "id": 901,
        "name": "Flow 2",
        "position": 2,
        "offers": [],
    }
    return seed_campaign(engine, 500, "spring", (NewStream(901, document, ((1, 100), (2, 40))),))


def _mark_unpushed(engine: Engine, offer_keitaro_id: int) -> None:
    with Session(engine) as session, session.begin():
        binding = session.scalar(
            select(OfferBinding).join(Offer).where(Offer.keitaro_id == offer_keitaro_id)
        )
        assert binding is not None
        binding.status = "pending"
        binding.unpushed = True
        binding.share = 40


def test_a_stream_without_offers_is_listed_last(engine: Engine):
    empty: dict[str, object] = {
        "id": 901,
        "name": "Flow 1",
        "position": 1,
        "filters": [{"name": "country"}],
    }
    with_offer: dict[str, object] = {"id": 902, "name": "Flow 2", "position": 2}
    campaign_id = seed_campaign(
        engine,
        500,
        "spring",
        (NewStream(901, empty, ()), NewStream(902, with_offer, ((1, 100),))),
    )
    view = load_editor(engine, campaign_id)
    assert view is not None
    assert [stream.name for stream in view.streams] == ["Flow 2", "Flow 1"]
    assert view.streams[0].filter_names == ()
    assert view.streams[1].filter_names == ("country",)


def test_fetch_keeps_an_unpushed_row_and_does_not_delete_a_missing_offer(engine: Engine):
    campaign_id = _seed(engine)
    _mark_unpushed(engine, 2)
    document: dict[str, object] = {
        "id": 901,
        "name": "Flow 2",
        "position": 2,
        "filters": [{"name": "country"}],
        "offers": [{"offer_id": 3, "share": 50, "state": "active"}],
    }
    assert apply_fetch(
        engine,
        campaign_id,
        (
            (
                901,
                document,
                ((3, 50),),
            ),
        ),
    )
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    rows = {row.keitaro_offer_id: row for row in stream.offers}
    assert set(rows) == {1, 2, 3}
    assert rows[1].status == "removed" and rows[1].share == 0 and rows[1].unpushed is False
    assert rows[2].status == "pending" and rows[2].share == 40 and rows[2].unpushed is True
    assert rows[3].status == "synced" and rows[3].share == 50 and rows[3].unpushed is False
    assert stream.status == "pending"


def test_fetch_that_matches_the_rows_changes_nothing(engine: Engine):
    campaign_id = _seed(engine)
    before = load_editor(engine, campaign_id)
    assert before is not None
    document: dict[str, object] = {"id": 901, "name": "Flow 2", "position": 2, "offers": []}
    offers = tuple((row.keitaro_offer_id, row.share) for row in before.streams[0].offers)
    assert apply_fetch(engine, campaign_id, ((901, document, offers),))
    after = load_editor(engine, campaign_id)
    assert after is not None
    assert after == before
