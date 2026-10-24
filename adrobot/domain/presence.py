"""Where a local campaign sits in Keitaro: live, archived, or gone."""

from collections.abc import Iterable, Mapping
from typing import cast

ACTIVE = "active"
DISABLED = "disabled"
ARCHIVED = "archived"
DELETED = "deleted"

PLACE_NOTE: dict[str, str] = {
    ARCHIVED: "In the Keitaro archive.",
    DELETED: "Not in Keitaro and not in the archive.",
    DISABLED: "Disabled in Keitaro.",
}

_LIVE = frozenset({ACTIVE, DISABLED, "deleted"})


def _id(row: Mapping[str, object]) -> int | None:
    value = row.get("id")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _row(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def index_states(live: Iterable[object], deleted: Iterable[object]) -> dict[int, str]:
    """Merge GET /campaigns and GET /campaigns/deleted. The archive list wins."""
    states: dict[int, str] = {}
    for value in live:
        row = _row(value)
        if row is None:
            continue
        row_id = _id(row)
        if row_id is None:
            continue
        state = row.get("state")
        states[row_id] = state if isinstance(state, str) and state in _LIVE else ACTIVE
    for value in deleted:
        row = _row(value)
        if row is None:
            continue
        row_id = _id(row)
        if row_id is None:
            continue
        states[row_id] = "deleted"
    return states


def campaign_place(states: Mapping[int, str], keitaro_id: int) -> str:
    """An id in neither list is deleted. state deleted is the archive."""
    state = states.get(keitaro_id)
    if state == "deleted":
        return ARCHIVED
    if state == DISABLED:
        return DISABLED
    if state == ACTIVE:
        return ACTIVE
    return DELETED
