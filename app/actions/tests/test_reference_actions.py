"""Tests for the reference-action contract and the four CMORE reference actions."""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock

from gundi_core.schemas.v2 import Integration

from app.actions.configurations import ListTagNamesQuery
from app.actions.handlers import action_list_tag_names
from app.actions.tests.test_handlers import _integration_dict


def make_integration(integration_id: str = None, token: str = None) -> Integration:
    data = _integration_dict(integration_id or str(uuid.uuid4()))
    if token is not None:
        data["configurations"][0]["data"]["token"] = token
    return Integration.parse_obj(data)


def test_reference_contract_types():
    from app.actions.core import ActionConfiguration, ReferenceActionConfiguration
    from app.actions.reference_data import ReferenceDataResponse, ReferenceOption

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
                         {"id": 1667, "value": "Abalone Harvesting", "order": 3},
                         {"id": 1669, "value": "Camp", "order": 1},
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
    return make_integration()


@pytest.fixture
def mock_cmore_client(mocker):
    """Patch CmoreClient in handlers; returns the mock instance the
    `async with CmoreClient(...) as client:` block yields. Also empties the
    reference tag cache: it is keyed by (base_url, token), which every test
    integration here shares, so one test's tags would otherwise serve the
    next."""
    from app.actions import handlers as handlers_module

    handlers_module.reference_tag_index._reset()
    instance = MagicMock()
    instance.get_tags = AsyncMock(return_value=RAW_TAGS)
    instance.get_classification_tree = AsyncMock(return_value=[])
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch.object(handlers_module, "CmoreClient", client_cls)
    return instance


@pytest.mark.asyncio
async def test_list_tag_names_returns_id_values_with_name_labels(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    result = await action_list_tag_names(integration, ListTagNamesQuery())

    options = [(o["value"], o["label"], o["group"], o["description"]) for o in result["options"]]
    assert options == [
        ("21", "Animal Sighting", "Wildlife", "ID 21"),
        ("20", "Evidence of Poacher", "Wildlife", "ID 20"),
    ]
    assert result["cache_ttl_seconds"] == 300
    mock_cmore_client.get_tags.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tag_names_shows_both_tags_on_cross_domain_name_collision(
    integration, mock_cmore_client
):
    """Same-named tags in two domains must BOTH appear (listed from by_id),
    disambiguated by group — fixes the last-wins dropdown collapse."""
    from unittest.mock import AsyncMock
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    mock_cmore_client.get_tags = AsyncMock(return_value=[
        {"id": 1, "name": "DomainA", "tags": [
            {"id": 10, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
        {"id": 2, "name": "DomainB", "tags": [
            {"id": 11, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
    ])
    result = await action_list_tag_names(integration, ListTagNamesQuery())
    assert [(o["value"], o["group"]) for o in result["options"]] == [
        ("10", "DomainA"), ("11", "DomainB"),
    ]


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
async def test_list_tag_fields_accepts_id_and_name(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    expected = [
        ("260", "Reported By", "String · ID 260"),
        ("261", "Evidence Type", "Lookup · ID 261"),
    ]
    by_id = await action_list_tag_fields(integration, ListTagFieldsQuery(tag="20"))
    assert [(o["value"], o["label"], o["description"]) for o in by_id["options"]] == expected
    by_name = await action_list_tag_fields(
        integration, ListTagFieldsQuery(tag="Evidence of Poacher")
    )
    assert [(o["value"], o["label"], o["description"]) for o in by_name["options"]] == expected


@pytest.mark.asyncio
async def test_list_tag_fields_unknown_tag_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    with pytest.raises(ValueError, match="No Such Tag"):
        await action_list_tag_fields(integration, ListTagFieldsQuery(tag="No Such Tag"))


@pytest.mark.asyncio
async def test_list_field_options_accepts_id_and_name(integration, mock_cmore_client):
    """Option values stay lookup value STRINGS (CMORE matches by string);
    only the tag/field selectors accept ids."""
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    by_id = await action_list_field_options(
        integration, ListFieldOptionsQuery(tag="20", field="261")
    )
    assert [o["value"] for o in by_id["options"]] == ["Camp", "Abalone Harvesting"]
    by_name = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag="Evidence of Poacher", field="Evidence Type"),
    )
    assert [o["value"] for o in by_name["options"]] == ["Camp", "Abalone Harvesting"]


@pytest.mark.asyncio
async def test_list_field_options_sorts_missing_order_after_ordered_and_by_value(
    integration, mock_cmore_client
):
    """Lookups without an `order` fall back to sorting after ordered ones,
    then alphabetically by value."""
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    raw_tags = [
        {
            "id": 8,
            "name": "Wildlife",
            "tags": [
                {
                    "id": 20,
                    "name": "Evidence of Poacher",
                    "typeLimiter": "Incident",
                    "fields": [
                        {
                            "id": 261,
                            "name": "Evidence Type",
                            "dataType": "Lookup",
                            "allowMultipleValues": True,
                            "lookups": [
                                {"id": 1, "value": "Zebra Tracks", "order": None},
                                {"id": 2, "value": "Camp", "order": 2},
                                {"id": 3, "value": "Abalone Harvesting", "order": None},
                                {"id": 4, "value": "Snare", "order": 1},
                            ],
                        },
                    ],
                },
            ],
        },
    ]
    mock_cmore_client.get_tags = AsyncMock(return_value=raw_tags)

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag="Evidence of Poacher", field="Evidence Type"),
    )
    assert [o["value"] for o in result["options"]] == [
        "Snare",
        "Camp",
        "Abalone Harvesting",
        "Zebra Tracks",
    ]


@pytest.mark.asyncio
async def test_list_field_options_non_lookup_field_returns_empty(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag="Evidence of Poacher", field="Reported By"),
    )
    assert result["options"] == []


@pytest.mark.asyncio
async def test_list_field_options_unknown_field_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    with pytest.raises(ValueError, match="No Such Field"):
        await action_list_field_options(
            integration,
            ListFieldOptionsQuery(tag="Evidence of Poacher", field="No Such Field"),
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


@pytest.mark.asyncio
async def test_reference_actions_share_a_ttl_cached_tag_fetch(
    integration, mock_cmore_client
):
    """The dropdown cascade (tag → fields → options) must not pay the ~25s
    production get_tags fetch once per dropdown — calls within the TTL window
    reuse one fetch."""
    await action_list_tag_names(integration, ListTagNamesQuery())
    await action_list_tag_names(integration, ListTagNamesQuery())

    assert mock_cmore_client.get_tags.await_count == 1


@pytest.mark.asyncio
async def test_reference_tag_cache_is_shared_across_runs_with_the_same_credentials(
    mock_cmore_client,
):
    """On the ephemeral (portal draft) path the runner mints a fresh
    integration id per run, so a cache keyed by integration id never hits and
    every dropdown pays the full get_tags fetch. Tag visibility is scoped by
    the token, so the cache is keyed by (base_url, token) instead."""
    await action_list_tag_names(make_integration(), ListTagNamesQuery())
    await action_list_tag_names(make_integration(), ListTagNamesQuery())

    assert mock_cmore_client.get_tags.await_count == 1


@pytest.mark.asyncio
async def test_reference_tag_cache_is_separate_per_token(mock_cmore_client):
    """Two tokens against the same CMORE may see different tag sets."""
    integration_id = str(uuid.uuid4())
    await action_list_tag_names(make_integration(integration_id), ListTagNamesQuery())
    await action_list_tag_names(
        make_integration(integration_id, token="another-token"), ListTagNamesQuery()
    )

    assert mock_cmore_client.get_tags.await_count == 2


@pytest.mark.asyncio
async def test_reference_cache_hit_does_not_open_a_cmore_client(integration, mocker):
    """CmoreClient.__init__ builds an httpx client (synchronous SSL setup);
    a cache hit, the common case in the dropdown cascade, must not pay it."""
    from app.actions import handlers as handlers_module

    handlers_module.reference_tag_index._reset()
    instance = MagicMock()
    instance.get_tags = AsyncMock(return_value=RAW_TAGS)
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch.object(handlers_module, "CmoreClient", client_cls)

    await action_list_tag_names(integration, ListTagNamesQuery())
    await action_list_tag_names(integration, ListTagNamesQuery())

    assert client_cls.call_count == 1


def test_reference_tag_cache_key_does_not_carry_the_token():
    """The cache key is what shows up in logs and in a heap dump; it must be a
    digest of the token, never the token itself."""
    from app.actions.handlers import _reference_cache_scope

    scope = _reference_cache_scope("secret-token-value")

    assert "secret-token-value" not in scope
    assert scope == _reference_cache_scope("secret-token-value")
    assert scope != _reference_cache_scope("other-token")
