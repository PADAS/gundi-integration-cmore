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


@pytest.mark.asyncio
async def test_get_tags_uses_long_per_request_timeout():
    # /v2/tags/getfull is the one heavy endpoint: production catalogs (DFFE)
    # take ~25s server-side before the first byte. The long timeout applies
    # per-request so delivery POSTs keep the fast client default.
    from app.datasource.client import TAGS_TIMEOUT

    client = CmoreClient(base_url="https://cmore.test", token="abc")
    client._client.get = AsyncMock(return_value=_ok_response([]))

    await client.get_tags()

    _, kwargs = client._client.get.await_args
    assert kwargs["timeout"] == TAGS_TIMEOUT
    assert TAGS_TIMEOUT >= 120.0
    await client._client.aclose()


@pytest.mark.asyncio
async def test_get_tags_timeout_respects_larger_client_timeout():
    # `validate --timeout 300` must not be silently capped by TAGS_TIMEOUT.
    client = CmoreClient(base_url="https://cmore.test", token="abc", timeout=300.0)
    client._client.get = AsyncMock(return_value=_ok_response([]))

    await client.get_tags()

    _, kwargs = client._client.get.await_args
    assert kwargs["timeout"] == 300.0
    await client._client.aclose()


@pytest.mark.asyncio
async def test_post_event_once_does_not_retry():
    # The validate CLI's --probe-event promises exactly one visible test
    # event; a retried non-idempotent POST could create up to five.
    from app.datasource.schemas import CmoreEvent

    client = CmoreClient(base_url="https://cmore.test", token="abc")
    request = httpx.Request("POST", "https://cmore.test/v2/messages/events")
    client._client.post = AsyncMock(
        return_value=httpx.Response(500, request=request)
    )

    with pytest.raises(httpx.HTTPStatusError):
        await client.post_event_once(CmoreEvent(description="probe"))

    assert client._client.post.await_count == 1
    await client._client.aclose()
