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
        value="abalone_harvesting", display="Abalone Harvesting", ordernum=1
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
    assert "Hoof Animal" in [o.display for o in animal.options]
    assert "hoof_animal" in [o.value for o in animal.options]
    assert "Buffalo" not in [o.display for o in animal.options]
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
    assert [o.ordernum for o in opts] == [1, 2] and len(opts[0].value) == 100 and len(opts[0].display) == 100


def test_option_values_are_slugs_and_collisions_get_the_lookup_id():
    from app.datasource.er_event_types import build_event_type_spec

    raw = {"id": 1, "name": "T", "fields": [{
        "id": 9, "name": "X", "dataType": "Lookup",
        "lookups": [{"id": 11, "value": "Sub-Adult", "order": 1}, {"id": 12, "value": "Sub Adult", "order": 2},
                    {"id": 13, "value": "Abalone Harvesting", "order": 3}],
    }]}
    opts = build_event_type_spec(raw, 1).fields[0].options
    assert [(o.value, o.display) for o in opts] == [
        ("sub_adult", "Sub-Adult"), ("sub_adult_12", "Sub Adult"), ("abalone_harvesting", "Abalone Harvesting"),
    ]


# ----- v2 payload and choices -----


def test_payload_envelope_matches_the_v2_shape():
    from app.datasource.er_event_types import build_event_type_payload, build_event_type_spec

    p = build_event_type_payload(build_event_type_spec(_tag(20), 7932))
    assert p["value"] == "cmore_7932_20_evidence_of_poacher" and p["display"] == "Evidence of Poacher"
    assert p["category"] == "cmore_7932" and p["is_active"] is True
    js, ui = p["schema"]["json"], p["schema"]["ui"]
    assert js["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert js["type"] == "object" and js["unevaluatedProperties"] is False
    assert ui["order"] == ["section-1"] and set(ui["fields"]) == set(js["properties"])
    assert ui["headers"] == {}
    assert ui["sections"]["section-1"]["leftColumn"][0] == {"name": "f261_evidence_type", "type": "field"}
    assert ui["sections"]["section-1"]["rightColumn"] == []


def test_scalar_and_choice_properties():
    from app.datasource.er_event_types import build_event_type_payload, build_event_type_spec

    p = build_event_type_payload(build_event_type_spec(_tag(20), 7932))
    props, ui = p["schema"]["json"]["properties"], p["schema"]["ui"]["fields"]
    assert props["f264_number_of_snares_found"] == {"type": "number", "title": "Number of Snares Found", "deprecated": False}
    assert ui["f264_number_of_snares_found"] == {"type": "NUMERIC", "parent": "section-1", "placeholder": "Number of snares found"}
    assert props["f261_evidence_type"] == {
        "type": "array", "title": "Evidence Type", "deprecated": False, "uniqueItems": True,
        "items": {"type": "string", "anyOf": [{"$ref": "/api/v2.0/schemas/choices.json?field=f261_evidence_type"}]},
    }
    assert ui["f261_evidence_type"] == {"type": "CHOICE_LIST", "inputType": "DROPDOWN", "parent": "section-1"}
    assert ui["f262_cas_number"] == {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1", "placeholder": "SAPS Crime Administration"}


def test_required_fields_land_in_required():
    from app.datasource.er_event_types import build_event_type_payload, build_event_type_spec

    raw = {"id": 1, "name": "T", "fields": [{"id": 9, "name": "X", "dataType": "String", "isMandatory": True}]}
    assert build_event_type_payload(build_event_type_spec(raw, 1))["schema"]["json"]["required"] == ["f9_x"]


def test_choice_records_use_the_existing_field_key_on_rerun():
    from app.datasource.er_event_types import build_choice_records, build_event_type_spec

    spec = build_event_type_spec(_tag(20), 7932)
    assert build_choice_records(spec, existing_keys={261: "f261_old_key"}).keys() == {"f261_old_key"}


def test_choice_records_shape():
    from app.datasource.er_event_types import build_choice_records, build_event_type_spec

    recs = build_choice_records(build_event_type_spec(_tag(20), 7932))["f261_evidence_type"]
    # EarthRanger's Choice.clean() only accepts ^\\w+$ values, so the value is
    # the slug and the display keeps the CMORE text.
    assert recs[0] == {"model": "activity.event", "field": "f261_evidence_type", "value": "abalone_harvesting",
                       "display": "Abalone Harvesting", "is_active": True, "ordernum": 1}


def _site_schema():
    """An Evidence of Poacher type as a reserve left it after editing: a
    shortened title, a hidden field, a conditional section, an extra field
    of their own, and CAS Number deleted."""
    return {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": {
                "f261_evidence_type": {"type": "array", "title": "Evidence", "deprecated": False,
                                       "uniqueItems": True, "items": {"type": "string", "anyOf": [{"$ref": "/api/v2.0/schemas/choices.json?field=f261_evidence_type"}]}},
                "f263_evidence_bag_no": {"type": "string", "title": "Evidence Bag No", "deprecated": True},
            },
            "required": [],
            # A conditional section's fields live in its then-subschema, not
            # at the root: that is how EarthRanger's form builder writes them.
            "allOf": [{"if": {"properties": {"f261_evidence_type": {"contains": {"const": "snare"}}}},
                       "then": {"properties": {
                           "f264_number_of_snares_found": {"type": "number", "title": "Snares", "deprecated": False},
                           "ranger_notes": {"type": "string", "title": "Ranger notes", "deprecated": False},
                       }, "required": ["ranger_notes"]}, "x-section": "section-2"}],
        },
        "ui": {
            "fields": {
                "f261_evidence_type": {"type": "CHOICE_LIST", "inputType": "DROPDOWN", "parent": "section-1"},
                "f263_evidence_bag_no": {"type": "TEXT", "inputType": "SHORT_TEXT", "parent": "section-1"},
                "f264_number_of_snares_found": {"type": "NUMERIC", "parent": "section-2"},
                "ranger_notes": {"type": "TEXT", "inputType": "LONG_TEXT", "parent": "section-2"},
            },
            "headers": {},
            "order": ["section-1", "section-2"],
            "sections": {
                "section-1": {"label": "Evidence", "columns": 1, "isActive": True,
                              "leftColumn": [{"name": "f261_evidence_type", "type": "field"}, {"name": "f263_evidence_bag_no", "type": "field"}],
                              "rightColumn": []},
                "section-2": {"label": "Snares", "columns": 1, "isActive": True,
                              "leftColumn": [{"name": "f264_number_of_snares_found", "type": "field"}, {"name": "ranger_notes", "type": "field"}],
                              "rightColumn": []},
            },
        },
    }


def test_merge_keeps_every_site_edit_and_adds_only_new_cmore_fields():
    import copy
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)  # fields 261..265 (260 Reported By skipped)
    site = _site_schema()
    existing_keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}
    known = {261, 262, 263, 264}  # 262 (CAS Number) was generated and the reserve deleted it; 265 is new in CMORE

    result = merge_event_type_schema(spec, site, existing_keys, known)
    merged, added, removed_by_site = result.schema, result.added, result.removed_by_site

    assert [f.cmore_field_id for f in added] == [265]
    assert [f.cmore_field_id for f in removed_by_site] == [262]
    before = _site_schema()
    for key in ("f261_evidence_type", "f263_evidence_bag_no"):
        assert merged["json"]["properties"][key] == before["json"]["properties"][key]
    for key in ("f261_evidence_type", "f263_evidence_bag_no", "f264_number_of_snares_found", "ranger_notes"):
        assert merged["ui"]["fields"][key] == before["ui"]["fields"][key]
    assert "f264_number_of_snares_found" not in merged["json"]["properties"]  # stays in its conditional section
    assert merged["json"]["allOf"] == before["json"]["allOf"]
    assert merged["ui"]["order"] == before["ui"]["order"]
    assert "f262_cas_number" not in merged["json"]["properties"]
    assert merged["json"]["properties"]["f265_sap_13"] == {"type": "string", "title": "SAP 13", "deprecated": False}
    # section-2 only shows for some answers, so the new field goes to the
    # last section that always shows.
    assert merged["ui"]["fields"]["f265_sap_13"]["parent"] == "section-1"
    assert merged["ui"]["sections"]["section-1"]["leftColumn"][-1] == {"name": "f265_sap_13", "type": "field"}
    assert merged["ui"]["sections"]["section-2"] == before["ui"]["sections"]["section-2"]
    assert merged["json"]["required"] == []

    assert site == before  # the input is not modified


def test_merge_adds_its_own_section_when_no_section_always_shows():
    from app.datasource.er_event_types import ADDED_SECTION_ID, build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    site["ui"]["sections"]["section-1"]["isActive"] = False  # switched off; section-2 is conditional
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}

    merged = merge_event_type_schema(spec, site, keys, {261, 262, 263, 264}).schema

    assert merged["ui"]["order"] == ["section-1", "section-2", ADDED_SECTION_ID]
    section = merged["ui"]["sections"][ADDED_SECTION_ID]
    assert section["isActive"] is True and section["label"] == "New from CMORE"
    assert section["leftColumn"] == [{"name": "f265_sap_13", "type": "field"}]
    assert merged["ui"]["fields"]["f265_sap_13"]["parent"] == ADDED_SECTION_ID

    # A later run reuses that section rather than adding another.
    # (262 is treated as new this time.)
    again = merge_event_type_schema(spec, merged, {**keys, 265: "f265_sap_13"}, {261, 263, 264, 265}).schema
    assert again["ui"]["order"].count(ADDED_SECTION_ID) == 1
    assert [c["name"] for c in again["ui"]["sections"][ADDED_SECTION_ID]["leftColumn"]] == ["f265_sap_13", "f262_cas_number"]


def test_merge_reuses_the_cmore_section_even_when_the_reserve_switched_it_off():
    """The reserve may hide "New from CMORE" too. A later run must append
    to it as it is, not re-create it active or list it twice."""
    from app.datasource.er_event_types import ADDED_SECTION_ID, build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    site["ui"]["sections"]["section-1"]["isActive"] = False
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}
    first = merge_event_type_schema(spec, site, keys, {261, 262, 263, 264}).schema
    first["ui"]["sections"][ADDED_SECTION_ID]["isActive"] = False

    again = merge_event_type_schema(spec, first, {**keys, 265: "f265_sap_13"}, {261, 263, 264, 265}).schema

    assert again["ui"]["order"] == ["section-1", "section-2", ADDED_SECTION_ID]
    section = again["ui"]["sections"][ADDED_SECTION_ID]
    assert section["isActive"] is False
    assert [c["name"] for c in section["leftColumn"]] == ["f265_sap_13", "f262_cas_number"]
    assert again["ui"]["fields"]["f262_cas_number"]["parent"] == ADDED_SECTION_ID


def test_merge_never_makes_an_added_field_required():
    """A required field the reserve hides or puts behind a condition would
    block every report; the reserve decides what is required."""
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    spec.fields = [f.copy(update={"required": True}) if f.cmore_field_id == 265 else f for f in spec.fields]
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}

    result = merge_event_type_schema(spec, _site_schema(), keys, {261, 262, 263, 264})

    assert [f.cmore_field_id for f in result.added] == [265]
    assert result.schema["json"]["required"] == []


def test_merge_with_nothing_new_returns_the_site_schema_unchanged():
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}
    result = merge_event_type_schema(spec, site, keys, {261, 262, 263, 264, 265})
    assert result.added == [] and result.schema == _site_schema()
    assert sorted(f.cmore_field_id for f in result.removed_by_site) == [262, 265]


@pytest.mark.parametrize("site", [
    None, {}, {"json": "{}"}, {"json": {"properties": "x"}},
    {"json": {"properties": {}}, "ui": {}},  # nothing on the site and no record: a rebuild, not a merge
    {"json": {"properties": {"f261_evidence_type": {}}}, "ui": None},
    {"json": {"properties": {"f261_evidence_type": {}}}, "ui": {"sections": None}},
])
def test_merge_refuses_a_site_schema_it_cannot_read(site):
    """Merging onto an empty form would rebuild it from CMORE and replace
    the reserve's; the caller must skip the type instead."""
    from app.datasource.er_event_types import SiteSchemaUnreadable, build_event_type_spec, merge_event_type_schema

    with pytest.raises(SiteSchemaUnreadable):
        merge_event_type_schema(build_event_type_spec(_tag(20), 7932), site, {}, None)


def test_merge_onto_an_empty_form_with_an_empty_record_adds_the_first_fields():
    """A type generated when its tag had no supported field has a valid
    empty form and a record of no fields. That record is not "no record":
    the fields CMORE adds later go in."""
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    result = merge_event_type_schema(spec, {"json": {"properties": {}}, "ui": {}}, {}, set())
    assert [f.cmore_field_id for f in result.added] == [f.cmore_field_id for f in spec.fields]
    assert result.removed_by_site == []


def test_merge_onto_a_form_the_reserve_emptied_adds_only_the_new_field():
    """The reserve deleted every generated field; the record still knows
    them, so a field new in CMORE is added and the rest stay deleted."""
    from app.datasource.er_event_types import SECTION_ID, build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = {"json": {"properties": {}}, "ui": {"order": [SECTION_ID], "sections": {SECTION_ID: {"label": "Evidence", "leftColumn": []}}}}
    result = merge_event_type_schema(spec, site, {}, {261, 262, 263, 264})
    assert [f.cmore_field_id for f in result.added] == [265]
    assert sorted(f.cmore_field_id for f in result.removed_by_site) == [261, 262, 263, 264]
    assert result.schema["ui"]["sections"][SECTION_ID]["leftColumn"] == [{"name": "f265_sap_13", "type": "field"}]


def test_site_field_keys_include_fields_in_conditional_sections():
    from app.datasource.er_event_types import site_field_keys

    assert site_field_keys(_site_schema()) == {
        261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found",
    }
    assert site_field_keys({"json": {"properties": {}, "allOf": [{"then": {"properties": {"f9_x": {}}}}, "bad"]}}) == {9: "f9_x"}
    assert site_field_keys({"json": "x"}) == {}


def test_merge_leaves_a_generated_field_the_reserve_moved_into_a_conditional_section_alone():
    """Such a field is not at the root. It is on the site all the same: not
    added again, not reported as removed."""
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema, site_field_keys

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    result = merge_event_type_schema(spec, site, site_field_keys(site), {261, 262, 263, 264, 265})
    assert result.added == []
    assert [f.cmore_field_id for f in result.removed_by_site] == [262, 265]


def test_merge_does_not_add_a_validation_policy_the_reserve_removed():
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema, site_field_keys

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    del site["json"]["unevaluatedProperties"]
    merged = merge_event_type_schema(spec, site, site_field_keys(site), {261, 262, 263, 264}).schema
    assert [f.key for f in spec.fields if f.key in merged["json"]["properties"]] == ["f261_evidence_type", "f263_evidence_bag_no", "f265_sap_13"]
    assert "unevaluatedProperties" not in merged["json"]


def test_merge_onto_a_form_whose_fields_are_all_conditional():
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema, site_field_keys

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    for key in ("f261_evidence_type", "f263_evidence_bag_no"):
        site["json"]["allOf"][0]["then"]["properties"][key] = site["json"]["properties"].pop(key)
    result = merge_event_type_schema(spec, site, site_field_keys(site), set())
    assert [f.cmore_field_id for f in result.added] == [262, 265]
    assert result.schema["ui"]["fields"]["f262_cas_number"]["parent"] == "section-1"


def test_merge_keeps_an_existing_section_when_the_order_is_empty():
    """A form whose ui.order is empty still holds the reserve's sections;
    the merge must not replace one with a fresh section of the same id."""
    from app.datasource.er_event_types import SECTION_ID, build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    site["ui"]["order"] = []
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}
    merged = merge_event_type_schema(spec, site, keys, {261, 262, 263, 264}).schema
    section = merged["ui"]["sections"][SECTION_ID]
    assert section["label"] == "Evidence"
    assert [c["name"] for c in section["leftColumn"]] == ["f261_evidence_type", "f263_evidence_bag_no", "f265_sap_13"]
    assert merged["ui"]["order"] == [SECTION_ID]


def test_merge_with_nothing_new_hands_back_the_site_schema_itself():
    from app.datasource.er_event_types import build_event_type_spec, merge_event_type_schema

    spec = build_event_type_spec(_tag(20), 7932)
    site = _site_schema()
    keys = {261: "f261_evidence_type", 263: "f263_evidence_bag_no", 264: "f264_number_of_snares_found"}
    assert merge_event_type_schema(spec, site, keys, {261, 262, 263, 264, 265}).schema is site


def test_plan_choice_additions_only_adds_and_reports_stale():
    from app.datasource.er_event_types import plan_choice_additions

    desired = [{"model": "activity.event", "field": "f", "value": "a", "display": "A", "is_active": True, "ordernum": 1},
               {"model": "activity.event", "field": "f", "value": "b", "display": "B", "is_active": True, "ordernum": 2}]
    existing = [{"id": "1", "value": "a", "display": "A, shortened by the reserve", "is_active": True, "ordernum": 9},
                {"id": "3", "value": "c", "display": "C", "is_active": True, "ordernum": 3},
                {"id": "4", "value": "d", "display": "D", "is_active": False, "ordernum": 4},
                {"id": "5", "display": "no value", "is_active": True, "ordernum": 5}]
    plan = plan_choice_additions(existing, desired)
    creates, stale = plan.creates, plan.stale
    assert creates == [desired[1]]
    assert stale == ["c"]  # active, not in CMORE; inactive extras are not reported
    assert plan_choice_additions([], desired).dict() == {"creates": desired, "stale": []}


