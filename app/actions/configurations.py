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


class PushObservationsConfig(PushActionConfiguration):
    pass


class PushEventsConfig(PushActionConfiguration):
    event_type_to_tag_id: Optional[Dict[str, int]] = FieldWithUIOptions(
        None,
        title="EarthRanger event type → C-more tag ID",
        description=(
            "Optional mapping from EarthRanger event_type to C-more tagId. "
            "When an event arrives whose event_type is in this map, the matching tag is attached."
        ),
    )
