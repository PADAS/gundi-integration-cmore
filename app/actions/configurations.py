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


class DeliverConfig(PushActionConfiguration):
    """Combined config for the single action_deliver handler.

    Collapses the previous PushObservationsConfig (empty) and PushEventsConfig
    (event_type_to_tag_id) into one config since one handler now dispatches on
    payload type internally.
    """

    event_type_to_tag_id: Optional[Dict[str, int]] = FieldWithUIOptions(
        None,
        title="Event type → C-more tag ID",
        description=(
            "Optional mapping from event_type to C-more tagId. "
            "When an event arrives whose event_type is in this map, the matching tag is attached."
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
