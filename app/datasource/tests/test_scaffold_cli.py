"""Offline tests for the scaffold-mapping CLI (no Gundi / live services).

Exercises the file-input path and the pure merge helper.
"""

import json
import os

from click.testing import CliRunner

from app.datasource.cli import cli, merge_event_type_mapping

_REAL_SCHEMA = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "docs", "rhino_carcass_schema_from_api.json"
)


def test_merge_event_type_mapping_replaces_same_event_type():
    existing = {"event_type_to_tag": [
        {"event_type": "rhino_carcass", "tag_name": "OLD"},
        {"event_type": "shot", "tag_name": "Shot"},
    ]}
    entry = {"event_type": "rhino_carcass", "tag_name": "Rhino Carcass", "field_mappings": []}
    merged = merge_event_type_mapping(existing, entry)
    rhino = [m for m in merged["event_type_to_tag"] if m["event_type"] == "rhino_carcass"]
    assert len(rhino) == 1 and rhino[0]["tag_name"] == "Rhino Carcass"
    # The unrelated mapping is preserved; input is not mutated.
    assert any(m["event_type"] == "shot" for m in merged["event_type_to_tag"])
    assert existing["event_type_to_tag"][0]["tag_name"] == "OLD"


def test_merge_into_empty_deliver_data():
    entry = {"event_type": "rhino_carcass", "tag_name": "Rhino Carcass", "field_mappings": []}
    assert merge_event_type_mapping({}, entry)["event_type_to_tag"] == [entry]


# Minimal CMORE get-tags dump with the Wildlife "Rhino Carcass" tag.
_TAGS = [{
    "id": 8, "name": "Wildlife", "tags": [{
        "id": 26, "name": "Rhino Carcass", "typeLimiter": "Incident",
        "fields": [
            {"id": 1261, "name": "Animal Sex", "dataType": "Lookup", "allowMultipleValues": False,
             "lookups": [{"id": 1, "value": "Male"}, {"id": 2, "value": "Female"}]},
            {"id": 1260, "name": "Animal Age", "dataType": "Lookup", "allowMultipleValues": False,
             "lookups": [{"id": 1, "value": "Adult"}, {"id": 2, "value": "Calf"}]},
        ],
    }],
}]

def test_scaffold_mapping_offline_end_to_end(tmp_path):
    """Run the CLI against the REAL ER schema dump + a minimal Wildlife tag."""
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps(_TAGS))
    out_file = tmp_path / "mapping.json"

    runner = CliRunner()
    result = runner.invoke(cli, [
        "scaffold-mapping",
        "--event-type", "rhino_carcass",
        "--tag", "Rhino Carcass",
        "--tags-file", str(tags_file),
        "--er-schema-file", _REAL_SCHEMA,
        "--out", str(out_file),
        "--non-interactive",
    ])
    assert result.exit_code == 0, result.output

    entry = json.loads(out_file.read_text())
    assert entry["event_type"] == "rhino_carcass"
    assert entry["tag_name"] == "Rhino Carcass"

    by_field = {fm["cmore_field_name"]: fm for fm in entry["field_mappings"]}
    # animal_sex: female/male auto-resolve (display match); only 'Unknown' is left.
    sex_maps = by_field["Animal Sex"].get("value_mappings", [])
    assert sex_maps == [{"from_value": "Unknown", "to_value": ""}]
    # age_of_animal: none of ER's six buckets match Adult/Calf → all blank.
    age_maps = by_field["Animal Age"]["value_mappings"]
    assert {"from_value": "b_3_months1_year", "to_value": ""} in age_maps
    assert len(age_maps) == 6
