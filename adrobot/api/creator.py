import asyncio
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from adrobot.db.campaigns import NewStream, add_created_stream, save_campaign
from adrobot.keitaro import client as keitaro
from adrobot.keitaro.bodies import (
    campaign_body,
    google_stream_body,
    make_alias,
    missing_from_tracker,
    offer_stream_body,
)
from adrobot.keitaro.client import JsonObject, KeitaroError
from adrobot.keitaro.placement import confirm_placement


class UnknownOffer(ValueError):
    """The offer is not one the tracker returned."""


class CreateIncomplete(RuntimeError):
    """Keitaro already has the campaign, but the rest did not finish."""

    def __init__(self, keitaro_campaign_id: int, local_id: int | None = None) -> None:
        super().__init__(
            f"campaign {keitaro_campaign_id} exists in Keitaro, creation did not finish"
        )
        self.keitaro_campaign_id = keitaro_campaign_id
        self.local_id = local_id


@dataclass(frozen=True, slots=True)
class CreateRequest:
    name: str
    country: str
    offer_id: int
    domain_id: int | None = None
    group_id: int | None = None
    source_id: int | None = None


def _int(body: JsonObject, key: str) -> int:
    value = body.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise KeitaroError(f"Keitaro answer has no integer {key!r}")
    return value


async def create_in_keitaro(
    client: httpx.AsyncClient, engine: Engine, request: CreateRequest
) -> int:
    """Create the campaign and its two streams, recording each the moment Keitaro accepts it.

    A stop halfway leaves what Keitaro accepted stored here, so the operator can open it,
    Fetch, and see the real state. Returns the local id.
    """
    offer_id = request.offer_id
    missing = missing_from_tracker(await keitaro.get_vocabulary(client))
    if missing:
        raise KeitaroError("tracker does not list: " + ", ".join(missing))
    known = {offer.id for offer in await keitaro.list_offers(client)}
    if offer_id not in known:
        raise UnknownOffer(str(offer_id))
    placement = await confirm_placement(
        client, request.domain_id, request.group_id, request.source_id
    )
    country = request.country.upper()

    body = campaign_body(
        request.name,
        make_alias(request.name),
        placement.domain_id,
        placement.group_id,
        placement.source_id,
    )
    campaign = await keitaro.create_campaign(client, body)
    campaign_id = _int(campaign, "id")
    local_id: int | None = None
    try:
        local_id = await asyncio.to_thread(save_campaign, engine, campaign_id, request.name)
        first = await keitaro.create_stream(client, google_stream_body(campaign_id, country))
        await asyncio.to_thread(
            add_created_stream,
            engine,
            local_id,
            NewStream(_int(first, "id"), dict[str, object](first), ()),
        )
        second = await keitaro.create_stream(client, offer_stream_body(campaign_id, offer_id))
        await asyncio.to_thread(
            add_created_stream,
            engine,
            local_id,
            NewStream(_int(second, "id"), dict[str, object](second), ((offer_id, 100),)),
        )
    except (KeitaroError, SQLAlchemyError) as err:
        raise CreateIncomplete(campaign_id, local_id) from err
    return local_id
