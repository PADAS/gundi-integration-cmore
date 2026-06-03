import json
import logging
from typing import List, Optional

import backoff
import httpx

from .schemas import (
    CmoreEvent,
    CmoreGatewayMapping,
    CmoreGNode,
    CmoreLocation,
    CmoreProperty,
    CmoreVirtualClientRequest,
)

logger = logging.getLogger(__name__)


class CmoreClient:
    def __init__(self, base_url: str, token: Optional[str] = None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
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
        return response.json()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def post_locations(self, locations: List[CmoreLocation]) -> dict:
        payload = [json.loads(loc.json(exclude_none=True)) for loc in locations]
        response = await self._client.post("/v2/clients/virtual/locations", json=payload)
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def post_properties(self, properties: List[CmoreProperty]) -> dict:
        payload = [prop.dict() for prop in properties]
        response = await self._client.post("/v2/clients/virtual/properties", json=payload)
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def post_event(self, event: CmoreEvent) -> dict:
        payload = json.loads(event.json(exclude_none=True))
        response = await self._client.post("/v2/messages/events", json=payload)
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def get_tags(self) -> list:
        response = await self._client.get("/v2/tags/getfull")
        response.raise_for_status()
        return response.json()

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def create_gnodes(self, clients: List[CmoreVirtualClientRequest]) -> List[CmoreGNode]:
        payload = [c.dict(exclude_none=True) for c in clients]
        response = await self._client.post("/v2/clients/virtual", json=payload)
        response.raise_for_status()
        return [CmoreGNode(**item) for item in response.json()]

    @backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5)
    async def get_gateway_mapping(self) -> List[CmoreGatewayMapping]:
        """Fetch existing trackSource/trackNo → clientId mappings for this token's application client."""
        response = await self._client.get("/v2/clients/virtual/gateway_mapping")
        response.raise_for_status()
        return [CmoreGatewayMapping(**item) for item in response.json()]
