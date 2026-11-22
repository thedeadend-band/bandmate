# Cortex Implementation Instructions — BandMate Booking and Editable Calendars

We are adding **BandMate Booking**, an integrated, opt-in, multi-band booking intelligence and Gmail outreach feature to the **existing BandMate repository**, not creating a new product.

Repository: https://github.com/thedeadend-band/bandmate

Read the attached `BANDMATE_BOOKING_SPEC.md` and `PROXMOX_INSTALL_UPDATE.md` completely. The specification defines the full product scope, acceptance tests and required deliverables. Implement this feature directly in the existing BandMate repository and deployment; do not build a second web application, separate frontend, separate repository, or new Proxmox VM by default. Treat this as an initial complete implementation request.

## Required decisions and architecture

1. BandMate already supports **multiple bands (Django `Group`)**, distinct users and memberships.
2. Booking must be **enabled separately for each band**, default OFF. It should be possible to add an artist as a normal BandMate group and enable Booking there.
3. Each Booking-enabled band must bring its **own OpenAI API key**, preferably one from a **distinct OpenAI project**, so OpenAI platform project usage and spend limits can be monitored separately. Key entry is per band, encrypted, private, testable, rotatable and NEVER shared or transparently inherited. A different key in the *same* OpenAI project alone does not produce separate project-level billing.
4. Each band manages its own Gmail OAuth authorization, historical mail import, contacts, opportunities, templates, blacklists, research jobs, outreach and usage dashboard.
5. Booking uses OpenAI API directly, not my separately hosted visual AI-agent, its key, database or local LLM. Do not require access to my existing AI-agent VM unless independent networking constraints make a locked-down egress proxy necessary.
6. **Calendar requirement:** Upgrade the **existing `/calendar/` page itself** to allow event create/edit/delete on **explicitly selected writable Google Calendars** and **BandMate-owned local calendars**, while preserving existing ICS feeds as read-only and supporting Google read-only mode. This editing feature is **independent of Booking enablement**, and calendar write capability requires additional per-band roles, per-calendar edit enablement, Google write OAuth and provider rights. Integrate Booking milestones into this upgraded calendar, and ultimately setlist/song resources.
7. Calendar editing MUST be implemented in the core Calendar phase. A group calendar manager opts each selected Google calendar into editable mode (default view-only), and calendar editors/managers can change eligible **existing** Google events directly on BandMate Calendar, not only BandMate-created events. Google tokens are per band and separate from Gmail; `calendar.events` authorization does NOT limit the token to one calendar, so enforce the allowlist on the backend for each request.
8. No autonomous emails: a verified band `booking_manager` reviews the recipient, subject and full message and deliberately clicks **Send**. Implement send idempotency, blacklist/opt-out checks and audit history.
9. We need repeatable deployment/update/backup/rollback on the **existing BandMate Proxmox LXC**. Do not accidentally switch to Docker or a separate VM.

## Inspected repository baseline

The provided public `main` branch was inspected at tree SHA `18dc9fbbeee3201415e811977373541ac813f660`; your working tree may be newer. Reinspect current HEAD and any uncommitted work FIRST.

- Django project `bandmate/`; primary app `player/`; templates in `player/templates/player`; Gunicorn on systemd, not a SPA.
- `player.models.Group`, `GroupMembership`, `Setlist`, `SetlistEntry`, `GroupSong`, `CalendarSource`, `CalendarEvent` already exist.
- `player.middleware.ActiveGroupMiddleware` selects `request.active_group`; **site staff can currently select any group**, and `GroupMembership` has **no per-group roles**. Booking must have explicit `booking_manager`, `booking_editor`, `booking_viewer` authorization. Staff status alone MUST NOT grant Gmail/key access.
- `player/calendar_sync.py` imports iCalendar data and replaces `CalendarEvent` rows on sync. **Preserve those read-only ICS tables exactly.** Add separate models for Google calendar accounts/events (read-only or explicitly editable), local Bandmate calendars/events, and booking overlays. Google Calendar editing is a required core feature.
- `player/job_queue.py` handles audio stem/pitch jobs using a thread and file locking. Do not put booking jobs on that audio queue.
- `bandmate/settings.py` currently uses SQLite (`db.sqlite3`). Keep initial architecture compatible with SQLite, short transactions, single booking worker, and systemd timer. PostgreSQL only later if necessary.
- `PROXMOX.md`, `deploy-service.sh`, `update-service.sh` use `/srv/bandmate`, `.venv`, `.env`, `bandmate.service`, and `bandmate-calendar-sync.timer`. Preserve working operational paths and Cloudflare Tunnel setup. Introduce a separate **`bandmate-google-calendar-sync.timer`** for Google Calendar two-way reconciliation, without making HTTP page loads perform slow syncs.
- There is already `docs/CODEX_HANDOFF.md`; extend it with a short link/feature map when implementing.

## Expected behavior

- Per-band booking dashboard, opportunity list/details, contact directory, public nearby artist gig references, template management, blacklisted page, usage/budget page, job activity and configuration.
- Read previous relevant Gmail correspondence to build contacts/venues/festivals/series, discover public opportunities through legitimate web sources, verify current-year application dates, forecast unknown future windows as **predictions only**, and schedule daily/weekly rechecks.
- Configurable location/radius, artist genres, target years, source adapters, per-band job limits and spending caps.
- On the SAME existing `/calendar/` page, show ICS subscriptions + Google calendars (read-only or editable) + local calendars + Booking overlays together, with source filters. Include an event detail/editor for title, dates/times/timezone, all-day, location and description; guarded create/update/delete actions.
- **Per-calendar write capability:** only if band calendar manager explicitly enabled `write_enabled`, user is calendar_editor/manager, Google OAuth has calendar.events scope (when Google), provider calendar accessRole permits the operation, and event can legally be changed. `calendar_manager`, `calendar_editor`, `calendar_viewer` must be separate from `booking_manager/editor/viewer`; existing members remain able to view as before. ICS and view-only Google sources stay immutable.
- Include Google Calendar read-only and edit authorization, selection of Google calendar IDs, incremental event sync (`nextSyncToken` and 410 reset), robust Google ETag conflicts, recurring-event safety, idempotent writes, audit trail, revocation handling, provider-private event visibility restrictions and separate group-bound Calendar refresh credentials. No unauthorized background writes.
- Show deadline/application/follow-up/confirmed-gig Booking overlays on BandMate Calendar without modifying read-only ICS source tables; group-specific view. Publish confirmed gigs to a selected writable calendar only on an authorized explicit "Add to calendar" action, with stable event link and deduplication.
- Generate personalized **draft emails** from band templates and supported source evidence, human review/send through band Gmail, track outgoing Gmail IDs/replies and send idempotency.
- Strict cross-band isolation for every record, OAuth callback, secret, view, background task, export, blacklist and AI usage entry. No fallback to another band's API key.
- Google restricted-scope Gmail access will need compliance/verification for production with arbitrary external bands. Build mockable adapters and document the actual deployment requirements; do not claim the service is approved before verification.

## Execution instructions

**Phase 0: inspect/plan/foundation (START HERE).**

1. Inspect repository HEAD, current schema, active-group middleware, calendar view/template, existing ICS models/sync, setlists, deployment scripts, test suite and current uncommitted changes. Report conflicts or deviations from the inspected baseline, and preserve current repository state and any existing development decisions.
2. Create `docs/booking/ARCHITECTURE.md`, `docs/booking/IMPLEMENTATION_PLAN.md` with milestones and design choices; update `docs/CODEX_HANDOFF.md` briefly. Explain what is reused versus added.
3. Add a new Django `booking` app to the existing project, safe migrations, per-group Booking feature toggle disabled by default, booking roles, and a shared authorization utility. **In parallel lay the foundational calendar-specific roles and calendar source models**, which must work even when Booking is disabled. Integrate conditional sidebar. Add a first-run Booking setup/dashboard skeleton that works without external API credentials.
4. Test cross-band access, site-staff privacy, booking/calendar role changes, booking feature toggle, URL ID tampering, calendar source write toggles, and regression on existing BandMate capabilities. Do not introduce any route that leaks booking records to other groups.
5. Add documented, safely deployable systemd booking worker/scheduler stubs and a separate Google calendar sync timer/service stub, without enabling external calls for unconfigured bands. No new user-facing service port.

**Phase 1: Calendar editing functionality (before/alongside Booking external integrations).**

6. Enhance Calendar's existing manage-sources and main page to add `iCal feed (read-only)`, `Google (view only / allow edits)`, and `BandMate local calendar`. Build local calendar event CRUD using a new local table and calendar editor roles. Preserve legacy calendar UI, source filtering and ICS imports.
7. Implement Google Calendar OAuth per group, separate from Gmail grants; initial readonly scope, incremental consent for `calendar.events` only when a calendar manager requests editable mode. Select individual calendars using Google CalendarList, check accessRole and server-side `write_enabled` on every mutation, and encrypt OAuth refresh credentials.
8. Implement Google calendar event CRUD from existing BandMate Calendar page including existing eligible events; add provider sync (`nextSyncToken`, invalid token 410 recovery), ETag concurrency control, resync and retries, DST/all-day handling, and audit. Require explicit recurrence scope or safely disallow unsupported recurrence edits. Tests mock provider permissions/errors, cross-band access and stale toggles.

**Phase 2: credentials and Gmail.**

9. Build an encrypted per-band key store backed by a separately held encryption master key; no plaintext secret in SQLite, logs, HTML, prompts, process args or Git. OpenAI per-band connection test, model choice, budget, usage ledger and key rotate/remove.
10. Build band-specific Gmail OAuth connection and test mock adapters; use `gmail.readonly` and `gmail.send` as separate least-privilege scopes. Explicit state binding, secure refresh token encryption, reconnect/disconnect and production verification guide. Import historical booking conversations and maintain reviewable provenance. No sending yet.

**Phase 3: discovery and reminders.**

11. Add contact/venue/organizer/series/opportunity/evidence schema, Gmail extraction, dedupe, radius search, verified-vs-predicted deadlines, public web discovery adapters and configurable periodic jobs.
12. Add blacklisting and suppression/opt-out rules, including prevention of rediscovery reactivation; integrate Booking milestones into the upgraded editable Calendar page, without modifying any Google events until authorized explicit publication.

**Phase 4: outreach.**

13. Add versioned templates, evidence-grounded AI drafts, preview/edit, single-click authenticated manager-only Gmail send, idempotency, reply threading, follow-up reminders, audit and per-band usage dashboard.

**Phase 5: polish / optional.**

14. Link confirmed gigs to setlists, resource/EPK helpers and rehearsal workflow. Consider shared **public-only** venue cache after isolation is proven. Social data integration is optional and only through authorized/public supported means.

### Coding/test requirements

- Add tests for every new model and state transition, including two bands with different OpenAI credentials, two Gmail accounts, **two independently authorized Google Calendars with opposite write policies**, shared user membership, calendar role vs Booking role, staff-only behavior and Booking-disabled group with editable Calendar enabled.
- Use Django ORM, templates, existing CSS theme, existing session auth/CSRF; avoid full frontend rewrite. Treat Google Calendar event CRUD as core deliverable, with a server-side source and provider permission gate for every write.
- Use a managed booking worker and scheduler **plus separate Google Calendar sync timer** (systemd service/timer on LXC); never long-running remote AI or Google sync requests in a Gunicorn page render or the audio job thread.
- Do not auto-send mail, fabricate source-backed details, treat predicted dates as published, scrape behind restricted social logins, or make OpenAI requests without the current band's key/budget approval.
- All secrets must be supplied **outside this chat** in BandMate configuration. Mock external connectors for tests; mark real connector tests as pending if no authorized account.
- Do not deploy to my production LXC or change GitHub remote until requested/authorized. Produce code in reviewable increments and keep existing BandMate functionality operational.
- At the completion of each phase, show changed files, new migrations, commands to test, tests actually run, results, any external prerequisites and the next milestone.

### First response I want from you

Summarize the **current HEAD-specific implementation plan**, map exactly how new Booking and editable Calendar code will connect to the existing Group/membership/calendar/sidebar and Proxmox installation, enumerate separate Gmail and Calendar OAuth scopes/provider ACL requirements, and identify which calendar writes are enabled/disabled per source and group; then **begin implementing Phase 0** in the existing repository and run the tests you can run. If you need a decision that genuinely blocks live integrations, document a reasonable default and proceed with mock/test scaffolding instead of stopping foundation work.

Reference spec: `BANDMATE_BOOKING_SPEC.md`.
