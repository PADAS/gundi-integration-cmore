import hashlib
import logging
import traceback

from gundi_core import schemas
from gundi_core.events import GundiDelivery
from gundi_core.schemas.v2 import Integration, LogLevel
from gundi_client_v2.transformations import apply_transformations

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
from .configurations import AuthenticateConfig, DeliverConfig

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
    # Not cryptographic — sha256 is just used as a deterministic, well-distributed
    # source of bits for the trackNo identifier.
    digest = hashlib.sha256(subject_key.encode("utf-8")).hexdigest()
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
        action_id="deliver",
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
                action_id="deliver",
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
    if not gnodes:
        raise ValueError(
            f"C-more create_gnodes returned no results for subject '{subject_key}'."
        )
    gnode = gnodes[0]
    if gnode.error:
        raise ValueError(
            f"C-more create_gnodes failed for subject '{subject_key}': {gnode.error}"
        )
    if not gnode.clientId:
        raise ValueError(
            f"C-more create_gnodes returned no clientId for subject '{subject_key}'."
        )
    client_id = gnode.clientId
    await state_manager.set_state(
        integration_id=integration_id,
        action_id="deliver",
        state={"client_id": client_id},
        source_id=subject_key,
    )
    logger.info(f"Created GNode clientId={client_id} for subject '{subject_key}'.")
    return client_id, True


def _subject_properties(client_id: int, observation: schemas.v2.Observation) -> list:
    additional = observation.additional or {}
    candidates = [
        ("subject_name", observation.source_name),
        ("subject_type", observation.subject_type),
        ("subject_subtype", additional.get("subject_subtype")),
        ("manufacturer_id", observation.external_source_id),
    ]
    return [
        CmoreProperty(clientId=client_id, name=name, value=str(value))
        for name, value in candidates
        if value
    ]


async def _push_observation(
    integration: Integration,
    observation: schemas.v2.Observation,
    metadata: dict,
):
    auth = _get_auth_config(integration)
    integration_id = str(integration.id)

    # external_source_id is the stable subject identifier (source_name can change).
    # Fall back to source_name if the provider doesn't supply external_source_id.
    subject_key = observation.external_source_id or observation.source_name
    if not subject_key:
        raise ValueError(
            "Observation has no external_source_id or source_name; cannot map to a C-more GNode."
        )

    callsign = observation.source_name or observation.external_source_id

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        try:
            client_id, was_created = await _resolve_client_id(
                client, integration_id, subject_key, callsign
            )
        except Exception as e:
            await log_action_activity(
                integration_id=integration_id,
                action_id="deliver",
                title=f"Failed to resolve GNode for subject '{subject_key}'",
                level=LogLevel.ERROR,
                data={**metadata, "error": f"{type(e).__name__}: {e}", "error_traceback": traceback.format_exc()},
            )
            raise

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


async def _push_event(
    integration: Integration,
    action_config: DeliverConfig,
    event: schemas.v2.Event,
):
    auth = _get_auth_config(integration)

    tags = None
    if action_config.event_type_to_tag_id and event.event_type:
        tag_id = action_config.event_type_to_tag_id.get(event.event_type)
        if tag_id is not None:
            tags = [CmoreEventTag(tagId=tag_id)]

    location = event.location
    cmore_event = CmoreEvent(
        description=event.title or event.event_type or "Gundi Event",
        latitude=location.lat if location else None,
        longitude=location.lon if location else None,
        dateOccurred=event.recorded_at,
        uploadType=UploadType.GENERATED,
        ownerGroupId=auth.owner_group_id,
        tags=tags,
    )

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        response = await client.post_event(cmore_event)

    return {"event_posted": True, "cmore_response": response}


@activity_logger()
async def action_deliver(
    integration: Integration,
    action_config: DeliverConfig,
    data: GundiDelivery,
    metadata: dict,
):
    """Single handler for generic-model delivery to C-more.

    Receives any Gundi payload type via the GundiDelivery envelope, applies
    any RouteConfiguration transformations, then dispatches by payload type.
    Unsupported payload types are logged and dropped.
    """
    payload = apply_transformations(
        data.payload,
        data.route_configuration,
        provider_id=data.provider.provider_id,
        destination_id=str(integration.id),
    )

    if isinstance(payload, schemas.v2.Observation):
        return await _push_observation(integration, payload, metadata)

    if isinstance(payload, schemas.v2.Event):
        return await _push_event(integration, action_config, payload)

    # Graceful drop for EventUpdate, Attachment, TextMessage
    payload_type = type(payload).__name__
    await log_action_activity(
        integration_id=str(integration.id),
        action_id="deliver",
        title=f"C-more does not handle {payload_type}; dropping.",
        level=LogLevel.INFO,
        data={**metadata, "payload_type": payload_type},
    )
    return {"dropped": True, "payload_type": payload_type}
