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
from typing import Dict, List, Literal, Optional, Set

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
# Where a re-run puts new CMORE fields when no section of the reserve's form
# always shows (each is switched off or behind a condition).
ADDED_SECTION_ID = "section-cmore"
ADDED_SECTION_LABEL = "New from CMORE"
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


def build_event_type_payload(spec: EventTypeSpec) -> dict:
    """POST body that creates the type on the EarthRanger v2 event-type
    endpoint. A re-run never sends this; it merges onto the site's schema
    with ``merge_event_type_schema``."""
    properties: Dict[str, dict] = {}
    ui_fields: Dict[str, dict] = {}
    left = []
    required = []
    for field in spec.fields:
        json_prop, ui_field = _property_pair(field, field.key)
        properties[field.key] = json_prop
        ui_fields[field.key] = ui_field
        left.append({"name": field.key, "type": "field"})
        if field.required:
            required.append(field.key)
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
        "value": spec.value,
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
        # The key the site already uses for this CMORE field, else the
        # generated one: keys are never rewritten once created (Decision 6).
        key = existing_keys.get(field.cmore_field_id, field.key)
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


class ChoicePlan(pydantic.BaseModel):
    """What a field's option list needs: ``creates`` are the choice payloads
    the site is missing; ``stale`` are its active values CMORE no longer
    offers (reported, never deactivated: they may be the reserve's own)."""
    creates: List[dict]
    stale: List[str]


def plan_choice_additions(existing: List[dict], desired: List[dict]) -> ChoicePlan:
    existing_values = {c.get("value") for c in existing or []}
    desired_values = {d["value"] for d in desired}
    return ChoicePlan(
        creates=[d for d in desired if d["value"] not in existing_values],
        stale=[c["value"] for c in existing or []
               if c.get("value") and c.get("is_active", True) and c["value"] not in desired_values],
    )


# ----- re-run: merge onto the site's schema -----


class ExistingType(pydantic.BaseModel):
    """A generated event type already on the site: its value, the field key
    the site holds for each CMORE field id, and its schema as a dict."""
    value: str
    keys: Dict[int, str]
    schema_: dict = pydantic.Field(alias="schema")

    class Config:
        allow_population_by_field_name = True

    @property
    def schema(self) -> dict:  # ``schema`` is taken by BaseModel.schema()
        return self.schema_


def _site_property_maps(schema) -> List[dict]:
    """Every ``properties`` object a site's form keeps fields in: the root,
    and the ``then`` of each conditional section, which is where
    EarthRanger's form builder moves a section's fields once it has a
    condition. Parts that are not objects are left out."""
    json_part = schema.get("json") if isinstance(schema, dict) else None
    if not isinstance(json_part, dict):
        return []
    maps = [json_part.get("properties")]
    for clause in json_part.get("allOf") or []:
        then = clause.get("then") if isinstance(clause, dict) else None
        if isinstance(then, dict):
            maps.append(then.get("properties"))
    return [m for m in maps if isinstance(m, dict)]


def site_field_keys(schema) -> Dict[int, str]:
    """Per CMORE field id, the key the site's form holds for it, wherever
    the reserve has put the field."""
    keys: Dict[int, str] = {}
    for properties in _site_property_maps(schema):
        for key in properties:
            field_id = parse_generated_field_key(key)
            if field_id is not None and field_id not in keys:
                keys[field_id] = key
    return keys


class SiteSchemaUnreadable(ValueError):
    """The site returned no form to merge onto; see ``merge_event_type_schema``."""


class MergeResult(pydantic.BaseModel):
    schema_: dict = pydantic.Field(alias="schema")
    added: List[FieldSpec]
    removed_by_site: List[FieldSpec]

    class Config:
        allow_population_by_field_name = True

    @property
    def schema(self) -> dict:  # ``schema`` is taken by BaseModel.schema()
        return self.schema_


def merge_event_type_schema(
    spec: EventTypeSpec,
    site_schema: Optional[dict],
    existing_keys: Dict[int, str],
    known_field_ids: Optional[Set[int]],
) -> MergeResult:
    """The site's schema with only CMORE fields it has never seen added.

    Everything already on the site stays as the reserve left it: titles,
    hidden (deprecated) fields, sections and their conditions, required
    fields, and fields of their own. A CMORE field is added only when it is
    not on the site (``existing_keys``, from ``site_field_keys``)
    and not in ``known_field_ids`` (the fields this type was generated with
    before; ``None`` when there is no record of it, which is not the same as
    a record of no fields); a known field missing from the site was removed
    by the reserve and stays removed.

    New fields go at the end of the last section that always shows (active,
    with no condition), else in a section of their own, which later runs
    reuse as the reserve left it. They are never made required: on a form
    the reserve has shaped, a required field that is hidden or behind a
    condition would block every report.

    Raises ``SiteSchemaUnreadable`` when the site's schema cannot be merged
    onto: its ``json``, ``properties`` or ``ui`` parts are not objects, or it
    has no fields and there is no record of it (a merge would then rebuild
    the form from CMORE and replace the reserve's). The input is not
    modified; with nothing to add it is returned as is."""
    json_in = site_schema.get("json") if isinstance(site_schema, dict) else None
    ui_in = site_schema.get("ui", {}) if isinstance(site_schema, dict) else None
    properties_in = json_in.get("properties") if isinstance(json_in, dict) else None
    if not isinstance(properties_in, dict) or not isinstance(ui_in, dict) \
            or not all(isinstance(ui_in.get(part, {}), dict) for part in ("fields", "sections", "headers")) \
            or not isinstance(ui_in.get("order", []), list):
        raise SiteSchemaUnreadable(f"{spec.value}: the site's form could not be read")
    if not any(_site_property_maps(site_schema)[1:] + [properties_in]) and known_field_ids is None:
        raise SiteSchemaUnreadable(f"{spec.value}: the site's form has no fields and there is no record of it")
    known = known_field_ids or set()
    added: List[FieldSpec] = []
    removed_by_site: List[FieldSpec] = []
    for field in spec.fields:
        if field.cmore_field_id in existing_keys:
            continue
        if field.cmore_field_id in known:
            removed_by_site.append(field)
            continue
        added.append(field)
    if not added:
        return MergeResult(schema=site_schema, added=added, removed_by_site=removed_by_site)
    schema = copy.deepcopy(site_schema)
    json_part = schema["json"]
    properties = json_part["properties"]
    ui = schema.setdefault("ui", {})
    ui_fields = ui.setdefault("fields", {})
    sections = ui.setdefault("sections", {})
    order = ui.setdefault("order", [])
    ui.setdefault("headers", {})
    json_part.setdefault("$schema", "https://json-schema.org/draft/2020-12/schema")
    json_part.setdefault("type", "object")
    # unevaluatedProperties is the reserve's validation policy: not touched.
    target = _section_for_added_fields(json_part, sections, order)
    if target is None:
        if ADDED_SECTION_ID in sections:
            target = ADDED_SECTION_ID  # ours from an earlier run, however the reserve left it
        else:
            target = SECTION_ID if not order else ADDED_SECTION_ID
            label = spec.display if not order else ADDED_SECTION_LABEL
            if target not in sections:
                sections[target] = {"label": label, "columns": 1, "isActive": True, "leftColumn": [], "rightColumn": []}
        if target not in order:
            order.append(target)
    if not isinstance(sections[target], dict):
        raise SiteSchemaUnreadable(f"{spec.value}: section {target!r} of the site's form could not be read")
    column = sections[target].setdefault("leftColumn", [])
    if not isinstance(column, list):
        column = sections[target]["leftColumn"] = []
    for field in added:
        json_prop, ui_field = _property_pair(field, field.key)
        ui_field["parent"] = target
        properties[field.key] = json_prop
        ui_fields[field.key] = ui_field
        column.append({"name": field.key, "type": "field"})
    return MergeResult(schema=schema, added=added, removed_by_site=removed_by_site)


def _section_for_added_fields(json_part: dict, sections: dict, order: List[str]) -> Optional[str]:
    """The last section in ``order`` that always shows: active, and not the
    target of a condition (an ``allOf`` entry's ``x-section``)."""
    conditional = {c.get("x-section") for c in json_part.get("allOf") or [] if isinstance(c, dict)}
    for section_id in reversed(order):
        section = sections.get(section_id)
        if isinstance(section, dict) and section.get("isActive", True) and section_id not in conditional:
            return section_id
    return None
