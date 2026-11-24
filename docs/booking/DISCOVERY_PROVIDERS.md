# Phase 3 discovery providers and AI extraction

This extends the committed Phase 3 core (`08f03c2`) on `codex/ai-booking-agent`.
Migration `0004_discovery_providers_and_artist_research` adds public artist/gig
records, provider request accounting, candidate AI analysis and ranking evidence.
All provider tests are mocked; no paid or live account calls were made.

## Public search and geocoding

Two optional fixed-endpoint implementations are supplied: Brave Search and
OpenCage. Both are off by default. Operators supply infrastructure keys in the
protected service environment, select the adapter and set nonzero quotas:

```sh
BOOKING_SEARCH_PROVIDER=brave
BOOKING_GEOCODER=opencage
BOOKING_PROVIDER_DAILY_LIMIT=100
BOOKING_PROVIDER_BAND_DAILY_LIMIT=10
BOOKING_PROVIDER_MIN_INTERVAL=2
```

The quotas above are illustrative, not provider plan entitlements. Supply
`BOOKING_BRAVE_KEY` and `BOOKING_OPENCAGE_KEY` securely; never use band OpenAI or
Gmail credentials for these providers. Verify your contract permits the intended
stored snippets/results and attribution. These provider costs are separate from
the per-band OpenAI budget, and provider billing remains authoritative.

Requests use fixed HTTPS hosts, no redirects or inherited proxies, 20-second
timeouts and 1 MB JSON limits. Error codes never include a URL, key or body.
Persisted global and band-local daily quotas count started requests even when
their response is lost. A global per-provider spacing gate prevents concurrent
bands from exceeding the configured start rate. Throttling/backoff stays in the
worker. No request happens while rendering or previewing a form.

Managers approve the exact query. Brave returns at most ten unverified leads;
search snippets never establish a deadline or known location. The worker does not
fetch result URLs automatically. A manager can create a separate source preview
from a lead, approve it, then use the existing robots/DNS-pinned page reader.
Search can target public artist gig calendars and venue opportunities. No private
mail contents are automatically included in a search query.

OpenCage returns up to five matches, requesting `no_record=1`. The manager reviews
the formatted place, coordinates and precision before saving to the selected
venue or band's search center. Pending lookups never change existing coordinates.
Changed venue revisions or band preferences reject a stale selection. Precision
describes the size of a matched place, not the probability it is correct. Stored
evidence and the review UI identify OpenCage and link its data credits.

Provider references:
[Brave web search](https://api-dashboard.search.brave.com/api-reference/web/search/get),
[Brave authentication](https://api-dashboard.search.brave.com/documentation/guides/authentication),
[OpenCage API and precision](https://opencagedata.com/api).

## Public artist references and ranking

JSON-LD event extraction now recognizes performer and venue names. A reviewed
public artist gig can be saved with source excerpt/date, linked to public artist
and venue records. It does not become a booking by the user's band. Ambiguous
identities and suppressed/archived records require manual resolution. Artist and
gig directories support the same band isolation, aliases, merges and blacklist
rules as other records; cross-band private profiles are never shared.

Opportunity ranking adds exact saved-genre matches, documented prior bookings,
documented replies/declines and application status. Each weight is explained in
the directory. Unknown responsiveness is neutral. Expected fee can be recorded
with evidence but is not weighted without an agreed currency/fee policy.
Radius filtering uses reviewed/manual coordinates; unknown locations remain visible.

Public source revisits can be daily, weekly or every 30 days. Each schedule applies
only to its exact approved source URL. There are no recurring AI calls or automatic
Gmail imports. Source changes return to review and preserve manual records.

## Per-candidate AI extraction

A manager selects a pending candidate and previews AI extraction. Approval displays
the exact retained excerpt, source category, model, input/output caps, pricing
version and maximum reserved estimate. It explicitly authorizes disclosure to
OpenAI, including the token-count request. Gmail import approval alone is not AI
consent. Excerpt changes, expiry/removal, suppression, feature/role/key changes or
model/price/token-policy changes invalidate queued work.

Prerequisites: this band's tested encrypted key, allowlisted model with Responses
structured-output/token-count support, AI enabled, nonzero daily/monthly limits,
and operator-reviewed pricing. `BOOKING_AI_PRICING` is a JSON map of model IDs to
positive `input` and `output` USD-per-million rates and a nonempty `version` string.
No prices or model names are guessed. Enter values reviewed against the project's
current billing rates, using uncached input rates for conservative reservations.

The worker reserves the worst-case estimate, counts the exact input with
`POST /v1/responses/input_tokens`, rejects counts beyond 8,192 tokens, rechecks
authorization and budget, durably marks the request started, then calls Responses
with no tools, `store:false` and at most the lower of 2,048 or the band's configured
output cap. No SDK retry can replay a paid call. A lost or malformed usage response
keeps the full reservation and records an uncertain estimated attempt; recovery
cannot issue another inference for that run. Operator reconciliation must inspect
actual provider usage before adjusting uncertain reservations. Do not reset them
to pending/reserved. Incomplete/refused/invalid extraction still accounts for known
provider usage. Different explicitly approved runs are separate paid requests.

Structured output is checked again locally: each fact is an allowlisted field,
its exact quote must exist in the approved excerpt, and its value must appear in
that quote. ISO dates must be valid; application dates must also quote the event
cycle year. Natural-language dates are deliberately left for human interpretation.
Prompt instructions in source text have no tool access. Quotes establish textual
support, not truth or semantic correctness: every suggestion is labeled unverified
and displayed separately from the opportunity form. No automatic deadlines,
contacts, outreach, publication or calendar writes result from extraction.

Usage records contain metadata/tokens/estimates, never prompts or responses.
Derived analysis remains on the same-band candidate and is cleared with Gmail
evidence when it expires or is removed. OpenAI retention policies still apply;
`store:false` is not a promise of zero provider-side retention.

API references:
[Token counting](https://developers.openai.com/api/docs/guides/token-counting),
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
[Responses storage](https://developers.openai.com/api/docs/guides/migrate-to-responses).

## Release gates

Before live use, verify authorized provider accounts and plan/storage terms,
model availability/pricing, Google verification and mailbox consent, then exercise
provider failures and retention on staging. Production deployment remains a
separate operator-approved action. Phase 4 still owns drafts/templates, reviewed
Gmail sending, reply threading and explicit calendar publication.
