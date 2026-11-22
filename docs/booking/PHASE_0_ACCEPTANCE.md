# Phase 0 acceptance report

Date: November 22, 2026. Base: `18dc9fbbeee3201415e811977373541ac813f660`.
Phase 0 branch: `codex/booking-editable-calendars`.

**Foundation implemented and locally verified. Full Booking and editable Calendar
are not yet implemented. No production deployment or external integration calls.**

## Changed-file map

| Files | Change |
| --- | --- |
| `booking/models.py`, `permissions.py`, `context_processors.py` | Default-off per-band settings, membership-bound roles and navigation gate |
| `booking/forms.py`, `views.py`, `urls.py`, `templates/booking/` | Dashboard, profile setup and manager-only role UI |
| `calendar_integrations/models.py`, `permissions.py`, `forms.py`, `views.py`, `urls.py`, `templates/` | Independent roles, local/Google source metadata, source setup and write policy; Google enablement fails closed |
| `player/group_permissions.py`, `group_feature_views.py`, `models.py` | Shared authorization, audited role changes, staff feature/initial-manager controls, audit model |
| `player/views.py` | Atomic audited membership removal, cascading revocation of both feature roles |
| `player/context_processors.py`, `urls.py`, `templates/player/`, `static/player/css/style.css` | Existing group/sidebar/calendar integration and consistent form styling |
| `bandmate/settings.py`, `urls.py` | Register apps, context processor and URLs |
| `booking/management/commands/`, `calendar_integrations/management/commands/` | Inert worker/scheduler/check/Google sync commands |
| `ops/systemd/`, `ops/install-feature-units.sh`, `ops/backup_sqlite.py` | Five optional unit templates, safe installer and verified private SQLite online backup |
| `deploy-service.sh`, `update-service.sh`, `.gitignore` | Prevent existing-install overwrite; pinned, locked, fail-fast backup-aware updates; ignore worker lock |
| `booking/tests.py`, `booking/test_operations.py`, `calendar_integrations/tests.py` | 41 new automated tests, with additional matrix cases inside tests |
| `docs/booking/`, `docs/CODEX_HANDOFF.md`, `README.md` | Architecture, plan, operations, future OAuth/security contracts and supplied source specs |

## Migrations

- `player/migrations/0023_groupaccessaudit.py`
- `booking/migrations/0001_initial.py`
- `calendar_integrations/migrations/0001_initial.py`

Additive tables only. No automatic group enablement/role grants. No alterations to
ICS source/event, song or setlist tables. The working `db.sqlite3` was not migrated
during development; run `manage.py migrate` when reviewing the app locally.

## Executed verification

| Check | Result |
| --- | --- |
| Baseline `manage.py test player` before changes | 46 passed |
| Final combined suite (command below) | **87 passed**, 70.672 seconds, zero system-check issues |
| `manage.py check` | Passed |
| `manage.py makemigrations --check --dry-run` | No changes detected |
| `git diff --check` | Passed |
| `bash -n deploy-service.sh update-service.sh ops/install-feature-units.sh` | Passed |
| Python compilation of new apps/helpers | Passed |
| Forward and reverse migrations on disposable online copy of local SQLite DB | Passed; every existing player table's row count preserved |
| Browser review on localhost with synthetic users/data | Login/group selection, Booking dashboard/setup and Calendar settings rendered; form spacing corrected and rechecked |
| Unit rendering into temporary directory | Five units rendered, repeat install safe, customized-unit overwrite rejected |
| Simulated updater | Success restores prior services; dirty tree/backup/dependency/check/migration/static failures abort without starting services; no merge before a verified backup |

```sh
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test booking calendar_integrations player --verbosity 1
```

The existing local `staticfiles/` warning appears in Django tests because collected
static assets are absent; tests pass and the preview uses Django's development
static serving. No tests use production services, user song files, paid APIs or
real OAuth credentials. The temporary preview server was stopped after review.

Security coverage includes two bands/shared membership, staff-only privacy,
disabled Booking, stale role/source/grant checks, explicit per-calendar opposite
write policies, cross-band IDs, role separation, membership removal, last-active-
manager protection, CSRF and escaped source names. Google checks test local policy
metadata only; they are not provider CRUD/consent tests.

## Outstanding prerequisites / next milestone

**Next: Phase 1 Calendar event CRUD and actual Google OAuth/sync.** Local event
tables/editor, provider adapters, live permission checks, encrypted credentials,
ETags, recurrence, idempotency and Booking overlays are not present yet. Gmail,
OpenAI keys/budgets, discovery, outreach and usage reporting follow in Phases 2–4.

Linux LXC/systemd installation and staging rollback have not been exercised.
Google verification, consent/test accounts, separately secured master-key backups
and real connector tests remain pending. See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)
and [INSTALL_UPDATE.md](INSTALL_UPDATE.md). No deployment is implied by this report.
