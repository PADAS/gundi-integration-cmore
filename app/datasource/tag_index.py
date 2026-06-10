"""Cached lookup of CMORE tags + fields by name.

CMORE's tag schema is per-instance (operators define tag domains, tags, and
fields in the CMORE admin UI). Action runners need to map Gundi event_type and
event_details keys to CMORE tagId / fieldId values at delivery time.

Resolving names on every event would mean a `get_tags()` call per event — too
expensive. Instead, build a flat index once per process per CMORE base_url and
reuse it. Process restart refreshes the cache.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .client import CmoreClient

logger = logging.getLogger(__name__)


@dataclass
class FieldInfo:
    id: int
    name: str
    data_type: str
    allow_multiple: bool = False
    lookups: List[dict] = field(default_factory=list)


@dataclass
class TagInfo:
    id: int
    name: str
    domain: str
    type_limiter: str
    fields: Dict[str, FieldInfo] = field(default_factory=dict)

    def field_by_name(self, field_name: str) -> Optional[FieldInfo]:
        return self.fields.get(field_name)


def _build_index(raw_response: list) -> Dict[str, TagInfo]:
    """Flatten CMORE's get_tags() response into {tag_name: TagInfo}.

    The response is `[TagDomain, ...]`; each domain has a list of tags; each
    tag has a list of fields. We flatten across domains and index by tag name.
    Logs a warning if tag names collide across domains — last-wins.
    """
    index: Dict[str, TagInfo] = {}
    for domain in raw_response or []:
        domain_name = domain.get("name", "")
        for tag in domain.get("tags", []) or []:
            tag_name = tag.get("name")
            if not tag_name:
                continue
            fields: Dict[str, FieldInfo] = {}
            for f in tag.get("fields", []) or []:
                f_name = f.get("name")
                if not f_name:
                    continue
                fields[f_name] = FieldInfo(
                    id=f["id"],
                    name=f_name,
                    data_type=f.get("dataType", "String"),
                    allow_multiple=bool(f.get("allowMultipleValues", False)),
                    lookups=f.get("lookups", []) or [],
                )
            tag_info = TagInfo(
                id=tag["id"],
                name=tag_name,
                domain=domain_name,
                type_limiter=tag.get("typeLimiter", ""),
                fields=fields,
            )
            if tag_name in index:
                logger.warning(
                    "CMORE tag name collision: %r appears in both domain %r "
                    "and %r. Last one wins.",
                    tag_name,
                    index[tag_name].domain,
                    domain_name,
                )
            index[tag_name] = tag_info
    return index


class TagIndex:
    """Lazy, per-(base_url, integration_id) cache of the CMORE tag schema.

    CMORE scopes tag visibility by ShareGroup, which is bound to the token
    on a per-integration basis. Two Gundi integrations pointing at the same
    CMORE instance with different tokens see different tag sets — so the
    cache MUST be keyed by integration_id too, not just base_url, otherwise
    one integration's empty view poisons the other's resolution.
    """

    def __init__(self) -> None:
        # Key: (base_url, integration_id) → {tag_name: TagInfo}
        self._cache: Dict[tuple, Dict[str, TagInfo]] = {}
        self._lock = asyncio.Lock()

    async def get(
        self,
        client: CmoreClient,
        base_url: str,
        integration_id: str,
        tag_name: str,
    ) -> Optional[TagInfo]:
        """Resolve a tag by name for the given integration's CMORE view."""
        index = await self._ensure_loaded(client, base_url, integration_id)
        return index.get(tag_name)

    async def _ensure_loaded(
        self, client: CmoreClient, base_url: str, integration_id: str
    ) -> Dict[str, TagInfo]:
        key = (base_url, integration_id)
        if key in self._cache:
            return self._cache[key]
        async with self._lock:
            # Double-check after acquiring the lock — another coroutine may
            # have populated while we were waiting.
            if key in self._cache:
                return self._cache[key]
            raw = await client.get_tags()
            index = _build_index(raw)
            logger.info(
                "Built CMORE tag index for %s (integration=%s): "
                "%d tags across all domains",
                base_url,
                integration_id,
                len(index),
            )
            self._cache[key] = index
            return index

    def _reset(self) -> None:
        """Test helper — drop the cache."""
        self._cache.clear()


# Module-level singleton used by handlers.
tag_index = TagIndex()
