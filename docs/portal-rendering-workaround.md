# DeliverConfig schema-shape workaround for portal rendering

**Status:** active workaround
**Tracking ticket:** [GUNDI-5371](https://allenai.atlassian.net/browse/GUNDI-5371)
**Affects:** `app/actions/configurations.py` — `DeliverConfig` and its nested models

## Why this exists (future-self note)

If you're reading this and wondering "why is `DeliverConfig` using `List[FooMapping]` with an explicit key field instead of the obvious `Dict[str, Foo]`?" — read on. This is a deliberate, temporary workaround for two portal bugs.

## The natural shape we want

`DeliverConfig` is operator-configured per customer integration via the Gundi portal admin form. The natural Pydantic shape would be:

```python
class CmoreTagMapping(BaseModel):
    tag_name: str
    field_mappings: Dict[str, str]            # gundi_key → cmore_field_name

class DeliverConfig(PushActionConfiguration):
    event_type_to_tag: Optional[Dict[str, CmoreTagMapping]]
    subject_type_to_affiliation: Optional[Dict[str, Affiliation]]
    subject_type_to_classification: Optional[Dict[str, CmoreClassification]]
```

This is what the action runner originally registered with the portal.

## What goes wrong in the portal

The Gundi portal renders integration action configs through a custom React component (`CustomArrayField.tsx` and friends) layered on top of `react-jsonschema-form`. That layer has two bugs:

1. **`additionalProperties` with object values renders as "[object Object]"** in a single read-only text input. Operators cannot view, edit, or add entries. Verified against the official rjsf playground using identical schema + ui_schema + form data — stock rjsf renders correctly, so the bug is in the portal's custom widget.

2. **`CustomArrayField` crashes on non-array data** with `TypeError: c.map is not a function`, breaking the entire config page. This blew up when we converted the schema and the legacy dict-shaped saved data became incompatible with the new array schema.

Both bugs are tracked in [GUNDI-5371](https://allenai.atlassian.net/browse/GUNDI-5371).

## The workaround

Restructure every `Dict[str, NestedModel]` into `List[NestedModel-with-explicit-key]`, AND flatten any further-nested object fields into the parent array element. The portal renders array-of-flat-objects correctly, even though the schema is more verbose for operators.

```python
class CmoreFieldMapping(BaseModel):
    event_details_key: str
    cmore_field_name: str

class CmoreTagMapping(BaseModel):
    event_type: str                              # was the outer dict key
    tag_name: str
    field_mappings: List[CmoreFieldMapping]      # was Dict[str, str]

class SubjectAffiliationMapping(BaseModel):
    subject_type: str                            # was the dict key
    affiliation: Affiliation                     # was the dict value

class SubjectClassificationMapping(BaseModel):
    subject_type: str                            # was the dict key
    # CmoreClassification's four fields flattened directly onto the item
    # because the portal also can't render nested object fields inside
    # array items:
    battleDimension: Optional[str] = None
    force: Optional[str] = None
    type: Optional[str] = None
    role: Optional[str] = None

class DeliverConfig(PushActionConfiguration):
    event_type_to_tag: Optional[List[CmoreTagMapping]]
    subject_type_to_affiliation: Optional[List[SubjectAffiliationMapping]]
    subject_type_to_classification: Optional[List[SubjectClassificationMapping]]
```

`CmoreClassification` itself stays as a nested object — it's the **wire shape** the C-more API expects. The handler reassembles a `CmoreClassification` from the flat `SubjectClassificationMapping` fields before posting to C-more.

## Tradeoffs (read these before reverting OR before adding more nested config)

- **No dict-key uniqueness guarantee.** Operators can accidentally create duplicate entries with the same `event_type` or `subject_type`. We mitigate at runtime by first-match-wins in handler lookups.
- **Operators see and type more.** The "event_type" string they used to type as the dict key now appears as a labeled field on each list item. Slightly worse UX, but tolerable, and the form actually renders.
- **More verbose JSON shape** in the saved config and in activity logs. Migration scripts (e.g., the workaround data conversion noted below) are non-trivial.
- **Future additions of `Dict[str, NestedModel]` config will hit the same bug.** Don't add new dict-of-object config fields until GUNDI-5371 is fixed; use the list pattern.
- **Future additions of nested objects inside array items will hit the second-level bug.** If you must nest, flatten the inner object's fields onto the parent (and reassemble in the handler).

## Revert plan when GUNDI-5371 is fixed

When the portal renders `additionalProperties` of object correctly AND `CustomArrayField` handles non-array data defensively:

1. **Verify in the rjsf playground equivalent OR in the portal directly** that `additionalProperties` of object renders the way stock rjsf renders it (see ticket for repro JSON). The portal team should attach proof.
2. **Revert `DeliverConfig` to the dict shape.** This is the inverse of commit `c84e9b6` (the Dict→List refactor) plus commit `2fb2373` (the CmoreClassification flatten). Restore:
   - `event_type_to_tag: Optional[Dict[str, CmoreTagMapping]]`
   - `CmoreTagMapping.field_mappings: Dict[str, str]` (drop `CmoreFieldMapping` entirely)
   - `subject_type_to_affiliation: Optional[Dict[str, Affiliation]]`
   - `subject_type_to_classification: Optional[Dict[str, CmoreClassification]]` (restore CmoreClassification nesting)
   - Drop `SubjectAffiliationMapping` and `SubjectClassificationMapping` wrapper classes.
3. **Restore the handler lookup pattern** from list-scan back to `dict.get(key)`. `_find_subject_mapping` returns to its earlier dict-friendly form (or just gets inlined).
4. **Restore the `ui_schema()` override** to use `additionalProperties` hints instead of `items`.
5. **Migrate existing saved configs** in the portal database from list shape back to dict shape. The transform is mechanical: each list item becomes a dict entry keyed by its `subject_type` / `event_type` field, and the SubjectClassificationMapping's flat classification fields nest back under a `classification` sub-object.
6. **Delete this file.**

## Data migration: dict → list (used when the workaround landed)

For reference, here's the shape transformation we applied to dev data:

**Before (dict):**

```json
{
  "event_type_to_tag": {
    "poacher_sighting": {
      "tag_name": "Poacher Sighting",
      "field_mappings": {
        "direction": "Direction",
        "num_people": "Number of People"
      }
    }
  },
  "subject_type_to_classification": {
    "dog": {"role": "K9", "type": "DOG", "force": "ANIMAL", "battleDimension": "LAND"}
  }
}
```

**After (list, with classification flattened):**

```json
{
  "event_type_to_tag": [
    {
      "event_type": "poacher_sighting",
      "tag_name": "Poacher Sighting",
      "field_mappings": [
        {"event_details_key": "direction", "cmore_field_name": "Direction"},
        {"event_details_key": "num_people", "cmore_field_name": "Number of People"}
      ]
    }
  ],
  "subject_type_to_classification": [
    {"subject_type": "dog", "role": "K9", "type": "DOG", "force": "ANIMAL", "battleDimension": "LAND"}
  ]
}
```

## Related files

- `app/actions/configurations.py` — DeliverConfig + the workaround Mapping classes; in-file comments reference this doc and GUNDI-5371.
- `app/actions/handlers.py` — `_push_event`, `_find_subject_mapping`, `_gnode_request_for` — the lookup logic that scans lists instead of dict-get.
- `app/actions/tests/test_handlers.py` — tests built against the list shape.

## Related commits

- `c84e9b6` — Dict→List refactor
- `2fb2373` — Flatten `CmoreClassification` into `SubjectClassificationMapping`
- `b68048d` — earlier ui_schema attempt with `additionalProperties` hints (kept for some entries, but the dict-form ones were superseded by the refactor)
