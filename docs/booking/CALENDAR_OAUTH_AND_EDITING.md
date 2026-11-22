# Calendar OAuth and event-editing contract

**Status: design for Phase 1, not a working OAuth integration.** Phase 0 provides
independent roles and source policy. Google enabling fails closed. There are no
event-editing endpoints, tokens or OAuth callbacks yet.

The existing Calendar page must combine read-only ICS, local and Google events
without reusing imported ICS rows. Local/Google sources start view-only; only a
band Calendar manager may opt them into edits. Event mutations also require a
current Calendar editor/manager. Booking enablement and Booking roles confer no
Calendar write access.

Initial Google read scopes are `calendar.events.readonly` and
`calendar.calendarlist.readonly`. Request `calendar.events` incrementally only
after explicit manager intent. Use separate Calendar credentials from Gmail and
never full Calendar/ACL administration scopes. The event-write grant spans
calendars, so backend ownership, selected ID and write policy checks are mandatory.
[Google scopes](https://developers.google.com/workspace/calendar/api/auth).

Recheck effective accessRole live before each write: writer/owner, or
writerWithoutPrivateAccess for eligible non-private events. Specialized events,
organizer-controlled data and private visibility need event-level enforcement.
Cached ACLs are insufficient. The current helper is only the local policy gate.
[Google CalendarList roles](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList).

Required tests/implementation: separate encrypted grants per band; single-use
callback state and PKCE; revocation/disconnect; shared remote-calendar warning;
provider-confirmed CRUD for existing eligible events; ETag conflicts; durable
operation IDs and retries; all-day exclusive end dates and DST; recurrence scope
or explicit refusal; quota backoff; incremental sync with pagination/tombstones
and calendar-local 410 reset. Google changes must never enqueue outbound writes.

Use the separate Google timer, not Gunicorn rendering or Booking/audio workers.
Google verification and explicit test accounts are live deployment prerequisites.
