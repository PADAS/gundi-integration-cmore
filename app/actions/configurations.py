from typing import List, Optional

import pydantic
from app.datasource.schemas import Affiliation, CmoreClassification
from app.services.utils import FieldWithUIOptions, GlobalUISchemaOptions, UIOptions
from .core import AuthActionConfiguration, ExecutableActionMixin, PushActionConfiguration, ReferenceActionConfiguration


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
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
        "https://cmorewc1.chpc.ac.za/za/WebAPI/api",
        title="API Base URL",
        description="Base URL for the C-more API.",
    )
    owner_group_id: int = FieldWithUIOptions(
        ...,
        title="Owner Group ID",
        description="ShareGroupId linked to this token. Events will be posted to this group.",
    )

    ui_global_options: GlobalUISchemaOptions = GlobalUISchemaOptions(
        order=["base_url", "token", "owner_group_id"],
    )


class ListTagNamesQuery(ReferenceActionConfiguration):
    """Query model for action_list_tag_names (no parameters)."""


class ListTagFieldsQuery(ReferenceActionConfiguration):
    """Query model for action_list_tag_fields."""

    tag_name: str


class ListFieldOptionsQuery(ReferenceActionConfiguration):
    """Query model for action_list_field_options."""

    tag_name: str
    field_name: str


class ListClassificationValuesQuery(ReferenceActionConfiguration):
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


class CmoreValueMapping(pydantic.BaseModel):
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


class CmoreFieldMapping(pydantic.BaseModel):
    """One Gundi event_details key → one CMORE field name (within a tag)."""

    event_details_key: str = FieldWithUIOptions(
        ...,
        title="Gundi event_details key",
        description="Key inside the Gundi event's event_details dict.",
    )
    cmore_field_name: str = FieldWithUIOptions(
        ...,
        title="CMORE field name",
        description="Field name within the chosen CMORE tag.",
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


class CmoreTagMapping(pydantic.BaseModel):
    """Map a Gundi event_type to a CMORE tag (by name) and its fields."""

    event_type: str = FieldWithUIOptions(
        ...,
        title="Gundi event_type",
        description="The event_type string on incoming Gundi events.",
    )
    tag_name: str = FieldWithUIOptions(
        ...,
        title="CMORE Tag Name",
        description=(
            "Name of the CMORE tag to attach to events of this type "
            "(e.g., 'Poacher Sighting'). Resolved to a tagId at runtime via "
            "CMORE's /v2/tags/getfull endpoint."
        ),
    )
    field_mappings: List[CmoreFieldMapping] = FieldWithUIOptions(
        default_factory=list,
        title="Field Mappings",
        description=(
            "Map Gundi event_details keys to CMORE field names within the "
            "chosen tag. Values are coerced per the CMORE field's data type: "
            "Lookup/FixedLookup values are resolved to a valid option (using "
            "the optional value mappings, then a case/punctuation-insensitive "
            "match); Number/Boolean values are validated; everything else is "
            "sent as a string."
        ),
    )


class SubjectAffiliationMapping(pydantic.BaseModel):
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


class SubjectClassificationMapping(pydantic.BaseModel):
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


def _reference(action: str, params: Optional[dict] = None) -> dict:
    """Build a gundi:reference ui_schema annotation (spec: docs/superpowers/
    specs/2026-07-31-reference-data-config-ui-design.md §2). Deliberately
    does NOT set ui:widget — portals without reference support must keep
    rendering plain text fields."""
    return {
        "action": action,
        "target": "self",
        "params": params or {},
        "allow_free_text": True,
    }


class DeliverConfig(PushActionConfiguration):
    """Combined config for the single action_deliver handler.

    Collapses the previous PushObservationsConfig (empty) and PushEventsConfig
    into one config since one handler now dispatches on payload type internally.
    """

    event_type_to_tag: Optional[List[CmoreTagMapping]] = FieldWithUIOptions(
        None,
        title="Event type → CMORE tag",
        description=(
            "Optional list of mappings from Gundi event_type to a CMORE tag + "
            "field mappings. Events whose event_type is not in this list are "
            "still posted to CMORE with description + location, but without a "
            "structured tag attached."
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
                    "tag_name": {
                        "ui:placeholder": "e.g. Poacher Sighting",
                        "ui:help": (
                            "Exact tag name from the CMORE instance. Must "
                            "be visible to this integration's ShareGroup."
                        ),
                    },
                    "field_mappings": {
                        "items": {
                            "ui:order": list(CmoreFieldMapping.__fields__),
                            "event_details_key": {"ui:placeholder": "e.g. animal_sex"},
                            "cmore_field_name": {"ui:placeholder": "e.g. Animal Sex"},
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
        event_items["tag_name"]["gundi:reference"] = _reference("list_tag_names")
        field_items = event_items["field_mappings"]["items"]
        field_items["cmore_field_name"]["gundi:reference"] = _reference(
            "list_tag_fields", {"tag_name": {"$data": "../../tag_name"}}
        )
        value_items = field_items["value_mappings"]["items"]
        value_items["to_value"]["gundi:reference"] = _reference(
            "list_field_options",
            {
                "tag_name": {"$data": "../../../../tag_name"},
                "field_name": {"$data": "../../cmore_field_name"},
            },
        )
        classification_items = base["subject_type_to_classification"]["items"]
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
