# Reviewed outreach and calendar publication

Phase 4 extends `codex/ai-booking-agent` after Phase 3 commit `82e86c4`.
Migration `0005_reviewed_outreach` adds templates/versions, editable drafts,
immutable send approvals, scoped conversation checks/replies and publication links.
Development validation uses mocked providers and disposable databases only.

## Templates and drafts

Managers maintain named venue/festival/civic/follow-up templates. Every save adds
an immutable version; earlier drafts retain their original version. Only documented
`{{placeholder}}` substitutions are accepted—no Django template execution. A draft
requires every referenced value; missing venue, EPK, prior-contact context or other
facts must be supplied/reviewed before creating it. Blank-template drafts can be
written manually by editors/managers. Opportunity detail links to its outreach
history, compose form, reminders and calendar publication.

Editors/managers review To, Cc, Bcc, subject and plain-text body. Recipients must
be valid distinct email addresses; Cc/Bcc each allow up to four, with nine total.
There are no attachments, automatic uploads, sender aliases or HTML emails. Links
remain visible in the complete plain-text preview. Saves use optimistic revisions.

Personalization fills reviewed opportunity/contact/band values into the selected
version. Optional AI assistance selects and orders up to 20 existing paragraphs;
it cannot write new factual claims, change addresses/links or send mail. This is
intentionally narrower than free-form AI rewriting. The manager approves the exact
subject/paragraph disclosure, model, pricing, token caps and maximum estimated
cost separately. A result stays a suggestion until an editor/manager reviews and
adopts it. Review for omitted context or changed meaning even when every paragraph
is preserved verbatim. AI does not verify the underlying human-supplied facts.

Draft arrangement uses the same per-band key/model policy, worst-case budget
reservation, input-token counting, bounded structured output, actual estimated
usage settlement and uncertain-call handling as extraction. It has no tools and
is never automatically retried or scheduled. Editing or approving the draft
invalidates pending AI work. No Gmail history is automatically included; only
reviewed text in the displayed draft scope is disclosed.

## Gmail authorization and send approval

Start with the existing separate `gmail.readonly` connection. A manager can then
choose **Authorize reviewed sending**, which adds `gmail.send` through a separate
OAuth state bound to the same verified mailbox, actor, session and credential
generation. A different mailbox or missing send scope fails closed. A read-only
reconnect does not silently retain send capability even if Google returns a broader
previous grant. OAuth/Google verification remains an operator release prerequisite.

The manager opens **Review recipients and send**, sees the actual sender, every
recipient, subject, entire body, lack of attachments, suppression result and
previous approvals, then deliberately checks consent and clicks **Send approved
message**. The POST creates one approval per draft and one idempotent worker job.
Approval captures exact content, SHA-256 hash, MIME payload, sender, credential
generation, actor, RFC Message-ID and expiry (24 hours). The draft is locked and
the immutable proof is protected from accidental record deletion. It can be
cancelled while still approved, before the worker marks transmission started.

`BOOKING_SEND_DAILY_LIMIT` defaults to 10 approvals per band per rolling 24 hours;
zero also blocks queued sends at preflight. Every recipient, including Cc/Bcc, may
have only one new approval in a rolling 24 hours. Failed/cancelled approvals still
count, avoiding easy retry loops. The limit is an application bound, not a promise
about Google quotas. No bulk-send UI or automatic follow-up exists.

The dedicated Booking worker rechecks actor/membership/feature, exact mailbox and
credential generation, read/send scopes, lease, approval expiry, content and all
recipient/linked-record suppressions. It refreshes the band's token and verifies
`users/me/profile` before sending. It durably records `sending` before the single
`users.messages.send` request. No HTTP render or AI handler calls send. The stable
RFC Message-ID supports reconciliation; Gmail does not provide a general
idempotency guarantee for that header.

Only a response with message/thread IDs marks `sent`. A confirmed result is retained
even if access is revoked during the remote call; it must never turn into a retry.
Early-stage opportunities become contacted only after confirmed delivery, while
confirmed/declined/later-stage records are not overwritten. Errors never claim success.

## Uncertain delivery and replies

A timeout, malformed confirmation or process interruption after transmission is
`uncertain`. Lease recovery never resends. From the message page a manager can
approve **Reconcile delivery**, which searches Sent mail for the exact RFC
Message-ID and compares sender, all recipients, subject and decoded plain text.
One exact match with SENT label confirms delivery. Missing, multiple or mismatched
results remain uncertain; they do not prove non-delivery. The read may retrieve
raw content for comparison but does not retain that provider payload. Inspect the
original mailbox before deliberately creating any replacement. Never reset a
message/job to approved/pending to force delivery.

For a confirmed message, **Check this conversation for replies** approves one
specific stored thread from the original mailbox, with 1–365 days of retention.
The worker reads metadata and up to 1,000 characters of each snippet, with at most
100 thread messages. Oversized threads fail visibly; no broad mailbox scan is used.
Stored replies link to the opportunity's sent conversation. Gmail reconnects may
use a new generation only through a new explicit check for the same mailbox.

Reply snippets share existing Gmail expiry/export/removal and tombstones. Removal
cancels pending reply reads, so an in-flight check cannot restore deleted content.
Human-edited drafts, template context and exact approved send evidence remain
separate and are disclosed as retained. Backups retain their operator-defined
lifecycle. Replies never change status, create AI calls or send follow-ups on their
own. An editor can deliberately draft a follow-up in a confirmed same-band thread;
it retains the original subject to meet Gmail's threading requirements, and the
manager still reviews and sends it. A new subject requires a separate draft.
Existing manual reminders remain available.

## Explicit calendar publication

A Booking editor/manager must also have Calendar editor/manager rights and an
eligible writable target. Confirmed active unsuppressed gigs can be published to
local or editable Google calendars. The review form includes target, title,
dates/times/timezone, all-day flag, location and description. No private notes,
attendees or invitations are copied automatically; Google uses existing safe
notification policy. Read-only Google and ICS are never writable targets.

One durable opportunity/target link prevents duplicate creation. Update uses the
current event version/ETag and fresh rights. Unlink leaves the event untouched and
retains its identity, so a later relink does not create a duplicate. Remove deletes
the linked event through the existing write pipeline. After a confirmed removal,
an explicit new publication is permitted. Pending/uncertain operations must be
resolved before changing the link. Opportunity edits never silently mutate events.

Google operations run in the separate Calendar worker, retaining live ACL checks,
ETag conflict handling and uncertain-write reconciliation. Publication operations
also recheck Booking role/feature and confirmed/suppression state before writes.
Ordinary Calendar edits continue to work while Booking is disabled.

## Reports and live acceptance

Usage includes per-model/category totals, daily estimated costs, remaining monthly
budget after reservations, recent jobs and JSON metadata export. Outreach includes
a manager-only export of exact approved content/delivery evidence and Booking audit
history. Exports are scoped to the active band and use `Cache-Control: no-store`;
credentials are excluded. OpenAI billing remains authoritative.

Before live use: review provider verification and sending policy, configure vault/
OAuth/model/pricing, test authorized staging mailboxes and calendars, exercise
revocation/timeout/reconciliation, and verify workers, backup/restore and retention.
No development test sent real mail or made a paid model call. Broader free-form AI
rewriting, attachments, automated reply polling and automatic status inference are
not part of this conservative implementation.

Provider references:
[Gmail send/MIME](https://developers.google.com/workspace/gmail/api/guides/sending),
[send endpoint](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send),
[thread requirements](https://developers.google.com/workspace/gmail/api/guides/threads),
[scope requirements](https://developers.google.com/workspace/gmail/api/auth/scopes).
