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


def test_reported_by_field_offers_the_tag_fields_of_its_sibling_tag():
    """The reporter field dropdown cascades off the tag chosen in the same
    mapping entry (a sibling, so a bare $data name)."""
    from app.actions.configurations import DeliverConfig

    node = DeliverConfig.ui_schema()["event_type_to_tag"]["items"]["reported_by_field"]
    ref = node["gundi:reference"]
    assert ref["action"] == "list_tag_fields"
    assert ref["target"] == "self"
    assert ref["params"] == {"tag": {"$data": "tag"}}
    assert "ui:widget" not in node


def test_create_er_event_types_config_fields_and_order():
    from app.actions.configurations import CreateErEventTypesConfig, _reference
    from app.actions.core import ExecutableActionMixin, GenericActionConfiguration

    assert issubclass(CreateErEventTypesConfig, GenericActionConfiguration)
    assert issubclass(CreateErEventTypesConfig, ExecutableActionMixin)
    ui = CreateErEventTypesConfig.ui_schema()
    assert ui["ui:order"] == ["tags", "share_group_label", "dry_run"]
    assert ui["tags"]["items"]["gundi:reference"] == _reference("list_tag_names")
    assert "ui:widget" not in ui["tags"]["items"]


def test_create_er_event_types_config_requires_tags():
    import pydantic
    import pytest
    from app.actions.configurations import CreateErEventTypesConfig

    with pytest.raises(pydantic.ValidationError):
        CreateErEventTypesConfig(tags=[])
    cfg = CreateErEventTypesConfig(tags=["20"])
    assert cfg.dry_run is False and cfg.share_group_label is None


def test_portal_forms_show_no_developer_docstrings():
    """The portal renders a config model's schema description above its
    form, and Pydantic fills it from the class docstring. Forms must show
    user-facing text only: no design-doc paths, handler names or history."""
    from app.actions.configurations import CreateErEventTypesConfig, DeliverConfig

    assert "description" not in DeliverConfig.schema()
    description = CreateErEventTypesConfig.schema()["description"]
    assert description == (
        "Creates one EarthRanger event type, with its choice lists, for each CMORE tag you "
        "select, on the EarthRanger site connected to this destination. Events reported with "
        "these types are sent to CMORE with no mapping to configure. Run it with Preview only "
        "first to check permissions."
    )
    for leak in ("docs/", "superpowers", "action_deliver", "event_type_to_tag", "``"):
        assert leak not in description


def test_create_er_event_types_has_a_readable_title():
    from app.actions import handlers

    assert getattr(handlers.action_create_er_event_types, "action_title", None) == "Create EarthRanger event types"



def _model_descriptions(schema):
    """Model-level descriptions in a schema and its nested definitions
    (field descriptions are user-facing help text and are not collected)."""
    found = {"<root>": schema.get("description")}
    for name, definition in (schema.get("definitions") or {}).items():
        found[name] = definition.get("description")
    return {k: v for k, v in found.items() if v}


def test_no_action_schema_carries_a_docstring_description_anywhere():
    """Every action's registered schema, nested definitions included, is free
    of docstring-derived descriptions. Only a model that declares a
    portal_description shows one, and it is that text."""
    from app.actions.configurations import CreateErEventTypesConfig
    from app.actions.core import discover_actions

    for action_id, (_, model, _) in discover_actions("app.actions.handlers", "action_").items():
        descriptions = _model_descriptions(model.schema())
        expected = {"<root>": CreateErEventTypesConfig.portal_description} if model is CreateErEventTypesConfig else {}
        assert descriptions == expected, action_id
