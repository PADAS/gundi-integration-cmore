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
FULL_PERMISSIONS = {
    "eventcategory": ["view", "add", "change"],
    "eventtype": ["view", "add", "change"],
    "choice": ["view", "add", "change"],
}


@pytest.fixture
def integration():
    return Integration.parse_obj(_integration_dict("99999999-9999-9999-9999-999999999999"))


def _er_integration(auth_data: dict, base_url: str = "https://site.pamdas.org/") -> Integration:
    data = _integration_dict(ER_INTEGRATION_ID)
    data["name"] = "Lapalala EarthRanger"
    data["base_url"] = base_url
    data["type"]["value"] = "earthranger"
    data["configurations"][0]["data"] = auth_data
    return Integration.parse_obj(data)


def _connection(provider_id: str = ER_INTEGRATION_ID) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "provider": {"id": provider_id, "name": "Lapalala EarthRanger", "type": {"id": str(uuid.uuid4()), "name": "EarthRanger", "value": "earthranger"}},
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
        params={"destination_id": str(integration.id), "provider_type": "earthranger"}
    )
    mock_gundi.get_integration_details.assert_awaited_once_with(ER_INTEGRATION_ID)
    kwargs = mock_er_admin.cls.call_args.kwargs
    assert mock_er_admin.cls.call_args.args == ("https://site.pamdas.org",)
    assert kwargs["token"] == "er-token" and kwargs["username"] is None and kwargs["password"] is None


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
async def test_missing_permissions_fail_before_any_write(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": {"eventtype": ["view"]}}
    with pytest.raises(IntegrationConfigurationError) as exc:
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))
    message = str(exc.value)
    assert "'ingest'" in message
    assert "choice: add, change" in message
    assert "eventcategory: add" in message
    assert "eventtype: add, change" in message
    assert _write_calls(mock_er_admin) == 0


@pytest.mark.asyncio
async def test_missing_choice_entry_is_reported_not_crashed(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    permissions = {k: v for k, v in FULL_PERMISSIONS.items() if k != "choice"}
    mock_er_admin.get_me.return_value = {"username": "ingest", "permissions": permissions}
    with pytest.raises(IntegrationConfigurationError, match="choice: add, change"):
        await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"]))


@pytest.mark.asyncio
async def test_preflight_passes_and_reports(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    result = await action_create_er_event_types(integration, CreateErEventTypesConfig(tags=["20"], dry_run=True))
    assert result["earthranger"] == {"site": "https://site.pamdas.org", "username": "ingest", "permissions_ok": True}
    assert result["dry_run"] is True


def test_missing_er_permissions_pure():
    from app.actions.handlers import _missing_er_permissions

    assert _missing_er_permissions({"permissions": dict(FULL_PERMISSIONS)}) == {}
    assert _missing_er_permissions({"permissions": {}}) == {
        "choice": ["add", "change"],
        "eventcategory": ["add"],
        "eventtype": ["add", "change"],
    }
    assert _missing_er_permissions({}) == _missing_er_permissions({"permissions": {}})


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
    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "CMORE group 7932"}]
    mock_er_admin.get_event_types.return_value = [_existing_type(), {"value": "rhino_carcass", "schema": {}}]
    tag = next(t for t in RAW_TAGS[0]["tags"] if t["id"] == 20)
    field = next(f for f in tag["fields"] if f["id"] == 261)
    mock_er_admin.get_choices.return_value = [
        {"id": str(i), "value": lk["value"], "display": lk["value"], "is_active": True, "ordernum": lk["order"]}
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
async def test_existing_category_is_not_rewritten(integration, mock_gundi, mock_er_admin, mock_cmore, activity_log):
    mock_er_admin.get_event_categories.return_value = [{"value": "cmore_7932", "display": "Old"}]
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
