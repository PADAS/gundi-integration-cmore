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
- Also confirm the configured **CMORE Tag** ref resolves, and that an
  `event_type_to_tag` entry exists for the event's `event_type` (an unmapped
  type posts with no tag by design — note that untagged events are excluded
  from CMORE's tag-based filtering, analytics, reporting, dashboards, and
  rule-based workflows). Two ways a ref stops resolving:
  - **ID-based ref** (`"tag": "8443"`): the ID doesn't exist on this instance
    — typo, or the config was copied from a different CMORE instance (IDs are
    instance-specific).
  - **Name-based ref** (`"tag": "Rhino Carcass"`): the tag was renamed in
    CMORE. Switch the mapping to the tag ID (the `get-tags` CLI output or the
    scaffold legend shows it) — IDs survive renames.

## A specific lookup value is dropped

Log line:
```
CMORE field 'Rhino Spesies' (Lookup) has no option matching 'Black Rhino'; dropping value. Valid options: [...]
```
The source value didn't resolve to a valid CMORE option. Add a **value mapping**
(`source value → CMORE value`) for that field, e.g. `Black Rhino → Black`. See
[Configuration → value resolution](configuration.md#how-field-values-are-resolved).
The [`scaffold-mapping`](configuration.md#scaffolding-mappings-cli-alternative) tool surfaces
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

## Attachments don't appear in CMORE

Photos/files on an ER event should arrive as **media comments** on the
delivered CMORE event (titled `EarthRanger attachment: <filename>`). Check in
order:

- **Forwarding not enabled.** The EarthRanger provider's **Forward Event
  Attachments** toggle (Provider → Pull Events) is **off by default**. Nothing
  reaches this runner until it's on.
- **`BUCKET_NAME` not set on the runner.** Log line:
  ```
  BUCKET_NAME env var is not set; cannot download Gundi attachments.
  ```
  Set it to Gundi's attachments bucket (`cdip-files-<env>`) via the infra
  repo's `additional_env_vars`.
- **No read access to the bucket.** A `403 Forbidden` from
  `storage.googleapis.com` in the logs means the runner's service account
  lacks `roles/storage.objectViewer` on the attachments bucket.
- **Stuck waiting for the parent event.** Activity-log entries titled
  **"Waiting for a related object to be delivered"** (`dependency_not_ready`)
  are normal when an attachment arrives before its event — PubSub redelivers
  with backoff and the comment posts once the event lands. If they *persist*,
  the parent event itself never delivered: fix the event's delivery first
  (see the sections above), and the attachment will follow on a retry.
- **File missing from storage.** Activity-log ERROR **"Attachment file not
  found in storage — dropping"** means the blob named in `file_path` isn't in
  the bucket — usually a wrong `BUCKET_NAME` (pointing at the wrong
  environment's bucket) rather than a genuinely missing file.

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
