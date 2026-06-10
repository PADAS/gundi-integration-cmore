"""Suggest ER → CMORE tag mappings to reduce hand-authoring.

Given an ER event type's fields (``er_schema.ERField``) and a CMORE tag
(``tag_index.TagInfo``), this proposes:

* which CMORE field each ER field maps to (token-set name matching), and
* for Lookup fields, how each ER choice maps to a CMORE option.

The output mirrors the ``CmoreTagMapping`` config shape and is meant to be
reviewed/corrected by an operator (interactively or by editing), not trusted
blindly. Everything here is pure and deterministic so it can be unit-tested
against real schemas.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.datasource.er_schema import ERChoice, ERField
from app.datasource.tag_index import FieldInfo, TagInfo

# Tokens that carry no matching signal in ER/CMORE field names.
_STOPWORDS = {"of", "the", "a", "an", "and", "to", "for", "by", "or"}

LOOKUP_TYPES = ("Lookup", "FixedLookup")


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _tokens(value: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(value).lower()) if t and t not in _STOPWORDS}


def field_match_score(er_field: ERField, cmore_field: FieldInfo) -> float:
    """Jaccard overlap of word tokens between the ER field (key + title) and
    the CMORE field name. Order-insensitive, stop-words removed."""
    er_tokens = _tokens(er_field.key) | _tokens(er_field.title)
    cm_tokens = _tokens(cmore_field.name)
    if not er_tokens or not cm_tokens:
        return 0.0
    return len(er_tokens & cm_tokens) / len(er_tokens | cm_tokens)


def suggest_cmore_field(
    er_field: ERField, tag_info: TagInfo, threshold: float = 0.5
) -> Tuple[Optional[FieldInfo], float]:
    """Best CMORE field for an ER field, or (None, best_score) below threshold."""
    best: Optional[FieldInfo] = None
    best_score = 0.0
    for cmore_field in tag_info.fields.values():
        score = field_match_score(er_field, cmore_field)
        if score > best_score:
            best, best_score = cmore_field, score
    if best is not None and best_score >= threshold:
        return best, best_score
    return None, best_score


def suggest_lookup_value(er_choice: ERChoice, cmore_field: FieldInfo) -> Optional[str]:
    """Best CMORE lookup option for an ER choice, matching its value OR display
    (normalized). Returns the canonical CMORE value string, or None."""
    candidates = {_normalize(er_choice.value), _normalize(er_choice.display)}
    for lookup in cmore_field.lookups or []:
        option = lookup.get("value")
        if option is not None and _normalize(option) in candidates:
            return option
    return None


@dataclass
class FieldScaffold:
    event_details_key: str
    cmore_field_name: str
    value_mappings: List[dict] = field(default_factory=list)  # {from_value, to_value}
    # ER choices we could not map to a CMORE option (need a human decision).
    unresolved_choices: List[str] = field(default_factory=list)


@dataclass
class ScaffoldResult:
    event_type: str
    tag_name: str
    fields: List[FieldScaffold] = field(default_factory=list)
    unmatched_er_fields: List[str] = field(default_factory=list)   # no CMORE field
    uncovered_cmore_fields: List[str] = field(default_factory=list)  # no ER field

    def to_config_entry(self) -> dict:
        """Render as a CmoreTagMapping-shaped dict for the DeliverConfig."""
        return {
            "event_type": self.event_type,
            "tag_name": self.tag_name,
            "field_mappings": [
                {
                    "event_details_key": f.event_details_key,
                    "cmore_field_name": f.cmore_field_name,
                    **({"value_mappings": f.value_mappings} if f.value_mappings else {}),
                }
                for f in self.fields
            ],
        }


def build_scaffold(
    er_fields: List[ERField],
    tag_info: TagInfo,
    event_type: str,
    *,
    threshold: float = 0.5,
    include_unresolved_blanks: bool = True,
) -> ScaffoldResult:
    """Propose a full mapping from an ER event type to a CMORE tag.

    For each ER field, pick the best CMORE field (token-set match). For Lookup
    CMORE fields, map each ER choice to a CMORE option:
      * trivial matches (ER value already resolves) are omitted — not needed,
      * non-trivial matches are pre-filled,
      * unmatched choices get a blank ``to_value`` for the operator to fill
        (when ``include_unresolved_blanks``) and are also reported.
    """
    result = ScaffoldResult(event_type=event_type, tag_name=tag_info.name)
    matched_cmore_names = set()

    for er_field in er_fields:
        cmore_field, _score = suggest_cmore_field(er_field, tag_info, threshold)
        if cmore_field is None:
            result.unmatched_er_fields.append(er_field.key)
            continue
        matched_cmore_names.add(cmore_field.name)

        scaffold = FieldScaffold(
            event_details_key=er_field.key,
            cmore_field_name=cmore_field.name,
        )

        if er_field.is_enum and cmore_field.data_type in LOOKUP_TYPES:
            for choice in er_field.choices or []:
                option = suggest_lookup_value(choice, cmore_field)
                if option is None:
                    scaffold.unresolved_choices.append(choice.value)
                    if include_unresolved_blanks:
                        scaffold.value_mappings.append(
                            {"from_value": choice.value, "to_value": ""}
                        )
                elif _normalize(choice.value) != _normalize(option):
                    # Non-trivial: needs an explicit mapping at runtime.
                    scaffold.value_mappings.append(
                        {"from_value": choice.value, "to_value": option}
                    )
                # else: trivial freebie (auto-resolves at runtime) — omit.

        result.fields.append(scaffold)

    result.uncovered_cmore_fields = [
        name for name in tag_info.fields if name not in matched_cmore_names
    ]
    return result
