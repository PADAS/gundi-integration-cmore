"""Tests for the CMORE tag-name indexer."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.datasource.tag_index import FieldInfo, TagIndex, TagInfo, _build_index


# ----- _build_index -----


def _sample_response():
    """Mirrors the real shape of CMORE's get_tags() response."""
    return [
        {
            "id": 8,
            "name": "Wildlife",
            "iconId": None,
            "tags": [
                {
                    "id": 29,
                    "name": "Poacher Sighting",
                    "typeLimiter": "Incident",
                    "tagDomainId": 8,
                    "fields": [
                        {
                            "id": 1327,
                            "name": "Direction",
                            "dataType": "Lookup",
                            "lookups": [
                                {"id": 2799, "value": "E to W"},
                                {"id": 2800, "value": "N to S"},
                            ],
                        },
                        {
                            "id": 1328,
                            "name": "Number of People",
                            "dataType": "Number",
                            "lookups": [],
                        },
                    ],
                },
            ],
        },
        {
            "id": 2,
            "name": "Other",
            "iconId": 7,
            "tags": [
                {
                    "id": 3144,
                    "name": "test tag",
                    "typeLimiter": "Message",
                    "tagDomainId": 2,
                    "fields": [],
                },
            ],
        },
        {
            "id": 1,
            "name": "System",
            "iconId": 4,
            "tags": [],
        },
    ]


def test_build_index_flattens_across_domains():
    index = _build_index(_sample_response())

    assert set(index) == {"Poacher Sighting", "test tag"}

    poacher = index["Poacher Sighting"]
    assert poacher.id == 29
    assert poacher.domain == "Wildlife"
    assert poacher.type_limiter == "Incident"
    assert set(poacher.fields) == {"Direction", "Number of People"}

    direction = poacher.field_by_name("Direction")
    assert direction is not None
    assert direction.id == 1327
    assert direction.data_type == "Lookup"
    assert len(direction.lookups) == 2


def test_build_index_handles_empty_response():
    assert _build_index([]) == {}
    assert _build_index(None) == {}


def test_build_index_skips_tags_with_no_name():
    response = [
        {
            "name": "X",
            "tags": [{"id": 1, "name": "", "typeLimiter": "Incident", "fields": []}],
        }
    ]
    assert _build_index(response) == {}


def test_build_index_skips_fields_with_no_name():
    response = [
        {
            "name": "X",
            "tags": [
                {
                    "id": 1,
                    "name": "Tag1",
                    "typeLimiter": "Incident",
                    "fields": [
                        {"id": 10, "name": "", "dataType": "String"},
                        {"id": 11, "name": "Known", "dataType": "String"},
                    ],
                }
            ],
        }
    ]
    index = _build_index(response)
    assert set(index["Tag1"].fields) == {"Known"}


def test_build_index_warns_on_tag_name_collision(caplog):
    response = [
        {
            "name": "DomainA",
            "tags": [
                {"id": 1, "name": "Collision", "typeLimiter": "Incident", "fields": []}
            ],
        },
        {
            "name": "DomainB",
            "tags": [
                {"id": 2, "name": "Collision", "typeLimiter": "Incident", "fields": []}
            ],
        },
    ]
    with caplog.at_level(logging.WARNING):
        index = _build_index(response)

    # Last-wins
    assert index["Collision"].id == 2
    assert index["Collision"].domain == "DomainB"
    assert any(
        "collision" in r.message.lower() and "DomainA" in r.message and "DomainB" in r.message
        for r in caplog.records
    )


# ----- TagIndex -----


def _make_client_with_get_tags(response):
    """Build a mock CmoreClient whose get_tags() returns the given response."""
    client = MagicMock()
    client.get_tags = AsyncMock(return_value=response)
    return client


@pytest.mark.asyncio
async def test_tag_index_get_returns_tag_info():
    idx = TagIndex()
    client = _make_client_with_get_tags(_sample_response())

    tag = await idx.get(client, "https://example/api", "int-1", "Poacher Sighting")
    assert tag is not None
    assert tag.id == 29
    assert tag.fields["Direction"].id == 1327


@pytest.mark.asyncio
async def test_tag_index_get_returns_none_for_unknown_tag():
    idx = TagIndex()
    client = _make_client_with_get_tags(_sample_response())

    tag = await idx.get(client, "https://example/api", "int-1", "Not A Real Tag")
    assert tag is None


@pytest.mark.asyncio
async def test_tag_index_calls_get_tags_only_once_per_integration():
    """Repeated lookups for the same (base_url, integration_id) should hit cache."""
    idx = TagIndex()
    client = _make_client_with_get_tags(_sample_response())

    await idx.get(client, "https://example/api", "int-1", "Poacher Sighting")
    await idx.get(client, "https://example/api", "int-1", "Poacher Sighting")
    await idx.get(client, "https://example/api", "int-1", "test tag")

    assert client.get_tags.await_count == 1


@pytest.mark.asyncio
async def test_tag_index_separates_caches_per_base_url():
    idx = TagIndex()
    client_a = _make_client_with_get_tags(_sample_response())
    client_b = _make_client_with_get_tags(
        [
            {
                "name": "Other",
                "tags": [
                    {
                        "id": 999,
                        "name": "B Only Tag",
                        "typeLimiter": "Incident",
                        "fields": [],
                    }
                ],
            }
        ]
    )

    a_tag = await idx.get(client_a, "https://a/api", "int-1", "Poacher Sighting")
    b_tag = await idx.get(client_b, "https://b/api", "int-2", "B Only Tag")

    assert a_tag.id == 29
    assert b_tag.id == 999
    # 'Poacher Sighting' shouldn't be reachable on b
    assert await idx.get(client_b, "https://b/api", "int-2", "Poacher Sighting") is None


@pytest.mark.asyncio
async def test_tag_index_separates_caches_per_integration_same_base_url():
    """Two integrations against the same CMORE may see different tag sets
    (per-ShareGroup visibility). The cache MUST not pool them under the same key."""
    idx = TagIndex()
    # Integration A sees nothing (e.g., a ShareGroup with no subscribed tags)
    client_low_visibility = _make_client_with_get_tags([])
    # Integration B sees the full Wildlife domain
    client_high_visibility = _make_client_with_get_tags(_sample_response())

    a_tag = await idx.get(client_low_visibility, "https://shared/api", "int-A", "Poacher Sighting")
    b_tag = await idx.get(client_high_visibility, "https://shared/api", "int-B", "Poacher Sighting")

    assert a_tag is None              # low-visibility integration: tag absent
    assert b_tag is not None           # high-visibility integration: tag present
    assert b_tag.id == 29
    # Each integration triggered its own get_tags() call.
    assert client_low_visibility.get_tags.await_count == 1
    assert client_high_visibility.get_tags.await_count == 1


@pytest.mark.asyncio
async def test_tag_index_reset_clears_cache():
    idx = TagIndex()
    client = _make_client_with_get_tags(_sample_response())

    await idx.get(client, "https://example/api", "int-1", "Poacher Sighting")
    idx._reset()
    await idx.get(client, "https://example/api", "int-1", "Poacher Sighting")

    # Cache was cleared, so get_tags called twice.
    assert client.get_tags.await_count == 2
