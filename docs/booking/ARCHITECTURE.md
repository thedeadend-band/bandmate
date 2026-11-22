# Booking and independent editable Calendar

## Current implementation: Phase 0

Started from clean HEAD `18dc9fbbeee3201415e811977373541ac813f660` on
`codex/booking-editable-calendars`. This matches the supplied baseline. The full
requested scope is preserved in [spec/START_HERE.md](spec/START_HERE.md).
This increment implements the foundation, not the complete Booking product.

Reuse Django templates, session authentication, CSRF, `player.Group`,
`GroupMembership`, sidebar/theme, SQLite, Gunicorn and the existing LXC.
No new frontend, remote service, public port, database, or audio queue dependency.

| Area | Integration |
| --- | --- |
| Booking | New `booking` Django app, `/booking/`, dashboard/setup/roles |
| Feature control | Staff-only `/admin-console/groups/<id>/features/`, linked from Edit Group |
| Authorization | Shared `player/group_permissions.py`; role lookup joins current membership each time |
| Calendar | Independent `calendar_integrations` app, `/calendar/manage/`, linked from existing Manage Calendars |
| Navigation | Booking appears only for an enabled band and explicitly assigned Booking role |
| Audit | `player.GroupAccessAudit`, limited to non-secret action/identity/role/policy fields |
| Background | Isolated management command stubs and systemd units; no remote work in this phase |

`BookingSettings` is one-to-one with Group. A missing row means disabled; creating
a row defaults disabled. Disabling blocks Booking pages and hides navigation but
retains settings and roles. It has no effect on Calendar.

Both role models use a one-to-one FK to `GroupMembership`, enforcing actual
membership at the database level. Removing/recreating membership does not restore
old roles. Booking manager/editor/viewer and Calendar manager/editor/viewer are
independent. Staff status alone is insufficient for either. Staff can explicitly
designate the first manager from current active members; the bootstrap field
disappears when a manager exists. Managers subsequently assign roles. Removing the
last manager through role management is rejected. If all managers leave the band,
staff can explicitly bootstrap a replacement, with an audit record.

New routes scope record IDs to the active Group even when a user belongs to both
bands. Model forms allowlist fields; submitted group IDs cannot switch tenants.
Roles, feature switches and source policies are re-read rather than trusted from
the browser or long-lived model instances. No new models are registered with the
unrestricted Django admin.

## Calendar ownership and write boundary

Existing `CalendarSource`, `CalendarEvent`, ICS imports and member source-management
permissions are preserved. Their rows are never repurposed. Current baseline ICS
sync-on-page-load behavior remains for this phase; remove it in Phase 1 in favor of
the existing timer and explicit sync status.

`BandmateLocalCalendar` owns a Group FK. `CalendarGoogleAccount` owns its Group;
`ConnectedCalendar` derives its tenant through the account FK, avoiding conflicting
tenant IDs on the same record. Provider identity is unique within its account.
Account metadata contains no tokens; there is no credential input in this phase.
Google account onboarding and encrypted refresh credentials belong to Phase 1.

| Source | Phase 0 | Required event-write gate in Phase 1 |
| --- | --- | --- |
| ICS, including Google export URLs | Existing read-only events | Never writable |
| Local | Manager creates source and explicitly sets policy; default view-only | Current editor/manager + enabled source + write policy |
| Google view-only | Metadata foundation; no OAuth connection UI | Never writable, even with a broadly scoped token |
| Google editable | Enabling through HTTP fails closed; disabling is supported | Current role + exact selected calendar + policy + live grant/ACL/event rights |

`source_write_allowed` is a tested **local policy gate only**, not live Google
authorization. It re-fetches source/account state and refuses unsupported source
types, insufficient scopes/ACLs and private events for
`writerWithoutPrivateAccess`. No event-write endpoints exist in Phase 0. Phase 1
must verify provider state immediately before each write, protect recurrence,
ETags and idempotency, and respect Google errors rather than treating cached ACLs
as current permission.

## Integration plan

Calendar initial scopes: `calendar.events.readonly` and
`calendar.calendarlist.readonly`; explicit incremental `calendar.events` consent
for editing. Never request full `calendar` or ACL administration. Google's token
is not restricted to one selected calendar: enforce the internal allowlist on
every mutation. Use separate, group-bound Calendar credentials and callbacks.
[Google scope reference](https://developers.google.com/workspace/calendar/api/auth).

Provider writers/owners may modify eligible events; `writerWithoutPrivateAccess`
must not receive private details or edit private events. Reconfirm rights on every
operation. [Google CalendarList reference](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList).

Gmail uses independent `gmail.readonly` and, later, explicitly consented
`gmail.send`; never inherit Calendar authorization. Verification for restricted
Gmail data is a production prerequisite, not a completed approval.
[Google Gmail scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes).

Future OpenAI, Gmail and Calendar secrets use authenticated encryption with a
separately held master key. Never copy existing Spotify/site-global credential
storage. OpenAI factories must require a validated Group and its own credential;
no global key fallback. Budget reservations and usage remain per band.

Separate Google/local event tables will feed the same `/calendar/` interface.
Booking overlays require Booking access; publication requires both Booking edit
and Calendar edit permission plus a deliberate reviewed action. No automatic
external writes or emails.

## Runtime

SQLite stays on the LXC; short transactions and one worker. The Phase 0 worker
uses a dedicated file lock and graceful SIGTERM/SIGINT shutdown. It does no work.
Scheduler and Google sync stubs report zero work honestly. Future durable job
leases, retries, budgets and provider adapters are prerequisites to execution.
Do not reuse `player/job_queue.py` or hold DB transactions across remote calls.

The additive migrations do not update any existing ICS, Group, song or setlist
rows. Reversing them deletes new feature configuration/audit rows, so rollback
requires a backup and deliberate operator action.
