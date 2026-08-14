# Tag-ID mapping keys — design

**Date:** 2026-08-14
**Status:** Approved (design review in chat, 2026-08-14)
**Origin:** CSIR integration-testing feedback (2026-08-07), points 4–5: tag
names are mutable, so name-based mappings can break or misclassify on rename;
use the immutable CMORE Tag ID as the mapping key, with the name as display.

## Problem

`CmoreTagMapping.tag_name` and `CmoreFieldMapping.cmore_field_name` store
CMORE tag/field *names*, resolved to numeric `tagId`/`fieldId` at delivery
time via `/v2/tags/getfull` (cached per process in
`app/datasource/tag_index.py`). A rename in CMORE silently degrades events to
unclassified (tag) or drops field values (field) — and can *misclassify* if
another tag later takes the old name. The same fragility exists for fields.
Lookup **values** are out of scope: CMORE matches those by value string, so
there is no ID to use.

The original reason for names — configs were hand-typed, and names are what a
person can read and verify — is obsolete now that the reference-data dropdown
work stores machine values and displays labels.

## Decisions

1. **Scope: tags and fields together.** Both are sent by numeric ID and share
   the rename fragility; schema churn is the expensive part, paid once.
2. **Shape: one string field per concept, holding ID-or-name.** The portal
   reference widget writes `ReferenceOption.value` (a string) and displays
   `label`, so no separate display-name field is needed. Fields stay JSON-type
   string (an integer type would fight both the widget and free text).
3. **No back-compat for existing stored configs.** They are demo-only
   (confirmed 2026-08-14), so fields are renamed honestly rather than leaving
   IDs in a field called `tag_name`. Typed tag/field *names* remain a working
   fallback — that is a live feature (portals without reference support,
   `allow_free_text`), not a migration shim.

## 1. Config schema (`app/actions/configurations.py`)

- `CmoreTagMapping.tag_name` → `tag: str`, title "CMORE Tag". Description:
  preferably the immutable tag ID (what the dropdown stores); an exact tag
  name also works but breaks if the tag is renamed.
- `CmoreFieldMapping.cmore_field_name` → `cmore_field: str`, title
  "CMORE Field", same ID-or-name semantics.
- `CmoreValueMapping` unchanged.
- UI schema: placeholders become `e.g. 8443 or Rhino Carcass` (and the field
  equivalent); `ui:order` lists and `$data` cascade paths follow the renames.
- **Accepted trade:** dropdown-authored config JSON no longer contains the
  human-readable name (`"tag": "8443"`). The widget shows the name, delivery
  logs print name + ID, and the scaffold CLI prints a legend (§4); raw-JSON
  readability drops by design.

## 2. Tag index and resolution (`app/datasource/tag_index.py`)

- `_build_index` produces both views in one pass:
  - `TagIndexData` dataclass: `by_id: Dict[int, TagInfo]`,
    `by_name: Dict[str, TagInfo]` (existing last-wins cross-domain collision
    warning preserved on the name view; IDs cannot collide).
  - `TagInfo` gains `fields_by_id` / `fields_by_name` and a
    `resolve_field(ref)` method replacing `field_by_name`.
  - `TagInfo`/`FieldInfo` stay dataclasses, matching the existing file.
- **One resolution rule for tags and fields:**
  1. Strip whitespace. All-digit ref matching an existing ID → resolve by ID
     (rename-immune — the point of the change).
  2. Otherwise, or digit ref matching no ID → exact name match (today's
     behavior).
  3. Pathological precedence: a tag *named* "8443" while a different tag *has*
     ID 8443 resolves to the ID. Deterministic and documented; an all-digit
     name with no colliding ID still resolves via rule 2.
- Delivery path (`_build_event_tag` in `app/actions/handlers.py`):
  `tag_index.get(...)` takes the config ref and returns `TagInfo` as before —
  payload construction (`tagId`, `fieldId`, value resolution) untouched.
  Degradation paths unchanged: unknown tag → post unclassified + warning;
  unknown field → skip + warning.
- Cache semantics unchanged: lazy, keyed `(base_url, integration_id)`,
  process-lifetime. ID-based mappings survive renames even with a stale cache
  — the ID is stable across the rename.

## 3. Reference actions and cascades (`app/actions/handlers.py`, annotations)

- `action_list_tag_names`: `ReferenceOption(value=str(tag.id),
  label=tag.name, group=tag.domain, description=f"ID {tag.id}")`; sort by
  `(group, label)`.
- `action_list_tag_fields`: `value=str(f.id)`, `label=f.name`,
  `description=f"{f.data_type} · ID {f.id}"`.
- `action_list_field_options`: **unchanged** — lookup options are matched by
  value string in CMORE.
- Query models renamed and ID-or-name-tolerant via the §2 rule:
  `ListTagFieldsQuery.tag`, `ListFieldOptionsQuery.tag` + `.field`. Required
  because `$data` cascades pass the stored config value — an ID for
  dropdown-authored configs, a name for free-texted ones.
- Annotations update mechanically: `{"tag": {"$data": "../../tag"}}` etc.
- **Action names stay** (`list_tag_names`, …): they are the registered,
  dev-verified surface; renaming buys aesthetics at re-verification cost.
- Portal (gundi-portal PRs #340/#341): **zero changes** — the widget is
  value/label-generic. ER-side provider actions untouched (event-type slugs
  are already immutable keys).

## 4. Scaffold CLI, docs, bundled fix

- `mapping_scaffold.py` / `cli.py`: emit `"tag": str(tag_info.id)` and
  `"cmore_field": str(field.id)`; the CLI's human-facing output gains a
  name↔ID legend, one line per mapping
  (`tag 8443 = "Rhino Carcass"`, `field 261 = "Evidence Type" (Lookup)`) —
  extend the existing review summary if present, else add the print lines.
- Docs (`configuration.md`, `tutorial-sync-event-type.md`,
  `troubleshooting.md`): "CMORE Tag Name" → "CMORE Tag"; the ID-or-name rule
  and precedence stated once in `configuration.md`, referenced elsewhere; the
  tutorial notes the ID as the preferred rename-proof value and where to find
  it; troubleshooting splits "tag ref doesn't resolve" into bad-ID vs
  renamed-tag-under-name-mapping.
- **Bundled (CSIR point 2):** `AuthenticateConfig.base_url` loses its
  test-instance default and becomes required; the URL pattern moves to
  description/placeholder. Same file, same registration cycle.
- Email draft (`dev/2026-08-14-email-draft-csir-feedback-reply.md`): replace
  the "existing name-based configurations will keep working" promise with
  "typing a tag name will continue to work as a fallback, but the ID is now
  the preferred key."

## 5. Testing and verification

- `test_tag_index.py`: dual-index build; the precedence table as explicit
  cases (ID hit; digit ref falling back to all-digit name; plain name;
  ID-beats-digit-name; whitespace); field resolution through the same cases;
  collision warning still fires and ID lookups are immune to it.
- `test_handlers.py`: `_build_event_tag` from ID-based, name-based, and mixed
  mappings; unknown ref → unclassified + warning. Rename simulation: index
  built, mapping by ID, index rebuilt with the tag renamed → same `tagId`
  delivered; same scenario name-mapped → unclassified + warning (the failure
  being eliminated).
- Reference actions: option shape (`value=str(id)`, `label=name`, label-sorted);
  queries accept ID and name; errors quote the unresolved ref.
- `test_configurations.py` + scaffold tests: renamed fields, updated `$data`
  paths, `base_url` required, scaffold IDs + legend.
- Dev E2E: re-enter the demo config via the portal dropdown (chain verified
  live 2026-08-11), deliver via `dev/send_event.py`, confirm `tagId`; rename
  the tag in dev CMORE admin, deliver again, confirm unchanged delivery.

## Rollout

Independent of the portal PRs — before they deploy, free-texted names keep
working; after, dropdowns store IDs. No flag, no coordination. Existing demo
configs stop validating and are re-entered via the dropdown (accepted).

## Out of scope

- Lookup-value IDs (CMORE matches by string; no ID exists).
- Renaming reference actions.
- The known deferred minors from PR #25 (drift test not validating `$data`
  path strings; orphaned classification params).
