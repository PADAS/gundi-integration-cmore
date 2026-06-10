"""Parse an EarthRanger event-type schema into a flat list of fields.

Used by the mapping-scaffold tooling to discover an ER event type's fields
and their enumerated choices, so ER → CMORE tag mappings can be suggested.

ER returns the schema as a JSON-schema-ish object: ``properties`` keyed by the
event_details key, each with a ``title`` and (for enumerated fields) ``enum``
plus ``enumNames``. ``enumNames`` is seen both as a dict (value → label) and as
a list parallel to ``enum``; both are handled. The schema is sometimes wrapped
in ``data`` / ``schema`` envelopes depending on the endpoint/version, so the
parser descends to the first object that carries ``properties``.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ERChoice:
    value: str
    display: str


@dataclass
class ERField:
    key: str
    title: str
    choices: Optional[List[ERChoice]] = None  # None => free-text (no enum)

    @property
    def is_enum(self) -> bool:
        return self.choices is not None


def _locate_schema(raw: dict) -> dict:
    """Descend through ER's ``data``/``schema`` envelopes to the object that
    actually holds ``properties``."""
    node = raw
    for _ in range(5):  # bounded descent; ER nests at most a couple levels
        if not isinstance(node, dict):
            return {}
        if isinstance(node.get("properties"), dict):
            return node
        for key in ("schema", "data"):
            if isinstance(node.get(key), dict):
                node = node[key]
                break
        else:
            return {}
    return node if isinstance(node, dict) else {}


def _parse_choices(prop: dict) -> Optional[List[ERChoice]]:
    enum = prop.get("enum")
    if not isinstance(enum, list) or not enum:
        return None
    names = prop.get("enumNames")
    choices: List[ERChoice] = []
    if isinstance(names, dict):
        for value in enum:
            choices.append(ERChoice(value=str(value), display=str(names.get(value, value))))
    elif isinstance(names, list) and len(names) == len(enum):
        for value, label in zip(enum, names):
            choices.append(ERChoice(value=str(value), display=str(label)))
    else:
        for value in enum:
            choices.append(ERChoice(value=str(value), display=str(value)))
    return choices


def parse_er_event_schema(raw: dict) -> List[ERField]:
    """Parse an ER event-type schema response into a flat list of ``ERField``.

    Tolerant of ER's known envelope shapes. Fields with no ``enum`` are
    returned with ``choices=None`` (free-text). Order follows the schema's
    ``properties`` insertion order.
    """
    schema = _locate_schema(raw or {})
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    fields: List[ERField] = []
    for key, prop in props.items():
        if not isinstance(prop, dict):
            continue
        title = prop.get("title") or key
        fields.append(ERField(key=key, title=str(title), choices=_parse_choices(prop)))
    return fields
