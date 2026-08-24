"""Validate a CMORE integration configuration against the live CMORE API.

Answers, for a given auth config (base_url / token / owner_group_id) and
optional deliver config: does this token have access to everything the action
runner needs at delivery time?

Checks are independent functions returning CheckResult(s) so they can be run
from the CLI (``python -m app.datasource.cli validate``) and reused by
``action_auth`` later. Each failing check carries a remediation hint and a
remediation *category*: things fixable in the Gundi portal vs. things only
the CMORE team can grant (share groups, Integration permission, tag domains).
"""

import enum
from typing import List, Optional, Tuple

import httpx
import pydantic

from .mapping_scaffold import _normalize
from .schemas import CmoreEvent, UploadType
from .tag_index import _build_index


class CheckStatus(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


class Remediation(str, enum.Enum):
    PORTAL = "fix in Gundi portal"
    CMORE_TEAM = "contact the CMORE team"


class CheckResult(pydantic.BaseModel):
    name: str
    status: CheckStatus
    detail: str
    remediation: Optional[str] = None
    remediation_category: Optional[Remediation] = None


async def check_auth(client) -> Tuple[CheckResult, Optional[list]]:
    """Token validity + base_url reachability via GET /v2/tags/getfull.

    Returns the raw tags response on success so downstream checks don't
    re-fetch it.
    """
    name = "auth"
    try:
        raw_tags = await client.get_tags()
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        if status_code in (401, 403):
            return (
                CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    detail=f"CMORE returned {status_code} for GET /v2/tags/getfull.",
                    remediation=(
                        "The token is invalid or was rotated. Check the token in the "
                        "auth config; regenerate the service API key if needed."
                    ),
                    remediation_category=Remediation.PORTAL,
                ),
                None,
            )
        if status_code >= 500 or status_code in (408, 429):
            return (
                CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    detail=(
                        f"CMORE returned {status_code} for GET /v2/tags/getfull — the "
                        "route was reached but the server failed to serve it."
                    ),
                    remediation=(
                        "Likely transient server trouble (the client already retried). "
                        "Retry later; if it persists, contact the CMORE team."
                    ),
                ),
                None,
            )
        return (
            CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                detail=f"CMORE returned {status_code} for GET /v2/tags/getfull.",
                remediation=(
                    "The server responded but not with the tags endpoint — the base_url "
                    "is likely wrong (it must include the API path, e.g. "
                    "https://<instance>/za/WebAPI/api)."
                ),
                remediation_category=Remediation.PORTAL,
            ),
            None,
        )
    except httpx.ConnectTimeout as e:
        return (
            CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                detail=f"Could not connect to CMORE ({type(e).__name__}): the host never "
                       "accepted the connection.",
                remediation="Check the base_url host and network access (firewall/VPN/DNS).",
                remediation_category=Remediation.PORTAL,
            ),
            None,
        )
    except httpx.TimeoutException as e:
        return (
            CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                detail=(
                    f"GET /v2/tags/getfull timed out ({type(e).__name__}): the server "
                    "accepted the connection but didn't respond in time. This is not "
                    "an authentication failure — a bad token gets an instant 401. "
                    "Large production tag catalogs can take 30s+ to serve."
                ),
                remediation="Rerun with a longer --timeout (e.g. --timeout 180).",
            ),
            None,
        )
    except httpx.HTTPError as e:
        return (
            CheckResult(
                name=name,
                status=CheckStatus.FAIL,
                detail=f"Could not reach CMORE: {type(e).__name__}: {e}",
                remediation="Check the base_url (scheme, host, and API path) and network access.",
                remediation_category=Remediation.PORTAL,
            ),
            None,
        )
    return (
        CheckResult(
            name=name,
            status=CheckStatus.PASS,
            detail="Token accepted; tags endpoint reachable.",
        ),
        raw_tags,
    )


def check_tag_mappings(index, deliver_data: Optional[dict]) -> List[CheckResult]:
    """Resolve every event_type_to_tag mapping against the live tag index.

    One CheckResult per mapping. An unresolvable tag most often means the
    share group lacks the tag's domain grant (tags from ungranted domains
    simply don't appear in /v2/tags/getfull) — or the tag was renamed.
    Unresolvable fields FAIL; a to_value that isn't a valid lookup option
    WARNs (the runner drops such values at delivery time but still posts
    the event).
    """
    mappings = (deliver_data or {}).get("event_type_to_tag") or []
    if not mappings:
        return [
            CheckResult(
                name="tag_mappings",
                status=CheckStatus.SKIP,
                detail="No event_type_to_tag mappings configured; nothing to resolve.",
            )
        ]

    results = []
    for mapping in mappings:
        event_type = mapping.get("event_type", "<missing event_type>")
        name = f"tag_mapping:{event_type}"
        tag_ref = mapping.get("tag")
        tag_info = index.resolve(str(tag_ref)) if tag_ref else None
        if tag_info is None:
            results.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    detail=f"Tag {tag_ref!r} not found in this token's tag view.",
                    remediation=(
                        "Either the tag was renamed (update the config to the tag ID) "
                        "or this share group lacks the tag's domain grant — ask the "
                        "CMORE team to grant the domain to your share group."
                    ),
                    remediation_category=Remediation.CMORE_TEAM,
                )
            )
            continue

        problems = []  # (is_failure, message)
        for fm in mapping.get("field_mappings") or []:
            field_ref = fm.get("cmore_field")
            field_info = tag_info.resolve_field(str(field_ref)) if field_ref else None
            if field_info is None:
                problems.append(
                    (True, f"Field {field_ref!r} not found on tag {tag_info.name!r}.")
                )
                continue
            if field_info.data_type in ("Lookup", "FixedLookup"):
                options = [
                    lk.get("value") for lk in (field_info.lookups or [])
                    if lk.get("value") is not None
                ]
                normalized = {_normalize(o) for o in options}
                for vm in fm.get("value_mappings") or []:
                    to_value = vm.get("to_value")
                    if to_value and _normalize(to_value) not in normalized:
                        problems.append(
                            (
                                False,
                                f"Value {to_value!r} is not a valid option for field "
                                f"{field_info.name!r} (options: {options}); the runner "
                                "will drop it at delivery time.",
                            )
                        )

        if any(is_failure for is_failure, _ in problems):
            status = CheckStatus.FAIL
        elif problems:
            status = CheckStatus.WARN
        else:
            status = CheckStatus.PASS
        detail = (
            "; ".join(msg for _, msg in problems)
            if problems
            else f"Tag {tag_info.name!r} (id {tag_info.id}) and all field/value refs resolve."
        )
        results.append(
            CheckResult(
                name=name,
                status=status,
                detail=detail,
                remediation=("Fix the mapping in the deliver config." if problems else None),
                remediation_category=(Remediation.PORTAL if problems else None),
            )
        )
    return results


def check_classifications(tree: list, deliver_data: Optional[dict]) -> List[CheckResult]:
    """Validate subject_type_to_classification values against the instance's
    classification tree (GET /v2/clients/get_classification_tree).

    Levels are hierarchical (battleDimension → force → type → role); each set
    level is validated within its parent's branch. A gap (a deeper level set
    while its parent is unset) can't be validated and WARNs.
    """
    mappings = (deliver_data or {}).get("subject_type_to_classification") or []
    if not mappings:
        return [
            CheckResult(
                name="classifications",
                status=CheckStatus.SKIP,
                detail="No subject_type_to_classification mappings configured.",
            )
        ]

    results = []
    for mapping in mappings:
        subject_type = mapping.get("subject_type", "<missing subject_type>")
        name = f"classification:{subject_type}"
        # (level name, configured value, key into the tree nodes, child list key)
        levels = [
            ("battleDimension", mapping.get("battleDimension"), "battleDimension", "forces"),
            ("force", mapping.get("force"), "force", "types"),
            ("type", mapping.get("type"), "type", "roles"),
            ("role", mapping.get("role"), None, None),
        ]
        nodes = tree or []
        problem = None
        gap = False
        for i, (level_name, value, node_key, child_key) in enumerate(levels):
            if value is None:
                # Deeper levels set beyond this gap can't be validated.
                if any(v is not None for _, v, _, _ in levels[i + 1:]):
                    gap = True
                break
            if level_name == "role":
                # nodes is the parent's role list (strings).
                if value not in nodes:
                    problem = (level_name, value, list(nodes))
                break
            match = next((n for n in nodes if n.get(node_key) == value), None)
            if match is None:
                problem = (level_name, value, [n.get(node_key) for n in nodes])
                break
            nodes = match.get(child_key) or []

        if problem:
            level_name, value, options = problem
            results.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.FAIL,
                    detail=(
                        f"{level_name} {value!r} is not valid here. "
                        f"Valid options: {options}."
                    ),
                    remediation="Fix the classification values in the deliver config.",
                    remediation_category=Remediation.PORTAL,
                )
            )
        elif gap:
            results.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.WARN,
                    detail=(
                        "A deeper classification level is set while its parent is "
                        "unset; the value can't be validated against the tree."
                    ),
                    remediation="Set the parent levels (battleDimension → force → type → role) too.",
                    remediation_category=Remediation.PORTAL,
                )
            )
        else:
            results.append(
                CheckResult(
                    name=name,
                    status=CheckStatus.PASS,
                    detail="Classification values are valid for this instance.",
                )
            )
    return results


async def check_gnodes(client) -> CheckResult:
    """Report the GNodes (virtual clients) this token's service owns.

    Only the owning Application Client may feed data for a GNode (anything
    else gets 403), so a token rotated onto a *different* service silently
    orphans every existing GNode. Zero GNodes is normal for a new
    integration but suspicious for one already delivering observations.
    """
    name = "gnode_ownership"
    try:
        mappings = await client.get_gateway_mapping()
    except httpx.HTTPError as e:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"Could not read the gateway mapping: {type(e).__name__}: {e}",
            remediation=(
                "The token must belong to an active service (Software/System client). "
                "Check the service's State in the CMORE Admin Site."
            ),
            remediation_category=Remediation.CMORE_TEAM,
        )
    if not mappings:
        return CheckResult(
            name=name,
            status=CheckStatus.WARN,
            detail=(
                "This token's service owns 0 GNodes. Expected for a new integration; "
                "if this integration was already delivering observations, the token "
                "may belong to a different service than before (existing GNodes "
                "would 403 on update)."
            ),
        )
    sources = sorted({m.trackSource for m in mappings})
    return CheckResult(
        name=name,
        status=CheckStatus.PASS,
        detail=f"Token's service owns {len(mappings)} GNode(s); trackSources: {sources}.",
    )


PROBE_EVENT_DESCRIPTION = "Gundi configuration test — please ignore"


async def check_owner_group(client, owner_group_id: Optional[int], probe: bool) -> CheckResult:
    """Verify the token can post events to the configured owner_group_id.

    CMORE binds a token's service to a Target Group; if they don't match (or
    the service isn't Active) event posts fail — while every read endpoint
    keeps working. There is no read endpoint that exposes the binding, so the
    only real check is posting an event. That's a visible write (it appears
    in the share group's feed), so it's opt-in via ``probe``.
    """
    name = "owner_group"
    if owner_group_id is None:
        return CheckResult(
            name=name,
            status=CheckStatus.SKIP,
            detail="No owner_group_id configured; nothing to verify.",
        )
    if not probe:
        return CheckResult(
            name=name,
            status=CheckStatus.SKIP,
            detail=(
                "Not verified — proving the token can post to this share group "
                "requires posting a real event. Rerun with --probe-event to post "
                "one clearly-labeled test event."
            ),
        )
    event = CmoreEvent(
        description=PROBE_EVENT_DESCRIPTION,
        uploadType=UploadType.GENERATED,
        ownerGroupId=owner_group_id,
    )
    try:
        # Non-retried on purpose: this POST is non-idempotent, and the probe
        # promises exactly one visible test event.
        response = await client.post_event_once(event)
    except httpx.HTTPError as e:
        return CheckResult(
            name=name,
            status=CheckStatus.FAIL,
            detail=f"Posting a test event to share group {owner_group_id} failed: "
                   f"{type(e).__name__}: {e}",
            remediation=(
                "The token's service is not linked to this share group. In the CMORE "
                "Admin Site, set the service's Target Group ID to this share group and "
                "State to Active — or ask the CMORE team if you can't."
            ),
            remediation_category=Remediation.CMORE_TEAM,
        )
    message_id = (response or {}).get("messageId")
    return CheckResult(
        name=name,
        status=CheckStatus.PASS,
        detail=(
            f"Posted test event to share group {owner_group_id}"
            + (f" (messageId {message_id})" if message_id is not None else "")
            + ". It is visible in the share group's feed."
        ),
    )


class ValidationReport(pydantic.BaseModel):
    checks: List[CheckResult]

    @property
    def has_failures(self) -> bool:
        return any(c.status == CheckStatus.FAIL for c in self.checks)


def _skipped(name: str) -> CheckResult:
    return CheckResult(
        name=name,
        status=CheckStatus.SKIP,
        detail="Skipped because the auth check failed.",
    )


async def run_validation(
    client,
    owner_group_id: Optional[int],
    deliver_data: Optional[dict],
    probe_event: bool,
) -> ValidationReport:
    """Run every check against one CMORE client and collect the results.

    If auth fails, downstream checks are reported as SKIP rather than run
    (they would all fail with the same underlying error).
    """
    checks: List[CheckResult] = []
    auth_result, raw_tags = await check_auth(client)
    checks.append(auth_result)
    if auth_result.status == CheckStatus.FAIL:
        checks.extend(
            [_skipped("tag_mappings"), _skipped("classifications"),
             _skipped("gnode_ownership"), _skipped("owner_group")]
        )
        return ValidationReport(checks=checks)

    index = _build_index(raw_tags)
    checks.extend(check_tag_mappings(index, deliver_data))

    if (deliver_data or {}).get("subject_type_to_classification"):
        try:
            tree = await client.get_classification_tree()
        except httpx.HTTPError as e:
            # Preserve the report contract: an endpoint failure is a FAIL
            # result on this check, never a traceback that aborts the run.
            checks.append(
                CheckResult(
                    name="classifications",
                    status=CheckStatus.FAIL,
                    detail=(
                        "Could not fetch the classification tree: "
                        f"{type(e).__name__}: {e}"
                    ),
                    remediation="Retry later; if it persists, contact the CMORE team.",
                )
            )
        else:
            checks.extend(check_classifications(tree, deliver_data))
    else:
        checks.extend(check_classifications([], deliver_data))

    checks.append(await check_gnodes(client))
    checks.append(await check_owner_group(client, owner_group_id, probe_event))
    return ValidationReport(checks=checks)
