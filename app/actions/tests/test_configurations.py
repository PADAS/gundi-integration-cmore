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
