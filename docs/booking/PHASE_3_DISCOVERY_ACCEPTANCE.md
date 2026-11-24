# Phase 3 discovery acceptance — 2026-11-24

Branch: `codex/ai-booking-agent`, extending core commit `08f03c2`. This increment
completes the remaining Phase 3 implementation in the six-phase plan (0–5).
Live-provider and staging acceptance remain separate release gates.

## Delivered

- Optional Brave Search and OpenCage adapters, explicit query previews/approval,
  persisted global/per-band quotas and request spacing. Search results remain
  unverified leads; fetching a source requires separate approval. Coordinate
  selections require review and reject stale venue/preferences changes.
- Public artist and gig directories with reviewed source evidence, same-band
  relationships, aliases and suppression. Public gigs never imply this band is
  booked; an artist listing is not saved as a venue's official website.
- Explained ranking using saved genres, documented booking history/responsiveness
  and application status. Unknown locations remain visible. Expected fee requires
  evidence and does not contribute an assumed currency-based weight.
- Separately approved candidate AI extraction using this band's encrypted/tested
  key, model and budget. Exact evidence/model/pricing/token scope, preflight input
  counting, durable reservations, observed usage accounting and no automatic paid
  replay. Quotes support suggestions; they do not verify facts or promote records.
- Gmail expiry/removal clears derived AI analysis. Fresh suppression checks cover
  embedded artist/venue identities and all retained email correspondents.
- Approved source revisits support daily, weekly or 30-day intervals. No recurring
  AI calls or automatic mailbox imports. Setup, security and operations docs updated.

## Changed files and migration

Additive migration:
[`0004_discovery_providers_and_artist_research.py`](../../booking/migrations/0004_discovery_providers_and_artist_research.py).
Adds artist/gig records, provider request/gate accounting, candidate analysis and
location/history/genre/fee evidence. Existing player/ICS data is not rewritten.

Main implementation files:

- `booking/services/providers.py`, `extraction.py`, `research_jobs.py`, `records.py`,
  `discovery.py`, `imports.py` and `ranking.py`.
- `booking/models.py`, record/research forms and views, `views.py`, `urls.py`, and
  Booking research, approval, candidate, directory and record templates.
- `bandmate/settings.py`, `.env.example`, `booking_check` and
  `booking/test_discovery_extensions.py`.

## Executed validation

```sh
DJANGO_LOG_LEVEL=ERROR .venv/bin/python manage.py test player booking calendar_integrations
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
git diff --check
```

- Full regression: **230 tests passed**, including 34 discovery-extension tests.
  Coverage includes tenant/role isolation, separate AI consent, band-specific keys,
  quota/rate gates, stale/revoked scopes, token limits, malformed/uncertain responses,
  known usage accounting, no paid replay, retention, suppression, geocoding review,
  public artist references, ranking and schedule restrictions.
- Django system check passed; no migration drift; whitespace check passed.
- Disposable online SQLite clone: forward all migrations, reverse Booking `0004`
  to `0003`, then forward again. Pre-existing player rows were unchanged; SQLite
  integrity and foreign keys passed. The source workspace DB was read only.
- Browser on synthetic temporary data: research navigation/search form, public
  search preview with zero processed results, geocoder coordinate/precision/credits
  review, and AI preview with exact excerpt/model/caps/estimate plus unchecked
  disclosure consent. No provider job was approved. Preview server stopped.

The test suite emits the existing missing `staticfiles/` development-directory
warning. Provider responses are mocked; browser preview used temporary static/data
roots and synthetic credentials. No live-provider or paid API call, mailbox read,
outbound email, workspace database migration or production deployment occurred.

## Prerequisites and next milestone

Search/geocoding default off and require operator credentials, nonzero quotas and
review of provider plan/storage terms. AI requires a tested per-band key, supported
allowlisted model, reviewed versioned pricing and nonzero limits. See
[discovery provider configuration](DISCOVERY_PROVIDERS.md) and
[OpenAI setup](OPENAI_SETUP.md). Complete authorized live-provider and staging LXC
worker/scheduler/backup/retention checks before rollout; local tests do not establish
account entitlement or production acceptance.

Next: **Phase 4 reviewed outreach**—versioned templates, evidence-grounded drafts,
explicit manager sending, reply threading and explicit calendar publication.
See the [implementation plan](IMPLEMENTATION_PLAN.md) and the historical
[Phase 3 core acceptance](PHASE_3_ACCEPTANCE.md).
