"""cmore's state manager: the template's IntegrationStateManager plus an
optional expiry on set_state. Kept here so app/services/state.py stays
identical to upstream (upstreaming the parameter is a recorded follow-up)."""

import json
from typing import Optional

import stamina

from app.services.retry_policies import REDIS_RETRY
from app.services.state import IntegrationStateManager, _skip_on_ephemeral_run


class CmoreStateManager(IntegrationStateManager):

    async def set_state(
        self, integration_id: str, action_id: str, state: dict, source_id: str = "no-source",
        ttl_seconds: Optional[int] = None,
    ):
        """Persist state. Pass ``ttl_seconds`` to give the key a Redis expiry:
        deliver uses it for the per-event CMORE message-id mappings, which
        would otherwise grow the keyspace indefinitely."""
        if ttl_seconds is None:
            return await super().set_state(integration_id, action_id, state, source_id)
        if _skip_on_ephemeral_run("set_state", integration_id, action_id):
            return
        key = f"integration_state.{integration_id}.{action_id}.{source_id}"
        value = json.dumps(state, default=str)
        async for attempt in stamina.retry_context(**REDIS_RETRY):
            with attempt:
                await self.db_client.set(key, value, ex=ttl_seconds)
