"""Tests for the single action_deliver handler and its private helpers."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from gundi_core.events import GundiDelivery, ProviderInfo
from gundi_core.schemas.v2 import (
    Attachment,
    Event,
    EventUpdate,
    Location,
    Observation,
    RouteConfiguration,
    TextMessage,
)


# ----- fixtures -----


def _integration_dict(integration_id: str) -> dict:
    return {
        "id": integration_id,
        "name": "C-more Test Integration",
        "base_url": "https://cmorewc1.chpc.ac.za/za/WebAPI/api",
        "enabled": True,
        "type": {
            "id": str(uuid.uuid4()),
            "name": "C-more",
            "value": "cmore",
            "description": "",
            "actions": [
                {
                    "id": str(uuid.uuid4()),
                    "type": "auth",
                    "name": "Authenticate",
                    "value": "auth",
                    "description": "Auth",
                    "schema": {},
                },
            ],
        },
        "owner": {
            "id": str(uuid.uuid4()),
            "name": "Test Org",
            "description": "",
        },
        "configurations": [
            {
                "id": str(uuid.uuid4()),
                "integration": integration_id,
                "action": {
                    "id": str(uuid.uuid4()),
                    "type": "auth",
                    "name": "Authenticate",
                    "value": "auth",
                },
                "data": {
                    "token": "test-token",
                    "base_url": "https://cmorewc1.chpc.ac.za/za/WebAPI/api",
                    "owner_group_id": 7932,
                },
            }
        ],
        "additional": {"generic_model": True},
        "default_route": None,
        "status": "healthy",
        "status_details": "",
    }


@pytest.fixture
def integration():
    from gundi_core.schemas.v2 import Integration

    return Integration.parse_obj(_integration_dict("99999999-9999-9999-9999-999999999999"))


@pytest.fixture
def deliver_config():
    from app.actions.configurations import (
        CmoreFieldMapping,
        CmoreTagMapping,
        DeliverConfig,
    )

    return DeliverConfig(
        event_type_to_tag=[
            CmoreTagMapping(
                event_type="lion_sighting",
                tag_name="Wildlife Sighting",
                field_mappings=[
                    CmoreFieldMapping(event_details_key="species", cmore_field_name="Species"),
                    CmoreFieldMapping(event_details_key="count", cmore_field_name="Count"),
                ],
            ),
        ],
    )


@pytest.fixture
def fake_tag_info():
    """Stand-in TagInfo a tag_index.get mock can return."""
    from app.datasource.tag_index import FieldInfo, TagInfo

    return TagInfo(
        id=42,
        name="Wildlife Sighting",
        domain="Wildlife",
        type_limiter="Incident",
        fields={
            "Species": FieldInfo(id=101, name="Species", data_type="String"),
            "Count": FieldInfo(id=102, name="Count", data_type="Number"),
        },
    )


def _patch_tag_index(mocker, returning):
    """Patch the module-level tag_index singleton's get() to return a fixed value."""
    from app.actions import handlers as handlers_module

    async def _async_get(*args, **kwargs):
        return returning

    mocker.patch.object(handlers_module.tag_index, "get", side_effect=_async_get)


@pytest.fixture
def provider_info():
    return ProviderInfo(
        provider_id=str(uuid.uuid4()),
        provider_type="telonics",
        provider_name="Telonics Provider",
        owner_id=str(uuid.uuid4()),
        owner_name="Wildlife Org",
    )


@pytest.fixture
def observation():
    return Observation(
        source_id=uuid.uuid4(),
        external_source_id="device-42",
        source_name="Collar 42",
        type="tracking-device",
        subject_type="elephant",
        recorded_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        location=Location(lon=-122.0, lat=47.0),
    )


@pytest.fixture
def event():
    return Event(
        source_id=uuid.uuid4(),
        external_source_id="camera-trap-7",
        recorded_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        location=Location(lon=-122.0, lat=47.0),
        title="Lion sighting",
        event_type="lion_sighting",
        event_details={"species": "lion"},
    )


@pytest.fixture
def text_message():
    return TextMessage(
        source_id=uuid.uuid4(),
        external_source_id="device-tx",
        sender="555-0100",
        recipients=["dispatch@example.com"],
        text="Need help",
        created_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def metadata():
    return {"gundi_id": str(uuid.uuid4())}


def _patch_cmore_client(mocker, post_locations_return=None, post_event_return=None, post_comment_return=None):
    """Patch the CmoreClient async-context-manager and capture method calls."""
    inner = MagicMock()
    inner.post_locations = AsyncMock(return_value=post_locations_return or {"status": "ok"})
    inner.post_event = AsyncMock(return_value=post_event_return or {"messageId": 14697})
    inner.post_comment = AsyncMock(return_value=post_comment_return or {"id": 88888})
    inner.post_properties = AsyncMock(return_value={"status": "ok"})
    inner.get_gateway_mapping = AsyncMock(return_value=[])
    inner.create_gnodes = AsyncMock(
        return_value=[MagicMock(clientId=8888, error=None)]
    )

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=None)
    mocker.patch("app.actions.handlers.CmoreClient", return_value=cm)
    return inner


def _patch_state_manager(mocker, existing_client_id=None):
    state = AsyncMock()
    state.get_state = AsyncMock(
        return_value={"client_id": existing_client_id} if existing_client_id else {}
    )
    state.set_state = AsyncMock()
    mocker.patch("app.actions.handlers.state_manager", state)
    return state


def _patch_activity_logger(mocker):
    activity_log = AsyncMock()
    mocker.patch("app.actions.handlers.log_action_activity", activity_log)
    # The @activity_logger decorator wraps action_deliver — neutralize by
    # making it a pass-through (it would otherwise try to publish to PubSub).
    mocker.patch(
        "app.services.activity_logger.publish_event", AsyncMock(return_value=None)
    )
    return activity_log


# ----- tests -----


@pytest.mark.asyncio
async def test_deliver_with_observation_payload_posts_to_locations(
    mocker, integration, deliver_config, provider_info, observation, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker, existing_client_id=8888)
    _patch_activity_logger(mocker)

    delivery = GundiDelivery(payload=observation, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_locations.assert_awaited_once()
    inner.post_event.assert_not_awaited()
    assert result["locations_posted"] == 1
    assert result["client_id"] == 8888


@pytest.mark.asyncio
async def test_deliver_with_event_payload_posts_event(
    mocker, integration, deliver_config, provider_info, event, metadata, fake_tag_info
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=fake_tag_info)

    # event.event_details has species but not count; only Species value lands.
    event.event_details = {"species": "lion"}
    delivery = GundiDelivery(payload=event, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    inner.post_locations.assert_not_awaited()
    assert result["event_posted"] is True

    posted = inner.post_event.await_args[0][0]
    assert posted.tags is not None
    assert posted.tags[0].tagId == 42
    # Only Species field was provided; Count is missing from event_details, skipped.
    assert len(posted.tags[0].values) == 1
    assert posted.tags[0].values[0].fieldId == 101
    assert posted.tags[0].values[0].value == "lion"


@pytest.mark.asyncio
async def test_event_with_missing_tag_still_posts(
    mocker, integration, deliver_config, provider_info, event, metadata
):
    """If the configured tag name isn't found on the CMORE instance, the event
    still posts (without a tag)."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=None)  # tag not found

    delivery = GundiDelivery(payload=event, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    posted = inner.post_event.await_args[0][0]
    assert posted.tags is None  # No tag attached, but event still posted
    assert result["event_posted"] is True


@pytest.mark.asyncio
async def test_event_with_unmapped_event_type_posts_without_tag(
    mocker, integration, deliver_config, provider_info, event, metadata
):
    """event_type is not in event_type_to_tag — no tag attached, no tag_index call."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    tag_mock = mocker.patch("app.actions.handlers.tag_index.get")

    event.event_type = "unconfigured_event_type"
    delivery = GundiDelivery(payload=event, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    posted = inner.post_event.await_args[0][0]
    assert posted.tags is None
    tag_mock.assert_not_called()
    assert result["event_posted"] is True


@pytest.mark.asyncio
async def test_event_field_mapping_skips_unknown_field(
    mocker, integration, deliver_config, provider_info, event, metadata, fake_tag_info
):
    """If a configured field_name doesn't exist on the tag, that field is
    skipped but the rest of the tag values are sent."""
    from app.actions.configurations import (
        CmoreFieldMapping,
        CmoreTagMapping,
        DeliverConfig,
    )
    from app.actions.handlers import action_deliver

    # Add a mapping to a field that doesn't exist on the fake tag.
    deliver_config = DeliverConfig(
        event_type_to_tag=[
            CmoreTagMapping(
                event_type="lion_sighting",
                tag_name="Wildlife Sighting",
                field_mappings=[
                    CmoreFieldMapping(event_details_key="species", cmore_field_name="Species"),
                    CmoreFieldMapping(event_details_key="count", cmore_field_name="Count"),
                    CmoreFieldMapping(event_details_key="made_up", cmore_field_name="Nonexistent"),
                ],
            ),
        ],
    )

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=fake_tag_info)

    event.event_details = {"species": "lion", "count": 3, "made_up": "value"}
    delivery = GundiDelivery(payload=event, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    posted = inner.post_event.await_args[0][0]
    # Only species + count made it; made_up was skipped (Nonexistent field).
    assert len(posted.tags[0].values) == 2
    posted_fields = {v.fieldId: v.value for v in posted.tags[0].values}
    assert posted_fields == {101: "lion", 102: "3"}


def test_stringify_for_cmore_handles_common_types():
    from datetime import datetime, timezone

    from app.actions.handlers import _stringify_for_cmore

    assert _stringify_for_cmore("hello") == "hello"
    assert _stringify_for_cmore(42) == "42"
    assert _stringify_for_cmore(3.14) == "3.14"
    assert _stringify_for_cmore(True) == "true"
    assert _stringify_for_cmore(False) == "false"
    assert _stringify_for_cmore(None) is None  # signals "skip"
    assert (
        _stringify_for_cmore(datetime(2026, 6, 3, 12, 0, 0, tzinfo=timezone.utc))
        == "2026-06-03T12:00:00+00:00"
    )
    assert _stringify_for_cmore([1, 2, 3]) == "[1, 2, 3]"
    assert _stringify_for_cmore({"k": "v"}) == '{"k": "v"}'


# ---------------------------------------------------------------------------
# Type-aware field-value resolution: Lookup/Number/Boolean coercion, value
# translation maps, and multi-value handling. Grounded in the real ER
# "rhino_carcass" event_details and the CMORE Wildlife "Rhino Carcass" tag.
# ---------------------------------------------------------------------------

from app.actions.configurations import CmoreFieldMapping, CmoreValueMapping
from app.actions.handlers import _resolve_field_value, _resolve_field_values
from app.datasource.tag_index import FieldInfo


def _lookup(*values):
    return [{"id": i, "value": v} for i, v in enumerate(values, start=1)]


def test_resolve_lookup_matches_case_insensitively():
    """A source value that differs only in case/punctuation resolves to the
    canonical CMORE option (e.g. ER 'male' → CMORE 'Male')."""
    sex = FieldInfo(id=1261, name="Animal Sex", data_type="Lookup",
                    lookups=_lookup("Male", "Female", "Indeterminable"))
    assert _resolve_field_value(sex, "male") == "Male"
    assert _resolve_field_value(sex, "INDETERMINABLE") == "Indeterminable"


def test_resolve_lookup_drops_unmatched_value():
    """A value with no matching option is dropped (returns None)."""
    sex = FieldInfo(id=1261, name="Animal Sex", data_type="Lookup",
                    lookups=_lookup("Male", "Female", "Indeterminable"))
    assert _resolve_field_value(sex, "unknown") is None


def test_resolve_number_validates_numeric():
    snares = FieldInfo(id=264, name="Number of Snares Found", data_type="Number")
    assert _resolve_field_value(snares, "3") == "3"
    assert _resolve_field_value(snares, "lots") is None


def test_resolve_boolean_coerces_yes_no():
    vet = FieldInfo(id=1282, name="Veterinarian on Scene", data_type="Boolean")
    assert _resolve_field_value(vet, "yes") == "true"
    assert _resolve_field_value(vet, "no") == "false"
    assert _resolve_field_value(vet, "maybe") is None


def test_resolve_field_values_applies_value_mapping_then_lookup():
    """ER 'b_3_months1_year' → (value map) 'Calf' → (lookup) canonical 'Calf'."""
    age = FieldInfo(id=1260, name="Animal Age", data_type="Lookup",
                    lookups=_lookup("Adult", "Sub-Adult", "Calf"))
    fm = CmoreFieldMapping(
        event_details_key="age_of_animal",
        cmore_field_name="Animal Age",
        value_mappings=[CmoreValueMapping(from_value="b_3_months1_year", to_value="Calf")],
    )
    assert _resolve_field_values(age, fm, "b_3_months1_year") == ["Calf"]


def test_resolve_field_values_supports_multi_value_lookup():
    """A multi-value lookup field expands a list into several resolved values."""
    evidence = FieldInfo(id=261, name="Evidence Type", data_type="Lookup",
                         allow_multiple=True, lookups=_lookup("Snare", "Trap", "Net"))
    fm = CmoreFieldMapping(event_details_key="evidence", cmore_field_name="Evidence Type")
    assert _resolve_field_values(evidence, fm, ["snare", "net"]) == ["Snare", "Net"]


@pytest.fixture
def rhino_carcass_tag_info():
    """Trimmed CMORE 'Rhino Carcass' tag mirroring the real Wildlife domain."""
    from app.datasource.tag_index import TagInfo

    return TagInfo(
        id=26, name="Rhino Carcass", domain="Wildlife", type_limiter="Incident",
        fields={
            "Rhino Spesies": FieldInfo(id=294, name="Rhino Spesies", data_type="Lookup",
                                       lookups=_lookup("White", "Black")),
            "Animal Sex": FieldInfo(id=1261, name="Animal Sex", data_type="Lookup",
                                    lookups=_lookup("Male", "Female", "Indeterminable")),
            "Animal Age": FieldInfo(id=1260, name="Animal Age", data_type="Lookup",
                                    lookups=_lookup("Adult", "Sub-Adult", "Calf")),
            "Carcass Age": FieldInfo(id=1262, name="Carcass Age", data_type="Lookup",
                                     lookups=_lookup("Today", "Fresh (less than 3 days)")),
            "Kill Type": FieldInfo(id=1263, name="Kill Type", data_type="Lookup",
                                   lookups=_lookup("Darted", "Poisoned", "Shot", "Snare", "Spear")),
            "Skull Tag Number": FieldInfo(id=1278, name="Skull Tag Number", data_type="String"),
        },
    )


@pytest.mark.asyncio
async def test_deliver_rhino_carcass_resolves_lookups(
    mocker, integration, provider_info, metadata, rhino_carcass_tag_info
):
    """End-to-end with the real ER rhino_carcass event_details: lookups resolve
    (via case-match or value-map), an unmappable value is dropped, the String
    passes through."""
    from app.actions.configurations import CmoreTagMapping, DeliverConfig
    from app.actions.handlers import action_deliver

    config = DeliverConfig(
        event_type_to_tag=[
            CmoreTagMapping(
                event_type="rhino_carcass",
                tag_name="Rhino Carcass",
                field_mappings=[
                    CmoreFieldMapping(event_details_key="animal_sex", cmore_field_name="Animal Sex"),
                    CmoreFieldMapping(
                        event_details_key="animal_common_name", cmore_field_name="Rhino Spesies",
                        value_mappings=[CmoreValueMapping(from_value="Black Rhino", to_value="Black")],
                    ),
                    CmoreFieldMapping(
                        event_details_key="age_of_animal", cmore_field_name="Animal Age",
                        value_mappings=[CmoreValueMapping(from_value="b_3_months1_year", to_value="Calf")],
                    ),
                    CmoreFieldMapping(
                        event_details_key="age_of_carcass", cmore_field_name="Carcass Age",
                        value_mappings=[CmoreValueMapping(from_value="very_fresh", to_value="Today")],
                    ),
                    # 'fence' maps to no valid Kill Type option → dropped.
                    CmoreFieldMapping(event_details_key="cause_of_death", cmore_field_name="Kill Type"),
                    CmoreFieldMapping(event_details_key="animal_id", cmore_field_name="Skull Tag Number"),
                ],
            ),
        ],
    )

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=rhino_carcass_tag_info)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-rhino",
        recorded_at=datetime(2026, 6, 10, 20, 3, 25, tzinfo=timezone.utc),
        location=Location(lat=47.686, lon=-122.359),
        title="Rhino Carcass",
        event_type="rhino_carcass",
        event_details={
            "animal_id": "RF001",
            "animal_sex": "male",
            "age_of_animal": "b_3_months1_year",
            "age_of_carcass": "very_fresh",
            "cause_of_death": "fence",
            "animal_common_name": "Black Rhino",
            "reported_to_opswpu": "yes",
        },
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, config, delivery, metadata)

    posted = inner.post_event.await_args[0][0]
    sent = {v.fieldId: v.value for v in posted.tags[0].values}
    assert sent == {
        1261: "Male",   # animal_sex 'male' → 'Male' (case match)
        294: "Black",   # 'Black Rhino' → 'Black' (value map)
        1260: "Calf",   # 'b_3_months1_year' → 'Calf' (value map)
        1262: "Today",  # 'very_fresh' → 'Today' (value map)
        1278: "RF001",  # animal_id → String passthrough
        # cause_of_death 'fence' had no valid Kill Type option → dropped
    }


@pytest.mark.asyncio
async def test_deliver_event_update_logs_warning_when_no_mapping_exists(
    mocker, integration, deliver_config, provider_info, metadata
):
    """EventUpdate for an external_source_id we never saw via post_event → log WARNING and drop."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    # State manager returns empty for the EventUpdate's external_source_id lookup.
    _patch_state_manager(mocker)
    activity_log = _patch_activity_logger(mocker)

    eu = EventUpdate(
        gundi_id="gid-never-seen",
        source_id=uuid.uuid4(),
        external_source_id="er-uuid-never-seen",
        changes={"status": "resolved"},
    )
    delivery = GundiDelivery(payload=eu, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_not_awaited()
    inner.post_comment.assert_not_awaited()
    assert result["dropped"] is True
    assert result["reason"] == "cmore_message_id_not_found"
    # The WARNING activity log surfaces this for ops monitoring.
    activity_log.assert_awaited_once()
    log_kwargs = activity_log.call_args.kwargs
    from gundi_core.schemas.v2 import LogLevel
    assert log_kwargs["level"] == LogLevel.WARNING


@pytest.mark.asyncio
async def test_deliver_event_update_posts_status_change_as_comment(
    mocker, integration, deliver_config, provider_info, metadata
):
    """Status change → post_comment with synthetic 'Status changed to X' body."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    # State manager has the mapping from a prior post_event delivery.
    state = AsyncMock()
    state.get_state = AsyncMock(return_value={"cmore_message_id": 14697})
    state.set_state = AsyncMock()
    mocker.patch("app.actions.handlers.state_manager", state)
    _patch_activity_logger(mocker)

    eu = EventUpdate(
        gundi_id="gid-seen",
        source_id=uuid.uuid4(),
        external_source_id="er-uuid-seen",
        changes={"status": "active"},
    )
    delivery = GundiDelivery(payload=eu, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_comment.assert_awaited_once()
    sent_comment = inner.post_comment.call_args.args[0]
    assert sent_comment.rootMessageId == 14697
    assert "Status changed to active" in sent_comment.description
    assert result["comment_posted"] is True


@pytest.mark.asyncio
async def test_deliver_event_update_posts_note_as_comment_with_author(
    mocker, integration, deliver_config, provider_info, metadata
):
    """A new note in changes → comment body carries author + timestamp + text."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    state = AsyncMock()
    state.get_state = AsyncMock(return_value={"cmore_message_id": 14697})
    state.set_state = AsyncMock()
    mocker.patch("app.actions.handlers.state_manager", state)
    _patch_activity_logger(mocker)

    eu = EventUpdate(
        gundi_id="gid-seen",
        source_id=uuid.uuid4(),
        external_source_id="er-uuid-seen",
        changes={
            "notes": [
                {
                    "id": "note-uuid",
                    "text": "Fresh tracks at the perimeter.",
                    "created_at": "2026-06-09T10:00:00+00:00",
                    "updates": [
                        {"user": {"username": "ranger1"}, "type": "add_eventnote"}
                    ],
                }
            ]
        },
    )
    delivery = GundiDelivery(payload=eu, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_comment.assert_awaited_once()
    sent = inner.post_comment.call_args.args[0]
    assert sent.rootMessageId == 14697
    assert "ranger1" in sent.description
    assert "Fresh tracks at the perimeter." in sent.description
    assert "2026-06-09T10:00:00+00:00" in sent.description


def test_format_event_update_comment_handles_all_change_kinds():
    """The formatter covers each change type emitted by the ER runner."""
    from app.actions.handlers import _format_event_update_comment

    assert _format_event_update_comment({"status": "active"}) == "Status changed to active"
    assert _format_event_update_comment({"priority": 200}) == "Priority changed to 200"
    assert _format_event_update_comment({"title": "X"}) == "Title changed to 'X'"
    assert _format_event_update_comment({}) is None
    assert _format_event_update_comment({"unknown": "field"}) is None
    note = {
        "id": "n",
        "text": "hello",
        "created_at": "2026-06-09T10:00:00+00:00",
        "updates": [{"user": {"username": "ranger1"}}],
    }
    body = _format_event_update_comment({"notes": [note]})
    assert "ranger1" in body and "hello" in body and "2026-06-09T10:00:00+00:00" in body


@pytest.mark.asyncio
async def test_deliver_event_records_mapping_for_followup_updates(
    mocker, integration, deliver_config, provider_info, metadata
):
    """post_event response carries messageId; we persist it for future EventUpdate lookups."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker, post_event_return={"messageId": 99999})
    state = _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        gundi_id="gid-new",
        source_id=uuid.uuid4(),
        external_source_id="er-uuid-new",
        recorded_at=datetime.now(tz=timezone.utc),
        event_type="poacher_sighting_rep",
        title="A sighting",
        location=Location(lat=0.0, lon=0.0),
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    state.set_state.assert_awaited_once()
    call_kwargs = state.set_state.call_args.kwargs
    # Keyed by gundi_id (unique per event), not external_source_id (the source).
    assert call_kwargs["source_id"] == "gid-new"
    assert call_kwargs["state"] == {"cmore_message_id": 99999}
    # Mapping is bounded by a TTL so the Redis keyspace doesn't grow forever.
    from app.actions.handlers import CMORE_EVENT_MAPPING_TTL_SECONDS
    assert call_kwargs["ttl_seconds"] == CMORE_EVENT_MAPPING_TTL_SECONDS


@pytest.mark.asyncio
async def test_two_events_same_source_get_distinct_mappings(
    mocker, integration, deliver_config, provider_info, metadata
):
    """Two events sharing an external_source_id but with distinct gundi_ids must
    map to distinct keys — otherwise an EventUpdate routes to the wrong (latest)
    event. Regression for keying the mapping by source instead of by gundi_id."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    inner.post_event = AsyncMock(side_effect=[{"messageId": 111}, {"messageId": 222}])
    state = _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    def _event(gundi_id):
        return Event(
            gundi_id=gundi_id,
            source_id=uuid.uuid4(),
            external_source_id="rhino_carcass",  # SAME source for both events
            recorded_at=datetime.now(tz=timezone.utc),
            event_type="rhino_carcass",
            title="Rhino Carcass",
            location=Location(lat=0.0, lon=0.0),
        )

    for gid in ("gid-A", "gid-B"):
        await action_deliver(
            integration, deliver_config,
            GundiDelivery(payload=_event(gid), provider=provider_info), metadata,
        )

    keys = {c.kwargs["source_id"]: c.kwargs["state"]["cmore_message_id"]
            for c in state.set_state.call_args_list}
    # Distinct gundi_id keys (would have collided on the shared external_source_id).
    assert keys == {"gid-A": 111, "gid-B": 222}


def test_extract_message_id_coerces_to_int():
    """CMORE post_event responses get normalized to int messageId or None."""
    from app.actions.handlers import _extract_message_id

    assert _extract_message_id({"messageId": 14697}) == 14697
    # String-typed numeric messageIds coerce cleanly.
    assert _extract_message_id({"messageId": "14697"}) == 14697
    # Non-integer values return None (and log; not asserted here) rather than
    # storing garbage that would crash later at CmoreComment(rootMessageId=...).
    assert _extract_message_id({"messageId": "not-a-number"}) is None
    assert _extract_message_id({"messageId": None}) is None
    # Missing key, non-dict input.
    assert _extract_message_id({}) is None
    assert _extract_message_id(None) is None
    assert _extract_message_id("not-a-dict") is None
    # The pre-PR 'or response.get("id")' fallback is intentionally gone —
    # CMORE's documented response is `messageId`, full stop.
    assert _extract_message_id({"id": 12345}) is None


@pytest.mark.asyncio
async def test_deliver_drops_attachment(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    activity_log = _patch_activity_logger(mocker)

    att = Attachment(
        source_id=uuid.uuid4(),
        external_source_id="x",
        file_path="/tmp/photo.jpg",
    )
    delivery = GundiDelivery(payload=att, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_locations.assert_not_awaited()
    inner.post_event.assert_not_awaited()
    assert result["dropped"] is True
    assert result["payload_type"] == "Attachment"


@pytest.mark.asyncio
async def test_deliver_drops_text_message(
    mocker, integration, deliver_config, provider_info, text_message, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    activity_log = _patch_activity_logger(mocker)

    delivery = GundiDelivery(payload=text_message, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_locations.assert_not_awaited()
    inner.post_event.assert_not_awaited()
    assert result["payload_type"] == "TextMessage"


@pytest.mark.asyncio
async def test_transformations_applied_before_event_dispatch(
    mocker, integration, deliver_config, provider_info, event, metadata, fake_tag_info
):
    """A route_configuration rule that rewrites event_type must be visible to
    the event handler — tag mapping reads event.event_type."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=fake_tag_info)

    # Start with an event_type the deliver_config map DOES NOT know.
    event.event_type = "raw_species_value"
    # RouteConfiguration rewrites event_type from event_details.species to
    # "lion_sighting", which IS in deliver_config.event_type_to_tag → triggers
    # the Wildlife Sighting tag mapping.
    route_config = RouteConfiguration(
        id=uuid.uuid4(),
        name="species → event_type",
        data={
            "field_mappings": {
                provider_info.provider_id: {
                    "ev": {
                        str(integration.id): {
                            "default": "fallback",
                            "provider_field": "event_details__species",
                            "destination_field": "event_type",
                            "map": {"lion": "lion_sighting"},
                        }
                    }
                }
            }
        },
    )
    delivery = GundiDelivery(
        payload=event,
        route_configuration=route_config,
        provider=provider_info,
    )

    await action_deliver(integration, deliver_config, delivery, metadata)

    posted = inner.post_event.await_args[0][0]
    assert posted.tags is not None
    assert posted.tags[0].tagId == 42  # lion → lion_sighting → Wildlife Sighting tag (id 42 in fake_tag_info)


@pytest.mark.asyncio
async def test_deliver_creates_gnode_with_mapped_affiliation_and_classification(
    mocker, integration, provider_info, observation, metadata
):
    from app.actions.configurations import (
        DeliverConfig,
        SubjectAffiliationMapping,
        SubjectClassificationMapping,
    )
    from app.actions.handlers import action_deliver
    from app.datasource.schemas import Affiliation, CmoreClassification

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)  # no cached client_id → GNode gets created
    _patch_activity_logger(mocker)

    config = DeliverConfig(
        subject_type_to_affiliation=[
            SubjectAffiliationMapping(subject_type="elephant", affiliation=Affiliation.NEUTRAL),
        ],
        subject_type_to_classification=[
            SubjectClassificationMapping(
                subject_type="elephant",
                battleDimension="LAND",
                force="NONMILITARY",
            ),
        ],
    )
    delivery = GundiDelivery(payload=observation, provider=provider_info)
    await action_deliver(integration, config, delivery, metadata)

    inner.create_gnodes.assert_awaited_once()
    request = inner.create_gnodes.await_args[0][0][0]
    assert request.callsign == "Collar 42"
    assert request.targetId == "device-42"
    assert request.trackSourceType == provider_info.provider_type
    assert request.affiliation == Affiliation.NEUTRAL
    assert request.classification.battleDimension == "LAND"
    assert request.classification.force == "NONMILITARY"


@pytest.mark.asyncio
async def test_deliver_subject_subtype_takes_precedence_over_subject_type(
    mocker, integration, provider_info, observation, metadata
):
    from app.actions.configurations import DeliverConfig
    from app.actions.handlers import action_deliver
    from app.datasource.schemas import Affiliation

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    from app.actions.configurations import SubjectAffiliationMapping

    observation.additional = {"subject_subtype": "ranger"}
    config = DeliverConfig(
        subject_type_to_affiliation=[
            SubjectAffiliationMapping(subject_type="ranger", affiliation=Affiliation.FRIENDLY),
            SubjectAffiliationMapping(subject_type="elephant", affiliation=Affiliation.NEUTRAL),
        ],
    )
    delivery = GundiDelivery(payload=observation, provider=provider_info)
    await action_deliver(integration, config, delivery, metadata)

    request = inner.create_gnodes.await_args[0][0][0]
    assert request.affiliation == Affiliation.FRIENDLY


@pytest.mark.asyncio
async def test_deliver_uses_default_affiliation_when_subject_unmapped(
    mocker, integration, provider_info, observation, metadata
):
    from app.actions.configurations import DeliverConfig
    from app.actions.handlers import action_deliver
    from app.datasource.schemas import Affiliation

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    from app.actions.configurations import SubjectAffiliationMapping

    config = DeliverConfig(
        default_affiliation=Affiliation.FRIENDLY,
        subject_type_to_affiliation=[
            SubjectAffiliationMapping(subject_type="lion", affiliation=Affiliation.NEUTRAL),
        ],
    )
    delivery = GundiDelivery(payload=observation, provider=provider_info)
    await action_deliver(integration, config, delivery, metadata)

    request = inner.create_gnodes.await_args[0][0][0]
    assert request.affiliation == Affiliation.FRIENDLY
    assert request.classification is None


@pytest.mark.asyncio
async def test_deliver_handles_empty_route_configuration(
    mocker, integration, deliver_config, provider_info, observation, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker, existing_client_id=8888)
    _patch_activity_logger(mocker)

    delivery = GundiDelivery(
        payload=observation,
        route_configuration=None,
        provider=provider_info,
    )
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    assert result["locations_posted"] == 1
    inner.post_locations.assert_awaited_once()


# ---------------------------------------------------------------------------
# Provider deep-link: the source_event_url is surfaced ONLY as a comment on
# the CMORE event (see _push_event), never in the event title/description.
# ---------------------------------------------------------------------------

from app.actions.handlers import _build_event_description


def test_build_event_description_excludes_url_even_with_provider_metadata():
    """The deep-link must NOT appear in the description — it is posted as a
    comment instead. The description is just the event title."""
    url = "https://gundi-er.pamdas.org/events/907a54b9-808b-45a6-919c-b6dd204c32c6"
    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        provider_metadata={"source_event_url": url},
    )
    body = _build_event_description(e)
    assert body == "Coyote Carcass"
    assert url not in body


def test_build_event_description_falls_back_to_title_when_no_provider_metadata():
    """No provider_metadata → just the title, same as before this feature."""
    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
    )
    assert _build_event_description(e) == "Coyote Carcass"


def test_build_event_description_falls_back_to_event_type_when_titleless():
    """Backward-compat: no title and no URL → fall back to event_type slug
    (which the ER runner populates with the EventType display name when
    possible, via the PR #16 title fallback)."""
    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        event_type="coyote_carcass",
    )
    assert _build_event_description(e) == "coyote_carcass"


def test_build_event_description_handles_provider_metadata_without_source_url():
    """provider_metadata dict present but missing the expected key → no link."""
    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        provider_metadata={"some_other_key": "value"},
    )
    assert _build_event_description(e) == "Coyote Carcass"


@pytest.mark.asyncio
async def test_deliver_event_keeps_deep_link_out_of_cmore_post_description(
    mocker, integration, deliver_config, provider_info, metadata
):
    """End-to-end: Event with provider_metadata reaches _push_event and the
    CMORE post description carries only the title — the deep-link is kept out
    of the description (it is posted as a comment instead)."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        location=Location(lat=0.0, lon=0.0),
        provider_metadata={
            "source_event_url": "https://gundi-er.pamdas.org/events/abc"
        },
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    sent = inner.post_event.call_args.args[0]
    assert sent.description == "Coyote Carcass"
    assert "https://gundi-er.pamdas.org/events/abc" not in sent.description


@pytest.mark.asyncio
async def test_deliver_event_also_posts_deep_link_as_comment(
    mocker, integration, deliver_config, provider_info, metadata
):
    """The deep-link is posted as a comment on the new CMORE event — this is
    the sole place the source URL is rendered (it is kept out of the
    description), so it surfaces in CMORE's event detail view."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker, post_event_return={"messageId": 99999})
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        location=Location(lat=0.0, lon=0.0),
        provider_metadata={
            "source_event_url": "https://gundi-er.pamdas.org/events/abc"
        },
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_comment.assert_awaited_once()
    sent_comment = inner.post_comment.call_args.args[0]
    assert sent_comment.rootMessageId == 99999
    assert "https://gundi-er.pamdas.org/events/abc" in sent_comment.description
    assert sent_comment.description.startswith("Source:")


def test_build_event_description_appends_serial_number():
    """The source event's serial number is appended to the title."""
    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Rhino Carcass",
        provider_metadata={"serial_number": 267},
    )
    assert _build_event_description(e) == "Rhino Carcass (#267)"


@pytest.mark.asyncio
async def test_deliver_event_comment_includes_serial_and_deep_link(
    mocker, integration, deliver_config, provider_info, metadata
):
    """The source comment carries both the ER serial number and the deep-link,
    and the serial is appended to the posted event's title."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker, post_event_return={"messageId": 99999})
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Rhino Carcass",
        location=Location(lat=0.0, lon=0.0),
        provider_metadata={
            "serial_number": 267,
            "source_event_url": "https://gundi-er.pamdas.org/events/abc",
        },
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    # Serial in the posted event's title.
    posted = inner.post_event.call_args.args[0]
    assert posted.description == "Rhino Carcass (#267)"

    # Serial + deep-link in the comment.
    sent_comment = inner.post_comment.call_args.args[0]
    assert "#267" in sent_comment.description
    assert "https://gundi-er.pamdas.org/events/abc" in sent_comment.description


@pytest.mark.asyncio
async def test_deliver_event_comment_serial_only_when_no_deep_link(
    mocker, integration, deliver_config, provider_info, metadata
):
    """A serial with no deep-link still posts a comment (with just the serial)."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker, post_event_return={"messageId": 99999})
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Rhino Carcass",
        location=Location(lat=0.0, lon=0.0),
        provider_metadata={"serial_number": 267},
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_comment.assert_awaited_once()
    sent_comment = inner.post_comment.call_args.args[0]
    assert "#267" in sent_comment.description
    assert "Source:" not in sent_comment.description


@pytest.mark.asyncio
async def test_deliver_event_skips_deep_link_comment_when_no_url(
    mocker, integration, deliver_config, provider_info, metadata
):
    """Events without provider_metadata don't get a redundant comment."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        location=Location(lat=0.0, lon=0.0),
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    inner.post_comment.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_event_skips_deep_link_comment_when_no_message_id(
    mocker, integration, deliver_config, provider_info, metadata
):
    """If CMORE returns a response without messageId, skip the comment (can't
    target it). The URL is not rendered anywhere in that case."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker, post_event_return={"status": "ok-but-weird"})
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    e = Event(
        source_id=uuid.uuid4(),
        external_source_id="er-uuid",
        recorded_at=datetime.now(tz=timezone.utc),
        title="Coyote Carcass",
        location=Location(lat=0.0, lon=0.0),
        provider_metadata={
            "source_event_url": "https://gundi-er.pamdas.org/events/abc"
        },
    )
    delivery = GundiDelivery(payload=e, provider=provider_info)
    await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    inner.post_comment.assert_not_awaited()
