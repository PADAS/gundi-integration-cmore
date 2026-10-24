import inspect
from typing import ClassVar, List, Optional

import pydantic
from app.datasource.schemas import Affiliation, CmoreClassification
from app.services.utils import FieldWithUIOptions, GlobalUISchemaOptions, UIOptions
from .core import (
    AuthActionConfiguration,
    ExecutableActionMixin,
    GenericActionConfiguration,
    PushActionConfiguration,
    ReferenceActionConfiguration,
)



def _portal_description(schema: dict, model) -> None:
    """Pydantic copies a model's docstring into its schema ``description``,
    and the Gundi portal renders that above the form and above nested array
    items. Docstrings here are developer notes, so they are dropped; a model
    that wants text in the portal sets ``portal_description`` instead."""
    explicit = getattr(model, "portal_description", None)
    if explicit:
        schema["description"] = explicit
    elif schema.get("description") == inspect.getdoc(model):
        schema.pop("description", None)


class PortalSchema(pydantic.BaseModel):
    """Base for every config model rendered in the Gundi portal (see
    _portal_description)."""

    class Config:
        schema_extra = staticmethod(_portal_description)

    @classmethod
    def schema(cls, *args, **kwargs):
        # Enums have no schema_extra hook and Pydantic gives them the
        # placeholder description "An enumeration."; drop it too.
        schema = super().schema(*args, **kwargs)
        for definition in (schema.get("definitions") or {}).values():
            if definition.get("enum") and definition.get("description") == "An enumeration.":
                definition.pop("description")
        return schema


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin, PortalSchema):
    token: pydantic.SecretStr = FieldWithUIOptions(
        ...,
        title="API Token",
        description=(
            "C-more API token (raw value, without the 'Token ' prefix). "
            "The client adds the 'Token ' prefix to the Authorization header automatically."
        ),
        ui_options=UIOptions(widget="password"),
    )
    base_url: str = FieldWithUIOptions(
        ...,
        title="API Base URL",
        description=(
            "Base URL for the C-more API on your CMORE instance: the server "
            "plus '/za/WebAPI/api' (e.g. https://cmore.csir.co.za/za/WebAPI/api). "
            "URLs differ per CMORE deployment."
        ),
    )
    owner_group_id: int = FieldWithUIOptions(
        ...,
        title="Owner Group ID",
        description="ShareGroupId linked to this token. Events will be posted to this group.",
    )

    ui_global_options: GlobalUISchemaOptions = GlobalUISchemaOptions(
        order=["base_url", "token", "owner_group_id"],
    )


class ListTagNamesQuery(ReferenceActionConfiguration, PortalSchema):
    """Query model for action_list_tag_names (no parameters)."""


class ListTagFieldsQuery(ReferenceActionConfiguration, PortalSchema):
    """Query model for action_list_tag_fields."""

    tag: str  # tag id (preferred) or exact tag name


class ListFieldOptionsQuery(ReferenceActionConfiguration, PortalSchema):
    """Query model for action_list_field_options."""

    tag: str
    field: str  # field id (preferred) or exact field name


class ListClassificationValuesQuery(ReferenceActionConfiguration, PortalSchema):
    """Query for action_list_classification_values. Each level narrows the
    CMORE classification tree; the action returns the next level's values."""

    battleDimension: Optional[str] = None
    force: Optional[str] = None
    type: Optional[str] = None


# NOTE: Dict[str, NestedModel] would be the natural Pydantic shape for the
# mappings below, but the Gundi portal's form renderer mis-handles
# `additionalProperties` with object values and displays "[object Object]"
# for them. We use List[NestedModel-with-explicit-key-field] as a workaround
# because portal renders arrays correctly. Same goes for any further-nested
# object fields inside those array items — flatten them onto the parent
# (see SubjectClassificationMapping below, where CmoreClassification's four
# fields are flattened in).
#
# See docs/portal-rendering-workaround.md for the full rationale and the
# revert plan when the portal bug GUNDI-5371 is fixed.


class CmoreValueMapping(PortalSchema):
    """Translate one source (ER) value into the CMORE value to send.

    Required for Lookup/FixedLookup fields whenever the source vocabulary
    differs from CMORE's allowed options (e.g. ER 'b_3_months1_year' → CMORE
    'Calf', ER 'Black Rhino' → CMORE 'Black'). Values that already match a
    CMORE option (case/punctuation-insensitively, e.g. 'male' → 'Male') do
    NOT need an entry.
    """

    from_value: str = FieldWithUIOptions(
        ...,
        title="Source value",
        description="The value as it appears in the Gundi event_details (from ER).",
    )
    to_value: str = FieldWithUIOptions(
        ...,
        title="CMORE value",
        description="The value to send to CMORE (must be a valid option for the field).",
    )


class CmoreFieldMapping(PortalSchema):
    """One Gundi event_details key → one CMORE field (by id or name, within a tag)."""

    event_details_key: str = FieldWithUIOptions(
        ...,
        title="Gundi event_details key",
        description="Key inside the Gundi event's event_details dict.",
    )
    cmore_field: str = FieldWithUIOptions(
        ...,
        title="CMORE Field",
        description=(
            "The CMORE field within the chosen tag. Preferably the immutable "
            "field ID (what the portal dropdown stores); an exact field name "
            "also works but breaks if the field is renamed in CMORE."
        ),
    )
    value_mappings: List[CmoreValueMapping] = FieldWithUIOptions(
        default_factory=list,
        title="Value Mappings",
        description=(
            "Optional source→CMORE value translations, mainly for Lookup "
            "fields whose allowed options differ from the source values. "
            "Unmapped values are matched against the field's options "
            "case/punctuation-insensitively; anything that still doesn't "
            "match a valid option is dropped (and logged)."
        ),
    )


class CmoreTagMapping(PortalSchema):
    """Map a Gundi event_type to a CMORE tag (by id or name) and its fields."""

    event_type: str = FieldWithUIOptions(
        ...,
        title="Gundi event_type",
        description="The event_type string on incoming Gundi events.",
    )
    tag: str = FieldWithUIOptions(
        ...,
        title="CMORE Tag",
        description=(
            "The CMORE tag to attach to events of this type. Preferably the "
            "immutable tag ID (e.g. '8443', what the portal dropdown stores); "
            "an exact tag name also works but breaks if the tag is renamed. "
            "Resolved at runtime via CMORE's /v2/tags/getfull endpoint."
        ),
    )
    field_mappings: List[CmoreFieldMapping] = FieldWithUIOptions(
        default_factory=list,
        title="Field Mappings",
        description=(
            "Map Gundi event_details keys to CMORE fields (by id or name) "
            "within the chosen tag. Values are coerced per the CMORE field's data type: "
            "Lookup/FixedLookup values are resolved to a valid option (using "
            "the optional value mappings, then a case/punctuation-insensitive "
            "match); Number/Boolean values are validated; everything else is "
            "sent as a string."
        ),
    )
    reported_by_field: Optional[str] = FieldWithUIOptions(
        None,
        title="Reported by field",
        description=(
            "CMORE field (by id or name, within the chosen tag) that receives "
            "the name of the person or subject who reported the event in "
            "EarthRanger. Leave blank to use a field on the tag named "
            "\"Reported by\" automatically. When the tag has no such field the "
            "reporter is added to the event's source comment instead."
        ),
    )


class SubjectAffiliationMapping(PortalSchema):
    """Map a Gundi subject_type/subject_subtype to a C-more affiliation."""

    subject_type: str = FieldWithUIOptions(
        ...,
        title="Gundi subject type",
        description=(
            "Matches against the Gundi observation's subject_subtype first, "
            "then subject_type."
        ),
    )
    affiliation: Affiliation = FieldWithUIOptions(
        ...,
        title="C-more affiliation",
    )


class SubjectClassificationMapping(PortalSchema):
    """Map a Gundi subject_type/subject_subtype to a C-more classification.

    The classification fields (battleDimension/force/type/role) are flattened
    directly onto this model rather than nested under a `classification`
    sub-object — same portal-rendering workaround as the parent dict→list
    refactor (GUNDI-5371). Handler reassembles them into a CmoreClassification
    before sending to C-more.
    """

    subject_type: str = FieldWithUIOptions(
        ...,
        title="Gundi subject type",
        description=(
            "Matches against the Gundi observation's subject_subtype first, "
            "then subject_type."
        ),
    )
    battleDimension: Optional[str] = FieldWithUIOptions(
        None,
        title="Battle Dimension",
        description="C-more classification.battleDimension (e.g. LAND, AIR, SEA).",
    )
    force: Optional[str] = FieldWithUIOptions(
        None,
        title="Force",
        description="C-more classification.force (e.g. ANIMAL, UNIT, CIVIL).",
    )
    type: Optional[str] = FieldWithUIOptions(
        None,
        title="Type",
        description="C-more classification.type (e.g. DOG, RHINO, FIXED_WING).",
    )
    role: Optional[str] = FieldWithUIOptions(
        None,
        title="Role",
        description="C-more classification.role (e.g. K9, POLICE_OFFICER).",
    )


def _reference(action: str, params: Optional[dict] = None, *, target: str = "self") -> dict:
    """Build a gundi:reference ui_schema annotation (spec: docs/superpowers/
    specs/2026-07-31-reference-data-config-ui-design.md §2). Deliberately
    does NOT set ui:widget — portals without reference support must keep
    rendering plain text fields.

    ``target="provider"`` directs the portal to fetch from the provider
    integration(s) of the connection instead of this integration — used for
    the EarthRanger-side vocabulary (event types, field keys, field values),
    whose reference actions live on the ER runner (Phase 2 of the spec).
    """
    return {
        "action": action,
        "target": target,
        "params": params or {},
        "allow_free_text": True,
    }


class DeliverConfig(PushActionConfiguration, PortalSchema):
    # Combined config for the single action_deliver handler.
    #
    # Collapses the previous PushObservationsConfig (empty) and PushEventsConfig
    # into one config since one handler now dispatches on payload type internally.
    # (A comment, not a docstring: Pydantic would show a docstring as the
    # form's description in the Gundi portal.)

    event_type_to_tag: Optional[List[CmoreTagMapping]] = FieldWithUIOptions(
        None,
        title="Event type → CMORE tag",
        description=(
            "Mappings from Gundi event_type to a CMORE tag + field mappings. "
            "Only events whose event_type is in this list are sent to CMORE; "
            "any other event is skipped and recorded in the activity log. "
            "A tag entry with no field mappings still counts as configured."
        ),
    )
    default_affiliation: Affiliation = FieldWithUIOptions(
        Affiliation.UNKNOWN,
        title="Default affiliation",
        description=(
            "Affiliation for GNodes whose subject type is not in the affiliation list. "
            "Controls track color in C-more: Unknown=yellow, Friendly=blue, Hostile=red, Neutral=green."
        ),
    )
    subject_type_to_affiliation: Optional[List[SubjectAffiliationMapping]] = FieldWithUIOptions(
        None,
        title="Subject type → affiliation",
        description=(
            "Optional list of mappings from Gundi subject_subtype or subject_type to a "
            "C-more affiliation. subject_subtype is matched first, then subject_type."
        ),
    )
    subject_type_to_classification: Optional[List[SubjectClassificationMapping]] = FieldWithUIOptions(
        None,
        title="Subject type → classification",
        description=(
            "Optional list of mappings from Gundi subject_subtype or subject_type to a C-more "
            "classification (battleDimension/force/type/role), which selects the map icon. "
            "Valid values are instance-specific — see the get-classification-tree CLI command. "
            "subject_subtype is matched first, then subject_type."
        ),
    )

    @classmethod
    def ui_schema(cls, *args, **kwargs):
        """UI schema for the Gundi portal form.

        Arrays render cleanly in the portal's rjsf-based renderer without
        additionalProperties hints. We just provide field-level placeholders
        and titles for the per-array-item sub-forms via the rjsf `items`
        convention.

        Once GUNDI-5371 (portal bug for additionalProperties-of-object) is
        fixed, this config can be reverted to the cleaner Dict shape and the
        ui_schema simplified accordingly.

        Every nested object level declares an explicit "ui:order": the portal
        stores schemas in Postgres jsonb, which canonicalizes JSON object key
        order (shortest key first, then bytewise), so schema property order
        does not survive storage and nested models otherwise render in
        arbitrary order. Arrays DO survive jsonb, which is why ui:order works.
        The orders are derived from the models' field order so the form always
        matches the source of truth.
        """
        base = super().ui_schema(*args, **kwargs)
        base.update({
            "ui:order": [
                "event_type_to_tag",
                "default_affiliation",
                "subject_type_to_affiliation",
                "subject_type_to_classification",
            ],
            "event_type_to_tag": {
                "items": {
                    "ui:order": list(CmoreTagMapping.__fields__),
                    "event_type": {"ui:placeholder": "e.g. poacher_sighting"},
                    "tag": {
                        "ui:placeholder": "e.g. 8443 or Rhino Carcass",
                        "ui:help": (
                            "Tag ID (preferred, rename-proof) or exact tag "
                            "name. Must be visible to this integration's "
                            "ShareGroup."
                        ),
                    },
                    "reported_by_field": {
                        "ui:placeholder": "blank = the tag's \"Reported by\" field",
                        "ui:help": (
                            "Field ID (preferred) or exact field name within "
                            "the tag. Blank auto-matches a field named "
                            "\"Reported by\"; otherwise the reporter goes into "
                            "the source comment."
                        ),
                    },
                    "field_mappings": {
                        "items": {
                            "ui:order": list(CmoreFieldMapping.__fields__),
                            "event_details_key": {"ui:placeholder": "e.g. animal_sex"},
                            "cmore_field": {"ui:placeholder": "e.g. 1261 or Animal Sex"},
                            "value_mappings": {
                                "items": {
                                    "ui:order": list(CmoreValueMapping.__fields__),
                                    "from_value": {"ui:placeholder": "e.g. b_3_months1_year"},
                                    "to_value": {"ui:placeholder": "e.g. Calf"},
                                },
                            },
                        },
                    },
                },
            },
            "subject_type_to_affiliation": {
                "items": {
                    "ui:order": list(SubjectAffiliationMapping.__fields__),
                    "subject_type": {"ui:placeholder": "e.g. ranger"},
                },
            },
            "subject_type_to_classification": {
                "items": {
                    "ui:order": list(SubjectClassificationMapping.__fields__),
                    "subject_type": {"ui:placeholder": "e.g. ranger"},
                    "battleDimension": {"ui:placeholder": "e.g. LAND"},
                    "force": {"ui:placeholder": "e.g. ANIMAL"},
                    "type": {"ui:placeholder": "e.g. DOG"},
                    "role": {"ui:placeholder": "e.g. K9"},
                },
            },
        })

        # Reference-data annotations (inert until the portal supports them).
        event_items = base["event_type_to_tag"]["items"]
        event_items["tag"]["gundi:reference"] = _reference("list_tag_names")
        # The reporter field lives next to the tag it belongs to, so the
        # cascade is a bare sibling reference.
        event_items["reported_by_field"]["gundi:reference"] = _reference(
            "list_tag_fields", {"tag": {"$data": "tag"}}
        )
        field_items = event_items["field_mappings"]["items"]
        field_items["cmore_field"]["gundi:reference"] = _reference(
            "list_tag_fields", {"tag": {"$data": "../../tag"}}
        )
        value_items = field_items["value_mappings"]["items"]
        value_items["to_value"]["gundi:reference"] = _reference(
            "list_field_options",
            {
                "tag": {"$data": "../../../../tag"},
                "field": {"$data": "../../cmore_field"},
            },
        )
        # Source-side (EarthRanger) vocabulary — served by the ER runner's
        # reference actions via target="provider". $data paths mirror the
        # self-target siblings above: event_type sits next to tag,
        # event_details_key next to cmore_field, from_value next to
        # to_value.
        event_items["event_type"]["gundi:reference"] = _reference(
            "list_event_types", target="provider"
        )
        field_items["event_details_key"]["gundi:reference"] = _reference(
            "list_event_type_fields",
            {"event_type": {"$data": "../../event_type"}},
            target="provider",
        )
        value_items["from_value"]["gundi:reference"] = _reference(
            "list_event_field_values",
            {
                "event_type": {"$data": "../../../../event_type"},
                "field_key": {"$data": "../../event_details_key"},
            },
            target="provider",
        )
        # Subject-side (EarthRanger) vocabulary — the subject_type fields in
        # both subject-mapping lists offer the provider's observed subject
        # subtypes (grouped by subject type, with the types themselves as
        # fallback options), served by the ER runner's list_subject_types
        # reference action. No params, no cascade.
        affiliation_items = base["subject_type_to_affiliation"]["items"]
        affiliation_items["subject_type"]["gundi:reference"] = _reference(
            "list_subject_types", target="provider"
        )
        classification_items = base["subject_type_to_classification"]["items"]
        classification_items["subject_type"]["gundi:reference"] = _reference(
            "list_subject_types", target="provider"
        )
        classification_items["battleDimension"]["gundi:reference"] = _reference(
            "list_classification_values"
        )
        classification_items["force"]["gundi:reference"] = _reference(
            "list_classification_values",
            {"battleDimension": {"$data": "battleDimension"}},
        )
        classification_items["type"]["gundi:reference"] = _reference(
            "list_classification_values",
            {
                "battleDimension": {"$data": "battleDimension"},
                "force": {"$data": "force"},
            },
        )
        classification_items["role"]["gundi:reference"] = _reference(
            "list_classification_values",
            {
                "battleDimension": {"$data": "battleDimension"},
                "force": {"$data": "force"},
                "type": {"$data": "type"},
            },
        )
        return base


class CreateErEventTypesConfig(GenericActionConfiguration, ExecutableActionMixin, PortalSchema):
    # Form for the portal-triggered *Create EarthRanger event types* action.
    #
    # Creates one EarthRanger event type (with choice lists) per selected CMORE
    # tag, in a category for this integration's share group, on the EarthRanger
    # provider connected to this destination. Generated types are delivered by
    # naming convention, so they need no ``event_type_to_tag`` entry (design:
    # docs/superpowers/specs/2026-10-23-cmore-tags-to-er-event-types-design.md).
    # The portal shows portal_description above the form (see PortalSchema).

    portal_description: ClassVar[str] = (
        "Creates one EarthRanger event type, with its choice lists, for each "
        "CMORE tag you select, on the EarthRanger site connected to this "
        "destination. Events reported with these types are sent to CMORE with "
        "no mapping to configure. Run it with Preview only first to check "
        "permissions."
    )


    tags: List[str] = FieldWithUIOptions(
        ...,
        min_items=1,
        title="CMORE tags",
        description=(
            "The CMORE tags to turn into EarthRanger event types, by tag ID "
            "(preferred) or exact name. One EarthRanger event type is created "
            "or updated per tag."
        ),
    )
    share_group_label: Optional[str] = FieldWithUIOptions(
        None,
        title="Share group label",
        description=(
            "Display name for the EarthRanger event category that holds these "
            "types, e.g. the reserve's name. Defaults to 'CMORE group <Owner "
            "Group ID>'. Only used when the category is first created."
        ),
    )
    dry_run: bool = FieldWithUIOptions(
        False,
        title="Preview only",
        description=(
            "Check the EarthRanger connection and permissions and report what "
            "would be created or updated, without writing anything."
        ),
    )

    @classmethod
    def ui_schema(cls, *args, **kwargs):
        base = super().ui_schema(*args, **kwargs)
        base.update({
            "ui:order": ["tags", "share_group_label", "dry_run"],
            "tags": {
                "items": {
                    "ui:placeholder": "e.g. 8443 or Rhino Carcass",
                    # Same dropdown the mapping list uses (inert until the
                    # portal supports reference annotations).
                    "gundi:reference": _reference("list_tag_names"),
                },
            },
            "share_group_label": {"ui:placeholder": "e.g. Lapalala CMORE"},
        })
        return base
