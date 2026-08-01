# Reference Data for Integration Config Forms — Design

**Date:** 2026-07-31
**Status:** Approved design (brainstorm output); implementation plan to follow
**Deliverable:** Platform RFC + working CMORE prototype in this repo

## Problem

Configuring the CMORE `deliver` action means filling a four-level nested form
(event_type → tag_name → field_mappings → value_mappings) where every value is
free text — but the *correct* values live in two external systems:

- **Destination side:** CMORE tag names, field names per tag, allowed option
  values per Lookup/FixedLookup field, and the classification tree are all
  instance-specific and only discoverable via the CMORE API with the
  integration's credentials.
- **Source side:** EarthRanger event types, `event_details` keys, and their
  value vocabularies live in the provider on the other side of the connection.

Today a savvy user runs the `scaffold-mapping` CLI, which fetches both sides
and interactively builds the mapping JSON. That works but doesn't belong in
the long-term operator workflow — this configuration should happen in the
Gundi portal UI.

The underlying platform limitation: an action's configuration form is rendered
from a static JSON schema registered per integration *type*. There is no way
for the form to populate lists or options from an *individual integration*.

## Decisions made during brainstorming

1. **Deliverable:** a platform RFC (this document is its basis) plus a working
   prototype in this repo, so the contract is validated against a real
   integration and is ready the day portal support lands.
2. **Cascading:** the contract supports parameterized queries from day one
   (options for one field can depend on the current value of another).
3. **Source-side data:** the contract lets a field source its options from the
   provider integration(s) on the other side of the connection, not just from
   the integration being configured.
4. **Portal UX:** enhance the existing schema-driven (rjsf-based) config form.
   No bespoke mapping-builder surface; that could be layered on the same
   contract later.
5. **Mechanism:** reference data is served by a new flavor of *action*
   ("reference actions") on the integration's runner, invoked through the
   existing synchronous `/v1/actions/execute` path. Alternatives considered
   and rejected:
   - *Schema snapshot baking* (a refresh action bakes per-integration enums +
     `oneOf` cascades into the JSON schema): requires per-integration schema
     storage in Gundi (heavier platform lift than a widget), CMORE's
     tags × fields × options cascade explodes the schema, and data goes stale.
   - *Dedicated `/reference` HTTP endpoint on runners*: creates a parallel
     invocation path with its own auth story, no activity logging, and no
     registration-time discoverability — exactly the fragmentation the action
     contract exists to prevent.

## Section 1 — Action Runner contract: reference actions

A reference action is a normal action handler with a new marker base class,
so it rides all existing machinery: `action_` prefix discovery, registration,
`POST /v1/actions/execute`, and activity logging.

```python
# template's app/actions/core.py (prototyped in this repo first)
class ReferenceActionConfiguration(ActionConfiguration):
    """Marker base: the config model IS the query. Reference actions are
    stateless — they read the integration's auth config but store no config
    of their own; the portal supplies query params via config_overrides."""
```

Standardized response envelope:

```python
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

Example implementation:

```python
# an integration's app/actions/handlers.py
class ListTagFieldsQuery(ReferenceActionConfiguration):
    tag_name: str

async def action_list_tag_fields(integration, action_config: ListTagFieldsQuery):
    # call the source system with the integration's auth config
    return ReferenceDataResponse(
        options=[ReferenceOption(value=f.name) for f in fields]
    ).dict()
```

**Registration.** `self_registration.py` detects `ReferenceActionConfiguration`
subclasses and registers them with `"type": "reference"` (new `ActionTypeEnum`
value in gundi-core and the Gundi API). The portal hides reference actions
from the per-integration configuration UI — they have no user-editable
config — but allows them through the same execute proxy used for
`is_executable` actions.

**Invocation.** The existing path, unchanged:
`POST /v1/actions/execute` with
`{integration_id, action_id: "list_tag_fields", config_overrides: {"tag_name": "Poacher Sighting"}}`
returns the `ReferenceDataResponse` JSON synchronously. Because reference
actions have no stored config, `config_overrides` fully populates the query
model; a missing or invalid param is a Pydantic validation error (422), which
the portal surfaces as "couldn't load options."

## Section 2 — The `gundi:reference` ui_schema annotation

A config field opts into dynamic options by carrying a `gundi:reference` key
in its `ui_schema` entry:

```json
"cmore_field_name": {
  "gundi:reference": {
    "action": "list_tag_fields",
    "target": "self",
    "params": { "tag_name": { "$data": "../../tag_name" } },
    "allow_free_text": true
  }
}
```

- **`action`** — the reference action id on the target integration's runner.
- **`target`** — `"self"` (the integration being configured) or `"provider"`
  (the integration(s) on the other side of the connection). With multiple
  connections, the portal unions provider options and dedupes by value.
- **`params`** — the query sent as `config_overrides`. Values are literals or
  `{"$data": <relative-path>}` resolved against current form state (see
  semantics below). While a `$data` dependency is empty, the portal does not
  fetch and the field stays free text. When a parent value changes, the portal
  re-fetches; an existing selection that is no longer valid is flagged with a
  warning — never silently cleared.
- **`allow_free_text`** — combobox (suggestions + arbitrary input; the
  default) vs strict select. All CMORE fields use `true`; this doubles as the
  failure mode: fetch fails → plain text input with a warning. The form never
  blocks on reference data.

**Forward compatibility (deliberate):** the annotation does *not* set
`ui:widget`. A portal that predates this feature ignores the unknown
`gundi:reference` key and renders today's plain text field, so integrations
can ship annotations immediately with zero coordination risk. A supporting
portal detects the key and swaps in its reference-select widget.

### `$data` path semantics

A `$data` path is resolved against the form-data tree, starting at the object
that contains the annotated field. An array and its items are *distinct*
levels. Each `../` climbs one level; a bare name refers to a sibling field in
the same containing object.

Worked example — the `DeliverConfig` form-data tree:

```
(root DeliverConfig)
└── event_type_to_tag                 (array)
    └── [i] CmoreTagMapping           (object: event_type, tag_name, field_mappings)
        └── field_mappings            (array)
            └── [j] CmoreFieldMapping (object: event_details_key, cmore_field_name, value_mappings)
                └── value_mappings    (array)
                    └── [k] CmoreValueMapping (object: from_value, to_value)
```

- From `cmore_field_name` (containing object: the `CmoreFieldMapping` item):
  `../` = the `field_mappings` array, `../../` = the `CmoreTagMapping` item →
  the tag is `"../../tag_name"`.
- From `to_value` (containing object: the `CmoreValueMapping` item):
  the field is `"../../cmore_field_name"`, the tag is `"../../../../tag_name"`.
- From classification `force` (sibling dependency): `{"$data": "battleDimension"}`.

## Section 3 — Portal behavior

**Auth & transport.** The portal never talks to a runner directly — it calls
the Gundi API, which reaches the runner at its registered `service_url` (the
same path the Authenticate test button uses for `is_executable` actions). New
authorization rule: reference-type actions are invocable by any user who can
edit that integration's configuration.

**Fetch lifecycle.** Options load lazily on field open/focus, not on form
load (a form with many mapping rows must not fire dozens of fetches at once).
Fetches are debounced and cached client-side keyed on
`(integration_id, action_id, params)`, honoring `cache_ttl_seconds`. Cloud Run
cold starts can make the first fetch slow: the widget shows a spinner with a
generous timeout (~15s), then degrades to free text with a retry affordance.

**Provider resolution.** For `target: "provider"`, the Gundi API resolves the
connections in which the integration is a destination, collects the provider
integrations, and checks whether each provider's *type* registered the named
reference action. Providers lacking it are skipped; if none has it, the field
stays free text. Multi-provider results are unioned and deduped by value.

**Stale selections.** If a stored config value is not among fetched options
(tag renamed in CMORE, event type deleted in ER), the portal shows the value
with a warning badge. Saved config is never auto-mutated by fetch results.

## Section 4 — CMORE prototype scope (this repo)

Four reference actions, all thin wrappers over the existing `CmoreClient`
(the same calls `scaffold-mapping` makes today):

| Action | Query params | Returns |
|---|---|---|
| `action_list_tag_names` | — | tag names visible to the token's share group |
| `action_list_tag_fields` | `tag_name` | field names within that tag |
| `action_list_field_options` | `tag_name`, `field_name` | allowed values for Lookup/FixedLookup fields (empty for free-text fields) |
| `action_list_classification_values` | `battleDimension?`, `force?`, `type?` | next level of the classification tree given choices so far |

`Affiliation` is already a static enum — no action needed.

`DeliverConfig.ui_schema()` gains `gundi:reference` annotations:

- `event_type_to_tag[].tag_name` → `list_tag_names` (`target: self`)
- `…field_mappings[].cmore_field_name` → `list_tag_fields`
  with `tag_name: {"$data": "../../tag_name"}`
- `…value_mappings[].to_value` → `list_field_options` with
  `tag_name: {"$data": "../../../../tag_name"}`,
  `field_name: {"$data": "../../cmore_field_name"}`
- `subject_type_to_classification[]` fields cascade through
  `list_classification_values` via sibling `$data` refs
  (`force` depends on `battleDimension`, `type` on both, `role` on all three)

Because annotations are inert to today's portal, this ships immediately with
no behavior change. The mixin and envelope are implemented in this repo's
`app/actions/core.py` first and proposed upstream to
`gundi-integration-action-runner`.

**Source side (ER)** is specified in the RFC but implemented as a fast-follow
in `gundi-integration-earthranger`: `list_event_types`,
`list_event_type_fields(event_type)`,
`list_event_field_values(event_type, field_key)`. When those land,
`event_type` and `event_details_key` here flip to `target: "provider"`
dropdowns.

**CLI:** stays as-is — it remains the headless/scripted path.

**Note:** the existing Dict→List config-shape workaround (GUNDI-5371, see
`docs/portal-rendering-workaround.md`) is orthogonal to this design and
remains in place until that portal bug is fixed.

## Section 5 — Rollout phases & generalization

- **Phase 0 — this repo, now (no platform dependency).** Mixin + envelope in
  `app/actions/core.py`, four reference actions, `ui_schema` annotations on
  `DeliverConfig`, tests. Ships inert.
- **Phase 1 — platform (the RFC's ask).** Three bounded changes:
  (1) gundi-core/API accept `"type": "reference"` at registration;
  (2) API authorizes execute-proxy calls to reference actions for config
  editors; (3) portal implements the reference-select combobox widget with
  `$data` resolution and provider-target lookup. Template upstreams the
  mixin + envelope.
- **Phase 2 — ecosystem.** ER runner adds the provider-side actions; CMORE's
  source-side fields flip to `target: "provider"`. Adoption elsewhere is
  incremental: SMART category trees, destination site/group pickers,
  EarthRanger-as-destination event-type choices — any config field whose
  valid values live behind the integration's credentials becomes a dropdown
  for the cost of one small action handler, with zero portal work per
  integration.

## Section 6 — Error handling & testing

**Invariants:**

- The form never blocks on reference data (free-text degradation everywhere).
- Saved config is never auto-mutated by fetch results (warning badges only).
- Every failure mode — timeout, 4xx/422, unknown action, no provider — lands
  in the same visible-but-editable state.

**Tests in this repo:**

- Unit tests per reference action over mocked `CmoreClient` fixtures:
  options extraction, empty/unknown tag, options request for a non-Lookup
  field, upstream error propagation.
- Registration test: reference actions emit `"type": "reference"` and are
  excluded from user-facing config actions.
- Drift test: every `gundi:reference` annotation in `DeliverConfig.ui_schema()`
  names a real registered action whose query model's fields match the
  declared `params` keys. This keeps annotations honest as config evolves.

Portal/widget testing is the platform team's scope; the RFC specifies the
degradation behaviors it should cover.
