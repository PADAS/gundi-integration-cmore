"""Tests for the ui_schema emitted to the Gundi portal.

The portal stores schemas in Postgres jsonb, which canonicalizes JSON object
key order (shortest key first, then bytewise). Property order in the JSON
schema is therefore lost by the time the portal renders the form, and nested
models render in essentially arbitrary order. Explicit ui:order arrays are
the only ordering that survives storage (jsonb preserves arrays), so every
nested object level must declare one.
"""

from app.actions.configurations import (
    CmoreFieldMapping,
    CmoreTagMapping,
    CmoreValueMapping,
    DeliverConfig,
    SubjectAffiliationMapping,
    SubjectClassificationMapping,
)


def model_field_order(model):
    return list(model.__fields__.keys())


def test_deliver_ui_schema_orders_every_nested_item_level():
    ui = DeliverConfig.ui_schema()

    tag_items = ui["event_type_to_tag"]["items"]
    assert tag_items["ui:order"] == model_field_order(CmoreTagMapping)

    field_items = tag_items["field_mappings"]["items"]
    assert field_items["ui:order"] == model_field_order(CmoreFieldMapping)

    value_items = field_items["value_mappings"]["items"]
    assert value_items["ui:order"] == model_field_order(CmoreValueMapping)

    affiliation_items = ui["subject_type_to_affiliation"]["items"]
    assert affiliation_items["ui:order"] == model_field_order(SubjectAffiliationMapping)

    classification_items = ui["subject_type_to_classification"]["items"]
    assert classification_items["ui:order"] == model_field_order(SubjectClassificationMapping)


def _collect_gundi_references(node, found):
    if isinstance(node, dict):
        if "gundi:reference" in node:
            found.append((node, node["gundi:reference"]))
        for value in node.values():
            _collect_gundi_references(value, found)


def test_gundi_reference_annotations_match_registered_reference_actions():
    """Drift guard: every gundi:reference annotation must name a real
    reference action whose query model matches the declared params, and must
    never set ui:widget (forward-compat: old portals ignore the annotation)."""
    from app.actions.configurations import DeliverConfig
    from app.actions.core import ReferenceActionConfiguration, discover_actions

    handlers = discover_actions(module_name="app.actions.handlers", prefix="action_")
    found = []
    _collect_gundi_references(DeliverConfig.ui_schema(), found)

    self_refs = [(node, ref) for node, ref in found if ref["target"] == "self"]
    provider_refs = [(node, ref) for node, ref in found if ref["target"] == "provider"]
    assert {ref["target"] for _, ref in found} <= {"self", "provider"}

    assert {ref["action"] for _, ref in self_refs} == {
        "list_tag_names",
        "list_tag_fields",
        "list_field_options",
        "list_classification_values",
    }
    # Provider-target actions live on the EarthRanger runner (Phase 2); their
    # query models can't be validated here, so pin the contract by name+params
    # against gundi-integration-earthranger's registered actions instead.
    assert {ref["action"] for _, ref in provider_refs} == {
        "list_subject_types",
        "list_event_types",
        "list_event_type_fields",
        "list_event_field_values",
    }
    provider_params_by_action = {
        ref["action"]: set(ref.get("params", {})) for _, ref in provider_refs
    }
    assert provider_params_by_action == {
        "list_subject_types": set(),
        "list_event_types": set(),
        "list_event_type_fields": {"event_type"},
        "list_event_field_values": {"event_type", "field_key"},
    }

    # Both subject-mapping lists offer the same provider-side vocabulary,
    # each annotated on its own subject_type item field. Assert the exact
    # ui_schema paths (so a misplaced annotation can't hide behind the
    # count) and keep the count guard against extra copies elsewhere.
    ui = DeliverConfig.ui_schema()
    for list_field in (
        "subject_type_to_affiliation",
        "subject_type_to_classification",
    ):
        ref = ui[list_field]["items"]["subject_type"]["gundi:reference"]
        assert ref["action"] == "list_subject_types", list_field
        assert ref["target"] == "provider", list_field
    subject_type_hosts = [
        node for node, ref in provider_refs if ref["action"] == "list_subject_types"
    ]
    assert len(subject_type_hosts) == 2

    for host_node, ref in found:
        assert "ui:widget" not in host_node, ref["action"]
        assert ref["allow_free_text"] is True

    for host_node, ref in self_refs:
        _, config_model, _ = handlers[ref["action"]]
        assert issubclass(config_model, ReferenceActionConfiguration)

        declared = set(ref.get("params", {}))
        model_fields = set(config_model.__fields__)
        assert declared <= model_fields, (ref["action"], declared - model_fields)
        required = {
            name for name, f in config_model.__fields__.items() if f.required
        }
        assert required <= declared, (ref["action"], required - declared)


def test_auth_base_url_is_required():
    """Instance URLs must not default to any particular CMORE deployment
    (CSIR feedback point 2)."""
    import pydantic
    import pytest
    from app.actions.configurations import AuthenticateConfig

    assert AuthenticateConfig.__fields__["base_url"].required
    with pytest.raises(pydantic.ValidationError):
        AuthenticateConfig(token="t", owner_group_id=1)
