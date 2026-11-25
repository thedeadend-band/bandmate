# Security boundaries

Implemented and tested locally:

- Group-scoped reads and mutations; posted tenant IDs ignored; staff has no private
  data shortcut. Independent Booking and Calendar roles follow membership/activity.
- CSRF and POST mutations; fresh role/feature/source checks; escaped templates and
  allowlisted form fields. Secret input uses a non-redisplaying password widget.
- Authenticated Fernet encryption bound to Group and purpose, with an out-of-Git,
  out-of-DB mode-0600 key ring and rotation commands for both feature families.
- Separate Gmail and Calendar grants, PKCE and single-use expiring OAuth state;
  account generations prevent stale callbacks/jobs from reversing disconnects.
- SQL parameter logging is suppressed. Callback query strings are redacted in
  Django's configured console/server logging. Provider errors expose safe codes,
  never response bodies, credentials or raw transport exceptions.
- Google live ACL/event checks, ETags, durable operation intent, conservative private
  and recurrence handling, deterministic create IDs and uncertain-write recovery.
- Group-only OpenAI factories, no global fallback, atomic budget reservation and
  preflight, bounded read-only connection jobs with leases and attempt fencing.
- Explicit, expiring import scopes; metadata/snippets only; no full mail bodies,
  attachments or automatic AI disclosure. Progressive checkpoints and content-free deletion
  tombstones; manager export/removal and automatic expiry of retained evidence.
- Public fetching validates every DNS address and redirect, pins the connection IP
  with original-host TLS verification, disallows private/reserved addresses and
  cross-origin redirects, checks robots and bounds response bytes/time.
- Persistent aliases and suppression prevent rename/merge/import from silently
  reactivating blacklisted identities. Opt-out resolution requires manager reason.
- Separate per-candidate AI consent binds exact evidence, credential generation,
  model, pricing and token caps. Started uncertain calls retain their reservation
  and cannot be replayed automatically. Gmail expiry/removal clears derived analysis.
- Search/geocoding use fixed provider endpoints, explicit query consent and global/
  per-band quotas. Coordinate results require review before changing locations.
- Sending requires separate mailbox-bound Gmail scope elevation and an immutable
  exact-content manager approval. Durable started state prevents automatic resend;
  reconciliation is read-only. No AI or scheduler can approve/send autonomously.
- Calendar publication requires both role families and fresh Booking/Calendar
  worker checks; ordinary Calendar remains independent of Booking.
- Gig plans, resource links and rehearsal focus remain Booking-role scoped.
  Setlist creation explicitly discloses band-member visibility; linking never
  expands existing setlist editing rights. Calendar/player backlinks recheck
  Booking access, and rehearsal overlays omit private focus notes.
- Resource approval does not publish or fetch files. Only reviewed same-band EPKs
  are selectable in draft composition; copied URLs remain subject to full message
  review. Archive/revocation prevents future selection but does not rewrite drafts
  or immutable send approvals. See [gig preparation](GIG_PREPARATION.md).

Before live use, operators must configure HTTPS, application client registration,
separately secured key recovery, proxy log redaction and Google verification.
Reverse proxies/access log collectors must omit query strings on both callback
paths; Django's filter cannot redact logs outside the application. Keep production
DEBUG off and limit database/backup access to the service and authorized operators.

Research text is untrusted evidence, never instructions. Deterministic extraction
and separately approved AI suggestions enter human review. AI has no tools and
cannot promote facts into records. Exact supporting quotes establish textual
support, not truth or semantic correctness. Every suggestion remains unverified.
Phase 4 enforces exact approved recipients/content, suppression checks, CSRF,
immutable approval, mailbox rechecks and read-only uncertain-send reconciliation.
AI drafting only arranges existing reviewed paragraphs. See [outreach](OUTREACH.md).

The inherited ICS fetcher, Spotify credentials and site-wide settings remain
outside this new credential design. Do not reuse them for private band providers.
