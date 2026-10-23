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
from typing import Dict, List, Literal, Optional

import pydantic

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


# ----- event type spec -----

FieldType = Literal["string", "textarea", "number", "boolean", "datetime", "select", "multiselect"]

# CMORE dataType → EarthRanger field type. Absent here = unsupported (skipped).
_TYPE_TABLE: Dict[str, FieldType] = {
    "String": "string",
    "IDNumber": "string",
    "Text": "textarea",
    "Number": "number",
    "Duration": "number",
    "Boolean": "boolean",
    "DateTime": "datetime",
    "Lookup": "select",
    "FixedLookup": "select",
}
_LOOKUP_TYPES = ("Lookup", "FixedLookup")
REPORTED_BY_FIELD_NAME = "reported by"
REPORTED_BY_SKIP_REASON = "filled from the EarthRanger reporter"


class ChoiceOption(pydantic.BaseModel):
    value: str
    display: str
    ordernum: int


class FieldSpec(pydantic.BaseModel):
    key: str
    cmore_field_id: int
    label: str
    type: FieldType
    required: bool = False
    hint: Optional[str] = None
    options: List[ChoiceOption] = []  # select / multiselect only


class SkippedField(pydantic.BaseModel):
    cmore_field_id: int
    name: str
    reason: str


class ShallowField(pydantic.BaseModel):
    """A lookup whose options have children (``linkedLookups``). Only the
    top level is offered until the EarthRanger API can create sub-choices."""
    cmore_field_id: int
    name: str
    nested_option_count: int


class EventTypeSpec(pydantic.BaseModel):
    value: str
    display: str
    category_value: str
    cmore_tag_id: int
    fields: List[FieldSpec]
    skipped: List[SkippedField]
    shallow_fields: List[ShallowField]


def _count_nested(lookups: list) -> int:
    total = 0
    for lookup in lookups or []:
        children = lookup.get("linkedLookups") or []
        total += len(children) + _count_nested(children)
    return total


def _options(raw_field: dict) -> List[ChoiceOption]:
    lookups = sorted(
        (lk for lk in (raw_field.get("lookups") or []) if lk.get("value") is not None),
        key=lambda lk: (lk.get("order") if lk.get("order") is not None else 0, lk.get("id") or 0),
    )
    return [
        ChoiceOption(
            value=str(lk["value"])[:CHOICE_VALUE_LIMIT],
            display=str(lk["value"])[:CHOICE_VALUE_LIMIT],
            ordernum=int(lk.get("order") if lk.get("order") is not None else index),
        )
        for index, lk in enumerate(lookups, start=1)
    ]


def build_event_type_spec(raw_tag: dict, group: int) -> EventTypeSpec:
    """One EarthRanger event type for ``raw_tag`` (a ``/v2/tags/getfull``
    tag entry), named for ``group`` (the integration's owner group id)."""
    tag_id = int(raw_tag["id"])
    tag_name = str(raw_tag.get("name") or "")
    fields: List[FieldSpec] = []
    skipped: List[SkippedField] = []
    shallow: List[ShallowField] = []
    for raw_field in raw_tag.get("fields") or []:
        field_id = int(raw_field["id"])
        name = str(raw_field.get("name") or "")
        data_type = str(raw_field.get("dataType") or "String")
        if name.strip().lower() == REPORTED_BY_FIELD_NAME:
            skipped.append(SkippedField(cmore_field_id=field_id, name=name, reason=REPORTED_BY_SKIP_REASON))
            continue
        field_type = _TYPE_TABLE.get(data_type)
        if field_type is None:
            skipped.append(SkippedField(cmore_field_id=field_id, name=name, reason=f"unsupported data type {data_type}"))
            continue
        options: List[ChoiceOption] = []
        if data_type in _LOOKUP_TYPES:
            if raw_field.get("allowMultipleValues"):
                field_type = "multiselect"
            options = _options(raw_field)
            nested = _count_nested(raw_field.get("lookups"))
            if nested:
                shallow.append(ShallowField(cmore_field_id=field_id, name=name, nested_option_count=nested))
        hint = str(raw_field.get("hint") or "").strip()[:HINT_LIMIT] or None
        fields.append(FieldSpec(
            key=field_key(field_id, name),
            cmore_field_id=field_id,
            label=name,
            type=field_type,
            required=bool(raw_field.get("isMandatory")),
            hint=hint,
            options=options,
        ))
    return EventTypeSpec(
        value=event_type_value(group, tag_id, tag_name),
        display=tag_name,
        category_value=category_value(group),
        cmore_tag_id=tag_id,
        fields=fields,
        skipped=skipped,
        shallow_fields=shallow,
    )
