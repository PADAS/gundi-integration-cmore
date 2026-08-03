"""Tests for the reference-action contract and the four CMORE reference actions."""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock


def test_reference_contract_types():
    from app.actions.core import (
        ActionConfiguration,
        ReferenceActionConfiguration,
        ReferenceDataResponse,
        ReferenceOption,
    )

    assert issubclass(ReferenceActionConfiguration, ActionConfiguration)

    response = ReferenceDataResponse(
        options=[ReferenceOption(value="Poacher Sighting", group="Wildlife")]
    )
    data = response.dict()
    assert data["options"][0]["value"] == "Poacher Sighting"
    assert data["options"][0]["label"] is None
    assert data["cache_ttl_seconds"] == 300
    assert data["truncated"] is False


RAW_TAGS = [
    {
        "id": 8,
        "name": "Wildlife",
        "tags": [
            {
                "id": 20,
                "name": "Evidence of Poacher",
                "typeLimiter": "Incident",
                "fields": [
                    {"id": 260, "name": "Reported By", "dataType": "String",
                     "allowMultipleValues": False, "lookups": []},
                    {"id": 261, "name": "Evidence Type", "dataType": "Lookup",
                     "allowMultipleValues": True,
                     "lookups": [
                         {"id": 1667, "value": "Abalone Harvesting", "order": 1},
                         {"id": 1669, "value": "Camp", "order": 3},
                     ]},
                ],
            },
            {"id": 21, "name": "Animal Sighting", "typeLimiter": "Incident", "fields": []},
        ],
    },
    {"id": 1, "name": "System", "tags": []},
]


@pytest.fixture
def integration():
    from gundi_core.schemas.v2 import Integration
    from app.actions.tests.test_handlers import _integration_dict

    return Integration.parse_obj(_integration_dict(str(uuid.uuid4())))


@pytest.fixture
def mock_cmore_client(mocker):
    """Patch CmoreClient in handlers; returns the mock instance the
    `async with CmoreClient(...) as client:` block yields."""
    from app.actions import handlers as handlers_module

    instance = MagicMock()
    instance.get_tags = AsyncMock(return_value=RAW_TAGS)
    instance.get_classification_tree = AsyncMock(return_value=[])
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch.object(handlers_module, "CmoreClient", client_cls)
    return instance


@pytest.mark.asyncio
async def test_list_tag_names_returns_all_tags_grouped_by_domain(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    result = await action_list_tag_names(integration, ListTagNamesQuery())

    values = [(o["value"], o["group"]) for o in result["options"]]
    assert values == [
        ("Animal Sighting", "Wildlife"),
        ("Evidence of Poacher", "Wildlife"),
    ]
    assert result["cache_ttl_seconds"] == 300
    mock_cmore_client.get_tags.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tag_names_propagates_upstream_errors(
    integration, mock_cmore_client
):
    """CMORE API failures must propagate (execute_action turns them into an
    error response the portal shows as 'couldn't load options') — never be
    swallowed into an empty options list."""
    import httpx
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    mock_cmore_client.get_tags = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=502)
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        await action_list_tag_names(integration, ListTagNamesQuery())


@pytest.mark.asyncio
async def test_list_tag_fields_returns_fields_for_tag(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    result = await action_list_tag_fields(
        integration, ListTagFieldsQuery(tag_name="Evidence of Poacher")
    )

    assert [(o["value"], o["description"]) for o in result["options"]] == [
        ("Reported By", "String"),
        ("Evidence Type", "Lookup"),
    ]


@pytest.mark.asyncio
async def test_list_tag_fields_unknown_tag_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    with pytest.raises(ValueError, match="No Such Tag"):
        await action_list_tag_fields(
            integration, ListTagFieldsQuery(tag_name="No Such Tag")
        )


@pytest.mark.asyncio
async def test_list_field_options_returns_lookup_values(integration, mock_cmore_client):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="Evidence Type"),
    )
    assert [o["value"] for o in result["options"]] == ["Abalone Harvesting", "Camp"]


@pytest.mark.asyncio
async def test_list_field_options_non_lookup_field_returns_empty(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="Reported By"),
    )
    assert result["options"] == []


@pytest.mark.asyncio
async def test_list_field_options_unknown_field_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    with pytest.raises(ValueError, match="No Such Field"):
        await action_list_field_options(
            integration,
            ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="No Such Field"),
        )


CLASSIFICATION_TREE = [
    {
        "battleDimension": "AIR",
        "forces": [
            {
                "force": "CIVIL",
                "types": [
                    {"type": "FIXED_WING", "roles": ["UNKNOWN", "LIGHT", "MICROLIGHT"]},
                ],
            },
        ],
    },
    {
        "battleDimension": "LAND",
        "forces": [
            {"force": "ANIMAL", "types": [{"type": "DOG", "roles": ["K9"]}]},
        ],
    },
]


@pytest.mark.asyncio
async def test_list_classification_values_cascades(integration, mock_cmore_client):
    from app.actions.configurations import ListClassificationValuesQuery
    from app.actions.handlers import action_list_classification_values

    mock_cmore_client.get_classification_tree = AsyncMock(
        return_value=CLASSIFICATION_TREE
    )

    async def values(**kwargs):
        result = await action_list_classification_values(
            integration, ListClassificationValuesQuery(**kwargs)
        )
        return [o["value"] for o in result["options"]]

    assert await values() == ["AIR", "LAND"]
    assert await values(battleDimension="AIR") == ["CIVIL"]
    assert await values(battleDimension="AIR", force="CIVIL") == ["FIXED_WING"]
    assert await values(battleDimension="AIR", force="CIVIL", type="FIXED_WING") == [
        "UNKNOWN", "LIGHT", "MICROLIGHT",
    ]


@pytest.mark.asyncio
async def test_list_classification_values_unknown_branch_raises(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListClassificationValuesQuery
    from app.actions.handlers import action_list_classification_values

    mock_cmore_client.get_classification_tree = AsyncMock(
        return_value=CLASSIFICATION_TREE
    )
    with pytest.raises(ValueError, match="SEA"):
        await action_list_classification_values(
            integration, ListClassificationValuesQuery(battleDimension="SEA")
        )
