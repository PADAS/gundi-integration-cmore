---
marp: true
paginate: true
size: 16:9
theme: default
style: |
  section {
    font-size: 26px;
  }
  section.lead h1 {
    font-size: 56px;
  }
  h1 {
    color: #1a5632;
  }
  h2 {
    color: #1a5632;
    font-size: 34px;
  }
  blockquote {
    background: #f2f7f4;
    border-left: 6px solid #1a5632;
    padding: 0.4em 0.8em;
  }
  table {
    font-size: 22px;
  }
  img[alt~="center"] {
    display: block;
    margin: 0 auto;
  }
---

<!-- _class: lead -->
<!-- _paginate: false -->

# Sharing EarthRanger events with CMORE

**A field guide to setting up the sync — no code required**

Rhino Carcass, from a ranger's report to your partners' map, automatically.

---

## What you're building

```
EarthRanger  ──►  Gundi  ──►  CMORE
(rangers          (routes and    (partners see the event,
 report events)    translates)    tagged, on their map)
```

- **EarthRanger** — where events are reported
- **Gundi** (gundiservice.org) — polls ER, translates, delivers
- **CMORE** — where events land, in your **share group**, classified with a **tag**

Configure each piece once. Then it runs by itself.

---

## The result, up front

![w:780 center](../images/cmore-15-delivered-event.png)

A ranger's Rhino Carcass report — tag fields filled, linked back to EarthRanger.

---

## Before you start: CMORE-side prerequisites

> **Your reserve's CMORE Organisation Admin does these first:**
> 1. Creates a **dedicated CMORE account** for the integration (e.g.
>    `lapalala.earthranger`) — not an individual staff member's login
> 2. Assigns the **Integration permission** to that account
>
> **Then request from the CMORE team (CSIR) — not self-service:**
>
> 3. An **organisation (share group)** on the instance, with that account in it
> 4. **Tag domain access** — e.g. *Wildlife* (Rhino Carcass + 16 more). Some
>    domains are restricted IP, granted case-by-case. Say **which tags** and
>    **which share group**.

Use the dedicated account for the rest of Part 1. Everything else you can do yourself.

---

## Part 1 — CMORE: two web interfaces

![w:700 center](../images/cmore-07-admin-manage-users.png)

The **Web Portal** (map view) — and the **Cmore Admin Site**, opened via the
**gears icon** in the portal toolbar. Setup happens in the Admin Site.

---

## Create a service, get the API token

![w:640 center](../images/cmore-09-service-detail.png)

**Service → Create New Service**, then **View**: the **Auth Token** is your API
key. (**GenerateNewToken** rotates it if it ever leaks.)

---

## Link the service to your share group

![w:660 center](../images/cmore-13-service-edit.png)

**Edit** the service: **Target Group ID** = your share group's number
(here **8334**) and **State = Active**. That number is also Gundi's **Owner Group ID**.

---

## Check your tags are visible

![w:640 center](../images/cmore-14-new-event-choose-tag.png)

Portal → **New Event**: your domain's tags must appear (here **Wildlife** →
Rhino Carcass etc.). Missing tags are silently dropped from delivered events.

---

## Part 2 — Gundi: open your connection

![w:700 center](../images/gundi-01-connection-view.png)

**Connections** → your connection → **Destinations** tab → the CMORE destination.

---

## Enter the three CMORE values

![w:720 center](../images/gundi-03-authenticate-form.png)

**API Base URL** (ends in `/za/WebAPI/api`) · **API Token** · **Owner Group ID**

---

## Test before any data flows

![w:720 center](../images/gundi-04-authenticate-run.png)

Click **Test Connection** — you want the green **Valid Credentials**.

---

## Map the event type to a CMORE tag

![w:750 center](../images/gundi-05-deliver-mapping.png)

**Deliver → Event type → CMORE tag**: `rhino_carcass` → **Rhino Carcass**,
then one row per field (`animal_sex` → `Animal Sex`, …).

---

## Value Mappings: translating vocabularies

![w:700 center](../images/gundi-06-value-mappings.png)

`male` → `Male` matches by itself. But `a_0-3_months` → `Calf` needs a row.
Unmatched values are dropped and logged — never sent as garbage.

---

## Part 3 — Choose what leaves EarthRanger

![w:660 center](../images/gundi-07-er-event-filter.png)

Connection → **Provider** tab → **Pull Events**: **Event Types** must include
`rhino_carcass`; **Run On Schedule** on. This filter is why an event type can
work in ER yet never reach CMORE.

---

## Part 4 — Prove it: report in EarthRanger…

![w:680 center](../images/er-01-report-form.png)

A test Rhino Carcass: **Sub-adult / White Rhino / Female**, location set.

---

## …and watch it arrive in CMORE

![w:680 center](../images/cmore-15-delivered-event.png)

Minutes later: tag populated (**Sub-Adult / White / Female**), plus a comment
linking back to the EarthRanger event.

---

## If something doesn't look right

| Symptom | First place to look |
|---|---|
| Event arrives **without the tag** | Tag name spelling; tag-domain access for your group |
| Tag there, **one field empty** | That value needs a Value Mapping |
| **Nothing arrives** | Event Types filter (Part 3); connection logs in Gundi |
| **Test Connection fails** | Token, base URL (`…/za/WebAPI/api`), service Active + linked |
| **No link back to ER** | See the troubleshooting guide |

---

<!-- _class: lead -->

## Where to get help

**Step-by-step tutorial (this walkthrough, with every screenshot):**
the *Tutorial: sync an event type* page in the integration docs

**Deeper reference:** *Configuration* and *Troubleshooting* pages

**Gundi questions:** support.earthranger.com · **CMORE access:** your CSIR contact
