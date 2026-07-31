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
