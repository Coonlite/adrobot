import copy
import re
import secrets
from collections.abc import Iterable, Mapping
from typing import cast

from adrobot.domain.shares import REMOVED, ShareRow, split_shares
from adrobot.keitaro.client import JsonObject, Vocabulary

GOOGLE_URL = "https://google.com"


STREAM_TYPE = "regular"
SCHEMA_REDIRECT = "redirect"
SCHEMA_OFFERS = "landings"
ACTION = "http"
COUNTRY_FILTER = "country"
FILTER_MODE = "accept"


def missing_from_tracker(vocabulary: Vocabulary) -> list[str]:
    """Names this app sends that the tracker's own dictionaries do not list."""
    missing: list[str] = []
    if STREAM_TYPE not in vocabulary.stream_types:
        missing.append(f"stream type {STREAM_TYPE!r}")
    for schema in (SCHEMA_REDIRECT, SCHEMA_OFFERS):
        if schema not in vocabulary.schemas:
            missing.append(f"schema {schema!r}")
    if ACTION not in vocabulary.actions:
        missing.append(f"action {ACTION!r}")
    if FILTER_MODE not in vocabulary.filter_modes.get(COUNTRY_FILTER, frozenset()):
        missing.append(f"filter {COUNTRY_FILTER!r} with mode {FILTER_MODE!r}")
    return missing


def make_alias(name: str, suffix: str | None = None) -> str:
    """Keitaro requires an alias. Same shape as the tracker's own: slug of the name + 6 hex."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "campaign"
    return f"{slug[:40].strip('-')}-{suffix or secrets.token_hex(3)}"


def campaign_body(
    name: str,
    alias: str,
    domain_id: int | None = None,
    group_id: int | None = None,
    source_id: int | None = None,
) -> JsonObject:
    """Campaign with position rotation. Domain, group and source are sent only when set."""
    body: JsonObject = {"name": name, "alias": alias, "type": "position", "state": "active"}
    if domain_id is not None:
        body["domain_id"] = domain_id
    if group_id is not None:
        body["group_id"] = group_id
    if source_id is not None:
        body["traffic_source_id"] = source_id
    return body


def _stream_base(campaign_id: int, name: str, position: int) -> JsonObject:
    return {
        "campaign_id": campaign_id,
        "type": STREAM_TYPE,
        "name": name,
        "position": position,
        "state": "active",
        "action_type": ACTION,
        "collect_clicks": True,
        "filter_or": False,
        "weight": 100,
        "offer_selection": "before_click",
    }


def google_stream_body(campaign_id: int, country: str) -> JsonObject:
    """Stream 1: everyone from the country goes to Google."""
    body = _stream_base(campaign_id, "Flow 1", 1)
    body["schema"] = SCHEMA_REDIRECT
    body["action_payload"] = GOOGLE_URL
    body["filters"] = [{"name": COUNTRY_FILTER, "mode": FILTER_MODE, "payload": [country]}]
    return body


def offer_stream_body(campaign_id: int, offer_id: int) -> JsonObject:
    """Stream 2: everyone else goes to the chosen offer."""
    body = _stream_base(campaign_id, "Flow 2", 2)
    body["schema"] = SCHEMA_OFFERS
    body["action_payload"] = ""
    body["filters"] = []
    body["offers"] = [{"offer_id": offer_id, "share": 100, "state": "active"}]
    return body


class PushError(ValueError):
    """The stored stream has no offer collection to replace."""


def put_body(document: Mapping[str, object], offers: Iterable[ShareRow]) -> dict[str, object]:
    """Deep-copy the stored stream and replace only its offer collection.

    Removed offers are left out. A new offer uses offer_id, share, and state when the
    stored items already have state. Every other document key stays.
    """
    body = copy.deepcopy(dict(document))
    raw = body.get("offers")
    if not isinstance(raw, list):
        raise PushError("stored stream has no offers list")
    stored: list[dict[str, object]] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        stored.append(_offer_item(cast(dict[object, object], item)))
    shares = split_shares(offers)
    active = {row.id for row in offers if row.status != REMOVED}
    template = stored[0] if stored else None
    seen: set[int] = set()
    rebuilt: list[dict[str, object]] = []
    for item in stored:
        offer_id = _stored_offer_id(item)
        if offer_id is None or offer_id not in active:
            continue
        updated = copy.deepcopy(item)
        updated["share"] = shares[offer_id]
        rebuilt.append(updated)
        seen.add(offer_id)
    for offer_id in sorted(active - seen):
        rebuilt.append(_fresh_offer(template, offer_id, shares[offer_id]))
    body["offers"] = rebuilt
    return body


def _offer_item(item: dict[object, object]) -> dict[str, object]:
    return {str(key): value for key, value in item.items()}


def _stored_offer_id(item: dict[str, object]) -> int | None:
    value = item.get("offer_id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _fresh_offer(
    template: dict[str, object] | None, offer_id: int, share: int
) -> dict[str, object]:
    item: dict[str, object] = {"offer_id": offer_id, "share": share}
    if template is not None and "state" in template:
        state = template["state"]
        item["state"] = state if isinstance(state, str) else "active"
    return item
