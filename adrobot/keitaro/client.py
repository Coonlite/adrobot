import asyncio
import logging
from dataclasses import dataclass

import httpx

from adrobot.config import Settings

logger = logging.getLogger(__name__)

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None
type JsonObject = dict[str, Json]


class KeitaroError(RuntimeError):
    """Keitaro answered with an error or an unexpected body."""


class KeitaroUnreachable(KeitaroError):
    """No answer arrived. A write may or may not have been applied."""


@dataclass(frozen=True, slots=True)
class OfferInfo:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """One domain, campaign group, or traffic source from a tracker list."""

    id: int
    name: str


def build_client(settings: Settings) -> httpx.AsyncClient:
    """One client per process. The API key only travels in this header."""
    return httpx.AsyncClient(
        base_url=settings.keitaro_base_url,
        headers={"Api-Key": settings.keitaro_api_key},
        timeout=httpx.Timeout(10.0),
    )


async def request_json(
    client: httpx.AsyncClient, method: str, path: str, body: JsonObject | None = None
) -> Json:
    try:
        response = await client.request(method, path, json=body)
    except httpx.HTTPError as err:
        raise KeitaroUnreachable(f"{method} {path} failed: {type(err).__name__}") from err
    if response.is_error:
        raise KeitaroError(f"{method} {path} returned {response.status_code}")
    try:
        return response.json()
    except ValueError as err:
        raise KeitaroError(f"{method} {path} returned a non-JSON body") from err


def as_object(value: Json, what: str) -> JsonObject:
    if not isinstance(value, dict):
        raise KeitaroError(f"{what} is not an object")
    return value


def as_list(value: Json, what: str) -> list[Json]:
    if not isinstance(value, list):
        raise KeitaroError(f"{what} is not a list")
    return value


async def campaign_lists(client: httpx.AsyncClient) -> tuple[list[Json], list[Json]]:
    """Live campaigns and the archive. Both lists are required to tell them apart."""
    try:
        async with asyncio.timeout(15), asyncio.TaskGroup() as tasks:
            live = tasks.create_task(request_json(client, "GET", "/campaigns"))
            deleted = tasks.create_task(request_json(client, "GET", "/campaigns/deleted"))
    except TimeoutError as err:
        raise KeitaroError("GET /campaigns timed out") from err
    return as_list(live.result(), "campaigns"), as_list(deleted.result(), "deleted campaigns")


async def get_streams(client: httpx.AsyncClient, campaign_id: int) -> list[JsonObject]:
    raw = as_list(await request_json(client, "GET", f"/campaigns/{campaign_id}/streams"), "streams")
    return [as_object(item, "stream") for item in raw]


async def clicks_today(client: httpx.AsyncClient, campaign_id: int) -> dict[int, int]:
    """Today's click count per stream, from POST /report/build.

    A stream that the report does not list had no clicks today.
    """
    payload = as_object(
        await request_json(
            client,
            "POST",
            "/report/build",
            {
                "range": {"interval": "today"},
                "dimensions": ["stream_id"],
                "measures": ["clicks"],
                "filters": [
                    {"name": "campaign_id", "operator": "EQUALS", "expression": campaign_id}
                ],
            },
        ),
        "report",
    )
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise KeitaroError("report rows is not a list")
    counts: dict[int, int] = {}
    for row in rows:
        item = as_object(row, "report row")
        stream_id = item.get("stream_id")
        clicks = item.get("clicks")
        if isinstance(stream_id, bool) or not isinstance(stream_id, int):
            raise KeitaroError("stream_id is not an integer")
        if isinstance(clicks, bool) or not isinstance(clicks, int):
            raise KeitaroError("clicks is not an integer")
        counts[stream_id] = clicks
    return counts


async def get_offer(client: httpx.AsyncClient, offer_id: int) -> OfferInfo:
    """Read one offer. The id in the body must be the id that was asked for."""
    row = as_object(await request_json(client, "GET", f"/offers/{offer_id}"), "offer")
    found, name = row.get("id"), row.get("name")
    if (
        isinstance(found, bool)
        or not isinstance(found, int)
        or found != offer_id
        or not isinstance(name, str)
    ):
        raise KeitaroError("offer id does not match")
    return OfferInfo(id=found, name=name)


def _catalog(raw: Json, what: str, *, campaigns_only: bool = False) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    for item in as_list(raw, what):
        row = as_object(item, what)
        if row.get("state") == "deleted":
            continue
        if campaigns_only and row.get("type") not in (None, "campaigns"):
            continue
        row_id, name = row.get("id"), row.get("name")
        if isinstance(row_id, bool) or not isinstance(row_id, int) or not isinstance(name, str):
            continue
        if name.strip():
            items.append(CatalogItem(id=row_id, name=name))
    return sorted(items, key=lambda row: row.name.casefold())


async def list_domains(client: httpx.AsyncClient) -> list[CatalogItem]:
    return _catalog(await request_json(client, "GET", "/domains"), "domains")


async def list_groups(client: httpx.AsyncClient) -> list[CatalogItem]:
    """Campaign groups. GET /groups requires type, and campaigns is the campaign list."""
    raw = await request_json(client, "GET", "/groups?type=campaigns")
    return _catalog(raw, "groups", campaigns_only=True)


async def list_sources(client: httpx.AsyncClient) -> list[CatalogItem]:
    return _catalog(await request_json(client, "GET", "/traffic_sources"), "sources")


async def list_offers(client: httpx.AsyncClient) -> list[OfferInfo]:
    raw = as_list(await request_json(client, "GET", "/offers"), "offers")
    offers: list[OfferInfo] = []
    for item in raw:
        row = as_object(item, "offer")
        offer_id, name = row.get("id"), row.get("name")
        if isinstance(offer_id, int) and isinstance(name, str):
            offers.append(OfferInfo(id=offer_id, name=name))
    return offers


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What this tracker accepts when a stream is created."""

    stream_types: frozenset[str]
    schemas: frozenset[str]
    actions: frozenset[str]
    filter_modes: dict[str, frozenset[str]]


def _values(raw: Json, key: str, what: str) -> list[JsonObject]:
    return [as_object(item, what) for item in as_list(raw, what) if key in as_object(item, what)]


async def get_vocabulary(client: httpx.AsyncClient) -> Vocabulary:
    async with asyncio.timeout(15), asyncio.TaskGroup() as group:
        types = group.create_task(request_json(client, "GET", "/stream_types"))
        schemas = group.create_task(request_json(client, "GET", "/stream_schemas"))
        actions = group.create_task(request_json(client, "GET", "/streams_actions"))
        filters = group.create_task(request_json(client, "GET", "/stream_filters"))

    filter_modes: dict[str, frozenset[str]] = {}
    for row in _values(filters.result(), "value", "stream filter"):
        name, modes = row.get("value"), row.get("modes")
        if isinstance(name, str):
            filter_modes[name] = frozenset(modes) if isinstance(modes, dict) else frozenset()
    return Vocabulary(
        stream_types=frozenset(
            str(r["value"]) for r in _values(types.result(), "value", "stream type")
        ),
        schemas=frozenset(str(r["value"]) for r in _values(schemas.result(), "value", "schema")),
        actions=frozenset(str(r["key"]) for r in _values(actions.result(), "key", "action")),
        filter_modes=filter_modes,
    )


async def create_campaign(client: httpx.AsyncClient, body: JsonObject) -> JsonObject:
    return as_object(await request_json(client, "POST", "/campaigns", body), "created campaign")


async def create_stream(client: httpx.AsyncClient, body: JsonObject) -> JsonObject:
    return as_object(await request_json(client, "POST", "/streams", body), "created stream")


async def put_stream(client: httpx.AsyncClient, stream_id: int, body: JsonObject) -> JsonObject:
    return as_object(await request_json(client, "PUT", f"/streams/{stream_id}", body), "stream")
