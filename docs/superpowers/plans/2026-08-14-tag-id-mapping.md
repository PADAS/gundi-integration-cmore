# Tag-ID Mapping Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CMORE tag/field mappings key on immutable numeric IDs (with names as a working fallback), so renames in CMORE can no longer break or misclassify delivered events.

**Architecture:** One string config field per concept (`tag`, `cmore_field`) holding "ID-or-name"; a dual-view tag index (`by_id` + `by_name`) with a single resolution rule (ID-first-if-digits, then exact name); reference dropdowns serve `value=str(id), label=name`; scaffold CLI emits IDs plus a human legend. No portal changes required.

**Tech Stack:** Python 3.10, Pydantic v1 (`.dict()`, `__fields__`), FastAPI action-runner template, pytest + pytest-asyncio + pytest-mock, click CLI.

**Spec:** `docs/superpowers/specs/2026-08-14-tag-id-mapping-design.md`

## Global Constraints

- Pydantic v1 syntax everywhere (`pydantic.BaseModel`, `__fields__`, `.dict()`). No `@dataclass` for *new* data structures except in `app/datasource/tag_index.py`, which already uses dataclasses — match the file.
- Config fields carrying refs stay JSON-schema type **string** (the portal reference widget writes `ReferenceOption.value: str`; free text must also work).
- Resolution precedence (spec §2) is fixed: strip whitespace → all-digit ref matching an existing ID resolves by ID → otherwise exact name match. ID beats an all-digit name.
- Reference action **names** do not change (`list_tag_names`, `list_tag_fields`, `list_field_options`, `list_classification_values`) — they are the registered, dev-verified surface.
- `action_list_field_options` option **values** stay lookup value strings (CMORE matches lookups by string).
- Each task must end with the FULL test suite green: `pytest --tb=short -q`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Work on a feature branch: `git checkout -b feature/tag-id-mapping` before Task 1 (create only if it doesn't exist yet).

## File Structure

| File | Responsibility in this change |
|---|---|
| `app/datasource/tag_index.py` | Dual-view index (`TagIndexData`), shared `_resolve` rule, `TagInfo.build` + `resolve_field` |
| `app/actions/configurations.py` | Field renames (`tag`, `cmore_field`), ui_schema/annotation updates, `base_url` required |
| `app/actions/handlers.py` | Delivery + reference actions consume the new index/refs; options serve IDs |
| `app/datasource/mapping_scaffold.py` | Scaffold carries IDs, emits ID-valued config entries, legend |
| `app/datasource/cli.py` | Wizard works off the dual index; prints legend; ID-tolerant defaults |
| `docs/configuration.md`, `docs/tutorial-sync-event-type.md`, `docs/troubleshooting.md` | Doc updates |
| Tests | `app/datasource/tests/test_tag_index.py`, `test_mapping_scaffold.py`, `test_scaffold_cli.py`; `app/actions/tests/test_configurations.py`, `test_reference_actions.py`, `test_handlers.py` |

**Sequencing invariant:** the drift-guard test `test_gundi_reference_annotations_match_registered_reference_actions` (`app/actions/tests/test_configurations.py:52`) asserts annotation param keys ⊆ query-model fields. Therefore annotation param **keys** (`tag_name` → `tag`, `field_name` → `field`) change only in Task 3 together with the query models. Task 2 changes annotation host keys and `$data` **paths** only.

---

### Task 1: Dual-view tag index with one resolution rule

**Files:**
- Modify: `app/datasource/tag_index.py`
- Modify (behavior-preserving call-site updates): `app/actions/handlers.py:77-153,525`, `app/datasource/mapping_scaffold.py:52,156`, `app/datasource/cli.py:426,438,457,576-587`
- Test: `app/datasource/tests/test_tag_index.py`; fixture updates in `app/actions/tests/test_handlers.py:104-118`, `app/datasource/tests/test_mapping_scaffold.py:88-106,127`, `app/datasource/tests/test_scaffold_cli.py:188-192`

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks rely on these exact names):
  - `TagIndexData` dataclass: `by_id: Dict[int, TagInfo]`, `by_name: Dict[str, TagInfo]`, method `resolve(ref: str) -> Optional[TagInfo]`
  - `TagInfo` fields: `id, name, domain, type_limiter, fields_by_id: Dict[int, FieldInfo], fields_by_name: Dict[str, FieldInfo]`; classmethod `TagInfo.build(*, id, name, domain="", type_limiter="", fields=()) -> TagInfo`; method `resolve_field(ref: str) -> Optional[FieldInfo]`
  - `_build_index(raw_response: list) -> TagIndexData`
  - `TagIndex.get(client, base_url, integration_id, tag_ref: str) -> Optional[TagInfo]` (resolves ID-or-name)
  - `FieldInfo` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `app/datasource/tests/test_tag_index.py` (keep the existing `_sample_response()` helper; tag "Poacher Sighting" has id 29, field "Direction" id 1327):

```python
# ----- ID-or-name resolution -----


def test_resolve_by_id_and_by_name():
    index = _build_index(_sample_response())
    assert index.resolve("29").name == "Poacher Sighting"
    assert index.resolve(" 29 ").name == "Poacher Sighting"  # whitespace stripped
    assert index.resolve("Poacher Sighting").id == 29
    assert index.resolve("9999") is None
    assert index.resolve("Not A Real Tag") is None


def test_resolve_digit_ref_with_no_matching_id_falls_back_to_name():
    response = [
        {"name": "X", "tags": [
            {"id": 5, "name": "8443", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("8443").id == 5  # no tag has id 8443 → name match


def test_resolve_id_beats_all_digit_name():
    response = [
        {"name": "X", "tags": [
            {"id": 7, "name": "8443", "typeLimiter": "Incident", "fields": []},
            {"id": 8443, "name": "Real Tag", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("8443").name == "Real Tag"  # documented precedence
    assert index.resolve("7").name == "8443"          # the digit-named tag via its own id


def test_resolve_field_by_id_and_by_name():
    poacher = _build_index(_sample_response()).resolve("Poacher Sighting")
    assert poacher.resolve_field("1327").name == "Direction"
    assert poacher.resolve_field("Direction").id == 1327
    assert poacher.resolve_field("9999") is None
    assert poacher.resolve_field("Nope") is None


def test_by_id_keeps_both_tags_on_name_collision():
    response = [
        {"name": "DomainA", "tags": [
            {"id": 1, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
        {"name": "DomainB", "tags": [
            {"id": 2, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
    ]
    index = _build_index(response)
    assert index.resolve("1").domain == "DomainA"   # both reachable by id
    assert index.resolve("2").domain == "DomainB"
    assert index.resolve("Collision").id == 2       # name view stays last-wins
```

And update the existing tests in the same file to the new shapes (mechanical):
- `test_build_index_flattens_across_domains`: `set(index)` → `set(index.by_name)`; `index["Poacher Sighting"]` → `index.by_name["Poacher Sighting"]`; `set(poacher.fields)` → `set(poacher.fields_by_name)`; `poacher.field_by_name("Direction")` → `poacher.resolve_field("Direction")`; add `assert index.by_id[29] is poacher`.
- `test_build_index_handles_empty_response`: `assert _build_index([]).by_id == {} and _build_index([]).by_name == {}`; same for `None`.
- `test_build_index_skips_tags_with_no_name`: `assert _build_index(response).by_name == {}` and `.by_id == {}`.
- `test_build_index_skips_fields_with_no_name`: `index["Tag1"].fields` → `index.by_name["Tag1"].fields_by_name`.
- `test_build_index_warns_on_tag_name_collision`: `index["Collision"]` → `index.by_name["Collision"]` (twice).
- `test_tag_index_get_returns_tag_info`: `tag.fields["Direction"]` → `tag.fields_by_name["Direction"]`; add a second lookup `assert (await idx.get(client, "https://example/api", "int-1", "29")).id == 29`.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest app/datasource/tests/test_tag_index.py -q`
Expected: new tests FAIL (`AttributeError: 'dict' object has no attribute 'resolve'` or similar); updated existing tests also fail until Step 3.

- [ ] **Step 3: Rewrite `app/datasource/tag_index.py`**

Replace the module body below the imports (keep the module docstring, updating "by name" → "by id or name" in its first line; keep imports, add nothing new):

```python
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
    if ref.isdigit() and int(ref) in by_id:
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

    def __init__(self) -> None:
        # Key: (base_url, integration_id) → TagIndexData
        self._cache: Dict[tuple, TagIndexData] = {}
        self._lock = asyncio.Lock()

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

    async def _ensure_loaded(
        self, client: CmoreClient, base_url: str, integration_id: str
    ) -> TagIndexData:
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
                len(index.by_id),
            )
            self._cache[key] = index
            return index

    def _reset(self) -> None:
        """Test helper — drop the cache."""
        self._cache.clear()


# Module-level singleton used by handlers.
tag_index = TagIndex()
```

- [ ] **Step 4: Update call sites, behavior-preserving**

`app/actions/handlers.py`:
- Line 29: `from app.datasource.tag_index import tag_index, _build_index` → `from app.datasource.tag_index import TagIndexData, tag_index, _build_index`
- `_fetch_tag_index` signature: `async def _fetch_tag_index(integration: Integration) -> TagIndexData:`
- Line 99 (`action_list_tag_names`): `for tag in index.values()` → `for tag in index.by_name.values()`
- Lines 110 and 131: `index.get(action_config.tag_name)` → `index.by_name.get(action_config.tag_name)`
- Line 117: `for f in tag.fields.values()` → `for f in tag.fields_by_name.values()`
- Line 136: `tag.field_by_name(action_config.field_name)` → `tag.resolve_field(action_config.field_name)`
- Line 525 (`_build_event_tag`): `tag_info.field_by_name(field_name)` → `tag_info.resolve_field(field_name)`

`app/datasource/mapping_scaffold.py`:
- Line 52: `tag_info.fields.values()` → `tag_info.fields_by_name.values()`
- Line 156: `for name in tag_info.fields` → `for name in tag_info.fields_by_name`

`app/datasource/cli.py`:
- Lines 426, 438, 457: `tag_info.field_by_name(` → `tag_info.resolve_field(` (three sites)
- Line 581: `titles = [f"{name}  ({index[name].domain})" for name in sorted(index)]` → `titles = [f"{name}  ({index.by_name[name].domain})" for name in sorted(index.by_name)]`
- Line 584: `sorted(index)` → `sorted(index.by_name)`
- Line 587: `tag_info = index.get(resolved_tag) if resolved_tag else None` → `tag_info = index.resolve(str(resolved_tag)) if resolved_tag else None`
- Line 498 `--tag` option help: `"CMORE tag name. Prompted if omitted."` → `"CMORE tag id or exact tag name. Prompted if omitted."`

Test fixture constructors (mechanical — `TagInfo(... fields={name: FieldInfo...})` → `TagInfo.build(... fields=[FieldInfo...])`):
- `app/actions/tests/test_handlers.py:109-118` (`fake_tag_info`):

```python
    return TagInfo.build(
        id=42,
        name="Wildlife Sighting",
        domain="Wildlife",
        type_limiter="Incident",
        fields=[
            FieldInfo(id=101, name="Species", data_type="String"),
            FieldInfo(id=102, name="Count", data_type="Number"),
        ],
    )
```

- `app/datasource/tests/test_mapping_scaffold.py:91-106` and `app/datasource/tests/test_scaffold_cli.py:188-192`: same transformation — drop the dict keys, pass the `FieldInfo(...)` list to `TagInfo.build(fields=[...])`. Any other `TagInfo(` constructions found by `git grep -n "TagInfo(" app/` get the same treatment (do run the grep).

- [ ] **Step 5: Run the full suite**

Run: `pytest --tb=short -q`
Expected: PASS (all files updated together; nothing changed behavior for name refs).

- [ ] **Step 6: Commit**

```bash
git add app/datasource/tag_index.py app/actions/handlers.py app/datasource/mapping_scaffold.py app/datasource/cli.py app/datasource/tests/test_tag_index.py app/actions/tests/test_handlers.py app/datasource/tests/test_mapping_scaffold.py app/datasource/tests/test_scaffold_cli.py
git commit -m "refactor: dual-view tag index with ID-or-name resolution

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Config schema renames (`tag`, `cmore_field`) + required `base_url`

**Files:**
- Modify: `app/actions/configurations.py`
- Modify: `app/actions/handlers.py:500-541` (consume renamed fields), `app/datasource/mapping_scaffold.py:89-102` (config-entry keys), `app/datasource/cli.py:412-416,551-555,585` (renamed keys in existing-entry defaults)
- Test: `app/actions/tests/test_handlers.py`, `app/actions/tests/test_configurations.py`, `app/datasource/tests/test_mapping_scaffold.py:178-181`, `app/datasource/tests/test_scaffold_cli.py`

**Interfaces:**
- Consumes: `TagIndex.get(..., tag_ref)` and `TagInfo.resolve_field(ref)` from Task 1.
- Produces: `CmoreTagMapping.tag: str` (ID-or-name), `CmoreFieldMapping.cmore_field: str` (ID-or-name); config-entry dict keys `"tag"` / `"cmore_field"`; `AuthenticateConfig.base_url` required. Annotation param keys still `tag_name`/`field_name` (Task 3 renames them with the query models).

- [ ] **Step 1: Update the config models**

In `app/actions/configurations.py`:

`CmoreFieldMapping` — rename `cmore_field_name` to:

```python
    cmore_field: str = FieldWithUIOptions(
        ...,
        title="CMORE Field",
        description=(
            "The CMORE field within the chosen tag. Preferably the immutable "
            "field ID (what the portal dropdown stores); an exact field name "
            "also works but breaks if the field is renamed in CMORE."
        ),
    )
```

`CmoreTagMapping` — rename `tag_name` to:

```python
    tag: str = FieldWithUIOptions(
        ...,
        title="CMORE Tag",
        description=(
            "The CMORE tag to attach to events of this type. Preferably the "
            "immutable tag ID (e.g. '8443', what the portal dropdown stores); "
            "an exact tag name also works but breaks if the tag is renamed. "
            "Resolved at runtime via CMORE's /v2/tags/getfull endpoint."
        ),
    )
```

Update `CmoreTagMapping`'s docstring first line to: `"""Map a Gundi event_type to a CMORE tag (by id or name) and its fields."""`

`AuthenticateConfig.base_url` — replace the default with required:

```python
    base_url: str = FieldWithUIOptions(
        ...,
        title="API Base URL",
        description=(
            "Base URL for the C-more API on your CMORE instance: the server "
            "plus '/za/WebAPI/api' (e.g. https://cmore.csir.co.za/za/WebAPI/api). "
            "URLs differ per CMORE deployment."
        ),
    )
```

In `DeliverConfig.ui_schema()`:
- `"tag_name": {...}` block → key `"tag"`, with `"ui:placeholder": "e.g. 8443 or Rhino Carcass"` and `"ui:help": "Tag ID (preferred, rename-proof) or exact tag name. Must be visible to this integration's ShareGroup."`
- `"event_details_key"` placeholder unchanged; `"cmore_field_name": {"ui:placeholder": "e.g. Animal Sex"}` → `"cmore_field": {"ui:placeholder": "e.g. 1261 or Animal Sex"}`
- Annotation host keys follow the renames; `$data` **paths** update; param **keys** stay (drift guard forces this until Task 3):

```python
        event_items = base["event_type_to_tag"]["items"]
        event_items["tag"]["gundi:reference"] = _reference("list_tag_names")
        field_items = event_items["field_mappings"]["items"]
        field_items["cmore_field"]["gundi:reference"] = _reference(
            "list_tag_fields", {"tag_name": {"$data": "../../tag"}}
        )
        value_items = field_items["value_mappings"]["items"]
        value_items["to_value"]["gundi:reference"] = _reference(
            "list_field_options",
            {
                "tag_name": {"$data": "../../../../tag"},
                "field_name": {"$data": "../../cmore_field"},
            },
        )
```

The provider-target annotations (on `event_type`, `event_details_key`, `from_value`) need NO changes: their host fields didn't rename, and their `$data` paths reference only `event_type`/`event_details_key` — never the renamed CMORE-side fields.

- [ ] **Step 2: Update consumers**

`app/actions/handlers.py` `_build_event_tag`:
- Line 500: `mapping.tag_name` → `mapping.tag`
- Line 505 (warning log arg): `mapping.tag_name` → `mapping.tag`
- Line 524: `field_name = fm.cmore_field_name` → `field_ref = fm.cmore_field` (and rename the local's uses on lines 525/530: `tag_info.resolve_field(field_ref)`, log arg `field_ref`)

`app/datasource/mapping_scaffold.py` `ScaffoldResult.to_config_entry` — keys only (values still names; Task 5 switches to IDs):

```python
        return {
            "event_type": self.event_type,
            "tag": self.tag_name,
            "field_mappings": [
                {
                    "event_details_key": f.event_details_key,
                    "cmore_field": f.cmore_field_name,
                    **({"value_mappings": f.value_mappings} if f.value_mappings else {}),
                }
                for f in self.fields
            ],
        }
```

`app/datasource/cli.py`:
- Line 585 (tag-picker default): `default=(existing_entry or {}).get("tag_name")` → resolve the ref to a name so ID-valued entries pre-select correctly:

```python
        resolved_tag = tag_name
        if not resolved_tag:
            existing_ref = (existing_entry or {}).get("tag")
            existing_tag = index.resolve(str(existing_ref)) if existing_ref else None
            titles = [f"{name}  ({index.by_name[name].domain})" for name in sorted(index.by_name)]
            resolved_tag = await _choose(
                f"Select the CMORE tag to map '{event_type}' events to:",
                sorted(index.by_name), titles=titles, skip_label=None,
                default=existing_tag.name if existing_tag else None,
            )
```

- `_interactive_fill` existing-entry defaults (lines 411-416) — renamed key, ID-tolerant:

```python
    for fm in (existing_entry or {}).get("field_mappings", []):
        ref = fm.get("cmore_field")
        fi = tag_info.resolve_field(str(ref)) if ref is not None else None
        existing_field_by_key[fm.get("event_details_key")] = fi.name if fi else ref
        existing_value_by_key[fm.get("event_details_key")] = {
            vm["from_value"]: vm["to_value"]
            for vm in fm.get("value_mappings", []) if vm.get("to_value")
        }
```

- [ ] **Step 3: Update tests (mechanical renames + one new test)**

- `app/actions/tests/test_handlers.py`: every `CmoreTagMapping(... tag_name=` → `tag=`; every `CmoreFieldMapping(... cmore_field_name=` → `cmore_field=`. Scoped sed is safe in this file:

```bash
sed -i '' 's/cmore_field_name=/cmore_field=/g; s/tag_name=/tag=/g' app/actions/tests/test_handlers.py
```

(Then `git diff app/actions/tests/test_handlers.py` and eyeball that only kwargs changed.)
- `app/datasource/tests/test_mapping_scaffold.py:180-181`: `entry["tag_name"] == "Rhino Carcass"` → `entry["tag"] == "Rhino Carcass"`; `fm["cmore_field_name"]` → `fm["cmore_field"]`. Do NOT rename `ScaffoldResult`/`FieldScaffold` attribute assertions (`.cmore_field_name` at lines 156/162 stay — the dataclass attrs keep their names).
- `app/datasource/tests/test_scaffold_cli.py`: dict-literal keys `"tag_name"` → `"tag"` (lines 63-76, 281) and `"cmore_field_name"` → `"cmore_field"` in dict lookups (lines 135, 205, 235, 283). Keyword args to `ScaffoldResult(`/`FieldScaffold(` at lines 198-200 stay `tag_name=`/`cmore_field_name=` (dataclass attrs).
- Add to `app/actions/tests/test_configurations.py`:

```python
def test_auth_base_url_is_required():
    """Instance URLs must not default to any particular CMORE deployment
    (CSIR feedback point 2)."""
    import pydantic
    import pytest
    from app.actions.configurations import AuthenticateConfig

    assert AuthenticateConfig.__fields__["base_url"].required
    with pytest.raises(pydantic.ValidationError):
        AuthenticateConfig(token="t", owner_group_id=1)
```

- [ ] **Step 4: Run the full suite**

Run: `pytest --tb=short -q`
Expected: PASS. If the drift-guard test fails, check that annotation param keys were NOT renamed (they must stay `tag_name`/`field_name` until Task 3).

- [ ] **Step 5: Commit**

```bash
git add app/actions/configurations.py app/actions/handlers.py app/datasource/mapping_scaffold.py app/datasource/cli.py app/actions/tests/ app/datasource/tests/
git commit -m "feat!: mapping config keys become 'tag'/'cmore_field' (ID-or-name); base_url required

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Reference actions serve IDs; query models renamed

**Files:**
- Modify: `app/actions/handlers.py:92-153`, `app/actions/configurations.py` (query models + annotation param keys)
- Test: `app/actions/tests/test_reference_actions.py`

**Interfaces:**
- Consumes: `TagIndexData.resolve`, `TagInfo.resolve_field`, `fields_by_id`, `by_id` from Task 1.
- Produces: `ListTagFieldsQuery.tag: str`; `ListFieldOptionsQuery.tag: str, field: str`; `list_tag_names`/`list_tag_fields` options with `value=str(id)`, `label=name`.

- [ ] **Step 1: Write the failing tests**

In `app/actions/tests/test_reference_actions.py` (fixture `RAW_TAGS` has tag "Evidence of Poacher" id 20 with fields 260/"Reported By"/String and 261/"Evidence Type"/Lookup, and tag "Animal Sighting" id 21), replace the four listed test bodies:

```python
@pytest.mark.asyncio
async def test_list_tag_names_returns_id_values_with_name_labels(
    integration, mock_cmore_client
):
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    result = await action_list_tag_names(integration, ListTagNamesQuery())

    options = [(o["value"], o["label"], o["group"], o["description"]) for o in result["options"]]
    assert options == [
        ("21", "Animal Sighting", "Wildlife", "ID 21"),
        ("20", "Evidence of Poacher", "Wildlife", "ID 20"),
    ]
    assert result["cache_ttl_seconds"] == 300
    mock_cmore_client.get_tags.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_tag_names_shows_both_tags_on_cross_domain_name_collision(
    integration, mock_cmore_client
):
    """Same-named tags in two domains must BOTH appear (listed from by_id),
    disambiguated by group — fixes the last-wins dropdown collapse."""
    from unittest.mock import AsyncMock
    from app.actions.configurations import ListTagNamesQuery
    from app.actions.handlers import action_list_tag_names

    mock_cmore_client.get_tags = AsyncMock(return_value=[
        {"id": 1, "name": "DomainA", "tags": [
            {"id": 10, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
        {"id": 2, "name": "DomainB", "tags": [
            {"id": 11, "name": "Collision", "typeLimiter": "Incident", "fields": []},
        ]},
    ])
    result = await action_list_tag_names(integration, ListTagNamesQuery())
    assert [(o["value"], o["group"]) for o in result["options"]] == [
        ("10", "DomainA"), ("11", "DomainB"),
    ]


@pytest.mark.asyncio
async def test_list_tag_fields_accepts_id_and_name(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    expected = [
        ("260", "Reported By", "String · ID 260"),
        ("261", "Evidence Type", "Lookup · ID 261"),
    ]
    by_id = await action_list_tag_fields(integration, ListTagFieldsQuery(tag="20"))
    assert [(o["value"], o["label"], o["description"]) for o in by_id["options"]] == expected
    by_name = await action_list_tag_fields(
        integration, ListTagFieldsQuery(tag="Evidence of Poacher")
    )
    assert [(o["value"], o["label"], o["description"]) for o in by_name["options"]] == expected


@pytest.mark.asyncio
async def test_list_tag_fields_unknown_tag_raises(integration, mock_cmore_client):
    from app.actions.configurations import ListTagFieldsQuery
    from app.actions.handlers import action_list_tag_fields

    with pytest.raises(ValueError, match="No Such Tag"):
        await action_list_tag_fields(integration, ListTagFieldsQuery(tag="No Such Tag"))


@pytest.mark.asyncio
async def test_list_field_options_accepts_id_and_name(integration, mock_cmore_client):
    """Option values stay lookup value STRINGS (CMORE matches by string);
    only the tag/field selectors accept ids."""
    from app.actions.configurations import ListFieldOptionsQuery
    from app.actions.handlers import action_list_field_options

    by_id = await action_list_field_options(
        integration, ListFieldOptionsQuery(tag="20", field="261")
    )
    assert [o["value"] for o in by_id["options"]] == ["Camp", "Abalone Harvesting"]
    by_name = await action_list_field_options(
        integration,
        ListFieldOptionsQuery(tag="Evidence of Poacher", field="Evidence Type"),
    )
    assert [o["value"] for o in by_name["options"]] == ["Camp", "Abalone Harvesting"]
```

Also update the file's remaining tests that construct `ListTagFieldsQuery(tag_name=...)` / `ListFieldOptionsQuery(tag_name=..., field_name=...)` to the new kwargs (`tag=`, `field=`) — find them with `grep -n "tag_name\|field_name" app/actions/tests/test_reference_actions.py`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest app/actions/tests/test_reference_actions.py -q`
Expected: FAIL (`unexpected keyword argument 'tag'` / value assertions).

- [ ] **Step 3: Implement**

`app/actions/configurations.py`:

```python
class ListTagFieldsQuery(ReferenceActionConfiguration):
    """Query model for action_list_tag_fields."""

    tag: str  # tag id (preferred) or exact tag name


class ListFieldOptionsQuery(ReferenceActionConfiguration):
    """Query model for action_list_field_options."""

    tag: str
    field: str  # field id (preferred) or exact field name
```

And rename the annotation param keys (from Task 2's interim state):
- `{"tag_name": {"$data": "../../tag"}}` → `{"tag": {"$data": "../../tag"}}`
- `{"tag_name": {"$data": "../../../../tag"}, "field_name": {"$data": "../../cmore_field"}}` → `{"tag": {"$data": "../../../../tag"}, "field": {"$data": "../../cmore_field"}}`

`app/actions/handlers.py`:

```python
async def action_list_tag_names(
    integration: Integration, action_config: ListTagNamesQuery
):
    """Reference action: all CMORE tags visible to this integration.

    Option values carry the immutable tag id (the preferred mapping key);
    the tag name is the display label. Listed from the id view so same-named
    tags in different domains all appear, disambiguated by group."""
    index = await _fetch_tag_index(integration)
    options = [
        ReferenceOption(
            value=str(tag.id),
            label=tag.name,
            group=tag.domain,
            description=f"ID {tag.id}",
        )
        for tag in index.by_id.values()
    ]
    options.sort(key=lambda o: (o.group or "", o.label or ""))
    return ReferenceDataResponse(options=options).dict()


async def action_list_tag_fields(
    integration: Integration, action_config: ListTagFieldsQuery
):
    """Reference action: fields within one CMORE tag (id-valued options)."""
    index = await _fetch_tag_index(integration)
    tag = index.resolve(action_config.tag)
    if tag is None:
        raise ValueError(
            f"Unknown CMORE tag {action_config.tag!r} for this integration."
        )
    options = [
        ReferenceOption(
            value=str(f.id),
            label=f.name,
            description=f"{f.data_type} · ID {f.id}",
        )
        for f in tag.fields_by_id.values()
    ]
    return ReferenceDataResponse(options=options).dict()
```

`action_list_field_options`: replace the lookups (`index.by_name.get(action_config.tag_name)` → `index.resolve(action_config.tag)`; `tag.resolve_field(action_config.field_name)` → `tag.resolve_field(action_config.field)`) and the two `ValueError` messages to quote `action_config.tag!r` / `action_config.field!r`. The lookup-sorting and option construction stay exactly as they are.

- [ ] **Step 4: Run the full suite**

Run: `pytest --tb=short -q`
Expected: PASS — including the drift guard, now that param keys and model fields renamed together.

- [ ] **Step 5: Commit**

```bash
git add app/actions/handlers.py app/actions/configurations.py app/actions/tests/test_reference_actions.py
git commit -m "feat: reference dropdowns store tag/field IDs, display names

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Rename-safety proof tests

**Files:**
- Test: `app/datasource/tests/test_tag_index.py`, `app/actions/tests/test_handlers.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3. Test-only task — it pins the behavior CSIR asked for; a reviewer can reject it independently if the scenarios are wrong.

- [ ] **Step 1: Add the rename-simulation test to `test_tag_index.py`**

```python
@pytest.mark.asyncio
async def test_id_mapping_survives_tag_rename():
    """The CSIR scenario: a tag renamed in CMORE. An id-based mapping keeps
    resolving; the old name (a name-based mapping) stops — the failure mode
    this change eliminates."""
    idx = TagIndex()
    renamed = _sample_response()
    renamed[0]["tags"][0]["name"] = "Poacher Sighting (Legacy)"
    client = _make_client_with_get_tags(renamed)

    tag = await idx.get(client, "https://example/api", "int-1", "29")
    assert tag is not None and tag.id == 29 and tag.name == "Poacher Sighting (Legacy)"
    assert await idx.get(client, "https://example/api", "int-1", "Poacher Sighting") is None
```

- [ ] **Step 2: Add the mixed-ref delivery test to `test_handlers.py`**

Place it next to `test_event_field_mapping_skips_unknown_field` (same fixtures/patch helpers; `fake_tag_info` is tag id 42 with fields Species=101/String, Count=102/Number):

```python
@pytest.mark.asyncio
async def test_event_tag_and_fields_resolve_by_id(
    mocker, integration, provider_info, event, metadata, fake_tag_info
):
    """Mapping keyed by immutable ids: one field by id, one by name, both
    resolve to the same fieldIds on the wire. (Tag-level id resolution is
    covered in test_tag_index — tag_index.get is mocked here.)"""
    from app.actions.configurations import (
        CmoreFieldMapping,
        CmoreTagMapping,
        DeliverConfig,
    )
    from app.actions.handlers import action_deliver

    deliver_config = DeliverConfig(
        event_type_to_tag=[
            CmoreTagMapping(
                event_type="lion_sighting",
                tag="42",
                field_mappings=[
                    CmoreFieldMapping(event_details_key="species", cmore_field="101"),
                    CmoreFieldMapping(event_details_key="count", cmore_field="Count"),
                ],
            ),
        ],
    )
    inner = _patch_cmore_client(mocker)
    _patch_state_manager(mocker)
    _patch_activity_logger(mocker)
    _patch_tag_index(mocker, returning=fake_tag_info)

    event.event_details = {"species": "lion", "count": 3}
    delivery = GundiDelivery(payload=event, provider=provider_info)
    result = await action_deliver(integration, deliver_config, delivery, metadata)

    posted = inner.post_event.await_args[0][0]
    assert posted.tags[0].tagId == 42
    assert {v.fieldId for v in posted.tags[0].values} == {101, 102}
    assert result["event_posted"] is True
```

(If `posted.tags[0]` shape differs, mirror how the nearest existing passing test asserts on `posted.tags` — do not change the wire schema.)

- [ ] **Step 3: Run**

Run: `pytest app/datasource/tests/test_tag_index.py app/actions/tests/test_handlers.py -q`
Expected: PASS immediately (behavior exists since Tasks 1–2). If either FAILS, that is a real defect in Tasks 1–2 — fix the implementation, not the test.

- [ ] **Step 4: Commit**

```bash
git add app/datasource/tests/test_tag_index.py app/actions/tests/test_handlers.py
git commit -m "test: pin rename-safety of ID-based tag/field mappings

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Scaffold emits IDs + name legend

**Files:**
- Modify: `app/datasource/mapping_scaffold.py`, `app/datasource/cli.py:436-438,613,627-629`
- Test: `app/datasource/tests/test_mapping_scaffold.py`, `app/datasource/tests/test_scaffold_cli.py`

**Interfaces:**
- Consumes: `FieldInfo.id`, `FieldInfo.data_type`, `TagInfo.id` from Task 1.
- Produces: `ScaffoldResult.tag_id: int`, `FieldScaffold.cmore_field_id: int`, `FieldScaffold.cmore_field_type: str`, `ScaffoldResult.legend_lines() -> List[str]`; `to_config_entry()` values become `str(id)`.

- [ ] **Step 1: Write the failing tests**

In `app/datasource/tests/test_mapping_scaffold.py`, update the `to_config_entry` test (lines ~178-181) and add the legend test. The file's tag fixture is `Rhino Carcass`; give it `id=8443` if it doesn't already carry an id, and note the fixture's field ids (Animal Sex=1261 per line 96):

```python
def test_to_config_entry_emits_ids(rhino_tag):
    result = build_scaffold(_er_fields(), rhino_tag, event_type="rhino_carcass")
    entry = result.to_config_entry()
    assert entry["tag"] == str(rhino_tag.id)
    by_key = {fm["event_details_key"]: fm for fm in entry["field_mappings"]}
    assert by_key["animal_sex"]["cmore_field"] == "1261"


def test_legend_lines_pair_ids_with_names(rhino_tag):
    result = build_scaffold(_er_fields(), rhino_tag, event_type="rhino_carcass")
    lines = result.legend_lines()
    assert lines[0] == f'tag {rhino_tag.id} = "Rhino Carcass"'
    assert 'field 1261 = "Animal Sex" (Lookup)' in lines
```

(Adapt the two helper names — `rhino_tag` fixture and `_er_fields()` — to whatever the file actually calls its tag fixture and ER-field list builder; keep the assertions identical.)

In `app/datasource/tests/test_scaffold_cli.py`, the `--out` / write-back assertions change from names to ids: `entry["tag"] == "Rhino Carcass"` → `entry["tag"] == str(<fixture tag id>)`; `by_field = {fm["cmore_field"]: ...}` keys become id strings (e.g. `"1260"` for Animal Age per the fixture at line 190). Keep `ScaffoldResult(...)`/`FieldScaffold(...)` constructions but add the now-needed `tag_id=`/`cmore_field_id=` kwargs matching the fixture ids.

- [ ] **Step 2: Run to verify failure**

Run: `pytest app/datasource/tests/ -q`
Expected: FAIL (`KeyError`/`AssertionError` on entry values; `legend_lines` missing).

- [ ] **Step 3: Implement in `mapping_scaffold.py`**

```python
@dataclass
class FieldScaffold:
    event_details_key: str
    cmore_field_name: str
    cmore_field_id: int = 0
    cmore_field_type: str = ""
    value_mappings: List[dict] = field(default_factory=list)  # {from_value, to_value}
    # ER choices we could not map to a CMORE option (need a human decision).
    unresolved_choices: List[str] = field(default_factory=list)


@dataclass
class ScaffoldResult:
    event_type: str
    tag_name: str
    tag_id: int = 0
    fields: List[FieldScaffold] = field(default_factory=list)
    unmatched_er_fields: List[str] = field(default_factory=list)   # no CMORE field
    uncovered_cmore_fields: List[str] = field(default_factory=list)  # no ER field

    def to_config_entry(self) -> dict:
        """Render as a CmoreTagMapping-shaped dict for the DeliverConfig.
        Emits immutable ids (rename-proof); legend_lines() carries the names
        for human review."""
        return {
            "event_type": self.event_type,
            "tag": str(self.tag_id),
            "field_mappings": [
                {
                    "event_details_key": f.event_details_key,
                    "cmore_field": str(f.cmore_field_id),
                    **({"value_mappings": f.value_mappings} if f.value_mappings else {}),
                }
                for f in self.fields
            ],
        }

    def legend_lines(self) -> List[str]:
        """Human-readable id ↔ name legend for the emitted config entry."""
        lines = [f'tag {self.tag_id} = "{self.tag_name}"']
        for f in self.fields:
            suffix = f" ({f.cmore_field_type})" if f.cmore_field_type else ""
            lines.append(f'field {f.cmore_field_id} = "{f.cmore_field_name}"{suffix}')
        return lines
```

In `build_scaffold`: `result = ScaffoldResult(event_type=event_type, tag_name=tag_info.name, tag_id=tag_info.id)`; and the `FieldScaffold(` construction gains `cmore_field_id=cmore_field.id, cmore_field_type=cmore_field.data_type`.

- [ ] **Step 4: Implement in `cli.py`**

- `_interactive_fill` (line ~436): `FieldScaffold(event_details_key=er_key, cmore_field_name=name)` → `FieldScaffold(event_details_key=er_key, cmore_field_name=name, cmore_field_id=field_info.id, cmore_field_type=field_info.data_type)` — note `field_info = tag_info.resolve_field(name)` must be assigned BEFORE constructing (move the existing line 438 up).
- After `click.echo("\n--- mapping entry ---\n" + rendered)` (line ~629):

```python
        click.echo("\n--- name legend (ids are what the entry stores) ---")
        for line in result.legend_lines():
            click.echo("  " + line)
```

- [ ] **Step 5: Run the full suite**

Run: `pytest --tb=short -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/datasource/mapping_scaffold.py app/datasource/cli.py app/datasource/tests/
git commit -m "feat: scaffold emits ID-keyed mappings with a name legend

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `docs/configuration.md`, `docs/tutorial-sync-event-type.md`, `docs/troubleshooting.md`

**Interfaces:** none (prose only). The doc claims must match Tasks 1–5 exactly: field titles "CMORE Tag"/"CMORE Field", ID-or-name with ID-first precedence, scaffold legend.

- [ ] **Step 1: `docs/configuration.md`**

- Authenticate table: `| **API Base URL** | — |` → `| **API Base URL** | yes |` (description text was already updated on 2026-08-14; keep it).
- Deliver section — replace the `CmoreTagMapping` bullet list entry for the tag with:

```markdown
- **Gundi event_type** — the event_type string on incoming events (e.g. `rhino_carcass`).
- **CMORE Tag** — the tag **ID** (preferred — immutable, e.g. `8443`) or the exact tag name. Resolved at runtime from CMORE's `/v2/tags/getfull`; must be visible to this integration's ShareGroup. See [ref resolution](#how-tag-and-field-refs-are-resolved).
- **Field Mappings** — list of **CmoreFieldMapping**:
  - **Gundi event_details key** → **CMORE Field** (field ID preferred, or exact field name — within the chosen tag).
  - **Value Mappings** (optional) — list of `source value → CMORE value` pairs.
```

- Add a new subsection directly above "How field values are resolved":

```markdown
#### How tag and field refs are resolved

The **CMORE Tag** and **CMORE Field** values accept either the immutable
numeric **ID** or the exact **name**:

1. An all-digit value that matches an existing ID resolves **by ID**. This is
   the preferred form — it keeps working when the tag or field is renamed in
   CMORE. The portal's dropdowns store IDs automatically; the
   [`scaffold-mapping`](#scaffolding-mappings-cli-alternative) tool emits IDs
   and prints an id ↔ name legend.
2. Anything else — or an all-digit value matching no ID — resolves by **exact
   name**. Name-based refs break (events post unclassified, field values are
   dropped) if the tag/field is renamed in CMORE.
3. Edge case: a tag literally *named* "8443" while a different tag *has* ID
   8443 resolves to the ID. Deterministic, and avoidable by using IDs.
```

- "How field values are resolved" section: no changes (value strings unchanged).

- [ ] **Step 2: `docs/tutorial-sync-event-type.md`**

- Section 2.3 step 2: replace

```markdown
2. **CMORE Tag Name**: `Rhino Carcass` — exactly as the tag is spelled in
   CMORE's tag chooser.
```

with

```markdown
2. **CMORE Tag**: the tag's **ID** (preferred — it keeps working if the tag
   is ever renamed; the `scaffold-mapping` tool's legend shows it, and the
   portal's dropdown stores it automatically) or the tag name `Rhino
   Carcass` spelled exactly as in CMORE's tag chooser.
```

- Section 2.3 step 3 intro: `**Field Mappings** — one row per detail...` table header `| Gundi event_details key (from ER) | CMORE field name |` → `| Gundi event_details key (from ER) | CMORE field (name or ID) |`, and after the "(Yes, "Rhino Spesies"...)" parenthetical add:

```markdown
   Field IDs work here too and are rename-proof — the scaffold tool emits
   them for you.
```

- [ ] **Step 3: `docs/troubleshooting.md`**

Replace the bullet updated on 2026-08-14 ("Also confirm the configured **Tag Name** matches a real CMORE tag exactly, ...") with:

```markdown
- Also confirm the configured **CMORE Tag** ref resolves, and that an
  `event_type_to_tag` entry exists for the event's `event_type` (an unmapped
  type posts with no tag by design — note that untagged events are excluded
  from CMORE's tag-based filtering, analytics, reporting, dashboards, and
  rule-based workflows). Two ways a ref stops resolving:
  - **ID-based ref** (`"tag": "8443"`): the ID doesn't exist on this instance
    — typo, or the config was copied from a different CMORE instance (IDs are
    instance-specific).
  - **Name-based ref** (`"tag": "Rhino Carcass"`): the tag was renamed in
    CMORE. Switch the mapping to the tag ID (the `get-tags` CLI output or the
    scaffold legend shows it) — IDs survive renames.
```

- [ ] **Step 4: Verify and commit**

Run: `grep -rn "Tag Name\|cmore_field_name\|tag_name" docs/configuration.md docs/tutorial-sync-event-type.md docs/troubleshooting.md` — expect no hits presenting name-keyed config as current (scaffold-internal attr mentions are fine if any).

```bash
git add docs/configuration.md docs/tutorial-sync-event-type.md docs/troubleshooting.md
git commit -m "docs: tag/field mappings key on IDs; document ref resolution

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Final verification sweep

**Files:** none new — verification only.

- [ ] **Step 1: Full suite**

Run: `pytest --tb=short -q`
Expected: PASS, zero warnings introduced by this change (`-W error::DeprecationWarning` not required).

- [ ] **Step 2: Leftover-reference sweep**

Run: `git grep -n "tag_name\|cmore_field_name\|field_by_name" app/ docs/configuration.md docs/tutorial-sync-event-type.md docs/troubleshooting.md`
Expected remaining hits ONLY: `ScaffoldResult.tag_name` / `FieldScaffold.cmore_field_name` dataclass attributes and their internal uses/tests in `app/datasource/` (they are the scaffold's human-readable working representation, by design). Anything else is a missed rename — fix it.

- [ ] **Step 3: Registered-schema sanity**

Run: `python -c "from app.actions.configurations import DeliverConfig, AuthenticateConfig; import json; s=DeliverConfig.schema(); print(json.dumps(s['definitions']['CmoreTagMapping']['properties'], indent=2)); print(AuthenticateConfig.schema()['required'])"`
Expected: `tag` present with type string; no `tag_name`; `base_url` in AuthenticateConfig required list.

- [ ] **Step 4: Commit anything outstanding, then stop**

The branch is ready for PR. Do NOT push or open a PR — the user decides.

**Post-merge/deploy manual checklist (for the operator — not executable by this plan):**
1. Deploy to dev; the runner re-registers on start (`REGISTER_ON_START=true`), which updates the registered schema. **Deploy and re-registration must be coupled:** until re-registration happens, the portal serves the old schema — the form shows the old `tag_name`/`cmore_field_name` fields (saves rejected as missing required `tag`) and dropdown cascades call reference actions with old param keys, which the renamed query models reject. For prod, confirm the deploy re-registers automatically or add a manual re-register step.
2. Verify the dev and prod integrations' auth configs carry `base_url` BEFORE deploying — it is now required, and a config without it fails validation on the next fetch.
3. Existing name-keyed demo configs stop validating — re-enter the `rhino_carcass` mapping via the portal (dropdowns if portal PRs #340/#341 are deployed; otherwise free-text using IDs from the scaffold legend). Deliveries in flight against an old-format config will error and retry between deploy and config re-entry (demo-only noise, not data loss).
4. Deliver a test event with `dev/send_event.py`; confirm the event lands in dev CMORE with the Rhino Carcass tag (`tagId` correct).
5. In dev CMORE admin, rename the Rhino Carcass tag; deliver again; confirm the event still lands **with** the tag — the rename-proofing CSIR asked for. Rename the tag back.
