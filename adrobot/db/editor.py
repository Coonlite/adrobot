import copy
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from adrobot.db.engine import write_session
from adrobot.db.models import Campaign, Offer, OfferBinding, Stream
from adrobot.db.push import PushBusy, PushUncertain, push_in_flight
from adrobot.domain.shares import ShareRow, split_shares


@dataclass(frozen=True, slots=True)
class CampaignLink:
    id: int
    keitaro_id: int
    name: str


@dataclass(frozen=True, slots=True)
class EditorOffer:
    keitaro_offer_id: int
    share: int
    pinned: bool
    status: str
    unpushed: bool


@dataclass(frozen=True, slots=True)
class EditorStream:
    id: int
    keitaro_id: int
    name: str
    position: int
    filter_names: tuple[str, ...]
    status: str
    offers: tuple[EditorOffer, ...]


@dataclass(frozen=True, slots=True)
class EditorView:
    id: int
    keitaro_id: int
    name: str
    streams: tuple[EditorStream, ...]


def list_campaigns(engine: Engine) -> tuple[CampaignLink, ...]:
    with Session(engine) as session:
        rows = session.scalars(select(Campaign).order_by(Campaign.id.desc()))
        return tuple(
            CampaignLink(id=row.id, keitaro_id=row.keitaro_id, name=row.name) for row in rows
        )


def _text(document: dict[str, object], key: str) -> str:
    value = document.get(key)
    return value if isinstance(value, str) else ""


def _position(document: dict[str, object]) -> int:
    value = document.get("position")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _filter_names(document: dict[str, object]) -> tuple[str, ...]:
    raw = document.get("filters")
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        name = cast(dict[object, object], item).get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return tuple(names)


def load_editor(engine: Engine, campaign_id: int) -> EditorView | None:
    with Session(engine) as session:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            return None
        local_id = campaign.id
        tracker_id = campaign.keitaro_id
        name = campaign.name
        streams = tuple(
            EditorStream(
                id=stream.id,
                keitaro_id=stream.keitaro_id,
                name=_text(stream.document, "name"),
                position=_position(stream.document),
                filter_names=_filter_names(stream.document),
                status=stream.status,
                offers=tuple(
                    EditorOffer(
                        keitaro_offer_id=row.offer.keitaro_id,
                        share=0 if row.status == "removed" else row.share,
                        pinned=row.pinned,
                        status=row.status,
                        unpushed=row.unpushed,
                    )
                    for row in stream.offers
                ),
            )
            for stream in campaign.streams
        )
    ordered = tuple(
        sorted(
            streams,
            key=lambda stream: (len(stream.offers) == 0, stream.position, stream.keitaro_id),
        )
    )
    return EditorView(id=local_id, keitaro_id=tracker_id, name=name, streams=ordered)


def _offer(session: Session, cache: dict[int, Offer], keitaro_id: int) -> Offer:
    found = cache.get(keitaro_id)
    if found is not None:
        return found
    found = session.scalar(select(Offer).where(Offer.keitaro_id == keitaro_id))
    if found is None:
        found = Offer(keitaro_id=keitaro_id)
        session.add(found)
    cache[keitaro_id] = found
    return found


def _take_remote(binding: OfferBinding, share: int) -> None:
    binding.status = "synced"
    binding.share = share
    binding.unpushed = False
    binding.published_share = share
    binding.published_status = "synced"


def _mark_removed(binding: OfferBinding) -> None:
    binding.status = "removed"
    binding.share = 0
    binding.unpushed = False
    binding.published_share = 0
    binding.published_status = "removed"


def _reconcile(
    session: Session, stream: Stream, offers: tuple[tuple[int, int], ...], cache: dict[int, Offer]
) -> None:
    remote = dict(offers)
    bindings = list(stream.offers)
    known = {row.offer.keitaro_id: row for row in bindings}
    for binding in bindings:
        if binding.unpushed:
            continue
        offer_id = binding.offer.keitaro_id
        if offer_id in remote:
            _take_remote(binding, remote[offer_id])
        else:
            _mark_removed(binding)
    for offer_id, share in offers:
        if offer_id in known:
            continue
        binding = OfferBinding(
            stream=stream,
            offer=_offer(session, cache, offer_id),
            share=share,
            status="synced",
            unpushed=False,
            published_share=share,
            published_status="synced",
        )
        session.add(binding)
        bindings.append(binding)
    dirty = any(row.unpushed for row in bindings)
    stream.unpushed = dirty
    stream.status = "pending" if dirty else "synced"


def apply_fetch(
    engine: Engine,
    campaign_id: int,
    streams: tuple[tuple[int, Mapping[str, object], tuple[tuple[int, int], ...]], ...],
) -> bool:
    """Reconcile tracker streams into the local campaign. False when the campaign is absent."""
    with write_session(engine) as session, session.begin():
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            return False
        local = {stream.keitaro_id: stream for stream in campaign.streams}
        moment = time.time()
        if any(push_in_flight(stream, moment) for stream in local.values()):
            raise PushBusy
        for stream in local.values():
            stream.push_state = "idle"
            stream.push_token = None
            stream.push_started_at = None
        cache: dict[int, Offer] = {}
        for keitaro_id, document, offers in streams:
            stored = copy.deepcopy(dict(document))
            stream = local.get(keitaro_id)
            if stream is None:
                stream = Stream(campaign=campaign, keitaro_id=keitaro_id, document=stored)
                session.add(stream)
            else:
                stream.document = stored
            _reconcile(session, stream, offers, cache)
        return True


@dataclass(frozen=True, slots=True)
class _Share:
    id: int
    share: int
    pinned: bool
    status: str


def _share_row(binding: OfferBinding) -> ShareRow:
    return _Share(
        id=binding.offer.keitaro_id,
        share=binding.share,
        pinned=binding.pinned,
        status=binding.status,
    )


def _write_shares(bindings: list[OfferBinding]) -> None:
    shares = split_shares(_share_row(row) for row in bindings)
    for row in bindings:
        # A removed pin keeps its amount for bring-back. The editor shows 0.
        if row.status == "removed" and row.pinned:
            continue
        row.share = shares[row.offer.keitaro_id]


def _flag_unpublished(bindings: list[OfferBinding]) -> None:
    for row in bindings:
        if row.status == "removed":
            continue
        row.unpushed = row.status == "pending" or row.share != row.published_share


def mark_edited(stream: Stream) -> None:
    stream.unpushed = True
    stream.status = "pending"


def _owned(session: Session, campaign_id: int, stream_id: int) -> Stream | None:
    stream = session.get(Stream, stream_id)
    if stream is None or stream.campaign_id != campaign_id:
        return None
    return stream


def add_offer(engine: Engine, campaign_id: int, stream_id: int, offer_id: int) -> bool | None:
    """Bind an offer as pending and recompute shares. None when the stream is absent.

    False when that offer is already on the stream, including a removed row. Nothing is written.
    """
    with write_session(engine) as session, session.begin():
        stream = _owned(session, campaign_id, stream_id)
        if stream is None:
            return None
        bindings = list(stream.offers)
        if any(row.offer.keitaro_id == offer_id for row in bindings):
            return False
        binding = OfferBinding(
            stream=stream,
            offer=_offer(session, {}, offer_id),
            share=0,
            status="pending",
            unpushed=True,
            published_share=0,
            published_status="absent",
        )
        session.add(binding)
        bindings.append(binding)
        _write_shares(bindings)
        _flag_unpublished(bindings)
        mark_edited(stream)
        return True


def _binding(bindings: list[OfferBinding], offer_id: int) -> OfferBinding | None:
    for row in bindings:
        if row.offer.keitaro_id == offer_id:
            return row
    return None


def remove_offer(engine: Engine, campaign_id: int, stream_id: int, offer_id: int) -> bool | None:
    """Take an offer out of the split. None when the stream is absent.

    False when that offer is missing or already removed. Nothing is written.
    The row stays, its share is 0, and a pin keeps its amount for bring-back.
    """
    with write_session(engine) as session, session.begin():
        stream = _owned(session, campaign_id, stream_id)
        if stream is None:
            return None
        bindings = list(stream.offers)
        target = _binding(bindings, offer_id)
        if target is None or target.status == "removed":
            return False
        target.status = "removed"
        target.unpushed = True
        _write_shares(bindings)
        _flag_unpublished(bindings)
        mark_edited(stream)
        return True


def pin_offer(engine: Engine, campaign_id: int, stream_id: int, offer_id: int) -> bool | None:
    """Fix the current share, or release it when that pin is pressed again.

    None when the stream is absent. False when that offer is missing or removed.
    Nothing else is written. Active shares stay as they are, and the stream does
    not become pending. Add, remove, and bring back run the split.
    """
    with write_session(engine) as session, session.begin():
        stream = _owned(session, campaign_id, stream_id)
        if stream is None:
            return None
        target = _binding(list(stream.offers), offer_id)
        if target is None or target.status == "removed":
            return False
        target.pinned = not target.pinned
        return True


def bring_back(engine: Engine, campaign_id: int, stream_id: int, offer_id: int) -> bool | None:
    """Put a removed offer back into the split. None when the stream is absent.

    False when that offer is not a removed row. Nothing is written. A pin total
    above 100 raises ShareError and leaves the row removed.
    """
    with write_session(engine) as session, session.begin():
        stream = _owned(session, campaign_id, stream_id)
        if stream is None:
            return None
        bindings = list(stream.offers)
        target = _binding(bindings, offer_id)
        if target is None or target.status != "removed":
            return False
        target.status = "pending"
        target.unpushed = True
        _write_shares(bindings)
        _flag_unpublished(bindings)
        mark_edited(stream)
        return True


def cancel_stream(engine: Engine, campaign_id: int, stream_id: int) -> bool | None:
    """Restore the last published rows. None when the stream is absent.

    A pending add is deleted. Every other row returns to the status and share of
    the last Push. PushBusy while a Push is in flight. PushUncertain after a Push that
    got no answer, until Fetch settles it. Keitaro is not called.
    """
    with write_session(engine) as session, session.begin():
        stream = _owned(session, campaign_id, stream_id)
        if stream is None:
            return None
        if push_in_flight(stream, time.time()):
            raise PushBusy
        if stream.push_state == "uncertain":
            raise PushUncertain
        for row in list(stream.offers):
            if row.published_status == "absent":
                session.delete(row)
                continue
            row.pinned = row.published_pinned
            if row.published_status == "removed":
                row.status = "removed"
                row.unpushed = False
                if not row.pinned:
                    row.share = 0
                continue
            row.status = "synced"
            row.share = row.published_share
            row.unpushed = False
        stream.unpushed = False
        stream.status = "synced"
        return True
