import asyncio
from dataclasses import dataclass

import httpx

from adrobot.keitaro.client import (
    CatalogItem,
    KeitaroError,
    list_domains,
    list_groups,
    list_sources,
)


class PlacementError(KeitaroError):
    """A chosen domain, group, or source is not in the tracker."""


@dataclass(frozen=True, slots=True)
class Placement:
    """Tracker ids a new campaign is attached to. None means the field is not sent."""

    domain_id: int | None = None
    group_id: int | None = None
    source_id: int | None = None


def _require(rows: list[CatalogItem], wanted: int, kind: str) -> None:
    if not any(row.id == wanted for row in rows):
        raise PlacementError(f"{kind} {wanted} is not in the tracker")


async def confirm_placement(
    client: httpx.AsyncClient,
    domain_id: int | None,
    group_id: int | None,
    source_id: int | None,
) -> Placement:
    """Read only the lists for chosen ids. An empty choice is left unset."""
    if domain_id is None and group_id is None and source_id is None:
        return Placement()
    try:
        async with asyncio.timeout(15), asyncio.TaskGroup() as tasks:
            domains = tasks.create_task(list_domains(client)) if domain_id is not None else None
            groups = tasks.create_task(list_groups(client)) if group_id is not None else None
            sources = tasks.create_task(list_sources(client)) if source_id is not None else None
    except* KeitaroError as errors:
        raise errors.exceptions[0] from errors
    if domains is not None and domain_id is not None:
        _require(domains.result(), domain_id, "domain")
    if groups is not None and group_id is not None:
        _require(groups.result(), group_id, "group")
    if sources is not None and source_id is not None:
        _require(sources.result(), source_id, "source")
    return Placement(domain_id, group_id, source_id)
