# BandMate Codex Handoff

This document is a practical handoff for continuing development with Codex.
It focuses on the current architecture, high-impact files, and known pitfalls.

## 1) Current Product Scope

Booking and editable Calendar development starts on `codex/booking-editable-calendars`.
Phase 0 is committed (`bba18cc`) on `codex/booking-editable-calendars`. The child
branch `codex/ai-booking-agent` adds Phase 1 local/Google Calendar editing and
Phase 2 per-band OpenAI/Gmail onboarding plus budget services, and Phase 3 records,
targeted Gmail import, public-source review, suppression and reminders. See
[architecture](booking/ARCHITECTURE.md), [six-phase plan](booking/IMPLEMENTATION_PLAN.md)
and [Phase 5 acceptance](booking/PHASE_5_ACCEPTANCE.md). New routes include
`/booking/connections/`, `/booking/usage/`, `/booking/records/opportunities/`,
`/booking/research/`, `/booking/blacklist/`, `/calendar/manage/` and `/calendar/events/`.
ICS rendering no longer performs synchronous sync; retain its existing timer.
Google sync and Booking research/checks use separate workers/locks from audio.
The scheduler now enqueues opt-in public-URL revisits and purges expired email.
Optional Brave Search/OpenCage adapters, reviewed public artist gigs, explained
genre/history ranking and separately consented budgeted AI extraction are implemented.
See [provider configuration](booking/DISCOVERY_PROVIDERS.md); adapters default off.
Phase 4 adds versioned templates, conservative AI draft arrangement, separate Gmail
send authorization, immutable reviewed sends, uncertain-delivery reconciliation,
scoped reply reads, explicit calendar publication and usage/activity exports. See
[outreach](booking/OUTREACH.md) for limits and recovery. Phase 5 adds
`/booking/gigs/` and `/booking/resources/`: confirmed-gig setlists, private notes,
reviewed EPK links and rehearsal planning/day reminders. Booking-only links from
Calendar and the setlist player preserve their independent permissions. See
[gig preparation](booking/GIG_PREPARATION.md) and additive migration `booking/0006`.
The planned local workflow is implemented; staging/live acceptance remains next.
No real email sends, paid inference or live provider calls were made in development.
No production migration/deployment or live-provider acceptance has been performed.
Use the backup-aware pinned updater described in [installation](booking/INSTALL_UPDATE.md).

BandMate is a Django app for:

- Multi-track song playback (mute/solo/pan, waveform seeking, synced lyrics)
- Setlist creation/editing/playback/export (PDF, MIDI)
- New Song Wizard (YouTube/download/upload, beat detect, quantize, stem separation queue)
- Queue management for stem-separation jobs
- Per-user Spotify connect + playlist import/export with review
- Song-level external linking (persistent Spotify + YouTube link metadata)
- Lyrics mode pages (setlist-driven and single-song)

## 2) Core Data and Storage Model

- Song audio + metadata is file-backed in `SONGS_DIR`:
  - One folder per song
  - `info.json` for metadata
  - `lyrics.lrc` for synced lyrics
- DB stores users, setlists, wizard/queue state, spotify auth, and app settings.

Main models in `player/models.py`:

- `Setlist`, `SetlistEntry`
- `SiteSettings`
- `SongWizard`
- `StemSeparationJob`
- `SpotifyConnection`

## 3) High-Value Files to Know

Backend:

- `player/views.py`:
  - song list/player endpoints
  - setlist CRUD and playback views
  - lyrics pages
  - spotify connect/import/export
  - song link update endpoint (`song_links_update`)
- `player/wizard_views.py`: wizard step flow + queue submit logic
- `player/wizard_services.py`: wizard background task logic + finalization
- `player/queue_views.py`: queue pages + polling/actions
- `player/urls.py`: route map

Frontend:

- `player/templates/player/song_player.html`: multitrack player + link panel
- `player/templates/player/song_list.html`: multitrack song cards
- `player/templates/player/lyrics_list.html`: lyrics hub cards
- `player/templates/player/lyrics_setlist_player.html`
- `player/templates/player/lyrics_song_player.html`
- `player/static/player/js/player.js`: multitrack playback/waveforms/lyrics sync
- `player/static/player/js/lyrics_player.js`: lyrics-mode playback
- `player/static/player/css/style.css`: most styling

## 4) Important Recent Behavior/Contracts

### Song-level provider links

Persisted in song `info.json`:

- `spotify_track_uri` (prefer canonical `spotify:track:...`)
- `youtube_video_id`
- `youtube_title` (optional)

`song_links_update` updates these fields and is used by the player-page link UI.

### Song list enrichment

`_get_available_songs()` in `player/views.py` now populates optional fields used by card UIs:

- `artist`, `title` defaults
- `spotify_track_uri`, `youtube_video_id`
- derived URLs: `spotify_track_url`, `youtube_video_url`

### Lyrics pre-roll behavior

`lyrics_player.js` intentionally keeps lyrics visible before first timestamp by
treating index `0` as active when playback time is before the first timed line.

## 5) Known Pitfalls (Do Not Regress)

1. **Song-name URL encoding**
   - Song names can include `&`, apostrophes, spaces.
   - Always use `encodeURIComponent(songName)` for client-built song API URLs.
   - Avoid embedding raw template-expanded URLs for paths that include song names.

2. **Template variable assumptions**
   - Some songs have partial metadata. Guard optional fields in templates or normalize in view helpers.
   - Missing keys (`artist`, guitar `type`/`capo`) previously caused noisy template lookup warnings.

3. **Static cache staleness**
   - When JS behavior changes, bump static query string in templates (`?v=...`) if users report stale behavior.

4. **info.json shape drift**
   - Keep metadata backward-compatible and resilient. Real-world songs may have sparse `info.json`.

## 6) Local Dev Checklist

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

If UI behavior looks unchanged after template/JS edits, hard-refresh browser and restart gunicorn in prod.

## 7) Suggested Codex Workflow

When making changes:

1. Locate impacted template + JS + backend endpoint together before editing.
2. Prefer small, vertical slices (endpoint + template + CSS + JS + quick validation).
3. Validate with:
   - `python -m py_compile player/views.py ...` for syntax
   - targeted manual browser checks for UI/interaction.
4. Keep `info.json` reading/writing centralized via helper functions in `player/views.py`.

## 8) Useful Project Docs

- `README.md` - setup + feature overview
- `GPU_SETUP.md` - GPU/audio-separator environment notes
- `PROXMOX.md` - container/deployment notes
- `docs/new-song-wizard-plan.md` - deeper wizard design context
