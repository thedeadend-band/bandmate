# Phase 4 acceptance — 2026-11-24

Branch: `codex/ai-booking-agent`, extending Phase 3 commit `82e86c4`.
Phase 4 implementation is locally complete within the conservative boundaries in
[OUTREACH.md](OUTREACH.md). No production deployment, workspace migration,
live mailbox read, paid inference or real outbound email occurred.

## Delivered

- Manager-maintained immutable template versions, validated placeholders and
  same-band editable drafts with optimistic revision checks and visible provenance.
- Separately consented AI selection/ordering of reviewed paragraphs, scoped to the
  exact draft/model/pricing/caps. Suggestions require review/adoption; no free-form
  factual invention, recipient changes or automatic sending.
- Separate Gmail send OAuth elevation bound to the existing verified mailbox.
  Read-only reconnect does not silently enable sending.
- Complete sender/To/Cc/Bcc/subject/body preview, deliberate manager-only send,
  immutable approved MIME/content snapshot, one approval per draft, duplicate-click
  protection, recipient/band limits, cancellation before start and fresh worker checks.
- Durable uncertain delivery, no automatic send replay after errors/crashes, and
  explicit read-only Sent-mail reconciliation against exact content and identity.
- Approved same-mailbox conversation reads, metadata/snippet retention/tombstones,
  same-thread reviewed follow-up drafts, manual reminders and outreach timelines.
- Explicit local/Google publication, update, unlink/relink and removal with both
  role families, stable publication links, event version/ETag checks and fresh
  Booking checks in the Google worker. Visible linked events suppress duplicate
  event-date overlays while preserving application dates and follow-ups.
- Model/category/daily estimated usage, budget remaining after reservations,
  recent jobs, metadata export and manager-only approved-message/audit export.

## Files and migration

Additive [`booking/0005_reviewed_outreach`](../../booking/migrations/0005_reviewed_outreach.py)
adds templates, drafts, approval proof, reply checks/references, publication links
and OAuth state binding. Existing player/ICS records are not rewritten.

Main code: `booking/models.py`, `outreach_forms.py`, `outreach_views.py`,
`publication_views.py`, `services/outreach.py`, `services/mail_delivery.py`,
`services/draft_ai.py`, existing worker/research/Gmail/usage/presentation integrations,
Booking templates, `calendar_integrations/services/google_mutations.py`,
`booking/test_outreach.py`, settings/environment example and setup/operations docs.

## Validation

```sh
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test player booking calendar_integrations
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

- Final full regression: **270 tests passed** in 92.736 seconds, including the
  publication-overlay and threaded follow-up subject checks.
- Focused outreach coverage: **40 tests passed** in 3.748 seconds.
- System check, migration-drift check and whitespace check passed.
- Private disposable online SQLite clone: forward all migrations, reverse Booking
  to `0004`, then forward again. Legacy player rows remained identical; SQLite
  integrity and foreign-key checks passed. Source workspace DB opened read only.
- Browser on synthetic temporary data: outreach directory, editable draft, complete
  send preview with unchecked consent, versioned template form, confirmed opportunity
  outreach history and local calendar publication reporting `create: succeeded`.
  No send or AI approval was submitted, and no provider worker ran. Preview server
  stopped after verification. Screenshot evidence captured for send preview and
  local publication. Later overlay deduplication is covered by the final regression.

Provider transport is mocked in tests. The existing missing `staticfiles/`
warning remains a development-only warning; preview uses a temporary static root.

## Operational boundaries and next milestone

Live Gmail/Google/AI and staging LXC acceptance remain pending. Configure vault,
registered OAuth clients/Google verification, band keys/models/pricing and provider
limits before live use. Confirm authorized test recipients and calendars separately
before real send/write tests; mocked success does not establish provider entitlement.
See [outreach recovery and retention](OUTREACH.md) and [operations](OPERATIONS.md).

AI is limited to selecting/ordering human-reviewed paragraphs after deterministic
placeholder personalization. Messages are plain text, without attachments. Reply
checks are explicit and limited to 100 messages in one stored conversation; they
never infer status or schedule sends. An uncertain send stays uncertain without
an exact Sent-mail match and cannot be manually reset through the product for replay.

The six-phase plan is numbered 0–5. The next implementation milestone is optional
**Phase 5**: confirmed-gig setlist/EPK/rehearsal helpers and polish. Production
rollout and live-provider acceptance remain separate from that optional work.
