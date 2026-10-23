"""Tests for action_create_er_event_types: provider lookup, permissions
preflight, and (Task 7) building and writing EarthRanger event types."""

import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from gundi_core.schemas.v2 import Integration

from app.actions.configurations import CreateErEventTypesConfig
from app.actions.handlers import action_create_er_event_types
from app.actions.tests.test_handlers import _integration_dict
from app.services.errors import IntegrationConfigurationError

FIXTURE = pathlib.Path(__file__).parent.parent.parent / "datasource" / "tests" / "fixtures" / "cmore_tags_sample.json"
RAW_TAGS = json.loads(FIXTURE.read_text())

ER_INTEGRATION_ID = "11111111-1111-1111-1111-111111111111"
# What user/me reports for a user who may create the category (and therefore
# gets its per-category permission set). EarthRanger never reports choice
# permissions there: the choices app is filtered out of user/me.
FULL_PERMISSIONS = {
    "eventcategory": ["view", "add", "change"],
    "eventtype": ["view", "add", "change"],
}
CATEGORY_PERMISSIONS = {"cmore_7932": ["view", "add", "change", "delete"]}


@pytest.fixture
def integration():
    return Integration.parse_obj(_integration_dict("99999999-9999-9999-9999-999999999999"))


def _er_integration(auth_data: dict, base_url: str = "https://site.pamdas.org/") -> Integration:
    data = _integration_dict(ER_INTEGRATION_ID)
    data["name"] = "Lapalala EarthRanger"
    data["base_url"] = base_url
    data["type"]["value"] = "earth_ranger"
    data["configurations"][0]["data"] = auth_data
    return Integration.parse_obj(data)


def _connection(provider_id: str = ER_INTEGRATION_ID) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": {"id": provider_id, "name": "Lapalala EarthRanger", "type": {"id": str(uuid.uuid4()), "name": "EarthRanger", "value": "earth_ranger"}},
        "destinations": [],
        "routing_rules": [],
        "default_route": None,
        "owner": {"id": str(uuid.uuid4()), "name": "Org"},
        "status": "healthy",
    }


@pytest.fixture
def mock_gundi(mocker):
    """Patch GundiClient in handlers: one ER provider with token auth by default."""
    from gundi_core.schemas.v2 import Connection

    gundi = MagicMock()
    gundi.get_connections = AsyncMock(return_value=[Connection.parse_obj(_connection())])
    gundi.get_integration_details = AsyncMock(
        return_value=_er_integration({"authentication_type": "token", "token": "er-token"})
    )
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=gundi)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("app.actions.handlers.GundiClient", client_cls)
    gundi.cls = client_cls
    return gundi


@pytest.fixture
def mock_er_admin(mocker):
    """Patch ErAdminClient in handlers; full permissions, empty site by default."""
    er = MagicMock()
    er.get_me = AsyncMock(return_value={"username": "ingest", "permissions": dict(FULL_PERMISSIONS)})
    er.get_event_categories = AsyncMock(return_value=[])
    er.get_event_types = AsyncMock(return_value=[])
    er.get_choices = AsyncMock(return_value=[])
    er.post_event_category = AsyncMock(return_value={})
    er.post_event_type = AsyncMock(return_value={})
    er.patch_event_type = AsyncMock(return_value={})
    er.post_choice = AsyncMock(return_value={})
    er.patch_choice = AsyncMock(return_value={})
    er.can_add_choices = AsyncMock(return_value=True)
    er.can_change_choices = AsyncMock(return_value=True)
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=er)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("app.actions.handlers.ErAdminClient", client_cls)
    er.cls = client_cls
    return er


@pytest.fixture
def mock_cmore(mocker):
    instance = MagicMock()
    instance.get_tags = AsyncMock(return_value=RAW_TAGS)
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("app.actions.handlers.CmoreClient", client_cls)
    return instance


@pytest.fixture
def activity_log(mocker):
    # The @activity_logger decorator publishes start/complete/error events to
    # PubSub; neutralise it like test_handlers does, then capture the
    # handler's own log_action_activity calls.
    mocker.patch("app.services.activity_logger.publish_event", AsyncMock(return_value=None))
    return mocker.patch("app.actions.handlers.log_action_activity", AsyncMock())


def _write_calls(er) -> int:
    return sum(
        m.await_count
        for m in (er.post_event_category, er.post_event_type, er.patch_event_type, er.post_choice, er.patch_choice)
    )


# ----- Task 6: provider resolution and preflight -----


def test_action_is_discoverable():
    from app.actions.core import discover_actions

    handlers = discover_actions(module_name="app.actions.handlers", prefix="action_")
    assert handlers["create_er_event_types"][1] is CreateErEventTypesConfig


@pytest.mark.asyncio
async def test_refuses_ephemeral_runs(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    from app.services.activity_logger import ephemeral_run

    token = ephemeral_run.set(True)
    try:
        with pytest.raises(IntegrationConfigurationError, match="saved integration"):
            await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    finally:
        ephemeral_run.reset(token)
    mock_gundi.cls.assert_not_called()


@pytest.mark.asyncio
async def test_resolves_the_single_connected_er_provider(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    mock_gundi.get_connections.assert_awaited_once_with(
        params={"destination_id": str(integration.id), "provider_type": "earth_ranger"}
    )
    mock_gundi.get_integration_details.assert_awaited_once_with(ER_INTEGRATION_ID)
    kwargs = mock_er_admin.cls.call_args.kwargs
    assert mock_er_admin.cls.call_args.args == ("https://site.pamdas.org",)
    assert kwargs["token"] == "er-token" and kwargs["username"] is None and kwargs["password"] is None


@pytest.mark.asyncio
async def test_ignores_non_earthranger_providers_the_server_returns(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """Defence in depth over the server-side provider_type filter: a dev CMORE
    destination is fed by EarthRanger, inReach and API-push providers, and a
    filter the server ignores must not pick another provider's credentials."""
    from gundi_core.schemas.v2 import Connection

    other = _connection(str(uuid.uuid4()))
    other["provider"]["type"]["value"] = "inreach"
    mock_gundi.get_connections.return_value = [Connection.parse_obj(other), Connection.parse_obj(_connection())]
    await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    mock_gundi.get_integration_details.assert_awaited_once_with(ER_INTEGRATION_ID)


@pytest.mark.asyncio
async def test_fails_with_no_provider(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_gundi.get_connections.return_value = []
    with pytest.raises(IntegrationConfigurationError, match="found 0 EarthRanger provider"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    mock_er_admin.cls.assert_not_called()


@pytest.mark.asyncio
async def test_fails_with_two_providers(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    from gundi_core.schemas.v2 import Connection

    mock_gundi.get_connections.return_value = [
        Connection.parse_obj(_connection()),
        Connection.parse_obj(_connection(str(uuid.uuid4()))),
    ]
    with pytest.raises(IntegrationConfigurationError, match="found 2 EarthRanger provider"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))


@pytest.mark.asyncio
async def test_username_password_provider_is_passed_through(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_gundi.get_integration_details.return_value = _er_integration(
        {"authentication_type": "username_password", "username": "u", "password": "p"}
    )
    await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    kwargs = mock_er_admin.cls.call_args.kwargs
    assert kwargs["username"] == "u" and kwargs["password"] == "p" and kwargs["token"] is None


@pytest.mark.asyncio
async def test_malformed_provider_url_is_a_configuration_error(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_gundi.get_integration_details.return_value = _er_integration(
        {"authentication_type": "token", "token": "t"}, base_url="site.pamdas.org"
    )
    with pytest.raises(IntegrationConfigurationError, match="site URL"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))


@pytest.mark.asyncio
async def test_missing_category_add_permission_fails_before_any_write(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """No category yet: creating it needs activity.add_eventcategory."""
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": {"eventtype": ["view"]}}
    with pytest.raises(IntegrationConfigurationError) as exc:
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    message = str(exc.value)
    assert "'ingest'" in message and "eventcategory: add" in message
    assert _write_calls(mock_er_admin) == 0


@pytest.mark.asyncio
async def test_existing_category_requires_its_per_category_permissions(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """Once the category exists, EarthRanger enforces activity.cmore_<group>_update
    (and read, to list the types), not the model permissions."""
    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "Old"}]
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": {"eventcategory": ["add"]}}
    with pytest.raises(IntegrationConfigurationError, match="cmore_7932: change, view"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    assert _write_calls(mock_er_admin) == 0

    # With the category permissions, no model permission is needed at all.
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": dict(CATEGORY_PERMISSIONS)}
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    assert result["earthranger"]["permissions_ok"] is True


@pytest.mark.asyncio
async def test_superuser_passes_the_preflight_without_listed_permissions(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_er_admin.get_me.return_value = {"username": "admin", "is_superuser": True, "permissions": {}}
    mock_er_admin.can_add_choices.return_value = False
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["earthranger"] == {"site": "https://site.pamdas.org", "username": "admin", "permissions_ok": True}


@pytest.mark.asyncio
async def test_choice_permissions_are_probed_not_read_from_user_me(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """user/me never lists the choices app, so the preflight probes the
    endpoint instead; a 403 there is reported like any other missing permission."""
    mock_er_admin.can_add_choices.return_value = False
    mock_er_admin.can_change_choices.return_value = False
    with pytest.raises(IntegrationConfigurationError, match="choice: add, change"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    assert _write_calls(mock_er_admin) == 0


@pytest.mark.asyncio
async def test_a_403_on_a_write_becomes_a_configuration_error_with_the_partial_result(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    import httpx
    from gundi_core.schemas.v2 import LogLevel

    request = httpx.Request("POST", "https://site.pamdas.org/api/v1.0/choices/")
    mock_er_admin.post_choice.side_effect = httpx.HTTPStatusError(
        "forbidden", request=request, response=httpx.Response(403, request=request)
    )
    with pytest.raises(IntegrationConfigurationError, match="HTTP 403"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    partial = next(c for c in activity_log.await_args_list if c.kwargs["level"] == LogLevel.ERROR)
    assert partial.kwargs["data"]["failed_event_type"] == "cmore_7932_20_evidence_of_poacher"
    assert partial.kwargs["data"]["event_types_completed"] == []
    assert partial.kwargs["data"]["category"]["action"] == "created"


@pytest.mark.asyncio
async def test_other_write_failures_log_the_partial_result_and_propagate(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    import httpx
    from gundi_core.schemas.v2 import LogLevel

    request = httpx.Request("POST", "https://site.pamdas.org/api/v2.0/activity/eventtypes/")
    mock_er_admin.post_event_type.side_effect = [
        {},  # first tag is written
        httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request)),
    ]
    with pytest.raises(httpx.HTTPStatusError):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20", "43"]))
    partial = next(c for c in activity_log.await_args_list if c.kwargs["level"] == LogLevel.ERROR)
    assert [et["value"] for et in partial.kwargs["data"]["event_types_completed"]] == ["cmore_7932_20_evidence_of_poacher"]
    assert partial.kwargs["data"]["failed_event_type"] == "cmore_7932_43_poached_animal"


@pytest.mark.asyncio
async def test_preflight_passes_and_reports(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["earthranger"] == {"site": "https://site.pamdas.org", "username": "ingest", "permissions_ok": True}
    assert result["dry_run"] is True


def test_missing_er_permissions_pure():
    from app.actions.handlers import _missing_er_permissions

    ok = {"username": "u", "permissions": dict(FULL_PERMISSIONS)}
    assert _missing_er_permissions(ok, group=7932, category_exists=False, can_add_choices=True, can_change_choices=True) == {}
    assert _missing_er_permissions({"permissions": {}}, group=7932, category_exists=False, can_add_choices=False, can_change_choices=False) == {
        "choice": ["add", "change"],
        "eventcategory": ["add"],
    }
    assert _missing_er_permissions({"permissions": {"cmore_7932": ["view"]}}, group=7932, category_exists=True, can_add_choices=True, can_change_choices=True) == {
        "cmore_7932": ["change"],
    }
    assert _missing_er_permissions({"is_superuser": True}, group=7932, category_exists=True, can_add_choices=False, can_change_choices=False) == {}
    assert _missing_er_permissions({}, group=1, category_exists=False, can_add_choices=True, can_change_choices=True) == {"eventcategory": ["add"]}


# ----- Task 7: build, reconcile and write -----

EVIDENCE_TYPE_OPTION_COUNT = 22


def _existing_type(value="cmore_7932_20_old_name", keys=("f261_old_key",)):
    return {
        "value": value,
        "display": "Old display",
        "category": "cmore_7932",
        "is_active": True,
        "schema": {"json": {"properties": {k: {"type": "string"} for k in keys}}, "ui": {}},
    }


@pytest.mark.asyncio
async def test_unknown_tag_ref_fails_before_any_write(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    with pytest.raises(IntegrationConfigurationError, match="not found"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["No Such Tag"]))
    assert _write_calls(mock_er_admin) == 0


@pytest.mark.asyncio
async def test_dry_run_builds_but_writes_nothing(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["dry_run"] is True
    assert result["category"] == {"value": "cmore_7932", "display": "CMORE group 7932", "action": "created"}
    et = result["event_types"][0]
    assert et["value"] == "cmore_7932_20_evidence_of_poacher" and et["action"] == "created"
    assert et["fields"] == 5  # six CMORE fields minus Reported By
    assert result["choices"] == {"created": EVIDENCE_TYPE_OPTION_COUNT, "updated": 0, "deactivated": 0}
    assert _write_calls(mock_er_admin) == 0


@pytest.mark.asyncio
async def test_first_run_creates_category_type_and_choices(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    mock_er_admin.post_event_category.assert_awaited_once_with(
        {"value": "cmore_7932", "display": "CMORE group 7932", "is_active": True}
    )
    payload = mock_er_admin.post_event_type.await_args.args[0]
    assert payload["value"] == "cmore_7932_20_evidence_of_poacher" and payload["category"] == "cmore_7932"
    assert "f261_evidence_type" in payload["schema"]["json"]["properties"]
    assert mock_er_admin.post_choice.await_count == EVIDENCE_TYPE_OPTION_COUNT
    assert all(c.args[0]["field"] == "f261_evidence_type" for c in mock_er_admin.post_choice.await_args_list)
    mock_er_admin.patch_event_type.assert_not_awaited()
    assert result["category"]["action"] == "created"
    assert result["choices"] == {"created": EVIDENCE_TYPE_OPTION_COUNT, "updated": 0, "deactivated": 0}


@pytest.mark.asyncio
async def test_share_group_label_names_the_category(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    await action_create_er_event_types(
        integration, CreateErEventTypesConfig(tags=["20"], share_group_label="Lapalala CMORE")
    )
    assert mock_er_admin.post_event_category.await_args.args[0]["display"] == "Lapalala CMORE"


@pytest.mark.asyncio
async def test_second_run_patches_the_existing_type_and_leaves_choices(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    from app.datasource.er_event_types import slugify

    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "CMORE group 7932"}]
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": dict(CATEGORY_PERMISSIONS)}
    mock_er_admin.get_event_types.return_value = [_existing_type(), {"value": "rhino_carcass", "schema": {}}]
    tag = next(t for t in RAW_TAGS[0]["tags"] if t["id"] == 20)
    field = next(f for f in tag["fields"] if f["id"] == 261)
    mock_er_admin.get_choices.return_value = [
        {"id": str(i), "value": slugify(lk["value"]), "display": lk["value"], "is_active": True, "ordernum": lk["order"]}
        for i, lk in enumerate(field["lookups"])
    ]
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    mock_er_admin.post_event_type.assert_not_awaited()
    value, payload = mock_er_admin.patch_event_type.await_args.args
    assert value == "cmore_7932_20_old_name" and payload["value"] == "cmore_7932_20_old_name"
    assert "f261_old_key" in payload["schema"]["json"]["properties"]
    assert "f261_evidence_type" not in payload["schema"]["json"]["properties"]
    mock_er_admin.get_choices.assert_awaited_once_with("f261_old_key")
    assert mock_er_admin.post_choice.await_count == 0 and mock_er_admin.patch_choice.await_count == 0
    assert result["event_types"][0]["action"] == "updated"
    assert result["choices"] == {"created": 0, "updated": 0, "deactivated": 0}


@pytest.mark.asyncio
async def test_second_run_reads_the_schema_earthranger_returns_as_a_json_string(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """EarthRanger's v2 listing returns `schema` as a JSON string, not an
    object (seen on dev 2026-10-23: the re-run failed with AttributeError
    'str' object has no attribute 'get'). Existing field keys must still be
    found so the re-run patches in place."""
    import json as _json

    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "CMORE group 7932"}]
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": dict(CATEGORY_PERMISSIONS)}
    existing = _existing_type()
    existing["schema"] = _json.dumps(existing["schema"], indent=2)
    mock_er_admin.get_event_types.return_value = [existing, {"value": "rhino_carcass", "schema": "not json"}]

    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))

    mock_er_admin.post_event_type.assert_not_awaited()
    value, payload = mock_er_admin.patch_event_type.await_args.args
    assert value == "cmore_7932_20_old_name"
    assert "f261_old_key" in payload["schema"]["json"]["properties"]
    assert result["event_types"][0]["action"] == "updated"


@pytest.mark.asyncio
async def test_existing_category_is_not_rewritten(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "Old"}]
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": dict(CATEGORY_PERMISSIONS)}
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    mock_er_admin.post_event_category.assert_not_awaited()
    assert result["category"] == {"value": "cmore_7932", "display": "Old", "action": "unchanged"}


@pytest.mark.asyncio
async def test_result_lists_skipped_and_shallow_fields(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20", "Poached Animal"], dry_run=True))
    by_value = {et["value"]: et for et in result["event_types"]}
    evidence = by_value["cmore_7932_20_evidence_of_poacher"]
    assert evidence["skipped"][0]["name"] == "Reported By"
    animal = by_value["cmore_7932_43_poached_animal"]
    assert animal["shallow_fields"][0]["name"] == "Animal"


@pytest.mark.asyncio
async def test_activity_log_summarises_the_run(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    from gundi_core.schemas.v2 import LogLevel

    await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    levels = [c.kwargs["level"] for c in activity_log.await_args_list]
    assert LogLevel.INFO in levels and LogLevel.WARNING in levels
    summary = next(c for c in activity_log.await_args_list if c.kwargs["level"] == LogLevel.INFO)
    assert summary.kwargs["data"]["event_types"] == 1
    assert summary.kwargs["data"]["choices"]["created"] == EVIDENCE_TYPE_OPTION_COUNT
    warning = next(c for c in activity_log.await_args_list if c.kwargs["level"] == LogLevel.WARNING)
    assert "Evidence of Poacher" in warning.kwargs["title"]



# ----- overlap with hand-built mappings -----


def _with_deliver_mappings(integration, entries):
    """The integration with a saved Deliver config whose event_type_to_tag
    holds ``entries`` (dicts with event_type and tag)."""
    data = integration.dict()
    data["configurations"].append({
        "id": str(uuid.uuid4()),
        "integration": str(integration.id),
        "action": {"id": str(uuid.uuid4()), "type": "push", "name": "Deliver", "value": "deliver"},
        "data": {"event_type_to_tag": [dict(e, field_mappings=[]) for e in entries]},
    })
    return Integration.parse_obj(data)


@pytest.mark.asyncio
async def test_flags_a_tag_already_mapped_from_a_hand_built_type_and_creates_it_anyway(
    integration, mock_gundi, mock_er_admin, mock_cmore, activity_log
):
    """A tag that a hand-built ER type already maps to (by tag id or name) is
    created anyway; the result and the activity log name the existing type, so
    the operator can decide which one rangers should use."""
    from gundi_core.schemas.v2 import LogLevel

    integration = _with_deliver_mappings(integration, [
        {"event_type": "poacher_evidence", "tag": "20"},
        {"event_type": "snare_found", "tag": "Evidence of Poacher"},
        {"event_type": "poached_animal_rep", "tag": "43"},
    ])
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))

    mock_er_admin.post_event_type.assert_awaited_once()
    assert result["event_types"][0]["mapped_by_hand"] == ["poacher_evidence", "snare_found"]
    warning = next(
        c for c in activity_log.await_args_list
        if c.kwargs["level"] == LogLevel.WARNING and "already mapped" in c.kwargs["title"]
    )
    assert "Evidence of Poacher" in warning.kwargs["title"]
    assert warning.kwargs["data"]["mapped_by_hand"] == ["poacher_evidence", "snare_found"]


@pytest.mark.asyncio
async def test_an_explicit_mapping_for_the_generated_type_is_not_an_overlap(
    integration, mock_gundi, mock_er_admin, mock_cmore, activity_log
):
    integration = _with_deliver_mappings(integration, [
        {"event_type": "cmore_7932_20_evidence_of_poacher", "tag": "20"},
    ])
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["event_types"][0]["mapped_by_hand"] == []


@pytest.mark.asyncio
async def test_no_deliver_config_means_no_overlap(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["event_types"][0]["mapped_by_hand"] == []



@pytest.mark.asyncio
async def test_only_the_entry_delivery_uses_counts_as_an_overlap(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """Delivery uses the first event_type_to_tag entry per event type; a later
    duplicate never routes anything, so it is not an overlap."""
    integration = _with_deliver_mappings(integration, [
        {"event_type": "snare_found", "tag": "43"},
        {"event_type": "snare_found", "tag": "20"},
    ])
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["event_types"][0]["mapped_by_hand"] == []


@pytest.mark.asyncio
async def test_a_generated_type_routed_to_another_tag_counts_as_an_overlap(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """An explicit entry wins over the naming convention, so a generated type
    mapped by hand to a different tag does land events on that tag."""
    integration = _with_deliver_mappings(integration, [
        {"event_type": "cmore_7932_20_evidence_of_poacher", "tag": "43"},
    ])
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["43"], dry_run=True))
    assert result["event_types"][0]["mapped_by_hand"] == ["cmore_7932_20_evidence_of_poacher"]


@pytest.mark.asyncio
async def test_overlap_warning_is_logged_only_when_the_type_is_created(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    """Preview and re-runs report the overlap in the result only; the
    activity-log warning is written once, when the second type appears."""
    integration = _with_deliver_mappings(integration, [{"event_type": "poacher_evidence", "tag": "20"}])

    def overlap_warnings():
        return [c for c in activity_log.await_args_list if "already mapped" in c.kwargs.get("title", "")]

    preview = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert preview["event_types"][0]["mapped_by_hand"] == ["poacher_evidence"]
    assert overlap_warnings() == []

    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "CMORE group 7932"}]
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": dict(CATEGORY_PERMISSIONS)}
    mock_er_admin.get_event_types.return_value = [_existing_type()]
    rerun = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    assert rerun["event_types"][0]["action"] == "updated"
    assert rerun["event_types"][0]["mapped_by_hand"] == ["poacher_evidence"]
    assert overlap_warnings() == []
