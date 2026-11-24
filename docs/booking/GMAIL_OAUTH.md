# Gmail onboarding

Phase 2 implements separate read-only Gmail authorization and explicit connection
checks. Phase 3 adds targeted imports; Phase 4 adds separately authorized reviewed
sending and scoped reply checks. See [outreach](OUTREACH.md).

Configure `GMAIL_OAUTH_CLIENT_ID`, `GMAIL_OAUTH_CLIENT_SECRET` and an exact registered
HTTPS `GMAIL_OAUTH_REDIRECT_URI` ending in `/booking/gmail/callback/`. These settings
never fall back to the Calendar OAuth client. A Booking manager acknowledges the
read-only connection on the Connections page before consent starts.

The grant requests only `https://www.googleapis.com/auth/gmail.readonly`. State is
bound to user, session, Group, connection generation and a ten-minute expiry;
PKCE verifiers are encrypted and erased after consumption. Changed band/role,
expired/replayed consent and disconnect/reconnect races fail closed. The callback
checks the authenticated mailbox with `users/me/profile`, normalizes it to lower
case and enforces one mailbox per band through a unique DB constraint. A collision
never discloses the other band's identity.
[Profile API](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile).

Refresh tokens are encrypted with the band's `gmail-refresh` purpose. Explicit
profile tests run in the Booking worker, with fresh feature/role/generation checks,
bounded retries and visible sanitized errors. A revoked grant disconnects only
its own band. No page load scans mail. Import approval does not send email content
to OpenAI; subsequent AI extraction needs separate per-candidate disclosure consent.

Disconnect removes the current mailbox address, encrypted token and history cursor
and invalidates pending consent. Connection audit records remain; secured backups
follow operator retention. Users may revoke Google-side authorization separately.
Existing imported data follows its approved retention period or can be removed
from Research & review; disconnect does not erase reviewed records or excerpts.

Managers preview an inclusive UTC date range, simple keywords, message cap and
retention duration, then approve that exact scope. Preview alone makes no Gmail
request. The worker searches matching messages and reads only selected headers
and Gmail snippets (up to 1,000 retained characters), never full bodies/attachments.
Candidates are suggestions; correspondence dates are never event dates/deadlines.
There is no automatic AI processing or scheduled mailbox import. See
[separate AI consent and retention](DISCOVERY_PROVIDERS.md).

Successful complete runs retain a scope-bound history checkpoint. Subsequent
identical scopes use history deltas intersected with the approved search; expired
history falls back to that targeted search, never an unrestricted mailbox scan.
Deleted messages observed in history clear retained content and derived candidate
AI analysis. Expiry, export and
manager removal operate only within the current band. Content-free tombstones
prevent re-import; human-reviewed records and notes remain separate.
See [limits and recovery](RESEARCH_AND_RECORDS.md),
[metadata reads](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get),
[history](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list)
and [synchronization](https://developers.google.com/workspace/gmail/api/guides/sync).

`gmail.readonly` is restricted. Production multi-band availability requires the
applicable Google verification/security-assessment gates; mocked tests do not
satisfy them. Phase 4 implements separately consented `gmail.send`, bound to the
existing verified mailbox, and exact-content manager-approved sends. Read-only
reconnect does not inherit send capability. Uncertain sends are never replayed.
[Google scope requirements](https://developers.google.com/workspace/gmail/api/auth/scopes).
