---
title: CMORE Admin Walkthrough — Questions
---

# CMORE-side setup: questions for the walkthrough

Use this while walking through the CMORE Web Portal and Admin Site with a
CMORE user or Organisation Admin. The goal is to pin down the exact
CMORE-side process so the [tutorial](tutorial-sync-event-type.md) and
[slide deck](slides/cmore-er-sync.md) describe what a reader will actually
see on screen.

Context: the CMORE team has asked that the guide state, as a standard
prerequisite, that each reserve creates a **dedicated CMORE account** for the
EarthRanger integration (e.g. `lapalala.earthranger`) and that the reserve's
**Organisation Admin** assigns the **Integration permission** to it. The CMORE
team does not do either step, but will assist if the admin gets stuck.

Capture screenshots as you go — the ones marked 📷 are missing from the guide.

---

## 1. Roles and terminology

- [ ] Is **"Organisation Admin"** a formal role name in CMORE? What does the
      UI call it (Manage Users, user profile, anywhere else)?

  Answer:

- [ ] How does someone become an Organisation Admin for a reserve — granted by
      the CMORE team, or by another admin in the organisation?

  Answer:

- [ ] What is the exact name of the **Integration permission** as it appears
      in the UI? (The guide currently infers it from the **Service** menu
      appearing in the Admin Site.)

  Answer:

---

## 2. Creating the dedicated account

Walk through **Admin Site → Manage Users → Register new User** together.

- [ ] 📷 Who can register a new user — the Organisation Admin, the user
      themselves via self-signup, or only the CMORE team?

  Answer:

- [ ] Does the account need a **real mailbox** (activation email, password
      reset)? If yes, what do they recommend for a non-person account — a
      shared or alias mailbox?

  Answer:

- [ ] Does the new account need to be **Approved** (the Approved column in
      Manage Users) before anything else can be done with it? Who approves?

  Answer:

- [ ] Is there a recommended or required **naming convention**, or is
      `<reserve>.earthranger` purely a suggestion?

  Answer:

- [ ] Does the account need any **T&C acceptance** or first-login step in the
      Web Portal before it can be used in the Admin Site?

  Answer:

---

## 3. Assigning the Integration permission

Walk through assigning the permission to the account just created.

- [ ] 📷 Exact click path: which screen, which control (checkbox, role
      dropdown, group membership)?

  Answer:

- [ ] Can the Organisation Admin do this alone, or does it need CMORE team
      involvement in practice?

  Answer:

- [ ] Does the permission take effect immediately, or does the account need
      to sign out and back in?

  Answer:

- [ ] Confirm the self-check: signed in as the dedicated account, the Admin
      Site shows the **Service** menu, and the account can create a service
      and view its API key.

  Result:

---

## 4. Share group and data access

- [ ] Who creates the **share group (organisation)** on the instance — still
      the CMORE team, or can an Organisation Admin do it?

  Answer:

- [ ] Who **adds the dedicated account to the share group** — the
      Organisation Admin or the CMORE team?

  Answer:

- [ ] The CMORE team's note says the dedicated account is used for "the
      relevant ShareGroup and data-access settings". Which of those settings
      does the Organisation Admin manage themselves, and which remain
      CMORE-team only?

  Answer:

- [ ] Tag domain access (e.g. **Wildlife**) — confirm this is still requested
      from the CMORE team, and confirm what they need in the request (tag
      names + share group).

  Answer:

- [ ] Where can the Organisation Admin read their **share group ID**? The
      guide currently says to copy it from an existing service's Target Group
      or ask the CMORE team.

  Answer:

---

## 5. Account lifecycle and the service token

The integration authenticates with the **service's Auth Token**, not the
account's password. The risk is a non-person account silently breaking the
token.

- [ ] Does CMORE enforce **password expiry**? If the dedicated account's
      password expires, does its service token keep working?

  Answer:

- [ ] Can the account be **locked for inactivity** (the Locked / Last Activity
      columns)? Does a locked account invalidate its services' tokens?

  Answer:

- [ ] Is a service **owned by the user who created it**, or by the share
      group? What happens to the service and token if the creating account is
      deactivated or deleted?

  Answer:

- [ ] Is MFA required or planned for portal accounts? Any impact on a shared
      non-person account?

  Answer:

---

## 6. Existing reserves set up under a personal account

- [ ] Do they expect existing integrations created under a staff member's
      account to be **migrated** to a dedicated account?

  Answer:

- [ ] If so, can an existing **service be transferred** to the dedicated
      account, or must it be recreated (new token, re-entered in Gundi)?

  Answer:

- [ ] Any deadline or preferred timing for that migration?

  Answer:

---

## 7. Support boundaries

- [ ] How should an Organisation Admin **contact the CMORE team** if they get
      stuck on account creation or the permission — email, ticket, named
      contact? The guide should give a concrete channel.

  Answer:

- [ ] Anything else the CMORE team wants the guide to say, or to stop saying?

  Answer:

---

## Follow-ups for the guide (fill in after the meeting)

- [ ] Add the Organisation Admin click path for the Integration permission,
      with screenshot.
- [ ] Replace the Manage Users screenshot with one showing a dedicated
      account.
- [ ] Move share group membership to the admin-owned list if the admin can
      manage it.
- [ ] Add a lifecycle note (password expiry / locking) if it affects tokens.
- [ ] Add a migration note for existing reserves if required.
- [ ] Add the CMORE team's support channel to the prerequisites box.

---

From meeting
