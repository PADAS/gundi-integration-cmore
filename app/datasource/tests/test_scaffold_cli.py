"""Offline tests for the scaffold-mapping CLI (no Gundi / live services).

Exercises the file-input path and the pure merge helper.
"""

import json
import os

import pytest
from click.testing import CliRunner

from types import SimpleNamespace

import app.datasource.cli as cli_module
from app.datasource.cli import (
    _choose,
    _extract_auth_data,
    _find_action_config,
    cli,
    merge_event_type_mapping,
)

_REAL_SCHEMA = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "docs", "rhino_carcass_schema_from_api.json"
)


def _fake_integration():
    """An Integration-like object with auth + push action configs."""
    return SimpleNamespace(
        base_url="https://cmorewc1.chpc.ac.za",  # top-level base lacks the API path
        configurations=[
            SimpleNamespace(
                id="auth-cfg",
                action=SimpleNamespace(value="auth"),
                data={"base_url": "https://cmorewc1.chpc.ac.za/za/WebAPI/api", "token": "secret"},
            ),
            SimpleNamespace(
                id="push-cfg",
                action=SimpleNamespace(value="push_events"),
                data={"event_type_to_tag": []},
            ),
        ],
    )


def test_extract_auth_data_returns_full_api_base_and_token():
    """The CMORE API base + token come from the auth config, not the
    integration's top-level base_url (which omits the API path)."""
    auth = _extract_auth_data(_fake_integration())
    assert auth["base_url"] == "https://cmorewc1.chpc.ac.za/za/WebAPI/api"
    assert auth["token"] == "secret"


def test_find_action_config_locates_push_config():
    config_id, data = _find_action_config(_fake_integration(), ("push_events", "deliver", "push"))
    assert config_id == "push-cfg"
    assert data == {"event_type_to_tag": []}


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

_INTERACTIVE_TAGS = [{"id": 8, "name": "Wildlife", "tags": [{
    "id": 26, "name": "Rhino Carcass", "typeLimiter": "Incident", "fields": [
        {"id": 1, "name": "Animal Sex", "dataType": "Lookup", "allowMultipleValues": False,
         "lookups": [{"id": 1, "value": "Male"}, {"id": 2, "value": "Female"}, {"id": 3, "value": "Indeterminable"}]},
        {"id": 2, "name": "Rhino Spesies", "dataType": "Lookup", "allowMultipleValues": False,
         "lookups": [{"id": 4, "value": "White"}, {"id": 5, "value": "Black"}]},
    ],
}]}]

# 'animal_sex' matches "Animal Sex"; 'species' shares no tokens with "Rhino
# Spesies" → unmatched → exercised via the numbered field picker.
_INTERACTIVE_SCHEMA = {"json": {"properties": {
    "animal_sex": {"title": "Animal Sex", "type": "string", "anyOf": [
        {"enum": ["male", "female", "unknown"],
         "x-enumExtra": {"male": {"display": "Male"}, "female": {"display": "Female"},
                         "unknown": {"display": "Unknown"}}}]},
    "species": {"title": "Species", "type": "string", "anyOf": [
        {"enum": ["bw", "wt"],
         "x-enumExtra": {"bw": {"display": "Black Rhino"}, "wt": {"display": "White Rhino"}}}]},
}}}


def test_interactive_picker_and_value_fill(tmp_path):
    """Numbered field picker wires an unmatched lookup field, then value
    prompts resolve each source value by number."""
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps(_INTERACTIVE_TAGS))
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(_INTERACTIVE_SCHEMA))
    out_file = tmp_path / "mapping.json"

    # Prompts in order: pick CMORE field for 'species' (1=Rhino Spesies);
    # Animal Sex 'unknown' → 3 (Indeterminable); Rhino Spesies 'bw' → 2 (Black),
    # 'wt' → 1 (White).
    runner = CliRunner()
    result = runner.invoke(cli, [
        "scaffold-mapping", "--event-type", "rhino_carcass", "--tag", "Rhino Carcass",
        "--tags-file", str(tags_file), "--er-schema-file", str(schema_file),
        "--out", str(out_file),
    ], input="1\n3\n2\n1\n")
    assert result.exit_code == 0, result.output

    by_field = {fm["cmore_field_name"]: fm for fm in json.loads(out_file.read_text())["field_mappings"]}
    # male/female auto-resolve (display match) → only 'unknown' mapped.
    assert by_field["Animal Sex"]["value_mappings"] == [{"from_value": "unknown", "to_value": "Indeterminable"}]
    # The picker wired 'species' → Rhino Spesies; both values filled by number.
    species = by_field["Rhino Spesies"]["value_mappings"]
    assert {"from_value": "bw", "to_value": "Black"} in species
    assert {"from_value": "wt", "to_value": "White"} in species


@pytest.mark.asyncio
async def test_choose_fallback_number_default_skip_quit(mocker):
    """Numbered-fallback _choose: number selects, Enter keeps the default (or
    skips when no default), and 'q' aborts."""
    mocker.patch("sys.stdin.isatty", return_value=False)  # force numbered fallback

    mocker.patch.object(cli_module.click, "prompt", return_value="2")
    assert await _choose("m", ["A", "B", "C"], skip_label="skip") == "B"

    # Enter (empty) keeps the pre-selected current/default.
    mocker.patch.object(cli_module.click, "prompt", return_value="")
    assert await _choose("m", ["A", "B", "C"], skip_label="skip", default="C") == "C"

    # Enter with no default → skip (None).
    assert await _choose("m", ["A", "B"], skip_label="skip") is None

    # 'n' advances to the next field when skip_all_label is offered.
    mocker.patch.object(cli_module.click, "prompt", return_value="n")
    assert await _choose("m", ["A"], skip_label="skip", skip_all_label="next field") is cli_module._SKIP_ALL
    # ...but 'n' is not special unless skip_all_label is set (treated as a value).
    assert await _choose("m", ["A"], skip_label="skip", allow_free_text=True) == "n"

    # 'b' goes back when back_label is offered.
    mocker.patch.object(cli_module.click, "prompt", return_value="b")
    assert await _choose("m", ["A"], skip_label="skip", back_label="back") is cli_module._BACK

    # 'q' quits the wizard.
    mocker.patch.object(cli_module.click, "prompt", return_value="q")
    with pytest.raises(cli_module.click.Abort):
        await _choose("m", ["A"], skip_label="skip")


@pytest.mark.asyncio
async def test_interactive_fill_keeps_existing_mapping_as_default(mocker):
    """When an existing mapping is supplied, its value is pre-selected and a
    bare Enter keeps it (rather than dropping the value)."""
    from app.datasource.cli import _interactive_fill
    from app.datasource.er_schema import ERChoice, ERField
    from app.datasource.mapping_scaffold import FieldScaffold, ScaffoldResult
    from app.datasource.tag_index import FieldInfo, TagInfo

    mocker.patch("sys.stdin.isatty", return_value=False)        # numbered fallback
    mocker.patch.object(cli_module.click, "prompt", return_value="")  # Enter = keep default

    tag_info = TagInfo(
        id=26, name="Rhino Carcass", domain="Wildlife", type_limiter="Incident",
        fields={"Animal Age": FieldInfo(
            id=1260, name="Animal Age", data_type="Lookup",
            lookups=[{"id": 1, "value": "Adult"}, {"id": 2, "value": "Sub-Adult"}, {"id": 3, "value": "Calf"}],
        )},
    )
    er_fields = [ERField("age_of_animal", "Age Of Animal",
                         [ERChoice("b_3_months1_year", "B: 3 Months - 1 Year")])]
    result = ScaffoldResult(
        event_type="rhino_carcass", tag_name="Rhino Carcass",
        fields=[FieldScaffold(
            event_details_key="age_of_animal", cmore_field_name="Animal Age",
            value_mappings=[{"from_value": "b_3_months1_year", "to_value": ""}],
        )],
    )
    existing_entry = {"field_mappings": [{
        "event_details_key": "age_of_animal", "cmore_field_name": "Animal Age",
        "value_mappings": [{"from_value": "b_3_months1_year", "to_value": "Calf"}],
    }]}

    await _interactive_fill(result, tag_info, er_fields, existing_entry)

    # Enter kept the existing 'Calf' mapping rather than dropping it.
    assert result.fields[0].value_mappings == [{"from_value": "b_3_months1_year", "to_value": "Calf"}]


def test_interactive_next_and_back_navigation(tmp_path):
    """'n' advances to the next field; 'b' returns to the previous one to edit."""
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps(_INTERACTIVE_TAGS))
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(_INTERACTIVE_SCHEMA))
    out_file = tmp_path / "mapping.json"

    # Phase 1: species → Rhino Spesies (1). Phase 2 fields: [Animal Sex, Rhino
    # Spesies]. At Animal Sex: 'n' (skip ahead). At Rhino Spesies: 'b' (back).
    # Back at Animal Sex: '3' (Indeterminable). At Rhino Spesies: 2 (Black),
    # 1 (White).
    runner = CliRunner()
    result = runner.invoke(cli, [
        "scaffold-mapping", "--event-type", "rhino_carcass", "--tag", "Rhino Carcass",
        "--tags-file", str(tags_file), "--er-schema-file", str(schema_file),
        "--out", str(out_file),
    ], input="1\nn\nb\n3\n2\n1\n")
    assert result.exit_code == 0, result.output

    by_field = {fm["cmore_field_name"]: fm for fm in json.loads(out_file.read_text())["field_mappings"]}
    # Going back let us set Animal Sex after initially skipping it.
    assert by_field["Animal Sex"]["value_mappings"] == [{"from_value": "unknown", "to_value": "Indeterminable"}]
    species = by_field["Rhino Spesies"]["value_mappings"]
    assert {"from_value": "bw", "to_value": "Black"} in species
    assert {"from_value": "wt", "to_value": "White"} in species


def test_interactive_quit_aborts_without_writing(tmp_path):
    """Entering 'q' at a prompt aborts the wizard (no output written)."""
    tags_file = tmp_path / "tags.json"
    tags_file.write_text(json.dumps(_INTERACTIVE_TAGS))
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(_INTERACTIVE_SCHEMA))
    out_file = tmp_path / "mapping.json"

    runner = CliRunner()
    result = runner.invoke(cli, [
        "scaffold-mapping", "--event-type", "rhino_carcass", "--tag", "Rhino Carcass",
        "--tags-file", str(tags_file), "--er-schema-file", str(schema_file),
        "--out", str(out_file),
    ], input="q\n")
    assert result.exit_code != 0  # click.Abort
    assert not out_file.exists()


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


def test_ensure_scheme_prepends_https_when_missing():
    from app.datasource.cli import _ensure_scheme
    assert _ensure_scheme("gundi-er.pamdas.org") == "https://gundi-er.pamdas.org"
    assert _ensure_scheme("https://x.org") == "https://x.org"
    assert _ensure_scheme("http://x.org") == "http://x.org"
    assert _ensure_scheme(None) is None
    assert _ensure_scheme("") == ""
