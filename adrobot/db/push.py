"""One Push to Keitaro as a protocol: claim the stream, send, then publish what was sent.

The stream is claimed with one conditional UPDATE and a snapshot of its rows is taken. The PUT
goes out from that snapshot, and finishing publishes the snapshot, so an edit made while the
request was in flight stays pending. Nothing here calls Keitaro.
"""

import time
import uuid
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Engine, or_, update
from sqlalchemy.engine import CursorResult

from adrobot.db.engine import write_session
from adrobot.db.models import Stream
from adrobot.db.views import StreamView, stream_view

PUSH_LEASE = 30.0


class PushBusy(Exception):
    """A Push holds this stream. Cancel, Fetch, and a second Push wait for it."""


class PushUncertain(Exception):
    """The last Push got no answer. Keitaro may hold the rows, so only Fetch can settle it."""


@dataclass(frozen=True, slots=True)
class PushTicket:
    """What one Push sends. Finishing publishes this snapshot, never the live rows."""

    token: str
    stream: StreamView


def push_in_flight(stream: Stream, now: float) -> bool:
    started = stream.push_started_at
    return stream.push_state == "pushing" and started is not None and now - started < PUSH_LEASE


def begin_push(
    engine: Engine, campaign_id: int, stream_id: int, now: float | None = None
) -> PushTicket | None:
    """Claim the stream for one Push and snapshot the rows that will be sent.

    None when the stream is absent. PushBusy while another Push holds a fresh claim.
    The claim is one conditional UPDATE, so two requests cannot both win.
    """
    moment = time.time() if now is None else now
    token = uuid.uuid4().hex
    with write_session(engine) as session, session.begin():
        claimed = cast(
            CursorResult[tuple[object, ...]],
            session.execute(
                update(Stream)
                .where(
                    Stream.id == stream_id,
                    Stream.campaign_id == campaign_id,
                    or_(
                        Stream.push_state != "pushing",
                        Stream.push_started_at.is_(None),
                        Stream.push_started_at <= moment - PUSH_LEASE,
                    ),
                )
                .values(push_state="pushing", push_token=token, push_started_at=moment)
            ),
        )
        if claimed.rowcount != 1:
            stream = session.get(Stream, stream_id)
            if stream is None or stream.campaign_id != campaign_id:
                return None
            raise PushBusy
        stream = session.get(Stream, stream_id)
        if stream is None:
            return None
        return PushTicket(token=token, stream=stream_view(stream))


def finish_push(engine: Engine, ticket: PushTicket) -> bool:
    """Publish the snapshot that Keitaro accepted. False when the claim was lost.

    A row edited while the request was in flight differs from the snapshot, so it
    stays pending. A row added in that window is not in the snapshot and is untouched.
    """
    sent = {row.keitaro_offer_id: row for row in ticket.stream.offers}
    with write_session(engine) as session, session.begin():
        released = cast(
            CursorResult[tuple[object, ...]],
            session.execute(
                update(Stream)
                .where(Stream.id == ticket.stream.id, Stream.push_token == ticket.token)
                .values(
                    push_state="idle",
                    push_token=None,
                    push_started_at=None,
                )
            ),
        )
        if released.rowcount != 1:
            return False
        stream = session.get(Stream, ticket.stream.id)
        if stream is None:
            return False
        for row in stream.offers:
            snap = sent.get(row.offer.keitaro_id)
            if snap is None:
                continue
            gone = snap.status == "removed"
            row.published_share = 0 if gone else snap.share
            row.published_status = "removed" if gone else "synced"
            row.published_pinned = snap.pinned
            now_gone = row.status == "removed"
            edited = now_gone != gone or (not gone and row.share != snap.share)
            row.unpushed = edited
            if not edited:
                row.status = "removed" if gone else "synced"
        dirty = any(row.unpushed for row in stream.offers)
        stream.unpushed = dirty
        stream.status = "pending" if dirty else "synced"
        return True


def abort_push(engine: Engine, ticket: PushTicket, *, unknown: bool) -> None:
    """Release the claim. The rows stay as they were, so the edit is still pending.

    unknown is True when no answer came back, so Keitaro may have applied the PUT.
    """
    with write_session(engine) as session, session.begin():
        session.execute(
            update(Stream)
            .where(Stream.id == ticket.stream.id, Stream.push_token == ticket.token)
            .values(
                push_state="uncertain" if unknown else "idle",
                push_token=None,
                push_started_at=None,
            )
        )
