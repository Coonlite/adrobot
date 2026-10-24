import pytest

from adrobot.keitaro.client import KeitaroError
from adrobot.keitaro.streams import parse_streams


def test_parse_streams_reads_offer_id_and_share_and_keeps_the_document():
    payload = [
        {
            "id": 7,
            "name": "Flow 2",
            "position": 2,
            "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
            "offers": [{"id": 3, "stream_id": 7, "offer_id": 11, "state": "active", "share": 40}],
        }
    ]
    parsed = parse_streams(payload)
    keitaro_id, document, offers = parsed[0]
    assert keitaro_id == 7
    assert offers == ((11, 40),)
    assert document["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU"]}]
    assert document["name"] == "Flow 2"


def test_parse_streams_rejects_a_stream_without_an_offers_list():
    with pytest.raises(KeitaroError):
        parse_streams([{"id": 1, "name": "Flow 1"}])


def test_parse_streams_rejects_a_repeated_offer():
    payload = [{"id": 1, "offers": [{"offer_id": 5, "share": 50}, {"offer_id": 5, "share": 50}]}]
    with pytest.raises(KeitaroError):
        parse_streams(payload)
