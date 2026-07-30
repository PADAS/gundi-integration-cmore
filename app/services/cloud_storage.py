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
