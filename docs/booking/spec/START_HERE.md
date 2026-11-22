# BandMate Booking + Editable Calendars

Complete implementation package for the **existing BandMate repository**:

https://github.com/thedeadend-band/bandmate

This package covers two connected but independently enabled capabilities:

- **Editable calendars:** Add BandMate-owned local calendars and OAuth-connected Google Calendars to the existing `/calendar/` page. Preserve ICS subscriptions as read-only. Make create/edit/delete available only when that calendar is explicitly enabled for writing, the signed-in member has the appropriate band-specific calendar role, and Google grants the necessary OAuth scope and provider rights. Calendar editing works even if Booking is disabled.
- **Booking intelligence:** An optional per-band module for Gmail history and outbound messages, venue/festival/series and artist discovery, deadline tracking, contact management, customized email templates, approved one-click sending, blacklists, scheduled research, and a per-band OpenAI API key with isolated cost reporting.

## Files

1. `BANDMATE_BOOKING_SPEC.md` — comprehensive, authoritative requirements, architecture, data model, UI, security, provider integrations, milestones, and acceptance criteria.
2. `CORTEX_IMPLEMENTATION_PROMPT.md` — instructions for Cortex to inspect the current repo and implement the specification in reviewable phases.
3. `PROXMOX_INSTALL_UPDATE.md` — safe extensions to BandMate's current Proxmox LXC, Gunicorn, SQLite, and systemd installation; tests, backup, update, recovery, and rollback guidance.

## How to use

Upload these three Markdown files into **your existing BandMate development conversation in Cortex**. Send the message below; Cortex should not need any older packages or change notes.

> Implement the attached **BandMate Booking and Editable Calendars** feature in the existing BandMate repository. Read `BANDMATE_BOOKING_SPEC.md` fully and follow `CORTEX_IMPLEMENTATION_PROMPT.md`, using `PROXMOX_INSTALL_UPDATE.md` for deployment design. Treat this as the initial, complete specification. Inspect the latest repository and existing development changes before coding. Begin Phase 0 and work in reviewable increments. Do not deploy to production without my approval.

## Firm requirements

- Both capabilities are integrated into the existing BandMate interface and use its existing band (`Group`) and user membership concepts.
- Booking is **off by default for each band**; editable Calendar access is **independent** of Booking's on/off state.
- Each enabled band uses **its own supplied OpenAI key**, ideally from its own OpenAI project, and its own Gmail OAuth connection and private records. No cross-band sharing or fallback.
- Calendar write permission is **off by default for every newly connected Google Calendar**. Google Calendar read-only vs editable OAuth scopes and actual provider access rights must be checked on every mutation. ICS remains read-only.
- Booking email sends are **always explicitly reviewed and initiated by an authorized band member**; AI cannot autonomously send mail.
- Run background research and Google synchronization via dedicated services, not the audio queue or synchronous HTTP requests.
- Preserve existing music playback, song creation, setlists, read-only ICS sync and current deployment.

## Repository inspection note

The package was designed after reviewing public `main` tree SHA `18dc9fbbeee3201415e811977373541ac813f660` on November 22, 2026. The actual working branch may have newer changes. Cortex must inspect live HEAD and uncommitted work, then adapt without overwriting newer code.

**The package is a development specification, not deployed code.** No API keys, secrets or user credentials are included.
