import httpx
import pytest

from adrobot.keitaro.placement import Placement, PlacementError, confirm_placement


def client_for(
    lists: dict[str, list[dict[str, object]]],
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=lists.get(request.url.path.rsplit("/", 1)[-1], []))

    return httpx.AsyncClient(
        base_url="https://t.test/admin_api/v1", transport=httpx.MockTransport(handler)
    ), seen


async def test_an_empty_choice_reads_nothing():
    client, seen = client_for({})
    assert await confirm_placement(client, None, None, None) == Placement()
    assert seen == []


async def test_chosen_ids_are_kept_and_groups_are_campaign_groups():
    client, seen = client_for(
        {
            "domains": [{"id": 4622, "name": "in.posdk.xyz"}],
            "groups": [{"id": 7, "name": "A", "type": "campaigns"}],
            "traffic_sources": [{"id": 8, "name": "S"}],
        }
    )
    assert await confirm_placement(client, 4622, 7, 8) == Placement(4622, 7, 8)
    groups = next(request for request in seen if request.url.path.endswith("groups"))
    assert groups.url.params["type"] == "campaigns"


async def test_missing_source_is_an_error():
    client, _ = client_for(
        {
            "domains": [{"id": 1, "name": "d"}],
            "groups": [{"id": 2, "name": "g"}],
            "traffic_sources": [],
        }
    )
    with pytest.raises(PlacementError, match="source 8 is not in the tracker"):
        await confirm_placement(client, 1, 2, 8)


async def test_deleted_domain_is_rejected():
    client, _ = client_for(
        {
            "domains": [{"id": 1, "name": "d.test", "state": "deleted"}],
            "groups": [{"id": 2, "name": "g"}],
            "traffic_sources": [{"id": 3, "name": "s"}],
        }
    )
    with pytest.raises(PlacementError, match="domain 1 is not in the tracker"):
        await confirm_placement(client, 1, 2, 3)


async def test_a_non_campaign_group_is_rejected():
    client, _ = client_for(
        {
            "domains": [{"id": 1, "name": "d"}],
            "groups": [{"id": 2, "name": "Offers", "type": "offers"}],
            "traffic_sources": [{"id": 3, "name": "s"}],
        }
    )
    with pytest.raises(PlacementError, match="group 2 is not in the tracker"):
        await confirm_placement(client, 1, 2, 3)
