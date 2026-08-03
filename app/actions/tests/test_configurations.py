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

    annotated_actions = {ref["action"] for _, ref in found}
    assert annotated_actions == {
        "list_tag_names",
        "list_tag_fields",
        "list_field_options",
        "list_classification_values",
    }

    for host_node, ref in found:
        assert "ui:widget" not in host_node, ref["action"]
        assert ref["target"] == "self"
        assert ref["allow_free_text"] is True

        _, config_model, _ = handlers[ref["action"]]
        assert issubclass(config_model, ReferenceActionConfiguration)

        declared = set(ref.get("params", {}))
        model_fields = set(config_model.__fields__)
        assert declared <= model_fields, (ref["action"], declared - model_fields)
        required = {
            name for name, f in config_model.__fields__.items() if f.required
        }
        assert required <= declared, (ref["action"], required - declared)
