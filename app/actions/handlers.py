import hashlib
import logging
import traceback

from gundi_core.schemas.v2 import Integration, LogLevel
from gundi_core.events.transformers import EventTransformedER, ObservationTransformedER

from app.datasource.client import CmoreClient
from app.datasource.schemas import (
    CmoreEvent,
    CmoreEventTag,
    CmoreLocation,
    CmoreProperty,
    CmoreVirtualClientRequest,
    UploadType,
)
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.state import IntegrationStateManager
from .configurations import AuthenticateConfig, PushEventsConfig, PushObservationsConfig

logger = logging.getLogger(__name__)

TRACK_SOURCE = "Gundi"

state_manager = IntegrationStateManager()


def _get_auth_config(integration: Integration) -> AuthenticateConfig:
    auth_config = integration.get_action_config("auth")
    if not auth_config:
        raise ValueError("Authentication configuration (auth) is required.")
    return AuthenticateConfig.parse_obj(auth_config.data)


def _track_no_for(subject_key: str) -> int:
    # Stable across process restarts (Python's built-in hash is salted per-process).
    digest = hashlib.md5(subject_key.encode("utf-8")).hexdigest()
    return int(digest[:14], 16)  # 56 bits, fits comfortably in C-more's 64-bit signed int


async def action_auth(integration: Integration, action_config: AuthenticateConfig):
    token = action_config.token.get_secret_value()
    if not token or not action_config.base_url:
        return {"valid_credentials": False, "error": "base_url and token are required."}
    try:
        async with CmoreClient(base_url=action_config.base_url, token=token) as client:
            await client.get_tags()
    except Exception as e:
        return {"valid_credentials": False, "error": f"{type(e).__name__}: {e}"}
    return {"valid_credentials": True}


async def _resolve_client_id(client: CmoreClient, integration_id: str, subject_key: str, callsign: str) -> tuple:
    """Return (client_id, was_created) for a subject, looking up or creating a GNode in Cmore."""
    state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="push_observations",
        source_id=subject_key,
    )
    if state.get("client_id"):
        return state["client_id"], False

    track_no = _track_no_for(subject_key)

    # Recover from state loss: check if the GNode already exists in C-more.
    mappings = await client.get_gateway_mapping()
    for mapping in mappings:
        if mapping.trackSource == TRACK_SOURCE and mapping.trackNo == track_no:
            await state_manager.set_state(
                integration_id=integration_id,
                action_id="push_observations",
                state={"client_id": mapping.clientId},
                source_id=subject_key,
            )
            logger.info(f"Recovered GNode clientId={mapping.clientId} for subject '{subject_key}' from gateway_mapping.")
            return mapping.clientId, False

    request = CmoreVirtualClientRequest(
        trackSource=TRACK_SOURCE,
        trackNo=track_no,
        callsign=callsign,
    )
    gnodes = await client.create_gnodes([request])
    client_id = gnodes[0].clientId
    await state_manager.set_state(
        integration_id=integration_id,
        action_id="push_observations",
        state={"client_id": client_id},
        source_id=subject_key,
    )
    logger.info(f"Created GNode clientId={client_id} for subject '{subject_key}'.")
    return client_id, True


def _subject_properties(client_id: int, observation) -> list:
    properties = []
    for name, value in [
        ("subject_name", observation.subject_name),
        ("subject_type", observation.subject_type),
        ("subject_subtype", observation.subject_subtype),
        ("manufacturer_id", observation.manufacturer_id),
    ]:
        if value:
            properties.append(CmoreProperty(clientId=client_id, name=name, value=str(value)))
    return properties


@activity_logger()
async def action_push_observations(
    integration: Integration,
    action_config: PushObservationsConfig,
    data: ObservationTransformedER,
    metadata: dict,
):
    auth = _get_auth_config(integration)
    observation = data.payload
    integration_id = str(integration.id)

    # manufacturer_id is the stable identifier (subject_name can change). Fall back to subject_name.
    subject_key = observation.manufacturer_id or observation.subject_name
    if not subject_key:
        raise ValueError("Observation has no manufacturer_id or subject_name; cannot map to a C-more GNode.")

    # Visible label in C-more — prefer the human-friendly name.
    callsign = observation.subject_name or observation.manufacturer_id

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        try:
            client_id, was_created = await _resolve_client_id(client, integration_id, subject_key, callsign)
        except Exception as e:
            await log_action_activity(
                integration_id=integration_id,
                action_id="push_observations",
                title=f"Failed to resolve GNode for subject '{subject_key}'",
                level=LogLevel.ERROR,
                data={"error": f"{type(e).__name__}: {e}", "error_traceback": traceback.format_exc(), **metadata},
            )
            raise

        # Push subject metadata as Cmore properties on first creation.
        if was_created:
            properties = _subject_properties(client_id, observation)
            if properties:
                await client.post_properties(properties)

        if not observation.location:
            logger.warning(f"Observation for '{subject_key}' has no location; skipping.")
            return {"locations_posted": 0, "subject": subject_key, "client_id": client_id}

        location = CmoreLocation(
            clientId=client_id,
            latitude=observation.location.lat,
            longitude=observation.location.lon,
            timestamp=observation.recorded_at,
            source="GPS",
        )
        await client.post_locations([location])

    return {"locations_posted": 1, "subject": subject_key, "client_id": client_id}


@activity_logger()
async def action_push_events(
    integration: Integration,
    action_config: PushEventsConfig,
    data: EventTransformedER,
    metadata: dict,
):
    auth = _get_auth_config(integration)
    event = data.payload

    tags = None
    if action_config.event_type_to_tag_id and event.event_type:
        tag_id = action_config.event_type_to_tag_id.get(event.event_type)
        if tag_id is not None:
            tags = [CmoreEventTag(tagId=tag_id)]

    location = event.location
    cmore_event = CmoreEvent(
        description=event.title or event.event_type or "EarthRanger Event",
        latitude=location.latitude if location else None,
        longitude=location.longitude if location else None,
        dateOccurred=event.time,
        uploadType=UploadType.GENERATED,
        ownerGroupId=auth.owner_group_id,
        tags=tags,
    )

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        response = await client.post_event(cmore_event)

    return {"event_posted": True, "cmore_response": response}
