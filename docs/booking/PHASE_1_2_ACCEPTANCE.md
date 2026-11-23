# Phase 1 and Phase 2 foundation acceptance

Validated locally on 2026-11-22/23 on `codex/ai-booking-agent`, a child of the
Phase 0 commit `bba18cc` on `codex/booking-editable-calendars`.

## Automated checks

- Full regression: `manage.py test player booking calendar_integrations` —
  **154 tests passed** before the final browser-driven input/layout corrections.
- Targeted recheck after those corrections: `manage.py test
  calendar_integrations.test_events booking.test_connections` — **36 tests passed**,
  including a new microsecond/date-time input regression. Current total: 155 tests.
- `manage.py check`, `makemigrations --check --dry-run`, and `git diff --check` pass.
- Disposable online clone of the existing SQLite DB: forward migration, reverse
  Booking/Calendar to Phase 0, then forward again passed. Existing player-table
  rows compared equal before/after; SQLite integrity and foreign keys passed.
  The working database was never migrated or edited.

Covered: independent Calendar and Booking roles; staff/cross-band denial;
Booking-OFF Calendar CRUD; idempotency; stale edits; CSRF; all-day/DST validation;
ICS immutability; no page-load network sync; Google pagination/tombstones/410;
partial sync failure; private/free-busy masking and ACL downgrade scrubbing;
recurrence/invitation restrictions; live ACL checks; If-Match; quota backoff;
revocation; uncertain-write reconciliation; separate grants; PKCE/state replay,
session/band/expiry/generation checks; stale callbacks after disconnect;
encrypted tenant/purpose binding; credential rotation; HTML/log redaction;
explicit queued connection checks and per-attempt usage; model allowlist;
disabled/revoked/rotated jobs; mailbox uniqueness; budget reservation, preflight,
settlement, midnight rollover and two simultaneous requests competing for a cap.
All provider responses are mocked. No paid or live provider tests were performed.

## Browser inspection

Used a separate temporary database/media/static directory and synthetic band/user/
event records at localhost. Inspected Calendar month view, event details/editor,
Booking connections and usage. Found and fixed unsupported six-digit fractional
seconds causing blank date-time inputs, simplified timed versus all-day controls,
and verified the Calendar toolbar no longer clips at the previously failing
843-pixel width. Added usage-table spacing and horizontal overflow protection.
No credentials were entered and no real provider actions were triggered.

## Implemented versus remaining

Phase 1 core is implemented; live Google OAuth/write acceptance remains outstanding.
Phase 2 credential onboarding, worker checks and budget services are implemented.
Paid inference handlers, message imports and their consent/retention/export controls,
research/discovery, richer usage reporting and reviewed outreach remain later work.
No email send route exists. See the [six-phase plan](IMPLEMENTATION_PLAN.md).

Production migrations, deployment, systemd/LXC staging, operator vault recovery,
HTTPS OAuth configuration, Google verification and explicitly approved provider
acceptance tests have not been performed. Local/mock acceptance is not production
approval. The original specification files remain unchanged under `spec/`.
