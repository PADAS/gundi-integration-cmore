"""Parse an EarthRanger event-type schema into a flat list of fields.

Used by the mapping-scaffold tooling to discover an ER event type's fields
and their enumerated choices, so ER → CMORE tag mappings can be suggested.

ER's v2 event-type schema is JSON Schema (draft 2020-12), wrapped in a
top-level ``json`` key (with a sibling ``ui`` block). Each field lives under
``json.properties`` with a ``title``. Choice fields don't carry their values
inline; instead they reference an external choice list::

    "animal_sex": {
        "title": "Animal Sex", "type": "string",
        "anyOf": [{"$ref": ".../schemas/choices.json?field=allrep_sex"}]
    }

So choices are resolved in two steps: parse the schema to discover each
field's choice-list ``$ref`` (``ERField.choices_ref``), then fetch that URL
and feed the response to ``parse_choices_json`` to populate ``choices``.

Older/inline ``enum`` + ``enumNames`` schemas are still handled directly.
"""

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlsplit


@dataclass
class ERChoice:
    value: str
    display: str


@dataclass
class ERField:
    key: str
    title: str
    choices: Optional[List[ERChoice]] = None  # populated once resolved
    choices_ref: Optional[str] = None         # URL to fetch choices, if external
    choice_list: Optional[str] = None         # the choice-list name (?field=...)

    @property
    def is_enum(self) -> bool:
        """True if this is a choice field (values may not be resolved yet)."""
        return self.choices is not None or self.choices_ref is not None


def _locate_schema(raw: dict) -> dict:
    """Descend through ER's ``json``/``schema``/``data`` envelopes to the
    object that actually holds ``properties``."""
    node = raw
    for _ in range(6):  # bounded descent
        if not isinstance(node, dict):
            return {}
        if isinstance(node.get("properties"), dict):
            return node
        for key in ("json", "schema", "data"):
            if isinstance(node.get(key), dict):
                node = node[key]
                break
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _enum_with_extra(node: dict) -> Optional[List[ERChoice]]:
    """Parse an ``enum`` (+ optional ``enumNames`` / ``x-enumExtra``) block.

    ER's pre-rendered ``s_format=enum`` schema carries choices as
    ``{"enum": [...], "x-enumExtra": {value: {"display": ...}}}``.
    """
    enum = node.get("enum")
    if not isinstance(enum, list) or not enum:
        return None
    extra = node.get("x-enumExtra")
    names = node.get("enumNames")
    out = []
    for value in enum:
        display = value
        if isinstance(extra, dict) and isinstance(extra.get(value), dict):
            display = extra[value].get("display", value)
        elif isinstance(names, dict):
            display = names.get(value, value)
        out.append(ERChoice(str(value), str(display)))
    if isinstance(names, list) and len(names) == len(enum):
        out = [ERChoice(str(v), str(label)) for v, label in zip(enum, names)]
    return out


def _inline_choices(prop: dict) -> Optional[List[ERChoice]]:
    """Choices carried directly on the property: a top-level ``enum``, an
    ``enum`` nested in an ``anyOf``/``oneOf`` member (ER's pre-rendered form),
    or a ``oneOf``/``anyOf`` list of ``{const/value, title/display}``."""
    direct = _enum_with_extra(prop)
    if direct is not None:
        return direct

    for key in ("anyOf", "oneOf"):
        members = prop.get(key)
        if not isinstance(members, list):
            continue
        for member in members:
            if not isinstance(member, dict):
                continue
            nested = _enum_with_extra(member)
            if nested is not None:
                return nested
        # const/value-style choice members (no enum).
        dict_members = [m for m in members if isinstance(m, dict)]
        if dict_members and all(("const" in m or "value" in m) for m in dict_members):
            return [
                ERChoice(
                    str(m.get("const", m.get("value"))),
                    str(m.get("title", m.get("display", m.get("const", m.get("value"))))),
                )
                for m in dict_members
            ]
    return None


def _choices_ref(prop: dict) -> Optional[str]:
    """A $ref to an external choice list, if present under anyOf/oneOf."""
    for key in ("anyOf", "oneOf"):
        members = prop.get(key)
        if isinstance(members, list):
            for m in members:
                if isinstance(m, dict) and isinstance(m.get("$ref"), str):
                    return m["$ref"]
    if isinstance(prop.get("$ref"), str):
        return prop["$ref"]
    return None


def _choice_list_name(ref: str) -> Optional[str]:
    """Extract the ?field=<name> choice-list name from a choices.json URL."""
    try:
        qs = parse_qs(urlsplit(ref).query)
        values = qs.get("field")
        return values[0] if values else None
    except Exception:
        return None


def parse_er_event_schema(raw: dict) -> List[ERField]:
    """Parse an ER event-type schema response into a flat list of ``ERField``.

    Inline choices are populated immediately; externally-referenced choice
    lists get ``choices_ref`` / ``choice_list`` set and must be resolved with
    a follow-up fetch (see ``parse_choices_json``). Fields with neither are
    free-text. Order follows the schema's ``properties`` insertion order.
    """
    schema = _locate_schema(raw or {})
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    fields: List[ERField] = []
    for key, prop in props.items():
        if not isinstance(prop, dict):
            continue
        title = str(prop.get("title") or key)
        choices = _inline_choices(prop)
        ref = None if choices else _choices_ref(prop)
        fields.append(ERField(
            key=key,
            title=title,
            choices=choices,
            choices_ref=ref,
            choice_list=_choice_list_name(ref) if ref else None,
        ))
    return fields


def parse_choices_json(raw) -> List[ERChoice]:
    """Parse an ER choices.json response into value/display pairs.

    Tolerant of the shapes ER uses for a choice list: a list of
    ``{value/const, display/title/name}`` dicts, a JSON-Schema fragment with
    ``oneOf``/``anyOf`` of ``{const, title}``, or ``enum`` + ``enumNames``.
    """
    # A bare list of choice dicts.
    if isinstance(raw, list):
        out = []
        for m in raw:
            if isinstance(m, dict):
                value = m.get("value", m.get("const", m.get("id")))
                display = m.get("display", m.get("title", m.get("name", value)))
                if value is not None:
                    out.append(ERChoice(str(value), str(display)))
            elif m is not None:
                out.append(ERChoice(str(m), str(m)))
        return out

    if isinstance(raw, dict):
        # Reuse the inline-property parser for enum / oneOf / anyOf shapes.
        inline = _inline_choices(raw)
        if inline is not None:
            return inline
        # Some endpoints nest the list under a key.
        for key in ("choices", "results", "items", "data"):
            if isinstance(raw.get(key), list):
                return parse_choices_json(raw[key])
    return []
