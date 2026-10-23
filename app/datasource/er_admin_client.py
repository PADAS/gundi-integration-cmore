"""EarthRanger admin client: categories, event types (v2) and choices.

Used by the CMORE runner to create EarthRanger event types from CMORE tags.
Plain httpx with plain arguments: no Gundi models, no template imports, so
it can be lifted into a CLI. Raises ``httpx.HTTPStatusError`` on 4xx/5xx
(after retrying transient failures) so the runner classifies auth and
connectivity problems the way it does for the CMORE client.

Endpoints (``site_root`` = ``scheme://host``):

* ``GET  /api/v1.0/user/me``
* ``GET|POST /api/v1.0/activity/events/categories/``
* ``GET  /api/v2.0/activity/eventtypes/?include_schema=true`` (paginated)
* ``POST /api/v2.0/activity/eventtypes/``, ``PATCH .../<value>/``
* ``GET  /api/v1.0/choices/?model=activity.event&field=<field>``
* ``POST /api/v1.0/choices/``, ``PATCH /api/v1.0/choices/<id>/``
* ``POST <site>/oauth2/token`` (password grant, client ``das_web_client``)
"""

import uuid
from typing import List, Optional

import backoff
import httpx

DEFAULT_TIMEOUT = 30.0
OAUTH_CLIENT_ID = "das_web_client"
CHOICE_MODEL = "activity.event"
_MAX_TRIES = 5  # module-level so tests can shrink it

_V1 = "/api/v1.0"
_V2 = "/api/v2.0"


class ErAdminAuthError(Exception):
    """The password grant was rejected (bad username/password or client)."""


def _giveup_on_client_error(exc: Exception) -> bool:
    """Don't retry 4xx (except 408/429): they won't succeed on retry."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and 400 <= exc.response.status_code < 500
        and exc.response.status_code not in (408, 429)
    )


retry_transient = backoff.on_exception(
    backoff.expo, httpx.HTTPError, max_tries=lambda: _MAX_TRIES, giveup=_giveup_on_client_error
)


def _data(response: httpx.Response):
    """EarthRanger wraps every body in ``{"data": ...}``."""
    body = response.json() if response.content else {}
    return body.get("data", body) if isinstance(body, dict) else body


def _never_seen_field(response: httpx.Response) -> bool:
    """EarthRanger validates ``field=`` against values already in the DB and
    answers 400 for a never-seen name; that means "no records yet"."""
    return response.status_code == 400 and "is not one of the available choices" in response.text


class ErAdminClient:
    def __init__(
        self,
        site_root: str,
        *,
        token: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        if not token and not (username and password):
            raise ValueError("ErAdminClient needs a token or a username and password")
        self.site_root = site_root.rstrip("/")
        self._token = token
        self._username = username
        self._password = password
        self._client = httpx.AsyncClient(base_url=self.site_root, timeout=timeout, transport=transport)

    async def __aenter__(self) -> "ErAdminClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.__aexit__(*exc)

    # ----- auth -----

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        response = await self._client.post(
            "/oauth2/token",
            data={
                "grant_type": "password",
                "client_id": OAUTH_CLIENT_ID,
                "username": self._username,
                "password": self._password,
            },
        )
        if response.status_code >= 400:
            raise ErAdminAuthError(f"EarthRanger rejected the password grant (HTTP {response.status_code})")
        token = (response.json() or {}).get("access_token")
        if not token:
            raise ErAdminAuthError("EarthRanger's token response carried no access_token")
        self._token = token
        return token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._ensure_token()}"}

    @retry_transient
    async def _request(self, method: str, path: str, *, params=None, json=None) -> httpx.Response:
        response = await self._client.request(method, path, params=params, json=json, headers=await self._headers())
        response.raise_for_status()
        return response

    async def _get_all(self, path: str, params: dict) -> List[dict]:
        """Follow ``data.next`` until null; a non-paginated list is returned as is."""
        items: List[dict] = []
        response = await self._request("GET", path, params=params)
        while True:
            data = _data(response)
            if isinstance(data, list):
                return items + data
            items.extend(data.get("results") or [])
            next_url = data.get("next")
            if not next_url:
                return items
            response = await self._request("GET", next_url)

    # ----- reads -----

    async def get_me(self) -> dict:
        return _data(await self._request("GET", f"{_V1}/user/me"))

    async def get_event_categories(self) -> List[dict]:
        return await self._get_all(f"{_V1}/activity/events/categories/", {})

    async def get_event_types(self) -> List[dict]:
        return await self._get_all(f"{_V2}/activity/eventtypes/", {"include_schema": "true"})

    async def get_choices(self, field: str) -> List[dict]:
        try:
            return await self._get_all(f"{_V1}/choices/", {"model": CHOICE_MODEL, "field": field})
        except httpx.HTTPStatusError as exc:
            if _never_seen_field(exc.response):
                return []
            raise

    # ----- permission probes -----
    #
    # user/me never lists the choices app (EarthRanger filters every app but
    # "activity" out of its permissions dict), so the only way to learn
    # whether the user may write choices without writing one is to ask the
    # endpoint: DRF checks model permissions before deserialising, so an
    # empty body answers 403 when the permission is missing and a validation
    # 400 (or a 404 for a random id) when it is held.

    async def _probe(self, method: str, path: str) -> bool:
        response = await self._client.request(method, path, json={}, headers=await self._headers())
        return response.status_code != 403

    async def can_add_choices(self) -> bool:
        return await self._probe("POST", f"{_V1}/choices/")

    async def can_change_choices(self) -> bool:
        return await self._probe("PATCH", f"{_V1}/choices/{uuid.uuid4()}/")

    # ----- writes -----

    async def post_event_category(self, payload: dict) -> dict:
        return _data(await self._request("POST", f"{_V1}/activity/events/categories/", json=payload))

    async def post_event_type(self, payload: dict) -> dict:
        return _data(await self._request("POST", f"{_V2}/activity/eventtypes/", json=payload))

    async def patch_event_type(self, value: str, payload: dict) -> dict:
        return _data(await self._request("PATCH", f"{_V2}/activity/eventtypes/{value}/", json=payload))

    async def post_choice(self, payload: dict) -> dict:
        return _data(await self._request("POST", f"{_V1}/choices/", json=payload))

    async def patch_choice(self, choice_id: str, payload: dict) -> dict:
        return _data(await self._request("PATCH", f"{_V1}/choices/{choice_id}/", json=payload))
