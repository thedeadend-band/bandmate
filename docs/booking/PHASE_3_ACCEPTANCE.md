# Phase 3 core acceptance — 2026-11-23

Historical core checkpoint, committed in `08f03c2`. The subsequent search,
geocoding, public artist and AI work is covered by
[current Phase 3 discovery acceptance](PHASE_3_DISCOVERY_ACCEPTANCE.md).

Branch: `codex/ai-booking-agent`, child of committed Phase 0 `bba18cc`.
This checkpoint includes Phase 1/2 and Phase 3 core work. No deployment, workspace DB migration,
live mailbox read, paid inference or outbound email was performed.

## Delivered in this increment

- New `booking/0003_research_records_and_imports` migration: records/evidence,
  aliases/suppressions, reminders, research scopes/candidates, mail references,
  schedules, geography preferences and scope-bound Gmail history.
- Record forms/views/templates and services: same-band relationships, stale edit
  protection, annual editions, reviewed evidence, merges and stable identity export.
- Targeted Gmail import adapter and checkpointed worker handler; preview/approval,
  history delta/404 recovery, caps, deletion/retention/export and review queue.
- Public HTTPS source fetching, robots/DNS/redirect limits, JSON-LD proposals,
  human review, unchanged-evidence dedupe and changed-evidence review.
- Blacklist/opt-out management, suppression across aliases and linked records;
  scored/filterable opportunities using manual coordinates and known facts.
- Dashboard, follow-ups and private read-only Booking Calendar overlays.
- Explicit weekly source schedules, daily job quotas, local expiry cleanup and
  updated operations/install/onboarding documentation.

Core code map: `booking/models.py`, `record_forms.py`, `record_views.py`,
`research_forms.py`, `research_views.py`, `presentation.py`, `services/records.py`,
`services/gmail.py`, `services/imports.py`, `services/discovery.py`,
`services/research_jobs.py`, `services/ranking.py`; Booking templates plus
`player/views.py` and the existing Calendar template.

## Executed validation

```sh
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test player booking calendar_integrations
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

- Full regression: **196 tests passed**, including 41 Phase 3 tests. Coverage
  includes tenant/role/feature/CSRF gates, stale revisions, evidence/date rules,
  email dedupe, cross-year merges, alias suppression and opt-out resolution;
  import scopes, pagination/history fallback/retry/checkpoints/caps, credential
  revocation, cancellation, retention, export isolation, public-source safety,
  scheduler limits and unchanged/changed evidence handling.
- Django system check passed; no migration drift; whitespace check passed.
- Disposable online SQLite clone: forward all migrations, reverse Booking to
  `0002`, forward to `0003`; pre-existing player table contents unchanged, SQLite
  integrity and foreign-key checks passed. Source DB opened read-only.
- Browser on a synthetic temporary database: dashboard deadlines/reminders,
  opportunity evidence, reminder creation, candidate acceptance into directory,
  public-source preview awaiting consent with zero processed results, and Calendar
  displaying both follow-ups plus verified deadline as read-only Booking entries.
  Temporary preview server stopped afterward.

The suite emits the existing missing `staticfiles/` development-directory warning;
there are no test failures. Preview used collected static files in a temporary root.

## Remaining gates and scope

This is **Phase 3 core**, not the full autonomous discovery scope. Licensed web
search and geocoding implementations, broader nearby-artist research, advanced
genre/history scoring and budgeted/consented AI extraction remain. See the
[plan](IMPLEMENTATION_PLAN.md) and [research behavior](RESEARCH_AND_RECORDS.md).

Before live use: authorized read-only Gmail account and Google verification,
configured secure vault/client registration, approved public sources/provider
terms, and staging LXC worker/scheduler/backup/retention checks. Mocked provider
success does not establish live acceptance. New migrations must be applied to
the chosen environment before opening these pages; production approval remains
separate. Phase 4 adds reviewed outreach and explicit calendar publication.
