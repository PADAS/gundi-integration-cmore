---
title: Overview
---

# Gundi CMORE Action Runner

This is a [Gundi](https://gundiservice.org) **destination action runner** for
[CMORE](https://cmore.csir.co.za) (a situational-awareness and collaboration
platform from CSIR). It receives data routed through Gundi — typically from an
**EarthRanger** site — and delivers it to the CMORE API.

It's a single Cloud Run service that handles **all** customer integrations of
type `cmore`: a destination integration is configured per-customer in the Gundi
portal, and this runner transforms and delivers their data to their CMORE
instance and share group.

## What it delivers

Two kinds of Gundi data flow to CMORE:

| Gundi data | Becomes in CMORE |
|---|---|
| **Observations** (GPS tracks for a subject — vehicle, ranger, animal, device) | a **GNode** (virtual sensor) whose position updates as the subject moves |
| **Events** (incident reports — sightings, carcasses, fence breaks, …) | a geolocated **event message**, optionally classified with a structured **tag** |
| **Event updates** (edits to an event — new notes, status/priority/title changes) | **comments** on the original CMORE event |

For observations, the runner **auto-creates the GNode** the first time it sees a
new subject, then posts each subsequent position under it. Affiliation and
classification (which control the track's colour and map icon in CMORE) are
configurable per subject type.

For events, each event is posted to the configured **share group** (Owner Group
ID). If a tag mapping is configured for the event's type, the event arrives
already classified, with its CMORE tag fields populated from the event details.
A deep link back to the source event is added as a comment so CMORE users can
click through to the origin system.

## Where it sits in Gundi

```
EarthRanger ──► EarthRanger action runner ──► Gundi (portal + routing)
                                                   │
                                                   ▼
                                       CMORE action runner (this service)
                                                   │
                                                   ▼
                                              CMORE API
```

The pieces are configured independently: EarthRanger access and pull filters
(which subjects/event types to share, how often to poll) live in the
**EarthRanger integration**; routing rules live in **Gundi**; and CMORE
credentials + the delivery mappings live in **this integration** (see
[Configuration](configuration.md)).

> cdip-routing forwards traffic to this runner via the generic-model path
> (a `GundiDelivery` envelope) because `cmore` is a generic-model destination
> type — the runner does the CMORE-specific transformation, not cdip-routing.

## Next

- [**Configuration**](configuration.md) — the Authenticate and Deliver
  actions and every setting, plus the scaffold tool that helps author tag
  mappings.
- [**Troubleshooting**](troubleshooting.md) — common symptoms (missing tags,
  dropped values, deep link not showing) and how to diagnose them.
