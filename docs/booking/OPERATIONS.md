# Operations and recovery

## Current status

Phase 0 has no external providers or executable jobs. `booking_check` reports
enabled-band count and the limitation. `booking_enqueue_due` and
`sync_google_calendars` report zero work. `booking_worker --once` checks startup
and singleton locking; without `--once` it idles until SIGTERM/SIGINT.

Do not interpret zero work as a successful Gmail/OpenAI/Google integration test.
No feature toggle triggers external authorization or processing. Feature/role
changes and source policies are auditable in `player.GroupAccessAudit`; no audit
UI/export or private event content exists yet.

## Safe backup

```sh
umask 077
.venv/bin/python ops/backup_sqlite.py /srv/bandmate/db.sqlite3 /SECURED_BACKUP_PATH/pre-change.sqlite3
```

The helper opens the source read-only, checks integrity, creates a new destination
exclusively with mode 0600, uses SQLite's online backup API and checks the result.
It never overwrites a backup or creates an absent source. Keep DB/media snapshots,
release SHA and migration state together. Back up `.env` and future master-key
versions separately with restricted access/encrypted off-host storage; losing a
master key will make future encrypted credentials unrecoverable. Test restore on
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

Staging LXC install/upgrade/restore and systemd hardening verification; encrypted
vault/key rotation; real OAuth accounts and Google verification; provider sync,
event concurrency/recurrence and reviewed email sending. Do not provision live
credentials into Phase 0 metadata records.
