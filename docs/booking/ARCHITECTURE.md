# Booking and independent editable Calendar

## Current increment

Phase 0 was committed as `bba18cc` on `codex/booking-editable-calendars`.
`codex/ai-booking-agent` descends from that commit and adds Phase 1 Calendar
editing, Phase 2 credential onboarding/budgets and Phase 3 records/import/review. Full scope:
[original package](spec/START_HERE.md); remaining work: [plan](IMPLEMENTATION_PLAN.md).
No production deployment, live credentials or paid provider calls were used.

## Tenant and role boundary

All new private records are bound to the active Group. Membership, active user,
role and feature/source policy are fetched again for mutations and worker calls.
Staff can enable a feature and designate its first current-member manager, but
has no implicit access to a band's private records. Calendar roles and availability
remain independent of Booking. Model forms ignore posted tenant/feature fields.
New private models are not registered in the unrestricted Django admin.

## Calendar

`BandmateLocalCalendar` owns local events. `CalendarGoogleAccount` owns encrypted
Calendar-only credentials; `ConnectedCalendar` derives its Group through that
account. ICS rows retain their original tables and are never editable.
`/calendar/` combines cached events and stable source IDs. Page rendering and
source-save perform no external sync. The existing ICS timer remains responsible
for ICS; `sync_google_calendars` has its own singleton lock and timer.

Local CRUD uses optimistic versions, durable idempotency and mutation audits.
Google writes persist explicit user intent in `CalendarWriteOperation`, then the
Google timer verifies current roles, selected source, OAuth scopes, live ACLs and
event rights. Conditional ETags prevent stale edits; deterministic create IDs and
operation markers support reconciliation. Uncertain patches/deletes are not
blindly replayed. No incoming Google change creates an outbound write.

Recurrence instances, invitations/attendees, special event types and private events
are read-only. Private details are masked conservatively; a free/busy ACL downgrade
also scrubs previously cached details even if the subsequent sync fails.
See [Calendar behavior](CALENDAR_OAUTH_AND_EDITING.md).

## Booking connections and budgets

`BookingOpenAICredential` and `BookingGmailConnection` are each bound to one Group.
The shared Fernet key ring binds ciphertext to both Group and credential purpose.
Calendar, Gmail and OpenAI credentials cannot substitute for each other.
There is no global OpenAI API-key fallback.

Managers save/replace/remove keys, choose an operator-allowlisted model, set limits,
and explicitly queue connection checks. `BookingJob` has idempotency, bounded
batches, leases, attempt fencing and backoff. Its dedicated worker executes
connection checks and explicitly approved Gmail/public-source research. Ordinary
page loads never call providers. Each OpenAI check attempt has a metadata-only
usage record. Separately consented candidate extraction uses Responses with this
band's key. Phase 4 adds explicit manager-approved sends in the Booking worker;
see [outreach and publication](OUTREACH.md).

Budget reservations take a write lock before reading allowance totals (including
on SQLite), then atomically reserve the maximum estimated request cost. `begin`
rechecks current feature/AI/model/credential/budget state immediately before a
handler may call a provider. Started uncertain requests retain their charge;
settlement records observed estimated cost once. UTC day/month rollover requires
a fresh reservation. Handlers also recheck the initiating actor and
bound input/output tokens; no budget method itself makes a remote request.

## Records and research

Group-owned organizations, venues, contacts, series and annual opportunities have
stable UUID identities. Saves use optimistic revisions; merges retain aliases and
archive the source. Suppression follows identities, linked entities and aliases.
Cross-year opportunity merges are rejected. Evidence is separate from manually
maintained records; published dates and predicted windows have distinct fields.

`BookingResearchRun` stores an immutable, expiring approval scope and binds its
durable job to actor/Group/credential generation. Gmail reads are metadata-only,
checkpointed and bounded; public pages use DNS-pinned HTTPS and robots checks.
Both produce `BookingCandidate` rows for human review. Retention clears Gmail
references, candidate excerpts and copied evidence while retaining content-free
dedupe hashes. No remote source can trigger account changes, writes or emails.

Brave Search supplies unverified leads, with a separate approval before fetching
each source. OpenCage matches require review before coordinate changes. Persisted
provider gates enforce global/per-band quotas and shared request spacing. Public
artist gigs have their own reviewed records and never imply bookings by this band.
AI extraction binds consent to an exact candidate excerpt/model/pricing/token scope,
checks supporting quotes and saves unverified suggestions without record promotion.
Gmail expiry/removal also clears derived AI analysis. See
[provider configuration](DISCOVERY_PROVIDERS.md).

Only explicit successful public-source runs can be scheduled for daily, weekly or
30-day revisits. AI extraction and Gmail imports cannot be scheduled.
The scheduler enqueues local work and purges expired email; it does not contact
providers. Calendar overlays are generated from visible same-band records and
never create Google write operations. See [research details](RESEARCH_AND_RECORDS.md).

## Runtime and migration safety

Network I/O occurs outside database transactions. The Booking worker, Google
sync command and audio worker have independent locks/queues. Research scheduling
is opt-in per approved source. New migrations are additive and do not rewrite existing player,
ICS, song or setlist rows. Reversing them discards new feature data; use a secured
backup and deliberate operator approval for a real rollback.
