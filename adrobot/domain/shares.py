from collections.abc import Iterable
from typing import Protocol

REMOVED = "removed"


class ShareError(ValueError):
    """The pinned shares cannot produce a valid split."""


class ShareRow(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def share(self) -> int: ...
    @property
    def pinned(self) -> bool: ...
    @property
    def status(self) -> str: ...


def split_shares(offers: Iterable[ShareRow]) -> dict[int, int]:
    """Return Keitaro offer id -> share. Every input id is present; the input is not mutated."""
    rows = tuple(offers)
    result = {row.id: 0 for row in rows}
    active = [row for row in rows if row.status != REMOVED]
    if not active:
        return result

    pinned = [row for row in active if row.pinned]
    free = sorted((row for row in active if not row.pinned), key=lambda row: row.id)
    pinned_sum = sum(row.share for row in pinned)

    if pinned_sum > 100:
        raise ShareError(f"pinned shares sum to {pinned_sum}, above 100")
    if not free and pinned_sum != 100:
        raise ShareError(f"pinned shares sum to {pinned_sum}, no unpinned offer takes the rest")

    for row in pinned:
        result[row.id] = row.share

    if free:
        quotient, extra = divmod(100 - pinned_sum, len(free))
        for index, row in enumerate(free):
            result[row.id] = quotient + (1 if index < extra else 0)
    return result
