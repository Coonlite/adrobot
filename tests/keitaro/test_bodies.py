import pytest

from adrobot.db.views import OfferView
from adrobot.keitaro.bodies import PushError, campaign_body, make_alias, put_body


def test_alias_is_a_slug_with_a_suffix():
    assert make_alias("Test Campaign #1!", "abc123") == "test-campaign-1-abc123"
    assert make_alias("Привет", "abc123") == "campaign-abc123"


def test_campaign_body_sends_only_the_ids_that_are_set():
    bare = campaign_body("x", "x-1")
    assert not {"domain_id", "group_id", "traffic_source_id"} & bare.keys()

    full = campaign_body("x", "x-1", domain_id=1, group_id=2, source_id=3)
    assert (full["domain_id"], full["group_id"], full["traffic_source_id"]) == (1, 2, 3)


def _view(offer_id: int, share: int, status: str) -> OfferView:
    return OfferView(offer_id, share, False, status, False, share)


def test_put_body_keeps_every_other_key_and_drops_removed_offers():
    document: dict[str, object] = {
        "id": 7,
        "name": "Flow 2",
        "filters": [{"name": "country"}],
        "offers": [
            {"offer_id": 1, "share": 100, "state": "active", "id": 9},
            {"offer_id": 2, "share": 0, "state": "active"},
        ],
    }
    body = put_body(
        document,
        (_view(1, 100, "synced"), _view(2, 0, "removed"), _view(3, 0, "pending")),
    )
    assert body["name"] == "Flow 2"
    assert body["filters"] == [{"name": "country"}]
    assert document["offers"] == [
        {"offer_id": 1, "share": 100, "state": "active", "id": 9},
        {"offer_id": 2, "share": 0, "state": "active"},
    ]
    offers = body["offers"]
    assert isinstance(offers, list)
    assert offers == [
        {"offer_id": 1, "share": 50, "state": "active", "id": 9},
        {"offer_id": 3, "share": 50, "state": "active"},
    ]


def test_put_body_refuses_a_document_without_an_offer_collection():
    with pytest.raises(PushError):
        put_body({"id": 1, "name": "Flow 1"}, ())
