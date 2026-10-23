---
title: Configuration
---

# Configuring the CMORE integration

All settings are configured on the **CMORE destination integration** in the
Gundi portal. The integration exposes two actions: **Authenticate** and
**Deliver**.

[← Overview](index.md)

---

## Authenticate

Credentials and the target CMORE instance. This action is also **executable**
from the portal — run it to verify the token works before any data flows
(catches a bad token or zero tag visibility up front).

| Field | Required | Description |
|---|---|---|
| **API Token** | yes | CMORE API token (raw value, *without* the `Token ` prefix — the client adds it). Stored as a secret. |
| **API Base URL** | yes | The **full** CMORE API base for your instance, including the path (e.g. `https://cmore.csir.co.za/za/WebAPI/api` on DFFE) — see [why the full URL](#why-the-base-url-includes-a-path). |
| **Owner Group ID** | yes | The CMORE **ShareGroupId** linked to this token. All events are posted to this group; it controls which CMORE users/teams can see the data. |

### Why the base URL includes a path

The domain alone isn't enough — CMORE deployments mount the application under
a **per-instance virtual directory**, so the URL structure is
`https://<host>/<instance>/WebAPI/api` and the `<instance>` segment varies:

| Deployment | Base URL |
|---|---|
| DFFE production | `https://cmore.csir.co.za/za/WebAPI/api` |
| CHPC test server | `https://cmorewc1.chpc.ac.za/za/WebAPI/api` |
| CSIR dev | `https://dmore.csir.co.za/dev/WebAPI/api` |

The CMORE API reference documents a "Base URL" only for the test server it was
written against, not a universal rule; its auth examples set session cookies
scoped to `path=/dev/`, and the Portal User Guide shows the portal mounted
under the same instance segment (`…/za/portal/login.html`). The trailing
`WebAPI/api` part is consistent across every documented deployment (casing
varies, which IIS treats as equivalent), but since nothing guarantees the
instance segment — or even that suffix — the integration asks for the full
URL rather than deriving it from a domain.

> The token's ShareGroup must have **tag visibility** for any tags you map (a
> CMORE admin subscribes the group to a tag domain). If the group sees zero
> tags, events still post but the structured tag is silently dropped.

---

## Deliver

How routed data is transformed for CMORE. One handler dispatches internally on
the payload type (Observation / Event / EventUpdate), so all of the following
live under the single Deliver config.

### Event type → CMORE tag (`event_type_to_tag`)

List mapping each Gundi `event_type` to a CMORE tag and its fields. **Only
event types in this list are sent to CMORE** — plus event types the connector
itself generated with [Create EarthRanger event types](#create-earthranger-event-types),
which map by naming convention and need no entry here. An event whose type
isn't listed (or any event, while the list is empty) is skipped and recorded in the
connection's activity log as a warning — *No CMORE tag mapping for event type
'…' — skipping event* — with the event type, EarthRanger serial number and the
list of configured types. A tag entry with no field mappings still counts as
configured: the event posts with the tag attached and no field values.

> **Why skip rather than post untagged:** a connection with no mappings yet
> used to forward every routed event untagged, which flooded CMORE with
> historical and unexpected events that then had to be cleaned up by hand.
> Untagged events are also invisible to everything that relies on
> classification — tag-based filtering and lookups, analytics, reporting,
> dashboards and rule-based workflows — so there is little value in them.

Each entry (**CmoreTagMapping**):

- **Gundi event_type** — the event_type string on incoming events (e.g. `rhino_carcass`).
- **CMORE Tag** — the tag **ID** (preferred — immutable, e.g. `8443`) or the exact tag name. Resolved at runtime from CMORE's `/v2/tags/getfull`; must be visible to this integration's ShareGroup. See [ref resolution](#how-tag-and-field-refs-are-resolved).
- **Field Mappings** — list of **CmoreFieldMapping**:
  - **Gundi event_details key** → **CMORE Field** (field ID preferred, or exact field name — within the chosen tag).
  - **Value Mappings** (optional) — list of `source value → CMORE value` pairs.
- **Reported by field** (optional) — the CMORE field, within the chosen tag,
  that receives the name of the EarthRanger user or subject who reported the
  event. See [Who reported the event](#who-reported-the-event).

#### Who reported the event

CMORE records every synced event as created by the integration account; the
API offers no way to post as someone else. The connector therefore carries the
original reporter — the EarthRanger user (first and last name, or username) or
subject (its name) on the event's **Reported by** — into the event itself:

1. **Tag field.** If **Reported by field** is set and resolves, the name is
   written there. If it is blank, the connector uses the first field on the
   tag whose name is *Reported by* (any case) — most CMORE tag domains define
   one, so this usually needs no configuration.
2. **Source comment.** When the tag has no such field, the configured one
   does not resolve, or a **Field Mapping** already fills that field from
   `event_details` (the explicit mapping wins; both cases are logged), the
   name is appended to the source comment instead:
   `EarthRanger event #267 — Reported by: Jane Doe — Source: <url>`.

The reporter arrives via `provider_metadata.reported_by`, stamped by the
EarthRanger runner (gundi-integration-earthranger #54 or later). An older
runner sends nothing, and the event posts exactly as before.

#### How tag and field refs are resolved

The **CMORE Tag** and **CMORE Field** values accept either the immutable
numeric **ID** or the exact **name**:

1. An all-digit value that matches an existing ID resolves **by ID**. This is
   the preferred form — it keeps working when the tag or field is renamed in
   CMORE. The portal's dropdowns store IDs automatically; the
   [`scaffold-mapping`](#scaffolding-mappings-cli-alternative) tool emits IDs
   and prints an id ↔ name legend.
2. Anything else — or an all-digit value matching no ID — resolves by **exact
   name**. Name-based refs break (events post unclassified, field values are
   dropped) if the tag/field is renamed in CMORE.
3. Edge case: a tag literally *named* "8443" while a different tag *has* ID
   8443 resolves to the ID. Deterministic, and avoidable by using IDs.

#### How field values are resolved

Per the CMORE field's data type:

- **Lookup / FixedLookup** — the source value is resolved to a valid CMORE option: first via the field's **value mappings**, then a **case/punctuation-insensitive** match against the tag's options (so ER `male` → CMORE `Male` needs no mapping, but ER `b_3_months1_year` → CMORE `Calf` does). A value that still doesn't match a valid option is **dropped and logged** — never sent as garbage. CMORE matches lookups by their **value string** (not id).
- **Number / Boolean** — validated/coerced (`yes/no/true/false/1/0` → `true`/`false`).
- **String / Text** — sent as-is.

> Authoring these mappings by hand is tedious for tag-heavy event types. Use
> the [scaffold tool](#scaffolding-mappings-cli-alternative) to generate most of it.

### Subject affiliation & classification (for GPS tracks)

These control how a subject's track renders on the CMORE map.

- **Default affiliation** — affiliation for subjects whose type isn't in the affiliation list. Controls track colour: `Unknown`=yellow, `Friendly`=blue, `Hostile`=red, `Neutral`=green.
- **Subject type → affiliation** (`subject_type_to_affiliation`) — list mapping a Gundi `subject_subtype` (matched first) or `subject_type` to a CMORE affiliation.
- **Subject type → classification** (`subject_type_to_classification`) — list mapping a subject type to a CMORE classification (`battleDimension` / `force` / `type` / `role`), which selects the map icon. Valid values are instance-specific — see the `get-classification-tree` CLI command.

> **Why lists, not key→value maps?** The portal's form renderer mis-handles
> object-valued maps (renders `[object Object]`), so these are modelled as
> arrays with an explicit key field, and the classification's four fields are
> flattened onto the array item. This is a workaround for a portal bug
> (GUNDI-5371) and will be simplified once that's fixed.

---

## Attachments

Files attached to ER events (photos, documents) are delivered as **media
comments** on the mapped CMORE event — CMORE has no event-attachment endpoint,
so a file-bearing comment (multipart `POST /comment`) is how photos appear on
an event in the CMORE UI. Each comment is titled
`EarthRanger attachment: <filename>`.

There is **no per-integration setting on the CMORE side** — attachments are
delivered automatically once the pieces below are in place:

1. **Enable forwarding on the EarthRanger provider.** In the connection's
   **Provider → Pull Events** config, turn on **Forward Event Attachments**
   (off by default). Files added to an event after it was first forwarded are
   picked up on subsequent pull runs.
2. **Runner environment** (per-deployment, not per-integration):
    - `BUCKET_NAME` env var — the GCS bucket where Gundi stores attachment
      files (`cdip-files-<env>`; same name/value the classic dispatchers use).
      Set via the infra repo's `additional_env_vars`.
    - The runner's service account needs **read access**
      (`roles/storage.objectViewer`) on that bucket — routing hands the runner
      only the blob name, and it downloads the bytes itself.

### Ordering: attachment before its event

Message ordering isn't guaranteed, so an attachment can arrive **before** the
event it belongs to has been delivered. The runner treats this as a *retryable
wait*, not a failure: the delivery raises a `dependency_not_ready` error
("Waiting for a related object to be delivered") and PubSub redelivers it with
backoff until the parent event's CMORE message exists. Occasional entries of
this kind in the activity log are normal; see
[Troubleshooting](troubleshooting.md#attachments-dont-appear-in-cmore) if they
persist.

---

## Reference dropdowns in the portal

The Deliver mapping form's pick-a-value fields render as **live dropdowns** in
the Gundi portal (combobox with free-text entry). Options are fetched on
demand — when you open a dropdown, never on page load — through the
integrations' *reference actions*:

- **CMORE-side fields** (`tag`, `cmore_field`, `to_value`, and the
  classification fields) list live values from **this CMORE instance**: tag
  names, a tag's fields, a field's allowed options, and the classification
  tree.
- **EarthRanger-side fields** (`event_type`, `event_details_key`,
  `from_value`, and `subject_type` in the subject-mapping lists) list live
  values from the **connected EarthRanger provider(s)**: event types (grouped
  by category), an event type's fields, a choice field's values, and the
  subject subtypes observed on the site (grouped by subject type, with the
  types themselves offered as fallback options). Only **v2** ER event types
  are offered — see the ER runner's
  [reference actions](https://padas.github.io/gundi-integration-earthranger/actions/reference-actions/)
  docs for details; classic v1 types can still be typed manually.

Dropdowns cascade: picking a `tag` scopes the `cmore_field` list to
that tag's fields; picking an `event_type` scopes `event_details_key`, and so
on. Every field stays usable no matter what: while a parent value is empty, or
if a fetch fails or nothing offers options, the field is a plain free-text
input (with a retry link on failure). A saved value that's no longer among the
fetched options gets a warning badge but is **never** changed automatically.

### Multiple EarthRanger providers on one CMORE destination

A CMORE integration's Deliver config is shared by **all** of its connections,
so when several ER providers deliver into the same CMORE integration, the
ER-side dropdowns show the **union of every connected site's vocabulary,
deduped by value**:

- `event_type` lists every event type across all connected sites. A mapping
  keyed on a type that exists on only one site is fine — it simply never
  matches events arriving from the other sites.
- Cascades tolerate per-site differences: asking for the fields of an event
  type that one site doesn't define skips that site and returns the fields
  from the site(s) that do.
- If two sites define the same slug with different display names, the first
  provider's label is shown (cosmetic only — the stored value is always the
  slug). If they define the same slug with *different schemas*, the field and
  value lists are the union of both definitions.

## Create EarthRanger event types

The fastest way to get a reserve reporting into CMORE is to let the connector
build the EarthRanger side from the CMORE tags. The **Create EarthRanger event
types** action (on the CMORE integration's page in the Gundi portal, with a
Run button) takes the CMORE tags you pick and, on the EarthRanger site
connected to this destination, creates:

- one **event category** for this share group,
- one **event type per tag**, with a field per CMORE property, and
- one **choice list** per lookup property, holding the CMORE options.

Events reported with these types deliver to CMORE with the tag and every
mapped field filled, **with no `event_type_to_tag` entry**: the names carry
the ids the connector needs.

### The form

- **CMORE tags** — the tags to turn into event types, by ID (preferred) or
  exact name; the dropdown is the same one the mapping list uses.
- **Share group label** (optional) — display name of the EarthRanger category,
  e.g. the reserve's name. Default `CMORE group <Owner Group ID>`. Only used
  when the category is first created.
- **Preview only** — checks the EarthRanger connection and permissions and
  reports what would be created or updated, without writing. Run this first.

### What it needs

- Exactly **one EarthRanger provider** connected to this CMORE destination in
  Gundi. None or several stops the action with
  `Expected exactly one connected EarthRanger provider, found N …`.
- That provider's EarthRanger user must be able to manage event types. The
  action checks `user/me` before writing and refuses with
  `EarthRanger user '<name>' lacks permissions: …` naming what is missing:
  `eventcategory: add`, `eventtype: add, change`, `choice: add, change`. Grant
  them on the EarthRanger site (Admin → Users → permission sets).
- A saved integration: the action does not run on a draft.

### Naming convention

| Object | Value | Example |
|---|---|---|
| Category | `cmore_<group>` | `cmore_7932` |
| Event type | `cmore_<group>_<tagid>_<slug>` | `cmore_7932_20_evidence_of_poacher` |
| Field key | `f<fieldid>_<slug>` | `f261_evidence_type` |
| Choice value | the CMORE option text | `Abalone Harvesting` |

`<group>` is this integration's **Owner Group ID**, so one EarthRanger site
can serve several CMORE share groups: each CMORE integration delivers only
the types that carry *its* group and skips the others as unmapped. The slug
is cosmetic; on a re-run existing objects are matched by the id prefix, so a
CMORE rename updates the display name but never creates a second type or
field. Nothing in EarthRanger is ever deleted: deselected tags are left
alone and dropped options are deactivated.

### Field translation

| CMORE | EarthRanger |
|---|---|
| String, IDNumber | text |
| Text | multi-line text |
| Number, Duration | number |
| Boolean | checkbox |
| DateTime | date/time |
| Lookup, FixedLookup | select, or multiselect when the tag allows multiple values |
| Media, Message | skipped |

Two more rules: a property named **Reported by** is skipped, because the
connector fills it from the EarthRanger reporter (see
[Who reported the event](#who-reported-the-event)); and a lookup whose options
have **nested children** (e.g. *Animal* → Hoof Animal → Buffalo) is offered at
its **top level only** for now — the EarthRanger API cannot create sub-choices
yet. Both are listed in the action result (`skipped`, `shallow_fields`) and in
the activity log.

### Delivery

For an event whose type has no `event_type_to_tag` entry, the connector checks
whether the type is one of its generated ones (`cmore_<group>_<tagid>_…` with
its own group). If so, the tag comes from `<tagid>`, each `f<fieldid>_…` key in
`event_details` goes to that field, and lookup values are matched to the
CMORE options by text. Keys that are not generated field keys (a field you
added to the type by hand) are ignored. An explicit mapping entry for a
generated type always wins over the convention.

## Scaffolding mappings (CLI alternative)

The repo ships a CLI that **generates** an `event_type_to_tag` mapping from a
live ER event type + the CMORE tag schema, so you fill in only the genuine
decisions instead of authoring everything by hand:

```bash
python -m app.datasource.cli scaffold-mapping \
  --gundi-username <you> --connection <er↔cmore-connection-id> \
  --event-type <er_event_type> --write
```

It discovers both ends from the Gundi connection, auto-matches fields by name,
pre-fills lookup values it can resolve, and walks you (arrow-key menus) through
the rest — then writes the mapping back to this integration's Deliver config.
Run without `--write` (or with `--out FILE`) to just print the config.

Other useful CLI commands: `get-tags` (dump the CMORE tag schema visible to a
token) and `get-classification-tree` (valid classification values).

[← Overview](index.md)
