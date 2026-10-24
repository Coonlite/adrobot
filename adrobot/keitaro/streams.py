import copy
from collections.abc import Mapping
from typing import cast

from adrobot.keitaro.client import Json, KeitaroError


def _json(value: object, what: str) -> Json:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        mapped: dict[str, Json] = {}
        for key, item in cast(dict[object, object], value).items():
            if not isinstance(key, str):
                raise KeitaroError(f"{what} has a non-text key")
            mapped[key] = _json(item, what)
        return mapped
    if isinstance(value, list):
        return [_json(item, what) for item in cast(list[object], value)]
    raise KeitaroError(f"{what} has a value this app cannot store")


def _object(value: object, what: str) -> dict[str, Json]:
    parsed = _json(value, what)
    if not isinstance(parsed, dict):
        raise KeitaroError(f"{what} is not an object")
    return parsed


def _integer(value: Json, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KeitaroError(f"{what} is not an integer")
    return value


def parse_streams(
    payload: object,
) -> tuple[tuple[int, Mapping[str, object], tuple[tuple[int, int], ...]], ...]:
    """Read GET /campaigns/{id}/streams.

    Each item is the Keitaro stream id, the full stream object, and (offer id, share) pairs.
    """
    if not isinstance(payload, list):
        raise KeitaroError("streams is not a list")
    parsed: list[tuple[int, Mapping[str, object], tuple[tuple[int, int], ...]]] = []
    seen: set[int] = set()
    for item in cast(list[object], payload):
        document = _object(item, "stream")
        keitaro_id = _integer(document.get("id"), "stream id")
        if keitaro_id in seen:
            raise KeitaroError(f"stream {keitaro_id} is repeated")
        seen.add(keitaro_id)
        offers = document.get("offers")
        if not isinstance(offers, list):
            raise KeitaroError(f"stream {keitaro_id} has no offers list")
        rows: list[tuple[int, int]] = []
        offer_ids: set[int] = set()
        for offer in offers:
            fields = _object(offer, f"stream {keitaro_id} offer")
            offer_id = _integer(fields.get("offer_id"), "offer_id")
            if offer_id in offer_ids:
                raise KeitaroError(f"stream {keitaro_id} repeats offer {offer_id}")
            offer_ids.add(offer_id)
            rows.append((offer_id, _integer(fields.get("share"), "share")))
        parsed.append((keitaro_id, copy.deepcopy(document), tuple(rows)))
    return tuple(parsed)
