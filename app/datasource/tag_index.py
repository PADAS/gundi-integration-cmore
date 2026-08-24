"""Cached lookup of CMORE tags + fields by id or name.

CMORE's tag schema is per-instance (operators define tag domains, tags, and
fields in the CMORE admin UI). Action runners need to map Gundi event_type and
event_details keys to CMORE tagId / fieldId values at delivery time.

Resolving names on every event would mean a `get_tags()` call per event — too
expensive. Instead, build a flat index once per process per CMORE base_url and
reuse it. Process restart refreshes the cache.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .client import CmoreClient

logger = logging.getLogger(__name__)

# Injection point for tests; monotonic so wall-clock changes can't expire caches.
_now = time.monotonic


@dataclass
class FieldInfo:
    id: int
    name: str
    data_type: str
    allow_multiple: bool = False
    lookups: List[dict] = field(default_factory=list)


def _resolve(ref, by_id: dict, by_name: dict):
    """Shared ID-or-name resolution: an all-digit ref matching an existing
    id wins; anything else (or a digit ref matching no id) is an exact name
    match. A tag literally *named* "8443" still resolves via the name branch
    as long as no tag *has* id 8443; if both exist, the id wins (documented
    precedence — deterministic, and the pathological case is operator error)."""
    ref = str(ref).strip()
    if ref.isdecimal() and int(ref) in by_id:
        return by_id[int(ref)]
    return by_name.get(ref)


@dataclass
class TagInfo:
    id: int
    name: str
    domain: str
    type_limiter: str
    fields_by_id: Dict[int, "FieldInfo"] = field(default_factory=dict)
    fields_by_name: Dict[str, "FieldInfo"] = field(default_factory=dict)

    @classmethod
    def build(cls, *, id, name, domain="", type_limiter="", fields=()):
        """Construct with both field views derived from one field list, so
        they can never drift apart."""
        fields = list(fields)
        return cls(
            id=id,
            name=name,
            domain=domain,
            type_limiter=type_limiter,
            fields_by_id={f.id: f for f in fields},
            fields_by_name={f.name: f for f in fields},
        )

    def resolve_field(self, ref: str) -> Optional["FieldInfo"]:
        return _resolve(ref, self.fields_by_id, self.fields_by_name)


@dataclass
class TagIndexData:
    """Both views of one CMORE tag schema fetch. by_id is complete; by_name
    is last-wins on cross-domain name collisions (warned at build time)."""

    by_id: Dict[int, TagInfo]
    by_name: Dict[str, TagInfo]

    def resolve(self, ref: str) -> Optional[TagInfo]:
        return _resolve(ref, self.by_id, self.by_name)


def _build_index(raw_response: list) -> TagIndexData:
    """Flatten CMORE's get_tags() response into a TagIndexData.

    The response is `[TagDomain, ...]`; each domain has a list of tags; each
    tag has a list of fields. Logs a warning if tag names collide across
    domains — last-wins in the name view; the id view keeps both.
    """
    by_id: Dict[int, TagInfo] = {}
    by_name: Dict[str, TagInfo] = {}
    for domain in raw_response or []:
        domain_name = domain.get("name", "")
        for tag in domain.get("tags", []) or []:
            tag_name = tag.get("name")
            if not tag_name:
                continue
            fields = [
                FieldInfo(
                    id=f["id"],
                    name=f["name"],
                    data_type=f.get("dataType", "String"),
                    allow_multiple=bool(f.get("allowMultipleValues", False)),
                    lookups=f.get("lookups", []) or [],
                )
                for f in tag.get("fields", []) or []
                if f.get("name")
            ]
            tag_info = TagInfo.build(
                id=tag["id"],
                name=tag_name,
                domain=domain_name,
                type_limiter=tag.get("typeLimiter", ""),
                fields=fields,
            )
            if tag_name in by_name:
                logger.warning(
                    "CMORE tag name collision: %r appears in both domain %r "
                    "and %r. Last one wins.",
                    tag_name,
                    by_name[tag_name].domain,
                    domain_name,
                )
            by_name[tag_name] = tag_info
            by_id[tag_info.id] = tag_info
    return TagIndexData(by_id=by_id, by_name=by_name)


class TagIndex:
    """Lazy, per-(base_url, integration_id) cache of the CMORE tag schema.

    CMORE scopes tag visibility by ShareGroup, which is bound to the token
    on a per-integration basis. Two Gundi integrations pointing at the same
    CMORE instance with different tokens see different tag sets — so the
    cache MUST be keyed by integration_id too, not just base_url, otherwise
    one integration's empty view poisons the other's resolution.
    """

    def __init__(self, ttl_seconds: Optional[float] = None) -> None:
        # ttl_seconds=None (the delivery-path default) caches for the process
        # lifetime — restart refreshes. A finite TTL is for config-time use
        # (reference actions), where "fresh within a couple of minutes" beats
        # paying the ~25s production get_tags fetch on every form interaction.
        self._ttl_seconds = ttl_seconds
        # Key: (base_url, integration_id) → (TagIndexData, fetched_at)
        self._cache: Dict[tuple, tuple] = {}
        # Per-key locks: fetches are single-flight per (base_url, integration)
        # but never serialize across keys — one cache-wide lock held across a
        # ~25s production get_tags fetch would make N integrations wait ~N×25s.
        # setdefault is atomic enough here: asyncio is single-threaded and
        # there is no await between the lookup and the insert.
        self._locks: Dict[tuple, asyncio.Lock] = {}

    async def get(
        self,
        client: CmoreClient,
        base_url: str,
        integration_id: str,
        tag_ref: str,
    ) -> Optional[TagInfo]:
        """Resolve a tag by id or name for the given integration's CMORE view."""
        index = await self._ensure_loaded(client, base_url, integration_id)
        return index.resolve(tag_ref)

    async def get_index(
        self, client: CmoreClient, base_url: str, integration_id: str
    ) -> TagIndexData:
        """The full (cached) index — for callers that enumerate tags/fields
        (reference actions) rather than resolving one ref."""
        return await self._ensure_loaded(client, base_url, integration_id)

    async def _ensure_loaded(
        self, client: CmoreClient, base_url: str, integration_id: str
    ) -> TagIndexData:
        key = (base_url, integration_id)
        cached = self._get_fresh(key)
        if cached is not None:
            return cached
        async with self._locks.setdefault(key, asyncio.Lock()):
            # Double-check after acquiring the lock — another coroutine may
            # have populated while we were waiting.
            cached = self._get_fresh(key)
            if cached is not None:
                return cached
            raw = await client.get_tags()
            index = _build_index(raw)
            logger.info(
                "Built CMORE tag index for %s (integration=%s): "
                "%d tags across all domains",
                base_url,
                integration_id,
                len(index.by_id),
            )
            self._cache[key] = (index, _now())
            return index

    def _get_fresh(self, key) -> Optional[TagIndexData]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        index, fetched_at = entry
        if self._ttl_seconds is not None and _now() - fetched_at > self._ttl_seconds:
            return None
        return index

    def _reset(self) -> None:
        """Test helper — drop the cache."""
        self._cache.clear()
        self._locks.clear()


# Module-level singleton used by handlers.
tag_index = TagIndex()
