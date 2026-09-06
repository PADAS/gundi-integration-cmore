"""Response envelope for C-more reference-data actions.

The template (``app/actions/core.py``) defines only the
``ReferenceActionConfiguration`` marker. The option/response shape the Gundi
portal consumes lives here so the framework file stays identical to upstream.
See docs/superpowers/specs/2026-07-31-reference-data-config-ui-design.md.
"""
from typing import List, Optional

from pydantic import BaseModel


class ReferenceOption(BaseModel):
    value: str
    label: Optional[str] = None        # portal defaults label to value
    description: Optional[str] = None  # tooltip / help text
    group: Optional[str] = None        # optional grouping for long lists


class ReferenceDataResponse(BaseModel):
    options: List[ReferenceOption]
    cache_ttl_seconds: int = 300       # portal-side cache hint
    truncated: bool = False            # true if the list was capped
