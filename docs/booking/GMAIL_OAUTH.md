# Gmail connection contract

**Status: planned for Phase 2; no Gmail OAuth or sending is implemented.**

Use a separate grant and callback from Calendar. Request `gmail.readonly` for
explicitly authorized history import and `gmail.send` only for reviewed sending.
Do not request broad mailbox access or modify permission by default. Google's
verification requirements are a production launch gate, not something satisfied
by a personal test client. [Official scope reference](https://developers.google.com/workspace/gmail/api/auth/scopes).

Initial policy: one mailbox per band, normalized identity checked at connection
and send time; reject accidental mailbox reuse across bands. Encrypt refresh
tokens under the band's connection with the shared versioned vault. State must
bind authenticated user, Group, nonce, expiry, callback and intended scopes;
recheck manager role and consume state exactly once, even if the active band
changed while consent was open. No user-wide or site-global token fallback.

Import targeted date ranges/queries and minimal relevant excerpts with provenance,
review, consent, retention and deletion controls. Never forward an entire mailbox
to AI by default. Provider errors and revoked grants must block work visibly.

Phase 4 adds manager-reviewed sending with exact recipients/content, suppression
checks, CSRF, durable idempotency and reconciliation of ambiguous results. Gmail
read authorization alone never permits sending. No external integration tests or
Google approval have been performed in Phase 0.
