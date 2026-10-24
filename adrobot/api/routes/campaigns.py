import asyncio
import logging
import re
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field, field_validator

from adrobot.api.creator import CreateIncomplete, CreateRequest, UnknownOffer, create_in_keitaro
from adrobot.api.deps import EngineDep, KeitaroDep
from adrobot.api.templates import failure, page
from adrobot.keitaro import client as keitaro
from adrobot.keitaro.client import KeitaroError
from adrobot.keitaro.placement import PlacementError

logger = logging.getLogger(__name__)

router = APIRouter()

OFFER_NOT_FOUND = "That offer is not in the tracker's offer list."


class CampaignForm(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(pattern=r"^[A-Za-z]{2}$")
    offer: str = Field(min_length=1)
    domain_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    source_id: int | None = Field(default=None, gt=0)

    @field_validator("domain_id", "group_id", "source_id", mode="before")
    @classmethod
    def empty_choice(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return value


def parse_offer_id(text: str) -> int | None:
    """The form field reads '<id> · <name>'. Only the leading id is used."""
    match = re.match(r"\s*(\d+)", text)
    return int(match.group(1)) if match else None


def _empty_form(notice: str) -> dict[str, object]:
    return {"offers": [], "domains": [], "groups": [], "sources": [], "notice": notice}


@router.get("/", response_class=HTMLResponse)
async def new_campaign_form(request: Request, client: KeitaroDep):
    catalog: dict[str, list[object]] | None = None
    try:
        async with asyncio.timeout(15), asyncio.TaskGroup() as tasks:
            offers = tasks.create_task(keitaro.list_offers(client))
            domains = tasks.create_task(keitaro.list_domains(client))
            groups = tasks.create_task(keitaro.list_groups(client))
            sources = tasks.create_task(keitaro.list_sources(client))
        catalog = {
            "offers": list(offers.result()),
            "domains": list(domains.result()),
            "groups": list(groups.result()),
            "sources": list(sources.result()),
        }
    except* KeitaroError:
        logger.warning("Create form lists could not be read from Keitaro")
    if catalog is None:
        return page(
            request, "new.html", _empty_form("Keitaro did not return the lists for a new campaign.")
        )
    missing = [name for name, rows in catalog.items() if not rows]
    notice = f"Keitaro returned no {', '.join(missing)}." if missing else None
    return page(request, "new.html", {**catalog, "notice": notice})


@router.post("/campaigns")
async def create_campaign(
    request: Request,
    form: Annotated[CampaignForm, Form()],
    client: KeitaroDep,
    engine: EngineDep,
):
    offer_id = parse_offer_id(form.offer)
    if offer_id is None:
        return failure(request, OFFER_NOT_FOUND)
    try:
        local_id = await create_in_keitaro(
            client,
            engine,
            CreateRequest(
                form.name, form.country, offer_id, form.domain_id, form.group_id, form.source_id
            ),
        )
    except UnknownOffer:
        return failure(request, OFFER_NOT_FOUND)
    except PlacementError as err:
        logger.warning("Campaign placement is not usable: %s", err)
        return failure(request, f"Nothing was created. {err}.")
    except CreateIncomplete as err:
        logger.warning("Creation stopped after campaign %s", err.keitaro_campaign_id)
        saved = err.local_id is not None
        message = (
            f"Keitaro created campaign {err.keitaro_campaign_id}, but creation did not finish. "
            + (
                "It is saved here with what Keitaro accepted. Open it and Fetch to see its streams."
                if saved
                else "Check it in the tracker before creating another one."
            )
        )
        return failure(request, message, back=f"/campaigns/{err.local_id}" if saved else None)
    except KeitaroError as err:
        logger.warning("Keitaro rejected the create request: %s", err)
        return failure(request, "Keitaro request failed. Nothing was created.")
    return RedirectResponse(url=f"/campaigns/{local_id}", status_code=303)
