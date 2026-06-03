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
    from app.actions.configurations import DeliverConfig

    return DeliverConfig(event_type_to_tag_id={"lion_sighting": 42})


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


def _patch_cmore_client(mocker, post_locations_return=None, post_event_return=None):
    """Patch the CmoreClient async-context-manager and capture method calls."""
    inner = MagicMock()
    inner.post_locations = AsyncMock(return_value=post_locations_return or {"status": "ok"})
    inner.post_event = AsyncMock(return_value=post_event_return or {"id": 123})
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
    mocker, integration, deliver_config, provider_info, event, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    delivery = GundiDelivery(payload=event, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_event.assert_awaited_once()
    inner.post_locations.assert_not_awaited()
    assert result["event_posted"] is True
    posted = inner.post_event.await_args[0][0]
    # Lion sighting maps to tag 42 in deliver_config
    assert posted.tags is not None
    assert posted.tags[0].tagId == 42


@pytest.mark.asyncio
async def test_deliver_drops_event_update(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    activity_log = _patch_activity_logger(mocker)

    eu = EventUpdate(
        source_id=uuid.uuid4(),
        external_source_id="x",
        changes={"status": "resolved"},
    )
    delivery = GundiDelivery(payload=eu, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    inner.post_locations.assert_not_awaited()
    inner.post_event.assert_not_awaited()
    assert result == {"dropped": True, "payload_type": "EventUpdate"}
    activity_log.assert_awaited_once()


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
    mocker, integration, deliver_config, provider_info, event, metadata
):
    """A route_configuration rule that rewrites event_type must be visible to
    the event handler — tag mapping reads event.event_type."""
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)

    # Start with an event_type the deliver_config map DOES NOT know.
    event.event_type = "raw_species_value"
    # RouteConfiguration rewrites event_type from event_details.species to a
    # mapped value that DeliverConfig.event_type_to_tag_id (map: lion_sighting -> 42)
    # then translates into a tag.
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
    assert posted.tags[0].tagId == 42  # lion → lion_sighting → 42


@pytest.mark.asyncio
async def test_deliver_creates_gnode_with_mapped_affiliation_and_classification(
    mocker, integration, provider_info, observation, metadata
):
    from app.actions.configurations import DeliverConfig
    from app.actions.handlers import action_deliver
    from app.datasource.schemas import Affiliation, CmoreClassification

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)  # no cached client_id → GNode gets created
    _patch_activity_logger(mocker)

    config = DeliverConfig(
        subject_type_to_affiliation={"elephant": Affiliation.NEUTRAL},
        subject_type_to_classification={
            "elephant": CmoreClassification(battleDimension="LAND", force="NONMILITARY")
        },
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

    observation.additional = {"subject_subtype": "ranger"}
    config = DeliverConfig(
        subject_type_to_affiliation={
            "ranger": Affiliation.FRIENDLY,
            "elephant": Affiliation.NEUTRAL,
        },
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

    config = DeliverConfig(
        default_affiliation=Affiliation.FRIENDLY,
        subject_type_to_affiliation={"lion": Affiliation.NEUTRAL},
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
