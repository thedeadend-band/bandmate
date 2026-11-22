# Security boundaries

Implemented in Phase 0:

- Group-scoped queries on every new object route; posted Group IDs are ignored.
- Membership-bound independent roles, database role constraints, no staff bypass.
- Staff-only feature availability and explicit initial-manager designation.
- Immediate membership/role/feature/source-policy revocation checks. Membership
  removal through group administration is atomic with audit and role cascade.
- CSRF and POST for new mutations; escaped templates and field allowlists.
- Metadata-only source models; no credential input, provider requests or send path.
- Default-off write policies; Google enablement rejected until live consent checks exist.
- Dedicated locked worker stub, zero-work scheduler/sync stubs, no audio queue use.

Before introducing credentials or external actions, implement:

- Authenticated encryption with an out-of-DB, out-of-Git versioned master key;
  rotation, backup recovery and redacted failure reporting. Suppress SQL debug
  logging for credential operations; the baseline development logger can print
  query parameters, so encryption alone is not enough for safe diagnostics.
- Separate tenant-bound Gmail and Calendar grants, one-use OAuth state with user,
  Group, nonce, scope, expiry and PKCE; fresh role verification on callbacks.
- Live Google provider ACL/event checks, privacy-limited caching, safe recurrence,
  ETags, durable write operation IDs and ambiguous-result reconciliation.
- Safe fetcher validation of DNS/IP, redirects, protocols, response limits and
  timeouts; source content treated as data, never instructions/tools.
- Per-band key factories, atomic budget reservations, no platform-key fallback.
- Durable jobs and fresh disablement/budget/authorization checks before each call.
- Audits of failed/successful provider mutations, secrets lifecycle and sending;
  retention/export/deletion policies and restricted-data consent.
- Manager-reviewed send only, exact approved content, suppression/opt-out,
  threading and idempotency. No autonomous sends or automatic invitations.

The inherited ICS fetcher, Spotify credentials and site-wide settings are outside
this foundation's secure credential design. Do not reuse them for new providers.
Provider integration is not production-ready until these later gates pass.
