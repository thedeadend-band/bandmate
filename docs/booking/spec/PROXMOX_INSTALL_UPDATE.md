# BandMate Booking and Editable Calendars — Proxmox Installation, Updates, and Operations

**Use with:** `BANDMATE_BOOKING_SPEC.md`\
**Audience:** Cortex/Codex implementer and BandMate server operator\
**Purpose:** integrate Booking **and opt-in editable Google/local calendars** with **existing BandMate** installation, not provision a separate application VM. Calendar editing works independently of Booking activation.

## 1. Observed current deployment (verify on real host)

The repository's `PROXMOX.md`, `deploy-service.sh`, and `update-service.sh` describe:

- Proxmox Debian **LXC** container (example number `200`, not a verified production CTID).
- Repository checked out at `/srv/bandmate`.
- Python virtual environment at `/srv/bandmate/.venv`.
- Environment file at `/srv/bandmate/.env`.
- SQLite `db.sqlite3` under project directory by Django settings.
- Gunicorn systemd unit `bandmate.service`.
- Existing `bandmate-calendar-sync.service` + `.timer` to refresh **read-only ICS calendars only**; these rows are not editable. Google Calendar editing requires distinct Google provider storage/sync.
- Optional Cloudflare Tunnel providing HTTPS to existing BandMate hostname.

**Do not:** create a fresh Docker Compose project, overwrite installed service, reset site data, rename the existing directory, change reverse proxy, or assume the sample CTID is the live ID. Verify current Proxmox host, LXC IDs and service status with the operator before any live deploy.

## 2. Target runtime layout

```
Existing Proxmox host
 └─ EXISTING BandMate LXC
     /srv/bandmate/
        bandmate/                    existing Django project
        player/                      existing audio/setlist/calendar
        booking/                     NEW Django app
        calendar_integrations/       NEW calendar extensions (or additive player modules)
        db.sqlite3                  current DB plus booking tables
        .venv/                       shared version-compatible Python environment
        .env                         current private runtime environment
     systemd:
        bandmate.service             existing Gunicorn
        bandmate-calendar-sync.timer existing ICS sync
        bandmate-google-calendar-sync.timer NEW incremental Google Calendar event sync
        booking-worker.service       NEW, persistent booking tasks
        booking-scheduler.service    NEW, one-shot due-job enqueue
        booking-scheduler.timer      NEW, ~15min tick
     outbound:
        api.openai.com               per-group key, HTTPS
        Gmail OAuth/API endpoints    per-group OAuth, HTTPS
        Google Calendar API/OAuth     separate per-group calendar OAuth, HTTPS
        legitimate public web APIs  search/site discovery
```

No separate external port; booking templates, OAuth callbacks, and editable-calendar pages use the existing BandMate HTTPS hostname. The Booking sidebar appears only to authorized members of enabled groups. Worker must not use OpenAI/Gmail secrets of another group.

## 3. Recommended host sizing

The AI inference is performed by OpenAI, not on LXC GPU. An initial single low-concurrency worker should run without a dedicated GPU. Monitor free RAM/disk/SQLite lock contention before adding workers or memory. Heavy web rendering/headless browser work should not be part of MVP; if later required, isolate it from primary audio processing and impose strict resource limits. The existing workload may already be memory-intensive due to audio libraries—do not assert a VM size without measuring it.

## 4. Safe operator preflight

Example commands **inside the actual existing BandMate LXC** (operator must confirm location/privileges):

```bash
cd /srv/bandmate
pwd
git status --short
git rev-parse HEAD
systemctl status bandmate --no-pager
systemctl status bandmate-calendar-sync.timer --no-pager
# Once calendar-editing units are deployed: systemctl status bandmate-google-calendar-sync.timer --no-pager
ls -l db.sqlite3 .env
.venv/bin/python manage.py check
.venv/bin/python manage.py showmigrations
```

If the server has uncommitted work, an unknown service config, missing backup or failing checks, do not run an automatic upgrade. Preserve current song media and other live files. Avoid entering any API key into shell history, Git, or Cortex chat.

## 5. Required backups BEFORE migration

Because current DB is SQLite and may be active, use SQLite's online backup API, not a raw `cp` of an in-use database. Safe sample inside container (adjust secured backup destination):

```bash
sudo mkdir -p /var/backups/bandmate
sudo chmod 700 /var/backups/bandmate
cd /srv/bandmate
BACKUP="/var/backups/bandmate/pre-booking-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
.venv/bin/python - "$BACKUP" <<'PY'
import sqlite3, sys
source = sqlite3.connect('/srv/bandmate/db.sqlite3')
try:
    dest = sqlite3.connect(sys.argv[1])
    try:
        source.backup(dest)
    finally:
        dest.close()
finally:
    source.close()
print('SQLite backup completed:', sys.argv[1])
PY
chmod 600 "$BACKUP"
.venv/bin/python - "$BACKUP" <<'PY'
import sqlite3, sys
c=sqlite3.connect(sys.argv[1]); print('integrity_check:', c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()
PY
```

Also back up files required for recovery: `.env`, **booking encryption master key** (once created), media uploads, group logos, template/resource attachments, and current service unit files; protect these separately with restricted permissions and encrypted off-host storage. Don't put encryption master key next to accessible unencrypted database backups. Prefer a Proxmox backup/snapshot in addition; a snapshot alone does not replace verified restorable application backups. Test restore on a staging clone.

## 6. Booking environment, keys and OAuth

Developer should provide example configuration and a `booking_check`/`booking doctor` command. The common `.env` may contain **only server-side infrastructure settings**, e.g. Google OAuth *client configuration*, encrypted-secret master key **location**, search provider defaults, and scheduling options.

**Never put all bands' OpenAI API keys in a single environment variable**. Each band manager pastes a distinct project API key into the Booking settings page; Django encrypts the key and stores ciphertext under that specific `Group`. Each band separately authorizes Gmail, and its refresh token is encrypted under the same versioned vault system. Each band also authorizes **Calendar separately** using its own encrypted Google Calendar OAuth credential. Gmail OAuth scopes do not confer Calendar permission. Existing ICS feeds use no OAuth.

**Editable Calendar consent policy:** On Google Calendar connect, request only read scopes (`calendar.events.readonly`, `calendar.calendarlist.readonly`) and present selected calendars as View only by default. When that band's `calendar_manager` switches a specific selected Google calendar to editable, trigger explicit incremental OAuth consent for `calendar.events` and set its internal `write_enabled` flag only after successful verification of token scope and provider `accessRole` (`writer`, `owner`, or `writerWithoutPrivateAccess` for non-private events). A Google write-scope token can technically write other calendars belonging to the account; **every BandMate write must still be allowlisted to the specifically enabled external calendar ID**. No token/scope changes just from opening Calendar. Bandmate-managed local calendars need no Google OAuth, but still require calendar editor/manager role. Do not use broad `calendar`/`calendar.acls` scope.

Calendar callbacks need their own HTTPS redirect URI, signed state binding Group/User/nonce/scopes, approval and error flows. Publish clear warnings if a shared external Google calendar is intentionally connected to more than one band. Protect event data, including private-event details and Google `writerWithoutPrivateAccess` restrictions. Never log access tokens or OAuth authorization codes.

OpenAI project per band for real independent usage/budget tracking: https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform

Gmail compliance: `gmail.readonly` is a restricted scope; a production app serving arbitrary external bands should expect Google's verification/security requirements. Follow https://developers.google.com/workspace/gmail/api/auth/scopes and don't falsely treat the developer's private test credentials as generally deployable to the public.

Google Calendar technical references: https://developers.google.com/workspace/calendar/api/auth, https://developers.google.com/workspace/calendar/api/v3/reference/calendarList, https://developers.google.com/workspace/calendar/api/guides/sync and https://developers.google.com/workspace/calendar/api/guides/version-resources. Multi-user external OAuth configuration may require Google's consent-screen/verification process; do not assume public production grants will work before approved.

## 7. Install/update of code (implementation contract)

**Cortex must modify and test the repo's real scripts**. These commands illustrate the operations; they are not a claim that `booking` code/commands exist yet.

1. Record `git rev-parse HEAD` and create verified DB/media/secret backups.
2. Deploy a reviewed Git commit/tag, install version-compatible `requirements.txt` additions to existing `.venv`.
3. Run `manage.py check`, tests and migration safety review. Apply Booking **and editable Calendar** migrations once; verify that existing player migrations remain applied. Preserve all existing ICS rows/sources and calendar look-and-feel.
4. Collect static with existing settings.
5. Deploy the booking worker and scheduler unit files **and `bandmate-google-calendar-sync.service`/`.timer`** and reload systemd.
6. Start background Booking and selected Google Calendar sync services; confirm Booking-disabled/unconfigured groups initiate **zero** external OpenAI and Gmail calls. A Booking-disabled band may still sync its independently authorized Calendar connections; disconnected/no-calendars bands must not produce Google API calls.
7. Restart `bandmate.service` only after validation and verify normal login, group switching, songs, setlists, **legacy ICS calendar + local event CRUD + Google view-only/editable modes**, wizard and Booking off/on flows.
8. Confirm `booking_check` status and error/usage dashboards; only then have an authorized band manager enter credentials and run explicit real integration tests.

Representative validation commands **once implemented**:

```bash
cd /srv/bandmate
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py test booking
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py booking_check
systemctl status bandmate booking-worker.service booking-scheduler.timer bandmate-google-calendar-sync.timer --no-pager
systemctl list-timers --all | grep -E 'bandmate-calendar|bandmate-google-calendar|booking-scheduler'
```

`booking_check` and units are required implementation outputs; do not run these as if they currently exist. Need to avoid running tests on production data that may mutate the live DB; Django normally creates a test DB but developer must verify no test uses external services or production files.

### Hardening the repo's existing updater

The currently inspected `update-service.sh` sets a `FAILED` flag but continues after some failures and attempts a service restart. That is unsuitable for larger booking-related schema/credential migrations. Update it to:

- Abort safely on failing preflight, backup, dependencies, migration or config checks (`set -euo pipefail`, explicit cleanup/traps as necessary).
- Detect and respect uncommitted changes, concurrent deployment locks, and pin a release SHA.
- Run DB integrity and a confirmed online backup before migration.
- Include `booking-worker`, `booking-scheduler`, **`bandmate-google-calendar-sync`** maintenance and health checks. Calendar timer must not be accidentally disabled when Booking feature is disabled.
- Avoid restarting the web process with incompatible code/migrations.
- Preserve `db.sqlite3`, `.env`, media, cache, user content and master key.
- Never use `git reset --hard`, force pull, destructive migration or delete-volume shortcuts as routine operations.

## 8. Suggested systemd units (templates, adjust service identity)

The developer should version unit templates in `ops/systemd/` and install via a controlled script. Sample **concept**, not final drop-in service until user and working directory/permissions verified:

```ini
# /etc/systemd/system/booking-worker.service
[Unit]
Description=BandMate Booking Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/srv/bandmate
EnvironmentFile=/srv/bandmate/.env
ExecStart=/srv/bandmate/.venv/bin/python manage.py booking_worker
Restart=on-failure
RestartSec=10
# Prefer User=bandmate and tightly scoped ReadWritePaths once verified.
# Never embed OpenAI keys or OAuth refresh tokens here.

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/booking-scheduler.service
[Unit]
Description=BandMate Booking Due Job Scheduler
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/srv/bandmate
EnvironmentFile=/srv/bandmate/.env
ExecStart=/srv/bandmate/.venv/bin/python manage.py booking_enqueue_due
```

```ini
# /etc/systemd/system/booking-scheduler.timer
[Unit]
Description=Schedule due BandMate Booking jobs

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
```

The worker must implement singleton protection under SQLite (process file lock, leases/idempotency), bounded retries, clear logs, shutdown cancellation and band-level pause. It must NOT reuse audio worker threads. If remote multi-process scale is needed, migrate to PostgreSQL plus a proper queue first.

### Google Calendar sync service and timer (new)

Cortex should add versioned and installable `bandmate-google-calendar-sync.service` and `.timer` using the same existing venv and project (not a new container). The service must run **only** the bounded calendar sync management command, never AI tasks or Gmail jobs. Example conceptual units:

```ini
# /etc/systemd/system/bandmate-google-calendar-sync.service
[Unit]
Description=BandMate Google Calendar Incremental Sync
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
WorkingDirectory=/srv/bandmate
EnvironmentFile=/srv/bandmate/.env
ExecStart=/srv/bandmate/.venv/bin/python manage.py sync_google_calendars
# Use the verified unprivileged BandMate service user / credential permissions.
```

```ini
# /etc/systemd/system/bandmate-google-calendar-sync.timer
[Unit]
Description=Sync opted-in Google Calendars in BandMate
[Timer]
OnBootSec=3min
OnUnitActiveSec=10min
Persistent=true
[Install]
WantedBy=timers.target
```

This is a **specification sample**, not a claim that `sync_google_calendars` exists yet. Only sync enabled accounts/calendars, isolate each band's errors, persist `nextSyncToken`, handle Google `410` safe per-calendar full resync, and ensure events changed directly in Google appear in the Bandmate view. Bandmate user-initiated create/update/delete must be protected by role + calendar `write_enabled` + Google OAuth scope + provider event permissions; write operations must use provider IDs, ETag conflict checking, and audit. Google write failures/revocation cannot block normal Bandmate operation. Do not run full Google sync on every `/calendar/` page request.

## 9. Rollback contract

A git checkout alone **cannot** reverse a migration that changes the schema. For every release, keep the commit SHA, DB snapshot, media changes and migration state together. On failure:

1. Stop new booking scheduled work and the worker (do not replay uncertain Gmail sends).
2. Determine whether migrations completed. If backwards compatible, return to prior code release only after confirming compatibility. Otherwise restore **matching DB backup** and related files on a staging clone first.
3. Restore compatible code, DB, encrypted-vault metadata and master key version together; restore media if affected.
4. Restart existing BandMate service and old calendar sync, then booking worker/timer **and Google Calendar sync timer** only after verification.
5. Check existing login, Group membership, songs, setlists, calendar, Booking routes and prior send log for duplication risk.

Rollback must require operator approval if it can discard data, especially emails/drafts entered after backup. Maintain a journal of actions taken and never silently lose messages or send twice.

## 10. Diagnostics and routine operations

```bash
# Once the new units are installed:
systemctl status bandmate booking-worker.service booking-scheduler.timer bandmate-google-calendar-sync.timer --no-pager
journalctl -u booking-worker.service -n 100 --no-pager
journalctl -u booking-scheduler.service -n 100 --no-pager
systemctl list-timers --all

# Safe pause of scheduled discovery (operator only):
systemctl stop booking-scheduler.timer
# Worker may be processing already queued jobs: use app's per-band pause controls.

# Resume scheduled work:
systemctl start booking-scheduler.timer
```

Every band should have a separate UI showing key status, Gmail auth, last Gmail sync, last discovery, jobs delayed by budget, error history, configured recurrence and OpenAI usage estimates. A failure for one band must not halt discovery for another. Alerts should indicate stale source/deadline risk, not just service uptime.

## 11. Staged rollout and validation

- **Stage A:** Deploy feature disabled for all current groups; verify existing BandMate regressions and database backup/restore.
- **Stage B:** For one designated Group, test Bandmate local calendar CRUD, Google read-only mode, Google editable mode with explicit calendar write consent, Google external-event edits flowing back to Bandmate, read-only enforcement and revocation. Test with Booking OFF as well as ON. Then enable Booking for `the dead end`; assign one Booking manager, test per-band AI key with tiny approved request, test Gmail OAuth in allowed test mode, and import a limited historical date range.
- **Stage C:** Run manual discovery and review matched sources; confirm deadline predictions clearly labeled. Verify blacklist suppression and calendar overlay.
- **Stage D:** Enable manual email sending with one approved internal test recipient and full audit before contacting organizers.
- **Stage E:** Onboard a **second dummy band with separate OpenAI key, Gmail OAuth and Google Calendar OAuth** to verify no cross-group data, calendar writes, billing ledger, or email send leaks. Then scale gradually.

**Do not call Gmail or OpenAI as soon as a group is toggled enabled.** Require explicit integration setup and per-band approval of any initial paid data processing.

## 12. Developer documentation required

Cortex must implement and verify:

- `docs/booking/INSTALL_UPDATE.md` with actual unit paths, commands and preflight for the live LXC.
- `docs/booking/OPERATIONS.md` with backup, restore, rollback, health, troubleshooting, key rotation and separate Gmail/Calendar Google OAuth errors.
- `docs/booking/CALENDAR_OAUTH_AND_EDITING.md` documenting scope elevation, per-calendar write toggle, calendar roles, ETags, two-way sync, recurrence limits, Google verification and private-event safety.
- Versioned systemd files and validated scripts to install/update/disable worker+timer.
- Automated tests that do not require live Gmail/OpenAI; optional explicit smoke tests behind opt-in credentials.
- A release checklist showing which live checks were actually run and whether Google verification is still pending.

**No production commands should be executed merely because this document was attached to a Cortex chat.** The developer should implement and test locally, then request explicit production deployment authorization.
