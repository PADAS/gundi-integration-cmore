from typing import Dict, Optional

import pydantic
from app.services.utils import FieldWithUIOptions, GlobalUISchemaOptions, UIOptions
from .core import AuthActionConfiguration, ExecutableActionMixin, PushActionConfiguration


class AuthenticateConfig(AuthActionConfiguration, ExecutableActionMixin):
    token: pydantic.SecretStr = FieldWithUIOptions(
        ...,
        title="API Token",
        description="C-more API token. Must be prefixed with 'Token' in the Authorization header.",
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
