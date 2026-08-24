"""Tests for the configuration validator (app/datasource/validation.py).

All checks run against a fake CmoreClient — no live services.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.datasource.validation import CheckStatus, check_auth


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://cmore.test/v2/tags/getfull")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(f"{status_code}", request=request, response=response)


@pytest.mark.asyncio
async def test_check_auth_passes_when_tags_fetch_succeeds():
    client = AsyncMock()
    client.get_tags.return_value = [{"tagDomainId": 1, "tags": []}]

    result, raw_tags = await check_auth(client)

    assert result.status == CheckStatus.PASS
    assert raw_tags == [{"tagDomainId": 1, "tags": []}]


@pytest.mark.asyncio
async def test_check_auth_fails_on_401_with_token_remediation():
    client = AsyncMock()
    client.get_tags.side_effect = _http_error(401)

    result, raw_tags = await check_auth(client)

    assert result.status == CheckStatus.FAIL
    assert raw_tags is None
    assert "token" in result.remediation.lower()


@pytest.mark.asyncio
async def test_check_auth_fails_on_connect_error_with_base_url_remediation():
    client = AsyncMock()
    client.get_tags.side_effect = httpx.ConnectError("nope")

    result, raw_tags = await check_auth(client)

    assert result.status == CheckStatus.FAIL
    assert "base_url" in result.remediation.lower() or "base url" in result.remediation.lower()


@pytest.mark.asyncio
async def test_check_auth_fails_on_404_with_base_url_remediation():
    # A wrong path (e.g. missing /za/WebAPI/api) reaches the server but 404s.
    client = AsyncMock()
    client.get_tags.side_effect = _http_error(404)

    result, _ = await check_auth(client)

    assert result.status == CheckStatus.FAIL
    assert "base_url" in result.remediation.lower() or "base url" in result.remediation.lower()


# --- tag-mapping checks -----------------------------------------------------

from app.datasource.tag_index import _build_index  # noqa: E402
from app.datasource.validation import check_tag_mappings  # noqa: E402

RAW_TAGS = [
    {
        "name": "Wildlife",
        "tags": [
            {
                "id": 8443,
                "name": "Rhino Carcass",
                "fields": [
                    {
                        "id": 1261,
                        "name": "Animal Sex",
                        "dataType": "Lookup",
                        "lookups": [{"value": "Male"}, {"value": "Female"}],
                    },
                    {"id": 1262, "name": "Horn Count", "dataType": "Number", "lookups": []},
                ],
            }
        ],
    }
]


def _index():
    return _build_index(RAW_TAGS)


def test_check_tag_mappings_skips_without_deliver_config():
    results = check_tag_mappings(_index(), {})
    assert len(results) == 1
    assert results[0].status == CheckStatus.SKIP


def test_check_tag_mappings_passes_when_everything_resolves():
    deliver = {
        "event_type_to_tag": [
            {
                "event_type": "rhino_carcass",
                "tag": "8443",
                "field_mappings": [
                    {
                        "event_details_key": "animal_sex",
                        "cmore_field": "1261",
                        "value_mappings": [{"from_value": "male", "to_value": "Male"}],
                    }
                ],
            }
        ]
    }
    results = check_tag_mappings(_index(), deliver)
    assert [r.status for r in results] == [CheckStatus.PASS]
    assert "rhino_carcass" in results[0].name


def test_check_tag_mappings_fails_on_unresolvable_tag_pointing_at_domain_grant():
    deliver = {"event_type_to_tag": [{"event_type": "shot", "tag": "Nonexistent Tag"}]}
    results = check_tag_mappings(_index(), deliver)
    assert results[0].status == CheckStatus.FAIL
    assert "domain" in results[0].remediation.lower()


def test_check_tag_mappings_fails_on_unresolvable_field():
    deliver = {
        "event_type_to_tag": [
            {
                "event_type": "rhino_carcass",
                "tag": "Rhino Carcass",
                "field_mappings": [{"event_details_key": "x", "cmore_field": "No Such Field"}],
            }
        ]
    }
    results = check_tag_mappings(_index(), deliver)
    assert results[0].status == CheckStatus.FAIL
    assert "No Such Field" in results[0].detail


def test_check_tag_mappings_warns_on_invalid_lookup_to_value():
    deliver = {
        "event_type_to_tag": [
            {
                "event_type": "rhino_carcass",
                "tag": "Rhino Carcass",
                "field_mappings": [
                    {
                        "event_details_key": "animal_sex",
                        "cmore_field": "Animal Sex",
                        "value_mappings": [{"from_value": "calf", "to_value": "Juvenile"}],
                    }
                ],
            }
        ]
    }
    results = check_tag_mappings(_index(), deliver)
    assert results[0].status == CheckStatus.WARN
    assert "Juvenile" in results[0].detail


def test_check_tag_mappings_accepts_case_insensitive_lookup_to_value():
    # The runner resolves 'male' → 'Male' at delivery time; not a problem.
    deliver = {
        "event_type_to_tag": [
            {
                "event_type": "rhino_carcass",
                "tag": "Rhino Carcass",
                "field_mappings": [
                    {
                        "event_details_key": "animal_sex",
                        "cmore_field": "Animal Sex",
                        "value_mappings": [{"from_value": "m", "to_value": "male"}],
                    }
                ],
            }
        ]
    }
    results = check_tag_mappings(_index(), deliver)
    assert [r.status for r in results] == [CheckStatus.PASS]


# --- classification checks ---------------------------------------------------

from app.datasource.validation import check_classifications  # noqa: E402

CLASSIFICATION_TREE = [
    {
        "battleDimension": "LAND",
        "forces": [
            {
                "force": "ANIMAL",
                "types": [{"type": "RHINO", "roles": ["UNKNOWN", "BLACK", "WHITE"]}],
            }
        ],
    }
]


def test_check_classifications_skips_without_config():
    results = check_classifications(CLASSIFICATION_TREE, {})
    assert len(results) == 1
    assert results[0].status == CheckStatus.SKIP


def test_check_classifications_passes_valid_full_path():
    deliver = {
        "subject_type_to_classification": [
            {
                "subject_type": "rhino",
                "battleDimension": "LAND",
                "force": "ANIMAL",
                "type": "RHINO",
                "role": "WHITE",
            }
        ]
    }
    results = check_classifications(CLASSIFICATION_TREE, deliver)
    assert [r.status for r in results] == [CheckStatus.PASS]
    assert "rhino" in results[0].name


def test_check_classifications_fails_on_invalid_force():
    deliver = {
        "subject_type_to_classification": [
            {"subject_type": "rhino", "battleDimension": "LAND", "force": "VEHICLE"}
        ]
    }
    results = check_classifications(CLASSIFICATION_TREE, deliver)
    assert results[0].status == CheckStatus.FAIL
    assert "VEHICLE" in results[0].detail


def test_check_classifications_warns_on_gap_in_levels():
    # type set without force: can't validate deeper levels without the parent.
    deliver = {
        "subject_type_to_classification": [
            {"subject_type": "rhino", "battleDimension": "LAND", "type": "RHINO"}
        ]
    }
    results = check_classifications(CLASSIFICATION_TREE, deliver)
    assert results[0].status == CheckStatus.WARN


# --- GNode ownership check ---------------------------------------------------

from app.datasource.schemas import CmoreGatewayMapping  # noqa: E402
from app.datasource.validation import check_gnodes  # noqa: E402


@pytest.mark.asyncio
async def test_check_gnodes_reports_count_and_sources():
    client = AsyncMock()
    client.get_gateway_mapping.return_value = [
        CmoreGatewayMapping(clientId=1, trackNo=10, trackSource="Gundi"),
        CmoreGatewayMapping(clientId=2, trackNo=11, trackSource="Gundi"),
    ]
    result = await check_gnodes(client)
    assert result.status == CheckStatus.PASS
    assert "2" in result.detail and "Gundi" in result.detail


@pytest.mark.asyncio
async def test_check_gnodes_warns_when_token_owns_no_gnodes():
    client = AsyncMock()
    client.get_gateway_mapping.return_value = []
    result = await check_gnodes(client)
    assert result.status == CheckStatus.WARN


@pytest.mark.asyncio
async def test_check_gnodes_fails_on_http_error():
    client = AsyncMock()
    client.get_gateway_mapping.side_effect = _http_error(403)
    result = await check_gnodes(client)
    assert result.status == CheckStatus.FAIL


# --- owner_group_id probe ------------------------------------------------------

from app.datasource.validation import check_owner_group  # noqa: E402


@pytest.mark.asyncio
async def test_check_owner_group_skips_by_default_with_probe_hint():
    client = AsyncMock()
    result = await check_owner_group(client, owner_group_id=7932, probe=False)
    assert result.status == CheckStatus.SKIP
    assert "--probe-event" in result.detail
    client.post_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_owner_group_skips_when_no_owner_group_configured():
    client = AsyncMock()
    result = await check_owner_group(client, owner_group_id=None, probe=True)
    assert result.status == CheckStatus.SKIP
    client.post_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_owner_group_probe_posts_labeled_test_event():
    client = AsyncMock()
    client.post_event.return_value = {"messageId": 352906}
    result = await check_owner_group(client, owner_group_id=7932, probe=True)
    assert result.status == CheckStatus.PASS
    assert "352906" in result.detail
    (event,), _ = client.post_event.await_args
    assert event.ownerGroupId == 7932
    assert "please ignore" in event.description.lower()


@pytest.mark.asyncio
async def test_check_owner_group_probe_failure_points_at_target_group():
    client = AsyncMock()
    client.post_event.side_effect = _http_error(403)
    result = await check_owner_group(client, owner_group_id=7932, probe=True)
    assert result.status == CheckStatus.FAIL
    assert "target group" in result.remediation.lower()


# --- orchestrator ---------------------------------------------------------------

from app.datasource.validation import ValidationReport, run_validation  # noqa: E402


def _full_client():
    client = AsyncMock()
    client.get_tags.return_value = RAW_TAGS
    client.get_classification_tree.return_value = CLASSIFICATION_TREE
    client.get_gateway_mapping.return_value = [
        CmoreGatewayMapping(clientId=1, trackNo=10, trackSource="Gundi"),
    ]
    return client


@pytest.mark.asyncio
async def test_run_validation_happy_path_reports_all_checks():
    deliver = {
        "event_type_to_tag": [{"event_type": "rhino_carcass", "tag": "Rhino Carcass"}],
        "subject_type_to_classification": [
            {"subject_type": "rhino", "battleDimension": "LAND", "force": "ANIMAL"}
        ],
    }
    report = await run_validation(
        _full_client(), owner_group_id=7932, deliver_data=deliver, probe_event=False
    )
    assert isinstance(report, ValidationReport)
    by_name = {c.name: c.status for c in report.checks}
    assert by_name["auth"] == CheckStatus.PASS
    assert by_name["tag_mapping:rhino_carcass"] == CheckStatus.PASS
    assert by_name["classification:rhino"] == CheckStatus.PASS
    assert by_name["gnode_ownership"] == CheckStatus.PASS
    assert by_name["owner_group"] == CheckStatus.SKIP
    assert not report.has_failures


@pytest.mark.asyncio
async def test_run_validation_skips_downstream_checks_when_auth_fails():
    client = AsyncMock()
    client.get_tags.side_effect = _http_error(401)
    report = await run_validation(
        client, owner_group_id=7932, deliver_data={}, probe_event=True
    )
    assert report.has_failures
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["auth"] == CheckStatus.FAIL
    downstream = [c for c in report.checks if c.name != "auth"]
    assert downstream and all(c.status == CheckStatus.SKIP for c in downstream)
    client.post_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_validation_does_not_fetch_classification_tree_when_unneeded():
    client = _full_client()
    await run_validation(client, owner_group_id=None, deliver_data={}, probe_event=False)
    client.get_classification_tree.assert_not_awaited()


# --- CLI wiring -------------------------------------------------------------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from click.testing import CliRunner  # noqa: E402

import app.datasource.cli as cli_module  # noqa: E402
from app.datasource.cli import _validation_inputs_from_integration, cli  # noqa: E402


def _fake_cmore_integration():
    return SimpleNamespace(
        base_url="https://cmorewc1.chpc.ac.za",
        configurations=[
            SimpleNamespace(
                id="auth-cfg",
                action=SimpleNamespace(value="auth"),
                data={
                    "base_url": "https://cmorewc1.chpc.ac.za/za/WebAPI/api",
                    "token": "secret",
                    "owner_group_id": 7932,
                },
            ),
            SimpleNamespace(
                id="push-cfg",
                action=SimpleNamespace(value="push_events"),
                data={"event_type_to_tag": [{"event_type": "shot", "tag": "8443"}]},
            ),
        ],
    )


def test_validation_inputs_from_integration_extracts_auth_and_deliver():
    base_url, token, owner_group_id, deliver = _validation_inputs_from_integration(
        _fake_cmore_integration()
    )
    assert base_url == "https://cmorewc1.chpc.ac.za/za/WebAPI/api"
    assert token == "secret"
    assert owner_group_id == 7932
    assert deliver == {"event_type_to_tag": [{"event_type": "shot", "tag": "8443"}]}


class _FakeCmoreClient:
    """Stands in for CmoreClient in the CLI; behavior set via class attrs."""

    get_tags_result = RAW_TAGS
    get_tags_error = None

    def __init__(self, base_url, token=None, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get_tags(self):
        if self.get_tags_error:
            raise self.get_tags_error
        return self.get_tags_result

    async def get_gateway_mapping(self):
        return [CmoreGatewayMapping(clientId=1, trackNo=10, trackSource="Gundi")]

    async def get_classification_tree(self):
        return CLASSIFICATION_TREE


@pytest.fixture
def fake_client(monkeypatch):
    _FakeCmoreClient.get_tags_error = None
    monkeypatch.setattr(cli_module, "CmoreClient", _FakeCmoreClient)
    return _FakeCmoreClient


def test_validate_raw_flags_all_pass_exits_zero(fake_client):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--token", "secret", "--base-url", "https://cmore.test/api",
         "validate", "--owner-group-id", "7932"],
    )
    assert result.exit_code == 0, result.output
    assert "auth" in result.output
    assert "PASS" in result.output


def test_validate_exits_one_when_a_check_fails(fake_client):
    request = httpx.Request("GET", "https://cmore.test/v2/tags/getfull")
    fake_client.get_tags_error = httpx.HTTPStatusError(
        "401", request=request, response=httpx.Response(401, request=request)
    )
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--token", "bad", "--base-url", "https://cmore.test/api", "validate"]
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


def test_validate_json_output_is_parseable(fake_client):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--token", "secret", "--base-url", "https://cmore.test/api",
         "validate", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert {c["name"] for c in payload["checks"]} >= {"auth", "gnode_ownership"}


def test_validate_requires_token_for_raw_path():
    runner = CliRunner()
    result = runner.invoke(cli, ["--base-url", "https://cmore.test/api", "validate"])
    assert result.exit_code != 0
    assert "token" in result.output.lower()


# --- timeout diagnosis --------------------------------------------------------


@pytest.mark.asyncio
async def test_check_auth_timeout_is_not_reported_as_auth_or_base_url_failure():
    # Seen live on cmore.csir.co.za: get_tags returns 200 but takes ~43s for
    # a ~2MB tag catalog. A timeout means "server slow", not "bad token/URL".
    client = AsyncMock()
    client.get_tags.side_effect = httpx.ReadTimeout("timed out")

    result, raw_tags = await check_auth(client)

    assert result.status == CheckStatus.FAIL
    assert raw_tags is None
    assert "timed out" in result.detail.lower()
    assert "not an authentication failure" in result.detail.lower()
    assert "--timeout" in result.remediation


def test_validate_timeout_option_reaches_the_client(fake_client, monkeypatch):
    created = {}
    original_init = _FakeCmoreClient.__init__

    def recording_init(self, base_url, token=None, **kwargs):
        created.update(kwargs, base_url=base_url)
        original_init(self, base_url, token=token, **kwargs)

    monkeypatch.setattr(_FakeCmoreClient, "__init__", recording_init)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["--token", "secret", "--base-url", "https://cmore.test/api",
         "validate", "--timeout", "90"],
    )
    assert result.exit_code == 0, result.output
    assert created["timeout"] == 90.0


def test_validate_default_timeout_is_generous(fake_client, monkeypatch):
    # Production tag catalogs (DFFE) take ~43s to serve; the validator must
    # not default to the delivery-path 10s timeout.
    created = {}
    original_init = _FakeCmoreClient.__init__

    def recording_init(self, base_url, token=None, **kwargs):
        created.update(kwargs)
        original_init(self, base_url, token=token, **kwargs)

    monkeypatch.setattr(_FakeCmoreClient, "__init__", recording_init)
    runner = CliRunner()
    result = runner.invoke(
        cli, ["--token", "secret", "--base-url", "https://cmore.test/api", "validate"]
    )
    assert result.exit_code == 0, result.output
    assert created["timeout"] >= 120.0
