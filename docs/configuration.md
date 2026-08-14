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
| **API Base URL** | yes | CMORE API base for **your** instance: the instance's server + `/za/WebAPI/api` (e.g. `https://cmore.csir.co.za/za/WebAPI/api` on DFFE). URLs differ per CMORE deployment; note the value includes the API path, not just the host. |
| **Owner Group ID** | yes | The CMORE **ShareGroupId** linked to this token. All events are posted to this group; it controls which CMORE users/teams can see the data. |

> The token's ShareGroup must have **tag visibility** for any tags you map (a
> CMORE admin subscribes the group to a tag domain). If the group sees zero
> tags, events still post but the structured tag is silently dropped.

---

## Deliver

How routed data is transformed for CMORE. One handler dispatches internally on
the payload type (Observation / Event / EventUpdate), so all of the following
live under the single Deliver config.

### Event type → CMORE tag (`event_type_to_tag`)

Optional list mapping each Gundi `event_type` to a CMORE tag and its fields.
Events whose type isn't listed still post (description + location + deep-link
comment) but **without** a structured tag.

> **Impact of an unmapped type:** untagged events show on the map but are
> excluded from everything that relies on classification — tag-based
> filtering and lookups, analytics, reporting, dashboards, and rule-based
> workflows.

Each entry (**CmoreTagMapping**):

- **Gundi event_type** — the event_type string on incoming events (e.g. `rhino_carcass`).
- **CMORE Tag** — the tag **ID** (preferred — immutable, e.g. `8443`) or the exact tag name. Resolved at runtime from CMORE's `/v2/tags/getfull`; must be visible to this integration's ShareGroup. See [ref resolution](#how-tag-and-field-refs-are-resolved).
- **Field Mappings** — list of **CmoreFieldMapping**:
  - **Gundi event_details key** → **CMORE Field** (field ID preferred, or exact field name — within the chosen tag).
  - **Value Mappings** (optional) — list of `source value → CMORE value` pairs.

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

- **CMORE-side fields** (`tag_name`, `cmore_field_name`, `to_value`, and the
  classification fields) list live values from **this CMORE instance**: tag
  names, a tag's fields, a field's allowed options, and the classification
  tree.
- **EarthRanger-side fields** (`event_type`, `event_details_key`,
  `from_value`) list live values from the **connected EarthRanger
  provider(s)**: event types (grouped by category), an event type's fields,
  and a choice field's values. Only **v2** ER event types are offered — see
  the ER runner's
  [reference actions](https://padas.github.io/gundi-integration-earthranger/actions/reference-actions/)
  docs for details; classic v1 types can still be typed manually.

Dropdowns cascade: picking a `tag_name` scopes the `cmore_field_name` list to
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
