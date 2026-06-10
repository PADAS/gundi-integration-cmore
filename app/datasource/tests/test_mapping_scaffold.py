"""Tests for the ER→CMORE mapping scaffold, grounded in the real ER
rhino_carcass event-type schema and the CMORE Wildlife 'Rhino Carcass' tag."""

import json
import os

from app.datasource.er_schema import ERChoice, ERField, parse_er_event_schema

_REAL_SCHEMA = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "docs", "rhino_carcass_schema_from_api.json"
)
from app.datasource.mapping_scaffold import (
    build_scaffold,
    suggest_cmore_field,
    suggest_lookup_value,
)
from app.datasource.tag_index import FieldInfo, TagInfo


def _lookup(*values):
    return [{"id": i, "value": v} for i, v in enumerate(values, start=1)]


# --- ER schema parsing -----------------------------------------------------


def test_parse_er_schema_enumnames_dict():
    raw = {
        "schema": {
            "properties": {
                "animal_sex": {
                    "type": "string",
                    "title": "Animal Sex",
                    "enum": ["male", "female"],
                    "enumNames": {"male": "Male", "female": "Female"},
                },
                "animal_id": {"type": "string", "title": "Animal ID"},
            }
        }
    }
    fields = parse_er_event_schema(raw)
    assert [f.key for f in fields] == ["animal_sex", "animal_id"]
    sex = fields[0]
    assert sex.is_enum
    assert sex.choices == [ERChoice("male", "Male"), ERChoice("female", "Female")]
    assert fields[1].is_enum is False  # free-text


def test_parse_er_schema_handles_double_nested_and_list_enumnames():
    raw = {"data": {"schema": {"schema": {"properties": {
        "x": {"title": "X", "enum": ["a", "b"], "enumNames": ["Alpha", "Beta"]},
    }}}}}
    fields = parse_er_event_schema(raw)
    assert fields[0].choices == [ERChoice("a", "Alpha"), ERChoice("b", "Beta")]


def test_parse_er_schema_empty_when_no_properties():
    assert parse_er_event_schema({"nonsense": True}) == []


def test_parse_real_er_api_schema_inline_enum():
    """The real ER v2 schema (pre_render + s_format=enum) wraps each choice
    field's values under anyOf[0] as enum + x-enumExtra, inside a top-level
    'json' envelope. Parse it end-to-end."""
    with open(_REAL_SCHEMA) as fh:
        raw = json.load(fh)
    fields = {f.key: f for f in parse_er_event_schema(raw)}

    # animal_id is free-text (no choices).
    assert fields["animal_id"].is_enum is False

    # animal_sex resolves to value/display pairs from x-enumExtra.
    sex = fields["animal_sex"]
    assert ERChoice("female", "Female") in sex.choices
    assert ERChoice("male", "Male") in sex.choices

    # age_of_animal carries all six ER buckets with their display labels.
    age = {c.value: c.display for c in fields["age_of_animal"].choices}
    assert age["b_3_months1_year"] == "B: 3 Months - 1 Year"
    assert len(age) == 6

    # animal_common_name uses the value as display ("Black Rhino").
    species = {c.value for c in fields["animal_common_name"].choices}
    assert species == {"Black Rhino", "White Rhino"}


# --- field matching --------------------------------------------------------


def _rhino_tag():
    return TagInfo(
        id=26, name="Rhino Carcass", domain="Wildlife", type_limiter="Incident",
        fields={
            "Rhino Spesies": FieldInfo(id=294, name="Rhino Spesies", data_type="Lookup",
                                       lookups=_lookup("White", "Black")),
            "Animal Sex": FieldInfo(id=1261, name="Animal Sex", data_type="Lookup",
                                    lookups=_lookup("Male", "Female", "Indeterminable")),
            "Animal Age": FieldInfo(id=1260, name="Animal Age", data_type="Lookup",
                                    lookups=_lookup("Adult", "Sub-Adult", "Calf")),
            "Carcass Age": FieldInfo(id=1262, name="Carcass Age", data_type="Lookup",
                                     lookups=_lookup("Today", "Fresh (less than 3 days)")),
            "Kill Type": FieldInfo(id=1263, name="Kill Type", data_type="Lookup",
                                   lookups=_lookup("Darted", "Poisoned", "Shot", "Snare", "Spear")),
            "Skull Tag Number": FieldInfo(id=1278, name="Skull Tag Number", data_type="String"),
        },
    )


def test_token_set_matching_handles_word_order():
    tag = _rhino_tag()
    # "age_of_animal" should match "Animal Age" despite reversed word order.
    f, score = suggest_cmore_field(ERField(key="age_of_animal", title="Age Of Animal"), tag)
    assert f is not None and f.name == "Animal Age"
    # "animal_sex" matches "Animal Sex" exactly.
    f2, _ = suggest_cmore_field(ERField(key="animal_sex", title="Animal Sex"), tag)
    assert f2.name == "Animal Sex"


def test_unrelated_field_has_no_match():
    tag = _rhino_tag()
    # "cause_of_death" shares no tokens with "Kill Type" → no match.
    f, score = suggest_cmore_field(ERField(key="cause_of_death", title="Cause Of Death"), tag)
    assert f is None


def test_suggest_lookup_value_matches_value_or_display():
    sex = FieldInfo(id=1261, name="Animal Sex", data_type="Lookup",
                    lookups=_lookup("Male", "Female", "Indeterminable"))
    assert suggest_lookup_value(ERChoice("male", "Male"), sex) == "Male"
    assert suggest_lookup_value(ERChoice("f", "Female"), sex) == "Female"  # via display
    assert suggest_lookup_value(ERChoice("unknown", "Unknown"), sex) is None


# --- end-to-end scaffold on the real rhino_carcass shape -------------------


def _rhino_er_fields():
    return [
        ERField("animal_id", "Animal ID"),  # free-text → String passthrough
        ERField("animal_sex", "Animal Sex",
                [ERChoice("male", "Male"), ERChoice("female", "Female")]),
        ERField("age_of_animal", "Age Of Animal",
                [ERChoice("b_3_months1_year", "Between 3 months and 1 year"),
                 ERChoice("adult", "Adult")]),
        ERField("cause_of_death", "Cause Of Death",
                [ERChoice("fence", "Fence"), ERChoice("shot", "Shot")]),
    ]


def test_build_scaffold_on_rhino_carcass():
    result = build_scaffold(_rhino_er_fields(), _rhino_tag(), event_type="rhino_carcass")

    by_key = {f.event_details_key: f for f in result.fields}

    # animal_sex → Animal Sex; both choices auto-resolve, so NO value_mappings.
    assert by_key["animal_sex"].cmore_field_name == "Animal Sex"
    assert by_key["animal_sex"].value_mappings == []

    # age_of_animal → Animal Age; 'adult' auto-resolves (omitted), the bucket is
    # unresolved → blank to_value for the operator to fill.
    age = by_key["age_of_animal"]
    assert age.cmore_field_name == "Animal Age"
    assert {"from_value": "b_3_months1_year", "to_value": ""} in age.value_mappings
    assert "b_3_months1_year" in age.unresolved_choices
    assert all(vm["from_value"] != "adult" for vm in age.value_mappings)  # freebie omitted

    # animal_id → Skull Tag Number (token match on 'number'? no — falls to no match
    # actually animal_id shares no tokens with Skull Tag Number) → unmatched.
    assert "animal_id" in result.unmatched_er_fields

    # cause_of_death has no CMORE field match.
    assert "cause_of_death" in result.unmatched_er_fields

    # CMORE fields with no ER counterpart are reported.
    assert "Rhino Spesies" in result.uncovered_cmore_fields

    # The rendered config entry is CmoreTagMapping-shaped.
    entry = result.to_config_entry()
    assert entry["event_type"] == "rhino_carcass"
    assert entry["tag_name"] == "Rhino Carcass"
    assert any(fm["cmore_field_name"] == "Animal Sex" for fm in entry["field_mappings"])
