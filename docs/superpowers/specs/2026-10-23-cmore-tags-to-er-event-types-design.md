# CMORE tags → EarthRanger event types — design

**Date:** 2026-10-23
**Status:** Approved 2026-10-23 (design conversation in chat; builder-check questions resolved the same day; Decisions 2 and 7 revised after the final code review the same day)
**Origin:** Partner feedback (Aug–Oct 2026 testing): mapping each CMORE tag
and its properties to an EarthRanger event type by hand is tedious. Asked for:
pick the CMORE tags to use, and get an EarthRanger event type for each, with
properties and choices that mirror the tag. Target: usable at the La Palala
workshop (from 2026-10-26).

## Intent

A reserve that reports to CMORE wants EarthRanger event types that look like
its CMORE tags, so rangers report once in EarthRanger and the event lands in
CMORE fully classified. Today that takes a hand-built event type per tag plus
a hand-built `event_type_to_tag` mapping per tag, field and lookup value.

This feature makes the CMORE runner do both: create the EarthRanger event
types from the tag definitions, and deliver events of those types without any
stored mapping, because the generated types carry the tag and field ids in
their names.

## Goals

- An operator with an EarthRanger provider and a CMORE destination already
  connected in Gundi picks CMORE tags in the portal, clicks Run, and gets one
  EarthRanger event type per tag in a category for that share group.
- Events reported with a generated type deliver to CMORE with the tag and all
  mappable fields filled, with no `event_type_to_tag` entry.
- Re-running is safe: it updates existing generated types and choice lists
  to match CMORE and never duplicates, even after a CMORE rename.
- An operator learns *before* the workshop whether the EarthRanger account
  can do this: *Preview only* runs the permissions check and reports what is
  missing.
- One EarthRanger site can serve several CMORE share groups: each CMORE
  integration generates and delivers only its own group's types.

## Non-goals

- A portal wizard on the route or connection. The action lives on the CMORE
  integration's configuration page like the existing executable actions.
- Syncing EarthRanger changes back to CMORE tags, or deleting EarthRanger
  event types when a tag disappears (they are deactivated, never deleted).
- Conditional sections, field layout beyond a single section, icons, or
  CMORE child tags.
- Nested lookup options (`linkedLookups`) as EarthRanger sub-choices: the
  EarthRanger API cannot create sub-choices yet; deferred a few weeks (see
  Decision 5).
- Replacing the explicit `event_type_to_tag` mapping. It stays for
  hand-built event types and wins when present.

## Anti-goals

- Writing to EarthRanger on a draft or ephemeral run. The action refuses to
  run outside a saved integration.
- Touching EarthRanger objects the connector did not create: the writer only
  creates or updates categories, event types and choice lists whose values
  follow the naming convention below.
- Silently skipping tags or fields. Everything skipped is listed in the
  action result and the activity log.

## Constraints

- Both runners deploy from the Gundi integration template; the new code lives
  in this fork's extension points (`app/actions/`, `app/datasource/`) and
  leaves `app/services/` untouched.
- EarthRanger limits: `EventCategory.value` ≤ 67 chars, `display` ≤ 100;
  `EventType.value` ≤ 255; `Choice.field` ≤ 40, `Choice.value`/`display`
  ≤ 100.
- Only the EarthRanger v2 event-type API is used (schema envelope with
  `json` + `ui`, choices by `$ref` to `/api/v2.0/schemas/choices.json?field=…`).
- The EarthRanger writer talks plain `httpx` from `app/datasource/`; no new
  dependency on `erclient`. Reads of the EarthRanger provider's credentials
  go through the runner's own Gundi client (confirmed by Chris: the runner's
  Gundi credentials can read the connected provider's auth).
- No new infra: no cross-runner PubSub publishing, no IAM changes.

## Decisions

1. **The CMORE runner does all of it.** It knows the tags and the mapping
   convention, and it can read the connected EarthRanger provider's auth from
   Gundi. The alternative (the EarthRanger runner creating types from a spec
   handed over by PubSub) needs cross-runner topic permissions and two
   deploys; not for this deadline.
2. **Convention over configuration for generated types.** Values carry ids:
   - category `value = cmore_<group>` where `<group>` is the integration's
     `owner_group_id`; `display` = the action's *Share group label*, default
     `CMORE group <group>`.
   - event type `value = cmore_<group>_<tagid>_<slug>` with `<slug>` the
     tag name lowercased, non-alphanumerics collapsed to `_`, truncated so the
     whole value stays ≤ 255; `display` = the tag name.
   - field key `f<fieldid>_<slug>` from the field name, same slug rule, ≤ 40
     chars including the prefix (so it doubles as the `Choice.field` name).
   - choice `value` = the **slug** of the CMORE lookup value (≤ 100 chars;
     the lookup id is appended when two options slug alike), `display` = the
     CMORE text verbatim (≤ 100), `ordernum` from CMORE's `order`. *Revised
     after the final review (2026-10-23): EarthRanger's `Choice.clean()`
     accepts only `^\w+$` values, so verbatim text relied on the API path
     skipping validation. Delivery normalises both sides (case and
     punctuation dropped), so the slug resolves to the CMORE option.*
3. **Delivery recognises the convention.** In `_push_event`, when no explicit
   `event_type_to_tag` entry matches and `event_type` parses as
   `cmore_<group>_<tagid>_…` **with `<group>` equal to this integration's
   `owner_group_id`**, the handler builds the tag from ids: tag by `<tagid>`,
   each `event_details` key `f<fieldid>_…` to field `<fieldid>`, values through
   the existing per-type coercion (`_resolve_field_value`) with no value
   mappings. A convention match counts as mapped for the PR #40 guardrail. A
   type for another group is unmapped here and skipped, which is how one
   EarthRanger site can feed several share groups.
4. **Explicit mapping wins.** If an `event_type_to_tag` entry names a
   generated type, that entry is used and the convention is not consulted.
5. **Field translation** (CMORE `dataType` → EarthRanger field):
   | CMORE | EarthRanger |
   |---|---|
   | String, IDNumber | string (short text) |
   | Text | textarea |
   | Number, Duration | number |
   | Boolean | boolean |
   | DateTime | datetime |
   | Lookup, FixedLookup | select; multiselect when `allowMultipleValues` |
   | Media, Message | skipped (listed in the result) |
   A field named *Reported by* (case-insensitive) is skipped: the connector
   fills it from the EarthRanger reporter (PR #42). `isMandatory` fields go in
   the schema's `required` list. `hint` becomes the placeholder, truncated to
   32 chars.

   **Nested lookups: top level only, for now** (decided 2026-10-23). A CMORE
   lookup option can carry `linkedLookups` (e.g. *Animal*: "Hoof Animal" →
   Buffalo, Kudu…; *Police Station*: province → town). EarthRanger supports
   sub-choices in its forms but its API cannot create them yet, so this
   design generates the select from the **top-level options only** and
   lists each such field, with its child count, under `shallow_fields` in
   the action result and the activity log. Delivery is unchanged: the
   resolver already matches top-level values. Revisit in a few weeks when
   the EarthRanger API can add sub-choices; the builder and the resolver are
   the two places that change.

6. **Idempotent writes, matched by id, never by slug.** The slug in a value
   or key is cosmetic and frozen at creation; CMORE renames must not create a
   second object. Existing objects are found by **prefix**: the category by
   its exact value (no slug), an event type by `cmore_<group>_<tagid>_` among
   the site's event types (one listing call), a field by `f<fieldid>_` among
   the existing schema's properties. When found, the existing value / key is
   kept and only `display`, the field title, the schema and `is_active: true`
   are patched; when absent, the object is created with a fresh slug.
   Choices: per field (keyed by the kept field key), diff desired against
   existing by `value` (create / update display+ordernum / deactivate extras),
   the earthranger-cli planner's rule. Tags deselected on a later run are
   left alone; nothing is deleted.
7. **Finding the EarthRanger site, and checking it can be written to.** The
   handler lists Gundi connections filtered by `destination_id` = this
   integration and `provider_type` = `earth_ranger` (cdip's `ConnectionFilter`
   supports both), then reads the provider's `auth` configuration (token, or
   username + password exchanged at `<site>/oauth2/token` with client id
   `das_web_client`) and `base_url` (site root = scheme + host) through
   `get_integration_details`, whose `configurations[].data` is unredacted.
   With exactly one provider it proceeds. With none, or more than one, the
   action fails with a configuration error naming the count; a later
   iteration can add a provider picker.

   **No credential override.** The connected provider's EarthRanger user is
   the one that writes (decided 2026-10-23). It is usually a data-ingest
   account, so before anything is written the handler runs a **permissions
   preflight** against what EarthRanger actually enforces (verified in das
   during the final review, 2026-10-23):
   - a superuser (`user/me` → `is_superuser`) passes everything;
   - while the `cmore_<group>` category does not exist: `eventcategory: add`
     (`activity.add_eventcategory`); creating it hands the same user the
     category's own permission set;
   - once it exists: the per-category permissions EarthRanger enforces on
     event types in it, reported by `user/me` under the category value as
     `cmore_<group>: change, view`;
   - choices: `choices.add_choice` / `change_choice`, which `user/me` never
     reports (only the `activity` app is listed), so they are **probed**
     with an empty `POST /api/v1.0/choices/` and `PATCH /api/v1.0/choices/<random>/`
     — DRF checks model permissions before deserialising, so 403 means
     missing and 400/404 means held.
   Anything missing fails the run with an `IntegrationConfigurationError`
   whose message lists the missing permissions and the EarthRanger username.
   A 403 on a later write is translated to the same error class, after an
   ERROR activity entry carrying the partial result (what was completed and
   which type failed). The preflight also runs on `dry_run` and its outcome
   is part of the result, so *Preview only* doubles as the pre-workshop
   readiness check.
8. **Where it runs.** A `GenericActionConfiguration` + `ExecutableActionMixin`
   action, `create_er_event_types`, titled *Create EarthRanger event types*,
   so the portal shows a Run button with its form. It is **not** a reference
   action and is not registered as one.

## 1. Action and config (`app/actions/configurations.py`)

`CreateErEventTypesConfig(GenericActionConfiguration, ExecutableActionMixin)`:

- `tags: List[str]` — title *CMORE tags*; each item carries a
  `gundi:reference` to `list_tag_names` (target self, no params) so the
  portal offers a dropdown per row, like the mapping lists. Free text (a tag
  id or exact name) also works. Empty list → configuration error.
- `share_group_label: Optional[str]` — title *Share group label*; the
  category's display name, default `CMORE group <owner_group_id>`.
- `dry_run: bool = False` — title *Preview only*; compute and report the plan
  without writing to EarthRanger.

UI order: `tags`, `share_group_label`, `dry_run`. The drift-guard test for
reference annotations must keep passing (the new annotation names an existing
self-target action with no params).

## 2. Schema builder (`app/datasource/er_event_types.py`, pure)

Ported from earthranger-cli's `schema_gen` and `choices` modules, trimmed to
what this feature needs. Pure functions, unit-tested against the captured
catalog in `8443.get-tags.json`:

- `slugify(name) -> str`, `event_type_value(group, tag) -> str`,
  `field_key(field) -> str`, `category_value(group) -> str`.
- `parse_generated_event_type(value, group) -> Optional[int]` (the tag id,
  or None when the value is not this group's convention) and
  `parse_generated_field_key(key) -> Optional[int]`. These are the inverse
  of the builders and are what delivery uses.
- `build_event_type_spec(tag: TagInfo-like dict, group, label) -> EventTypeSpec`
  (Pydantic): value, display, category, one section, fields with
  type/label/hint/required/options, plus the list of skipped fields with a
  reason each.
- `build_event_type_payload(spec) -> dict`: the v2 POST/PATCH body with the
  `json` + `ui` schema envelope exactly as earthranger-cli emits it (every
  json property carries `deprecated: false`; choice fields use the `$ref`
  template; `unevaluatedProperties: false`).
- `build_choice_records(spec) -> dict[field_name, list[dict]]` and
  `plan_field_choices(existing, desired) -> list[ChoiceOp]`.

`TagInfo` today does not keep the lookup *options* beyond `lookups` (raw
dicts) and drops `isMandatory`, `hint`, `order`; the builder reads the raw
`/v2/tags/getfull` entries (the client already returns them) rather than
widening `TagInfo`.

## 3. EarthRanger writer (`app/datasource/er_admin_client.py`)

An `httpx.AsyncClient` wrapper, plain args only (site root, token **or**
username/password), `@backoff` on transient failures, raising
`httpx.HTTPStatusError` on 4xx/5xx so the template classifies auth (401/403)
and connectivity failures as it does for CMORE. Methods:

- `get_me()` → the `user/me` document (username + `permissions`)
- `get_event_categories()`, `post_event_category(payload)`
- `get_event_types(include_schema=True)` (v2 listing; the handler filters by
  the `cmore_<group>_<tagid>_` prefix), `post_event_type(payload)`,
  `patch_event_type(value, payload)`
- `get_choices(field)`, `post_choice(payload)`, `patch_choice(id, payload)`

Paths: `/api/v1.0/activity/events/categories`,
`/api/v2.0/activity/eventtypes[/<value>]`, `/api/v1.0/choices/`. The
password grant uses `<site>/oauth2/token`, `grant_type=password`,
`client_id=das_web_client`. Nothing in this module imports Gundi or template
code.

## 4. Handler (`app/actions/handlers.py`)

`action_create_er_event_types(integration, action_config)`:

1. Refuse on an ephemeral run (`IntegrationConfigurationError`, fixed text).
2. Open the CMORE client from the auth config; fetch the full tag catalog;
   resolve each configured tag by id or name through the existing index
   rules; unknown refs fail the whole run before any write.
3. Resolve the EarthRanger provider and run the permissions preflight
   (Decision 7). A missing permission stops here, on dry runs too.
4. Build specs; collect skipped fields. List the site's existing generated
   event types for this group once and reconcile by id prefix (Decision 6),
   reusing existing values and field keys.
5. Unless `dry_run`: ensure category; for each spec, create or patch the
   event type, then plan and apply choice ops per lookup field.
6. Return `{earthranger: {site, username, permissions_ok}, category, event_types: [{value, display, action: created|updated|unchanged, fields: n, skipped: [...], shallow_fields: [...]}], choices: {created, updated, deactivated}, dry_run}`
   and log one activity entry at INFO summarising counts, one at WARNING per
   skipped field group.

Per-event-type failures stop the run with the partial result in the error
details, so a re-run can finish the rest (every step is idempotent).

## 5. Delivery by convention (`app/actions/handlers.py`)

In `_push_event`, after the explicit lookup and before the guardrail:

```
if mapping is None:
    tag_id = parse_generated_event_type(event.event_type, auth.owner_group_id)
    if tag_id is not None:
        mapping = CmoreTagMapping(event_type=event.event_type, tag=str(tag_id),
                                  field_mappings=[])   # fields come from the convention
        convention = True
```

`_build_event_tag` gains a `convention: bool` path: instead of walking
`field_mappings`, it walks `event.event_details`, keeps keys whose
`parse_generated_field_key` resolves to a field on the tag, and coerces each
value with `_resolve_field_value` (lists for multi-value fields). The reporter
field logic from PR #42 applies unchanged. Everything else in `_push_event`
(description, comment, state) is untouched.

## 6. Docs

- `docs/configuration.md`: a *Create EarthRanger event types* section (what
  it makes, the naming convention, re-run behaviour, share groups) and a note
  in the mapping section that generated types need no entry.
- `docs/tutorial-sync-event-type.md`: a short box in Part 2 pointing at the
  action as the fast path, leaving the manual walkthrough as the explanation.
- `docs/troubleshooting.md`: "The action fails to find EarthRanger" (0 or 2+
  providers), "The EarthRanger user lacks permissions" (what the preflight
  names and where to grant it), "A field is missing from the generated type"
  (skipped types).

## 7. Testing and verification

- Builder: round-trip every tag in `8443.get-tags.json` through spec → payload
  and assert the convention parsers invert the values; one test per field
  type; truncation at each limit; *Reported by* and Media/Message skipped.
- Choice planner: create / update / deactivate / unchanged, order-settled
  rule.
- Handler: dry run writes nothing but still reports the preflight; happy
  path with a mocked ER client asserts the POST bodies; second run patches
  instead of posting; a renamed tag or field on re-run patches the existing
  object and keeps its value / key; a missing permission fails before any
  write and names it; 0 and 2 providers fail with a configuration error;
  ephemeral refusal.
- Nested lookups: the builder emits top-level options only and reports the
  field under `shallow_fields` with its child count.
- Delivery: a generated type delivers with tag and fields and no mapping;
  the same type for another group is skipped by the guardrail; an explicit
  mapping for a generated type wins.
- Manual, in dev before the workshop: run the action against dev CMORE and
  dev EarthRanger for two tags, report an event of each generated type in
  EarthRanger, and confirm both arrive in CMORE classified with field values.

## Left to the builder

- Exact slug truncation strategy when two tags collapse to the same slug
  (the id prefix already keeps the values distinct; only readability is at
  stake).
- Whether `EventTypeSpec` and `ChoiceOp` are Pydantic models or plain
  classes, as long as the repo's Pydantic-over-dataclasses convention holds
  for anything that crosses a function boundary.
- Backoff parameters for the EarthRanger writer; match the CMORE client's.
- The exact activity-log titles.
