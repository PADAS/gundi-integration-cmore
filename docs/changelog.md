---
title: Changelog
---

# Changelog

What changed for reserves using the CMORE integration, newest first. Each entry
says what you will notice and anything you need to do. Engineering detail is in
the linked pull requests.

[← Overview](index.md) · [Configuration](configuration.md) · [Troubleshooting](troubleshooting.md)

---

## Unreleased

### Re-running *Create EarthRanger event types* keeps your edits

Before, a re-run rebuilt each generated event type's form from CMORE. That
undid shortened titles, hidden fields, conditional sections, fields you added
for EarthRanger only, and reworded dropdown options. Now a type belongs to the
reserve once it exists, and a re-run only **adds** what is new in CMORE. The
full rules are in
[Configuration → Re-runs keep the reserve's edits](configuration.md#re-runs-keep-the-reserves-edits).
([#49](https://github.com/PADAS/gundi-integration-cmore/pull/49))

New fields go at the end of a section that always shows, or in a "New from
CMORE" section, and are never made required. A type whose form EarthRanger
can't return is skipped and reported, not replaced.

Three side effects to know about:

- **Options CMORE removes stay selectable in EarthRanger.** A re-run used to
  switch off a dropdown option that CMORE had deleted. It no longer does,
  because it can't tell a CMORE option from one the reserve added. A ranger
  can still pick the old option, but CMORE no longer knows it, so **that field
  arrives in CMORE empty**. The only other sign is a warning in the runner
  log. Nobody is notified.
    - **What to do:** after each run, check `stale_options` in the result. For
      each option listed, switch it off in EarthRanger, or add a value mapping
      to the CMORE option that replaces it. See
      [Troubleshooting](troubleshooting.md#a-dropdown-value-arrives-in-cmore-empty).
- **Renamed CMORE tags and properties are no longer picked up.** A re-run
  leaves the type's name and field titles as they are on the site. Delivery
  is unaffected, because it matches on ids, not names. Rename the type in
  EarthRanger yourself if you want the new name.
- **Types created before this change may get one deleted field back.** Gundi
  now records which CMORE fields it built for each type, so a field you
  delete stays deleted. Types created earlier have no record yet, so their
  first re-run restores a field you deleted once. Delete it again and it stays
  gone.

## 2026-10-24

- **The overlap is flagged.** If the Deliver configuration already maps a tag
  from an event type you built by hand, *Create EarthRanger event types* warns
  that rangers will see both types. It lists them under `mapped_by_hand` in the
  result. ([#48](https://github.com/PADAS/gundi-integration-cmore/pull/48))
- **The portal forms are cleaner.** Developer notes no longer appear as field
  descriptions in the configuration forms, and the action has a proper title.
  ([#47](https://github.com/PADAS/gundi-integration-cmore/pull/47))

## 2026-10-23

- **New action: *Create EarthRanger event types*.** It builds an EarthRanger
  event type, with its fields and dropdown options, for each CMORE tag you
  pick, and those types deliver to CMORE without any manual mapping. Use
  Preview to see what it would create first.
  ([#43](https://github.com/PADAS/gundi-integration-cmore/pull/43),
  fixes in [#45](https://github.com/PADAS/gundi-integration-cmore/pull/45) and
  [#46](https://github.com/PADAS/gundi-integration-cmore/pull/46))
- **CMORE shows who reported the event.** The EarthRanger reporter's name goes
  into the tag's "Reported by" field, or into the source comment when the tag
  has no such field.
  ([#42](https://github.com/PADAS/gundi-integration-cmore/pull/42))

## 2026-10-22

- **Unmapped event types are no longer sent.** An EarthRanger event type with
  no CMORE tag mapping used to reach CMORE without a tag. It is now skipped,
  and the activity log shows a warning with `reason: event_type_not_mapped`.
  If you relied on such a type, map it or generate it with the new action.
  ([#40](https://github.com/PADAS/gundi-integration-cmore/pull/40))
