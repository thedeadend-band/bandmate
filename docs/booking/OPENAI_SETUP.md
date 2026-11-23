# Per-band OpenAI setup

A Booking manager uses Booking → Connections to paste a key once. The saved key is
encrypted; subsequent pages show only a short hash fingerprint. Replacement bumps
its generation and invalidates queued checks and prior model-test results. Removing
the key also disables AI. BandMate never substitutes another band's or an environment
`OPENAI_API_KEY`. Project label/ID fields are informational, not proof of ownership.

Use a dedicated OpenAI project/key for each band; configure provider-side alerts
and review usage in the [OpenAI project dashboard](https://platform.openai.com/settings/organization/projects).
The application's estimates do not include other applications or project keys.

The operator sets `BOOKING_OPENAI_MODELS` to a comma-separated allowlist verified
against current provider availability; the default is empty. The band selects a
model, monthly estimated USD cap, daily UTC request cap and maximum output tokens.
AI defaults OFF, and zero caps stop inference. Do not assume a fixed model name
or static provider pricing remains valid.

“Test key connection” queues a server-side bearer-authenticated `GET /v1/models`
using only this band's key. It does not generate text. Allowed model IDs returned
by that request are saved as availability metadata. A successful model listing
does not prove inference entitlement, quota, rate limits or billing readiness.
[OpenAI authentication](https://developers.openai.com/api/reference/overview),
[model-list reference](https://developers.openai.com/api/reference/python/resources/models/methods/list).

The worker records every check attempt (including retry/error) without prompts or
secrets. Usage shows 24-hour, 7-day, 30-day and UTC-month totals. Costs are labeled
estimated. Paid inference is not implemented or tested in this increment.

Budget service contract for future handlers: reserve the worst-case priced input
and output allowance at enqueue; call `begin` again immediately before inference;
recheck the actor and exact credential generation; enforce token bounds in the
provider request; settle observed estimated usage with a pricing-version label.
Never release a started request with an unknown outcome. A request crossing UTC
midnight must release its unstarted reservation and obtain a fresh job/reservation.
Budget pauses must become visible job states when inference handlers are added.
Model/category breakdowns, charts, export and provider billing reconciliation are
later dashboard work, not implemented controls.
