"""cmore's state manager: the template's IntegrationStateManager plus an
optional expiry on set_state (deliver expires per-event CMORE message-id
mappings). Lives here so app/services/state.py stays identical to the template."""

import json

import pytest


@pytest.mark.asyncio
async def test_set_state_passes_ttl_to_redis(mocker, mock_redis, integration_v2, mock_integration_state):
    """When ttl_seconds is provided, the underlying Redis SET uses an EX expiry."""
    from app.actions.state import CmoreStateManager

    mocker.patch("app.services.state.redis", mock_redis)
    state_manager = CmoreStateManager()
    integration_id = str(integration_v2.id)
    source_id = "er-event-uuid"

    await state_manager.set_state(
        integration_id=integration_id,
        action_id="deliver",
        source_id=source_id,
        state=mock_integration_state,
        ttl_seconds=7_776_000,  # 90 days
    )

    mock_redis.Redis.return_value.set.assert_called_once_with(
        f"integration_state.{integration_id}.deliver.{source_id}",
        json.dumps(mock_integration_state, default=str),
        ex=7_776_000,
    )


@pytest.mark.asyncio
async def test_set_state_without_ttl_behaves_like_the_template(mocker, mock_redis, integration_v2, mock_integration_state):
    from app.actions.state import CmoreStateManager

    mocker.patch("app.services.state.redis", mock_redis)
    state_manager = CmoreStateManager()
    integration_id = str(integration_v2.id)

    await state_manager.set_state(
        integration_id=integration_id, action_id="deliver", state=mock_integration_state,
    )

    mock_redis.Redis.return_value.set.assert_called_once_with(
        f"integration_state.{integration_id}.deliver.no-source",
        json.dumps(mock_integration_state, default=str),
    )


@pytest.mark.asyncio
async def test_set_state_with_ttl_is_a_no_op_on_the_ephemeral_path(mocker, mock_redis, integration_v2, mock_integration_state):
    """A portal draft run must not write state, with or without an expiry."""
    from app.actions.state import CmoreStateManager
    from app.services.activity_logger import ephemeral_run

    mocker.patch("app.services.state.redis", mock_redis)
    state_manager = CmoreStateManager()
    token = ephemeral_run.set(True)
    try:
        await state_manager.set_state(
            integration_id=str(integration_v2.id), action_id="deliver",
            state=mock_integration_state, ttl_seconds=60,
        )
    finally:
        ephemeral_run.reset(token)

    mock_redis.Redis.return_value.set.assert_not_called()


@pytest.mark.asyncio
async def test_set_state_with_ttl_writes_the_key_and_value_the_template_writes(mocker, mock_redis, integration_v2, mock_integration_state):
    """The TTL path re-implements the template's write; if the template ever
    changes its key layout or serializer, the mapping deliver writes here must
    still be the one get_state reads."""
    from app.actions.state import CmoreStateManager
    from app.services.state import IntegrationStateManager

    mocker.patch("app.services.state.redis", mock_redis)
    redis_set = mock_redis.Redis.return_value.set
    args = dict(integration_id=str(integration_v2.id), action_id="deliver", source_id="evt-1", state=mock_integration_state)

    await IntegrationStateManager().set_state(**args)
    template_call = redis_set.call_args
    redis_set.reset_mock()
    await CmoreStateManager().set_state(**args, ttl_seconds=60)
    cmore_call = redis_set.call_args

    assert cmore_call.args == template_call.args
    assert cmore_call.kwargs == {"ex": 60}
