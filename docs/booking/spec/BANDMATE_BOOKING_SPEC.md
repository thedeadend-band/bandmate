# BandMate Booking and Editable Calendar — Complete Implementation Specification

**Implementation specification for Cortex**\
**Target repository:** https://github.com/thedeadend-band/bandmate\
**Inspected branch:** `main`, tree SHA `18dc9fbbeee3201415e811977373541ac813f660` (reinspect HEAD before coding)\
**Status:** requested feature scope; implement and verify against the current repository

> Implement the feature in the existing BandMate Django application and Proxmox deployment. Do not create another repository, website, database service, or VM by default. Preserve existing BandMate features.

## 1. Product outcome

Add an opt-in **Booking** module to BandMate for discovering live-performance opportunities, learning from historical Gmail threads, maintaining booking contacts and deadlines, personalizing outreach, sending approved messages, and managing each opportunity from discovery through confirmed gig. The module should capitalize on existing BandMate bands/groups, login, calendar, song catalog, setlists, and band resources. **Separately, upgrade the existing BandMate Calendar page to support optional writable Google Calendar connections and BandMate-managed calendars**, with per-band/per-calendar write enablement and calendar-specific roles. Calendar editing must NOT depend on Booking being enabled. All AI expenses and Gmail access must be separate for each band.

**Personas:** a band/artist booking manager; authorized band collaborators; site administrator; background job worker. A solo artist is a normal BandMate `Group`, with one or more members—do not introduce a redundant artist-account hierarchy for MVP.

**Business goals:** capture historical contacts; find missed and future application windows; mine public artist calendars and venue schedules; discover similar venues/festivals/municipal series within configurable radius; verify source and deadline details; prepare tailored messages; track replies and follow-ups; blacklist unwanted opportunities; update automatically on a per-band schedule.

**Non-goals (MVP):** fully autonomous sending, automated bulk cold-email campaigns, unrestricted social scraping, automatic purchasing, unattended changes to external calendar events, editing read-only ICS subscriptions, shared OpenAI keys, access to the visual AI-agent project, deployment of a separate UI, automatic creation of users' OpenAI projects.

## 2. Verified repository baseline (do not overwrite blindly)

This section is grounded in the inspected public `main` branch. Verify against current HEAD in the existing Cortex session before changing code.

| Existing element | Verified location / behavior | Implementation implication |
|---|---|---|
| Framework | `bandmate/settings.py`, Django 4.2–5.x compatible requirement `django>=4.2,<6.0`; server-rendered templates and JS | Create a Django `booking` app; use existing UI/components/theme. No Next.js requirement. |
| Persistence | `bandmate/settings.py` uses `db.sqlite3` SQLite | Keep SQLite for initial single-host build; design safe jobs and optional PostgreSQL migration later. Do not assume PostgreSQL already exists. |
| Bands | `player.models.Group` (name, logo, color scheme) | Use `Group` as canonical tenant/band identity. |
| Membership | `player.models.GroupMembership` links `User` and `Group`, no per-group role field | Add explicit **booking-specific roles** before enabling connection/secret/sending operations. |
| Active band | `player.middleware.ActiveGroupMiddleware` selects `request.active_group` via session and group access; staff currently can select any group | Every booking URL and query must re-check group membership and booking role. Site staff cannot silently read private emails/keys. |
| Navigation | `player/templates/player/base.html` sidebar, `nav_active` and group theme | Add one conditional `Booking` nav item when enabled/authorized. |
| Calendar | `player.models.CalendarSource` and `CalendarEvent`; `player/calendar_sync.py` imports ICS feeds; `views.calendar_view` renders feed events | Preserve existing read-only ICS sync/rows. Add separate OAuth-backed Google calendars (read-only or explicitly editable), BandMate-managed local calendars, event editing on the SAME Calendar page, and Booking overlays. See §10. |
| Setlists | `player.models.Setlist` / `SetlistEntry`, group-scoped, optional date | Link booked gigs to group setlists later; no changes to audio metadata required for MVP. |
| Songs | `GroupSong` group catalog and file-backed `SONGS_DIR` | Use group song catalog in optional future EPK/setlist suggestions; no need to expose music files to OpenAI. |
| Background audio | `player/job_queue.py` uses threads and a file lock for stem/pitch processing | **Do not reuse audio job queue** for remote research, OAuth, or Gmail processing. |
| Deployment | `PROXMOX.md`, `deploy-service.sh`, `update-service.sh`: Debian LXC example, repo at `/srv/bandmate`, virtualenv, Gunicorn `bandmate.service`, Cloudflare Tunnel option | Extend this working deployment with separate booking worker and scheduler systemd services/timers. Do not replace it with Docker. |
| Existing sync | `bandmate-calendar-sync.timer` runs `manage.py sync_calendars` | Keep ICS timer; add a dedicated Google Calendar incremental-sync timer/command with failure isolation, and a separate Booking worker/scheduler. |
| Existing project handoff | `docs/CODEX_HANDOFF.md` | Continue in existing Cortex session, reconcile with current branch and document new feature there. |

**Important observed authorization nuance:** Site staff currently can choose any `Group` through group selection. This behavior must not automatically grant access to a group's Gmail messages, OAuth credentials, OpenAI key, booking data, or send permission. Define a special audited support flow if operators genuinely need access; default deny.

## 3. Core architectural decision

Implement **one BandMate web application** plus **independent booking background execution** in the same codebase. Do not connect the booking module to the visual-agent repo, containers, database, persona, or local LLM. Default AI calls are outbound HTTPS from the BandMate LXC directly to OpenAI using the selected band's credential.

```
Browser (existing BandMate login and selected Group)
  └─ Django BandMate app (existing Gunicorn, templates, auth)
       ├─ player/               existing songs, setlists, calendar, audio
       ├─ calendar_integrations/ NEW additive Google/local calendar features (or player module)
       ├─ booking/              NEW Django app: routes/views/models/services/templates
       ├─ SQLite initially      player + booking schema, band-scoped
       └─ booking service layer
            ├─ Credential vault (encrypted per-band OpenAI + Gmail OAuth)
            ├─ OpenAI client (key explicitly resolved by group_id)
            ├─ Gmail client (authorized mailbox explicitly resolved by group_id)
            ├─ Research adapters (search engine/public venue calendars)
            ├─ Dedup/provenance/ranking/draft engine
            └─ Job dispatcher

systemd bandmate-google-calendar-sync.timer -> sync opted-in Google calendars
systemd booking-scheduler.timer -> management command to enqueue due jobs
systemd booking-worker.service -> dedicated managed process with safe queue lease

Each band: independently enabled, independently authenticated, private records,
separate key and ideally separate OpenAI API project, independent budget/usage.
```

**Deployment decision:** Extend existing Proxmox LXC by default, not a new VM. If future workloads become heavy, move the worker to another VM/CT that runs the same source and connects to the approved DB/service; the UI and authentication remain in BandMate. No GPU required for this OpenAI-hosted workload. For SQLite, the worker must remain on the same host/storage; changing to remote worker requires an appropriate shared transactional DB, typically PostgreSQL.

**Suggested code tree (adjust to project conventions):**

```
booking/
  __init__.py
  apps.py
  models.py                  # or models/ package
  urls.py
  views.py                   # thin request handlers
  forms.py
  permissions.py             # all authorization checks
  services/
    credentials.py           # server-side encrypted secrets
    openai_client.py         # group-bound credential factory
    gmail_oauth.py
    gmail_sync.py
    discovery.py
    source_fetch.py
    dedup.py
    extraction.py
    ranking.py
    outreach.py
    calendar_overlay.py
    calendar_export.py         # explicit, idempotent booking -> editable calendar publishing
    budgets.py
    audit.py
  jobs/
    dispatch.py
    tasks.py
  management/commands/
    booking_enqueue_due.py
    booking_worker.py
    booking_check.py
  migrations/
  templates/booking/
  static/booking/
  tests/
    test_tenant_isolation.py
    test_permissions.py
    test_credentials.py
    test_gmail.py
    test_discovery.py
    test_outreach.py
    test_jobs.py
    test_calendar.py
    test_deployment.py

docs/booking/
  ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  INSTALL_UPDATE.md
  GMAIL_OAUTH.md
  SECURITY.md
  OPERATIONS.md
```

Additional calendar feature files, in `player/` or a **new additive Django app** named `calendar_integrations/` (choose based on repository conventions; no parallel web product):

```
calendar_integrations/
  models.py                    # OAuth accounts, selected calendars, cached events, local calendars
  permissions.py               # group membership + calendar manager/editor/viewer checks
  services/google_oauth.py    # calendar-only incremental consent, encrypted token storage
  services/google_sync.py     # initial/full/incremental sync, 410 recovery
  services/event_mutations.py # provider-aware create/update/delete with etags
  management/commands/sync_google_calendars.py
  tests/test_calendar_permissions.py
  tests/test_google_calendar_sync.py
  tests/test_calendar_mutations.py
```

Prefer standard Django ORM and templates. Do not add a new framework, database, broker, or API service without a demonstrated need.

## 4. Band activation, membership, authorization

### 4.1 Enablement lifecycle

1. BandMate site admin sees **Booking enabled** toggle on the group's administrative settings form (`admin_group_edit` or an adjacent group-feature panel), OFF by default for existing/new bands. The site-wide module code may be installed but tenant records/jobs must not run when disabled.
2. When enabled, eligible members see the Booking sidebar, and authorized booking managers can complete setup: OpenAI key, Gmail OAuth, geographic preferences, templates, budget, discovery cadence.
3. If required integrations are missing, show **Setup required**; read-only manually entered contacts/opportunities remain usable without an API key.
4. Disablement immediately hides navigation and blocks booking URLs/actions, shuts off scheduled work and outbound calls, but **retains records** for later reenablement subject to retention policy. Disabling does not revoke Gmail at Google or OpenAI credentials automatically; provide separate Disconnect/Remove controls.
5. A group may represent an independent band or solo artist with its own settings; no other group's secret or booking data is inherited.

### 4.2 Role model (new)

`BookingMembershipRole(group, user, role)` with unique `(group,user)` and a valid `GroupMembership` requirement; values:

- `booking_manager`: manage settings, credentials, Gmail connection, templates, contacts, automation, budget, sending, suppression, exports.
- `booking_editor`: research and edit opportunities/contacts; draft email and request approval; no secrets or sending by default.
- `booking_viewer`: read booking opportunities, deadline calendar, and non-sensitive aggregated activity; no credentials; hide email body/recipients unless explicitly authorized.

Membership in a group is **not** by itself permission to configure an OpenAI key, connect a Gmail mailbox, or send email. Site `is_staff` grants the ability to enable a feature, not permission to inspect tenant secrets or inbox contents. A global support impersonation/break-glass mechanism (if implemented) must require explicit band authorization and detailed audit, otherwise avoid it entirely. Only an authorized band member may hold a booking role. Define deterministic bootstrap assignment of `booking_manager` when a group is newly enabled (e.g. site admin designates a member), avoiding implicit manager role to everyone.

Every read/write/background action must take a **validated `group_id`** and check authorization. Do not trust posted `group_id`, path IDs, `session['active_group_id']`, or HTML visibility as authorization. `request.active_group` is a convenience, not a substitute for tenant scoping; for background tasks persist group_id at enqueue time and resolve credentials by it.

### 4.3 Independent Calendar administration and editing roles

The **Calendar feature is independent of the Booking feature toggle**: band members can use Calendar when Booking is disabled. Define explicit per-band `calendar_manager`, `calendar_editor`, and `calendar_viewer` capabilities, implemented using membership-linked permissions (or a coherent shared per-group role framework). Do not automatically inherit `booking_manager`, `is_staff`, or general group membership as Google Calendar write permission.

- `calendar_manager` (designated member): connect/disconnect calendar-only Google OAuth accounts; select imported calendars; opt a specific local or connected Google calendar into editable mode; assign calendar roles and event modification policies; view sync/connection health.
- `calendar_editor`: create/edit/delete events **only** on a BandMate-managed calendar or on an explicitly write-enabled connected calendar when Google permits the operation; cannot connect OAuth accounts or change another calendar's write policy.
- `calendar_viewer`: see non-private group-authorized calendar events and Booking milestones subject to Booking privacy checks; no mutation. Existing group-member Calendar viewing must continue working.

An assigned role is necessary but **not sufficient** for Google writes: also require (1) the selected Group owns the `ConnectedCalendar` row, (2) its `write_enabled` switch is ON, (3) its Google account has granted `calendar.events` (or a verified narrower scope that actually authorizes the operation), (4) current provider `accessRole` supports the specific event mutation, and (5) the event itself is mutable. `writerWithoutPrivateAccess` may not edit private events. No write by merely checking an editable-looking calendar in the UI. Admins do not receive implicit credentials access for other bands.

The site administrator may manage **feature availability**, but must not get another band's OAuth token or silently modify calendars. A designated band calendar manager controls consent and role assignment. The site admin initially designates one current GroupMembership as `calendar_manager` for a Group via the Group admin UI; neither staff nor all group members become calendar managers implicitly. Assignment and all mutation decisions must be audited. Calendar permissions apply to both UI and server endpoints, regardless of Booking enablement.

**Cross-tenant tests are release blockers.** A user of Band A must not read, update, send, delete, or even infer private Band B records by changing URL IDs, AJAX parameters, job IDs, redirect parameters, email thread IDs, or cached identifiers.

## 5. Per-band OpenAI keys, OpenAI projects, and usage

### 5.1 User requirement and OpenAI project semantics

Each band independently supplies its **own OpenAI API key**. For genuine separate costs on the OpenAI dashboard, recommend **one OpenAI API project per band**, with that project's dedicated key. A separate key *in the same OpenAI project* is useful for request identification but does **not** isolate the project-level spend dashboard, budgets, or resources. BandMate cannot assume users have permission to create projects automatically; provide setup instructions and entry form instead.

OpenAI documentation: https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform and https://platform.openai.com/settings/organization/projects.

### 5.2 Setup page

Per band (`/booking/settings/ai/`) provide:

- Enable/disable AI processing independently of the Booking module.
- API key paste-once field, masked thereafter; do not display the saved secret or send it back to browser.
- Optional OpenAI project identifier and human-friendly project label (informational, not a guarantee of billability boundaries).
- A **Test connection** action that makes a minimal approved request using the selected key; log success, permission errors, and configured model availability, never the key.
- Model choice from application allowlist (sane default configurable; no reliance on a fixed model name), per-task maximum tokens/request, daily request cap, monthly app-estimated spending cap, manual override authorization.
- Key rotation and revocation/removal controls; key fingerprint (last 4 or stable hash) for diagnostics, never expose full key.
- Link to OpenAI project Usage and Limits setup. OpenAI-side project hard limits/spend alerts are strongly recommended; the app's own budget gate should also stop new AI jobs. External usage may delay or slightly exceed enforceable limits.

Never fall back to a platform-wide `OPENAI_API_KEY` for another band's job. If Band B is not configured, its AI work must be blocked—even if Band A or site admin has a working key. Tests must prove this. Any optional system-owned public discovery with shared budget must be opt-in and separately modeled, never silently substituted.

### 5.3 Secure storage

Use an application-controlled master encryption key stored **outside Git** and **outside the SQLite database**, e.g. protected environment file/secret provisioned by the operator. Encrypt each band's OpenAI API key and Gmail refresh token at rest using a maintained authenticated encryption primitive/library (e.g. Fernet with managed rotation, or AES-GCM with versioned key IDs). Ciphertext fields in DB; plaintext in worker memory only while needed. Implement key rotation/migration, audited access, avoid logging headers and full exceptions that contain secrets. Backups must include the encrypted DB and a separately secured way to recover the master key; losing the master key makes ciphertext unrecoverable.

Beware existing `SiteSettings` has site-wide provider keys and the existing Spotify connection uses DB token fields. **Do not copy those patterns for private multi-band OpenAI/Gmail credentials.** A new secure vault is required.

### 5.4 Usage and budgets

Log *every* attempted OpenAI job to `BookingAIUsage`: group, job ID, task category, model, request ID if available, input/output/cached tokens if returned, request timestamp, observed usage, estimated USD, cost pricing-version, success/failure, and attributable key fingerprint/project label. Keep metadata, not sensitive prompts by default.

Booking usage UI: last 24h/7d/30d/current month totals; per model; per category (mail analysis, discovery, dedup, draft writing); running and failed jobs; budget remaining; historical chart/export. **Label self-calculated costs “estimated.”** OpenAI's organization/project official usage/cost analytics are authoritative for provider-side totals, and might include activity by other keys if a project is shared. Reconciliation through an authorized organization admin usage API is **optional**, not required, and must not demand a privileged org admin key from each tenant. Provide clear dashboard link and instructions for OpenAI-side project reporting.

Budget gate must be checked at enqueue and immediately before calling OpenAI; implement atomic per-band allowance reservation to prevent concurrent jobs exceeding configured limits. On depleted budget: queue/skip with visible `budget_paused` status, never use a different band's key.

## 6. Per-band Gmail connection and historical intelligence

### 6.1 Authorization and compliance

Use Google OAuth 2.0 Authorization Code + PKCE/state as supported by chosen Google libraries, with exact registered HTTPS redirect URI and CSRF protection. Store Gmail OAuth credentials per **Group** (not shared user-wide), record granting user, normalized authorized mailbox address, scopes, expiration, and last sync/error. A band manager can connect/disconnect/reauthorize the account. No background read or send without a valid authorization.

Scope planning: `gmail.readonly` (restricted) to analyze previous mail + `gmail.send` (sensitive) for user-approved sending. Avoid `mail.google.com` / `gmail.modify` unless a demonstrable requirement exists. Public multi-band availability with restricted Gmail scopes may require Google OAuth app verification and a security assessment when restricted-scope data is stored/transmitted; do not assume a personal/test-mode OAuth client will work for arbitrary external bands. **Treat Google verification as a production launch gate**, while providing mocked/test mailbox paths for development. Policy: https://developers.google.com/workspace/gmail/api/auth/scopes.

Do not forward entire email histories to OpenAI by default. For Gmail import, fetch targeted threads with search filters, minimize excerpts, redact unrelated sensitive details where feasible, document consent, show what data is sent to AI, and allow the band manager to disable AI processing of Gmail and use manual review instead. Enforce documented retention and deletion/export workflows for connected email data.

### 6.2 Mailbox setup

One active Gmail connection per band for MVP, with possible future multi-mailbox support. Forbid connecting the same mailbox to multiple bands by default to prevent accidental cross-band exposure; if supporting an exception later, require separate explicit authorization and distinct tenant-scoped sync/retention policies. No Gmail credential reuse between groups.

### 6.3 History import

Initial configurable date range (default since 2024-01-01) and optional Gmail search query (e.g. bookings, gig, festival, town, arts, summer concerts, application, entertainment coordinator). Use Gmail pagination, message IDs, history IDs and safe retry checkpoints. Import should:

1. Search SENT and relevant incoming conversations for venues, bookers, municipalities, festivals, summer series, breweries, fairs, talent buyers.
2. Extract structured `Opportunity`, `Organization`, `Venue`, `Contact`, `EventSeries`, and conversation relationships with confidence and source message references. Don't invent email addresses or dates.
3. Attach historical year, dates applied/answered, response type, booking outcome, next follow-up, and known annual deadline patterns.
4. Deduplicate via normalized email/domain, venue name + city, verified source URL, external event IDs and human merge decisions.
5. Keep inferred facts in a review queue with evidence, not auto-promote uncertain results. Never treat previous-year application dates as confirmed for next year.
6. Store only necessary email body excerpts, Gmail thread references, message timestamps, source hash, and relevant metadata; preserve audit provenance.

Incremental sync uses Gmail history API where viable and resyncs safely after an expired history cursor. Outgoing mail sent outside BandMate may update an existing opportunity but must never create an unauthorized cross-tenant link. Gmail API throttles, invalid grants, expired tokens, and disconnected account must be handled visibly.

## 7. Research and discovery automation

### 7.1 Configurable geography and search

Band settings: central address/place or map coordinate, geocoded lat/lon, straight-line radius in miles (editable), excluded cities, target years (default current plus next; for initial use prioritize 2027), musical genre/keywords, venue types and relative priorities. Distinguish miles straight-line from driving distance; geocoding and distance confidence must be shown. If source lacks location, tag `location_unverified` rather than silently excluding.

Discovery stages, with each originating band ID:

1. Historic Gmail-derived organizations/venues and recurring annual events.
2. Public official venue, chamber, city arts/culture, parks and recreation, fair/festival, downtown association, municipal events, and series calendars/application pages.
3. Public band/artist gig pages, official websites and indexed event listings for nearby cover bands; collect public booked venues and investigate those venues' application/contact routes.
4. Search for similar organizations/events within radius, including small venues, outdoor summer concert series, fairs and festivals, community events, private-event venues where appropriate.
5. Revisit known opportunities more frequently near expected application windows, and re-check sources that previously had no date.

### 7.2 Provider interfaces

Define interfaces `SearchProvider`, `SiteFetcher`, `PublicEventSource`, `Geocoder`, `AiExtractor`. Provider implementations may use a configurable licensed web search API, official calendars/ICS/JSON-LD pages, organizer websites, publicly accessible structured event listings. Default to Google/Meta-independent sources where possible. Never claim Facebook/Instagram support unless authorized official API access for those particular data types has been verified. Do not circumvent authentication, bot protections, CAPTCHA, or terms; if blocked, save a lead and ask for manual review or a URL entered by a user. Store provider quotas and rate limits. Prefer outbound requests with domain/redirect/DNS/address safety checks to prevent SSRF.

### 7.3 Evidence and annual deadlines

Every extracted contact, opportunity and date field has provenance: source type, canonical URL/message reference, retrieved date, observed excerpt/snapshot hash, confidence, verification state, and last checked. Separate:

- `verified_deadline` — date explicitly published for the relevant application cycle/year.
- `predicted_window` — forecast from prior cycles (e.g. 2026 deadline informs possible 2027 timing) with explanation.
- `unknown` — needs discovery or outreach.

Never label predictions as official. Preserve `event_year` separately from `deadline_date` and `application_open_date`. Create alerts for approaching verified deadlines and early check-ins for predicted windows. Mark stale links and removed pages, with manual overrides preserved rather than overwritten by crawlers.

### 7.4 Ranking, de-duplication, and blacklisting

Rank with transparent inputs: geography, genre fit, known previous booking, dates, application status, confidence, expected performance fee if known, history/contact responsiveness, and user's saved priority. Allow sort/filter/search, tags, triage, notes, exports.

Blacklists apply **only to the active group** and can target venue, event/series, organizer, email, email domain, or exact opportunity. Default blacklist hides records from ordinary results and blocks suggestion/AI processing/outbound sending; blacklisted page supports reason/date/actor and explicit restore. Preserve enough normalized identifiers to avoid re-import/unblacklist on discovery; handle merges and alias changes. Opt-out/unsubscribe/“do not contact” always suppresses sending even if a booking record is restored until a manager explicitly and appropriately resolves it. Prevent sending to unsubscribed or previously rejected contacts where contact restrictions apply. No cross-band sharing of private blacklists.

## 8. Opportunities, contacts, and booking lifecycle

Suggested statuses: `discovered`, `needs_review`, `researching`, `qualified`, `ready_to_contact`, `drafted`, `contacted`, `awaiting_reply`, `follow_up_due`, `applied`, `under_consideration`, `offered`, `confirmed`, `declined`, `not_selected`, `closed`, `blacklisted`. Avoid conflating *event date* with *application deadline* or *reply date*.

Opportunity detail shows event/venue, source, year/season, location, contact, dates/deadline/opening status, history, previous-year performances, linked artist evidence, editable notes, activities, outreach timeline, follow-up reminders, model/AI confidence and `last_verified`. Support manual add, import from URL, bulk review, merging duplicates, reopen annual editions.

`Venue` may host many opportunities; `EventSeries` has annual `Opportunity` editions; `Contact` can belong to multiple organizations; an event occurrence should not overwrite prior-year facts. A public entity record can optionally be shared as a **public-data cache** in a later phase, but every private relationship/activity/email/notes/template/status remains group-scoped. MVP may keep even public entities group-local to simplify isolation.

## 9. Email templates, personalized drafts, send and reply tracking

Per-band template manager with versioning and placeholders such as `{{band_name}}`, `{{venue_name}}`, `{{event_name}}`, `{{organizer_name}}`, `{{prior_contact_context}}`, `{{year}}`, `{{epk_url}}`, `{{available_dates}}`. Support type-specific templates (venue, festival, civic concerts, follow-up). Validate missing placeholders and preview examples. Add EPK/resource URLs without automatically uploading private song files.

Draft workflow:

1. Manager/editor chooses opportunity, contact, and template.
2. AI personalization generates a **draft only**, bounded by approved facts, public sources, previous relevant interactions and band profile. No fabricated prior meetings, commitments, fee offers, deadlines or EPK details.
3. Preview final recipients (To/Cc/Bcc), subject, body, attachments/links, Gmail identity, contact suppression status and duplicate-send warning. User can edit.
4. **Only authenticated `booking_manager`**, after a deliberate human click on **Send**, invokes Gmail `users.messages.send` via server with CSRF protection, fresh permission/blacklist checks, a send idempotency key, and secure thread-id handling. The background AI cannot call send autonomously. Gmail API errors must not be reported as successful sends.
5. Persist message/thread IDs, immutable exact approved content hash/snapshot, recipient, requesting user, timestamp and outcome; mark opportunity contacted only on confirmed send result. Protect against browser retry/double click; handle ambiguous network errors by checking Gmail sent state before retrying.
6. On replies, tie incoming Gmail thread to the opportunity, show response with appropriate privacy permissions, propose follow-up or status change, **never automatically send**. Offer scheduled *reminder* only.

Do not send bulk unsolicited emails or automatically bypass Google's sending limits. Include easy opt-out/suppression workflow; reconcile company sending policies and email marketing law as applicable. Rate limit deliberate sends per band and recipient and provide clear errors.

## 10. Two-way Calendar integration, editable calendars, and Booking overlay

**Core product requirement:** Include opt-in editable calendars in the existing BandMate Calendar page, even when Booking is disabled. Preserve existing read-only ICS subscriptions. Allow authorized calendar editors to create, edit, and delete events on eligible local and Google calendars. Build Google write support in the primary implementation phases.

### 10.1 Source choices and write behavior

| Source type offered by Manage Calendars | Reads | Writes from BandMate Calendar | Connection and policy |
|---|---|---|---|
| Existing ICS/webcal feed | Yes, existing sync | **Never**; there is no standards-based writable ICS feed | No OAuth; existing `CalendarSource`/`CalendarEvent` unchanged, labeled **Read-only** |
| Google Calendar — View only | Yes, Google Events API | **Never** | Google OAuth with read scope, enabled calendar source, no write toggle |
| Google Calendar — Allow editing | Yes, Google Events API | **Yes**: create, edit, delete events according to Bandmate role, Google's privileges, and event type | Additional explicit per-calendar **Allow editing** switch and Google write consent |
| BandMate calendar (local) | Yes | **Yes when local editing enabled**: create, edit, delete locally | Group-owned local calendar; manager opts into local write mode; designated calendar roles; no Google OAuth required |

Expose `Manage Calendars` source/add flow with choices **Subscribe to iCal**, **Connect Google Calendar**, and **Create BandMate Calendar**. When connecting Google, identify the Google account and let a manager choose individual calendars from `calendarList.list`; display external calendar name, timezone, `accessRole`, sync status, and mode `View only` or `Editable`. By default, newly connected calendars are **view-only**, and `write_enabled=False` until deliberate enablement; calendars with insufficient Google privileges are ineligible for write mode. An existing ICS URL must remain read-only even when it points to a Google Calendar export URL; to edit it, connect that Google account through OAuth instead.

The Bandmate Calendar page renders events from ICS feeds, connected Google calendars, and local calendars **together** with existing source filters and consistent appearance. Calendar event click opens a detail panel; `+ New event`, Edit, Delete appear only when the current user, source, and provider authorize the action. On unauthorized or read-only calendars, the panel offers **View details / Open in Google Calendar** when available, but no Bandmate edit controls. If multiple calendars are selected, require explicit target calendar selection for creation. Display Google sync/permission failures without hiding existing events.

### 10.2 Consent, OAuth scope, and provider enforcement

Use a **calendar-only Google OAuth credential per Group/account**, distinct from the band's Gmail OAuth grants; never treat Gmail authorization as Calendar authorization or silently expand Gmail scopes. Reuse the existing server-side Google OAuth client registration only if configured for both APIs and if compliance permits; store Calendar refresh credentials encrypted and bound to Group and granting member. Use Authorization Code with state/PKCE where supported and a registered HTTPS callback.

- Google read-only scopes: `https://www.googleapis.com/auth/calendar.events.readonly` plus `https://www.googleapis.com/auth/calendar.calendarlist.readonly` to allow calendar selection, subject to verified API behavior.
- Google editable scope: request `https://www.googleapis.com/auth/calendar.events` **only when a manager explicitly enables an editable connection**, using incremental authorization or reconnect as required. Do **not** request full `https://www.googleapis.com/auth/calendar` unless a separate, approved requirement demands calendar ownership/ACL administration. Do not request ACL-management scope.
- **Important scope nuance:** Google OAuth `calendar.events` applies broadly to events across calendars the authorized account can access; Google's token does not intrinsically constrain itself to Bandmate's one chosen calendar. Enforce the selected `external_calendar_id` and per-calendar `write_enabled` restriction on **every server-side write**; do not claim Google's OAuth grant is calendar-specific.
- Check `calendarList.accessRole` (e.g., `writer`, `owner`, `writerWithoutPrivateAccess`) both on onboarding and again before write or when permission changes. Google can reject changes to organizer-controlled, invited, private or specialized events even on writable calendars; surface the provider's error without forcing a write.
- No other band can read or reuse a connected account, token, selected calendar or event. If the **same external Google calendar** is intentionally connected to more than one band, require an explicit manager warning and acknowledgement that changes on the shared external calendar will become visible in both; do not leak Bandmate-private Booking metadata through cross-band sync.
- Provide a **Disable editing** control separate from **Disconnect**, instant on next request. Disabling write policy prevents all new writes but does not delete Google events. Disconnect revokes local authorization and stops sync/writes, with explicit explanation that existing remote events are not deleted.

Google's access roles include `writerWithoutPrivateAccess` for non-private events (introduced in 2026). The UI must not expose private event contents to a user restricted to busy-only information. Google verification/consent-screen restrictions should be documented for a multi-band public deployment; use mocked adapter tests until permitted to connect real accounts.

### 10.3 Actual calendar editing behavior and event fields

Add a native, accessible event create/edit modal or page to **`/calendar/`** rather than redirecting members to the Booking UI. Required fields: title, source/target calendar, start and end with timezone, all-day option, description, location, optional reminders where provider permits, and optional association with a Booking opportunity or setlist. Preserve existing values and timezone semantics on edits; display validation errors inline.

- `calendar_editor` may create events on selected writable Google calendars and write-enabled local calendars. Event deletion requires a visible confirmation showing title/date/calendar; removal only deletes the selected source event, not a whole `CalendarSource`/Google calendar.
- Enable modifying **existing external events** on an explicitly writable Google calendar when the Google account has event rights and the event is mutable; do not limit editing to Bandmate-created events. However, respect organizer/attendee ownership and private/special event types; if mutation is prohibited, show the provider-reported reason.
- For recurring events, preserve recurrence metadata and provide a clear scope selection (**this instance** vs **entire series**) if supported. If the initial implementation cannot safely edit an existing series, render it read-only with an explanatory message; **do not unintentionally modify the entire series**.
- No automatic invitations or notification emails unless requested and supported by chosen provider settings; surface any Google `sendUpdates` option intentionally and default to a documented safe policy. Never use destructive Google calendar/ACL endpoints for normal event operations.
- `CalendarEvent` rows sourced from ICS are **immutable imported cache**; do not write booking/local/Google records into them, since `sync_calendar_source` deletes/recreates them.
- External writes must be sent to Google *first* (or via an explicitly tracked outbox with reliable retry), and considered successful only on provider confirmation; show pending/failed states honestly. Use unique operation IDs and idempotency/reconciliation to avoid duplicate events during retry, and store Google event ID, `iCalUID` if supplied, `etag`, and last sync. Google-created changes should then reconcile back into cache, not appear twice.
- Before modifying/deleting external events, fetch latest ETag and use conditional request (`If-Match`) or equivalent concurrency control. On precondition failure, present **Event changed in Google; reload/compare before saving**. Never silently overwrite an externally changed event.
- Respect Google quotas, 429/5xx rate limits/backoff, token revocation, partial sync failures, deleted events, timezone/DST boundaries and all-day exclusive end dates.

### 10.4 Two-way synchronization and data ownership

Keep the original `bandmate-calendar-sync.timer` for `CalendarSource` ICS. Introduce a distinct `sync_google_calendars` management command and `bandmate-google-calendar-sync.timer` (e.g., every 5–15 minutes, final cadence configurable). Sync each enabled selected Google calendar using `events.list` initial full sync then **`nextSyncToken` incremental sync**; handle paginated results, deletion tombstones, and an expired/invalid token (`410 Gone`) by safe resync of *only that calendar*, not by deleting other source data. Never enqueue writes merely because a remote event changed.

Cache Google events in a **different model/table** from ICS `CalendarEvent`, retaining external IDs and versioning. Sync is two-way in the sense that changes made in Google appear in Bandmate and authorized edits in Bandmate are persisted to Google; do not publish local events to an external calendar without an explicit member action. Long sync requests must not run during the Calendar HTTP page render. Use a dedicated short-lived systemd service/timer or a bounded calendar worker, separate from Booking AI tasks and the audio processing queue. SQLite-safe short transactions and locks are mandatory.

Local Bandmate calendars store their own events; no external sync required. Prevent event duplication in combined view through stable IDs (`provider:connection:calendar:event`) and explicit Booking publication links—not title-only fuzzy suppression.

### 10.5 Booking relationships and explicit calendar publication

Render Booking application windows, verified/predicted deadlines, follow-up reminders and tentative/confirmed performances as an **independent, band-scoped Booking overlay** on the same `/calendar/` page (when Booking is enabled and user has appropriate Booking view role). Clicking a milestone opens the opportunity. Predicted deadlines remain visually distinguishable from verified dates.

For a confirmed gig, offer **Add to calendar** with a selector of eligible local/editable Google calendars. A permitted calendar editor explicitly confirms the proposed title, date, time, location, description and target calendar. Only then create the external/local event, store a durable `(group, opportunity, calendar, external_event_id)` publication link and avoid duplicate publication. Offer **Update linked event**, **Unlink**, and **Remove from calendar** as separate explicit actions with confirmations; changing an opportunity alone does **not** silently overwrite or delete a Google event. `booking_manager` does not automatically confer `calendar_editor` privileges; require both when linking from a Booking page.

For a booking milestone itself, editing the Booking underlying date may require Booking edit permission but is not permission to modify any external Google event. If a member changes a linked Google event directly from Calendar, retain the mapping and allow a guided reconciliation back to the Booking record rather than silently rewriting its verified source data. Link confirmed gigs to `Setlist` when the same Group owns it; future practice integration remains optional. Do not call OpenAI during calendar page load.

### 10.6 Calendar authorization matrix

| Action | Viewer / group member | Calendar editor | Calendar manager | Booking manager alone |
|---|---:|---:|---:|---:|
| View authorized group calendar event | Yes (privacy-limited) | Yes | Yes | Member view only |
| Add/edit/delete write-enabled local calendar event | No | Yes | Yes | No |
| Add/edit/delete event on read-only ICS or Google-view-only | **Never** | **Never** | **Never** | **Never** |
| Add/edit/delete event on Google calendar with editing enabled, valid write OAuth and provider event rights | No | Yes | Yes | No |
| Connect/disconnect Google Calendar and grant/revoke app-level editing | No | No | Yes | No |
| Enable specific calendar's `write_enabled` policy or manage calendar roles | No | No | Yes | No |
| View private Booking deadline/lead metadata | Booking role required | Booking role required | Booking role required | Yes |
| Publish a confirmed Booking gig to writable calendar | No | Requires Booking edit authority too | Requires Booking edit authority too | Requires Calendar edit authority too |

**Tests and API enforcement must implement this matrix, not just hide UI controls.** Revoking membership, calendar role, write toggle, or Google consent invalidates write permission immediately, including for a previously rendered page or stale AJAX request.

## 11. Main user interface and routes

Follow BandMate's existing server-rendered UI, `base.html`, theme/color selection, mobile-first styles and auth. New nav item **Booking**, only when enabled and user has appropriate role. Suggested routes (names adjustable):

| Route | Function |
|---|---|
| `/booking/` | Dashboard: approaching deadlines, leads, outreach, follow-ups, integration/budget status |
| `/booking/opportunities/` | Filterable searchable list; status, year, location, confidence, category |
| `/booking/opportunities/<id>/` | Detail, sources, contacts, activities, AI evidence, drafts |
| `/booking/contacts/` | Contact/organization directory; Gmail-derived, manual, public-source provenance |
| `/booking/artists/` | Public artist/cover-band gig research references |
| `/booking/templates/` | Band-specific versioned outreach templates |
| `/booking/blacklisted/` | Dedicated blacklist page and restoration workflow |
| `/booking/activity/` | Send history, sync activity, task failures, audit trail |
| `/booking/jobs/` | Job queue, scheduling, pause/resume, last run, quotas and errors |
| `/booking/usage/` | Per-band OpenAI token/request/estimated USD usage and budget |
| `/booking/settings/` | Onboarding, geography, categories, schedule, connect Gmail, AI key |
| `/booking/gmail/callback/` | OAuth callback; explicit state to group/user; route exemption in active-group middleware may be needed |

The **existing `/calendar/` interface must gain event create/edit/delete actions** and a calendar-specific connection-management flow. Suggested additional routes (method names/paths can be refined by Cortex, but semantics cannot be dropped):

| Route | Action |
|---|---|
| `GET /calendar/sources/` | Existing source management enhanced with `iCal`, `Google (view-only/editable)`, `BandMate local` choices |
| `GET /calendar/google/connect/` | Calendar-only OAuth connection with least-privilege read grants |
| `GET /calendar/google/callback/` | Bind OAuth state to user and group; never rely solely on selected session group |
| `POST /calendar/google/permissions/` | Manager-only explicit request/reconnect for event-write scopes |
| `POST /calendar/sources/<id>/write-mode/` | Manager-only enable/disable editable mode for that exact group calendar |
| `POST /calendar/events/` | Calendar editor: create event in a chosen writable calendar |
| `POST/PATCH /calendar/events/<id>/` | Calendar editor: update existing mutable event, with ETag check where applicable |
| `POST/DELETE /calendar/events/<id>/delete/` | Calendar editor: confirmed delete on eligible writable calendar |
| `POST /booking/opportunities/<id>/calendar/publish/` | Explicit cross-role-confirmed publication of gig into selected writable calendar |
| `POST /booking/opportunities/<id>/calendar/reconcile/` | Explicit update/unlink/remove flow for linked publication |

All mutations need CSRF, band membership, calendar role, selected-source write policy, and provider permission checks **on the server**. Never treat calendar source IDs or event IDs as trusted. All actions must be audited, including failed authorization/provider writes.

Data entry must be possible manually even when AI/Gmail disabled. Show setup errors cleanly, with group name visible in header so users know which tenant they are editing. Feature and permission checks apply to HTTP GET and POST, JSON endpoints, form actions and callbacks.

## 12. Domain model and constraints (proposed, refine with Django migrations)

Keep model names sensible; these are required *concepts*, not mandatory exact spelling. Use `ForeignKey(Group)` with indexed filters everywhere tenant-specific and `created_by`/`updated_by` for auditing. **Never use unscoped object lookups in request handlers.**

### 12.1 Configuration, credentials, roles

- `BookingSettings`: `group` OneToOne, `enabled`, `ai_enabled`, search center label/lat/lon, radius miles, genres, target years, search cadence/time zone, Gmail scan cutoff, retention flags, budget thresholds, notification settings, created/updated.
- `BookingMembershipRole`: group + user + role unique, validated against `GroupMembership`.
- `BookingOpenAICredential`: group OneToOne, encrypted key, key fingerprint, optional project ID/label, model allowlist, tested status/time/error code, rotated_at, revoked_at. Never store plaintext.
- `BookingGmailConnection`: group OneToOne, authorized mailbox unique by policy, connected-by user, encrypted refresh credential, granted scopes, access status, Gmail cursor/history id, last_sync, last_error, revoked_at.
- `BookingAIUsage`: group, AI job, provider, task type, model, tokens/cached tokens, request ID, estimated cost, pricing version, creation time, result/error code.
- `BookingBudgetReservation`: group, month/day, authorized/actual cost and job, unique job, concurrency-safe cap accounting.

### 12.1a Calendar data and write authorization (independent of Booking)

These records are available to all bands irrespective of Booking enabled state; reference `player.models.Group` and `GroupMembership`. Keep existing ICS `CalendarSource`/`CalendarEvent` rows backward-compatible, do not mutate/re-purpose them, and do not add provider IDs to ICS rows and assume they are editable.

- `GroupCalendarRole`: `group`, `user`, `calendar_manager|calendar_editor|calendar_viewer` or capability flags; enforce actual `GroupMembership` in all handlers. One-to-one/unique membership mapping as appropriate.
- `CalendarGoogleAccount`: `group`, account email, granting `user`, encrypted refresh token, granted scopes, token health/revocation, created/updated; support more than one distinct account per group only if product permits it. Calendar token never shared with Gmail or another Group.
- `ConnectedCalendar`: `group`, account FK, external calendar ID, name, color, timezone, provider `accessRole`, `write_enabled` **false by default**, `is_enabled`, `sync_token`, `last_sync`, `last_error`; unique `(group, account, external_calendar_id)`.
- `ConnectedCalendarEvent`: `group`, `ConnectedCalendar`, Google `eventId`, optional `iCalUID`, `etag`, recurring master/instance identifiers, event type/visibility, start/end/all-day/timezone/summary/description/location, last_seen, tombstone state; unique per `(connected_calendar, external_event_id)`. Do not expose private event fields beyond provider access.
- `BandmateLocalCalendar`: `group`, name, color, timezone, enabled, `write_enabled` (explicit manager-controlled setting); group-owned local source, editable only when enabled and by calendar editors/managers.
- `BandmateLocalCalendarEvent`: `group`, local calendar, event fields, optional booking/setlist associations, recurrence representation if supported, creator/last editor; no Google API side effects.
- `CalendarMutationAudit`: `group`, acting user, source/provider/event identity, old/new non-secret summary, provider request/correlation ID, outcome/error, `etag` before/after, timestamps; avoid exposing private descriptions to unauthorized audit readers.
- `CalendarWriteOperation`: group, selected calendar/event, action/create ID, idempotency key, request fingerprint, status (`pending|succeeded|failed|ambiguous`), provider ID/version; robust recovery when provider confirmation is ambiguous.

All Group/Calendar/Booking foreign keys must match. For calendar OAuth callbacks, store signed state recording intended `group_id`, `user_id`, nonce, scopes and expiry; revalidate membership and manager role at return. On revoked or insufficient permissions, downgrade to view-only or show blocked status; never opportunistically switch credentials to another band/account.

### 12.2 Research and opportunity data

- `BookingOrganization`: group, name, type, websites/domains, location, alias info, tags.
- `BookingVenue`: group, organization nullable, name, address, coordinates, website, capacity/genre fit when sourced.
- `BookingContact`: group, organization/venue links, name, role, normalized email, phone/links, source/provenance, opt-out flags.
- `BookingEventSeries`: group, organizer/venue, name, recurrence pattern/season, public source URL.
- `BookingOpportunity`: group, series/venue/organizer references, target year, category, location, deadline/open window, predicted window, event date or range, source/confidence, stage, score, assigned manager, priority, notes, timestamps.
- `BookingArtistReference`: group, artist/band, public source URLs, genres, evidence; optional `BookingArtistGig` with event/date/venue/source; no private third-party profile data.
- `BookingEvidence`: group, parent entity/field, source type/URL/Gmail ref, excerpt/hash, retrieved_at, verified_at, confidence, policy/adapter ID.
- `BookingIdentityAlias` / matching records to make dedup and blacklist persistent across renames.

### 12.3 Outreach, milestones, automation, audit

- `BookingTemplate`, `BookingTemplateVersion`: group, type, subject/body, placeholders, default flag, version, creator.
- `BookingDraft`: group, opportunity/contact/template version, AI provenance, edited final subject/body, approval state, creator, timestamps.
- `BookingOutreachMessage`: group, draft, Gmail message/thread IDs, recipients, content hash, user who sent, idempotency key unique per group, delivery status, sent_at, error.
- `BookingConversationReference`: group, Gmail message/thread ID, subject, date, relevant redacted excerpt, participant refs; unique `(group, gmail_message_id)`.
- `BookingSuppression`: group, type, normalized match, reason, created_by, created_at, active, optionally `do_not_contact` immutable until explicitly cleared. Unique active normalized scope.
- `BookingCalendarMilestone`: group, opportunity, type, start/end, verified/predicted; visual overlay independent of external calendars.
- `BookingCalendarPublication`: group, opportunity/milestone, target local or Google calendar, stable external event ID/etag, create-operation ID, last published snapshot/hash, status, audit; no cross-group FK. Explicit link only.
- `BookingFollowUp`: group, opportunity/message, due_at, assignee, state; reminder only.
- `BookingJob`: group, task type, provider, status, priority, scheduled_at, locked_by/lease_until, attempts, idempotency key, budget reservation, last_error, created/updated.
- `BookingRunHistory`, `BookingAuditEvent`: group, initiator, action, affected record, outcome, metadata **without secret values**, timestamps.

**Indexes/constraints:** unique group+normalized source/event-year where appropriate; unique Gmail message per group; tenant-filter indexes; durable send idempotency unique; due date indexes; no cross-group FK association (validate in model/service methods); default safe deletion behaviors (don't cascade-delete sent proof silently); transactional/audited suppression updates; restrict deletion of audit trail. Retain evidence for disputed dates, without storing excessive mailbox content.

## 13. AI workflow boundaries

AI is a **structured extraction and writing service**, not the workflow controller. Typed tasks:

- `extract_mail_booking_entities` (source message => proposed organizations/contacts/events and evidence spans)
- `classify_event_and_genre_fit`
- `extract_public_event_opportunity` (source HTML/text => fields + provenance)
- `match_deduplicate_opportunity` (candidate matches, never auto-merge weak matches)
- `infer_annual_application_window` (predicted only; explanation and source years)
- `research_followup_questions` (proposed search queries and sources)
- `draft_outreach_email` (fact-constrained subject/body)
- `summarize_incoming_reply` (propose status/follow-up, not send)

Use OpenAI structured output / JSON schema with server-side validation, bounded input sizes, model allowlist, explicit tokens and timeouts, redaction of unrelated Gmail content, prompt injection resistance. Sources are untrusted: external page/Gmail instructions must never override system rules, call arbitrary tools, reveal credentials, or send mail. Provider calls must be made only through the group-credential factory. Rate-limit external sources and cache results when permitted. AI must never invent an email, verified deadline, gig, application link, previous band history, or a source URL. If missing: `null` + reason and follow-up task.

## 14. Job scheduling and execution model

Use dedicated systemd-managed **booking** jobs, not request threads or audio queue. Baseline:

- `booking-scheduler.timer`: every ~15 minutes triggers `python manage.py booking_enqueue_due` to evaluate band-specific daily/weekly jobs and due date notifications.
- `booking-worker.service`: isolated process started by systemd, processes `BookingJob` with retry/backoff and safe shutdown. Initial single worker concurrency for SQLite; use reliable single-host advisory file lock plus DB state/idempotency. Never use naive `select_for_update` as your sole SQLite concurrency guarantee.
- `booking_check` command: print credentials *status only*, run due/timer state, queue health and recent failures; never echo credentials.
- Jobs per group: Gmail initial backfill, incremental Gmail sync, venue revisit, new venue discovery, competitor artist gig discovery, deadline recrawl, dedup review, usage/budget reconciliation, summary notifications.
- Suggested initial cadence: Gmail delta hourly; public research once daily; deadline recrawl daily within ~90 days of estimated window, otherwise weekly; low-priority broad discovery weekly; user-triggered jobs on demand. All configurable, constrained by per-band quota/budget and source rules.
- Disable/paused group should enqueue no new external work; running jobs check setting again before network calls. Per-band fair scheduling, throttling and runaway job prevention. Source-failure retry with bounded exponential backoff, no credentials in error text.
- `BookingJob` should store minimal payload references, not API keys and not full Gmail messages. Do not rely on process memory to persist progress.

**SQLite safety:** Use short DB transactions; never hold transaction while web crawling/OpenAI request runs. If multiple Gunicorn processes and worker contend, apply busy timeout and robust locking. Plan a targeted PostgreSQL migration *only* if observed write contention or scale demands it; document migration/rollback separately. Avoid Redis/Celery until needed; if adopted later it is an internal worker implementation detail, not a separate app.

## 15. Security, privacy, abuse, reliability

### Authorization and secrets

- Strict tenant scoping for every page, API call, model query, search index, cached result, export, background job, Gmail authorization callback, and audit record.
- Group/booking role changes should immediately invalidate access; user removed from GroupMembership loses booking access.
- No `is_staff` shortcut to private band secrets, Gmail or send; log exceptional support operations if ever supported.
- Encryption key never committed, no hardcoded fallback, never in HTML, JS, logs, telemetry, error trace, queue payload, prompts.
- OAuth state must bind current authenticated user, group, nonce, expiry, redirect; callback should not use a stale or unrelated active-group session. Safely handle middleware routing for callback and verify group membership afresh.
- Validate Gmail sender/mailbox in send path, handle token refresh/revocation. Never send from wrong band.

### External fetch security

- Enforce HTTPS where supported; disallow file://, ftp://, localhost, loopback, RFC1918/link-local/metadata endpoints, DNS rebinding and private redirect destinations; cap response sizes, redirects and timeouts; sanitize HTML and user-facing excerpts; guard XSS/CSRF.
- No unauthorized Facebook/Instagram scraping, login automation, bypassing access restrictions or anti-bot challenges. Respect source terms/robots and citations.
- Restrict user-entered sources/attachments, avoid malware downloads, sanitize CSV exports against spreadsheet formula injection.
- Never pass mailbox raw credentials to OpenAI or browser. Only extracts needed for explicitly configured AI tasks; document Google restricted-data handling requirements.

### Compliance/operations

- Provide privacy disclosure and band admin controls to export/delete collected Gmail data and disconnect/erase tokens. Define retention default (e.g. historical correspondence metadata retained only while connection/feature exists or per configured retention); support manual removal.
- Audit all key connect/remove/test, Gmail connect/disconnect, feature toggle, send, blacklisting, job scheduling, model/budget changes.
- No autonomous sending. Limit sends per band/recipient, make unsubscribe and contact restrictions visible.
- Monitoring must report email OAuth expiry, missed scheduled runs, failed research, sources stale beyond threshold, low budget and approaching deadlines. Errors visible in UI and service journal without secrets.

## 16. Proxmox LXC deployment, install/update/rollback

**Existing deployment observed:** `PROXMOX.md` describes a Debian Proxmox **LXC** (example ID `200` only, not necessarily actual container); `deploy-service.sh` installs in `/srv/bandmate`, creates `.venv`, `.env`, `bandmate.service`, `bandmate-calendar-sync.timer`; `update-service.sh` pulls repo, pip installs, migrates, collects static and restarts. Treat live host, container ID, backups and service names as operator-verified before changing anything. Keep current Cloudflare Tunnel/reverse-proxy routing; no new public port needed. Direct OpenAI HTTPS from LXC unless outbound restrictions require a securely isolated egress proxy; existing visual AI-agent VM is **not** an application dependency.

### 16.1 New runtime dependencies and units

- Add necessary Python libraries using `requirements.txt` (OpenAI SDK, Google OAuth/Gmail API libraries, cryptography, selected search/geocoding integrations, network request safety). Lock/validate versions via existing practices. Do not force a dependency manager change.
- Create `booking-worker.service` and `booking-scheduler.service` + `.timer` from checked-in operator examples. Also add `bandmate-google-calendar-sync.service` + `.timer` (or an equivalent bounded command), **separate** from the existing ICS sync timer. Worker process must inherit appropriate working directory, restricted environment and secret master key. Run as dedicated unprivileged user where permissions permit; don't break existing song/audio file access.
- Add `.env.example` entries for server-side OAuth client ID/secret, **separate Gmail and Calendar callback URIs**, encryption key location, provider config, job settings; **per-band OpenAI keys, Gmail refresh credentials and Google Calendar refresh credentials are entered/authorized in UI and encrypted in DB**, not put in a single `.env` or shared with visual-agent. API key never placed in Git or startup arguments.
- Bind no additional externally reachable port. Extend Django `INSTALLED_APPS`, `urls.py`, templates, migrations and staticfiles. Preserve existing `bandmate.service` and audio/calendar units.
- Existing production is SQLite; before potentially long job runs, ensure DB backup and testing. Use SQLite online backup for consistent DB snapshot, not unsafe raw copy of live DB.

### 16.2 Initial deploy (developer must provide exact tested commands)

1. Verify target Git HEAD and record its SHA; run existing BandMate tests.
2. Back up `/srv/bandmate/db.sqlite3` with SQLite online backup and securely back up .env/master encryption key + relevant media; verify backups and restore path. Take optional Proxmox snapshot where available.
3. Install new dependencies into existing `/srv/bandmate/.venv` and ensure encryption master key/Google OAuth app are configured locally, without saving secrets to Git.
4. Run `python manage.py check`, `python manage.py makemigrations --check --dry-run`, `python manage.py migrate --noinput`, relevant tests, `python manage.py collectstatic --noinput`.
5. Install new booking systemd service/timer **and bounded Google Calendar sync service/timer**, `daemon-reload`, enable the units; ensure no duplicate worker and no enabled groups automatically trigger calls until fully configured.
6. Restart Gunicorn, verify existing songs/setlists/**existing ICS calendar**/wizard still work; test local Calendar event CRUD and Google read-only/editable modes via mocks with Booking disabled and enabled. Test one enabled band and real per-band Google credentials only with consent.
7. Document exact operator commands in `docs/booking/INSTALL_UPDATE.md` and update `deploy-service.sh`/`update-service.sh` with safe guardrails while preserving current installation choices.

### 16.3 Upgrade safety / rollback

Current `update-service.sh` continues with subsequent steps even after some failures; **do not rely on it unchanged** for migrations involving encrypted credentials or significant schema changes. Enhance it with `set -euo pipefail`, preflight, backup, step-level failure abort, service health checks, management of worker/timer, and a reliable lock to avoid concurrent deploys. Store recorded release SHA and backup timestamp. Never delete `.env`, master key, media or `db.sqlite3` on update. For rollback after **irreversible DB migrations**, check out compatible source **and restore the matching pre-upgrade database backup** (and any essential files), not merely `git reset`.

Define maintenance mode or brief service pause for incompatible migrations, test restore on a staging LXC, and provide a 1-command **operator-approved** rollback script with explicit restore prompts. Do not automate destructive rollback during a partial job failure. Send jobs should not be replayed after recovery without verifying Gmail Sent.

### 16.4 Operational minimums

- Service status and `journalctl` for `bandmate.service`, `booking-worker.service`, `booking-scheduler.timer`, `bandmate-calendar-sync.timer`, `bandmate-google-calendar-sync.timer`.
- Health dashboard for last tick, last discovery, last Gmail sync, **per-calendar Google sync and write authorization health**, per-band key status and budget, queued/failing tasks; no secrets.
- Backup schedule, rotation, recovery test, audit log retention; test a migration on a clone before prod.
- Ensure `Booking` never prevents normal BandMate use when Google/OpenAI/search is down.

## 17. Endpoints and service contracts

Prefer normal Django view routes; if AJAX is used, require CSRF/session and booking role checks. Planned semantic endpoints (HTTP methods adjustable):

```
GET  /booking/overview/
GET  /booking/opportunities/
POST /booking/opportunities/new/
POST /booking/opportunities/<id>/edit/
POST /booking/opportunities/<id>/blacklist/
POST /booking/opportunities/<id>/restore/
GET  /booking/blacklisted/
GET  /booking/contacts/
POST /booking/contacts/new/
GET  /booking/templates/
POST /booking/templates/new/
POST /booking/drafts/generate/
POST /booking/drafts/<id>/save/
POST /booking/drafts/<id>/send/         # manager-only; idempotency
GET  /booking/usage/
POST /booking/settings/ai/connect/      # manager-only; encrypt immediately
POST /booking/settings/ai/test/
POST /booking/settings/ai/remove/
GET  /booking/gmail/connect/
GET  /booking/gmail/callback/
POST /booking/gmail/disconnect/
POST /booking/jobs/discover/
POST /booking/jobs/sync-gmail/
POST /booking/jobs/pause/
# Calendar API routes (can be in player.urls or calendar_integrations.urls)
GET  /calendar/google/connect/
GET  /calendar/google/callback/
POST /calendar/google/permissions/
POST /calendar/google/disconnect/
POST /calendar/sources/<id>/write-mode/
POST /calendar/events/
POST /calendar/events/<id>/
POST /calendar/events/<id>/delete/
POST /booking/opportunities/<id>/calendar/publish/
POST /booking/opportunities/<id>/calendar/reconcile/
```

Do not trust URL IDs as ownership. Each handler must retrieve data by `id` **and** `group=request.active_group` and verify the user's booking role. No GET endpoint performs send/delete/secret changes. Return safe errors and idempotent responses.

## 18. Implementation phases and acceptance criteria

### Phase 0 — repository integration, tenant enforcement, disabled feature

- Add new Django `booking` app, models/migrations, feature enablement toggle on Group, booking-specific role assignment and universal permissions utility.
- Conditional sidebar, skeleton dashboard/settings, **calendar-specific roles and opt-in write permissions**, no regressions in player, group switch, setlists or existing ICS calendar.
- Booking disabled by default; direct URL access blocked when disabled. Staff not auto-authorized to read private booking data.
- **Tests:** two groups/users and one staff; access controls on every route, ID tampering, disabled state, role changes and switching group mid-session.

### Phase 1 — Calendar editing foundation, local calendars, Google read-only and editable integration

- Update the existing Calendar UI to combine ICS, Google-backed, and BandMate-managed local calendars. Add native event editor, selection of target calendar, edit/delete confirmation, keyboard/mobile accessibility and conditional action controls.
- Add calendar-only per-group Google OAuth, separate from Gmail, choosing calendars by API with provider accessRole, read-only default and per-calendar editing opt-in; reauthorization for `calendar.events` only after explicit choice. Google connected calendar permission checks enforced server-side.
- Implement local calendar CRUD and Google event CRUD **including existing eligible events**, two-way sync (`nextSyncToken`, `410` recovery), optimistic ETag conflict handling, audit/idempotency, timezone and DST.
- Add `bandmate-google-calendar-sync.service` and timer, docs and OAuth verification guidance. No Booking enabled/AI key required to use Calendar editing.
- **Tests:** ICS cannot be edited, Google view-only cannot be edited even when token has broader scope, writable Google calendar can be edited only by calendar_editor/manager with provider rights, staff/booking_manager not automatically permitted, revoked consent/role/write-switch invalidates stale browser action, editing one band never changes another, external Google edits sync back, conflicts/recurrence handled safely.

### Phase 2 — independent OpenAI + Gmail onboarding

- Per-group encrypted key with masked UI, test/rotate/remove; per-band project instructions; no system key fallback; AI mock and real test mode.
- Per-group Gmail OAuth with secure callback; optional manual data entry while disconnected; compliance guidance for public use.
- Usage budget counters and audit, credential statuses. No sending yet.
- **Tests:** Band A key never used for Band B; no key or Gmail refresh token in log/HTML/backup plaintext; one band disconnected must not stop another; no email cross-scope.

### Phase 3 — Gmail backfill, records, discovery

- Historical Gmail import with review and dedup, contacts/organizations/opportunities/evidence; incoming delta sync.
- Public web discovery, nearby artist gig research, radius, sources, score, verified vs predicted dates; scheduler and worker with per-band quotas.
- Blacklist page, suppress rediscovery and outreach; deadline alerts and **Booking calendar overlay on the newly editable Calendar UI, independent of external Google writes**.
- **Tests:** idempotent Gmail sync, no duplicate opportunities across repeated crawls, predicted deadline never confirmed, blocked item stays suppressed, calendar still reads original ICS **and retains Google/local editing functions**.

### Phase 4 — outreach and end-to-end workflow

- Versioned templates; fact-grounded AI drafts; preview/edit/one-click human sending; Gmail thread sync and follow-up reminders; **explicit Add to calendar / update / unlink / remove flow for confirmed gigs, with both Booking and Calendar permissions**, per-band usage dashboard; full audit and reports.
- **Tests:** no autonomous send path, sending requires manager+explicit action, duplicate-click protection, revoked OAuth failure, opt-out prevents send, wrong-group thread not displayed/sent, sent evidence survives restart.

### Phase 5 — optional enhancements

- Shared **public-only** opportunity cache with strong provenance and tenant-private overlays.
- Integrations for officially available social APIs, additional search providers, organizer application form tracking (never unattended submission), lead assignments, EPK assets, gig-to-setlist/practice workflow (Google Calendar writes are already included in the core calendar phase).
- Database/worker scaling (PostgreSQL, Redis/Celery) only if justified by load.

**Release gates per phase:** migrations reversible where possible; run existing+new tests; no secrets in source/logs; test-disabled no effect; complete deployment/rollback docs; demonstrate in UI; no fictional provider success. Do not auto-enable booking for all groups during upgrade.

## 19. Critical test matrix

| Scenario | Required outcome |
|---|---|
| Booking disabled for Group B | No sidebar, page or job access, despite Group A being enabled. |
| User belongs to A and B, active=A, URL references B record | Request denied, no data leak. |
| Global Django staff selects Group C | No inbox, OpenAI key or send without explicit booking role. |
| A key valid, B key absent/invalid | A works; B fails gracefully, never uses A. |
| A and B have distinct keys/projects | Usage ledger, budgets, job quotas and dashboard remain separate. |
| Same Google OAuth callback reused or group switched mid-flow | Invalid state/cross-group connection rejected. |
| Token revoked after draft | Send fails safely, no false `sent` status. |
| Two sends from browser double-click / retry | At most one delivered message (or explicit ambiguous state requiring reconciliation). |
| Contact blacklisted after draft generated | Send blocked at final preflight. |
| Venue changes name on website | Alias/dedup preserves blacklist and outreach history. |
| 2026 deadline found; 2027 not published | 2027 deadline is visibly predicted, never verified. |
| Repeated crawl/import/sync | Stable records, evidence and idempotent jobs; no duplicates. |
| ICS resync for Group A | Existing events survive booking overlay; booking milestones are not deleted. |
| Booking disabled but editable Calendar enabled for Group A | Calendar event editing still works for authorized A members. |
| ICS feed or Google view-only source, user clicks forged edit request | Server rejects; no local or external mutation. |
| Manager toggles Google calendar `write_enabled=False` while stale edit form open | Stale save rejected server-side and audited. |
| User has `booking_manager` but no calendar editor role | Cannot mutate external/local calendar, cannot publish gig. |
| User is `calendar_editor` but has no booking access | Can edit eligible calendar events but cannot see or publish booking-private data. |
| Google writable OAuth token but chosen calendar is Google `reader` | Reject write and display account access limitation. |
| Google account has `writerWithoutPrivateAccess` | Non-private event permitted; private event content and edits withheld. |
| External event updated on Google during Bandmate edit | ETag conflict requires reload/compare, no silent overwrite. |
| Google sync receives 410 invalid token | Resync only chosen calendar, preserve ICS, other groups and local events. |
| Google event create retries after uncertain timeout | No duplicate creation; reconcile operation ID/external ID before retry. |
| Recurring series editing | User sees explicit instance/series scope or operation is safely refused. |
| Google Calendar write OAuth disconnected/revoked | All further writes blocked immediately, existing remote events untouched. |
| Same external Google calendar connected by A and B | Explicit warning/acknowledgement of shared remote effects, no cross-tenant private metadata leak. |
| Third-party HTML prompts “ignore previous rules” | Model outputs bounded schema; no secrets or network/send privilege. |
| SSRF URL redirects to localhost / metadata IP | Fetch rejected. |
| OpenAI budget reached mid-schedule | Remaining new AI work pauses for that band, other bands unaffected. |
| Worker killed during sync/send attempt | Safe checkpoint/idempotency; no silent duplicate send. |
| Existing song wizard/audio queue active | Booking work doesn't interrupt or hijack audio queue. |
| Rollback after schema change | Matching backup restore procedure tested on staging. |

## 20. Required artifacts in the existing GitHub repository

- Source and migrations for `booking/`, test suite, minimal dependency additions.
- Admin/group setting for enablement; role UI, sidebar, settings, onboarding, all dashboard pages.
- **Full Calendar upgrade**: local calendars, per-group Google calendar OAuth/read-only/editable sources, calendar roles and per-source write toggle, event CRUD UI, Google incremental sync, conflict handling, audit, and Booking overlay/source filters.
- `booking-worker.service`, `booking-scheduler.service`, `booking-scheduler.timer`, **`bandmate-google-calendar-sync.service` and `.timer`** operator artifacts; scripts must work in existing LXC.
- Edits to `deploy-service.sh`, `update-service.sh` with safe upgrades, backups and worker management; **do not rewrite the deployment to Docker**.
- `docs/booking/ARCHITECTURE.md`, `IMPLEMENTATION_PLAN.md`, `SECURITY.md`, `GMAIL_OAUTH.md`, **`CALENDAR_OAUTH_AND_EDITING.md`**, `INSTALL_UPDATE.md`, `OPERATIONS.md`.
- Add a short section to `docs/CODEX_HANDOFF.md` and `README.md` summarizing Booking and feature gating.
- Fixtures/mocks so core UI can run and tests pass without secret keys or Gmail connection.
- Acceptance report showing tests/results and any external API verification that is still pending.

## 21. Questions for the developer to capture, without blocking Phase 0

The developer should log these as editable decisions in `docs/booking/IMPLEMENTATION_PLAN.md`, use defaults for foundational implementation, and ask owner only where necessary for real integration:

1. Should the *site admin* enable Booking for any band (default), or may a band-designated manager enable their own feature? Site admin default.
2. Are managers allowed to read the full Gmail messages, or only extracted booking snippets? Recommend snippets plus authorized drill-down for managers.
3. One Gmail mailbox per group now (default) vs multiple organizers' mailboxes later.
4. Would use of OpenAI be restricted to distinct project IDs per group, or merely warn about project reuse? Recommend **warn and detect duplicates**, since BandMate cannot independently prove project ownership from a pasted key.
5. Where are current Proxmox LXC backups/master secrets safely stored? Operator must confirm before enabling live credentials.
6. Default geographical origin/radius, discovery categories, language, and cadence. Provisional: Bonney Lake/Lake Tapps, WA; 75 straight-line miles; 2027 focus; 90s alternative/rock cover band; editable per group, never hardcode globally.
7. Default per-band AI spend threshold and job quotas. No arbitrary paid requests until a manager configures a limit and key.
8. Google OAuth external production review/verification path and domain; mock/test connections until approved.
9. Which licensed search/geocoding source to use? Adapter choice configurable; do not build prohibited social scrapers.
10. Calendar defaults: `calendar_viewer` for all group members; a designated calendar_manager assigns editor permissions. New Google connections read-only by default, editing must be explicitly enabled on each selected calendar; Bandmate local calendars available without OAuth.
11. Whether calendar event invite/attendee management and complex recurring-series editing are in initial launch; default to standard event fields, safe recurrence restrictions and explicit `sendUpdates` behavior. Do not block basic create/edit/delete for eligible non-recurring Google events.
12. Public Google OAuth approval and separate Calendar consent-screen scope verification; mock/test until allowed.

## 22. Authoritative external references

- OpenAI project and keys: https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform
- OpenAI key security: https://help.openai.com/en/articles/8304786-keeping-your-openai-account-secure
- Gmail OAuth scopes and verification: https://developers.google.com/workspace/gmail/api/auth/scopes
- Gmail API docs: https://developers.google.com/workspace/gmail/api/guides
- Google Calendar scopes: https://developers.google.com/workspace/calendar/api/auth
- Google Calendar list and accessRole: https://developers.google.com/workspace/calendar/api/v3/reference/calendarList
- Google Calendar event API: https://developers.google.com/workspace/calendar/api/v3/reference/events
- Google Calendar incremental sync: https://developers.google.com/workspace/calendar/api/guides/sync
- Google Calendar ETag conditional updates: https://developers.google.com/workspace/calendar/api/guides/version-resources
- Django: https://docs.djangoproject.com/
- BandMate code: https://github.com/thedeadend-band/bandmate

**Final implementation principle:** BandMate retains one cohesive existing Calendar page with explicitly opted-in event editing independent of Booking; BandMate Booking adds optional intelligence and calendar publishing. Every write, credential, cost, email, job and calendar connection is scoped to exactly one band and verified at request time. The visual AI-agent project is not involved.
