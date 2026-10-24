import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from adrobot.db.campaigns import NewStream
from adrobot.db.editor import (
    add_offer,
    apply_fetch,
    bring_back,
    cancel_stream,
    load_editor,
    pin_offer,
    remove_offer,
)
from adrobot.db.models import Offer, OfferBinding, Stream
from adrobot.db.push import (
    PushBusy,
    PushUncertain,
    abort_push,
    begin_push,
    finish_push,
)
from adrobot.domain.shares import ShareError
from tests.db.helpers import load_stream, seed_campaign


def _seed(engine: Engine) -> tuple[int, int]:
    document: dict[str, object] = {
        "id": 901,
        "name": "Flow 2",
        "position": 2,
        "offers": [{"offer_id": 1, "share": 100, "state": "active", "id": 5}],
    }
    campaign_id = seed_campaign(engine, 500, "spring", (NewStream(901, document, ((1, 100),)),))
    with Session(engine) as session:
        stream_id = session.scalar(select(Stream.id).where(Stream.keitaro_id == 901))
    assert stream_id is not None
    return campaign_id, stream_id


def test_add_recomputes_shares_and_leaves_the_stream_pending(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    assert add_offer(engine, campaign_id, stream_id, 2) is True
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    assert stream.status == "pending"
    shares = {row.keitaro_offer_id: (row.share, row.status) for row in stream.offers}
    assert shares == {1: (50, "synced"), 2: (50, "pending")}
    assert add_offer(engine, campaign_id, stream_id, 2) is False
    assert load_editor(engine, campaign_id) == view


def test_cancel_drops_the_pending_offer_and_restores_the_share(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    assert cancel_stream(engine, campaign_id, stream_id) is True
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    assert stream.status == "synced"
    assert [(row.keitaro_offer_id, row.share) for row in stream.offers] == [(1, 100)]
    with Session(engine) as session:
        assert session.scalar(select(Offer).where(Offer.keitaro_id == 2)) is not None
        binding = session.scalar(select(OfferBinding).join(Offer).where(Offer.keitaro_id == 2))
        assert binding is None


def test_cancel_restores_a_removed_offer_and_drops_a_pending_add(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    assert remove_offer(engine, campaign_id, stream_id, 1) is True
    assert cancel_stream(engine, campaign_id, stream_id) is True
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    assert stream.status == "synced"
    assert [(row.keitaro_offer_id, row.share, row.status) for row in stream.offers] == [
        (1, 100, "synced")
    ]
    with Session(engine) as session:
        binding = session.scalar(select(OfferBinding).join(Offer).where(Offer.keitaro_id == 2))
        assert binding is None


def test_cancel_puts_a_brought_back_offer_back_to_removed(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    with Session(engine) as session, session.begin():
        stream = session.get(Stream, stream_id)
        assert stream is not None
        offer = Offer(keitaro_id=2)
        session.add(offer)
        session.add(
            OfferBinding(
                stream=stream,
                offer=offer,
                share=0,
                status="removed",
                unpushed=False,
                published_share=0,
                published_status="removed",
            )
        )
    assert bring_back(engine, campaign_id, stream_id, 2) is True
    assert cancel_stream(engine, campaign_id, stream_id) is True
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    assert stream.status == "synced"
    shares = {row.keitaro_offer_id: (row.share, row.status) for row in stream.offers}
    assert shares == {1: (100, "synced"), 2: (0, "removed")}


def test_remove_zeros_the_share_and_bring_back_splits_again(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    assert remove_offer(engine, campaign_id, stream_id, 1) is True
    view = load_editor(engine, campaign_id)
    assert view is not None
    stream = view.streams[0]
    assert stream.status == "pending"
    shares = {row.keitaro_offer_id: (row.share, row.status) for row in stream.offers}
    assert shares == {1: (0, "removed"), 2: (100, "pending")}
    assert remove_offer(engine, campaign_id, stream_id, 1) is False
    assert remove_offer(engine, campaign_id, stream_id, 9) is False
    assert load_editor(engine, campaign_id) == view
    assert bring_back(engine, campaign_id, stream_id, 1) is True
    restored = load_editor(engine, campaign_id)
    assert restored is not None
    again = {row.keitaro_offer_id: (row.share, row.status) for row in restored.streams[0].offers}
    assert again == {1: (50, "pending"), 2: (50, "pending")}
    assert restored.streams[0].status == "pending"
    assert bring_back(engine, campaign_id, stream_id, 1) is False
    assert load_editor(engine, campaign_id) == restored


def test_remove_keeps_a_pin_and_bring_back_restores_it(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    with Session(engine) as session, session.begin():
        stream = session.get(Stream, stream_id)
        assert stream is not None
        for row in stream.offers:
            if row.offer.keitaro_id == 1:
                row.pinned = True
                row.share = 40
                row.published_share = 40
                row.status = "synced"
                row.unpushed = False
            else:
                row.share = 60
                row.published_share = 60
                row.status = "synced"
                row.unpushed = False
    assert remove_offer(engine, campaign_id, stream_id, 1) is True
    shown = load_editor(engine, campaign_id)
    assert shown is not None
    removed = next(row for row in shown.streams[0].offers if row.keitaro_offer_id == 1)
    assert removed.share == 0 and removed.pinned is True
    with Session(engine) as session:
        stored = session.scalar(select(OfferBinding).join(Offer).where(Offer.keitaro_id == 1))
        assert stored is not None and stored.share == 40 and stored.pinned is True
    assert bring_back(engine, campaign_id, stream_id, 1) is True
    back = load_editor(engine, campaign_id)
    assert back is not None
    shares = {row.keitaro_offer_id: (row.share, row.status) for row in back.streams[0].offers}
    assert shares == {1: (40, "pending"), 2: (60, "synced")}


def test_bring_back_writes_nothing_when_pins_exceed_100(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    with Session(engine) as session, session.begin():
        stream = session.get(Stream, stream_id)
        assert stream is not None
        first = stream.offers[0]
        first.pinned = True
        first.share = 50
        first.published_share = 50
        second = Offer(keitaro_id=2)
        session.add(second)
        session.add(
            OfferBinding(
                stream=stream,
                offer=second,
                share=60,
                pinned=True,
                status="removed",
                unpushed=False,
                published_share=0,
            )
        )
        stream.status = "synced"
        stream.unpushed = False
    seen = load_stream(engine, stream_id)
    assert seen is not None
    with pytest.raises(ShareError):
        bring_back(engine, campaign_id, stream_id, 2)
    after = load_stream(engine, stream_id)
    assert after is not None and after == seen
    removed = next(row for row in after.offers if row.keitaro_offer_id == 2)
    assert removed.status == "removed" and removed.share == 60


def test_pin_keeps_70_and_later_offers_split_the_rest(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    for offer_id in (2, 3, 4):
        add_offer(engine, campaign_id, stream_id, offer_id)
    with Session(engine) as session, session.begin():
        stream = session.get(Stream, stream_id)
        assert stream is not None
        for row in stream.offers:
            row.share = {1: 70, 2: 10, 3: 10, 4: 10}[row.offer.keitaro_id]
    assert pin_offer(engine, campaign_id, stream_id, 1) is True
    pinned = load_editor(engine, campaign_id)
    assert pinned is not None
    assert {row.keitaro_offer_id: (row.share, row.pinned) for row in pinned.streams[0].offers} == {
        1: (70, True),
        2: (10, False),
        3: (10, False),
        4: (10, False),
    }
    assert add_offer(engine, campaign_id, stream_id, 5) is True
    again = load_editor(engine, campaign_id)
    assert again is not None
    shares = {row.keitaro_offer_id: row.share for row in again.streams[0].offers}
    assert shares == {1: 70, 2: 8, 3: 8, 4: 7, 5: 7}
    assert sum(shares.values()) == 100
    assert cancel_stream(engine, campaign_id, stream_id) is True
    restored = load_editor(engine, campaign_id)
    assert restored is not None
    assert restored.streams[0].status == "synced"
    kept = next(row for row in restored.streams[0].offers if row.keitaro_offer_id == 1)
    assert kept.pinned is False and kept.share == 100


def test_pin_locks_70_beside_0_until_an_offer_is_added_or_brought_back(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    assert add_offer(engine, campaign_id, stream_id, 2) is True
    assert add_offer(engine, campaign_id, stream_id, 3) is True
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None and finish_push(engine, ticket) is True
    with Session(engine) as session, session.begin():
        stored = session.get(Stream, stream_id)
        assert stored is not None
        stored.status = "synced"
        stored.unpushed = False
        for row in stored.offers:
            if row.offer.keitaro_id == 3:
                row.status = "removed"
                row.share = 0
                row.unpushed = False
                row.published_status = "removed"
                row.published_share = 0
                continue
            row.share = {1: 70, 2: 0}[row.offer.keitaro_id]
            row.published_share = row.share
            row.status = "synced"
            row.unpushed = False
            row.published_status = "synced"
            row.pinned = False
    assert pin_offer(engine, campaign_id, stream_id, 1) is True
    locked = load_editor(engine, campaign_id)
    assert locked is not None
    assert locked.streams[0].status == "synced"
    pinned = load_stream(engine, stream_id)
    assert pinned is not None and pinned.unpushed is False
    assert {row.keitaro_offer_id: (row.share, row.pinned) for row in locked.streams[0].offers} == {
        1: (70, True),
        2: (0, False),
        3: (0, False),
    }
    assert pin_offer(engine, campaign_id, stream_id, 1) is True
    released = load_editor(engine, campaign_id)
    assert released is not None
    assert released.streams[0].status == "synced"
    released_rows = {
        row.keitaro_offer_id: (row.share, row.pinned) for row in released.streams[0].offers
    }
    assert released_rows == {1: (70, False), 2: (0, False), 3: (0, False)}
    assert pin_offer(engine, campaign_id, stream_id, 1) is True
    assert bring_back(engine, campaign_id, stream_id, 3) is True
    brought = load_editor(engine, campaign_id)
    assert brought is not None
    assert brought.streams[0].status == "pending"
    assert {row.keitaro_offer_id: (row.share, row.pinned) for row in brought.streams[0].offers} == {
        1: (70, True),
        2: (15, False),
        3: (15, False),
    }


def _states(engine: Engine, campaign_id: int) -> dict[int, tuple[str, int, bool]]:
    view = load_editor(engine, campaign_id)
    assert view is not None
    return {r.keitaro_offer_id: (r.status, r.share, r.unpushed) for r in view.streams[0].offers}


def test_finish_push_publishes_the_snapshot_that_was_sent(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None
    assert finish_push(engine, ticket) is True
    published = load_stream(engine, stream_id)
    assert published is not None and published.status == "synced"
    assert all(row.status == "synced" and row.unpushed is False for row in published.offers)
    assert {row.keitaro_offer_id: row.published_share for row in published.offers} == {1: 50, 2: 50}


def test_an_edit_during_push_stays_pending_after_finish(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None
    assert add_offer(engine, campaign_id, stream_id, 3) is True
    assert remove_offer(engine, campaign_id, stream_id, 1) is True
    assert finish_push(engine, ticket) is True
    view = load_editor(engine, campaign_id)
    assert view is not None and view.streams[0].status == "pending"
    rows = _states(engine, campaign_id)
    assert rows[1][0] == "removed" and rows[1][2] is True
    assert rows[3][0] == "pending" and rows[3][2] is True
    assert rows[2][0] == "synced" or rows[2][2] is True
    assert cancel_stream(engine, campaign_id, stream_id) is True
    back = _states(engine, campaign_id)
    assert 3 not in back
    assert back[1][:2] == ("synced", 50) and back[2][:2] == ("synced", 50)


def test_a_second_push_is_refused_until_the_first_ends_or_expires(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    first = begin_push(engine, campaign_id, stream_id, now=1000.0)
    assert first is not None
    with pytest.raises(PushBusy):
        begin_push(engine, campaign_id, stream_id, now=1005.0)
    assert begin_push(engine, campaign_id, stream_id, now=1031.0) is not None
    assert finish_push(engine, first) is False


def test_cancel_and_fetch_wait_for_a_push_in_flight(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None
    with pytest.raises(PushBusy):
        cancel_stream(engine, campaign_id, stream_id)
    with pytest.raises(PushBusy):
        apply_fetch(engine, campaign_id, ())
    assert finish_push(engine, ticket) is True
    assert cancel_stream(engine, campaign_id, stream_id) is True


def test_a_failed_push_keeps_the_edit_pending_and_frees_the_stream(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None
    abort_push(engine, ticket, unknown=True)
    view = load_editor(engine, campaign_id)
    assert view is not None and view.streams[0].status == "pending"
    assert finish_push(engine, ticket) is False
    again = begin_push(engine, campaign_id, stream_id)
    assert again is not None and finish_push(engine, again) is True


def test_fetch_takes_over_a_claim_that_expired(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id, now=1.0)
    assert ticket is not None
    assert apply_fetch(engine, campaign_id, ()) is True
    assert finish_push(engine, ticket) is False


def test_cancel_after_an_unanswered_push_waits_for_fetch(engine: Engine):
    campaign_id, stream_id = _seed(engine)
    add_offer(engine, campaign_id, stream_id, 2)
    ticket = begin_push(engine, campaign_id, stream_id)
    assert ticket is not None
    abort_push(engine, ticket, unknown=True)
    with pytest.raises(PushUncertain):
        cancel_stream(engine, campaign_id, stream_id)
    assert apply_fetch(engine, campaign_id, ()) is True
    assert cancel_stream(engine, campaign_id, stream_id) is True
