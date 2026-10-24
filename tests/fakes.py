"""A stand-in tracker for tests: an httpx MockTransport that answers like Keitaro."""

import copy
import json
from typing import Any, cast

import httpx

from adrobot.config import Settings

SETTINGS = Settings(
    keitaro_base_url="https://tracker.test/admin_api/v1",
    keitaro_api_key="secret",
)

Lists = dict[str, list[dict[str, object]]]

DICTIONARIES: Lists = {
    "/stream_types": [{"value": "regular"}, {"value": "default"}],
    "/stream_schemas": [{"value": "landings"}, {"value": "redirect"}],
    "/streams_actions": [{"key": "http"}, {"key": "meta"}],
    "/stream_filters": [{"value": "country", "modes": {"accept": "YES", "reject": "NO"}}],
    "/domains": [{"id": 4622, "name": "in.posdk.xyz", "state": "active"}],
    "/groups": [{"id": 7, "name": "FORTESTS", "type": "campaigns"}],
    "/traffic_sources": [{"id": 8, "name": "FORTESTS"}],
    "/campaigns/deleted": [],
    "/campaigns": [{"id": 500, "name": "spring", "state": "active"}],
}


def tracker(
    fail_stream: int | None = None,
    dictionaries: Lists | None = None,
    fail_get_streams: bool = False,
    campaigns: list[dict[str, object]] | None = None,
    deleted_campaigns: list[dict[str, object]] | None = None,
    fail_deleted: bool = False,
) -> tuple[httpx.AsyncClient, list[httpx.Request]]:
    """Returns the client and the list of requests it received."""
    seen: list[httpx.Request] = []
    streams_created = 0
    created: list[dict[str, Any]] = []
    served = dict(DICTIONARIES if dictionaries is None else dictionaries)
    if campaigns is not None:
        served["/campaigns"] = campaigns
    if deleted_campaigns is not None:
        served["/campaigns/deleted"] = deleted_campaigns

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal streams_created
        seen.append(request)
        path = request.url.path
        if fail_deleted and request.method == "GET" and path.endswith("/campaigns/deleted"):
            return httpx.Response(500, json={"error": "boom"})
        for suffix, rows in served.items():
            if request.method == "GET" and path.endswith(suffix):
                return httpx.Response(200, json=rows)
        if request.method == "GET" and "/campaigns/" in path and path.endswith("/streams"):
            if fail_get_streams:
                return httpx.Response(500, json={"error": "boom"})
            rows: list[dict[str, Any]] = []
            for saved in created:
                row = copy.deepcopy(saved)
                offers: Any = row.get("offers")
                if isinstance(offers, list):
                    for offer in cast(list[dict[str, Any]], row["offers"]):
                        offer["share"] = 40
                else:
                    row["offers"] = []
                rows.append(row)
            return httpx.Response(200, json=rows)
        if request.method == "GET" and "/offers/" in path:
            tail = path.rstrip("/").rsplit("/", 1)[-1]
            names = {18813: "Some offer", 42: "Other offer"}
            if tail.isdigit() and int(tail) in names:
                offer_id = int(tail)
                return httpx.Response(200, json={"id": offer_id, "name": names[offer_id]})
            return httpx.Response(404, json={"error": "missing"})
        if request.method == "GET" and path.endswith("/offers"):
            return httpx.Response(
                200,
                json=[{"id": 18813, "name": "Some offer"}, {"id": 42, "name": "Other offer"}],
            )
        if request.method == "PUT" and "/streams/" in path:
            return httpx.Response(200, json=json.loads(request.content))
        if request.method == "POST" and path.endswith("/campaigns"):
            return httpx.Response(200, json={"id": 500, "alias": "spring-abc", "name": "spring"})
        if request.method == "POST" and path.endswith("/report/build"):
            report_rows: list[dict[str, int]] = []
            for saved in created:
                offers: Any = saved.get("offers")
                stream_id = saved.get("id")
                if isinstance(offers, list) and offers and isinstance(stream_id, int):
                    report_rows.append({"stream_id": stream_id, "clicks": 4})
            return httpx.Response(
                200, json={"rows": report_rows, "total": len(report_rows), "meta": {"count": 0}}
            )
        if request.method == "POST" and path.endswith("/streams"):
            streams_created += 1
            if streams_created == fail_stream:
                return httpx.Response(500, json={"error": "boom"})
            body: Any = json.loads(request.content)
            saved: dict[str, Any] = {**body, "id": 900 + streams_created}
            created.append(saved)
            return httpx.Response(200, json=saved)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        base_url=SETTINGS.keitaro_base_url, transport=httpx.MockTransport(handler)
    )
    return client, seen
