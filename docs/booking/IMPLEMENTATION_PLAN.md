# Implementation plan

There are **six phases, numbered 0–5**. Phase 0 is committed on
`codex/booking-editable-calendars` (`bba18cc`); work continues on its child branch
`codex/ai-booking-agent`. See [Phase 1–2 acceptance](PHASE_1_2_ACCEPTANCE.md)
and [Phase 3 acceptance](PHASE_3_DISCOVERY_ACCEPTANCE.md).
Phase 4 is committed as `1091d97`; see [Phase 4 acceptance](PHASE_4_ACCEPTANCE.md).
Current work: [Phase 5 acceptance](PHASE_5_ACCEPTANCE.md).

## Phase 0 — committed foundation

- [x] Inspect exact HEAD, memberships, calendar, templates, tests, scripts and working tree.
- [x] Create `codex/booking-editable-calendars` from clean `18dc9fb`.
- [x] Booking app, disabled-by-default settings and explicit membership-bound roles.
- [x] Independent Calendar roles, local/Google source metadata and default-off write policy.
- [x] Staff feature toggle/first-manager designation, member role management, audit.
- [x] Conditional sidebar, credential-free dashboard/setup and Calendar configuration.
- [x] Dedicated inert worker/scheduler/Google-sync commands and opt-in unit installer.
- [x] Fail-fast pinned updater, private online SQLite backups and fresh-install protection.
- [x] Isolation, role, source-policy, command and deployment failure-path tests.
- [ ] Production rollout (not authorized or performed).

See [PHASE_0_ACCEPTANCE.md](PHASE_0_ACCEPTANCE.md) for executed checks.

## Phase 1 — implemented, awaiting live-provider acceptance

1. Add local event model and audited CRUD with timezone/all-day validation; combine
   ICS/local/Google events and filters on existing `/calendar/`. Add accessible
   detail/create/edit/delete UI and explicit target selection.
2. Implement authenticated encryption before accepting Calendar refresh tokens;
   group/user/nonce-bound, expiring single-use OAuth state and PKCE. Separate
   Calendar callback; read-only first, explicit per-calendar scope elevation.
3. Implement CalendarList selection, live ACL checks, shared-calendar warning,
   existing external-event CRUD, private-event restrictions, provider-first or
   tracked-outbox writes, ETags and durable idempotency. Initially disallow
   recurrence edits if exact instance/series behavior is not proven.
4. Implement paginated incremental sync/410 recovery and tombstones in dedicated
   command. No new sync on HTTP render; remove existing ICS sync-on-render and
   sync-all on source-save in favor of its timer.
5. Verify Booking-OFF editing, independent accounts with opposite write policies,
   stale permissions, revocation, conflicts, recurrence, DST/all-day and retries.

## Phase 2 — credential onboarding and budget foundation implemented

Encrypted per-band OpenAI key save/replace/remove, background model-list tests,
operator model allowlist, token/request/cost settings, atomic budget reservation
and preflight services, per-attempt estimated usage ledger and usage page. Separate
Gmail OAuth, mailbox identity/uniqueness, PKCE, connection checks and disconnect.
No provider fallback. Paid inference is added in Phase 3 and reviewed sending in
Phase 4. Mocked tests
cover two bands, role/feature revocation, stale generations, log/HTML redaction,
rotation, backoff and simultaneous budget reservations.

Phase 3 now supplies message import preview/consent, retention/export/deletion.
Phase 3 extraction uses budget state and token caps before any paid call.
Phase 4 adds charts/category breakdowns and usage export. Live credentials,
OAuth/billing and Google verification remain operator gates.

## Phase 3 — implemented, awaiting live-provider and staging acceptance

- [x] Band-scoped organizations, venues, contacts, series, annual opportunities,
  source evidence, optimistic editing, manual merges and stable identity exports.
- [x] Targeted Gmail preview/approval, bounded metadata/snippet import, progressive
  checkpoints, history deltas/404 recovery, deletion tombstones and retention.
- [x] Explicit public-URL research using robots-aware, DNS-pinned HTTPS fetching;
  JSON-LD event extraction and manual review before creating records.
- [x] Durable aliases, blacklists and opt-outs that survive rename/merge/research;
  restoring visibility does not clear contact restrictions.
- [x] Published application dates require evidence; predicted windows stay separate;
  next-year editions never inherit last year's dates.
- [x] Manual coordinates, straight-line radius/excluded-city filtering and explained
  priority/distance/deadline scores; unknown locations remain visible.
- [x] Dashboard, reminders and read-only overlays on the existing Calendar.
- [x] Worker quotas, cancellation/revocation checks, explicit periodic public-source
  revisits and local retention cleanup, including while Booking is OFF.
- [x] Optional Brave Search and OpenCage adapters with explicit query approval,
  global/per-band quotas, reviewed coordinates and provider provenance.
- [x] Public artist gig research and separate reviewed artist/gig records;
  saved-genre and documented booking-history/responsiveness scoring.
- [x] Evidence-grounded AI extraction with separate per-candidate consent,
  versioned pricing, budget reservation, token bounds and uncertain-call accounting.
- [ ] Live-provider and staging LXC acceptance; current tests use mocked providers.

See [research behavior and limits](RESEARCH_AND_RECORDS.md) and
[discovery provider setup](DISCOVERY_PROVIDERS.md). Search, source fetching,
coordinate changes and AI disclosure each require their own reviewed approval.

## Phase 4 — implemented, awaiting live-provider and staging acceptance

- [x] Versioned templates and reviewed placeholder-based drafts; optional budgeted
  AI selection/ordering of existing paragraphs without invented claims.
- [x] Manager-only explicit send, separate Gmail send OAuth, immutable content and
  identity snapshots, recipient limits and no replay after uncertain transmission.
- [x] Exact Sent-mail reconciliation, scoped reply checks/retention, same-thread
  follow-up drafts, manual reminders and opportunity outreach history.
- [x] Explicit add/update/unlink/relink/remove calendar publication with both roles,
  stable links, optimistic versions and fresh worker-side Booking checks.
- [x] Model/category/daily usage, remaining budget, jobs and tenant-scoped exports.
- [ ] Live-provider and staging acceptance; no real sends or paid tests performed.

See [outreach behavior and limits](OUTREACH.md). AI arrangement, metadata-only
explicit reply reads and plain-text messages are deliberate initial boundaries.

## Phase 5 — gig preparation implemented, awaiting staging acceptance

- [x] Confirmed-gig workspace, private preparation notes, same-band setlist links
  and explicit setlist creation using the existing owner/editor/player workflow.
- [x] Manager-reviewed EPK/resource links and explicit EPK selection into drafts,
  with copied URL/revision provenance and no automatic upload or sharing.
- [x] Rehearsal planning, practice setlists, completion/cancellation/reopening and
  private day reminders on Booking Calendar milestones.
- [x] Booking-role-gated navigation from Calendar event detail and setlist player;
  feature-off behavior, tenancy, stale edits and duplicate actions tested.
- [ ] Staging install/upgrade/restore and the existing live-provider release gates.

See [gig preparation behavior](GIG_PREPARATION.md). Shared public-only caches,
social adapters, hosted assets and database/queue scaling remain optional future
extensions, requiring separate design/provenance or measured load.

## Decisions and prerequisites

- Site admin enables Booking and designates the initial current-member manager.
- One Gmail mailbox per band initially; no shared mailbox across bands by default.
- Gmail snippets by default, manager-only deliberate drill-down later.
- Warn about reused OpenAI projects; do not claim key ownership/project billing
  isolation can be proven from a pasted key.
- Future geography defaults: 100 straight-line miles; current and next calendar
  years, prioritizing 2027 for initial research. Store predictions separately.
- No external credentials, paid calls or live accounts are needed for Phase 0.
- Before live integrations: operator supplies encryption key storage/backup path,
  HTTPS OAuth registrations/test accounts, consent/verification status and explicit
  permission for any paid test or email. Never supply secrets through chat.
- Linux systemd/LXC and provider acceptance remain separate staging/live gates.

## Baseline deviations and operational findings

HEAD matches the package; no uncommitted changes existed at start. Actual code
confirms that staff can select any Group, member roles were absent, ICS sync
deletes/recreates events and ran synchronously on page loads. The updater
continued after failures, and the fresh deployer could overwrite `.env` on an
existing install. The new updater/fresh-install guard address the latter two.
Phase 1 removed ICS sync-on-render/source-save; its independent timer now owns refresh.
