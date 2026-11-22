# Implementation plan

## Phase 0 — current increment

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

## Phase 1 — next, core Calendar editing

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

## Phase 2 — credentials and Gmail

Per-band OpenAI vault/key test/rotation, model allowlist, budget reservations and
estimated usage ledger; no fallback. Separate Gmail OAuth/grants, mailbox identity
and duplicate-mailbox policy, scoped import interfaces and consent/retention UI.
No send endpoint yet. Test two independent keys/accounts and log/HTML redaction.

## Phase 3 — discovery

Tenant-scoped contact/venue/series/opportunity/evidence models; historical Gmail
and delta imports; dedup/aliases, persistent blacklist and opt-out suppression;
bounded safe source fetch/search/geocoding adapters; verified versus predicted
deadlines; Booking overlays. Replace stubs with durable SQLite-safe jobs, leases,
quotas, backoff, scheduling and per-band enablement checks before external calls.

## Phase 4 — reviewed outreach

Versioned templates, evidence-grounded drafts and preview/edit; manager-only
explicit send with CSRF, suppression checks, immutable approved content and
idempotency/ambiguous-outcome reconciliation. Reply tracking and reminders.
Explicit publish/update/unlink/remove calendar actions with both role gates,
stable publication links and deduplication. Usage/activity dashboards and exports.

## Phase 5 — optional

Confirmed-gig setlists/EPK/rehearsal helpers; public-only sharing after proven
isolation. Social adapters only if supported and authorized. Database/queue
scaling only in response to measured contention.

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
The legacy ICS behavior is intentionally deferred to the core Calendar phase.
