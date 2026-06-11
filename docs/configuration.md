---
title: Configuration
---

# Configuring the CMORE integration

All settings are configured on the **CMORE destination integration** in the
Gundi portal. The integration exposes two actions: **Authenticate** and
**Deliver**.

[← Overview](index.html)

---

## Authenticate

Credentials and the target CMORE instance. This action is also **executable**
from the portal — run it to verify the token works before any data flows
(catches a bad token or zero tag visibility up front).

| Field | Required | Description |
|---|---|---|
| **API Token** | yes | CMORE API token (raw value, *without* the `Token ` prefix — the client adds it). Stored as a secret. |
| **API Base URL** | — | CMORE API base, e.g. `https://cmorewc1.chpc.ac.za/za/WebAPI/api`. Note it includes the API path, not just the host. |
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

Each entry (**CmoreTagMapping**):

- **Gundi event_type** — the event_type string on incoming events (e.g. `rhino_carcass`).
- **CMORE Tag Name** — the exact CMORE tag to attach (e.g. `Rhino Carcass`). Resolved to a tag id at runtime from CMORE's `/v2/tags/getfull`; must be visible to this integration's ShareGroup.
- **Field Mappings** — list of **CmoreFieldMapping**:
  - **Gundi event_details key** → **CMORE field name** (within the chosen tag).
  - **Value Mappings** (optional) — list of `source value → CMORE value` pairs.

#### How field values are resolved

Per the CMORE field's data type:

- **Lookup / FixedLookup** — the source value is resolved to a valid CMORE option: first via the field's **value mappings**, then a **case/punctuation-insensitive** match against the tag's options (so ER `male` → CMORE `Male` needs no mapping, but ER `b_3_months1_year` → CMORE `Calf` does). A value that still doesn't match a valid option is **dropped and logged** — never sent as garbage. CMORE matches lookups by their **value string** (not id).
- **Number / Boolean** — validated/coerced (`yes/no/true/false/1/0` → `true`/`false`).
- **String / Text** — sent as-is.

> Authoring these mappings by hand is tedious for tag-heavy event types. Use
> the [scaffold tool](#scaffolding-mappings) to generate most of it.

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

## Scaffolding mappings

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

[← Overview](index.html)
