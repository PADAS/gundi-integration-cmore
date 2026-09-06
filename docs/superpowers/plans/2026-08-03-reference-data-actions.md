# Reference Data Actions (Phase 0) Implementation Plan

> **Superseded (2026-09):** the `REGISTER_REFERENCE_ACTIONS` flag this plan introduces was removed in the
> September 2026 template sync (PR #35). The template always registers reference actions with type `reference`
> (upstream PR #101); nothing reads the flag any more. The rest of this plan is kept as history of how the
> reference actions were built. Do not re-add the flag or the registration gate in `self_registration.py`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase 0 (this-repo, no-platform-dependency) scope of the approved spec `docs/superpowers/specs/2026-07-31-reference-data-config-ui-design.md`: the reference-action contract, four CMORE reference actions, inert `gundi:reference` ui_schema annotations on `DeliverConfig`, and tests.

**Architecture:** Reference actions are ordinary `action_`-prefixed handlers whose config model subclasses a new `ReferenceActionConfiguration` marker — the config model IS the query, populated entirely from `config_overrides` (stateless). They return a standardized `ReferenceDataResponse` options envelope. `DeliverConfig.ui_schema()` gains `gundi:reference` annotations that today's portal ignores (they never set `ui:widget`), so everything ships inert. Registration maps them to a new `"reference"` action type behind a default-off settings flag so registering against today's Gundi API (which doesn't know the type yet) is never broken.

**Tech Stack:** Python 3.10, FastAPI, Pydantic v1, pytest + pytest-asyncio + pytest-mock, existing `CmoreClient` (httpx) in `app/datasource/`.

## Global Constraints

- Pydantic models for all new data structures — no `@dataclass` (repo convention; the existing `tag_index.py` dataclasses are grandfathered, do not convert them).
- All CMORE HTTP calls go through `CmoreClient` in `app/datasource/` (it already carries `@retry_transient` backoff); handlers never call httpx directly.
- Reference handlers must NOT use the module-level `tag_index` cache — config-time fetches must be fresh; the cache is per-process with no TTL and exists for per-event delivery cost, not config UX.
- The `gundi:reference` annotation must never set `ui:widget` (forward-compat contract from the spec §2).
- `REGISTER_REFERENCE_ACTIONS` defaults to `False` — Phase 0 must not change what gets registered in Gundi unless explicitly enabled.
- Run tests with plain `pytest <path> -v` from the repo root.
- Commit after every green task; messages follow conventional commits (`feat:`/`test:` style used in this repo).

## Reference: external data shapes (from the live CMORE API)

`CmoreClient.get_tags()` → list of tag *domains*, each with nested tags and fields (see `8443.get-tags.json`):

```json
[{"id": 8, "name": "Wildlife", "tags": [
  {"id": 20, "name": "Evidence of Poacher", "typeLimiter": "Incident", "fields": [
    {"id": 260, "name": "Reported By", "dataType": "String", "allowMultipleValues": false, "lookups": []},
    {"id": 261, "name": "Evidence Type", "dataType": "Lookup", "allowMultipleValues": true,
     "lookups": [{"id": 1667, "value": "Abalone Harvesting", "order": 1}]}
  ]}
]}]
```

`CmoreClient.get_classification_tree()` → nested battleDimension/forces/types/roles:

```json
[{"battleDimension": "AIR", "forces": [
  {"force": "CIVIL", "types": [
    {"type": "FIXED_WING", "roles": ["UNKNOWN", "LIGHT", "MICROLIGHT"]}
  ]}
]}]
```

`app/datasource/tag_index.py` already has `_build_index(raw) -> Dict[str, TagInfo]` that flattens the get_tags response (`TagInfo.name/.domain/.fields`; `FieldInfo.name/.data_type/.lookups`). Reference handlers reuse `_build_index` for parsing but always call `get_tags()` fresh.

---

### Task 1: Reference-action contract types

**Files:**
- Modify: `app/actions/core.py` (add three classes after `GenericActionConfiguration`, ~line 61)
- Modify: `app/actions/__init__.py` (export the new names)
- Test: `app/actions/tests/test_reference_actions.py` (create)

**Interfaces:**
- Produces: `ReferenceActionConfiguration(ActionConfiguration)` marker base; `ReferenceOption(value, label=None, description=None, group=None)`; `ReferenceDataResponse(options: List[ReferenceOption], cache_ttl_seconds: int = 300, truncated: bool = False)`. All later tasks import these from `app.actions.core`.

- [ ] **Step 1: Write the failing test**

Create `app/actions/tests/test_reference_actions.py`:

```python
"""Tests for the reference-action contract and the four CMORE reference actions."""

import pytest


def test_reference_contract_types():
    from app.actions.core import (
        ActionConfiguration,
        ReferenceActionConfiguration,
        ReferenceDataResponse,
        ReferenceOption,
    )

    assert issubclass(ReferenceActionConfiguration, ActionConfiguration)

    response = ReferenceDataResponse(
        options=[ReferenceOption(value="Poacher Sighting", group="Wildlife")]
    )
    data = response.dict()
    assert data["options"][0]["value"] == "Poacher Sighting"
    assert data["options"][0]["label"] is None
    assert data["cache_ttl_seconds"] == 300
    assert data["truncated"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/actions/tests/test_reference_actions.py::test_reference_contract_types -v`
Expected: FAIL with `ImportError: cannot import name 'ReferenceActionConfiguration'`

- [ ] **Step 3: Write minimal implementation**

In `app/actions/core.py`, the imports already include `from typing import Optional` and `from pydantic import BaseModel, Field`. Change the typing import to `from typing import List, Optional`, then add after `class GenericActionConfiguration(ActionConfiguration):`:

```python
class ReferenceActionConfiguration(ActionConfiguration):
    """Marker base for reference-data actions: the config model IS the query.

    Reference actions are stateless — they read the integration's auth config
    but store no configuration of their own; callers (the Gundi portal)
    supply query params via config_overrides. They return a
    ReferenceDataResponse dict. See
    docs/superpowers/specs/2026-07-31-reference-data-config-ui-design.md.
    """


class ReferenceOption(BaseModel):
    value: str
    label: Optional[str] = None        # portal defaults label to value
    description: Optional[str] = None  # tooltip / help text
    group: Optional[str] = None        # optional grouping for long lists


class ReferenceDataResponse(BaseModel):
    options: List[ReferenceOption]
    cache_ttl_seconds: int = 300       # portal-side cache hint
    truncated: bool = False            # true if the list was capped
```

In `app/actions/__init__.py`, add the three names to the existing `from .core import ...` (or `from app.actions.core import ...`) re-export list, matching however `AuthActionConfiguration` is currently exported there.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest app/actions/tests/test_reference_actions.py::test_reference_contract_types -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/core.py app/actions/__init__.py app/actions/tests/test_reference_actions.py
git commit -m "feat: reference-action contract types (marker config + options envelope)"
```

---

### Task 2: Registration support behind a default-off flag

**Files:**
- Modify: `app/services/core.py` (add `REFERENCE = "reference"` to `ActionTypeEnum`, lines 4-8)
- Modify: `app/settings/integration.py` (add `REGISTER_REFERENCE_ACTIONS` env flag; follow the `ATTACHMENTS_BUCKET_NAME = env.str("BUCKET_NAME", None)` pattern already in that file, and mirror however that name is re-exported through `app/settings/__init__.py`)
- Modify: `app/services/self_registration.py` (skip/type-map reference actions)
- Test: `app/services/tests/test_self_registration.py` (append two tests)

**Interfaces:**
- Consumes: `ReferenceActionConfiguration` from Task 1.
- Produces: `ActionTypeEnum.REFERENCE.value == "reference"`; setting `REGISTER_REFERENCE_ACTIONS: bool = False`; registration behavior: flag off → reference actions excluded from the registration payload; flag on → included with `"type": "reference"`.

- [ ] **Step 1: Write the failing tests**

Append to `app/services/tests/test_self_registration.py` (reuse the existing `mock_gundi_client_v2` fixture and patching style of `test_register_integration_with_executable_action`):

```python
def _mock_reference_action_handlers():
    from app.actions.core import ReferenceActionConfiguration

    class MockListThingsQuery(ReferenceActionConfiguration):
        parent: str

    async def action_list_things(integration, action_config: MockListThingsQuery):
        return {"options": []}

    return {"list_things": (action_list_things, MockListThingsQuery, None)}


@pytest.mark.asyncio
async def test_reference_actions_excluded_from_registration_by_default(
    mocker,
    mock_gundi_client_v2,
    mock_get_webhook_handler_for_fixed_json_payload,
):
    mocker.patch("app.services.self_registration.INTEGRATION_TYPE_SLUG", "x_tracker")
    mocker.patch(
        "app.services.self_registration.action_handlers",
        _mock_reference_action_handlers(),
    )
    mocker.patch(
        "app.services.self_registration.get_webhook_handler",
        mock_get_webhook_handler_for_fixed_json_payload,
    )
    await register_integration_in_gundi(gundi_client=mock_gundi_client_v2)
    data = mock_gundi_client_v2.register_integration_type.call_args.args[0]
    assert data["actions"] == []


@pytest.mark.asyncio
async def test_reference_actions_registered_with_reference_type_when_enabled(
    mocker,
    mock_gundi_client_v2,
    mock_get_webhook_handler_for_fixed_json_payload,
):
    mocker.patch("app.services.self_registration.INTEGRATION_TYPE_SLUG", "x_tracker")
    mocker.patch("app.services.self_registration.REGISTER_REFERENCE_ACTIONS", True)
    mocker.patch(
        "app.services.self_registration.action_handlers",
        _mock_reference_action_handlers(),
    )
    mocker.patch(
        "app.services.self_registration.get_webhook_handler",
        mock_get_webhook_handler_for_fixed_json_payload,
    )
    await register_integration_in_gundi(gundi_client=mock_gundi_client_v2)
    data = mock_gundi_client_v2.register_integration_type.call_args.args[0]
    assert len(data["actions"]) == 1
    action = data["actions"][0]
    assert action["value"] == "list_things"
    assert action["type"] == "reference"
    assert action["is_periodic_action"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/services/tests/test_self_registration.py -k reference -v`
Expected: both FAIL — the first because the action registers with type `"generic"` (so `data["actions"] != []`), the second with `AttributeError` patching the missing `REGISTER_REFERENCE_ACTIONS`.

- [ ] **Step 3: Implement**

In `app/services/core.py`:

```python
class ActionTypeEnum(str, Enum):
    AUTHENTICATION = "auth"
    PULL_DATA = "pull"
    PUSH_DATA = "push"
    GENERIC = "generic"
    REFERENCE = "reference"
```

In `app/settings/integration.py` (env import already present in that file):

```python
# Phase 0 of the reference-data design (docs/superpowers/specs/
# 2026-07-31-reference-data-config-ui-design.md): reference actions are only
# registered in Gundi once the platform accepts the "reference" action type.
# Until then this stays off so self-registration never sends a type the API
# would reject.
REGISTER_REFERENCE_ACTIONS = env.bool("REGISTER_REFERENCE_ACTIONS", False)
```

In `app/services/self_registration.py`:

1. Extend the `from app.actions import (...)` block with `ReferenceActionConfiguration` and the `from app.settings import (...)` block with `REGISTER_REFERENCE_ACTIONS`.
2. Inside the `for action_id, value in action_handlers.items():` loop, directly after the `InternalActionConfiguration` skip block, add:

```python
        if issubclass(config_model, ReferenceActionConfiguration) and not REGISTER_REFERENCE_ACTIONS:
            logger.info(
                f"Skipping reference action '{action_id}' "
                "(REGISTER_REFERENCE_ACTIONS is off until the platform supports the 'reference' type)."
            )
            continue
```

3. In the action-type chain, add a first branch (before the `AuthActionConfiguration` check):

```python
        if issubclass(config_model, ReferenceActionConfiguration):
            action_type = ActionTypeEnum.REFERENCE.value
        elif issubclass(config_model, AuthActionConfiguration):
```

(the existing `if issubclass(config_model, AuthActionConfiguration):` becomes `elif`).

- [ ] **Step 4: Run the tests**

Run: `pytest app/services/tests/test_self_registration.py -v`
Expected: the two new tests PASS and all pre-existing registration tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/core.py app/settings/integration.py app/services/self_registration.py app/services/tests/test_self_registration.py
git commit -m "feat: register reference actions with 'reference' type behind default-off flag"
```

---

### Task 3: Stateless execution of reference actions in the action runner

**Files:**
- Modify: `app/services/action_runner.py` (the `if not action_config and not config_overrides:` guard, ~line 240)
- Test: `app/services/tests/test_action_runner.py` (append one test)

**Interfaces:**
- Consumes: `ReferenceActionConfiguration` from Task 1.
- Produces: `execute_action(integration_id, action_id=<reference action>, config_overrides=None or {})` no longer 404s for reference actions with no stored config — the query model parses from overrides/defaults alone. All four Task 4-7 actions rely on this.

Why: `execute_action` currently returns a 404 "configuration missing" when an action has no stored config AND `config_overrides` is falsy. Reference actions never have stored config, and zero-param queries (`list_tag_names`) legitimately arrive with empty overrides.

- [ ] **Step 1: Write the failing test**

Append to `app/services/tests/test_action_runner.py`, following the existing tests' fixture/patching style in that file (they patch `app.services.action_runner.action_handlers` and the config manager; mirror the arrange of the nearest passing test that runs `execute_action` end-to-end):

```python
@pytest.mark.asyncio
async def test_execute_reference_action_with_no_stored_config_and_no_overrides(
    mocker, mock_publish_event, integration_v2_no_configurations_or_defaults
):
    """A stateless reference action with an all-optional query must execute
    even when the integration stores no config for it and the caller sends
    no config_overrides (previously a 404 'configuration missing')."""
    from app.actions.core import ReferenceActionConfiguration

    class ListThingsQuery(ReferenceActionConfiguration):
        parent: str = ""

    captured = {}

    async def action_list_things(integration, action_config: ListThingsQuery):
        captured["config"] = action_config
        return {"options": [{"value": "a"}]}

    mocker.patch(
        "app.services.action_runner.action_handlers",
        {"list_things": (action_list_things, ListThingsQuery, None)},
    )
    mocker.patch("app.services.action_runner._portal", mocker.MagicMock())
    mocker.patch(
        "app.services.action_runner.config_manager.get_integration_details",
        mocker.AsyncMock(return_value=integration_v2_no_configurations_or_defaults),
    )
    mocker.patch(
        "app.services.action_runner.config_manager.get_action_configuration",
        mocker.AsyncMock(return_value=None),
    )

    result = await execute_action(
        integration_id=str(integration_v2_no_configurations_or_defaults.id),
        action_id="list_things",
    )

    assert captured["config"].parent == ""
    assert result == {"options": [{"value": "a"}]}
```

Note for the implementer: if `integration_v2_no_configurations_or_defaults` (or an equivalent "integration with no action configs" fixture) doesn't exist in `app/conftest.py`, build the integration inline with `Integration.parse_obj({... "configurations": [] ...})` using the dict shape from `app/actions/tests/test_handlers.py::_integration_dict`. The patched names above must match how `test_action_runner.py` already patches `config_manager` / `_portal` — read two neighboring tests first and copy their arrange verbatim where it differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/services/tests/test_action_runner.py -k reference -v`
Expected: FAIL — `execute_action` returns the 404 "Configuration ... is missing" error payload instead of the handler result.

- [ ] **Step 3: Implement**

In `app/services/action_runner.py`, import `ReferenceActionConfiguration` alongside the existing `PullActionConfiguration` import from `app.actions`, then change the guard:

```python
    is_reference_action = isinstance(config_model, type) and issubclass(
        config_model, ReferenceActionConfiguration
    )

    # Get the configuration needed to execute the action
    action_config = await config_manager.get_action_configuration(integration_id, action_id)
    if not action_config and not config_overrides and not is_reference_action:
```

(Reference actions are stateless: an empty `config_data` is valid input for them — required query params still fail Pydantic validation below with a 422, which is the correct signal to the portal.)

- [ ] **Step 4: Run tests**

Run: `pytest app/services/tests/test_action_runner.py -v`
Expected: new test PASSES; all existing action-runner tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/action_runner.py app/services/tests/test_action_runner.py
git commit -m "feat: allow stateless reference actions to execute without stored config"
```

---

### Task 4: `action_list_tag_names` + shared fetch helper

**Files:**
- Modify: `app/actions/configurations.py` (add `ListTagNamesQuery`)
- Modify: `app/actions/handlers.py` (add `_fetch_tag_index` helper + `action_list_tag_names`)
- Test: `app/actions/tests/test_reference_actions.py` (extend)

**Interfaces:**
- Consumes: `ReferenceActionConfiguration`, `ReferenceOption`, `ReferenceDataResponse` (Task 1); `_get_auth_config` and `CmoreClient` already in `handlers.py`; `_build_index` from `app/datasource/tag_index.py`.
- Produces: `_fetch_tag_index(integration) -> Dict[str, TagInfo]` (Tasks 5-6 reuse it); `action_list_tag_names(integration, action_config: ListTagNamesQuery) -> dict` returning a `ReferenceDataResponse`-shaped dict with one option per tag, `group` = CMORE domain name.

- [ ] **Step 1: Write the failing tests**

Add to `app/actions/tests/test_reference_actions.py`:

```python
import uuid
from unittest.mock import AsyncMock, MagicMock

RAW_TAGS = [
    {
        "id": 8,
        "name": "Wildlife",
        "tags": [
            {
                "id": 20,
                "name": "Evidence of Poacher",
                "typeLimiter": "Incident",
                "fields": [
                    {"id": 260, "name": "Reported By", "dataType": "String",
                     "allowMultipleValues": False, "lookups": []},
                    {"id": 261, "name": "Evidence Type", "dataType": "Lookup",
                     "allowMultipleValues": True,
                     "lookups": [
                         {"id": 1667, "value": "Abalone Harvesting", "order": 1},
                         {"id": 1669, "value": "Camp", "order": 3},
                     ]},
                ],
            },
            {"id": 21, "name": "Animal Sighting", "typeLimiter": "Incident", "fields": []},
        ],
    },
    {"id": 1, "name": "System", "tags": []},
]


@pytest.fixture
def integration():
    from gundi_core.schemas.v2 import Integration
    from app.actions.tests.test_handlers import _integration_dict

    return Integration.parse_obj(_integration_dict(str(uuid.uuid4())))


@pytest.fixture
def mock_cmore_client(mocker):
    """Patch CmoreClient in handlers; returns the mock instance the
    `async with CmoreClient(...) as client:` block yields."""
    from app.actions import handlers as handlers_module

    instance = MagicMock()
    instance.get_tags = AsyncMock(return_value=RAW_TAGS)
    instance.get_classification_tree = AsyncMock(return_value=[])
    client_cls = MagicMock()
    client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
    client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mocker.patch.object(handlers_module, "CmoreClient", client_cls)
    return instance


@pytest.mark.asyncio
async def test_list_tag_names_returns_all_tags_grouped_by_domain(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    result = await action_list_tag_names(integration, ListTagNamesQuery())

    values = [(o["value"], o["group"]) for o in result["options"]]
    assert values == [
        ("Animal Sighting", "Wildlife"),
        ("Evidence of Poacher", "Wildlife"),
    ]
    assert result["cache_ttl_seconds"] == 300
    mock_cmore_client.get_tags.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tag_names_propagates_upstream_errors(
    integration, mock_cmore_client
):
    """CMORE API failures must propagate (execute_action turns them into an
    error response the portal shows as 'couldn't load options') — never be
    swallowed into an empty options list."""
    import httpx
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    mock_cmore_client.get_tags = AsyncMock(
        side_effect=httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=MagicMock(status_code=502)
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        await action_list_tag_names(integration, ListTagNamesQuery())
```

(Precondition for the `integration` fixture: `_get_auth_config` needs an `auth` entry in the integration's `configurations` — `_integration_dict` in `test_handlers.py` provides one. If its auth data lacks any of `token`/`base_url`/`owner_group_id`, extend this test file's own copy of the dict rather than editing `test_handlers.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/actions/tests/test_reference_actions.py -v`
Expected: FAIL with `ImportError: cannot import name 'ListTagNamesQuery'`

- [ ] **Step 3: Implement**

In `app/actions/configurations.py`, import `ReferenceActionConfiguration` from `.core` and add (near the top, after `AuthenticateConfig`):

```python
class ListTagNamesQuery(ReferenceActionConfiguration):
    """Query model for action_list_tag_names (no parameters)."""
```

In `app/actions/handlers.py`:

1. Extend the tag_index import: `from app.datasource.tag_index import tag_index, _build_index`.
2. Extend the core import to include the contract types: `from .core import ...` — or, if handlers.py has no `.core` import today, add `from app.actions.core import ReferenceDataResponse, ReferenceOption`.
3. Extend the `.configurations` import with `ListTagNamesQuery`.
4. Add near `_get_auth_config`:

```python
async def _fetch_tag_index(integration: Integration) -> dict:
    """Fresh tag fetch for reference actions (config-time UX).

    Deliberately bypasses the module-level tag_index cache: that cache is
    per-process with no TTL, tuned for per-event delivery cost. A config
    form fetch must reflect current CMORE state.
    """
    auth = _get_auth_config(integration)
    async with CmoreClient(
        base_url=auth.base_url, token=auth.token.get_secret_value()
    ) as client:
        raw = await client.get_tags()
    return _build_index(raw)


async def action_list_tag_names(
    integration: Integration, action_config: ListTagNamesQuery
):
    """Reference action: all CMORE tag names visible to this integration."""
    index = await _fetch_tag_index(integration)
    options = [
        ReferenceOption(value=tag.name, group=tag.domain)
        for tag in index.values()
    ]
    options.sort(key=lambda o: (o.group or "", o.value))
    return ReferenceDataResponse(options=options).dict()
```

- [ ] **Step 4: Run tests**

Run: `pytest app/actions/tests/test_reference_actions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/handlers.py app/actions/tests/test_reference_actions.py
git commit -m "feat: list_tag_names reference action"
```

---

### Task 5: `action_list_tag_fields`

**Files:**
- Modify: `app/actions/configurations.py` (add `ListTagFieldsQuery`)
- Modify: `app/actions/handlers.py` (add `action_list_tag_fields`)
- Test: `app/actions/tests/test_reference_actions.py` (extend)

**Interfaces:**
- Consumes: `_fetch_tag_index` (Task 4), contract types (Task 1).
- Produces: `action_list_tag_fields(integration, action_config: ListTagFieldsQuery) -> dict`; `ListTagFieldsQuery(tag_name: str)`. Option `value` = field name, `description` = CMORE dataType (so the portal tooltip shows "Lookup"/"String"/…). Unknown tag → raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_list_tag_fields_returns_fields_for_tag(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    result = await action_list_tag_fields(
        integration, ListTagFieldsQuery(tag_name="Evidence of Poacher")
    )

    assert [(o["value"], o["description"]) for o in result["options"]] == [
        ("Reported By", "String"),
        ("Evidence Type", "Lookup"),
    ]


@pytest.mark.asyncio
async def test_list_tag_fields_unknown_tag_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    with pytest.raises(ValueError, match="No Such Tag"):
        await action_list_tag_fields(
            integration, ListTagFieldsQuery(tag_name="No Such Tag")
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/actions/tests/test_reference_actions.py -k tag_fields -v`
Expected: FAIL with `ImportError: cannot import name 'ListTagFieldsQuery'`

- [ ] **Step 3: Implement**

`app/actions/configurations.py`:

```python
class ListTagFieldsQuery(ReferenceActionConfiguration):
    """Query model for action_list_tag_fields."""

    tag_name: str
```

`app/actions/handlers.py` (fields keep CMORE's own order — insertion order of `TagInfo.fields` — so don't sort):

```python
async def action_list_tag_fields(
    integration: Integration, action_config: ListTagFieldsQuery
):
    """Reference action: field names within one CMORE tag."""
    index = await _fetch_tag_index(integration)
    tag = index.get(action_config.tag_name)
    if tag is None:
        raise ValueError(
            f"Unknown CMORE tag {action_config.tag_name!r} for this integration."
        )
    options = [
        ReferenceOption(value=f.name, description=f.data_type)
        for f in tag.fields.values()
    ]
    return ReferenceDataResponse(options=options).dict()
```

(Extend the `.configurations` import in handlers.py with `ListTagFieldsQuery`.)

- [ ] **Step 4: Run tests** — `pytest app/actions/tests/test_reference_actions.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/handlers.py app/actions/tests/test_reference_actions.py
git commit -m "feat: list_tag_fields reference action"
```

---

### Task 6: `action_list_field_options`

**Files:**
- Modify: `app/actions/configurations.py` (add `ListFieldOptionsQuery`)
- Modify: `app/actions/handlers.py` (add `action_list_field_options`)
- Test: `app/actions/tests/test_reference_actions.py` (extend)

**Interfaces:**
- Consumes: `_fetch_tag_index` (Task 4), contract types (Task 1).
- Produces: `action_list_field_options(integration, action_config: ListFieldOptionsQuery) -> dict`; `ListFieldOptionsQuery(tag_name: str, field_name: str)`. Lookup/FixedLookup → one option per lookup `value`; any other dataType → empty `options` (free-text field, nothing to suggest). Unknown tag or field → `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_list_field_options_returns_lookup_values(integration, mock_cmore_client):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="Evidence Type"),
    )
    assert [o["value"] for o in result["options"]] == ["Abalone Harvesting", "Camp"]


@pytest.mark.asyncio
async def test_list_field_options_non_lookup_field_returns_empty(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    result = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="Reported By"),
    )
    assert result["options"] == []


@pytest.mark.asyncio
async def test_list_field_options_unknown_field_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    with pytest.raises(ValueError, match="No Such Field"):
        await action_list_field_options(
            integration,
            ListFieldOptionsQuery(tag_name="Evidence of Poacher", field_name="No Such Field"),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/actions/tests/test_reference_actions.py -k field_options -v`
Expected: FAIL with `ImportError: cannot import name 'ListFieldOptionsQuery'`

- [ ] **Step 3: Implement**

`app/actions/configurations.py`:

```python
class ListFieldOptionsQuery(ReferenceActionConfiguration):
    """Query model for action_list_field_options."""

    tag_name: str
    field_name: str
```

`app/actions/handlers.py` (the `("Lookup", "FixedLookup")` pair matches the dataType handling already in `_resolve_field_value` — keep them consistent):

```python
async def action_list_field_options(
    integration: Integration, action_config: ListFieldOptionsQuery
):
    """Reference action: allowed values for a Lookup/FixedLookup field.

    Non-lookup fields legitimately return an empty options list — they are
    free-text in CMORE, so there is nothing to suggest.
    """
    index = await _fetch_tag_index(integration)
    tag = index.get(action_config.tag_name)
    if tag is None:
        raise ValueError(
            f"Unknown CMORE tag {action_config.tag_name!r} for this integration."
        )
    field_info = tag.field_by_name(action_config.field_name)
    if field_info is None:
        raise ValueError(
            f"Unknown field {action_config.field_name!r} in CMORE tag "
            f"{action_config.tag_name!r}."
        )
    if field_info.data_type not in ("Lookup", "FixedLookup"):
        return ReferenceDataResponse(options=[]).dict()
    options = [
        ReferenceOption(value=lookup["value"])
        for lookup in field_info.lookups
        if lookup.get("value")
    ]
    return ReferenceDataResponse(options=options).dict()
```

- [ ] **Step 4: Run tests** — `pytest app/actions/tests/test_reference_actions.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/handlers.py app/actions/tests/test_reference_actions.py
git commit -m "feat: list_field_options reference action"
```

---

### Task 7: `action_list_classification_values`

**Files:**
- Modify: `app/actions/configurations.py` (add `ListClassificationValuesQuery`)
- Modify: `app/actions/handlers.py` (add `_classification_options` + `action_list_classification_values`)
- Test: `app/actions/tests/test_reference_actions.py` (extend)

**Interfaces:**
- Consumes: contract types (Task 1); `_get_auth_config`, `CmoreClient` (existing).
- Produces: `action_list_classification_values(integration, action_config: ListClassificationValuesQuery) -> dict`; `ListClassificationValuesQuery(battleDimension: Optional[str], force: Optional[str], type: Optional[str])` — returns the *next* level of the classification tree given what's chosen: nothing set → battleDimensions; battleDimension set → its forces; +force → its types; +type → its roles. Unknown intermediate value → `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
CLASSIFICATION_TREE = [
    {
        "battleDimension": "AIR",
        "forces": [
            {
                "force": "CIVIL",
                "types": [
                    {"type": "FIXED_WING", "roles": ["UNKNOWN", "LIGHT", "MICROLIGHT"]},
                ],
            },
        ],
    },
    {
        "battleDimension": "LAND",
        "forces": [
            {"force": "ANIMAL", "types": [{"type": "DOG", "roles": ["K9"]}]},
        ],
    },
]


@pytest.mark.asyncio
async def test_list_classification_values_cascades(integration, mock_cmore_client):
    from app.actions.configurations import ListClassificationValuesQuery
    from app.actions.handlers import action_list_classification_values

    mock_cmore_client.get_classification_tree = AsyncMock(
        return_value=CLASSIFICATION_TREE
    )

    async def values(**kwargs):
        result = await action_list_classification_values(
            integration, ListClassificationValuesQuery(**kwargs)
        )
        return [o["value"] for o in result["options"]]

    assert await values() == ["AIR", "LAND"]
    assert await values(battleDimension="AIR") == ["CIVIL"]
    assert await values(battleDimension="AIR", force="CIVIL") == ["FIXED_WING"]
    assert await values(battleDimension="AIR", force="CIVIL", type="FIXED_WING") == [
        "UNKNOWN", "LIGHT", "MICROLIGHT",
    ]


@pytest.mark.asyncio
async def test_list_classification_values_unknown_branch_raises(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListClassificationValuesQuery
    from app.actions.handlers import action_list_classification_values

    mock_cmore_client.get_classification_tree = AsyncMock(
        return_value=CLASSIFICATION_TREE
    )
    with pytest.raises(ValueError, match="SEA"):
        await action_list_classification_values(
            integration, ListClassificationValuesQuery(battleDimension="SEA")
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest app/actions/tests/test_reference_actions.py -k classification -v`
Expected: FAIL with `ImportError: cannot import name 'ListClassificationValuesQuery'`

- [ ] **Step 3: Implement**

`app/actions/configurations.py` (field names deliberately mirror `SubjectClassificationMapping`'s camelCase `battleDimension` and the `$data` sibling refs in Task 8):

```python
class ListClassificationValuesQuery(ReferenceActionConfiguration):
    """Query for action_list_classification_values. Each level narrows the
    CMORE classification tree; the action returns the next level's values."""

    battleDimension: Optional[str] = None
    force: Optional[str] = None
    type: Optional[str] = None
```

`app/actions/handlers.py`:

```python
def _classification_options(tree: list, query) -> list:
    """Walk the classification tree one level past the deepest set param."""
    if not query.battleDimension:
        return [n["battleDimension"] for n in tree if n.get("battleDimension")]
    node = next(
        (n for n in tree if n.get("battleDimension") == query.battleDimension), None
    )
    if node is None:
        raise ValueError(f"Unknown battleDimension {query.battleDimension!r}.")
    forces = node.get("forces") or []
    if not query.force:
        return [f["force"] for f in forces if f.get("force")]
    force_node = next((f for f in forces if f.get("force") == query.force), None)
    if force_node is None:
        raise ValueError(
            f"Unknown force {query.force!r} under battleDimension "
            f"{query.battleDimension!r}."
        )
    types = force_node.get("types") or []
    if not query.type:
        return [t["type"] for t in types if t.get("type")]
    type_node = next((t for t in types if t.get("type") == query.type), None)
    if type_node is None:
        raise ValueError(f"Unknown type {query.type!r} under force {query.force!r}.")
    return [r for r in (type_node.get("roles") or []) if r]


async def action_list_classification_values(
    integration: Integration, action_config: ListClassificationValuesQuery
):
    """Reference action: next level of the CMORE classification tree."""
    auth = _get_auth_config(integration)
    async with CmoreClient(
        base_url=auth.base_url, token=auth.token.get_secret_value()
    ) as client:
        tree = await client.get_classification_tree()
    values = _classification_options(tree, action_config)
    return ReferenceDataResponse(
        options=[ReferenceOption(value=v) for v in values]
    ).dict()
```

- [ ] **Step 4: Run tests** — `pytest app/actions/tests/test_reference_actions.py -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/handlers.py app/actions/tests/test_reference_actions.py
git commit -m "feat: list_classification_values reference action"
```

---

### Task 8: `gundi:reference` annotations on DeliverConfig + drift test

**Files:**
- Modify: `app/actions/configurations.py` (`DeliverConfig.ui_schema()`, lines ~249-301)
- Test: `app/actions/tests/test_configurations.py` (append drift test)

**Interfaces:**
- Consumes: the four action ids (`list_tag_names`, `list_tag_fields`, `list_field_options`, `list_classification_values`) and their query models (Tasks 4-7); `discover_actions` from `app.actions.core`.
- Produces: `gundi:reference` annotations in the ui_schema, per spec §2/§4. Inert today: no `ui:widget` is added or changed anywhere.

`$data` semantics reminder (spec §2): paths resolve from the object containing the annotated field; an array and its item are distinct levels; each `../` climbs one level; a bare name is a sibling field.

- [ ] **Step 1: Write the failing drift test**

Append to `app/actions/tests/test_configurations.py`:

```python
def _collect_gundi_references(node, found):
    if isinstance(node, dict):
        if "gundi:reference" in node:
            found.append((node, node["gundi:reference"]))
        for value in node.values():
            _collect_gundi_references(value, found)


def test_gundi_reference_annotations_match_registered_reference_actions():
    """Drift guard: every gundi:reference annotation must name a real
    reference action whose query model matches the declared params, and must
    never set ui:widget (forward-compat: old portals ignore the annotation)."""
    from app.actions.configurations import DeliverConfig
    from app.actions.core import ReferenceActionConfiguration, discover_actions

    handlers = discover_actions(module_name="app.actions.handlers", prefix="action_")
    found = []
    _collect_gundi_references(DeliverConfig.ui_schema(), found)

    annotated_actions = {ref["action"] for _, ref in found}
    assert annotated_actions == {
        "list_tag_names",
        "list_tag_fields",
        "list_field_options",
        "list_classification_values",
    }

    for host_node, ref in found:
        assert "ui:widget" not in host_node, ref["action"]
        assert ref["target"] == "self"
        assert ref["allow_free_text"] is True

        _, config_model, _ = handlers[ref["action"]]
        assert issubclass(config_model, ReferenceActionConfiguration)

        declared = set(ref.get("params", {}))
        model_fields = set(config_model.__fields__)
        assert declared <= model_fields, (ref["action"], declared - model_fields)
        required = {
            name for name, f in config_model.__fields__.items() if f.required
        }
        assert required <= declared, (ref["action"], required - declared)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest app/actions/tests/test_configurations.py -k gundi_reference -v`
Expected: FAIL — `annotated_actions` is empty (no annotations exist yet).

- [ ] **Step 3: Implement the annotations**

In `DeliverConfig.ui_schema()`, merge `gundi:reference` keys into the dicts already built there (do not touch any existing key; the paths below name where each annotation lands). Add a module-level helper just above `DeliverConfig` to keep the literals readable:

```python
def _reference(action: str, params: Optional[dict] = None) -> dict:
    """Build a gundi:reference ui_schema annotation (spec: docs/superpowers/
    specs/2026-07-31-reference-data-config-ui-design.md §2). Deliberately
    does NOT set ui:widget — portals without reference support must keep
    rendering plain text fields."""
    return {
        "action": action,
        "target": "self",
        "params": params or {},
        "allow_free_text": True,
    }
```

Then, inside `ui_schema()` after the existing `base.update({...})` call, add:

```python
        # Reference-data annotations (inert until the portal supports them).
        event_items = base["event_type_to_tag"]["items"]
        event_items["tag_name"]["gundi:reference"] = _reference("list_tag_names")
        field_items = event_items["field_mappings"]["items"]
        field_items["cmore_field_name"]["gundi:reference"] = _reference(
            "list_tag_fields", {"tag_name": {"$data": "../../tag_name"}}
        )
        value_items = field_items["value_mappings"]["items"]
        value_items["to_value"]["gundi:reference"] = _reference(
            "list_field_options",
            {
                "tag_name": {"$data": "../../../../tag_name"},
                "field_name": {"$data": "../../cmore_field_name"},
            },
        )
        classification_items = base["subject_type_to_classification"]["items"]
        classification_items["battleDimension"]["gundi:reference"] = _reference(
            "list_classification_values"
        )
        classification_items["force"]["gundi:reference"] = _reference(
            "list_classification_values",
            {"battleDimension": {"$data": "battleDimension"}},
        )
        classification_items["type"]["gundi:reference"] = _reference(
            "list_classification_values",
            {
                "battleDimension": {"$data": "battleDimension"},
                "force": {"$data": "force"},
            },
        )
        classification_items["role"]["gundi:reference"] = _reference(
            "list_classification_values",
            {
                "battleDimension": {"$data": "battleDimension"},
                "force": {"$data": "force"},
                "type": {"$data": "type"},
            },
        )
        return base
```

Note: the existing method ends with `return base` after the `base.update({...})` — replace that single `return base` with the block above. The existing per-field dicts (`"tag_name": {...}`, `"to_value"` etc.) already exist inside the `base.update` literal for most of these; where one doesn't (e.g. `to_value` currently has no dict of its own), indexing `value_items["to_value"]` would `KeyError` — in that case add an empty dict for the field inside the `base.update` literal first (e.g. `"to_value": {},`) and keep the assignment style above uniform.

- [ ] **Step 4: Run tests**

Run: `pytest app/actions/tests/test_configurations.py -v`
Expected: drift test PASSES; all existing ui_schema tests still PASS (the annotations only add keys, never change existing ones).

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/tests/test_configurations.py
git commit -m "feat: inert gundi:reference annotations on DeliverConfig ui_schema"
```

---

### Task 9: Full-suite verification

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest --tb=short`
Expected: everything PASSES — the new reference actions must not have broken discovery (`discover_actions` now also finds the four new `action_*` functions; the registration default keeps them out of the Gundi payload), delivery handlers, or webhook tests.

- [ ] **Step 2: Sanity-check discovery output**

Run: `python -c "from app.actions.core import get_actions; print(sorted(get_actions()))"`
Expected output includes: `auth`, `deliver`, `list_classification_values`, `list_field_options`, `list_tag_fields`, `list_tag_names` (plus any other pre-existing actions).

- [ ] **Step 3: Commit anything outstanding & done**

If steps 1-2 required fixes, commit them with a `fix:` message. Phase 0 is complete: contract types, four reference actions, inert annotations, default-off registration, and tests.
