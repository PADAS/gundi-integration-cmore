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


# ----- event type spec -----


def test_spec_for_evidence_of_poacher():
    from app.datasource.er_event_types import ChoiceOption, build_event_type_spec

    spec = build_event_type_spec(_tag(20), 7932)
    assert spec.value == "cmore_7932_20_evidence_of_poacher"
    assert spec.display == "Evidence of Poacher"
    assert spec.category_value == "cmore_7932"
    assert spec.cmore_tag_id == 20
    keys = [f.key for f in spec.fields]
    assert "f260_reported_by" not in keys  # filled from the ER reporter
    assert [s.name for s in spec.skipped] == ["Reported By"]
    by_key = {f.key: f for f in spec.fields}
    assert by_key["f261_evidence_type"].type == "multiselect"  # allowMultipleValues on a Lookup
    assert by_key["f261_evidence_type"].cmore_field_id == 261
    assert by_key["f261_evidence_type"].options[0] == ChoiceOption(
        value="Abalone Harvesting", display="Abalone Harvesting", ordernum=1
    )
    assert by_key["f264_number_of_snares_found"].type == "number"
    assert by_key["f264_number_of_snares_found"].hint == "Number of snares found"
    assert by_key["f262_cas_number"].type == "string"
    assert spec.shallow_fields == []


def test_type_translation_table():
    from app.datasource.er_event_types import build_event_type_spec

    def one(data_type, allow_multiple=False):
        raw = {"id": 1, "name": "T", "fields": [{
            "id": 9, "name": "X", "dataType": data_type, "allowMultipleValues": allow_multiple,
            "lookups": [{"id": 1, "value": "a", "order": 1}],
        }]}
        spec = build_event_type_spec(raw, 1)
        return spec.fields[0].type if spec.fields else [s.reason for s in spec.skipped]

    assert one("String") == "string" and one("IDNumber") == "string"
    assert one("Text") == "textarea"
    assert one("Number") == "number" and one("Duration") == "number"
    assert one("Boolean") == "boolean"
    assert one("DateTime") == "datetime"
    assert one("Lookup") == "select" and one("FixedLookup") == "select"
    assert one("Lookup", allow_multiple=True) == "multiselect"
    assert one("Media") == ["unsupported data type Media"]
    assert one("Message") == ["unsupported data type Message"]


def test_mandatory_hint_truncation_and_required():
    from app.datasource.er_event_types import build_event_type_spec

    raw = {"id": 1, "name": "T", "fields": [
        {"id": 9, "name": "X", "dataType": "String", "isMandatory": True, "hint": "h" * 40},
    ]}
    f = build_event_type_spec(raw, 1).fields[0]
    assert f.required is True and len(f.hint) == 32


def test_nested_options_emit_top_level_only_and_report_shallow():
    from app.datasource.er_event_types import build_event_type_spec

    spec = build_event_type_spec(_tag(43), 7932)  # Poached Animal
    animal = next(f for f in spec.fields if f.label == "Animal")
    assert all(o.value for o in animal.options)
    assert "Hoof Animal" in [o.value for o in animal.options]
    assert "Buffalo" not in [o.value for o in animal.options]
    shallow = next(s for s in spec.shallow_fields if s.name == "Animal")
    assert shallow.cmore_field_id == 1478 and shallow.nested_option_count > 0


def test_fields_with_colliding_slugs_keep_distinct_keys():
    from app.datasource.er_event_types import build_event_type_spec

    raw = {"id": 1, "name": "T", "fields": [
        {"id": 1, "name": "Count", "dataType": "Number"},
        {"id": 2, "name": "count", "dataType": "Number"},
    ]}
    assert [f.key for f in build_event_type_spec(raw, 1).fields] == ["f1_count", "f2_count"]


def test_options_are_ordered_by_cmore_order_and_truncated():
    from app.datasource.er_event_types import build_event_type_spec

    raw = {"id": 1, "name": "T", "fields": [{
        "id": 9, "name": "X", "dataType": "Lookup",
        "lookups": [{"id": 2, "value": "b", "order": 2}, {"id": 1, "value": "x" * 120, "order": 1}],
    }]}
    opts = build_event_type_spec(raw, 1).fields[0].options
    assert [o.ordernum for o in opts] == [1, 2] and len(opts[0].value) == 100
