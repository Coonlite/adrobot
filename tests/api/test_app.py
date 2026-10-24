from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from adrobot.api import app as app_module
from adrobot.api.app import app
from adrobot.api.routes.campaigns import parse_offer_id
from adrobot.config import Settings
from tests.fakes import SETTINGS, tracker


def test_parse_offer_id_reads_the_leading_number():
    assert parse_offer_id("18813 · Flexacil [JOINT-PE]") == 18813
    assert parse_offer_id("  42") == 42
    assert parse_offer_id("another test offer 2") is None
    assert parse_offer_id("") is None


@pytest.fixture
def web(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    client, _ = tracker()

    def fake_settings() -> Settings:
        return SETTINGS

    def fake_client(_settings: Settings) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(app_module, "load_settings", fake_settings)
    monkeypatch.setattr(app_module, "make_engine", lambda: engine)
    monkeypatch.setattr(app_module, "build_client", fake_client)
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


class _Reply(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Mapping[str, str]: ...


def _notice(web: TestClient, response: _Reply) -> str:
    assert response.status_code == 303
    page = web.get(response.headers["location"])
    assert page.status_code == 200
    assert 'class="toast"' in page.text
    assert "Campaign not created" not in page.text
    again = web.get(response.headers["location"])
    assert "Check the form" not in again.text
    assert "Nothing was changed" not in again.text
    return page.text


def test_invalid_form_stays_on_the_form(web: TestClient):
    response = web.post("/campaigns", data={"name": "", "country": "AUS", "offer": ""})
    page = _notice(web, response)
    assert response.headers["location"] == "/"
    assert "Check the form" in page
    assert "New campaign" in page
    assert "Choose a domain" in page
    assert '<option value="4622">in.posdk.xyz</option>' in page
    assert '<option value="7">FORTESTS</option>' in page
    assert '<option value="8">FORTESTS</option>' in page


def test_offer_without_an_id_is_refused(web: TestClient):
    response = web.post("/campaigns", data={"name": "x", "country": "AU", "offer": "no number"})
    page = _notice(web, response)
    assert response.headers["location"] == "/"
    assert "offer list" in page


def test_campaign_list_without_a_campaign_points_at_create(web: TestClient):
    response = web.get("/campaigns")
    assert response.status_code == 200
    assert "Create a campaign" in response.text
    assert "secret" not in response.text
    assert "/static/app.css" in response.text
    old = web.get("/editor")
    assert old.status_code == 307
    assert old.headers["location"] == "/campaigns"


def test_fetch_replaces_shares_from_the_tracker(web: TestClient):
    created = web.post(
        "/campaigns",
        data={
            "name": "spring",
            "country": "au",
            "offer": "18813 · Some offer",
            "domain_id": "4622",
            "group_id": "7",
            "source_id": "8",
        },
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/campaigns/1"
    before = web.get("/campaigns/1")
    assert 'href="/campaigns"' in before.text
    assert before.status_code == 200
    assert 'text-btn">100%' in before.text
    fetched = web.post("/campaigns/1/fetch")
    assert fetched.status_code == 303
    assert fetched.headers["location"] == "/campaigns/1"
    old_editor = web.get("/campaigns/1/editor")
    assert old_editor.status_code == 307
    assert old_editor.headers["location"] == "/campaigns/1"
    page = web.get(fetched.headers["location"])
    assert page.status_code == 200
    assert "Some offer" in page.text
    assert 'text-btn">40%' in page.text
    assert 'text-btn">100%' not in page.text
    flow_2 = page.text.find("Stream: Flow 2")
    flow_1 = page.text.find("Stream: Flow 1")
    assert flow_2 < page.text.find("filter by None") < flow_1
    assert flow_1 < page.text.find("filter by country")
    assert flow_2 < page.text.find("clicks today: 4") < flow_1
    assert flow_1 < page.text.find("clicks today: 0")
    assert "Offer for Flow 1" not in page.text
    assert "Offer for Flow 2" in page.text
    assert "Search offers" in page.text
    assert "18813 · Some offer" in page.text
    assert 'href="https://tracker.test/admin/#!/campaigns/500"' in page.text
    assert 'href="https://tracker.test/admin/#!/offers/"' in page.text
    assert "synced" not in page.text
    assert "secret" not in page.text


def test_fetch_error_leaves_the_saved_share(engine: Engine, monkeypatch: pytest.MonkeyPatch):
    client, _ = tracker(fail_get_streams=True)

    def fake_settings() -> Settings:
        return SETTINGS

    def fake_client(_settings: Settings) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(app_module, "load_settings", fake_settings)
    monkeypatch.setattr(app_module, "make_engine", lambda: engine)
    monkeypatch.setattr(app_module, "build_client", fake_client)
    with TestClient(app, follow_redirects=False) as web:
        created = web.post(
            "/campaigns",
            data={
                "name": "spring",
                "country": "au",
                "offer": "18813 · Some offer",
                "domain_id": "4622",
                "group_id": "7",
                "source_id": "8",
            },
        )
        assert created.status_code == 303
        failed = web.post("/campaigns/1/fetch")
        notice = _notice(web, failed)
        assert failed.headers["location"] == "/campaigns/1"
        assert "Nothing was changed" in notice
        assert "Fetch streams from KT" in notice
        page = web.get("/campaigns/1")
        assert 'text-btn">100%' in page.text
        assert 'text-btn">40%' not in page.text
        assert "secret" not in notice


def _add_action(page: str) -> str:
    actions = [part.split('"', 1)[0] for part in page.split('action="')[1:]]
    found = [action for action in actions if action.endswith("/offers")]
    assert found
    return found[0]


def test_add_recalculates_shares_and_push_publishes_them(web: TestClient):
    created = web.post(
        "/campaigns",
        data={
            "name": "spring",
            "country": "au",
            "offer": "18813 · Some offer",
            "domain_id": "4622",
            "group_id": "7",
            "source_id": "8",
        },
    )
    assert created.status_code == 303
    page = web.get("/campaigns/1")
    action = _add_action(page.text)
    assert action.endswith("/offers")
    missing = web.post(action, data={"offer": "999 · Missing"})
    assert "Nothing was changed" in _notice(web, missing)
    assert missing.headers["location"] == "/campaigns/1"
    duplicate = web.post(action, data={"offer": "18813 · Some offer"})
    assert "Nothing was changed" in _notice(web, duplicate)
    assert duplicate.headers["location"] == "/campaigns/1"
    assert 'text-btn">100%' in web.get("/campaigns/1").text
    added = web.post(action, data={"offer": "42 · Other offer"})
    assert added.status_code == 303
    pending = web.get(added.headers["location"])
    assert 'text-btn">50%' in pending.text
    assert 'text-btn">100%' not in pending.text
    assert "is-pending" in pending.text
    assert "Push to KT" in pending.text
    assert "Cancel" in pending.text
    assert "pending" in pending.text
    cancel = action.replace("/offers", "/cancel")
    cancelled = web.post(cancel)
    assert cancelled.status_code == 303
    restored = web.get(cancelled.headers["location"])
    assert 'text-btn">100%' in restored.text
    assert "Push to KT" not in restored.text
    web.post(action, data={"offer": "42 · Other offer"})
    pushed = web.post(action.replace("/offers", "/push"))
    assert pushed.status_code == 303
    published = web.get(pushed.headers["location"])
    assert published.status_code == 200
    assert 'text-btn">50%' in published.text
    assert "Push to KT" not in published.text
    assert "is-pending" not in published.text
    assert "secret" not in published.text


def test_remove_archives_the_row_and_bring_back_reopens_the_stream(web: TestClient):
    created = web.post(
        "/campaigns",
        data={
            "name": "spring",
            "country": "au",
            "offer": "18813 · Some offer",
            "domain_id": "4622",
            "group_id": "7",
            "source_id": "8",
        },
    )
    assert created.status_code == 303
    page = web.get("/campaigns/1")
    action = _add_action(page.text)
    added = web.post(action, data={"offer": "42 · Other offer"})
    assert added.status_code == 303
    removed = web.post(f"{action}/18813/remove")
    assert removed.status_code == 303
    pending = web.get(removed.headers["location"])
    assert "is-removed" in pending.text
    assert 'text-btn">0%' in pending.text
    assert 'text-btn">100%' in pending.text
    assert "Bring back" in pending.text
    assert "is-pending" in pending.text
    assert "Push to KT" in pending.text
    missing = web.post(f"{action}/999/remove")
    assert missing.headers["location"] == "/campaigns/1"
    assert "Nothing was changed" in _notice(web, missing)
    brought = web.post(f"{action}/18813/bring-back")
    assert brought.status_code == 303
    again = web.get(brought.headers["location"])
    assert 'text-btn">50%' in again.text
    assert 'text-btn">0%' not in again.text
    assert "is-removed" not in again.text
    assert "is-pending" in again.text
    assert "Push to KT" in again.text
    assert "Cancel" in again.text
    assert "secret" not in again.text
    cancelled = web.post(action.replace("/offers", "/cancel"))
    assert cancelled.status_code == 303
    restored = web.get(cancelled.headers["location"])
    assert 'text-btn">100%' in restored.text
    assert 'text-btn">50%' not in restored.text
    assert "is-pending" not in restored.text
    assert "Push to KT" not in restored.text
    assert "is-removed" not in restored.text


@contextmanager
def _session(
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    *,
    campaigns: list[dict[str, object]] | None = None,
    deleted_campaigns: list[dict[str, object]] | None = None,
    fail_deleted: bool = False,
) -> Generator[TestClient]:
    client, _ = tracker(
        campaigns=campaigns, deleted_campaigns=deleted_campaigns, fail_deleted=fail_deleted
    )

    def fake_settings() -> Settings:
        return SETTINGS

    def fake_client(_settings: Settings) -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(app_module, "load_settings", fake_settings)
    monkeypatch.setattr(app_module, "make_engine", lambda: engine)
    monkeypatch.setattr(app_module, "build_client", fake_client)
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def _create(web: TestClient) -> None:
    created = web.post(
        "/campaigns",
        data={"name": "spring", "country": "au", "offer": "18813 · Some offer"},
    )
    assert created.status_code == 303


def test_an_archived_campaign_is_labeled_on_the_list_and_in_the_editor(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    with _session(
        engine,
        monkeypatch,
        campaigns=[],
        deleted_campaigns=[{"id": 500, "state": "deleted", "name": "functions"}],
    ) as web:
        _create(web)
        page = web.get("/campaigns")
        assert page.status_code == 200
        assert 'class="status archived"' in page.text
        assert "In the Keitaro archive." in page.text
        assert "Not in Keitaro and not in the archive." not in page.text
        editor = web.get("/campaigns/1")
        assert 'class="status archived"' in editor.text
        assert "In the Keitaro archive." in editor.text


def test_a_campaign_missing_from_both_lists_is_deleted(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    with _session(engine, monkeypatch, campaigns=[], deleted_campaigns=[]) as web:
        _create(web)
        page = web.get("/campaigns")
        assert 'class="status deleted"' in page.text
        assert "Not in Keitaro and not in the archive." in page.text
        assert "In the Keitaro archive." not in page.text
        editor = web.get("/campaigns/1")
        assert 'class="status deleted"' in editor.text
        assert "Not in Keitaro and not in the archive." in editor.text


def test_a_failed_archive_read_does_not_call_the_campaign_deleted(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
):
    with _session(engine, monkeypatch, fail_deleted=True) as web:
        _create(web)
        page = web.get("/campaigns")
        assert "Archive state was not read." in page.text
        assert "status archived" not in page.text
        assert "status deleted" not in page.text
        editor = web.get("/campaigns/1")
        assert "Archive state was not read." in editor.text
        assert "status archived" not in editor.text
        assert "status deleted" not in editor.text


def test_create_redirects_with_303_and_the_page_shows_both_streams(web: TestClient):
    response = web.post(
        "/campaigns",
        data={"name": "spring", "country": "au", "offer": "18813 · Some offer"},
    )
    assert response.status_code == 303
    page = web.get(response.headers["location"])
    assert page.status_code == 200
    assert "Flow 1" in page.text and "Flow 2" in page.text
    assert "secret" not in page.text
