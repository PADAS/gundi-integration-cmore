"""
CLI for interacting with the C-more API directly.

Usage:
    python -m app.datasource.cli --help

Auth options can also be set via environment variables:
    CMORE_BASE_URL, CMORE_TOKEN
"""
import asyncio
import json
from datetime import datetime, timezone

import click

from .client import CmoreClient
from .schemas import (
    Affiliation,
    CmoreEvent,
    CmoreLocation,
    CmoreProperty,
    CmoreVirtualClientRequest,
    TrackType,
    UploadType,
)

DEFAULT_BASE_URL = "https://cmorewc1.chpc.ac.za/za/WebAPI/api"


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting the 'Z' UTC suffix.

    Python 3.10's datetime.fromisoformat() doesn't accept a trailing 'Z',
    which is otherwise valid ISO-8601. Normalize it to '+00:00' first.
    """
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def run(coro):
    return asyncio.run(coro)


@click.group()
@click.option("--base-url", envvar="CMORE_BASE_URL", default=DEFAULT_BASE_URL, show_default=True, help="C-more API base URL.")
@click.option("--token", envvar="CMORE_TOKEN", default=None, help="C-more API token. Required for all commands except 'login'.")
@click.pass_context
def cli(ctx, base_url, token):
    """C-more API client CLI."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["token"] = token


def _require_token(ctx):
    if not ctx.obj.get("token"):
        raise click.UsageError("--token (or CMORE_TOKEN env var) is required for this command.")


@cli.command("login")
@click.option("--username", required=True, help="C-more username.")
@click.option("--password", required=True, prompt=True, hide_input=True, help="C-more password (prompted if omitted).")
@click.option(
    "--client-type",
    default="SoftwareClient",
    show_default=True,
    type=click.Choice(["BrowserClient", "MobileClient", "SoftwareClient", "SystemClient"], case_sensitive=False),
)
@click.option("--unique-id", default="gundi-cli", show_default=True, help="Unique client identifier (allows multiple sessions per user).")
@click.pass_context
def login(ctx, username, password, client_type, unique_id):
    """Exchange username/password for a security token + user info. Does not require --token."""
    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"]) as client:
            result = await client.login(
                username=username,
                password=password,
                client_type=client_type,
                unique_id=unique_id,
            )
        click.echo(json.dumps(result, indent=2))

    run(_run())


@cli.command("get-tags")
@click.pass_context
def get_tags(ctx):
    """Fetch all tag metadata from C-more."""
    _require_token(ctx)
    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.get_tags()
        click.echo(json.dumps(result, indent=2))

    run(_run())


@cli.command("get-classification-tree")
@click.pass_context
def get_classification_tree(ctx):
    """Fetch the valid classification options (battleDimension/force/type/role) for this instance."""
    _require_token(ctx)
    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.get_classification_tree()
        click.echo(json.dumps(result, indent=2))

    run(_run())


@cli.command("gateway-mapping")
@click.pass_context
def gateway_mapping(ctx):
    """Fetch existing trackSource/trackNo → clientId mappings for this token."""
    _require_token(ctx)
    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.get_gateway_mapping()
        click.echo(json.dumps([m.dict() for m in result], indent=2))

    run(_run())


@cli.command("create-gnode")
@click.option("--track-no", required=True, type=int, help="Unique 64-bit integer track number.")
@click.option("--track-source", required=True, help="Source system name (e.g. 'Gundi').")
@click.option(
    "--track-type",
    default=TrackType.OWN_TRACK.value,
    show_default=True,
    type=click.Choice([t.value for t in TrackType], case_sensitive=False),
)
@click.option("--callsign", default=None, help="Optional callsign/name for the GNode.")
@click.option("--target-id", default=None, help="Optional external target ID.")
@click.option(
    "--affiliation",
    default=Affiliation.UNKNOWN.value,
    show_default=True,
    type=click.Choice([a.value for a in Affiliation], case_sensitive=False),
)
@click.pass_context
def create_gnode(ctx, track_no, track_source, track_type, callsign, target_id, affiliation):
    """Create a new virtual GNode client in C-more."""
    _require_token(ctx)
    request = CmoreVirtualClientRequest(
        trackNo=track_no,
        trackSource=track_source,
        trackType=TrackType(track_type),
        callsign=callsign,
        targetId=target_id,
        affiliation=Affiliation(affiliation),
    )

    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.create_gnodes([request])
        click.echo(json.dumps([g.dict() for g in result], indent=2))

    run(_run())


@cli.command("post-location")
@click.option("--client-id", required=True, type=int, help="C-more GNode clientId.")
@click.option("--lat", required=True, type=float, help="Latitude.")
@click.option("--lon", required=True, type=float, help="Longitude.")
@click.option("--timestamp", default=None, help="ISO timestamp (defaults to now).")
@click.option("--altitude", default=None, type=float, help="Altitude in metres.")
@click.option("--accuracy", default=None, type=float, help="Accuracy in metres.")
@click.option("--heading", default=None, type=float, help="Heading in degrees.")
@click.option("--speed", default=None, type=float, help="Speed.")
@click.option("--source", default=None, help="Location source label (e.g. 'GPS').")
@click.pass_context
def post_location(ctx, client_id, lat, lon, timestamp, altitude, accuracy, heading, speed, source):
    """Post a GPS location for a GNode."""
    _require_token(ctx)
    ts = _parse_iso(timestamp) if timestamp else datetime.now(tz=timezone.utc)
    location = CmoreLocation(
        clientId=client_id,
        latitude=lat,
        longitude=lon,
        timestamp=ts,
        altitude=altitude,
        accuracy=accuracy,
        heading=heading,
        speed=speed,
        source=source,
    )

    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.post_locations([location])
        click.echo(json.dumps(result, indent=2))

    run(_run())


@cli.command("post-property")
@click.option("--client-id", required=True, type=int, help="C-more GNode clientId.")
@click.option("--name", required=True, help="Property name.")
@click.option("--value", required=True, help="Property value.")
@click.pass_context
def post_property(ctx, client_id, name, value):
    """Post a key/value property for a GNode."""
    _require_token(ctx)
    prop = CmoreProperty(clientId=client_id, name=name, value=value)

    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.post_properties([prop])
        click.echo(json.dumps(result, indent=2))

    run(_run())


@cli.command("post-event")
@click.option("--description", required=True, help="Event description.")
@click.option("--lat", default=None, type=float, help="Latitude.")
@click.option("--lon", default=None, type=float, help="Longitude.")
@click.option("--altitude", default=None, type=float, help="Altitude in metres.")
@click.option("--accuracy", default=None, type=float, help="Accuracy in metres.")
@click.option("--date-occurred", default=None, help="ISO timestamp of when the event occurred.")
@click.option("--owner-group-id", default=None, type=int, help="C-more ShareGroupId.")
@click.option(
    "--upload-type",
    default=UploadType.GENERATED.value,
    show_default=True,
    type=click.Choice([t.value for t in UploadType], case_sensitive=False),
)
@click.pass_context
def post_event(ctx, description, lat, lon, altitude, accuracy, date_occurred, owner_group_id, upload_type):
    """Create an event in C-more."""
    _require_token(ctx)
    event = CmoreEvent(
        description=description,
        latitude=lat,
        longitude=lon,
        altitude=altitude,
        accuracy=accuracy,
        dateOccurred=_parse_iso(date_occurred) if date_occurred else None,
        uploadType=UploadType(upload_type),
        ownerGroupId=owner_group_id,
    )

    async def _run():
        async with CmoreClient(base_url=ctx.obj["base_url"], token=ctx.obj["token"]) as client:
            result = await client.post_event(event)
        click.echo(json.dumps(result, indent=2))

    run(_run())


def _extract_auth_data(integration) -> dict:
    """Return the 'auth' action configuration's data dict for an integration,
    or {} if not present/readable."""
    for config in getattr(integration, "configurations", None) or []:
        action = getattr(config, "action", None)
        action_value = getattr(action, "value", None) or getattr(action, "type", None)
        if action_value == "auth":
            return getattr(config, "data", None) or {}
    return {}


def _find_action_config(integration, action_values):
    """Return (configuration_id, data) for the first config whose action value
    is in ``action_values``, or (None, {})."""
    for config in getattr(integration, "configurations", None) or []:
        action = getattr(config, "action", None)
        action_value = getattr(action, "value", None) or getattr(action, "type", None)
        if action_value in action_values:
            return str(config.id), (getattr(config, "data", None) or {})
    return None, {}


def merge_event_type_mapping(deliver_data: dict, entry: dict) -> dict:
    """Merge one CmoreTagMapping ``entry`` into a DeliverConfig's
    ``event_type_to_tag`` list, replacing any existing entry for the same
    event_type. Returns a new dict (does not mutate the input)."""
    data = dict(deliver_data or {})
    mappings = [m for m in (data.get("event_type_to_tag") or []) if m.get("event_type") != entry["event_type"]]
    mappings.append(entry)
    data["event_type_to_tag"] = mappings
    return data


# Sentinels for menu outcomes. Distinct objects because questionary's
# Choice(value=None) defaults the value to the *title* string — so None can't
# safely mark "skip".
_QUIT = object()
_SKIP = object()


async def _choose(message, options, *, skip_label, titles=None, allow_free_text=False, default=None):
    """Single-choice picker. Uses an arrow-key menu (questionary) on a real
    terminal; falls back to a numbered prompt when there's no TTY (piped
    input, CI, tests) or questionary isn't installed.

    ``options`` are the returned values; ``titles`` are optional display
    strings parallel to ``options``. ``default`` (if it's one of ``options``)
    is pre-selected — the current/existing mapping — so Enter keeps it.
    Returns the chosen value, ``None`` if skipped. When ``allow_free_text``
    (numbered fallback only), a typed value that isn't a list number is
    returned verbatim.

    Quitting (the "quit" entry, ``q`` in the fallback, or Ctrl-C) raises
    ``click.Abort`` to exit the wizard cleanly without writing anything.
    """
    import sys

    titles = titles or [str(o) for o in options]
    has_default = default is not None and default in options
    try:
        import questionary

        use_arrows = sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        use_arrows = False

    if use_arrows:
        choices = []
        for title, value in zip(titles, options):
            label = title + ("   (current)" if has_default and value == default else "")
            choices.append(questionary.Choice(title=label, value=value))
        if skip_label is not None:
            choices.append(questionary.Choice(title=skip_label, value=_SKIP))
        choices.append(questionary.Choice(title="✗ quit (discard & exit)", value=_QUIT))
        kwargs = {"choices": choices, "qmark": "›"}
        if has_default:
            kwargs["default"] = default
        # unsafe_ask_async runs in the current event loop (we're inside
        # asyncio.run) AND re-raises KeyboardInterrupt so Ctrl-C quits rather
        # than silently returning None.
        try:
            answer = await questionary.select(message.strip(), **kwargs).unsafe_ask_async()
        except KeyboardInterrupt:
            raise click.Abort()
        if answer is _QUIT:
            raise click.Abort()
        if answer is _SKIP:
            return None
        return answer

    click.echo(message)
    for i, title in enumerate(titles, start=1):
        marker = "   (current)" if has_default and options[i - 1] == default else ""
        click.echo(f"  {i}. {title}{marker}")
    parts = ["number"]
    if allow_free_text:
        parts.append("value")
    parts.append("q to quit")
    if has_default:
        tail = f" (Enter to keep current: {default})"
    elif skip_label is not None:
        tail = " (Enter to skip)"
    else:
        tail = ""
    sel = click.prompt(f"  {' / '.join(parts)}{tail}", default="", show_default=False).strip()
    if sel.lower() == "q":
        raise click.Abort()
    if not sel:
        return default if has_default else None
    if sel.isdigit() and 1 <= int(sel) <= len(options):
        return options[int(sel) - 1]
    if allow_free_text:
        return sel
    return None


async def _interactive_fill(result, tag_info, er_fields, existing_entry=None):
    """Walk the scaffold with the operator: wire unmatched fields, then fill
    blank lookup value mappings. Each pick is an arrow-key menu on a TTY (see
    ``_choose``). If ``existing_entry`` (the current mapping for this event
    type) is given, the previously-chosen field/value is pre-selected as the
    default. Mutates ``result`` in place."""
    from .mapping_scaffold import FieldScaffold, suggest_lookup_value, _normalize

    er_by_key = {f.key: f for f in er_fields}
    LOOKUP_TYPES = ("Lookup", "FixedLookup")

    # Existing mapping → defaults: er_key -> cmore_field, er_key -> {from: to}.
    existing_field_by_key = {}
    existing_value_by_key = {}
    for fm in (existing_entry or {}).get("field_mappings", []):
        existing_field_by_key[fm.get("event_details_key")] = fm.get("cmore_field_name")
        existing_value_by_key[fm.get("event_details_key")] = {
            vm["from_value"]: vm["to_value"]
            for vm in fm.get("value_mappings", []) if vm.get("to_value")
        }

    def _lookup_options(field_info):
        return [lk.get("value") for lk in (getattr(field_info, "lookups", None) or [])]

    # 1) Wire unmatched ER fields by picking from the uncovered CMORE fields.
    uncovered = list(result.uncovered_cmore_fields)
    for er_key in list(result.unmatched_er_fields):
        if not uncovered:
            break
        titles = [f"{n}  ({tag_info.field_by_name(n).data_type})" for n in uncovered]
        name = await _choose(
            f"\nER field '{er_key}' has no CMORE match — pick a CMORE field:",
            uncovered, titles=titles, skip_label="— skip this field —",
            default=existing_field_by_key.get(er_key),
        )
        if name is None:
            continue
        uncovered.remove(name)
        result.unmatched_er_fields.remove(er_key)
        scaffold = FieldScaffold(event_details_key=er_key, cmore_field_name=name)
        # Seed value mappings for a newly-wired lookup field from its ER choices.
        field_info = tag_info.field_by_name(name)
        er_field = er_by_key.get(er_key)
        if field_info.data_type in LOOKUP_TYPES and er_field and er_field.choices:
            for choice in er_field.choices:
                option = suggest_lookup_value(choice, field_info)
                if option is None:
                    scaffold.value_mappings.append({"from_value": choice.value, "to_value": ""})
                elif _normalize(choice.value) != _normalize(option):
                    scaffold.value_mappings.append({"from_value": choice.value, "to_value": option})
        result.fields.append(scaffold)

    # 2) Fill blank value mappings — each source value shown with its ER
    #    display label so the choice is obvious.
    for field_scaffold in result.fields:
        blanks = [vm for vm in field_scaffold.value_mappings if not vm["to_value"]]
        if not blanks:
            continue
        field_info = tag_info.field_by_name(field_scaffold.cmore_field_name)
        options = _lookup_options(field_info)
        er_field = er_by_key.get(field_scaffold.event_details_key)
        displays = {c.value: c.display for c in (er_field.choices or [])} if er_field else {}
        existing_values = existing_value_by_key.get(field_scaffold.event_details_key, {})
        for vm in blanks:
            label = displays.get(vm["from_value"], "")
            shown = vm["from_value"] + (f"  ({label})" if label and label != vm["from_value"] else "")
            chosen = await _choose(
                f"\n{field_scaffold.cmore_field_name}  ←  {shown}",
                options, skip_label="— drop this value —", allow_free_text=True,
                default=existing_values.get(vm["from_value"]),
            )
            if chosen:
                vm["to_value"] = chosen
        field_scaffold.value_mappings = [vm for vm in field_scaffold.value_mappings if vm["to_value"]]


@cli.command("scaffold-mapping")
@click.option("--gundi-username", envvar="GUNDI_USERNAME", help="Gundi username (password grant).")
@click.option("--gundi-password", envvar="GUNDI_PASSWORD", help="Gundi password (prompted if omitted).")
@click.option("--connection", help="Gundi connection id (provider=ER, destination=CMORE).")
@click.option("--event-type", required=True, help="ER event_type value (slug), e.g. rhino_carcass.")
@click.option("--tag", "tag_name", default=None, help="CMORE tag name. Prompted if omitted.")
@click.option("--er-schema-file", type=click.Path(exists=True), help="Offline: ER event-type schema JSON.")
@click.option("--tags-file", type=click.Path(exists=True), help="Offline: CMORE get-tags JSON.")
@click.option("--out", type=click.Path(), default=None, help="Write the config entry to this file.")
@click.option("--write/--no-write", default=False, help="Write the mapping back to the CMORE integration in Gundi.")
@click.option("--non-interactive", is_flag=True, help="Skip prompts; emit the raw scaffold.")
@click.pass_context
def scaffold_mapping(ctx, gundi_username, gundi_password, connection, event_type,
                     tag_name, er_schema_file, tags_file, out, write, non_interactive):
    """Scaffold an ER event_type → CMORE tag mapping, interactively.

    Discovers both ends from a Gundi connection (provider=ER, destination=CMORE),
    fetches the ER event-type schema + CMORE tag schema, suggests field and
    lookup-value mappings, and lets you confirm/fill the rest. Can write the
    result back to the CMORE integration's deliver config.

    Offline mode: pass --er-schema-file and --tags-file to run without Gundi.
    """
    import httpx
    from .er_schema import parse_er_event_schema
    from .mapping_scaffold import build_scaffold
    from .tag_index import _build_index

    async def _run():
        gundi = None
        cmore_base = ctx.obj["base_url"]
        cmore_token = ctx.obj["token"]
        er_base = er_token = dest_integration = None
        deliver_config_id, deliver_data, existing_entry = None, {}, None

        if connection:
            from gundi_client_v2 import GundiClient

            password = gundi_password or click.prompt("Gundi password", hide_input=True)
            gundi = GundiClient(username=gundi_username, password=password)
            conn = await gundi.get_connection_details(connection)
            provider = await gundi.get_integration_details(conn.provider.id)
            dest_integration = await gundi.get_integration_details(conn.destinations[0].id)
            er_auth = _extract_auth_data(provider)
            er_base = er_auth.get("base_url") or provider.base_url
            er_token = er_auth.get("token") or er_token
            # CMORE's API lives under a path (e.g. /za/WebAPI/api) that the
            # integration's top-level base_url omits; the auth config carries the
            # full API base the runner actually uses, so prefer it.
            cmore_auth = _extract_auth_data(dest_integration)
            cmore_base = cmore_auth.get("base_url") or dest_integration.base_url or cmore_base
            cmore_token = cmore_auth.get("token") or cmore_token
            # Existing deliver config → defaults for any mapping already set up.
            deliver_config_id, deliver_data = _find_action_config(
                dest_integration, ("push_events", "deliver", "push")
            )
            existing_entry = next(
                (m for m in (deliver_data.get("event_type_to_tag") or [])
                 if m.get("event_type") == event_type),
                None,
            )
            click.echo(f"Connection {connection}: provider={provider.name!r} destination={dest_integration.name!r}")
            if existing_entry:
                click.echo(f"Found an existing mapping for '{event_type}' → defaults pre-selected.")

        if not non_interactive:
            click.echo(
                f"\nBuilding an ER → CMORE mapping for event_type '{event_type}'.\n"
                "At each prompt: ↑/↓ + Enter to choose (or type the number), "
                "pick “quit” / press Ctrl-C to exit.\n"
            )

        # CMORE tag schema
        if tags_file:
            with open(tags_file) as fh:
                raw_tags = json.load(fh)
        else:
            if not cmore_token:
                raise click.UsageError("CMORE token unavailable; pass --token or --tags-file.")
            async with CmoreClient(base_url=cmore_base, token=cmore_token) as client:
                raw_tags = await client.get_tags()
        index = _build_index(raw_tags)
        resolved_tag = tag_name
        if not resolved_tag:
            # Pick the CMORE tag from a menu (arrow-key on a TTY) rather than
            # making the operator type the exact name.
            titles = [f"{name}  ({index[name].domain})" for name in sorted(index)]
            resolved_tag = await _choose(
                f"Select the CMORE tag to map '{event_type}' events to:",
                sorted(index), titles=titles, skip_label=None,
                default=(existing_entry or {}).get("tag_name"),
            )
        tag_info = index.get(resolved_tag) if resolved_tag else None
        if tag_info is None:
            raise click.UsageError(f"CMORE tag {resolved_tag!r} not found in the tag schema.")

        # ER event-type schema
        if er_schema_file:
            with open(er_schema_file) as fh:
                raw_schema = json.load(fh)
        else:
            if not er_base or not er_token:
                raise click.UsageError("ER base_url/token unavailable; pass --er-schema-file.")
            # The v2 schema endpoint with pre_render + s_format=enum inlines each
            # choice field's values (enum + x-enumExtra), so no separate choices
            # fetch is needed.
            url = f"{er_base.rstrip('/')}/api/v2.0/activity/eventtypes/{event_type}/schema"
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {er_token}"}) as http:
                resp = await http.get(
                    url, params={"pre_render": "true", "s_format": "enum"}, timeout=30.0
                )
                resp.raise_for_status()
                raw_schema = resp.json()
        er_fields = parse_er_event_schema(raw_schema)
        if not er_fields:
            raise click.UsageError("No fields parsed from the ER schema; check the event_type/schema shape.")

        # Scaffold
        result = build_scaffold(er_fields, tag_info, event_type=event_type)
        click.echo(
            f"\nScaffold: {len(result.fields)} field(s) matched, "
            f"{len(result.unmatched_er_fields)} ER field(s) unmatched, "
            f"{len(result.uncovered_cmore_fields)} CMORE field(s) uncovered."
        )
        if result.unmatched_er_fields:
            click.echo("  Unmatched ER fields: " + ", ".join(result.unmatched_er_fields))
        if result.uncovered_cmore_fields:
            click.echo("  Uncovered CMORE fields: " + ", ".join(result.uncovered_cmore_fields))

        if not non_interactive:
            await _interactive_fill(result, tag_info, er_fields, existing_entry)

        entry = result.to_config_entry()
        rendered = json.dumps(entry, indent=2)
        click.echo("\n--- mapping entry ---\n" + rendered)

        if out:
            with open(out, "w") as fh:
                fh.write(rendered + "\n")
            click.echo(f"\nWrote {out}")

        if write:
            if gundi is None or dest_integration is None:
                raise click.UsageError("--write requires --connection (to locate the CMORE integration).")
            if deliver_config_id is None:
                raise click.UsageError("Could not find a deliver/push action configuration on the CMORE integration.")
            new_data = merge_event_type_mapping(deliver_data, entry)
            await gundi.update_integration_configuration(dest_integration.id, deliver_config_id, new_data)
            click.echo(f"\nWrote mapping back to CMORE integration {dest_integration.id} (config {deliver_config_id}).")

        if gundi is not None:
            await gundi.close()

    run(_run())


if __name__ == "__main__":
    cli()
