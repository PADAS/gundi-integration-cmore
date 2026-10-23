"""Tests for the EarthRanger admin client (categories, event types, choices)."""

import json

import httpx
import pytest

from app.datasource.er_admin_client import ErAdminAuthError, ErAdminClient

SITE = "https://site.pamdas.org"


def _transport(handler):
    """A MockTransport that records every request it sees."""
    seen = []

    def _handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(_handle), seen


def _ok(data):
    return httpx.Response(200, json={"data": data})


@pytest.mark.asyncio
async def test_token_auth_sends_bearer_and_unwraps_data():
    transport, seen = _transport(lambda r: _ok({"username": "ingest", "permissions": {}}))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        me = await client.get_me()
    assert me == {"username": "ingest", "permissions": {}}
    assert seen[0].method == "GET" and seen[0].url.path == "/api/v1.0/user/me"
    assert seen[0].headers["Authorization"] == "Bearer t"


@pytest.mark.asyncio
async def test_password_grant_exchanges_then_uses_token():
    def handler(r):
        if r.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "granted"})
        return _ok({"username": "u"})

    transport, seen = _transport(handler)
    async with ErAdminClient(SITE, username="u", password="p", transport=transport) as client:
        await client.get_me()
    assert seen[0].method == "POST" and seen[0].url.path == "/oauth2/token"
    form = dict(httpx.QueryParams(seen[0].content.decode()))
    assert form == {"grant_type": "password", "client_id": "das_web_client", "username": "u", "password": "p"}
    assert seen[1].headers["Authorization"] == "Bearer granted"


@pytest.mark.asyncio
async def test_password_grant_failure_raises_auth_error():
    transport, _ = _transport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    async with ErAdminClient(SITE, username="u", password="bad", transport=transport) as client:
        with pytest.raises(ErAdminAuthError):
            await client.get_me()


@pytest.mark.asyncio
async def test_get_event_types_follows_pagination_with_schema():
    def handler(r):
        assert r.url.params["include_schema"] == "true"
        if r.url.params.get("page") == "2":
            return _ok({"results": [{"value": "c"}], "next": None})
        return _ok({"results": [{"value": "a"}, {"value": "b"}], "next": f"{SITE}/api/v2.0/activity/eventtypes/?include_schema=true&page=2"})

    transport, seen = _transport(handler)
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        types = await client.get_event_types()
    assert [t["value"] for t in types] == ["a", "b", "c"]
    assert seen[0].url.path == "/api/v2.0/activity/eventtypes/"


@pytest.mark.asyncio
async def test_get_choices_returns_empty_on_the_never_seen_400():
    transport, _ = _transport(lambda r: httpx.Response(400, json={"status": {"detail": "f261_x is not one of the available choices"}}))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        assert await client.get_choices("f261_x") == []


@pytest.mark.asyncio
async def test_get_choices_filters_by_model_and_field():
    transport, seen = _transport(lambda r: _ok({"results": [{"id": "1", "value": "a"}], "next": None}))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        choices = await client.get_choices("f261_evidence_type")
    assert choices == [{"id": "1", "value": "a"}]
    assert seen[0].url.path == "/api/v1.0/choices/"
    assert dict(seen[0].url.params) == {"model": "activity.event", "field": "f261_evidence_type"}


@pytest.mark.asyncio
async def test_writes_target_the_right_paths():
    transport, seen = _transport(lambda r: _ok({"id": "x"}))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        await client.post_event_category({"value": "cmore_1"})
        await client.post_event_type({"value": "cmore_1_2_x"})
        await client.patch_event_type("cmore_1_2_x", {"display": "X"})
        await client.post_choice({"value": "a"})
        await client.patch_choice("abc", {"is_active": False})
    assert [(r.method, r.url.path) for r in seen] == [
        ("POST", "/api/v1.0/activity/events/categories/"),
        ("POST", "/api/v2.0/activity/eventtypes/"),
        ("PATCH", "/api/v2.0/activity/eventtypes/cmore_1_2_x/"),
        ("POST", "/api/v1.0/choices/"),
        ("PATCH", "/api/v1.0/choices/abc/"),
    ]
    assert json.loads(seen[2].content) == {"display": "X"}


@pytest.mark.asyncio
async def test_server_error_raises_http_status_error(monkeypatch):
    import app.datasource.er_admin_client as module

    monkeypatch.setattr(module, "_MAX_TRIES", 1)
    transport, _ = _transport(lambda r: httpx.Response(500, text="boom"))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await client.get_event_categories()


@pytest.mark.asyncio
async def test_choice_permission_probes_read_the_status_without_writing():
    """DRF checks model permissions before deserialising, so an empty POST
    answers 400 when the user may add choices and 403 when not; an empty
    PATCH on a random id answers 404 (allowed) or 403."""
    def handler(r):
        if r.method == "POST":
            return httpx.Response(400, json={"status": {"detail": "value is required"}})
        return httpx.Response(403, json={"status": {"detail": "forbidden"}})

    transport, seen = _transport(handler)
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        assert await client.can_add_choices() is True
        assert await client.can_change_choices() is False
    assert [(r.method, r.url.path.rsplit("/", 2)[0]) for r in seen] == [("POST", "/api/v1.0"), ("PATCH", "/api/v1.0/choices")]
    assert json.loads(seen[0].content) == {} and json.loads(seen[1].content) == {}


@pytest.mark.asyncio
async def test_choice_permission_probes_treat_not_found_as_allowed():
    transport, _ = _transport(lambda r: httpx.Response(404, json={}))
    async with ErAdminClient(SITE, token="t", transport=transport) as client:
        assert await client.can_change_choices() is True
