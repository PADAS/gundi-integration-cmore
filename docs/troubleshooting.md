---
title: Troubleshooting
---

# Troubleshooting

Common symptoms when events/tracks don't show up correctly in CMORE, and how to
diagnose them. Most are visible in the CMORE runner logs:

```bash
gcloud run services logs read cmore-actions-runner \
  --project=<project> --region=us-central1 --limit=100
```

[← Overview](index.md) · [Configuration](configuration.md)

---

## Events post, but the structured tag is missing

**Most likely: the ShareGroup can't see the tag.** CMORE scopes tag visibility
per ShareGroup; if the integration's token has no visibility to the tag, the
event still posts (description + location) but the tag is dropped.

- Check what the token can see:
  ```bash
  python -m app.datasource.cli --base-url <cmore-base> --token <token> get-tags
  ```
  Zero tags (or the domain missing) → a **CMORE admin must subscribe the
  ShareGroup to the tag domain**. The API can't do this; it's portal-admin only.
- Also confirm the configured **Tag Name** matches a real CMORE tag exactly, and
  that an `event_type_to_tag` entry exists for the event's `event_type` (an
  unmapped type posts with no tag by design).

## A specific lookup value is dropped

Log line:
```
CMORE field 'Rhino Spesies' (Lookup) has no option matching 'Black Rhino'; dropping value. Valid options: [...]
```
The source value didn't resolve to a valid CMORE option. Add a **value mapping**
(`source value → CMORE value`) for that field, e.g. `Black Rhino → Black`. See
[Configuration → value resolution](configuration.md#how-field-values-are-resolved).
The [`scaffold-mapping`](configuration.md#scaffolding-mappings) tool surfaces
these to fill in.

## Nothing reaches the runner at all

- **`broker_config.topic` not set** on the destination integration. Without it,
  cdip-routing falls back to a legacy topic name (`destination-<id>-<env>`) and
  nothing arrives. Set it to `cmore-push-data-topic`.
- **Push subscription returns 401.** The Pub/Sub service agent needs
  `roles/iam.serviceAccountTokenCreator` on the runner's service account to mint
  the OIDC token; without it the runner rejects deliveries.
- Confirm the `cmore-push-data-topic` + push subscription actually exist in the
  project (they're provisioned manually per environment).

## The source deep link doesn't appear in CMORE

The link is posted as a **comment** on the event (`Source: <url>`), not in the
title — check the event's detail view / comments.

If it's missing entirely, `provider_metadata.source_event_url` isn't reaching
the runner. The whole chain must carry it:

- the EarthRanger runner stamps `provider_metadata` (needs `er_ui_root` configured),
- the cdip Sensors API forwards it (needs `gundi-core>=1.12.0`),
- cdip-routing preserves it (needs `gundi-core>=1.12.0`).

The runner logs the value it received:
```
_push_event received: ... provider_metadata={'source_event_url': '...'}
```
`provider_metadata=None` there means it was dropped upstream.

## An event edit's comment lands on the wrong event

The update→comment mapping is keyed by the event's `gundi_id` (unique per
event). If comments attach to the wrong event, an older build keyed by
`external_source_id` (shared across a source's events) may be deployed — redeploy
the runner.

## Subject tracks show the wrong colour / icon

Affiliation controls track colour (`Unknown`=yellow, `Friendly`=blue,
`Hostile`=red, `Neutral`=green); classification selects the map icon. Map the
subject type via **Subject type → affiliation / classification**
([Configuration](configuration.md)). Classification values are instance-specific
— list valid ones with:
```bash
python -m app.datasource.cli --base-url <cmore-base> --token <token> get-classification-tree
```

## All events share one source in CMORE

If every event groups under one source, the source defaults to
`default-source`. The EarthRanger runner sets the Gundi `source` to the event
type so events group sensibly — confirm that build is deployed.

## The scaffold CLI errors with `UnsupportedProtocol`

```
httpx.UnsupportedProtocol: Request URL is missing an 'http://' or 'https://' protocol.
```
An integration's `base_url` is stored without a scheme. Recent CLI builds prepend
`https://` automatically; otherwise add `https://` to the ER/CMORE integration's
base URL in the portal.
