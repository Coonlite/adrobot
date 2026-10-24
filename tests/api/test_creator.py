import json
import re

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from adrobot.api.creator import CreateIncomplete, CreateRequest, UnknownOffer, create_in_keitaro
from adrobot.db.editor import load_editor
from adrobot.db.models import Campaign, OfferBinding, Stream
from adrobot.keitaro.client import KeitaroError
from adrobot.keitaro.placement import PlacementError
from tests.fakes import DICTIONARIES, Lists, tracker

REQUEST = CreateRequest(
    name="spring", country="au", offer_id=18813, domain_id=4622, group_id=7, source_id=8
)


def count(engine: Engine, model: type[Campaign] | type[Stream] | type[OfferBinding]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


async def test_create_writes_campaign_streams_and_binding(engine: Engine):
    client, seen = tracker()
    local_id = await create_in_keitaro(client, engine, REQUEST)

    view = load_editor(engine, local_id)
    assert view is not None
    assert (view.keitaro_id, view.name) == (500, "spring")
    offers = {s.keitaro_id: tuple(o.keitaro_offer_id for o in s.offers) for s in view.streams}
    assert offers == {901: (), 902: (18813,)}

    bodies = [json.loads(r.content) for r in seen if r.method == "POST"]
    assert (bodies[0]["domain_id"], bodies[0]["group_id"], bodies[0]["traffic_source_id"]) == (
        4622,
        7,
        8,
    )
    assert re.fullmatch(r"spring-[0-9a-f]{6}", bodies[0]["alias"])
    assert bodies[1]["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert bodies[1]["action_payload"] == "https://google.com"
    assert bodies[2]["offers"] == [{"offer_id": 18813, "share": 100, "state": "active"}]


async def test_failed_second_stream_keeps_what_keitaro_accepted(engine: Engine):
    client, _ = tracker(fail_stream=2)
    with pytest.raises(CreateIncomplete) as raised:
        await create_in_keitaro(client, engine, REQUEST)

    assert raised.value.keitaro_campaign_id == 500
    assert raised.value.local_id is not None
    view = load_editor(engine, raised.value.local_id)
    assert view is not None and view.keitaro_id == 500
    assert [s.keitaro_id for s in view.streams] == [901]
    assert count(engine, OfferBinding) == 0


async def test_unknown_offer_creates_nothing(engine: Engine):
    client, seen = tracker()
    with pytest.raises(UnknownOffer):
        await create_in_keitaro(client, engine, CreateRequest("x", "AU", 999, 4622, 7, 8))

    assert {r.method for r in seen} == {"GET"}
    assert count(engine, Campaign) == 0


async def test_tracker_without_a_needed_value_creates_nothing(engine: Engine):
    no_landings: Lists = {**DICTIONARIES, "/stream_schemas": [{"value": "redirect"}]}
    client, seen = tracker(dictionaries=no_landings)
    with pytest.raises(KeitaroError, match="landings"):
        await create_in_keitaro(client, engine, REQUEST)

    assert all(r.method == "GET" for r in seen)
    assert count(engine, Campaign) == 0


async def test_unset_placement_is_left_out_of_the_body(engine: Engine):
    client, seen = tracker()
    await create_in_keitaro(client, engine, CreateRequest("spring", "au", 18813))

    campaign = next(json.loads(request.content) for request in seen if request.method == "POST")
    assert not {"domain_id", "group_id", "traffic_source_id"} & campaign.keys()


async def test_missing_group_creates_nothing(engine: Engine):
    client, seen = tracker(dictionaries={**DICTIONARIES, "/groups": []})
    with pytest.raises(PlacementError, match="group 7 is not in the tracker"):
        await create_in_keitaro(client, engine, REQUEST)

    assert all(r.method == "GET" for r in seen)
    assert count(engine, Campaign) == 0
