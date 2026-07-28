# Field-User Guide: Sync an EarthRanger Event Type to CMORE — Design

**Date:** 2026-07-28
**Status:** Approved

## Purpose

The existing documentation serves two audiences well — integration engineers
(GitHub Pages: `index.md`, `configuration.md`, `troubleshooting.md`) and
decision-makers (`executive-brief-cmore-integration.md`) — but there is no
guide for the people who will actually configure and operate the pipeline in
the field: conservation-org admins who are comfortable clicking through the
Gundi portal, EarthRanger, and CMORE, but who don't read JQ filters or run
CLIs.

This project produces a screenshot-driven walkthrough (doc + slide deck)
showing how to configure **CMORE + EarthRanger + Gundi to sync one Event Type
end-to-end**, using **Rhino Carcass** (`rhino_carcass`) as the concrete
example. It also closes a real knowledge gap: what the CMORE-side
configuration looks like to an end user, and where self-service ends and
CMORE/CSIR admin support begins (e.g. subscribing a share group to the
Wildlife tag domain required CMORE team support during testing).

## Audience

- **Primary:** field/ops administrators at conservation organizations setting
  up the integration for their site.
- **Secondary:** semi-technical partners (CMORE operators, ER site admins)
  who need to understand their side of the handshake.
- **Explicitly not:** integration engineers (they have `configuration.md`).

Writing rules that follow from this: no JQ, no JSON schemas, no CLI internals.
The `scaffold-mapping` CLI is mentioned once, as an optional "ask your
integration engineer to generate this for you" shortcut. Every step is a
portal action with a screenshot.

## Deliverables

All live in this repo:

1. **`docs/tutorial-sync-event-type.md`** — "Sync an EarthRanger Event Type
   to CMORE", a step-by-step tutorial joining the existing GitHub Pages nav
   (linked from `index.md`). Structure:
   - **What you're building** — one diagram (ER → Gundi → CMORE), one
     paragraph, one "what it looks like when done" screenshot (the rhino
     carcass event pin in CMORE with its tag populated).
   - **Prerequisites** — accounts/access needed on all three systems, plus an
     honest callout box listing what must be requested from a CMORE/CSIR
     admin (informed by the investigation below): e.g. tag-domain visibility
     for your share group, service/instance provisioning, anything else the
     live exploration reveals as non-self-serve.
   - **Part 1: CMORE side** — create/locate the service, obtain the API
     token, identify the share group (Owner Group ID), confirm tag
     visibility.
   - **Part 2: Gundi side** — create the CMORE destination integration,
     fill in Authenticate (token, base URL, owner group), run the executable
     Authenticate action to verify credentials, configure the Deliver
     `event_type_to_tag` mapping for `rhino_carcass` (tag name + field
     mappings + value mappings), via the portal form.
   - **Part 3: EarthRanger / routing side** — the ER integration + connection
     in Gundi, event-type filtering (share only `rhino_carcass`), polling
     schedule.
   - **Part 4: See it work** — report a rhino carcass event in ER, watch it
     arrive in CMORE classified with the Rhino Carcass tag and the deep-link
     comment.
   - **If something doesn't look right** — a short symptom table (event
     arrives without tag, value dropped, nothing arrives) linking into the
     existing `troubleshooting.md` for detail.
2. **`docs/slides/cmore-er-sync.md`** — Marp slide deck (~15–20 slides)
   distilled from the tutorial: title, the "what you're building" picture,
   one slide per major step (screenshot + 2–3 bullets), a prerequisites/ask-
   your-admin slide, and a "where to get help" closer. Rendered to PDF with
   marp-cli; the markdown source is the artifact of record.
3. **`docs/images/`** — screenshots shared by doc and deck. Naming:
   `<system>-<nn>-<slug>.png` (e.g. `gundi-03-authenticate-form.png`,
   `cmore-02-share-group.png`). Captured via Playwright at a consistent
   viewport (1440×900), annotated (arrows/boxes) only where a click target is
   genuinely ambiguous.
4. **CMORE self-serve vs. admin boundary findings** — folded into the
   tutorial's Prerequisites section (not a separate doc). Documents what a
   CMORE end user can do themselves vs. what requires CMORE/CSIR support,
   generalizing the Wildlife-tag-domain episode.

## Workflow (hybrid: explore CMORE live first, doc-first for the rest)

1. **Mine the CMORE reference material** in `docs/` (Portal User Guide PDF,
   API Reference, Collaboration PDF, 2017 training deck) for portal concepts
   and terminology — treated as background, not ground truth, since they may
   predate the current portal.
2. **Live CMORE portal exploration + capture** — walk the CMORE test instance
   UI as an end user: services/API keys, share groups, tag domains, the map
   and event views. Capture screenshots as we go and record the self-serve
   vs. admin boundary. This is exploration of the genuine unknown, done
   before writing.
3. **Write the full tutorial** with a placeholder + shot-list entry for every
   remaining screenshot (exact page, state, crop).
4. **Gundi portal + ER capture sessions** — one focused Playwright session
   per system, following the shot list.
5. **Assemble** — slot images into the doc, distill the Marp deck, render the
   PDF, link the tutorial from `index.md`.

### Inputs required from the user (before step 2/4)

- CMORE test instance URL + login (or an authenticated session).
- Gundi stage portal URL + login, and the existing ER↔CMORE test connection.
- EarthRanger test site URL + login (with the Rhino Carcass event type
  available for reporting).

## Screenshot conventions

- Playwright-driven, 1440×900 viewport, default browser chrome excluded.
- Test-system data only; scrub/avoid anything sensitive (real tokens are
  never shown — the token field is captured masked or with a dummy value).
- Named and numbered per system so the doc and deck reference the same files.

## Error handling / accuracy safeguards

- Every configuration claim in the tutorial must be verified against the live
  systems during capture (the UI is ground truth, not the PDFs or existing
  docs).
- The end-to-end flow (Part 4) is actually executed once — a real test event
  reported in ER and observed in CMORE — before the doc claims it works.
- Where the portal-rendering workaround (list-shaped mappings, GUNDI-5371)
  surfaces in the UI, the tutorial describes what the user sees today,
  without explaining the bug.

## Out of scope

- GPS track / observation (GNode) sync — this guide covers events only; a
  follow-up guide can reuse the structure.
- Webhook-based flows, JQ transforms, dynamic schemas.
- Changes to the integration's code or config models.
- Publishing pipeline changes beyond adding the page + deck to the existing
  GitHub Pages structure.

## Success criteria

- A field admin with accounts on all three systems can follow the tutorial
  start-to-finish and see a tagged rhino-carcass event in CMORE without
  asking an engineer anything except the items the Prerequisites box tells
  them to request.
- The Prerequisites section answers "what do I need to ask the CMORE team
  for, and what can I do myself?" concretely.
- The deck presents the same flow in ~15–20 slides with one screenshot per
  step slide.
