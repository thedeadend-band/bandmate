# Installing Booking and editable Calendar

These are operator instructions for the **existing** LXC, not commands executed
by development. Verify host/container, service identity and backup destinations
before using them. No new VM, Docker deployment, port or tunnel is required.

## Local review

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test player booking calendar_integrations
.venv/bin/python manage.py migrate
.venv/bin/python manage.py booking_check
```

Migrations: `player/0023_groupaccessaudit`, `booking/0001_initial`,
`calendar_integrations/0001_initial`, `calendar_integrations/0002_editable_calendars`,
`booking/0002_credentials_and_budgets` and `booking/0003_research_records_and_imports`.
They are additive. Existing groups receive
no roles or enabled feature automatically. Database changes apply only when the
operator runs `migrate`; development validation uses disposable databases.

In Settings → Edit Group → **Booking and Calendar permissions**, designate
initial managers from existing members. A Booking manager can configure location
and roles. A Calendar manager can create local sources and choose write policy
through Calendar → Manage Calendars → **Local calendars and Calendar roles**.
Local event CRUD works without credentials. Google/Gmail connections require the
separate HTTPS clients and vault described in the integration documents. OpenAI
keys are entered only in the band manager Connections form, never in chat.

## Existing LXC update

Before maintenance, verify application backups and a staging restore: SQLite,
media/song data, environment, unit customizations and (when introduced) separately
secured encryption key versions. Record installed/reviewed release SHAs. The
updater creates a private verified online DB backup but does not replace those
independent recovery backups.

The old updater must not be used to install this release. Stage the reviewed
`update-service.sh` and `ops/backup_sqlite.py` together outside the running checkout
(for example `/opt/bandmate-release-tools/`, preserving `ops/`), then run the
staged script with the full reviewed commit SHA:

```sh
sudo bash /opt/bandmate-release-tools/update-service.sh REVIEWED_40_CHARACTER_SHA
```

The updater refuses a dirty checkout or non-fast-forward release, locks deployment,
checks existing configuration/migrations and Rubber Band, pauses web/background
units, verifies a SQLite online backup in `/var/backups/bandmate/release-.../`,
fast-forwards exactly to the SHA, installs dependencies, checks/applies migrations
and collects static. Only previously active web/worker/timer units are restarted.
One-shot work is not replayed. Any failed step stops the update; there is no
destructive automatic rollback. A failure during restart can leave a partially
restarted system; inspect the journal and every service.

The trusted `.env` must be shell-compatible, as with the existing installer. No
environment, media, database, remote URL or master-key file is replaced. Unit
upgrades are explicit so customized production units are never silently replaced.
`deploy-service.sh` now rejects an existing installation before doing any work.

## Optional integration units

Keep units uninstalled until their configuration has been reviewed. Google sync
and explicitly queued Booking connection/research jobs now contact providers;
the scheduler enqueues approved public-source revisits and clears expired mail
content. Verify an existing **unprivileged** runtime
user can read `.env`, run the venv, and write the database directory/worker lock.
Do not recursively change ownership of the existing installation without review.

Render units to a temporary directory first:

```sh
BANDMATE_SERVICE_USER=bandmate BANDMATE_UNIT_DIR=/tmp/bandmate-units \
  bash ops/install-feature-units.sh
systemd-analyze verify /tmp/bandmate-units/*.service /tmp/bandmate-units/*.timer
```

The installer validates the user exists, replaces the application/user template
values, refuses to overwrite customized units, and **does not enable/start** them.
For an approved installation after inspection:

```sh
sudo env BANDMATE_SERVICE_USER=bandmate bash ops/install-feature-units.sh
sudo systemctl daemon-reload
sudo systemctl enable --now booking-worker.service booking-scheduler.timer bandmate-google-calendar-sync.timer
```

Keep `bandmate-calendar-sync.timer` for ICS. The new Google timer is independent
of Booking enablement. The worker takes a dedicated singleton file lock, not the
audio queue lock. No service stores API keys in its unit/command line.

Live Linux/systemd verification, service ownership and real credentials have not
been tested on the production LXC. See [OPERATIONS.md](OPERATIONS.md).
