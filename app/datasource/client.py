import json
import logging
from typing import List, Optional

import backoff
import httpx

from .schemas import (
    CmoreComment,
    CmoreEvent,
    CmoreGatewayMapping,
    CmoreGNode,
    CmoreLocation,
    CmoreProperty,
    CmoreVirtualClientRequest,
)

logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT = 10.0


def _giveup_on_client_error(exc: Exception) -> bool:
    """Don't retry on 4xx status errors — they won't succeed on retry."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and 400 <= exc.response.status_code < 500
        and exc.response.status_code not in (408, 429)
    )


# Retry transient transport errors and 5xx; give up immediately on 4xx.
retry_transient = backoff.on_exception(
    backoff.expo, httpx.HTTPError, max_tries=5, giveup=_giveup_on_client_error
)


def _safe_json(response: httpx.Response, default):
    """Return response.json() unless the body is empty.

    C-more's write endpoints (POST /properties, POST /locations, etc.) return
    2xx with an empty body on success. Calling .json() on that raises
    JSONDecodeError. Use this helper to return a sensible default instead.
    """
    if not response.content:
        return default
    return response.json()


class CmoreClient:
    def __init__(self, base_url: str, token: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        headers = {"Content-Type": "application/json"}
        if token:
            # Tolerate tokens that already include the "Token " prefix.
            raw = token.strip()
            if raw.lower().startswith("token "):
                raw = raw[6:].strip()
            headers["Authorization"] = f"Token {raw}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    @retry_transient
    async def login(
        self,
        username: str,
        password: str,
        client_type: str = "SoftwareClient",
        unique_id: str = "gundi-cli",
    ) -> dict:
        """POST /api/token/login — exchange credentials for a security token + user info."""
        response = await self._client.post(
            "/token/login",
            data={
                "username": username,
                "password": password,
                "clientType": client_type,
                "uniqueId": unique_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return _safe_json(response, {})

    @retry_transient
    async def post_locations(self, locations: List[CmoreLocation]) -> dict:
        payload = [json.loads(loc.json(exclude_none=True)) for loc in locations]
        response = await self._client.post("/v2/clients/virtual/locations", json=payload)
        response.raise_for_status()
        return _safe_json(response, {})

    @retry_transient
    async def post_properties(self, properties: List[CmoreProperty]) -> dict:
        payload = [prop.dict() for prop in properties]
        response = await self._client.post("/v2/clients/virtual/properties", json=payload)
        response.raise_for_status()
        return _safe_json(response, {})

    @retry_transient
    async def post_event(self, event: CmoreEvent) -> dict:
        payload = json.loads(event.json(exclude_none=True))
        response = await self._client.post("/v2/messages/events", json=payload)
        response.raise_for_status()
        return _safe_json(response, {})

    @retry_transient
    async def post_comment(self, comment: CmoreComment) -> dict:
        """Attach a text comment to an existing CMORE event.

        ``rootMessageId`` on the request body is the CMORE messageId returned
        by a prior ``post_event``; the comment is hung off that event. Note
        that the CMORE comment endpoint sits at ``/comment`` (no v2 prefix),
        unlike most of the rest of this client.
        """
        payload = json.loads(comment.json(exclude_none=True))
        response = await self._client.post("/comment", json=payload)
        response.raise_for_status()
        return _safe_json(response, {})

    @retry_transient
    async def get_tags(self) -> list:
        response = await self._client.get("/v2/tags/getfull")
        response.raise_for_status()
        return _safe_json(response, [])

    @retry_transient
    async def get_classification_tree(self) -> list:
        """Fetch the valid battleDimension/force/type/role combinations for this instance."""
        response = await self._client.get("/v2/clients/get_classification_tree")
        response.raise_for_status()
        return _safe_json(response, [])

    @retry_transient
    async def create_gnodes(self, clients: List[CmoreVirtualClientRequest]) -> List[CmoreGNode]:
        payload = [c.dict(exclude_none=True) for c in clients]
        response = await self._client.post("/v2/clients/virtual", json=payload)
        response.raise_for_status()
        return [CmoreGNode(**item) for item in _safe_json(response, [])]

    @retry_transient
    async def get_gateway_mapping(self) -> List[CmoreGatewayMapping]:
        """Fetch existing trackSource/trackNo → clientId mappings for this token's application client."""
        response = await self._client.get("/v2/clients/virtual/gateway_mapping")
        response.raise_for_status()
        return [CmoreGatewayMapping(**item) for item in _safe_json(response, [])]
