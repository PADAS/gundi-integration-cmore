# RFC: Reference-data actions — live options for integration config forms

> **Superseded detail (2026-08-14):** the CMORE config fields and reference-action params shown in the examples below were renamed `tag_name`→`tag`, `field_name`→`field`, `cmore_field_name`→`cmore_field`, and dropdown option values now carry tag/field IDs with names as labels — see [configuration.md](configuration.md#how-tag-and-field-refs-are-resolved). Payload shapes are otherwise unchanged.

**Status:** Proposal to the Gundi platform team
**Author:** Chris Doehring
**Date:** 2026-08-03
**Runner-side implementation:** shipped & verified — [PADAS/gundi-integration-cmore#25](https://github.com/PADAS/gundi-integration-cmore/pull/25), running in dev
**Full design doc:** `gundi-integration-cmore/docs/superpowers/specs/2026-07-31-reference-data-config-ui-design.md`

## Problem

An integration's config form is rendered from a static JSON schema registered
per integration *type*. But for many integrations the *valid values* of config
fields are instance-specific and live behind the integration's credentials:

- CMORE: tag names, per-tag field names, per-field allowed values, the
  classification tree — all defined per CMORE instance by its operators.
- The same shape recurs across the fleet: SMART category trees, destination
  site/group pickers, EarthRanger event types.

Today an operator configuring the CMORE `deliver` action types every value
free-hand into a four-level nested form, or a developer runs an interactive
CLI (`scaffold-mapping`) that fetches both vocabularies and builds the JSON.
Neither belongs in the long-term operator workflow.

## Proposal (one paragraph)

Integrations expose **reference actions** — a new action flavor whose config
model *is* the query and whose result is a standardized options list. Config
form fields declare their options source via a **`gundi:reference`**
annotation in the `ui_schema` the runner already registers. The portal renders
such fields as a combobox that fetches options through the **existing**
synchronous action-execution path (`POST /v1/actions/execute` on the runner,
proxied by the Gundi API), passing query params as `config_overrides`. Any
config field whose valid values live behind the integration's credentials
becomes a dropdown for the cost of one small action handler — with zero
portal work per integration.

## What already exists (Phase 0, shipped)

`gundi-integration-cmore` (merged, deployed to dev, inert) implements the
runner side end-to-end. These are **real dev responses** captured 2026-08-03
against the dev CMORE instance via `/v1/actions/execute`:

```jsonc
// {"action_id": "list_tag_names"}  (no params)
{
  "options": [
    {"value": "Elephant Carcass", "label": null, "description": null, "group": "Wildlife"},
    {"value": "Evidence of Poacher", "label": null, "description": null, "group": "Wildlife"}
    // ... 17 total
  ],
  "cache_ttl_seconds": 300,
  "truncated": false
}

// {"action_id": "list_tag_fields", "config_overrides": {"tag_name": "Evidence of Poacher"}}
// → options: "Reported By" (String), "Evidence Type" (Lookup), "CAS Number" (String),
//   "Evidence Bag No" (String), "Number of Snares Found" (Number), "SAP 13" (String)

// {"action_id": "list_field_options", "config_overrides":
//   {"tag_name": "Evidence of Poacher", "field_name": "Evidence Type"}}
// → 22 options, in the CMORE instance's configured display order

// {"action_id": "list_classification_values"} → AIR, CYBER, LAND, ...
// {"config_overrides": {"battleDimension": "LAND"}} → ANIMAL, EQUIPMENT, UNIT, UNKNOWN
```

Missing required params produce a normal Pydantic validation error (422
semantics), and reference-action failures deliberately do **not** attach
integration configurations to error events (verified — no secrets in error
payloads).

### Runner-side contract (to upstream to `gundi-integration-action-runner`)

```python
class ReferenceActionConfiguration(ActionConfiguration):
    """Marker base: the config model IS the query. Stateless — reads the
    integration's auth config, stores no config of its own."""

class ReferenceOption(BaseModel):
    value: str
    label: Optional[str] = None        # portal defaults label to value
    description: Optional[str] = None  # tooltip / help text
    group: Optional[str] = None        # optional grouping for long lists

class ReferenceDataResponse(BaseModel):
    options: List[ReferenceOption]
    cache_ttl_seconds: int = 300       # portal-side cache hint
    truncated: bool = False            # true if the list was capped
```

A reference action is an ordinary `action_`-prefixed handler:

```python
class ListTagFieldsQuery(ReferenceActionConfiguration):
    tag_name: str

async def action_list_tag_fields(integration, action_config: ListTagFieldsQuery):
    ...  # call the source system with the integration's auth
    return ReferenceDataResponse(options=[...]).dict()
```

Registration emits it with `"type": "reference"` (new `ActionTypeEnum` value)
and `is_periodic_action: false`. The platform now accepts the type, so the
template (and this runner, since the Sept 2026 upstream sync) always registers
reference actions; the interim default-off `REGISTER_REFERENCE_ACTIONS` flag
has been removed.

### The `gundi:reference` ui_schema annotation (already registered by runners)

```jsonc
"cmore_field_name": {
  "gundi:reference": {
    "action": "list_tag_fields",         // reference action id on the target runner
    "target": "self",                    // or "provider" — see Phase 2
    "params": { "tag_name": { "$data": "../../tag_name" } },
    "allow_free_text": true              // combobox (default) vs strict select
  }
}
```

- **`params`** values are literals or `{"$data": <relative-path>}` resolved
  against current form state — this is what makes cascading dropdowns work
  (tag → its fields → a field's values).
- **`$data` semantics:** the path starts at the object containing the
  annotated field; an array and its items are distinct levels; each `../`
  climbs one level; a bare name is a sibling field. Worked example for the
  CMORE `DeliverConfig` (field inside `event_type_to_tag[].field_mappings[]`):
  the tag name two ancestors up is `"../../tag_name"`; from
  `value_mappings[].to_value` it is `"../../../../tag_name"`.
- **Deliberate forward-compatibility:** the annotation never sets
  `ui:widget`. A portal without reference support ignores the unknown key and
  renders today's plain text input — which is why Phase 0 could ship to
  production ahead of this RFC. A supporting portal detects the key and swaps
  in the reference-select widget.

## The asks (three bounded changes)

### 1. gundi-core / API: accept the `"reference"` action type

Add `reference` to the action-type enum and accept it at integration-type
registration. Reference actions have no user-editable configuration, so the
portal should **not** render them in the per-integration configuration list.

### 2. API: authorize execute-proxy calls to reference actions for config editors

The portal already has a synchronous path to a runner's
`POST /v1/actions/execute` (used by the Authenticate test button). Extend the
authorization rule: **any user who can edit an integration's configuration may
invoke actions of type `reference` on it.**

> ⚠️ Note: today's proxy authorization may key on the `is_executable` schema
> flag. Reference actions do **not** set `is_executable` — the gate for them
> must key on the action *type*. Calling this out explicitly so it isn't
> discovered late.

Request/response contract is exactly the existing one:
`{integration_id, action_id, config_overrides: {…query params…}}` →
`ReferenceDataResponse` JSON (synchronous).

### 3. Portal: the reference-select widget

One new widget in the schema-driven config form, activated by the presence of
`gundi:reference`:

- **Fetch lazily** on field open/focus (never on form load — a mapping form
  can have dozens of annotated fields), debounced, cached client-side keyed on
  `(integration_id, action_id, params)` honoring `cache_ttl_seconds`.
- **Resolve `$data` params** from current form state per the semantics above.
  While a referenced parent is empty, don't fetch (field stays free text).
  When a parent changes, re-fetch; flag (never auto-clear) a selection that's
  no longer among the options.
- **Degrade, never block** (the invariant): fetch failure/timeout → plain
  text input with a warning and a retry affordance. Cloud Run cold starts make
  the first fetch potentially slow — generous timeout (~15s) behind a spinner.
- **`allow_free_text: true`** (the common case) renders a combobox accepting
  arbitrary input; `false` renders a strict select that still degrades to free
  text on fetch failure.
- **Stale saved values** (tag renamed upstream): show with a warning badge.
  Saved config is never mutated by fetch results.

## Phase 2 (`target: "provider"`) — for awareness, not in this ask

Half of CMORE's mapping vocabulary is source-side (ER event types,
`event_details` keys). The annotation's `target: "provider"` directs the
portal to fetch from the provider integration(s) of the connection instead:
resolve the connections where this integration is a destination, keep
providers whose type registered the named reference action, union and dedupe
the options. Requires the ER runner to implement three reference actions
(`list_event_types`, `list_event_type_fields`, `list_event_field_values`) —
a fast-follow in `gundi-integration-earthranger` once Phase 1 lands.

## Error semantics & operational notes

- Missing/invalid query params → 422 (Pydantic validation on the runner).
  Portal shows "couldn't load options."
- Unknown reference values (e.g. a tag that no longer exists) currently
  surface as 500 from the runner; we recommend the template contract map
  these to 422 as well (tracked as a follow-up in the runner template
  upstreaming).
- Reference-action failures **omit integration configurations from error
  events** (implemented) — worth adopting template-wide; a failed dropdown
  fetch is routine and must not spray secrets into activity logs. Related:
  consider throttling activity-log events for reference-action failures, since
  interactive fetch frequency ≫ scheduled-action frequency.
- Load: bounded by portal debounce + `cache_ttl_seconds`; reference fetches
  are per-form-interaction, not per-event.

## Rollout

1. Platform ships asks 1-2 (API) — backward compatible; no integration changes.
2. CMORE runner registers reference actions (always on since the Sept 2026
   template sync) → validates ask 1-2 end-to-end.
3. Portal ships ask 3 → CMORE mapping form gets working dropdowns with zero
   further integration-side changes (annotations are already live).
4. Template upstreaming (mixin + envelope + docs) → any integration can adopt
   with one small handler per vocabulary.
5. Phase 2: ER provider-side actions; CMORE's source-side fields flip to
   `target: "provider"`.

## Alternatives considered (and why not)

- **Baking per-integration enums into the JSON schema** (a "refresh" action
  pushes a schema variant): requires per-integration schema storage in Gundi,
  explodes combinatorially for cascades (tags × fields × options as
  `oneOf`/`dependencies`), and goes stale between refreshes.
- **A dedicated `/reference` HTTP endpoint on runners**: a parallel
  invocation path with its own auth story, no activity logging, no
  registration-time discoverability — the fragmentation the action contract
  exists to prevent.
- **A bespoke mapping-builder portal page**: best-in-class UX for this one
  problem, but a new surface to own; the schema-driven widget benefits every
  integration and a builder can layer on the same contract later.
