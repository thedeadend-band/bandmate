# BandMate Codex Handoff

This document is a practical handoff for continuing development with Codex.
It focuses on the current architecture, high-impact files, and known pitfalls.

## 1) Current Product Scope

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
