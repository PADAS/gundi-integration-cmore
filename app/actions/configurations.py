from typing import Dict, Optional

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


class CmoreTagMapping(pydantic.BaseModel):
    """Map a Gundi event_type to a CMORE tag (by name) and its fields."""

    tag_name: str = FieldWithUIOptions(
        ...,
        title="CMORE Tag Name",
        description=(
            "Name of the CMORE tag to attach to events of this type "
            "(e.g., 'Poacher Sighting'). Resolved to a tagId at runtime via "
            "CMORE's /v2/tags/getfull endpoint."
        ),
    )
    field_mappings: Dict[str, str] = FieldWithUIOptions(
        default_factory=dict,
        title="Field Mappings",
        description=(
            "Map of Gundi event_details keys to CMORE field names within the "
            "chosen tag. Values are stringified before sending. For Lookup-typed "
            "CMORE fields, event_details should already contain the CMORE-valid "
            "string (e.g., 'N to S' for a Direction field)."
        ),
    )


class DeliverConfig(PushActionConfiguration):
    """Combined config for the single action_deliver handler.

    Collapses the previous PushObservationsConfig (empty) and PushEventsConfig
    into one config since one handler now dispatches on payload type internally.
    """

    event_type_to_tag: Optional[Dict[str, CmoreTagMapping]] = FieldWithUIOptions(
        None,
        title="Event type → CMORE tag",
        description=(
            "Optional mapping from Gundi event_type to a CMORE tag + field "
            "mappings. Events whose event_type is not in this map are still "
            "posted to CMORE with description + location, but without a "
            "structured tag attached."
        ),
    )
    default_affiliation: Affiliation = FieldWithUIOptions(
        Affiliation.UNKNOWN,
        title="Default affiliation",
        description=(
            "Affiliation for GNodes whose subject type is not in the affiliation map. "
            "Controls track color in C-more: Unknown=yellow, Friendly=blue, Hostile=red, Neutral=green."
        ),
    )
    subject_type_to_affiliation: Optional[Dict[str, Affiliation]] = FieldWithUIOptions(
        None,
        title="Subject type → affiliation",
        description=(
            "Optional mapping from Gundi subject_subtype or subject_type to a C-more affiliation, "
            "e.g. {\"ranger\": \"Friendly\", \"elephant\": \"Neutral\"}. "
            "subject_subtype is matched first, then subject_type."
        ),
    )
    subject_type_to_classification: Optional[Dict[str, CmoreClassification]] = FieldWithUIOptions(
        None,
        title="Subject type → classification",
        description=(
            "Optional mapping from Gundi subject_subtype or subject_type to a C-more classification "
            "(battleDimension/force/type/role), which selects the map icon. "
            "Valid values are instance-specific — see the get-classification-tree CLI command. "
            "subject_subtype is matched first, then subject_type."
        ),
    )

    @classmethod
    def ui_schema(cls, *args, **kwargs):
        """Hand-built UI schema for the Gundi portal form.

        The auto-walker in UISchemaModelMixin only descends one level and
        doesn't emit `additionalProperties` hints for `Dict[str, NestedModel]`
        shapes — so without this override, the portal renders dict values as
        primitive text inputs ("[object Object]"). The hints below tell
        react-jsonschema-form how to render each nested level.

        See GUNDI-5366 for the longer-term fix in the template's ui_schema
        auto-generation.
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
                "additionalProperties": {
                    "ui:title": "Tag mapping",
                    "ui:description": (
                        "Map this Gundi event_type to a CMORE tag. The "
                        "object key is the event_type string."
                    ),
                    "tag_name": {
                        "ui:title": "CMORE Tag Name",
                        "ui:placeholder": "e.g. Poacher Sighting",
                        "ui:help": (
                            "Exact tag name from the CMORE instance. Must "
                            "be visible to this integration's ShareGroup."
                        ),
                    },
                    "field_mappings": {
                        "ui:description": (
                            "Map Gundi event_details keys (left) to CMORE "
                            "field names within the chosen tag (right). "
                            "For Lookup-typed CMORE fields, the Gundi value "
                            "must match the CMORE lookup text exactly."
                        ),
                        "additionalProperties": {
                            "ui:title": "CMORE field name",
                            "ui:placeholder": "e.g. Direction",
                        },
                    },
                },
            },
            "subject_type_to_affiliation": {
                "additionalProperties": {
                    "ui:title": "Affiliation",
                },
            },
            "subject_type_to_classification": {
                "additionalProperties": {
                    "ui:title": "Classification",
                    "battleDimension": {"ui:title": "Battle Dimension"},
                    "force": {"ui:title": "Force"},
                    "type": {"ui:title": "Type"},
                    "role": {"ui:title": "Role"},
                },
            },
        })
        return base
