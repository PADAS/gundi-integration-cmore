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

    assert set(index.by_name) == {"Poacher Sighting", "test tag"}

    poacher = index.by_name["Poacher Sighting"]
    assert poacher.id == 29
    assert poacher.domain == "Wildlife"
    assert poacher.type_limiter == "Incident"
    assert set(poacher.fields_by_name) == {"Direction", "Number of People"}
    assert index.by_id[29] is poacher

    direction = poacher.resolve_field("Direction")
    assert direction is not None
    assert direction.id == 1327
    assert direction.data_type == "Lookup"
    assert len(direction.lookups) == 2


def test_build_index_handles_empty_response():
    assert _build_index([]).by_id == {} and _build_index([]).by_name == {}
    assert _build_index(None).by_id == {} and _build_index(None).by_name == {}


def test_build_index_skips_tags_with_no_name():
    response = [
        {
            "name": "X",
            "tags": [{"id": 1, "name": "", "typeLimiter": "Incident", "fields": []}],
        }
    ]
    assert _build_index(response).by_name == {}
    assert _build_index(response).by_id == {}


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
    assert set(index.by_name["Tag1"].fields_by_name) == {"Known"}


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
    assert index.by_name["Collision"].id == 2
    assert index.by_name["Collision"].domain == "DomainB"
    assert any(
        "collision" in r.message.lower() and "DomainA" in r.message and "DomainB" in r.message
        for r in caplog.records
    )


# ----- ID-or-name resolution -----


def test_resolve_by_id_and_by_name():
    index = _build_index(_sample_response())
    assert index.resolve("29").name == "Poacher Sighting"
    assert index.resolve(" 29 ").name == "Poacher Sighting"  # whitespace stripped
    assert index.resolve("Poacher Sighting").id == 29
    assert index.resolve("9999") is None
    assert index.resolve("Not A Real Tag") is None


def test_resolve_digit_ref_with_no_matching_id_falls_back_to_name():
    response = [
        {"name": "X", "tags": [
            {"id": 5, "name": "8443", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("8443").id == 5  # no tag has id 8443 → name match


def test_resolve_id_beats_all_digit_name():
    response = [
        {"name": "X", "tags": [
            {"id": 7, "name": "8443", "typeLimiter": "Incident", "fields": []},
            {"id": 8443, "name": "Real Tag", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("8443").name == "Real Tag"  # documented precedence
    assert index.resolve("7").name == "8443"          # the digit-named tag via its own id


def test_resolve_field_by_id_and_by_name():
    poacher = _build_index(_sample_response()).resolve("Poacher Sighting")
    assert poacher.resolve_field("1327").name == "Direction"
    assert poacher.resolve_field("Direction").id == 1327
    assert poacher.resolve_field("9999") is None
    assert poacher.resolve_field("Nope") is None


def test_by_id_keeps_both_tags_on_name_collision():
    response = [
        {"name": "DomainA", "tags": [
            {"id": 1, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
        {"name": "DomainB", "tags": [
            {"id": 2, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("1").domain == "DomainA"   # both reachable by id
    assert index.resolve("2").domain == "DomainB"
    assert index.resolve("Collision").id == 2       # name view stays last-wins


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
    assert tag.fields_by_name["Direction"].id == 1327
    assert (await idx.get(client, "https://example/api", "int-1", "29")).id == 29


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


@pytest.mark.asyncio
async def test_id_mapping_survives_tag_rename():
    """The CSIR scenario: a tag renamed in CMORE. An id-based mapping keeps
    resolving; the old name (a name-based mapping) stops — the failure mode
    this change eliminates."""
    idx = TagIndex()
    renamed = _sample_response()
    renamed[0]["tags"][0]["name"] = "Poacher Sighting (Legacy)"
    client = _make_client_with_get_tags(renamed)

    tag = await idx.get(client, "https://example/api", "int-1", "29")
    assert tag is not None and tag.id == 29 and tag.name == "Poacher Sighting (Legacy)"
    assert await idx.get(client, "https://example/api", "int-1", "Poacher Sighting") is None
