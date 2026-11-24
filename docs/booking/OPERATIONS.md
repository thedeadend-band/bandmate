# Operations and recovery

## Current status

The Booking worker processes explicitly queued OpenAI model-list/Gmail-profile
checks and approved Gmail/public-URL/search/geocoding/AI research.
`booking_worker --once` processes one bounded batch; the long-running worker
checks every 30 seconds. `booking_check` is read-only and prints no secrets.
`booking_enqueue_due` enqueues explicitly approved periodic public-URL revisits and
purges expired email content, including for Booking-disabled bands. It makes no
provider calls. No recurring source is enabled by default; Gmail requires fresh
preview/approval per run. See [research limits/recovery](RESEARCH_AND_RECORDS.md).

Search/geocoding require operator credentials and nonzero global/per-band quotas;
AI requires reviewed model pricing plus the band's tested key and budget settings.
See [discovery provider setup](DISCOVERY_PROVIDERS.md). Never reset a started AI
reservation or retry an uncertain paid request: reconcile actual provider usage
before adjusting retained reservations. AI requests are not scheduled or retried
automatically. Input counting and inference both require the approved disclosure.

`sync_google_calendars` now executes real sync and explicitly queued event writes.
It has an independent lock/timer and works while Booking is OFF. Account-list
refreshes are explicit; selected enabled calendars sync on the timer. Ordinary
pages do not sync Google or ICS. Keep the existing ICS timer enabled for ICS.

Do not enable provider jobs with production accounts until the release gates below
are met. Mocked success is not live provider acceptance. Feature changes and
connection lifecycle events use `GroupAccessAudit`; event changes also have
`CalendarMutationAudit` and durable operation status. No email sends are available.

## Vault provisioning and rotation

Set `BANDMATE_SECRET_KEY_FILE` to a service-readable file outside Git and the DB,
mode 0600 (parent directory protected). It contains one or more newline-separated
Fernet keys; the first encrypts new data, all listed keys can decrypt older data.
Generate keys with the installed `cryptography.fernet.Fernet.generate_key()` into
the protected file, never into chat or command-line arguments. The application
refuses a group/world-readable key file. No key file was created for production.

For rotation, back up DB and old key ring separately; prepend a new key, then run:

```sh
.venv/bin/python manage.py rotate_calendar_credentials
.venv/bin/python manage.py rotate_booking_credentials
```

Commands compare original ciphertext before updating, so they do not overwrite a
concurrent reconnect. They are safe to retry if a failure leaves a mixed-key DB.
Keep all old keys until every active record and retained backup has a tested
recovery path. Never discard a key merely because a rotation command succeeded.
Both expired OAuth state stores are pruned on subsequent connect attempts.

Configure separate Calendar/Gmail client settings using [.env.example](../../.env.example).
Disable DEBUG in production. Proxy/access logs must omit callback query strings
on `/calendar/manage/google/callback/` and `/booking/gmail/callback/`; the app
redacts its own configured logs but cannot alter proxy logs.

## Safe backup

```sh
umask 077
.venv/bin/python ops/backup_sqlite.py /srv/bandmate/db.sqlite3 /SECURED_BACKUP_PATH/pre-change.sqlite3
```

The helper opens the source read-only, checks integrity, creates a new destination
exclusively with mode 0600, uses SQLite's online backup API and checks the result.
It never overwrites a backup or creates an absent source. Keep DB/media snapshots,
release SHA and migration state together. Back up `.env` and master-key
versions separately with restricted access/encrypted off-host storage; losing a
master key will make encrypted credentials unrecoverable. Test restore on
a staging clone before relying on any recovery procedure.

## Update failure / rollback

The updater leaves a release journal and active-unit list in a mode-0700 backup
directory. It fails immediately and never resets Git or automatically restores a
database. Before the maintenance pause, failures leave running services alone.
After the pause, failures can leave services stopped or partially restarted.

1. Inspect the failure, current HEAD, `showmigrations` and `active-units.txt`.
   Stop workers/timers/web while selecting a consistent recovery state.
2. On a staging clone, verify a matching backup and compatible code. Phase 0 is
   additive: old code can ignore the new tables, but prove compatibility rather
   than assuming every future release permits code-only rollback.
3. If reversing the new migrations, remember they delete new configuration and
   audit rows. Obtain operator approval for any data-discarding restore. Prefer
   retaining those tables while investigating.
4. Restore approved compatible source/data/environment together. Do not use
   `git reset --hard` as a routine updater or replay uncertain future email sends.
5. Start only the previously active units after checks pass. Verify login, group
   switching, songs, setlists, wizard/audio queue and ICS; then Booking gating and
   Calendar configuration. Do not tie the Google timer to the Booking toggle.

## Service diagnosis (after optional installation)

```sh
systemctl status bandmate booking-worker.service booking-scheduler.timer bandmate-calendar-sync.timer bandmate-google-calendar-sync.timer
journalctl -u booking-worker.service -n 100 --no-pager
journalctl -u bandmate-google-calendar-sync.service -n 100 --no-pager
```

The worker lock is `.booking-worker.lock` in the application directory. Its mere
existence does not mean a worker is active; the kernel file lock is authoritative.
Do not remove a live lock file to start another worker. Stop the existing process.

## Remaining release gates

Staging LXC install/upgrade/restore and service identity/hardening verification;
operator-held vault backup/recovery; real read-only OAuth test accounts and Google
verification; then explicitly authorized isolated Google CRUD/ETag/revocation tests.
No paid OpenAI inference or email test has been run. Phase 3 import/data-retention
controls have passed mocked/local acceptance; live mailbox and staging retention
checks remain. Phase 4 reviewed sending remains unimplemented.

Pending Google writes are visible on Calendar and operation pages. Conflicts require
reloading the current event; ambiguous results require inspecting Google before a
new user action. Never manually reset ambiguous rows to pending to force a replay.
Connection-check errors appear on Booking → Connections; revoked credentials need
reconnection, and queued tests will not use a replacement generation silently.
