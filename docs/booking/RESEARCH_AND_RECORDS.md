# Records and reviewed research

Phase 3 is implemented on `codex/ai-booking-agent`, with no live provider calls
performed during development. It includes optional Brave Search/OpenCage adapters,
reviewed public artist gigs and separately consented AI extraction. See
[provider setup and limits](DISCOVERY_PROVIDERS.md).

## Human-maintained records

Booking editors/managers create organizations, venues, contacts, event series and
annual opportunities. Viewers can read. Every relationship, assignee and detail
route is scoped to the active band. Saves reject stale revisions. Managers can
merge records of the same kind; the source stays archived with its notes, related
records/evidence move to the target, and aliases preserve old suppression matches.
Opportunities from different years cannot merge. Starting next year's edition
copies basic identity/relationships but no dates or date evidence.

Published application opening dates and verified deadlines require a source URL
and excerpt identifying the relevant cycle. The application deadline may fall in
the preceding calendar year; the event date must match its event year. Predictions
have their own dates/reason and always appear as predictions. Editing unrelated
notes does not silently refresh a date's verification timestamp.

Venue coordinates and the band's center coordinates are entered manually or saved
from an explicitly reviewed geocoder match. Radius
is straight-line miles, never driving distance. Missing coordinates stay visible
as unverified. Scores explain saved priority, known distance and verified deadlines
within 60 days; excluded cities are penalized and optionally filtered. This does
not infer undocumented history. Saved genre matches and documented prior bookings,
replies/declines and application status contribute explained ranking weights.

## Research lifecycle and limits

Only a manager creates/approves research scopes. Approval expires after 15 minutes,
is actor/group-bound and creates one idempotent job. Queued work expires after two
days. Feature, role, cancellation, credential generation and lease ownership are
rechecked before provider operations and before storing results. No network calls
occur in page renders or database transactions. Ten research jobs per band per UTC
day are allowed, including scheduled jobs and failed attempts to run approved work.

Gmail scopes include inclusive UTC dates, up to 20 simple keywords (no arbitrary
Gmail operators), 1–100 new messages and 1–365 days' retention. Search matches any
approved keyword. Each worker slice lists at most five messages and checkpoints
progress after each message. History enumeration is capped at 20 pages/2,000 changed
IDs before targeted rescan; search caps at 100 pages. Reaching a limit does not
advance the stored history checkpoint. Narrow dates/keywords for further imports.
Repeated messages and previously removed messages do not create new candidates.

Only selected headers, correspondent addresses, thread/message references and up
to 1,000 characters of Gmail's snippet are stored. No full body/attachments are
requested. All results enter review; email dates never become application/event
dates. Import approval does not authorize disclosure to OpenAI; AI extraction
requires a separate per-candidate preview and consent. Changed import scope causes
a fresh targeted search rather than reusing an unrelated history checkpoint.

Public sources must use HTTPS on port 443. Every DNS address must be public; the
socket connects to a validated IP while TLS verifies the original hostname.
Redirects repeat safety/role/suppression checks and cannot cross hosts. Robots
disallow/rate policies, non-200 responses other than absent robots, compressed
responses and unsupported content types fail closed. Limits: four request hops,
15-second socket timeout, 30-second body-read budget, 64 KB robots and 1 MB source.
No authenticated social scraping, browser challenges or login bypass is supported.

JSON-LD Event/MusicEvent/Festival fields yield at most 20 review candidates;
otherwise a page title/excerpt becomes one lead. Event `startDate` is not an
application deadline. Structured evidence is retained with proposals. Unchanged
findings deduplicate; changed evidence returns to review without overwriting
human records. Source text cannot authorize actions. Blocked sources need manual
review or an approved alternative URL.

Candidate acceptance requires an editor/manager to review the opportunity form.
An observed contact address can be explicitly saved, with band-local email dedupe
and suppression checks. No candidate automatically sends mail or publishes a gig.

## Suppression, retention and export

Managers blacklist stable record identities, email addresses, exact URLs, domains
or name/city pairs. Aliases survive renames/merges and stop rediscovery. Linked
blacklisted entities hide opportunities and Calendar overlays. Restoring visibility
preserves opt-outs. Clearing an opt-out is a separate manager action requiring a
reason documenting renewed permission; it does not clear an active blacklist.

The scheduler purges expired imported email even when Booking is OFF. Email/evidence
views and exports also enforce expiry before displaying retained content. Removal
clears references, candidate content/AI analysis and copied Gmail evidence and keeps only
content-free dedupe hashes. Observed Gmail history deletions use the same cleanup.
Manager removal cancels pending imports. Human-reviewed contact/opportunity fields
and notes remain separate, as disclosed before removal. Backups follow the operator's
retention policy; deleting live excerpts does not erase old backups.

Managers can export same-band records/evidence/reminders/suppressions or retained
mail metadata as JSON. Responses are `no-store`; credentials are never included.
These exports are review aids, not a replacement for a secured database backup.

## Reminders and scheduling

Follow-ups, published application dates, event dates and predicted windows appear
as read-only Booking overlays in the existing Calendar. Completed reminders drop
out of Calendar. Booking-disabled/non-role users cannot see private overlays.
No overlay creates a Google Calendar mutation or modifies an ICS row.

A successful public-source run can be explicitly enabled for daily, weekly or
30-day revisits of that exact URL. Scheduled sources remain linked from Research & review even when
their original run falls outside the recent-run list. Disabling a schedule prevents
future enqueues; cancel any already-queued run separately. Losing manager access
or disabling Booking disables its schedule when next due; re-enable deliberately
after restoring access. There is no automatic recurring Gmail import or AI call.

```sh
.venv/bin/python manage.py booking_enqueue_due
.venv/bin/python manage.py booking_worker --once
```

The first command only enqueues/cleans locally; the second can contact providers
for approved work. Run against staging only after configuring authorized accounts.
Retryable public-source/import transport/429/5xx failures back off, with at most
four consecutive failed slices. AI calls are not automatically retried. UI errors
are sanitized codes. Cancellation/revocation cannot undo a remote
read already in flight, but results cannot be stored after the final gate fails.

## Provider boundaries and remaining acceptance

Search returns unverified leads; following a result requires a separate approved
public-source fetch. Coordinates require review before they affect location data.
Public artist gigs remain separate from the band's own opportunities. AI facts
require exact supporting quotes and remain unverified suggestions; nothing is
automatically promoted into records, contacts or deadlines.

Operators must configure provider accounts, quotas and reviewed model prices,
verify provider plan/storage terms, and complete live-provider and staging LXC
acceptance. See [discovery providers](DISCOVERY_PROVIDERS.md) and
[current acceptance](PHASE_3_DISCOVERY_ACCEPTANCE.md). Reviewed drafts, sending,
reply threading and explicit calendar publication belong to Phase 4.
