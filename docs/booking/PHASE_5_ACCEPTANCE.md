# Phase 5 acceptance — 2026-11-24

Branch: `codex/ai-booking-agent`, extending Phase 4 commit `1091d97`.
The gig preparation milestone is locally complete. See
[workflow and boundaries](GIG_PREPARATION.md). No live provider call, paid inference,
real email, workspace migration or production deployment was performed.

## Delivered

- Confirmed-gig directory and preparation page with private notes, same-band
  setlist selection, explicit creation of a band-visible setlist and player links.
- Existing setlist ownership, songs, dates, playback and export behavior retained;
  unlink/delete behavior and stale/duplicate-create protection verified.
- Manager-maintained resource library with safe web schemes, archived state,
  explicit outreach approval and same-band reviewed EPK selection in drafts.
- Exact copied EPK URL/identity/revision provenance without private resource notes;
  later resource changes do not mutate drafts or approved messages.
- Rehearsal planning/editing, practice setlists, complete/cancel/reopen transitions,
  duplicate-click protection and private Booking Calendar day reminders.
- Role-gated links from published Calendar events and the existing setlist player;
  Booking-disabled and ordinary-member views retain their existing functionality.

## Code and migration

- New `booking/gig_forms.py`, `gig_views.py`, `services/gigs.py`, `test_gigs.py`
  and `gig.html`, `gigs.html`, `resources.html` templates.
- Integrations in Booking models/routes/navigation/record details/presentation and
  outreach forms/service; Calendar event details and the player setlist view/template.
- Additive [`0006_gig_preparation`](../../booking/migrations/0006_gig_preparation.py):
  `BookingGigPlan`, `BookingResource`, `BookingRehearsal`.
- Updated plan, handoff, architecture, security, installation and operations docs.

## Executed validation

```sh
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test booking.test_gigs booking.test_outreach
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test player booking calendar_integrations
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

- **64 focused tests passed** in 4.928 seconds: 24 new gig preparation tests plus
  40 existing outreach tests.
- **294 full regression tests passed** in 93.728 seconds.
- System checks, migration-drift check and whitespace checks passed.
- Private disposable SQLite clone: migrate forward, reverse Booking to `0005`,
  then forward to `0006`. Legacy player rows unchanged; SQLite integrity and
  foreign-key checks passed. The workspace database was opened read only.
- Browser with synthetic temporary data: confirmed-gig list, save an existing
  setlist link/private notes, add a rehearsal, open its setlist player and see the
  Booking backlink, mark rehearsal complete, inspect the resource library, select
  a saved EPK/template and verify the exact URL in an editable draft.
- Screenshot evidence: `/tmp/bandmate-phase5-gig.jpg`. Preview server stopped after
  validation. No send or AI approval was submitted; no provider worker ran.

The existing missing `staticfiles/` warning appears in tests; the browser preview
uses a temporary collected static root. Browser checks used synthetic empty
setlists; actual audio playback was not exercised in this phase's browser session.

## Remaining work

The six-phase implementation plan is numbered 0–5. Planned local implementation
is now complete within the documented boundaries. Staging LXC install/upgrade/
restore, production rollout and authorized live OAuth/mail/calendar/AI acceptance
remain pending. The broader optional extensions listed in the specification
(shared public caches, social adapters, additional providers, hosted assets and
load-driven infrastructure scaling) remain deferred.
