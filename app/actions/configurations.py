from typing import List, Optional

import pydantic
from app.datasource.schemas import Affiliation, CmoreClassification
from app.services.utils import FieldWithUIOptions, GlobalUISchemaOptions, UIOptions
from .core import AuthActionConfiguration, ExecutableActionMixin, PushActionConfiguration


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


# NOTE: Dict[str, NestedModel] would be the natural Pydantic shape for the
# mappings below, but the Gundi portal's form renderer mis-handles
# `additionalProperties` with object values and displays "[object Object]"
# for them. We use List[NestedModel-with-explicit-key-field] as a workaround
# because portal renders arrays correctly. Revert to dict shapes once the
# portal bug GUNDI-5371 is fixed.


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
            "chosen tag. Values are stringified before sending. For "
            "Lookup-typed CMORE fields, event_details should already contain "
            "the CMORE-valid string (e.g., 'N to S' for a Direction field)."
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
    """Map a Gundi subject_type/subject_subtype to a C-more classification."""

    subject_type: str = FieldWithUIOptions(
        ...,
        title="Gundi subject type",
        description=(
            "Matches against the Gundi observation's subject_subtype first, "
            "then subject_type."
        ),
    )
    classification: CmoreClassification = FieldWithUIOptions(
        ...,
        title="C-more classification",
        description="battleDimension / force / type / role.",
    )


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
                            "event_details_key": {"ui:placeholder": "e.g. direction"},
                            "cmore_field_name": {"ui:placeholder": "e.g. Direction"},
                        },
                    },
                },
            },
            "subject_type_to_affiliation": {
                "items": {
                    "subject_type": {"ui:placeholder": "e.g. ranger"},
                },
            },
            "subject_type_to_classification": {
                "items": {
                    "subject_type": {"ui:placeholder": "e.g. ranger"},
                    "classification": {
                        "battleDimension": {"ui:title": "Battle Dimension"},
                        "force": {"ui:title": "Force"},
                        "type": {"ui:title": "Type"},
                        "role": {"ui:title": "Role"},
                    },
                },
            },
        })
        return base
