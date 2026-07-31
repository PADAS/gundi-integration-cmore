# CMORE Attachment Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver Gundi `Attachment` payloads (photos/files from EarthRanger events) to CMORE as multipart file comments on the corresponding CMORE event, instead of dropping them.

**Architecture:** The deliver action already receives `Attachment` payloads via the `GundiDelivery` envelope and drops them. An `Attachment` carries `related_to` (the gundi_id of the parent Event) and `file_path` (a blob name in Gundi's GCS attachments bucket — the same bucket the classic dispatchers read via `BUCKET_NAME`). We add: (1) a multipart upload method on `CmoreClient` targeting CMORE's `POST /comment` endpoint ("Create a Comment with a File Attachment" in `docs/Cmore API Reference.html`), (2) a read-only GCS download helper, and (3) a `_push_attachment_as_comment` handler wired into `action_deliver`'s dispatch, reusing the existing `gundi_id → cmore_message_id` Redis mapping written by `_push_event`.

**Tech Stack:** Python 3.10, FastAPI action-runner template, httpx, google-cloud-storage, pytest + pytest-asyncio + pytest-mock.

**Companion plan:** The provider side (reading files out of EarthRanger and posting them to Gundi) is a separate plan in the `gundi-integration-earthranger` repo: `docs/superpowers/plans/2026-07-30-er-event-attachments-to-gundi.md`. This plan is independently testable without it (Attachment payloads can be simulated), but end-to-end photo flow needs both.

## Global Constraints

- Pydantic models for all data structures — no `@dataclass` (repo convention, CLAUDE.md).
- All CMORE API client code lives in `app/datasource/`; Gundi/framework-side helpers live in `app/services/` (repo convention).
- External HTTP calls in `app/datasource/` wrapped with `backoff` (`@retry_transient` in `client.py`).
- Tests mock all external services; test files under the package's `tests/` dir; run with `pytest`.
- After editing `requirements.in`: `pip-compile --output-file=requirements.txt requirements-base.in requirements-dev.in requirements.in`.

## Key facts an implementer needs (verified during research)

- **CMORE endpoint:** `POST {base}/comment` as `multipart/form-data`. Form fields: `description`, `rootMessageId`, `uploadType` (plus optional `latitude`/`longitude`/`accuracy`/`altitude`), and a `file` part with filename + content type. Response contains the created comment's id. Source: `docs/Cmore API Reference.html`, section "Create a Comment with a File Attachment".
- **Gundi `Attachment` schema** (`gundi_core.schemas.v2.Attachment`): `gundi_id`, `related_to` (gundi_id of the parent Event), `file_path` (GCS blob name), `source_id`, `external_source_id`, `annotations`.
- **Bucket:** the classic dispatchers (see `gundi-dispatcher-er/core/dispatchers.py::EREventAttachmentDispatcher`) read the same files with `cdip_connector.core.cloudstorage` from the bucket named by the `BUCKET_NAME` env var, in project `GCP_PROJECT_ID`. We mirror that contract.
- **Event mapping:** `_push_event` already persists `{"cmore_message_id": <int>}` in Redis keyed by `(integration_id, "deliver", str(event.gundi_id))` with a 90-day TTL (`CMORE_EVENT_MAPPING_TTL_SECONDS`).
- **httpx gotcha:** `CmoreClient.__init__` currently sets a client-level default header `Content-Type: application/json`. httpx only sets the multipart boundary content-type when no content-type is already present in merged headers — so the default must be removed or the multipart POST goes out mislabeled as JSON. httpx infers the right content-type per request (`json=` → JSON, `data=`+`files=` → multipart), and the one place that needs urlencoded (`login`) already passes an explicit per-request header.
- **Ordering race:** PubSub delivery order is not guaranteed; an Attachment can reach this runner before its parent Event. The classic dispatcher raises `ReferenceDataError` so the message is retried. We do the same by raising (→ non-2xx → PubSub redelivery with backoff) rather than dropping, which is a deliberate difference from the `EventUpdate` path's warn-and-drop.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `app/datasource/client.py` | Modify | Add `post_comment_with_file()`; remove client-level default `Content-Type` header |
| `app/datasource/tests/test_client.py` | Create | Unit tests for the new client method + header behavior |
| `app/services/cloud_storage.py` | Create | Read-only GCS download of Gundi attachment blobs |
| `app/services/tests/test_cloud_storage.py` | Create | Unit tests for the download helper (mocked GCS) |
| `app/settings/integration.py` | Modify | `ATTACHMENTS_BUCKET_NAME` setting (env `BUCKET_NAME`) |
| `app/actions/handlers.py` | Modify | `_push_attachment_as_comment()` + dispatch branch in `action_deliver` |
| `app/actions/tests/test_handlers.py` | Modify | Replace the drop-Attachment test; add delivery/race/missing-blob tests |
| `requirements.in`, `requirements.txt` | Modify | Add `google-cloud-storage` |
| `docs/index.md` | Modify | Document attachment support |

---

### Task 1: `CmoreClient.post_comment_with_file()` multipart upload

**Files:**
- Modify: `app/datasource/client.py` (class `CmoreClient`, after `post_comment` at ~line 111)
- Create: `app/datasource/tests/test_client.py`

**Interfaces:**
- Consumes: existing `CmoreComment` model (`description: str`, `rootMessageId: int`, `uploadType: UploadType`), existing `retry_transient` / `_safe_json` helpers in the same file.
- Produces: `async def post_comment_with_file(self, comment: CmoreComment, filename: str, content: bytes, content_type: str = "application/octet-stream") -> dict` — used by Task 3.

- [ ] **Step 1: Write the failing tests**

Create `app/datasource/tests/test_client.py`:

```python
from unittest.mock import AsyncMock

import httpx
import pytest

from app.datasource.client import CmoreClient
from app.datasource.schemas import CmoreComment, UploadType


def _ok_response(payload):
    return httpx.Response(
        200, json=payload, request=httpx.Request("POST", "https://cmore.test/comment")
    )


@pytest.mark.asyncio
async def test_post_comment_with_file_sends_multipart_form():
    client = CmoreClient(base_url="https://cmore.test", token="abc")
    client._client.post = AsyncMock(return_value=_ok_response({"id": 555}))

    comment = CmoreComment(description="EarthRanger attachment: photo.jpg", rootMessageId=14697)
    result = await client.post_comment_with_file(
        comment, filename="photo.jpg", content=b"\xff\xd8jpegbytes", content_type="image/jpeg"
    )

    assert result == {"id": 555}
    client._client.post.assert_awaited_once()
    args, kwargs = client._client.post.await_args
    assert args[0] == "/comment"
    # Form fields mirror the JSON comment body; multipart requires strings.
    assert kwargs["data"] == {
        "description": "EarthRanger attachment: photo.jpg",
        "rootMessageId": "14697",
        "uploadType": UploadType.GENERATED.value,
    }
    assert kwargs["files"] == {"file": ("photo.jpg", b"\xff\xd8jpegbytes", "image/jpeg")}
    await client._client.aclose()


@pytest.mark.asyncio
async def test_client_has_no_default_content_type_header():
    # A client-level Content-Type default would override httpx's multipart
    # boundary header and break file uploads (httpx only sets the content-type
    # for `files=` requests when none is already present).
    client = CmoreClient(base_url="https://cmore.test", token="abc")
    assert "content-type" not in client._client.headers
    assert client._client.headers["Authorization"] == "Token abc"
    await client._client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/datasource/tests/test_client.py -v`
Expected: FAIL — `AttributeError: 'CmoreClient' object has no attribute 'post_comment_with_file'` and the header assertion fails because `content-type` is currently set.

- [ ] **Step 3: Implement**

In `app/datasource/client.py`, change `__init__` — replace:

```python
        headers = {"Content-Type": "application/json"}
```

with:

```python
        # No default Content-Type: httpx infers it per request (json= → JSON,
        # data=+files= → multipart with boundary). A client-level default would
        # override the multipart boundary header and break file uploads.
        headers = {}
```

Then add the new method directly after `post_comment`:

```python
    @retry_transient
    async def post_comment_with_file(
        self,
        comment: CmoreComment,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Attach a file comment (photo/document) to an existing CMORE event.

        CMORE's ``POST /comment`` endpoint accepts a multipart form where the
        ``file`` part carries the binary and the remaining parts mirror the
        JSON comment fields. Media comments are how the CMORE UI shows photos
        attached to an event.
        """
        data = {
            "description": comment.description,
            "rootMessageId": str(comment.rootMessageId),
            "uploadType": comment.uploadType.value,
        }
        files = {"file": (filename, content, content_type)}
        response = await self._client.post("/comment", data=data, files=files)
        response.raise_for_status()
        return _safe_json(response, {})
```

- [ ] **Step 4: Run the new tests and the full suite**

Run: `pytest app/datasource/tests/test_client.py -v && pytest`
Expected: new tests PASS; full suite PASS (the removed default header must not break existing JSON/urlencoded calls — `login` sets its own header, all other calls use `json=`).

- [ ] **Step 5: Commit**

```bash
git add app/datasource/client.py app/datasource/tests/test_client.py
git commit -m "feat: multipart file-comment upload on CmoreClient"
```

---

### Task 2: GCS attachment download helper + settings + dependency

**Files:**
- Create: `app/services/cloud_storage.py`
- Create: `app/services/tests/test_cloud_storage.py`
- Modify: `app/settings/integration.py` (currently just a placeholder comment)
- Modify: `requirements.in` (add `google-cloud-storage`), then regenerate `requirements.txt`

**Interfaces:**
- Consumes: `app.settings` (`GCP_PROJECT_ID` already exists in `app/settings/base.py`; `ATTACHMENTS_BUCKET_NAME` added here).
- Produces: `def download_attachment(file_path: str) -> Optional[bytes]` — synchronous (google-cloud-storage has no async API); Task 3 calls it via `asyncio.to_thread`. Returns `None` when the blob doesn't exist.

- [ ] **Step 1: Add the dependency**

Append to `requirements.in`:

```
google-cloud-storage
```

Run: `pip-compile --output-file=requirements.txt requirements-base.in requirements-dev.in requirements.in && pip install -r requirements.txt`
Expected: `google-cloud-storage` appears pinned in `requirements.txt` and imports cleanly (`python -c "from google.cloud import storage"`).

- [ ] **Step 2: Add the setting**

Replace the contents of `app/settings/integration.py` (currently only `# Add your integration-specific settings here`) with:

```python
# Add your integration-specific settings here
from environs import Env

env = Env()
env.read_env()

# GCS bucket where Gundi's sensors API stores event-attachment files. The
# routing layer hands destinations only the blob name (Attachment.file_path);
# this runner needs read access to the bucket to fetch the bytes. The env var
# name matches the classic dispatchers (cdip_connector BUCKET_NAME) so the
# same per-environment value can be reused.
ATTACHMENTS_BUCKET_NAME = env.str("BUCKET_NAME", None)
```

Verify the settings package re-exports it: `python -c "from app import settings; print(settings.ATTACHMENTS_BUCKET_NAME)"`. If `app/settings/__init__.py` does not already do `from .integration import *`, add that line.

- [ ] **Step 3: Write the failing tests**

Create `app/services/tests/test_cloud_storage.py`:

```python
from unittest.mock import MagicMock

import pytest


def test_download_attachment_returns_bytes(mocker):
    from app.services import cloud_storage

    blob = MagicMock()
    blob.download_as_bytes.return_value = b"jpegbytes"
    bucket = MagicMock()
    bucket.get_blob.return_value = blob
    mocker.patch.object(cloud_storage, "_bucket", return_value=bucket)

    assert cloud_storage.download_attachment("attachments/abc_photo.jpg") == b"jpegbytes"
    bucket.get_blob.assert_called_once_with("attachments/abc_photo.jpg")


def test_download_attachment_returns_none_for_missing_blob(mocker):
    from app.services import cloud_storage

    bucket = MagicMock()
    bucket.get_blob.return_value = None
    mocker.patch.object(cloud_storage, "_bucket", return_value=bucket)

    assert cloud_storage.download_attachment("attachments/nope.jpg") is None


def test_bucket_requires_configuration(mocker):
    from app.services import cloud_storage

    cloud_storage._bucket.cache_clear()
    mocker.patch.object(cloud_storage.settings, "ATTACHMENTS_BUCKET_NAME", None)
    with pytest.raises(ValueError, match="BUCKET_NAME"):
        cloud_storage._bucket()
    cloud_storage._bucket.cache_clear()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest app/services/tests/test_cloud_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.cloud_storage'`.

- [ ] **Step 5: Implement**

Create `app/services/cloud_storage.py`:

```python
"""Read-only access to Gundi's attachment files in Google Cloud Storage.

Gundi's sensors API stores event-attachment files in a GCS bucket; the
routing layer passes destination integrations only the blob name
(``Attachment.file_path``). This mirrors the pattern used by the classic
dispatchers (``cdip_connector.core.cloudstorage``) with a minimal client.
"""
import logging
from functools import lru_cache
from typing import Optional

from google.cloud import storage

from app import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _bucket():
    if not settings.ATTACHMENTS_BUCKET_NAME:
        raise ValueError(
            "BUCKET_NAME env var is not set; cannot download Gundi attachments."
        )
    client = storage.Client(project=settings.GCP_PROJECT_ID)
    return client.bucket(settings.ATTACHMENTS_BUCKET_NAME)


def download_attachment(file_path: str) -> Optional[bytes]:
    """Return the attachment bytes, or None when the blob doesn't exist.

    Synchronous — google-cloud-storage has no async API. Call via
    ``asyncio.to_thread`` from async handlers to avoid blocking the loop.
    """
    blob = _bucket().get_blob(file_path)
    if blob is None:
        logger.warning(
            "Attachment blob %r not found in bucket %r",
            file_path,
            settings.ATTACHMENTS_BUCKET_NAME,
        )
        return None
    return blob.download_as_bytes()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest app/services/tests/test_cloud_storage.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Commit**

```bash
git add app/services/cloud_storage.py app/services/tests/test_cloud_storage.py \
        app/settings/integration.py requirements.in requirements.txt
git commit -m "feat: GCS download helper for Gundi attachment blobs"
```

---

### Task 3: `_push_attachment_as_comment` handler + dispatch + docs

**Files:**
- Modify: `app/actions/handlers.py` (new handler near `_push_event_update_as_comment` ~line 673; dispatch branch in `action_deliver` ~line 770; imports at top)
- Modify: `app/actions/tests/test_handlers.py` (replace `test_deliver_drops_attachment` ~line 760; extend `_patch_cmore_client` ~line 185)
- Modify: `docs/index.md` (payload-type support table/mention)

**Interfaces:**
- Consumes: `CmoreClient.post_comment_with_file(comment, filename, content, content_type)` from Task 1; `app.services.cloud_storage.download_attachment(file_path)` from Task 2; existing `state_manager`, `_get_auth_config`, `log_action_activity`, `CmoreComment`.
- Produces: `async def _push_attachment_as_comment(integration, action_config, attachment) -> dict` returning `{"attachment_posted": True, "cmore_message_id": ..., "cmore_response": ...}` on success, `{"dropped": True, "reason": ...}` on unroutable payloads; raises `ValueError` when the parent event hasn't been delivered yet (→ PubSub retry).

- [ ] **Step 1: Write the failing tests**

In `app/actions/tests/test_handlers.py`, extend `_patch_cmore_client` — add one line next to the existing `inner.post_comment` mock:

```python
    inner.post_comment_with_file = AsyncMock(return_value=post_comment_return or {"id": 88888})
```

Then REPLACE `test_deliver_drops_attachment` (the test asserting `result["dropped"] is True` for Attachment payloads, ~line 760) with these four tests:

```python
def _patch_attachment_download(mocker, content=b"\xff\xd8jpegbytes"):
    download = mocker.patch(
        "app.actions.handlers.download_attachment", return_value=content
    )
    return download


@pytest.mark.asyncio
async def test_deliver_attachment_posts_file_comment(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    state = _patch_state_manager(mocker)
    state.get_state = AsyncMock(return_value={"cmore_message_id": 14697})
    _patch_activity_logger(mocker)
    download = _patch_attachment_download(mocker)

    related_event_gundi_id = uuid.uuid4()
    att = Attachment(
        gundi_id=uuid.uuid4(),
        related_to=related_event_gundi_id,
        source_id=uuid.uuid4(),
        external_source_id="x",
        file_path="attachments/abc_photo.jpg",
    )
    delivery = GundiDelivery(payload=att, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    assert result["attachment_posted"] is True
    download.assert_called_once_with("attachments/abc_photo.jpg")
    inner.post_comment_with_file.assert_awaited_once()
    args, kwargs = inner.post_comment_with_file.await_args
    comment = args[0]
    assert comment.rootMessageId == 14697
    assert kwargs["filename"] == "abc_photo.jpg"
    assert kwargs["content"] == b"\xff\xd8jpegbytes"
    assert kwargs["content_type"] == "image/jpeg"
    # Looked up the mapping under the PARENT event's gundi_id.
    state.get_state.assert_awaited_once_with(
        integration_id=str(integration.id),
        action_id="deliver",
        source_id=str(related_event_gundi_id),
    )


@pytest.mark.asyncio
async def test_deliver_attachment_raises_when_parent_event_not_delivered(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    _patch_cmore_client(mocker)
    _patch_state_manager(mocker)  # get_state returns {} → no cmore_message_id
    _patch_activity_logger(mocker)
    _patch_attachment_download(mocker)

    att = Attachment(
        gundi_id=uuid.uuid4(),
        related_to=uuid.uuid4(),
        source_id=uuid.uuid4(),
        external_source_id="x",
        file_path="attachments/abc_photo.jpg",
    )
    delivery = GundiDelivery(payload=att, provider=provider_info)
    # Raising (instead of dropping) makes PubSub redeliver: the parent Event
    # may simply not have been processed yet (ordering isn't guaranteed).
    with pytest.raises(ValueError, match="not delivered yet"):
        await action_deliver(integration, deliver_config, delivery, metadata)


@pytest.mark.asyncio
async def test_deliver_attachment_drops_when_no_related_to(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_attachment_download(mocker)

    att = Attachment(
        gundi_id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        external_source_id="x",
        file_path="attachments/abc_photo.jpg",
    )
    delivery = GundiDelivery(payload=att, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    assert result == {"dropped": True, "reason": "missing_related_to"}
    inner.post_comment_with_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_deliver_attachment_drops_when_blob_missing(
    mocker, integration, deliver_config, provider_info, metadata
):
    from app.actions.handlers import action_deliver

    inner = _patch_cmore_client(mocker)
    state = _patch_state_manager(mocker)
    state.get_state = AsyncMock(return_value={"cmore_message_id": 14697})
    _patch_activity_logger(mocker)
    mocker.patch("app.actions.handlers.download_attachment", return_value=None)

    att = Attachment(
        gundi_id=uuid.uuid4(),
        related_to=uuid.uuid4(),
        source_id=uuid.uuid4(),
        external_source_id="x",
        file_path="attachments/gone.jpg",
    )
    delivery = GundiDelivery(payload=att, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    assert result == {"dropped": True, "reason": "file_not_found"}
    inner.post_comment_with_file.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/actions/tests/test_handlers.py -k attachment -v`
Expected: FAIL — the deliver dispatch still drops Attachments (`KeyError: 'attachment_posted'` / no `download_attachment` in handlers).

- [ ] **Step 3: Implement the handler**

In `app/actions/handlers.py`, add to the imports at the top:

```python
import asyncio
import mimetypes
import os

from app.services.cloud_storage import download_attachment
```

Add the handler after `_push_event_update_as_comment`:

```python
async def _push_attachment_as_comment(
    integration: Integration,
    action_config: DeliverConfig,
    attachment: schemas.v2.Attachment,
):
    """Deliver a Gundi Attachment as a CMORE file comment on the parent event.

    ``related_to`` carries the gundi_id of the Event the file belongs to; the
    CMORE messageId stored at ``_push_event`` time tells us which CMORE event
    to hang the media comment on. The bytes live in Gundi's attachment bucket
    under ``file_path``.

    A missing event mapping raises (→ PubSub redelivery with backoff) rather
    than dropping: message ordering isn't guaranteed, so the attachment often
    just beat its parent Event through the pipeline. This intentionally
    differs from the EventUpdate path's warn-and-drop — a retried update is
    redundant next pull, a dropped photo is gone for good.
    """
    related_to = attachment.related_to
    if not related_to or str(related_to) == "None":
        logger.warning(
            "Attachment without related_to; cannot route to a CMORE event. Dropping."
        )
        return {"dropped": True, "reason": "missing_related_to"}

    state = await state_manager.get_state(
        integration_id=str(integration.id),
        action_id="deliver",
        source_id=str(related_to),
    )
    cmore_message_id = state.get("cmore_message_id") if state else None
    if not cmore_message_id:
        raise ValueError(
            f"CMORE event for gundi_id={related_to} not delivered yet; "
            "attachment will be retried."
        )

    file_bytes = await asyncio.to_thread(download_attachment, attachment.file_path)
    if file_bytes is None:
        await log_action_activity(
            integration_id=str(integration.id),
            action_id="deliver",
            title="Attachment file not found in storage — dropping",
            level=LogLevel.ERROR,
            data={"file_path": attachment.file_path, "gundi_id": str(attachment.gundi_id)},
        )
        return {"dropped": True, "reason": "file_not_found"}

    filename = os.path.basename(attachment.file_path) or "attachment"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    auth = _get_auth_config(integration)
    async with CmoreClient(base_url=auth.base_url, token=auth.token.get_secret_value()) as client:
        cmore_comment = CmoreComment(
            description=f"EarthRanger attachment: {filename}",
            rootMessageId=int(cmore_message_id),
            uploadType=UploadType.GENERATED,
        )
        response = await client.post_comment_with_file(
            cmore_comment,
            filename=filename,
            content=file_bytes,
            content_type=content_type,
        )
        logger.info(
            "Posted CMORE file comment (root_message_id=%s, filename=%r, %d bytes): "
            "cmore_response=%r",
            cmore_message_id,
            filename,
            len(file_bytes),
            response,
        )

    return {
        "attachment_posted": True,
        "cmore_message_id": cmore_message_id,
        "cmore_response": response,
    }
```

Wire the dispatch in `action_deliver` — insert before the graceful-drop block (currently `# Graceful drop for Attachment, TextMessage (no CMORE analogue yet).`):

```python
    if isinstance(payload, schemas.v2.Attachment):
        # ER event photos/files → CMORE media comments on the mapped event.
        return await _push_attachment_as_comment(integration, action_config, payload)
```

And update the drop comment to reflect reality:

```python
    # Graceful drop for TextMessage (no CMORE analogue yet).
```

- [ ] **Step 4: Run the tests**

Run: `pytest app/actions/tests/test_handlers.py -v`
Expected: all PASS, including the four new attachment tests; no other deliver tests broken.

- [ ] **Step 5: Update docs**

In `docs/index.md`, find the section describing what the deliver action supports (Observations / Events / EventUpdates) and add a line:

```markdown
- **Attachments** — files attached to ER events (photos, documents) are posted to
  CMORE as media comments on the mapped event (multipart `POST /comment`).
  Requires the `BUCKET_NAME` env var and read access to Gundi's attachments bucket.
```

- [ ] **Step 6: Run the full suite and commit**

Run: `pytest`
Expected: PASS.

```bash
git add app/actions/handlers.py app/actions/tests/test_handlers.py docs/index.md
git commit -m "feat: deliver Gundi attachments to CMORE as media comments"
```

---

### Task 4: Deployment checklist (manual, no code)

These are operator steps, recorded here so the rollout isn't blocked on rediscovering them:

- [ ] Set `BUCKET_NAME` on the CMORE runner's deployment (Cloud Run env) to the Gundi attachments bucket for that environment — same value the classic dispatchers use (check the ER dispatcher's Cloud Run config in the same GCP project; default in `cdip_connector` is `cdip-dev-cameratrap` for dev).
- [ ] Grant the runner's service account `roles/storage.objectViewer` on that bucket.
- [ ] Confirm the PubSub subscription driving the deliver action has a retry policy with backoff and a dead-letter topic — the new handler deliberately raises to trigger redelivery when an attachment arrives before its parent event; without a DLQ a poison message would retry indefinitely.
- [ ] End-to-end check (after the companion ER-provider plan is deployed): attach a photo to an ER event on the connected site, wait for the pull cycle, confirm the photo appears as a media comment on the CMORE event.

---

## Self-Review

- **Spec coverage:** multipart CMORE upload (Task 1), file retrieval from Gundi storage (Task 2), routing/dispatch + parent-event mapping + race handling (Task 3), rollout (Task 4). The provider side is explicitly a companion plan.
- **Placeholder scan:** all steps carry runnable code/commands; no TBDs.
- **Type consistency:** `post_comment_with_file(comment: CmoreComment, filename: str, content: bytes, content_type: str)` is identical in Task 1's implementation, Task 1's tests, and Task 3's call site; `download_attachment(file_path: str) -> Optional[bytes]` matches between Task 2 and Task 3; state key `cmore_message_id` matches what `_push_event` writes today.
