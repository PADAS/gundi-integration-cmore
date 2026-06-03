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


if __name__ == "__main__":
    cli()
