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
