"""Tests for the CMORE tag → EarthRanger event type builders."""

import json
import pathlib

import pytest

from app.datasource.er_event_types import (
    category_value,
    event_type_value,
    field_key,
    field_key_prefix,
    generated_type_prefix,
    parse_generated_event_type,
    parse_generated_field_key,
    slugify,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "cmore_tags_sample.json"


def _tag(tag_id: int) -> dict:
    for domain in json.loads(FIXTURE.read_text()):
        for tag in domain.get("tags") or []:
            if tag["id"] == tag_id:
                return tag
    raise KeyError(tag_id)


# ----- naming convention -----


def test_slugify_collapses_runs_and_strips_edges():
    assert slugify("Evidence of Poacher") == "evidence_of_poacher"
    assert slugify("  K9 / Deployment!! ") == "k9_deployment"
    assert slugify("???") == ""


def test_category_value_carries_the_group():
    assert category_value(7932) == "cmore_7932"


def test_event_type_value_carries_group_tag_and_slug():
    assert event_type_value(7932, 20, "Evidence of Poacher") == "cmore_7932_20_evidence_of_poacher"


def test_event_type_value_with_empty_slug_has_no_trailing_underscore():
    assert event_type_value(7932, 20, "???") == "cmore_7932_20"


def test_event_type_value_is_truncated_to_255():
    assert len(event_type_value(7932, 20, "x" * 300)) == 255


def test_field_key_fits_the_choice_field_limit():
    assert field_key(261, "Evidence Type") == "f261_evidence_type"
    assert len(field_key(261, "a very long field name that keeps going on and on")) == 40


def test_parse_generated_event_type_matches_only_this_group():
    assert parse_generated_event_type("cmore_7932_20_evidence_of_poacher", 7932) == 20
    assert parse_generated_event_type("cmore_7932_20", 7932) == 20
    assert parse_generated_event_type("cmore_7933_20_evidence_of_poacher", 7932) is None
    assert parse_generated_event_type("rhino_carcass", 7932) is None
    assert parse_generated_event_type("cmore_7932_x_bad", 7932) is None


def test_parse_generated_field_key():
    assert parse_generated_field_key("f261_evidence_type") == 261
    assert parse_generated_field_key("species") is None
    assert parse_generated_field_key("f_261") is None


def test_prefixes():
    assert generated_type_prefix(7932, 20) == "cmore_7932_20_"
    assert field_key_prefix(261) == "f261_"
