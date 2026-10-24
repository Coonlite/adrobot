import asyncio
import logging
from typing import Annotated, cast

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from adrobot.api.deps import EngineDep, KeitaroDep, SettingsDep
from adrobot.api.routes.campaigns import parse_offer_id
from adrobot.api.templates import failure, page
from adrobot.db.editor import (
    add_offer,
    apply_fetch,
    bring_back,
    cancel_stream,
    list_campaigns,
    load_editor,
    pin_offer,
    remove_offer,
)
from adrobot.db.push import (
    PushBusy,
    PushUncertain,
    abort_push,
    begin_push,
    finish_push,
)
from adrobot.domain.presence import PLACE_NOTE, campaign_place, index_states
from adrobot.domain.shares import ShareError
from adrobot.keitaro import client as keitaro
from adrobot.keitaro.bodies import PushError, put_body
from adrobot.keitaro.client import JsonObject, KeitaroError, KeitaroUnreachable
from adrobot.keitaro.streams import parse_streams

logger = logging.getLogger(__name__)

router = APIRouter()

FETCH_FAILED = "Keitaro did not return the streams. Nothing was changed."
OFFER_UNKNOWN = "That offer is not in the tracker's offer list. Nothing was changed."
OFFER_PRESENT = "That offer is already on this stream. Nothing was changed."
OFFER_ACTIVE = "That offer is not an active row on this stream. Nothing was changed."
OFFER_KEPT = "That offer is not removed. Nothing was changed."
SHARES_REFUSED = "The shares could not be split. Nothing was changed."
PUSH_FAILED = "Keitaro did not accept the stream. Nothing was marked published."
PUSH_BUSY = "A Push is already running on this stream. Nothing was changed."
PUSH_SETTLE = "The last Push got no answer. Fetch first, then decide. Nothing was changed."
PUSH_UNKNOWN = "Keitaro did not answer. The Push may have been applied, so Fetch to compare."
PUSH_LOST = "Keitaro accepted the stream, but a Fetch took over meanwhile. Fetch again to compare."
PUSH_EMPTY = "This stream has no offer collection. Nothing was sent."
MISSING_CAMPAIGN = "That campaign is not here."
MISSING_STREAM = "That stream is not on this campaign. Nothing was changed."
ARCHIVE_UNREAD = "Keitaro did not return the campaign lists. Archive state was not read."


def _names(catalog: list[keitaro.OfferInfo]) -> dict[int, str]:
    return {offer.id: offer.name for offer in catalog}


async def _states(client: KeitaroDep) -> dict[int, str] | None:
    found: dict[int, str] | None = None
    try:
        live, deleted = await keitaro.campaign_lists(client)
        found = index_states(live, deleted)
    except* KeitaroError:
        logger.warning("Campaign archive state could not be read")
    except* TimeoutError:
        logger.warning("Campaign archive state timed out")
    return found


@router.get("/campaigns", response_class=HTMLResponse)
async def campaign_list(request: Request, engine: EngineDep, client: KeitaroDep):
    campaigns = await asyncio.to_thread(list_campaigns, engine)
    states = await _states(client)
    places = (
        None
        if states is None
        else {row.id: campaign_place(states, row.keitaro_id) for row in campaigns}
    )
    shown: dict[str, object] = {
        "campaigns": campaigns,
        "editor": None,
        "places": places,
        "place_note": PLACE_NOTE,
    }
    if states is None:
        shown["notice"] = ARCHIVE_UNREAD
    return page(request, "editor.html", shown)


@router.get("/editor")
async def old_campaign_list() -> RedirectResponse:
    return RedirectResponse(url="/campaigns", status_code=307)


@router.get("/campaigns/{campaign_id}/editor")
async def old_campaign(campaign_id: int) -> RedirectResponse:
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=307)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def show_editor(
    request: Request, campaign_id: int, engine: EngineDep, client: KeitaroDep, settings: SettingsDep
):
    view = await asyncio.to_thread(load_editor, engine, campaign_id)
    if view is None:
        return failure(request, MISSING_CAMPAIGN, back="/campaigns")
    try:
        offers = await keitaro.list_offers(client)
        names = _names(offers)
        names_unavailable = False
    except KeitaroError:
        logger.warning("Offer names could not be read for campaign %s", campaign_id)
        offers = []
        names = {}
        names_unavailable = True
    try:
        clicks = await keitaro.clicks_today(client, view.keitaro_id)
        clicks_unavailable = False
    except KeitaroError:
        logger.warning("Click report failed for campaign %s", campaign_id)
        clicks = {}
        clicks_unavailable = True
    tracker_url = settings.keitaro_base_url.removesuffix("/admin_api/v1")
    states = await _states(client)
    shown: dict[str, object] = {
        "campaigns": (),
        "editor": view,
        "names": names,
        "names_unavailable": names_unavailable,
        "offers": offers,
        "clicks": clicks,
        "clicks_unavailable": clicks_unavailable,
        "tracker_link": f"{tracker_url}/admin/#!/campaigns/{view.keitaro_id}",
        "offers_link": f"{tracker_url}/admin/#!/offers/",
        "place": None if states is None else campaign_place(states, view.keitaro_id),
        "place_note": PLACE_NOTE,
    }
    if states is None:
        shown["notice"] = ARCHIVE_UNREAD
    return page(request, "editor.html", shown)


@router.post("/campaigns/{campaign_id}/fetch")
async def fetch_streams(request: Request, campaign_id: int, engine: EngineDep, client: KeitaroDep):
    view = await asyncio.to_thread(load_editor, engine, campaign_id)
    if view is None:
        return failure(request, MISSING_CAMPAIGN, back="/campaigns")
    try:
        payload = await keitaro.get_streams(client, view.keitaro_id)
        streams = parse_streams(payload)
    except KeitaroError as err:
        logger.warning("Stream fetch failed for campaign %s: %s", campaign_id, err)
        return failure(request, FETCH_FAILED)
    try:
        await asyncio.to_thread(apply_fetch, engine, campaign_id, streams)
    except PushBusy:
        return failure(request, PUSH_BUSY)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers")
async def add_stream_offer(
    request: Request,
    campaign_id: int,
    stream_id: int,
    engine: EngineDep,
    client: KeitaroDep,
    offer: Annotated[str, Form()],
):
    offer_id = parse_offer_id(offer)
    if offer_id is None:
        return failure(request, OFFER_UNKNOWN)
    try:
        await keitaro.get_offer(client, offer_id)
    except KeitaroError as err:
        logger.warning("Offer %s was not read: %s", offer_id, err)
        return failure(request, OFFER_UNKNOWN)
    try:
        added = await asyncio.to_thread(add_offer, engine, campaign_id, stream_id, offer_id)
    except ShareError as err:
        logger.warning("Add offer %s did not split: %s", offer_id, err)
        return failure(request, SHARES_REFUSED)
    if added is None:
        return failure(request, MISSING_STREAM)
    if not added:
        return failure(request, OFFER_PRESENT)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}/pin")
async def pin_stream_offer(
    request: Request, campaign_id: int, stream_id: int, offer_id: int, engine: EngineDep
):
    try:
        pinned = await asyncio.to_thread(pin_offer, engine, campaign_id, stream_id, offer_id)
    except ShareError as err:
        logger.warning("Pin offer %s did not split: %s", offer_id, err)
        return failure(request, SHARES_REFUSED)
    if pinned is None:
        return failure(request, MISSING_STREAM)
    if not pinned:
        return failure(request, OFFER_ACTIVE)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}/remove")
async def remove_stream_offer(
    request: Request, campaign_id: int, stream_id: int, offer_id: int, engine: EngineDep
):
    try:
        removed = await asyncio.to_thread(remove_offer, engine, campaign_id, stream_id, offer_id)
    except ShareError as err:
        logger.warning("Remove offer %s did not split: %s", offer_id, err)
        return failure(request, SHARES_REFUSED)
    if removed is None:
        return failure(request, MISSING_STREAM)
    if not removed:
        return failure(request, OFFER_ACTIVE)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/offers/{offer_id}/bring-back")
async def bring_back_stream_offer(
    request: Request, campaign_id: int, stream_id: int, offer_id: int, engine: EngineDep
):
    try:
        restored = await asyncio.to_thread(bring_back, engine, campaign_id, stream_id, offer_id)
    except ShareError as err:
        logger.warning("Bring back offer %s did not split: %s", offer_id, err)
        return failure(request, SHARES_REFUSED)
    if restored is None:
        return failure(request, MISSING_STREAM)
    if not restored:
        return failure(request, OFFER_KEPT)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/cancel")
async def cancel_stream_edit(request: Request, campaign_id: int, stream_id: int, engine: EngineDep):
    try:
        cancelled = await asyncio.to_thread(cancel_stream, engine, campaign_id, stream_id)
    except PushBusy:
        return failure(request, PUSH_BUSY)
    except PushUncertain:
        return failure(request, PUSH_SETTLE)
    if cancelled is None:
        return failure(request, MISSING_STREAM)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/streams/{stream_id}/push")
async def push_stream(
    request: Request, campaign_id: int, stream_id: int, engine: EngineDep, client: KeitaroDep
):
    try:
        ticket = await asyncio.to_thread(begin_push, engine, campaign_id, stream_id)
    except PushBusy:
        return failure(request, PUSH_BUSY)
    if ticket is None:
        return failure(request, MISSING_STREAM)
    sent = ticket.stream
    try:
        body = put_body(sent.document, sent.offers)
    except PushError as err:
        logger.warning("Push skipped for stream %s: %s", stream_id, err)
        await asyncio.to_thread(abort_push, engine, ticket, unknown=False)
        return failure(request, PUSH_EMPTY)
    try:
        await keitaro.put_stream(client, sent.keitaro_id, cast(JsonObject, body))
    except KeitaroUnreachable as err:
        logger.warning("Push to stream %s has no answer: %s", stream_id, err)
        await asyncio.to_thread(abort_push, engine, ticket, unknown=True)
        return failure(request, PUSH_UNKNOWN)
    except KeitaroError as err:
        logger.warning("Push failed for stream %s: %s", stream_id, err)
        await asyncio.to_thread(abort_push, engine, ticket, unknown=False)
        return failure(request, PUSH_FAILED)
    if not await asyncio.to_thread(finish_push, engine, ticket):
        logger.warning("Push claim for stream %s was lost before it finished", stream_id)
        return failure(request, PUSH_LOST)
    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)
