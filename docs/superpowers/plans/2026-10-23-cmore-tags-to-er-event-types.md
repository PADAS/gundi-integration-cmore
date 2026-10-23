# CMORE tags → EarthRanger event types — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A portal-triggered action on the CMORE integration that creates one EarthRanger event type (with choice lists) per selected CMORE tag, and a delivery path that maps those generated types back to their tags by naming convention with no stored mapping.

**Architecture:** Pure builders under `app/datasource/er_event_types.py` turn raw `/v2/tags/getfull` entries into event-type specs, v2 schema payloads and choice records; a plain-httpx EarthRanger admin client under `app/datasource/er_admin_client.py` writes them; the handler `action_create_er_event_types` in `app/actions/handlers.py` resolves the connected EarthRanger provider through Gundi, runs a permissions preflight, reconciles by id prefix and writes. `_push_event` gains a convention branch that recognises `cmore_<group>_<tagid>_…` for this integration's own group.

**Tech Stack:** Python 3.10, Pydantic v1 (`pydantic.BaseModel`), httpx + `backoff`, `gundi_client_v2.GundiClient`, pytest + pytest-asyncio + pytest-mock. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-cmore-tags-to-er-event-types-design.md`

## Global Constraints

- Pydantic models for every structure that crosses a function boundary; no `@dataclass` (repo convention; `tag_index.py`'s dataclasses predate it and are not touched).
- All EarthRanger HTTP calls live in `app/datasource/er_admin_client.py`, plain args only, no imports from `app.services` or `app.actions`; `@backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=5, giveup=<4xx except 429>)` as in `app/datasource/client.py`.
- Nothing under `app/services/` changes.
- EarthRanger limits: category `value` ≤ 67 and `display` ≤ 100; event type `value` ≤ 255; `Choice.field` ≤ 40; choice `value` and `display` ≤ 100; placeholder (`hint`) ≤ 32.
- Naming: category `cmore_<group>`; event type `cmore_<group>_<tagid>_<slug>`; field key `f<fieldid>_<slug>`; choice `value` = CMORE lookup value verbatim. Slugs: lowercase, runs of non-`[a-z0-9]` → `_`, stripped of leading/trailing `_`.
- Existing objects are matched by id prefix, never by slug; values and keys are never rewritten once created; nothing in EarthRanger is deleted.
- EarthRanger API paths: `GET /api/v1.0/user/me`; `GET|POST /api/v1.0/activity/events/categories/`; `GET /api/v2.0/activity/eventtypes/?include_schema=true`, `POST /api/v2.0/activity/eventtypes/`, `PATCH /api/v2.0/activity/eventtypes/<value>/`; `GET /api/v1.0/choices/?model=activity.event&field=<field>` (a 400 for a never-seen field means "no records"), `POST /api/v1.0/choices/`, `PATCH /api/v1.0/choices/<id>/`. Password grant: `POST <site>/oauth2/token` with `grant_type=password`, `client_id=das_web_client`.
- The action refuses ephemeral runs with `IntegrationConfigurationError` and a fixed message that echoes no submitted value.

## Review Focus

1. A CMORE tag name that slugs to the empty string (all punctuation, e.g. "???") must still yield a valid event type value `cmore_<group>_<tagid>` with no trailing underscore. Pinned in Task 1.
2. Two fields on one tag whose names slug identically (e.g. "Count" and "count") must keep distinct keys, which the id prefix guarantees; the schema must not drop one. Pinned in Task 2.
3. A lookup option value longer than 100 chars is truncated for the choice record, and the delivered value must still resolve: delivery matches the *CMORE* option by the normalised prefix, so the choice `display` keeps the truncated text and `_resolve_field_value` is handed the ER choice value. Pinned in Task 3 (truncation) and Task 8 (resolution of a truncated value).
4. An event of a generated type whose `event_details` carries a key that is not `f<id>_…` (an operator added a field to the ER type by hand) must be ignored, not fail delivery. Pinned in Task 8.
5. `user/me` without a `choice` permissions entry at all (older EarthRanger) must be reported as missing `choice: add, change`, not crash on a KeyError. Pinned in Task 6.

---

### Task 1: Naming convention and parsers

**Files:**
- Create: `app/datasource/er_event_types.py`
- Create: `app/datasource/tests/fixtures/cmore_tags_sample.json`
- Test: `app/datasource/tests/test_er_event_types.py`

**Interfaces:**
- Produces: `slugify(name: str) -> str`; `category_value(group: int) -> str`; `event_type_value(group: int, tag_id: int, tag_name: str) -> str`; `field_key(field_id: int, field_name: str) -> str`; `parse_generated_event_type(value: str, group: int) -> Optional[int]`; `parse_generated_field_key(key: str) -> Optional[int]`; `generated_type_prefix(group: int, tag_id: int) -> str` (returns `cmore_<group>_<tagid>_`); `field_key_prefix(field_id: int) -> str` (returns `f<fieldid>_`).

- [ ] **Step 1: Create the fixture** from the captured catalog (domain "Wildlife", tags 20 *Evidence of Poacher*, 29 *Poacher Sighting*, 43 *Poached Animal* which has nested options):

```bash
python3 -I - <<'PY'
import json
d = json.load(open("8443.get-tags.json"))
keep = {20, 29, 43}
out = [{**blk, "tags": [t for t in (blk.get("tags") or []) if t["id"] in keep]} for blk in d if blk.get("id") == 8]
json.dump(out, open("app/datasource/tests/fixtures/cmore_tags_sample.json", "w"), indent=1)
PY
```

- [ ] **Step 2: Write the failing tests**

```python
import pytest
from app.datasource.er_event_types import (
    slugify, category_value, event_type_value, field_key,
    parse_generated_event_type, parse_generated_field_key,
    generated_type_prefix, field_key_prefix,
)

def test_slugify_collapses_runs_and_strips_edges():
    assert slugify("Evidence of Poacher") == "evidence_of_poacher"
    assert slugify("  K9 / Deployment!! ") == "k9_deployment"
    assert slugify("???") == ""

def test_category_value_carries_the_group():
    assert category_value(7932) == "cmore_7932"

def test_event_type_value_carries_group_tag_and_slug():
    assert event_type_value(7932, 20, "Evidence of Poacher") == "cmore_7932_20_evidence_of_poacher"

def test_event_type_value_with_empty_slug_has_no_trailing_underscore():
    assert event_type_value(7932, 20, "???") == "cmore_7932_20"

def test_event_type_value_is_truncated_to_255():
    assert len(event_type_value(7932, 20, "x" * 300)) == 255

def test_field_key_fits_the_choice_field_limit():
    assert field_key(261, "Evidence Type") == "f261_evidence_type"
    assert len(field_key(261, "a very long field name that keeps going on and on")) == 40

def test_parse_generated_event_type_matches_only_this_group():
    assert parse_generated_event_type("cmore_7932_20_evidence_of_poacher", 7932) == 20
    assert parse_generated_event_type("cmore_7932_20", 7932) == 20
    assert parse_generated_event_type("cmore_7933_20_evidence_of_poacher", 7932) is None
    assert parse_generated_event_type("rhino_carcass", 7932) is None
    assert parse_generated_event_type("cmore_7932_x_bad", 7932) is None

def test_parse_generated_field_key():
    assert parse_generated_field_key("f261_evidence_type") == 261
    assert parse_generated_field_key("species") is None
    assert parse_generated_field_key("f_261") is None

def test_prefixes():
    assert generated_type_prefix(7932, 20) == "cmore_7932_20_"
    assert field_key_prefix(261) == "f261_"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -v`
Expected: FAIL with `ModuleNotFoundError: app.datasource.er_event_types`

- [ ] **Step 4: Implement the eight functions in `app/datasource/er_event_types.py`**

`event_type_value` joins `cmore_<group>_<tagid>` and the slug with `_` only when the slug is non-empty, then truncates to 255 and strips a trailing `_`. `field_key` truncates to 40 the same way. The parsers use one compiled regex each (`^cmore_(\d+)_(\d+)(?:_|$)` and `^f(\d+)_`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add app/datasource/er_event_types.py app/datasource/tests/test_er_event_types.py app/datasource/tests/fixtures/cmore_tags_sample.json
git commit -m "Add the naming convention for CMORE-generated EarthRanger event types"
```

---

### Task 2: Event type spec from a raw CMORE tag

**Files:**
- Modify: `app/datasource/er_event_types.py`
- Test: `app/datasource/tests/test_er_event_types.py`

**Interfaces:**
- Consumes: Task 1's builders.
- Produces (Pydantic, in `er_event_types.py`):
  ```python
  class ChoiceOption(BaseModel): value: str; display: str; ordernum: int
  class FieldSpec(BaseModel):
      key: str; cmore_field_id: int; label: str
      type: Literal["string", "textarea", "number", "boolean", "datetime", "select", "multiselect"]
      required: bool = False; hint: Optional[str] = None
      options: List[ChoiceOption] = []          # select / multiselect only
  class SkippedField(BaseModel): cmore_field_id: int; name: str; reason: str
  class ShallowField(BaseModel): cmore_field_id: int; name: str; nested_option_count: int
  class EventTypeSpec(BaseModel):
      value: str; display: str; category_value: str; cmore_tag_id: int
      fields: List[FieldSpec]; skipped: List[SkippedField]; shallow_fields: List[ShallowField]
  def build_event_type_spec(raw_tag: dict, group: int) -> EventTypeSpec
  ```
  `raw_tag` is one entry of a domain's `tags` list from `/v2/tags/getfull`.

- [ ] **Step 1: Write the failing tests** (load the fixture once with a module-level helper `_tag(tag_id)`)

```python
def test_spec_for_evidence_of_poacher():
    spec = build_event_type_spec(_tag(20), 7932)
    assert spec.value == "cmore_7932_20_evidence_of_poacher"
    assert spec.display == "Evidence of Poacher"
    assert spec.category_value == "cmore_7932"
    keys = [f.key for f in spec.fields]
    assert "f260_reported_by" not in keys                     # filled from the ER reporter
    assert [s.name for s in spec.skipped] == ["Reported By"]
    by_key = {f.key: f for f in spec.fields}
    assert by_key["f261_evidence_type"].type == "multiselect"  # allowMultipleValues on a Lookup
    assert by_key["f261_evidence_type"].options[0] == ChoiceOption(value="Abalone Harvesting", display="Abalone Harvesting", ordernum=1)
    assert by_key["f264_number_of_snares_found"].type == "number"
    assert by_key["f264_number_of_snares_found"].hint == "Number of snares found"

def test_type_translation_table():
    def one(data_type, allow_multiple=False):
        raw = {"id": 1, "name": "T", "fields": [{"id": 9, "name": "X", "dataType": data_type, "allowMultipleValues": allow_multiple, "lookups": [{"id": 1, "value": "a", "order": 1}]}]}
        spec = build_event_type_spec(raw, 1)
        return spec.fields[0].type if spec.fields else [s.reason for s in spec.skipped]
    assert one("String") == "string" and one("IDNumber") == "string"
    assert one("Text") == "textarea"
    assert one("Number") == "number" and one("Duration") == "number"
    assert one("Boolean") == "boolean"
    assert one("DateTime") == "datetime"
    assert one("Lookup") == "select" and one("FixedLookup") == "select"
    assert one("Lookup", allow_multiple=True) == "multiselect"
    assert one("Media") == ["unsupported data type Media"]
    assert one("Message") == ["unsupported data type Message"]

def test_mandatory_hint_truncation_and_required():
    raw = {"id": 1, "name": "T", "fields": [{"id": 9, "name": "X", "dataType": "String", "isMandatory": True, "hint": "h" * 40}]}
    f = build_event_type_spec(raw, 1).fields[0]
    assert f.required is True and len(f.hint) == 32

def test_nested_options_emit_top_level_only_and_report_shallow():
    spec = build_event_type_spec(_tag(43), 7932)            # Poached Animal
    animal = next(f for f in spec.fields if f.label == "Animal")
    assert all(o.value for o in animal.options)
    assert "Buffalo" not in [o.value for o in animal.options]
    shallow = next(s for s in spec.shallow_fields if s.name == "Animal")
    assert shallow.nested_option_count > 0

def test_fields_with_colliding_slugs_keep_distinct_keys():
    raw = {"id": 1, "name": "T", "fields": [{"id": 1, "name": "Count", "dataType": "Number"}, {"id": 2, "name": "count", "dataType": "Number"}]}
    assert [f.key for f in build_event_type_spec(raw, 1).fields] == ["f1_count", "f2_count"]

def test_options_are_ordered_by_cmore_order_and_truncated():
    raw = {"id": 1, "name": "T", "fields": [{"id": 9, "name": "X", "dataType": "Lookup", "lookups": [{"id": 2, "value": "b", "order": 2}, {"id": 1, "value": "x" * 120, "order": 1}]}]}
    opts = build_event_type_spec(raw, 1).fields[0].options
    assert [o.ordernum for o in opts] == [1, 2] and len(opts[0].value) == 100
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -k "spec or translation or mandatory or nested or colliding or ordered" -v`
Expected: FAIL with `ImportError` on `build_event_type_spec`

- [ ] **Step 3: Implement the models and `build_event_type_spec` in `app/datasource/er_event_types.py`**

Translation table as a module dict keyed by `dataType`; "Reported by" skip compares `name.strip().lower() == "reported by"` with reason `"filled from the EarthRanger reporter"`; a nested option is one whose `linkedLookups` list is non-empty, counted recursively for `nested_option_count`; options sorted by `order` then id.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/datasource/er_event_types.py app/datasource/tests/test_er_event_types.py
git commit -m "Build an EarthRanger event type spec from a CMORE tag"
```

---

### Task 3: v2 schema payload and choice planning

**Files:**
- Modify: `app/datasource/er_event_types.py`
- Test: `app/datasource/tests/test_er_event_types.py`

**Interfaces:**
- Consumes: `EventTypeSpec`, `FieldSpec`, `ChoiceOption` (Task 2).
- Produces:
  ```python
  def build_event_type_payload(spec: EventTypeSpec, *, existing_value: Optional[str] = None, existing_keys: Dict[int, str] = {}) -> dict
  def build_choice_records(spec: EventTypeSpec, existing_keys: Dict[int, str] = {}) -> Dict[str, List[dict]]
  class ChoiceOp(BaseModel): action: Literal["create", "update", "deactivate", "unchanged"]; value: str; payload: Optional[dict] = None; choice_id: Optional[str] = None
  def plan_field_choices(existing: List[dict], desired: List[dict]) -> List[ChoiceOp]
  ```
  `existing_value` / `existing_keys` (cmore field id → existing ER key) let a re-run keep values and keys already on the site (spec Decision 6).

- [ ] **Step 1: Write the failing tests**

```python
def test_payload_envelope_matches_the_v2_shape():
    p = build_event_type_payload(build_event_type_spec(_tag(20), 7932))
    assert p["value"] == "cmore_7932_20_evidence_of_poacher" and p["display"] == "Evidence of Poacher"
    assert p["category"] == "cmore_7932" and p["is_active"] is True
    js, ui = p["schema"]["json"], p["schema"]["ui"]
    assert js["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert js["type"] == "object" and js["unevaluatedProperties"] is False
    assert ui["order"] == ["section-1"] and set(ui["fields"]) == set(js["properties"])
    assert ui["sections"]["section-1"]["leftColumn"][0] == {"name": "f261_evidence_type", "type": "field"}

def test_scalar_and_choice_properties():
    p = build_event_type_payload(build_event_type_spec(_tag(20), 7932))
    props, ui = p["schema"]["json"]["properties"], p["schema"]["ui"]["fields"]
    assert props["f264_number_of_snares_found"] == {"type": "number", "title": "Number of Snares Found", "deprecated": False}
    assert ui["f264_number_of_snares_found"] == {"type": "NUMERIC", "parent": "section-1", "placeholder": "Number of snares found"}
    assert props["f261_evidence_type"] == {
        "type": "array", "title": "Evidence Type", "deprecated": False, "uniqueItems": True,
        "items": {"type": "string", "anyOf": [{"$ref": "/api/v2.0/schemas/choices.json?field=f261_evidence_type"}]},
    }
    assert ui["f261_evidence_type"] == {"type": "CHOICE_LIST", "inputType": "DROPDOWN", "parent": "section-1"}

def test_required_fields_land_in_required():
    raw = {"id": 1, "name": "T", "fields": [{"id": 9, "name": "X", "dataType": "String", "isMandatory": True}]}
    assert build_event_type_payload(build_event_type_spec(raw, 1))["schema"]["json"]["required"] == ["f9_x"]

def test_existing_value_and_keys_are_kept_on_rerun():
    spec = build_event_type_spec(_tag(20), 7932)
    p = build_event_type_payload(spec, existing_value="cmore_7932_20_old_name", existing_keys={261: "f261_old_key"})
    assert p["value"] == "cmore_7932_20_old_name"
    assert "f261_old_key" in p["schema"]["json"]["properties"] and "f261_evidence_type" not in p["schema"]["json"]["properties"]
    assert build_choice_records(spec, existing_keys={261: "f261_old_key"}).keys() == {"f261_old_key"}

def test_choice_records_shape():
    recs = build_choice_records(build_event_type_spec(_tag(20), 7932))["f261_evidence_type"]
    assert recs[0] == {"model": "activity.event", "field": "f261_evidence_type", "value": "Abalone Harvesting", "display": "Abalone Harvesting", "is_active": True, "ordernum": 1}

def test_plan_field_choices_create_update_deactivate_unchanged():
    desired = [{"model": "activity.event", "field": "f", "value": "a", "display": "A", "is_active": True, "ordernum": 1},
               {"model": "activity.event", "field": "f", "value": "b", "display": "B", "is_active": True, "ordernum": 2}]
    existing = [{"id": "1", "value": "a", "display": "A", "is_active": True, "ordernum": 1},
                {"id": "2", "value": "b", "display": "old", "is_active": True, "ordernum": 2},
                {"id": "3", "value": "c", "display": "C", "is_active": True, "ordernum": 3}]
    ops = {op.value: op for op in plan_field_choices(existing, desired)}
    assert ops["a"].action == "unchanged"
    assert ops["b"].action == "update" and ops["b"].choice_id == "2" and ops["b"].payload == {"display": "B", "is_active": True}
    assert ops["c"].action == "deactivate" and ops["c"].payload == {"is_active": False}
    assert plan_field_choices([], desired)[0].action == "create"

def test_plan_leaves_server_order_alone_when_visible_order_already_matches():
    desired = [{"model": "activity.event", "field": "f", "value": "a", "display": "A", "is_active": True, "ordernum": 1}]
    existing = [{"id": "1", "value": "a", "display": "A", "is_active": True, "ordernum": 10}]
    assert plan_field_choices(existing, desired)[0].action == "unchanged"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -k "payload or required_fields or existing_value or choice_records or plan_" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `build_event_type_payload`, `build_choice_records`, `ChoiceOp`, `plan_field_choices`**

Port the envelope from earthranger-cli `schema_gen.build_schema` (single section `section-1`, label = `spec.display`, `columns: 1`, all fields in `leftColumn`, `headers: {}`) and the planner from earthranger-cli `choices.plan_field_choices` (including `choice_sort_key` and the order-settled rule). Scalar/ui maps: string → `{"type":"string"}` / `{"type":"TEXT","inputType":"SHORT_TEXT"}`; textarea → string / `LONG_TEXT`; number → number / `NUMERIC`; boolean → boolean / `BOOLEAN`; datetime → `{"type":"string","format":"date-time"}` / `DATE_TIME`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_event_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/datasource/er_event_types.py app/datasource/tests/test_er_event_types.py
git commit -m "Emit the EarthRanger v2 schema payload and plan choice records for a generated type"
```

---

### Task 4: EarthRanger admin client

**Files:**
- Create: `app/datasource/er_admin_client.py`
- Test: `app/datasource/tests/test_er_admin_client.py`

**Interfaces:**
- Produces:
  ```python
  class ErAdminClient:
      def __init__(self, site_root: str, *, token: Optional[str] = None, username: Optional[str] = None, password: Optional[str] = None, timeout: float = 30.0)
      async def __aenter__(self) -> "ErAdminClient"; async def __aexit__(self, *exc) -> None
      async def get_me(self) -> dict
      async def get_event_categories(self) -> List[dict]
      async def post_event_category(self, payload: dict) -> dict
      async def get_event_types(self) -> List[dict]           # v2, include_schema=true, all pages
      async def post_event_type(self, payload: dict) -> dict
      async def patch_event_type(self, value: str, payload: dict) -> dict
      async def get_choices(self, field: str) -> List[dict]  # [] on the 400 "never seen" answer
      async def post_choice(self, payload: dict) -> dict
      async def patch_choice(self, choice_id: str, payload: dict) -> dict
  class ErAdminAuthError(Exception)   # password grant rejected
  ```
  `site_root` is `scheme://host`. Responses are unwrapped from EarthRanger's `{"data": ...}` envelope; paginated lists follow `data.next` until null.

- [ ] **Step 1: Write the failing tests** using `httpx.MockTransport` injected through a private `transport` kwarg (`ErAdminClient(..., transport=...)`), recording each request:

```python
def test_token_auth_sends_bearer_and_unwraps_data()           # GET /api/v1.0/user/me → {"data": {"username": "x", "permissions": {}}}; Authorization == "Bearer t"
async def test_password_grant_exchanges_then_uses_token()     # first request is POST /oauth2/token form with grant_type=password, client_id=das_web_client; then Bearer
async def test_password_grant_failure_raises_auth_error()     # token endpoint 400 → ErAdminAuthError
async def test_get_event_types_follows_pagination_with_schema()  # two pages via data.next; query has include_schema=true; returns 3 items
async def test_get_choices_returns_empty_on_the_never_seen_400()  # 400 body mentioning "is not one of the available choices" → []
async def test_get_choices_filters_by_model_and_field()      # params model=activity.event&field=f261_evidence_type
async def test_patch_event_type_targets_the_value_path()     # PATCH /api/v2.0/activity/eventtypes/cmore_1_2_x/
async def test_server_error_raises_http_status_error()       # 500 → httpx.HTTPStatusError after backoff gives up (patch backoff to max_tries=1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_admin_client.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `ErAdminClient` in `app/datasource/er_admin_client.py`**

One `httpx.AsyncClient(base_url=site_root)`; the password grant runs lazily on first request; the same `retry_transient` shape as `app/datasource/client.py` (copy, do not import, to keep the module standalone).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/datasource/tests/test_er_admin_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/datasource/er_admin_client.py app/datasource/tests/test_er_admin_client.py
git commit -m "Add a plain-httpx EarthRanger admin client for categories, event types and choices"
```

---

### Task 5: Action configuration model

**Files:**
- Modify: `app/actions/configurations.py` (after `DeliverConfig`)
- Test: `app/actions/tests/test_configurations.py`

**Interfaces:**
- Produces:
  ```python
  class CreateErEventTypesConfig(GenericActionConfiguration, ExecutableActionMixin):
      tags: List[str]                      # title "CMORE tags"; items annotated gundi:reference list_tag_names (self, no params)
      share_group_label: Optional[str]     # title "Share group label"
      dry_run: bool = False                # title "Preview only"
      @classmethod ui_schema() -> dict     # ui:order ["tags", "share_group_label", "dry_run"]
  ```

- [ ] **Step 1: Write the failing tests**

```python
def test_create_er_event_types_config_fields_and_order():
    from app.actions.configurations import CreateErEventTypesConfig
    from app.actions.core import ExecutableActionMixin, GenericActionConfiguration
    assert issubclass(CreateErEventTypesConfig, GenericActionConfiguration)
    assert issubclass(CreateErEventTypesConfig, ExecutableActionMixin)
    ui = CreateErEventTypesConfig.ui_schema()
    assert ui["ui:order"] == ["tags", "share_group_label", "dry_run"]
    from app.actions.configurations import _reference
    assert ui["tags"]["items"]["gundi:reference"] == _reference("list_tag_names")
    assert "ui:widget" not in ui["tags"]["items"]

def test_create_er_event_types_config_requires_tags():
    import pydantic, pytest
    from app.actions.configurations import CreateErEventTypesConfig
    with pytest.raises(pydantic.ValidationError):
        CreateErEventTypesConfig(tags=[])
    cfg = CreateErEventTypesConfig(tags=["20"])
    assert cfg.dry_run is False and cfg.share_group_label is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/actions/tests/test_configurations.py -k create_er_event_types -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the model** with `FieldWithUIOptions` like its neighbours; `tags` uses `pydantic.Field(..., min_items=1)`; `ui_schema` builds `{"ui:order": [...], "tags": {"items": {"gundi:reference": _reference("list_tag_names")}}}` merged with `GlobalUISchemaOptions` as `DeliverConfig.ui_schema` does.

- [ ] **Step 4: Run all configuration tests** (the drift guard `test_gundi_reference_annotations_match_registered_reference_actions` walks `DeliverConfig` only; confirm it still passes)

Run: `.venv/bin/python -m pytest app/actions/tests/test_configurations.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/tests/test_configurations.py
git commit -m "Add the Create EarthRanger event types action configuration"
```

---

### Task 6: Handler: provider resolution and permissions preflight

**Files:**
- Modify: `app/actions/handlers.py` (new helpers near `_get_auth_config`; new action at the end, before `action_deliver`)
- Test: `app/actions/tests/test_create_er_event_types.py`

**Interfaces:**
- Consumes: `CreateErEventTypesConfig` (Task 5); `ErAdminClient` (Task 4); `GundiClient.get_connections(params)` and `get_integration_details(id)` from `gundi_client_v2`; `ephemeral_run` from `app.services.activity_logger`; `IntegrationConfigurationError` from `app.services.errors`.
- Produces:
  ```python
  class ErProvider(BaseModel): integration_id: str; site_root: str; token: Optional[str]; username: Optional[str]; password: Optional[str]
  async def _resolve_er_provider(integration: Integration) -> ErProvider
  REQUIRED_ER_PERMISSIONS = {"eventcategory": {"add"}, "eventtype": {"add", "change"}, "choice": {"add", "change"}}
  def _missing_er_permissions(me: dict) -> Dict[str, List[str]]     # resource → sorted missing verbs; {} when complete
  async def action_create_er_event_types(integration: Integration, action_config: CreateErEventTypesConfig) -> dict
  ```
  In this task the action stops after the preflight and returns `{"earthranger": {"site": ..., "username": ..., "permissions_ok": True}, "dry_run": ..., "event_types": [], "category": None, "choices": {"created": 0, "updated": 0, "deactivated": 0}}`.

- [ ] **Step 1: Write the failing tests** (fixtures: `integration` from `test_handlers._integration_dict`; `mock_gundi` patching `app.actions.handlers.GundiClient` so `get_connections` returns a list of `Connection`-shaped dicts and `get_integration_details` returns an ER `Integration` with an `auth` configuration `{"authentication_type": "token", "token": "er-token"}` and `base_url="https://site.pamdas.org/"`; `mock_er_admin` patching `app.actions.handlers.ErAdminClient` with `get_me` returning `{"username": "ingest", "permissions": {...}}`)

```python
async def test_refuses_ephemeral_runs()                      # ephemeral_run.set(True) → IntegrationConfigurationError; GundiClient never constructed
async def test_resolves_the_single_connected_er_provider()   # get_connections called with {"destination_id": <integration.id>, "provider_type": "earthranger"}; ErAdminClient built with site_root "https://site.pamdas.org", token "er-token"
async def test_fails_with_no_provider()                      # [] → IntegrationConfigurationError whose message contains "0 EarthRanger provider"
async def test_fails_with_two_providers()                    # two → message contains "2 EarthRanger providers"
async def test_username_password_provider_is_passed_through()  # auth {"authentication_type": "username_password", "username": "u", "password": "p"} → ErAdminClient(username="u", password="p", token=None)
async def test_missing_permissions_fail_before_any_write()   # permissions {"eventtype": ["view"]} → IntegrationConfigurationError mentioning "eventcategory: add", "eventtype: add, change", "choice: add, change" and the username; no other ErAdminClient method awaited
async def test_missing_choice_entry_is_reported_not_crashed()  # permissions without a "choice" key at all → same error lists "choice: add, change"
async def test_preflight_passes_and_reports()                # full permissions → result["earthranger"] == {"site": "https://site.pamdas.org", "username": "ingest", "permissions_ok": True}
def test_missing_er_permissions_pure():
    assert _missing_er_permissions({"permissions": {"eventcategory": ["add"], "eventtype": ["add", "change"], "choice": ["add", "change"]}}) == {}
    assert _missing_er_permissions({"permissions": {}}) == {"choice": ["add", "change"], "eventcategory": ["add"], "eventtype": ["add", "change"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/actions/tests/test_create_er_event_types.py -v`
Expected: FAIL with `ImportError` on `action_create_er_event_types`

- [ ] **Step 3: Implement `ErProvider`, `_resolve_er_provider`, `_missing_er_permissions`, and the action up to the preflight**

Site root = `f"{parsed.scheme}://{parsed.hostname}"` of the provider's `base_url` (reject a missing scheme or host with `IntegrationConfigurationError("The EarthRanger provider's site URL is missing or malformed")`). Fixed messages: ephemeral → `"This action writes to EarthRanger and only runs on a saved integration"`; provider count → `f"Expected exactly one connected EarthRanger provider, found {n} EarthRanger provider(s)"`; permissions → `f"EarthRanger user {username!r} lacks permissions: " + "; ".join(f"{r}: {', '.join(v)}" for r, v in sorted(missing.items()))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/actions/tests/test_create_er_event_types.py -v`
Expected: PASS

- [ ] **Step 5: Register-ability check**: `discover_actions("app.actions.handlers", "action_")` lists `create_er_event_types` with config model `CreateErEventTypesConfig` (add one assertion to the test file).

- [ ] **Step 6: Commit**

```bash
git add app/actions/handlers.py app/actions/tests/test_create_er_event_types.py
git commit -m "Add the Create EarthRanger event types action: provider lookup and permissions preflight"
```

---

### Task 7: Handler: build, reconcile and write

**Files:**
- Modify: `app/actions/handlers.py` (`action_create_er_event_types`)
- Test: `app/actions/tests/test_create_er_event_types.py`

**Interfaces:**
- Consumes: Tasks 2–4 builders and client; `tag_index._build_index` / `TagIndexData.resolve` for ref resolution (`app/datasource/tag_index.py`); `log_action_activity`.
- Produces: the full result dict from spec §4 step 6:
  `{"earthranger": {...}, "category": {"value", "display", "action"}, "event_types": [{"value", "display", "action", "fields", "skipped": [SkippedField dicts], "shallow_fields": [ShallowField dicts]}], "choices": {"created", "updated", "deactivated"}, "dry_run"}`.

- [ ] **Step 1: Write the failing tests** (extend the fixtures: `mock_cmore` patching `app.actions.handlers.CmoreClient` with `get_tags` returning the Task 1 fixture; `mock_er_admin` with `get_event_categories → []`, `get_event_types → []`, `get_choices → []`, `post_*`/`patch_*` as `AsyncMock` returning `{}`)

```python
async def test_unknown_tag_ref_fails_before_any_write()      # tags=["No Such Tag"] → IntegrationConfigurationError; no post_* awaited
async def test_dry_run_builds_but_writes_nothing()           # tags=["20"], dry_run=True → result["event_types"][0]["value"] == "cmore_7932_20_evidence_of_poacher", action "created"; no post_*/patch_* awaited; result["dry_run"] is True
async def test_first_run_creates_category_type_and_choices()
    # tags=["20"] → post_event_category({"value": "cmore_7932", "display": "CMORE group 7932", "is_active": True})
    # post_event_type payload["value"] == "cmore_7932_20_evidence_of_poacher"; post_choice awaited once per option of f261_evidence_type with field "f261_evidence_type"
    # result["choices"]["created"] == <option count>, result["category"]["action"] == "created"
async def test_share_group_label_names_the_category()        # share_group_label="Lapalala CMORE" → display "Lapalala CMORE"
async def test_second_run_patches_the_existing_type_and_leaves_choices()  # get_event_types returns the type with value "cmore_7932_20_old" and schema whose property is "f261_old_key"; get_choices returns matching records → patch_event_type("cmore_7932_20_old", payload with property "f261_old_key"); no post_event_type; choices all "unchanged"
async def test_existing_category_is_not_rewritten()          # get_event_categories returns [{"value": "cmore_7932", "display": "Old"}] → no post_event_category; result["category"]["action"] == "unchanged"
async def test_result_lists_skipped_and_shallow_fields()     # tags=["20", "43"] → event_types[0]["skipped"][0]["name"] == "Reported By"; event_types[1]["shallow_fields"][0]["name"] == "Animal"
async def test_activity_log_summarises_the_run()             # log_action_activity awaited with level INFO and data containing counts; one WARNING per type with skipped fields
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/actions/tests/test_create_er_event_types.py -v`
Expected: the new tests FAIL (`KeyError` / assertion on empty `event_types`)

- [ ] **Step 3: Implement the rest of the action**

Order: resolve tag refs against `_build_index(await client.get_tags())` using `TagIndexData.resolve` (id-or-name) and keep the **raw** tag dict for each (index raw tags by id while walking the response); build specs; list existing event types once and map `generated_type_prefix(group, tag_id)` → existing (value, `{cmore_field_id: existing_key}` via `parse_generated_field_key` over `schema.json.properties`); if not dry run: ensure category, then per spec `post_event_type` or `patch_event_type`, then per choice field `get_choices` → `plan_field_choices` → apply. Count choice ops into the result. One `log_action_activity` at INFO titled `"Created EarthRanger event types from CMORE tags"` with the counts; one at WARNING per type with skipped fields titled `f"Fields skipped on {display}"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest app/actions/tests/test_create_er_event_types.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/actions/handlers.py app/actions/tests/test_create_er_event_types.py
git commit -m "Create and update EarthRanger event types and choices from the selected CMORE tags"
```

---

### Task 8: Delivery by convention

**Files:**
- Modify: `app/actions/handlers.py` (`_push_event` mapping lookup; `_build_event_tag`)
- Test: `app/actions/tests/test_handlers.py`

**Interfaces:**
- Consumes: `parse_generated_event_type`, `parse_generated_field_key` (Task 1); `_get_auth_config(integration).owner_group_id`.
- Produces: `_build_event_tag(client, base_url, integration_id, mapping, event, *, convention: bool = False) -> Tuple[Optional[CmoreEventTag], bool]`. With `convention=True` the field walk is over `event.event_details` keys that parse to a field id on the tag; values go through `_resolve_field_value` (lists for `allow_multiple` fields) with no value mappings.

- [ ] **Step 1: Write the failing tests** (reuse `_patch_cmore_client`, `_patch_state_manager`, `_patch_activity_logger`, `_patch_tag_index`; a `generated_tag_info` fixture = `TagInfo.build(id=20, name="Evidence of Poacher", ..., fields=[FieldInfo(id=261, name="Evidence Type", data_type="Lookup", allow_multiple=True, lookups=[{"value": "Camp"}, {"value": "Bait Collection"}]), FieldInfo(id=264, name="Number of Snares Found", data_type="Number")])`; the `deliver_config` fixture has no entry for the generated type; `owner_group_id` in the fixture is 7932)

```python
async def test_generated_type_delivers_by_convention_without_a_mapping()
    # event_type "cmore_7932_20_evidence_of_poacher", event_details {"f261_evidence_type": ["Camp", "Bait Collection"], "f264_number_of_snares_found": 3}
    # → post_event awaited; tags[0].tagId == 20; values == [(261,"Camp"),(261,"Bait Collection"),(264,"3")]; activity log NOT awaited (not a skip)
async def test_generated_type_for_another_group_is_skipped_as_unmapped()  # "cmore_7933_20_evidence_of_poacher" → dropped, reason "event_type_not_mapped"
async def test_explicit_mapping_for_a_generated_type_wins()  # deliver_config with an entry for the generated type mapping species→Species on tag 42 → tag 42 used, convention not consulted (tag_index.get called with "42" or the fixture's tag ref)
async def test_unknown_keys_on_a_generated_type_are_ignored()  # event_details {"notes": "x", "f264_number_of_snares_found": 2} → one value, no error
async def test_truncated_choice_value_still_resolves()       # lookup value "x"*120 on the tag; event_details carries "x"*100 → resolved to the CMORE value ("x"*120)
async def test_reporter_field_still_applies_on_convention_path()  # tag has field 260 "Reported By"; provider_metadata reported_by "Jane" → (260, "Jane") in values
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest app/actions/tests/test_handlers.py -k "generated_type or explicit_mapping_for_a_generated or unknown_keys or truncated_choice or convention_path" -v`
Expected: FAIL (the first test sees `dropped`, others `TypeError` on `convention`)

- [ ] **Step 3: Implement the convention branch** in `_push_event` exactly as spec §5 shows (after the explicit lookup, before the `mapping is None` guardrail), and the `convention` walk in `_build_event_tag`. For the truncated-value case, match in `_resolve_field_value`'s lookup branch by `_normalize_for_match(option)[:100] == target` when the candidate is exactly 100 chars (document the rule in a comment).

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q -p no:warnings`
Expected: all pass except the two known local-Python `test_redaction` failures (pass under 3.10.18: `/private/tmp/claude-502/-Users-chrisdo-padas-gundi-integration-cmore/51fb2378-d707-4584-b7c7-0f992f449650/scratchpad/venv310/bin/python -m pytest -q`)

- [ ] **Step 5: Commit**

```bash
git add app/actions/handlers.py app/actions/tests/test_handlers.py
git commit -m "Deliver CMORE-generated event types by naming convention, no mapping required"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/configuration.md` (new `## Create EarthRanger event types` section before `## Scaffolding mappings`; a paragraph under `### Event type → CMORE tag` saying generated types need no entry)
- Modify: `docs/tutorial-sync-event-type.md` (a callout box at the top of `### 2.3` pointing at the action as the fast path)
- Modify: `docs/troubleshooting.md` (three sections before `## Nothing reaches the runner at all`: "The action can't find the EarthRanger site", "The EarthRanger user lacks permissions", "A field is missing from a generated event type")

- [ ] **Step 1: Write the configuration section** covering: what the action creates (category, types, choices, naming with a worked example `cmore_7932_20_evidence_of_poacher` / `f261_evidence_type`), the form fields (CMORE tags, Share group label, Preview only), the permissions the EarthRanger provider user needs (`eventcategory: add`, `eventtype: add, change`, `choice: add, change`), re-run behaviour (matched by id, renames keep values, nothing deleted), share groups (one category per group; another group's types are skipped as unmapped), and the skipped/shallow field rules (Media, Message, Reported by; nested options top-level only, deferred).

- [ ] **Step 2: Write the tutorial callout and the three troubleshooting sections**, each naming the exact error text from Task 6 and where to fix it (Gundi connection for the provider count; EarthRanger admin → Users → permission sets for permissions; the action result's `skipped` / `shallow_fields` for fields).

- [ ] **Step 3: Verify every internal link resolves** (`grep -o '](\(configuration\|troubleshooting\|tutorial-sync-event-type\).md#[^)]*' docs/*.md` and check each anchor exists as a heading).

- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/tutorial-sync-event-type.md docs/troubleshooting.md
git commit -m "Document the Create EarthRanger event types action and its naming convention"
```
