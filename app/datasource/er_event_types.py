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

import copy
import re
from typing import Dict, List, Literal, Optional, Set, Tuple

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
    """Choice options for a lookup field. EarthRanger's ``Choice.clean()``
    accepts only ``^\\w+$`` values, so the value is the slug of the CMORE
    option (the lookup id is appended when two options slug alike) and the
    display keeps the CMORE text. Delivery normalises both sides, so the
    slug still resolves to the CMORE option."""
    lookups = sorted(
        (lk for lk in (raw_field.get("lookups") or []) if lk.get("value") is not None),
        key=lambda lk: (lk.get("order") if lk.get("order") is not None else 0, lk.get("id") or 0),
    )
    options: List[ChoiceOption] = []
    seen = set()
    for index, lk in enumerate(lookups, start=1):
        text = str(lk["value"])
        value = (slugify(text) or f"option_{lk.get('id') or index}")[:CHOICE_VALUE_LIMIT]
        if value in seen:
            suffix = f"_{lk.get('id') or index}"
            value = value[: CHOICE_VALUE_LIMIT - len(suffix)] + suffix
        seen.add(value)
        options.append(ChoiceOption(
            value=value,
            display=text[:CHOICE_VALUE_LIMIT],
            ordernum=int(lk.get("order") if lk.get("order") is not None else index),
        ))
    return options


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


# ----- v2 schema payload -----

SECTION_ID = "section-1"
CHOICE_MODEL = "activity.event"
_CHOICES_REF = "/api/v2.0/schemas/choices.json?field={field}"

# EarthRanger's meta-schema requires "deprecated" on every json property and
# has no integer variant (numbers are "number"). From earthranger-cli.
_SCALAR_JSON: Dict[str, dict] = {
    "string": {"type": "string"},
    "textarea": {"type": "string"},
    "number": {"type": "number"},
    "boolean": {"type": "boolean"},
    "datetime": {"type": "string", "format": "date-time"},
}
_SCALAR_UI: Dict[str, dict] = {
    "string": {"type": "TEXT", "inputType": "SHORT_TEXT"},
    "textarea": {"type": "TEXT", "inputType": "LONG_TEXT"},
    "number": {"type": "NUMERIC"},
    "boolean": {"type": "BOOLEAN"},
    "datetime": {"type": "DATE_TIME"},
}


def _effective_key(field: FieldSpec, existing_keys: Dict[int, str]) -> str:
    """The key an existing site already uses for this CMORE field, else the
    generated one. Keys are never rewritten once created (Decision 6)."""
    return existing_keys.get(field.cmore_field_id, field.key)


def _property_pair(field: FieldSpec, key: str) -> tuple:
    if field.type in _SCALAR_JSON:
        json_prop = {**_SCALAR_JSON[field.type], "title": field.label, "deprecated": False}
        ui_field = {**_SCALAR_UI[field.type], "parent": SECTION_ID}
    else:
        ref = _CHOICES_REF.format(field=key)
        ui_field = {"type": "CHOICE_LIST", "inputType": "DROPDOWN", "parent": SECTION_ID}
        if field.type == "multiselect":
            json_prop = {
                "type": "array",
                "title": field.label,
                "deprecated": False,
                "uniqueItems": True,
                "items": {"type": "string", "anyOf": [{"$ref": ref}]},
            }
        else:
            json_prop = {"type": "string", "title": field.label, "deprecated": False, "anyOf": [{"$ref": ref}]}
    if field.hint:
        ui_field["placeholder"] = field.hint
    return json_prop, ui_field


def build_event_type_payload(
    spec: EventTypeSpec,
    *,
    existing_value: Optional[str] = None,
    existing_keys: Dict[int, str] = {},
) -> dict:
    """POST/PATCH body for the EarthRanger v2 event-type endpoint. On a
    re-run ``existing_value`` and ``existing_keys`` keep what the site already
    has, so only display, schema and is_active change."""
    properties: Dict[str, dict] = {}
    ui_fields: Dict[str, dict] = {}
    left = []
    required = []
    for field in spec.fields:
        key = _effective_key(field, existing_keys)
        json_prop, ui_field = _property_pair(field, key)
        properties[key] = json_prop
        ui_fields[key] = ui_field
        left.append({"name": key, "type": "field"})
        if field.required:
            required.append(key)
    schema = {
        "json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "unevaluatedProperties": False,
            "properties": properties,
            "required": required,
        },
        "ui": {
            "fields": ui_fields,
            "headers": {},
            "order": [SECTION_ID],
            "sections": {
                SECTION_ID: {
                    "label": spec.display,
                    "columns": 1,
                    "isActive": True,
                    "leftColumn": left,
                    "rightColumn": [],
                }
            },
        },
    }
    return {
        "value": existing_value or spec.value,
        "display": spec.display,
        "category": spec.category_value,
        "is_active": True,
        "schema": schema,
    }


# ----- choices -----


def build_choice_records(spec: EventTypeSpec, existing_keys: Dict[int, str] = {}) -> Dict[str, List[dict]]:
    """Desired ``Choice`` records per lookup field key, in CMORE order."""
    records: Dict[str, List[dict]] = {}
    for field in spec.fields:
        if field.type not in ("select", "multiselect"):
            continue
        key = _effective_key(field, existing_keys)
        records[key] = [
            {
                "model": CHOICE_MODEL,
                "field": key,
                "value": option.value,
                "display": option.display,
                "is_active": True,
                "ordernum": option.ordernum,
            }
            for option in field.options
        ]
    return records


def plan_choice_additions(existing: List[dict], desired: List[dict]) -> Tuple[List[dict], List[str]]:
    """The choices to create for one field, and the active values on the site
    that CMORE no longer offers. Existing choices are never changed: their
    wording, order and active state are the reserve's (a re-run keeps site
    edits). A stale value is reported, not deactivated, because a value CMORE
    dropped cannot be told from one the reserve added."""
    existing_values = {c.get("value") for c in existing or []}
    desired_values = {d["value"] for d in desired}
    creates = [d for d in desired if d["value"] not in existing_values]
    stale = [c["value"] for c in existing or [] if c.get("is_active", True) and c.get("value") not in desired_values]
    return creates, stale


# ----- re-run: merge onto the site's schema -----


def merge_event_type_schema(
    spec: EventTypeSpec,
    site_schema: dict,
    existing_keys: Dict[int, str],
    known_field_ids: Set[int],
) -> Tuple[dict, List[FieldSpec], List[FieldSpec]]:
    """The site's schema with only CMORE fields it has never seen added.

    Everything already on the site stays as the reserve left it: titles,
    hidden (deprecated) fields, sections and their conditions, required
    fields, and fields of their own. A CMORE field is added only when it is
    not on the site and not in ``known_field_ids`` (the fields this type was
    generated with before); a known field missing from the site was removed
    by the reserve and stays removed. New fields go at the end of the last
    section. Returns ``(schema, added, removed_by_site)``; the input is not
    modified."""
    schema = copy.deepcopy(site_schema or {})
    json_part = schema.setdefault("json", {})
    properties = json_part.setdefault("properties", {})
    ui = schema.setdefault("ui", {})
    ui_fields = ui.setdefault("fields", {})
    sections = ui.setdefault("sections", {})
    order = ui.setdefault("order", [])
    ui.setdefault("headers", {})
    added: List[FieldSpec] = []
    removed_by_site: List[FieldSpec] = []
    for field in spec.fields:
        key = existing_keys.get(field.cmore_field_id)
        if key and key in properties:
            continue
        if field.cmore_field_id in known_field_ids:
            removed_by_site.append(field)
            continue
        added.append(field)
    if not added:
        return schema, added, removed_by_site
    json_part.setdefault("$schema", "https://json-schema.org/draft/2020-12/schema")
    json_part.setdefault("type", "object")
    json_part.setdefault("unevaluatedProperties", False)
    required = json_part.setdefault("required", [])
    if not order:
        order.append(SECTION_ID)
    target = order[-1]
    section = sections.setdefault(target, {
        "label": spec.display, "columns": 1, "isActive": True, "leftColumn": [], "rightColumn": [],
    })
    column = section.setdefault("leftColumn", [])
    for field in added:
        json_prop, ui_field = _property_pair(field, field.key)
        ui_field["parent"] = target
        properties[field.key] = json_prop
        ui_fields[field.key] = ui_field
        column.append({"name": field.key, "type": "field"})
        if field.required and field.key not in required:
            required.append(field.key)
    return schema, added, removed_by_site
