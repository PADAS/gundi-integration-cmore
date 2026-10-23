"""Build EarthRanger event types from CMORE tags, and recognise them again.

A CMORE tag (one entry of a domain's ``tags`` list in ``/v2/tags/getfull``)
becomes one EarthRanger v2 event type whose *names carry the CMORE ids*:

* category ``cmore_<group>`` (``<group>`` = the integration's owner group),
* event type ``cmore_<group>_<tagid>_<slug>``,
* field key ``f<fieldid>_<slug>``,
* choice value = the CMORE lookup value verbatim.

Because the ids are in the names, delivery can map a generated type back to
its tag and fields with no stored mapping (see ``parse_generated_*``). The
slug is cosmetic: existing objects are matched by the id prefix, never by
slug, so a CMORE rename does not create a second object.

Everything here is pure: no HTTP, no Gundi models. Shapes follow
earthranger-cli's ``schema_gen`` / ``choices`` modules (design:
docs/superpowers/specs/2026-10-23-cmore-tags-to-er-event-types-design.md).
"""

import re
from typing import Optional

# EarthRanger column limits (das/activity/models.py, das/choices/models.py).
EVENT_TYPE_VALUE_LIMIT = 255
CATEGORY_VALUE_LIMIT = 67
CATEGORY_DISPLAY_LIMIT = 100
CHOICE_FIELD_LIMIT = 40
CHOICE_VALUE_LIMIT = 100
HINT_LIMIT = 32

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_GENERATED_TYPE = re.compile(r"^cmore_(\d+)_(\d+)(?:_|$)")
_GENERATED_FIELD = re.compile(r"^f(\d+)_")


def slugify(name: str) -> str:
    """Lowercase ``name``, collapse runs of non-``[a-z0-9]`` to ``_``, strip
    leading/trailing ``_``. May be empty (e.g. ``"???"``)."""
    return _NON_SLUG.sub("_", str(name or "").lower()).strip("_")


def _with_slug(prefix: str, name: str, limit: int) -> str:
    slug = slugify(name)
    value = f"{prefix}_{slug}" if slug else prefix
    return value[:limit].rstrip("_")


def category_value(group: int) -> str:
    return f"cmore_{int(group)}"


def generated_type_prefix(group: int, tag_id: int) -> str:
    """The rename-proof part of a generated event type value, trailing ``_``
    included, for matching existing types on the site."""
    return f"cmore_{int(group)}_{int(tag_id)}_"


def event_type_value(group: int, tag_id: int, tag_name: str) -> str:
    return _with_slug(f"cmore_{int(group)}_{int(tag_id)}", tag_name, EVENT_TYPE_VALUE_LIMIT)


def field_key_prefix(field_id: int) -> str:
    return f"f{int(field_id)}_"


def field_key(field_id: int, field_name: str) -> str:
    """Schema property key for a CMORE field; doubles as the ``Choice.field``
    name for lookups, hence the 40-char limit."""
    return _with_slug(f"f{int(field_id)}", field_name, CHOICE_FIELD_LIMIT)


def parse_generated_event_type(value: str, group: int) -> Optional[int]:
    """The CMORE tag id when ``value`` is a generated event type **of this
    group**; None for another group's type or any hand-made value."""
    match = _GENERATED_TYPE.match(str(value or ""))
    if match is None or int(match.group(1)) != int(group):
        return None
    return int(match.group(2))


def parse_generated_field_key(key: str) -> Optional[int]:
    """The CMORE field id when ``key`` is a generated field key, else None."""
    match = _GENERATED_FIELD.match(str(key or ""))
    return int(match.group(1)) if match else None
