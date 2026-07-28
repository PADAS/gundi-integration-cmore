# Field-User CMORE/ER Sync Tutorial + Deck — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a screenshot-driven tutorial (`docs/tutorial-sync-event-type.md`) and Marp slide deck (`docs/slides/cmore-er-sync.md` → PDF) showing a field admin how to sync the Rhino Carcass event type from EarthRanger to CMORE via Gundi.

**Architecture:** Hybrid research-then-write workflow — mine the CMORE PDFs for background, explore the live CMORE portal first (the genuine unknown), write the full tutorial with a shot list, then capture Gundi/ER screenshots against that list, run the flow end-to-end once for real, and finally distill the deck. Spec: `docs/superpowers/specs/2026-07-28-cmore-er-sync-field-guide-design.md`.

**Tech Stack:** Markdown (GitHub Pages), Marp (`@marp-team/marp-cli` via npx), Playwright MCP browser tools for screenshots.

## Global Constraints

- Audience is field/ops admins: no JQ, no JSON schema, no CLI internals. The scaffold CLI appears exactly once, as an optional "ask your integration engineer" aside.
- Concrete example throughout: ER event type `rhino_carcass` → CMORE tag `Rhino Carcass`.
- Screenshots: Playwright, 1440×900 viewport, saved to `docs/images/` as `<system>-<nn>-<slug>.png` (systems: `cmore`, `gundi`, `er`). Annotate only genuinely ambiguous click targets.
- **Credentials are supplied by the operator in-session at execution time. Never write credentials into any file, commit, or screenshot. Capture token fields masked or with a dummy value.**
- The live UI is ground truth; the PDFs in `docs/` (Portal User Guide, API Reference, 2017 training deck) are background only.
- Every configuration claim in the tutorial must be verified against the live systems; the E2E flow (Task 6) must actually succeed before the doc claims it works.
- Where the GUNDI-5371 list-shaped-mapping workaround surfaces in the portal UI, describe what the user sees today without explaining the bug.
- Out of scope: observation/GNode sync, webhooks, JQ transforms, code changes.
- All work happens on branch `docs/cmore-er-sync-field-guide`.

---

### Task 1: Mine CMORE reference material into working notes

**Files:**
- Create: `<scratchpad>/cmore-notes.md` (session scratchpad — NOT committed)
- Read: `docs/Cmore Portal User Guide.pdf`, `docs/Cmore API.pdf`, `docs/Cmore Collaboration.pdf`, `docs/Cmore training EWT_CSIR_2017.pdf`, `docs/Cmore API Reference.html`

**Interfaces:**
- Produces: `cmore-notes.md` with two sections — `## Concepts` (definitions in CMORE's own terminology: service, API token, share group / ShareGroupId, tag, tag domain, GNode, event message, affiliation, classification) and `## Open questions for live exploration` (each phrased as a yes/no or where-is-it question).

- [ ] **Step 1: Read the CMORE Portal User Guide PDF** (Read tool, in page batches ≤20). Note every screen name and navigation path that touches: login, user profile, services/API keys, groups/share groups, tags and tag domains, the map, event/message views.
- [ ] **Step 2: Skim the API PDF + API Reference HTML** for the auth model (where tokens come from, what a ShareGroupId is) and the tags endpoints (`/v2/tags/getfull`) — only to define terms, not to document API usage.
- [ ] **Step 3: Write `cmore-notes.md`** with the two sections above. The Open-questions section MUST include at least: (a) Can a logged-in user create/rotate their own API token in the portal, or is it admin-issued? (b) Where does a user see which share group(s) they belong to and the group's ID? (c) Where does a user see which tag domains their group is subscribed to? (d) Can a user self-subscribe a group to a tag domain (the Wildlife-domain episode suggests no) — what does the UI show instead? (e) Where do incoming integration events appear (map layer? feed? both)?
- [ ] **Step 4: Verify** the notes answer "what does each CMORE term mean" for every term used in the spec's tutorial outline. No commit (scratchpad only).

### Task 2: Live CMORE portal exploration + screenshot capture

**Files:**
- Create: `docs/images/cmore-*.png` (numbered as captured)
- Modify: `<scratchpad>/cmore-notes.md` — add `## Findings: self-serve vs admin` and `## Captured shots`

**Interfaces:**
- Consumes: open questions from Task 1; CMORE URL + login from operator (in-session).
- Produces: screenshots named `cmore-01-…` onward; findings section listing, for each setup step, `self-serve` or `requires CMORE admin` with what the UI showed as evidence.

- [ ] **Step 1: Load Playwright MCP tools** (ToolSearch `+browser`), navigate to the CMORE portal login URL, set viewport 1440×900.
- [ ] **Step 2: Capture `cmore-01-login.png`** (login page, credentials NOT typed yet or masked).
- [ ] **Step 3: Log in and systematically visit** — capturing a screenshot at each screen that answers a Task-1 open question: profile/account (token visibility), groups/share groups (group name + ID), tag/tag-domain visibility, the map view, the event feed/messages view. Number sequentially `cmore-02-…` etc., slug = screen name.
- [ ] **Step 4: Locate a prior test event if one exists** (from earlier integration testing) to preview what a delivered ER event looks like; capture it if found.
- [ ] **Step 5: Record findings** in `cmore-notes.md`: answer every open question, and write the self-serve vs. admin boundary table (rows: obtain API token, find ShareGroupId, create/join share group, subscribe group to tag domain, see delivered events).
- [ ] **Step 6: Verify & commit** — every image is 1440×900 PNG, no credentials visible (inspect each), names follow convention. `git add docs/images/cmore-*.png && git commit -m "docs: CMORE portal screenshots for field tutorial"`.

### Task 3: Write the tutorial with embedded CMORE shots + shot list for the rest

**Files:**
- Create: `docs/tutorial-sync-event-type.md`
- Modify: `docs/index.md` (add link in the `## Next` list)
- Read: `docs/configuration.md`, `docs/troubleshooting.md`, `docs/rhino_carcass_schema.json`, `docs/rhino_carcass_schema_from_api.json`

**Interfaces:**
- Consumes: Task 2 findings + captured CMORE images.
- Produces: complete tutorial text; every not-yet-captured screenshot appears as `![...](images/<system>-<nn>-<slug>.png)` plus an HTML comment `<!-- SHOT: <system>-<nn>-<slug> — page: <exact page/URL>, state: <what must be configured/visible>, crop: <full page|region> -->`. The union of these comments is the Task 4/5 shot list.

- [ ] **Step 1: Write the full tutorial** with frontmatter `title: Tutorial — Sync an Event Type` and exactly these H2 sections (per spec): `What you're building`, `Prerequisites`, `Part 1: Set up the CMORE side`, `Part 2: Configure the Gundi destination`, `Part 3: Connect EarthRanger and choose what to share`, `Part 4: See it work`, `If something doesn't look right`. Field-admin voice: numbered steps, one action per step, each step names the exact button/field label as verified in Task 2 (CMORE) or flagged as SHOT-comment-to-verify (Gundi/ER). Prerequisites includes a callout box `> **Ask your CMORE administrator for:**` populated from the Task 2 boundary table. Part 2 walks Authenticate (API Token, API Base URL, Owner Group ID — copied field names from `configuration.md`), running the executable Authenticate action, then the Deliver `event_type_to_tag` entry: Gundi event_type `rhino_carcass`, CMORE Tag Name `Rhino Carcass`, and 3–5 real field mappings taken from `rhino_carcass_schema.json` (e.g. `species` → `Species`, plus one value-mapping example like `b_3_months1_year` → `Calf` if present in the schema). `If something doesn't look right` is a 4-row symptom table (no tag on event / a field value missing / nothing arrives / credentials fail) each linking to the matching `troubleshooting.md` anchor.
- [ ] **Step 2: Add the ASCII/mermaid pipeline diagram** (ER → Gundi → CMORE, matching `index.md`'s diagram style) to `What you're building`.
- [ ] **Step 3: Link it from `index.md`** — add `- [**Tutorial: sync an event type**](tutorial-sync-event-type.md) — step-by-step, with screenshots, for site administrators.` to the `## Next` list.
- [ ] **Step 4: Self-check against the spec** — every spec-outline bullet has a section; no TBDs; every image ref either exists in `docs/images/` or has a SHOT comment. Fix inline.
- [ ] **Step 5: Commit** — `git add docs/tutorial-sync-event-type.md docs/index.md && git commit -m "docs: field-admin tutorial for syncing an event type to CMORE (screenshots pending)"`.

### Task 4: Gundi portal capture session

**Files:**
- Create: `docs/images/gundi-*.png`
- Modify: `docs/tutorial-sync-event-type.md` (replace SHOT comments with real refs; correct any prose the UI contradicts)

**Interfaces:**
- Consumes: SHOT comments with system `gundi`; Gundi portal URL + login from operator. Test resources: connection `https://gundiservice.org/connections/b729de34-934f-4d3c-a398-ea668c688374`, CMORE destination config `https://gundiservice.org/connections/b729de34-934f-4d3c-a398-ea668c688374/destinations/b80e6781-40c3-4887-9d31-3ad0fb628423/configuration`.
- Produces: all `gundi-*` images; tutorial Part 2 + Part 3 verified against the live portal.

- [ ] **Step 1: Log in to the Gundi portal** (Playwright, 1440×900) and locate the existing CMORE destination integration and the ER↔CMORE connection.
- [ ] **Step 2: Capture every `gundi-*` SHOT** in list order: integrations list, create/select CMORE destination, Authenticate form (token masked/dummy), Authenticate run result, Deliver form with the `rhino_carcass` mapping entry expanded, connection/routing view, ER integration's event-type filter. If a screen differs from the tutorial prose, fix the prose immediately.
- [ ] **Step 3: Verify & commit** — no real token visible in any shot; all `gundi` SHOT comments replaced. `git add docs/images/gundi-*.png docs/tutorial-sync-event-type.md && git commit -m "docs: Gundi portal screenshots; verify tutorial against live portal"`.

### Task 5: EarthRanger capture session

**Files:**
- Create: `docs/images/er-*.png`
- Modify: `docs/tutorial-sync-event-type.md`

**Interfaces:**
- Consumes: SHOT comments with system `er`; ER site URL + login from operator.
- Produces: all `er-*` images; a real Rhino Carcass test event reported in ER (its ER event ID recorded in scratchpad notes for Task 6).

- [ ] **Step 1: Log in to the ER test site** (Playwright, 1440×900), open the report menu, confirm Rhino Carcass is reportable.
- [ ] **Step 2: Report a real test event** — title it clearly as a test (e.g. "TEST — tutorial walkthrough"), set a location, fill the mapped fields with values the Deliver mapping covers (including one that exercises a value mapping). Capture the filled report form (`er-01-…`) and the saved event (`er-02-…`). Record the event ID/serial in scratchpad notes.
- [ ] **Step 3: Verify & commit** — `git add docs/images/er-*.png docs/tutorial-sync-event-type.md && git commit -m "docs: EarthRanger screenshots for field tutorial"`.

### Task 6: End-to-end verification + "what it looks like when done" shots

**Files:**
- Create: `docs/images/cmore-<nn>-delivered-event.png`, `docs/images/cmore-<nn>-delivered-tag.png`
- Modify: `docs/tutorial-sync-event-type.md` (Part 4 + hero image in `What you're building`)

**Interfaces:**
- Consumes: the Task 5 test event; polling interval from the ER integration config (seen in Task 4).
- Produces: proof the flow works; final images for Part 4.

- [ ] **Step 1: Wait one polling interval**, then open CMORE and find the delivered event (map pin and/or feed).
- [ ] **Step 2: Capture** the event in CMORE showing (a) the event with description + location, (b) the populated Rhino Carcass tag fields, (c) the deep-link comment back to ER. Use these for Part 4 and reuse the best one as the `What you're building` hero image.
- [ ] **Step 3: If the event does not arrive or the tag is missing**, STOP and diagnose using `docs/troubleshooting.md` (check the integration's activity logs in the Gundi portal); fix config (not code) and re-run from Task 5 Step 2. The tutorial may not ship claiming a flow that didn't demonstrably work.
- [ ] **Step 4: Verify & commit** — Part 4 prose matches exactly what was observed. `git add docs/images/ docs/tutorial-sync-event-type.md && git commit -m "docs: verified E2E rhino-carcass delivery; final tutorial screenshots"`.

### Task 7: Marp slide deck + PDF render

**Files:**
- Create: `docs/slides/cmore-er-sync.md`, `docs/slides/cmore-er-sync.pdf`

**Interfaces:**
- Consumes: the finished tutorial and its images (relative path from `docs/slides/` is `../images/…`).
- Produces: 15–20 slide Marp deck + rendered PDF.

- [ ] **Step 1: Write the deck** with Marp frontmatter (`marp: true`, `paginate: true`, `size: 16:9`). Slide sequence: (1) title "Sharing EarthRanger events with CMORE", (2) what-you're-building diagram + hero shot, (3) prerequisites / ask-your-admin box, (4–6) CMORE side, (7–11) Gundi side (create, authenticate, verify, mapping ×2), (12–13) ER connect + filter, (14–16) see-it-work (report in ER, arrives in CMORE, tag detail), (17) if-something-looks-wrong symptom table, (18) where to get help (links to Pages docs). One screenshot per step slide, 2–3 bullets max.
- [ ] **Step 2: Render** — `npx -y @marp-team/marp-cli docs/slides/cmore-er-sync.md --pdf --allow-local-files -o docs/slides/cmore-er-sync.pdf`. Expected: exits 0, PDF exists.
- [ ] **Step 3: Visually verify** — open/Read the PDF (spot-check ≥4 pages): images render, nothing overflows, slide count 15–20.
- [ ] **Step 4: Commit** — `git add docs/slides/ && git commit -m "docs: Marp slide deck for CMORE/ER sync walkthrough"`.

### Task 8: Final review pass

**Files:**
- Modify: `docs/tutorial-sync-event-type.md`, `docs/slides/cmore-er-sync.md` (fixes only)

- [ ] **Step 1: Link/image audit** — every `![](images/…)` target exists (`grep -o 'images/[^)]*' docs/tutorial-sync-event-type.md | while read f; do test -f "docs/$f" || echo "MISSING $f"; done`); no leftover `<!-- SHOT` comments; `index.md` link resolves; troubleshooting anchors exist.
- [ ] **Step 2: Credential sweep** — `grep -riE 'vhxwr|Fork table|Handle8im|password' docs/` returns nothing; re-inspect any screenshot showing a form with a token field.
- [ ] **Step 3: Field-admin read-through** — read the tutorial start to finish as the target reader; every step is one action with an exact label; jargon only where the UI itself uses it. Fix inline.
- [ ] **Step 4: Commit & wrap up** — `git add -A docs/ && git commit -m "docs: final review fixes for CMORE/ER field tutorial + deck"`. Then summarize deliverables and hand back for user review / PR decision.
