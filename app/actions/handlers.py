import hashlib
import json
import logging
import traceback
from datetime import datetime
from typing import Optional

from gundi_core import schemas
from gundi_core.events import GundiDelivery
from gundi_core.schemas.v2 import Integration, LogLevel
from gundi_client_v2.transformations import apply_transformations

from app.datasource.client import CmoreClient
from app.datasource.schemas import (
    CmoreClassification,
    CmoreComment,
    CmoreEvent,
    CmoreEventTag,
    CmoreLocation,
    CmoreProperty,
    CmoreTagValue,
    CmoreVirtualClientRequest,
    UploadType,
)
from app.datasource.tag_index import tag_index
from app.services.activity_logger import activity_logger, log_action_activity
from app.services.state import IntegrationStateManager
from .configurations import AuthenticateConfig, CmoreTagMapping, DeliverConfig

logger = logging.getLogger(__name__)

TRACK_SOURCE = "Gundi"

# TTL for the per-event external_source_id → cmore_message_id mapping. Long
# enough that any realistic edit window (notes, status flips) lands while we
# can still find the target CMORE event; short enough that the keyspace
# eventually prunes itself for events that nobody touches again.
CMORE_EVENT_MAPPING_TTL_SECONDS = 90 * 24 * 60 * 60  # 90 days

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


async def _resolve_client_id(
    client: CmoreClient,
    integration_id: str,
    subject_key: str,
    request: CmoreVirtualClientRequest,
) -> tuple:
    """Return (client_id, was_created) for a subject, looking up or creating a GNode in Cmore."""
    state = await state_manager.get_state(
        integration_id=integration_id,
        action_id="deliver",
        source_id=subject_key,
    )
    if state.get("client_id"):
        return state["client_id"], False

    # Recover from state loss: check if the GNode already exists in C-more.
    mappings = await client.get_gateway_mapping()
    for mapping in mappings:
        if mapping.trackSource == request.trackSource and mapping.trackNo == request.trackNo:
            await state_manager.set_state(
                integration_id=integration_id,
                action_id="deliver",
                state={"client_id": mapping.clientId},
                source_id=subject_key,
            )
            logger.info(f"Recovered GNode clientId={mapping.clientId} for subject '{subject_key}' from gateway_mapping.")
            return mapping.clientId, False

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


def _find_subject_mapping(observation: schemas.v2.Observation, mappings):
    """Return the first mapping whose subject_type matches the observation.

    Matches against subject_subtype first, then subject_type. Returns the
    mapping object itself (not a value); call sites pick the relevant
    attribute (`.affiliation`, `.classification`, etc.).
    """
    if not mappings:
        return None
    additional = observation.additional or {}
    for cand in (additional.get("subject_subtype"), observation.subject_type):
        if not cand:
            continue
        for m in mappings:
            if m.subject_type == cand:
                return m
    return None


def _gnode_request_for(
    observation: schemas.v2.Observation,
    action_config: DeliverConfig,
    subject_key: str,
    track_source_type: str,
) -> CmoreVirtualClientRequest:
    aff_mapping = _find_subject_mapping(
        observation, action_config.subject_type_to_affiliation
    )
    affiliation = (
        aff_mapping.affiliation if aff_mapping else action_config.default_affiliation
    )
    cls_mapping = _find_subject_mapping(
        observation, action_config.subject_type_to_classification
    )
    classification = (
        CmoreClassification(
            battleDimension=cls_mapping.battleDimension,
            force=cls_mapping.force,
            type=cls_mapping.type,
            role=cls_mapping.role,
        )
        if cls_mapping
        else None
    )
    return CmoreVirtualClientRequest(
        trackSource=TRACK_SOURCE,
        trackNo=_track_no_for(subject_key),
        # Callsign is the display name in the portal; targetId identifies the device/unit.
        callsign=observation.source_name or observation.external_source_id,
        targetId=observation.external_source_id,
        trackSourceType=track_source_type,
        affiliation=affiliation,
        classification=classification,
    )


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
    action_config: DeliverConfig,
    observation: schemas.v2.Observation,
    metadata: dict,
    track_source_type: str,
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

    request = _gnode_request_for(observation, action_config, subject_key, track_source_type)

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        try:
            client_id, was_created = await _resolve_client_id(
                client, integration_id, subject_key, request
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


def _stringify_for_cmore(value) -> Optional[str]:
    """Coerce a Python value into the text form C-more's field model expects.

    C-more field values are all strings. None signals "skip this field
    entirely" (don't send an empty value).
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    # bool subclasses int, so check bool first.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)
    return str(value)


async def _build_event_tag(
    client: CmoreClient,
    base_url: str,
    integration_id: str,
    mapping: CmoreTagMapping,
    event: schemas.v2.Event,
) -> Optional[CmoreEventTag]:
    """Resolve mapping + event_details into a CmoreEventTag.

    Returns None if the configured tag is not found on the C-more instance —
    the event still gets posted (with description + location), just without
    the structured tag.
    """
    tag_info = await tag_index.get(client, base_url, integration_id, mapping.tag_name)
    if tag_info is None:
        logger.warning(
            "CMORE tag %r not found on instance; dropping tag from event "
            "(event_type=%r). Event will still post with description/location only.",
            mapping.tag_name,
            event.event_type,
        )
        return None

    # The C-more events endpoint accepts tags whose typeLimiter is Message or
    # Incident. Resource-typed tags belong to a different entity model.
    if tag_info.type_limiter not in ("Message", "Incident"):
        logger.warning(
            "CMORE tag %r has typeLimiter=%r which is not Message/Incident; "
            "C-more may reject the event.",
            tag_info.name,
            tag_info.type_limiter,
        )

    details = event.event_details or {}
    values = []
    for fm in (mapping.field_mappings or []):
        ed_key = fm.event_details_key
        field_name = fm.cmore_field_name
        field_info = tag_info.field_by_name(field_name)
        if field_info is None:
            logger.warning(
                "CMORE tag %r has no field %r; skipping event_details key %r.",
                tag_info.name,
                field_name,
                ed_key,
            )
            continue
        raw_value = details.get(ed_key)
        stringified = _stringify_for_cmore(raw_value)
        if stringified is None:
            continue
        values.append(CmoreTagValue(fieldId=field_info.id, value=stringified))

    logger.info(
        "Attached CMORE tag %r (tagId=%d, typeLimiter=%s) with %d field value(s) "
        "to event (event_type=%r).",
        tag_info.name,
        tag_info.id,
        tag_info.type_limiter,
        len(values),
        event.event_type,
    )
    return CmoreEventTag(tagId=tag_info.id, values=values)


def _get_source_event_url(event: schemas.v2.Event) -> Optional[str]:
    """Pull the provider deep-link out of event.provider_metadata.

    Returns ``None`` if the field is missing, the dict is malformed, or the
    URL is blank. Callers should treat ``None`` as "no deep-link to render."
    """
    pm = event.provider_metadata or {}
    if not isinstance(pm, dict):
        return None
    url = pm.get("source_event_url")
    return url if isinstance(url, str) and url else None


def _build_event_description(event: schemas.v2.Event) -> str:
    """Render the CMORE event description.

    Just the event title. The source deep-link is NOT included here — it is
    posted as a comment immediately after ``post_event`` (see ``_push_event``),
    which keeps the title clean while still surfacing the cross-reference in
    CMORE's detail view.

    Title fallback chain: title → event_type slug → "Gundi Event".
    """
    return event.title or event.event_type or "Gundi Event"


async def _push_event(
    integration: Integration,
    action_config: DeliverConfig,
    event: schemas.v2.Event,
):
    auth = _get_auth_config(integration)

    # Diagnostic: log what we received from Gundi so missing/empty
    # provider_metadata can be traced upstream (cdip-routing's gundi-core
    # version must include the field for it to flow through here).
    logger.info(
        "_push_event received: external_source_id=%r event_type=%r title=%r "
        "provider_metadata=%r",
        event.external_source_id,
        event.event_type,
        event.title,
        event.provider_metadata,
    )

    mapping = None
    if action_config.event_type_to_tag and event.event_type:
        mapping = next(
            (m for m in action_config.event_type_to_tag if m.event_type == event.event_type),
            None,
        )

    if action_config.event_type_to_tag is None:
        logger.info(
            "DeliverConfig.event_type_to_tag is None; posting event (event_type=%r) "
            "with no tag.",
            event.event_type,
        )
    elif mapping is None:
        known = [m.event_type for m in action_config.event_type_to_tag]
        logger.info(
            "No event_type_to_tag mapping for event_type=%r; posting event with "
            "no tag. Known event_types: %s",
            event.event_type,
            known,
        )

    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        tags = None
        if mapping is not None:
            tag = await _build_event_tag(
                client, auth.base_url, str(integration.id), mapping, event
            )
            if tag is not None:
                tags = [tag]

        location = event.location
        cmore_event = CmoreEvent(
            description=_build_event_description(event),
            latitude=location.lat if location else None,
            longitude=location.lon if location else None,
            dateOccurred=event.recorded_at,
            uploadType=UploadType.GENERATED,
            ownerGroupId=auth.owner_group_id,
            tags=tags,
        )
        # Diagnostic: log the outbound description verbatim so it can be
        # cross-checked against what shows up in CMORE's UI.
        logger.info(
            "Posting CMORE event: description=%r",
            cmore_event.description,
        )
        response = await client.post_event(cmore_event)
        logger.info(
            "Posted CMORE event (event_type=%r, has_tag=%s): cmore_response=%r",
            event.event_type,
            tags is not None,
            response,
        )
        cmore_message_id = _extract_message_id(response)

        # Post the source deep-link URL as a comment so it surfaces in CMORE's
        # event detail view. This is the sole place the link is rendered — it
        # is intentionally kept out of the event title/description. Only when
        # both the URL was attached upstream AND we got a messageId back.
        source_event_url = _get_source_event_url(event)
        if source_event_url and cmore_message_id is not None:
            comment_body = f"Source: {source_event_url}"
            # Diagnostic: log the outbound comment body so it can be
            # cross-checked against what shows up in CMORE's UI.
            logger.info(
                "Posting CMORE deep-link comment: description=%r root_message_id=%s",
                comment_body,
                cmore_message_id,
            )
            await client.post_comment(CmoreComment(
                description=comment_body,
                rootMessageId=cmore_message_id,
                uploadType=UploadType.GENERATED,
            ))
            logger.info(
                "Posted CMORE deep-link comment (root_message_id=%s).",
                cmore_message_id,
            )
        else:
            # Explicit "we didn't post a comment" log so an absent URL is
            # distinguishable from a posted-but-invisible URL.
            logger.info(
                "Skipping CMORE deep-link comment: source_event_url=%r "
                "cmore_message_id=%r",
                source_event_url,
                cmore_message_id,
            )

    # Persist the source→messageId mapping so a subsequent EventUpdate for the
    # same source event can be hung off this CMORE event as a comment
    # (GUNDI-5386). Keyed by the provider's external_source_id (e.g. the ER
    # event UUID) — Gundi propagates that same string on both Event and
    # EventUpdate payloads.
    if event.external_source_id and cmore_message_id is not None:
        await state_manager.set_state(
            integration_id=str(integration.id),
            action_id="deliver",
            source_id=event.external_source_id,
            state={"cmore_message_id": cmore_message_id},
            ttl_seconds=CMORE_EVENT_MAPPING_TTL_SECONDS,
        )

    return {"event_posted": True, "cmore_response": response}


def _extract_message_id(post_event_response):
    """Pull the CMORE messageId out of a post_event response.

    CMORE returns ``{"messageId": <int>, ...}`` on success. Coerces to int
    here so callers can rely on an integer (or None) — guards against
    string-typed messageIds slipping into Redis state and crashing
    downstream CmoreComment construction with a ValueError.
    """
    if not isinstance(post_event_response, dict):
        return None
    raw = post_event_response.get("messageId")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.error(
            "CMORE post_event response had non-integer messageId=%r; "
            "skipping external_source_id → cmore_message_id mapping write.",
            raw,
        )
        return None


def _format_event_update_comment(changes: dict) -> Optional[str]:
    """Render a Gundi EventUpdate.changes dict as CMORE comment text.

    Per GUNDI-5386 the upstream ER runner emits one update_event per change,
    so a given changes dict carries exactly one of these keys:

    - ``notes``: list of ER note dicts. One note per emission.
    - ``status`` / ``priority`` / ``title``: the new field value.

    Returns ``None`` if the changes dict has no recognised field — caller
    should treat as a no-op (and log).
    """
    if not isinstance(changes, dict):
        return None

    notes = changes.get("notes")
    if notes and isinstance(notes, list):
        note = notes[0] or {}
        text = (note.get("text") or "").strip()
        # ER notes don't carry a top-level "author" field directly; the
        # first entry in the note's ``updates`` history is the "Created"
        # event whose user is the author. Best-effort extraction.
        author = _extract_note_author(note)
        created_at = note.get("created_at") or ""
        if author and created_at:
            return f"{author} ({created_at}): {text}".strip()
        if author:
            return f"{author}: {text}".strip()
        if created_at:
            return f"{created_at}: {text}".strip()
        return text or None

    if "status" in changes:
        return f"Status changed to {changes['status']}"
    if "priority" in changes:
        return f"Priority changed to {changes['priority']}"
    if "title" in changes:
        return f"Title changed to '{changes['title']}'"
    return None


def _extract_note_author(note: dict) -> str:
    """Best-effort: find the author from an ER note's updates history."""
    updates = note.get("updates") or []
    for entry in updates:
        user = entry.get("user") or {}
        username = user.get("username")
        if username:
            return username
    return ""


async def _push_event_update_as_comment(
    integration: Integration,
    action_config: DeliverConfig,
    event_update: schemas.v2.EventUpdate,
):
    """Forward a Gundi EventUpdate to CMORE as a comment on the original event.

    Looks up the CMORE messageId we stored at ``_push_event`` time, formats
    the changes dict as comment text, and POSTs via ``CmoreClient.post_comment``.

    A missing mapping (e.g. CMORE never received the original Event because
    of a race or a backfill ordering issue) is logged as WARNING and dropped
    rather than crashing the handler.
    """
    external_source_id = event_update.external_source_id
    if not external_source_id:
        logger.warning(
            "EventUpdate without external_source_id; cannot route to a CMORE event. Dropping."
        )
        return {"dropped": True, "reason": "missing_external_source_id"}

    state = await state_manager.get_state(
        integration_id=str(integration.id),
        action_id="deliver",
        source_id=external_source_id,
    )
    cmore_message_id = state.get("cmore_message_id") if state else None
    if not cmore_message_id:
        await log_action_activity(
            integration_id=str(integration.id),
            action_id="deliver",
            title="CMORE event not yet seen — skipping update",
            level=LogLevel.WARNING,
            data={"external_source_id": external_source_id},
        )
        return {"dropped": True, "reason": "cmore_message_id_not_found"}

    comment_text = _format_event_update_comment(event_update.changes or {})
    if not comment_text:
        logger.info(
            "EventUpdate.changes (%r) produced no recognised comment text; skipping.",
            event_update.changes,
        )
        return {"dropped": True, "reason": "unrecognised_changes"}

    auth = _get_auth_config(integration)
    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        cmore_comment = CmoreComment(
            description=comment_text,
            rootMessageId=cmore_message_id,
            uploadType=UploadType.GENERATED,
        )
        response = await client.post_comment(cmore_comment)
        logger.info(
            "Posted CMORE comment (root_message_id=%s, len=%d): cmore_response=%r",
            cmore_message_id,
            len(comment_text),
            response,
        )

    return {"comment_posted": True, "cmore_message_id": cmore_message_id, "cmore_response": response}


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
        # trackSourceType identifies the equipment/source system in C-more
        # (e.g. "telonics"); the provider type slug is the closest Gundi analogue.
        return await _push_observation(
            integration,
            action_config,
            payload,
            metadata,
            track_source_type=data.provider.provider_type,
        )

    if isinstance(payload, schemas.v2.Event):
        return await _push_event(integration, action_config, payload)

    if isinstance(payload, schemas.v2.EventUpdate):
        # ER → CMORE comment forwarding (GUNDI-5386). One EventUpdate from
        # the ER runner per logical change; one CMORE comment per EventUpdate.
        return await _push_event_update_as_comment(integration, action_config, payload)

    # Graceful drop for Attachment, TextMessage (no CMORE analogue yet).
    payload_type = type(payload).__name__
    await log_action_activity(
        integration_id=str(integration.id),
        action_id="deliver",
        title=f"C-more does not handle {payload_type}; dropping.",
        level=LogLevel.INFO,
        data={**metadata, "payload_type": payload_type},
    )
    return {"dropped": True, "payload_type": payload_type}
