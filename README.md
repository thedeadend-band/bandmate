# BandMate

A self-hosted multi-track audio player, setlist manager, and song creation
toolkit for bands. Songs live as directories on your server; each directory
contains individual track files (MP3, WAV, FLAC, etc.) that are played back
simultaneously with per-track mute/solo controls, waveform visualization,
and a scrub-able playhead.

## Features

- **User authentication** – Django-based login; built-in admin console for user management.
- **Song browser** – Automatically discovers song folders in a configurable directory. Upload songs as ZIP archives or individual files.
- **Multi-track player** – Web Audio API plays all tracks in perfect sync with volume and pan.
- **Download ZIP** – Download all tracks for a song as a single ZIP archive from the player page.
- **Setlists** – Build setlists with break markers, play master tracks in sequence, and export to PDF or MIDI.
- **Waveform display** – Server-side peak generation (cached to disk) rendered on `<canvas>`.
- **Mute / Solo** – Standard DAW-style mute and solo per track.
- **Scrub / Seek** – Click or drag on any waveform to scrub. Keyboard shortcuts included.
- **Synced lyrics** – Timestamped LRC lyrics scroll in sync with playback, with fullscreen mode.
- **New Song Wizard** – Step-by-step workflow to create songs from scratch:
  - Enter song info (title, artist) and look up tempo/key via GetSongBPM
  - Download audio from YouTube or upload your own stems
  - Beat detection, quantization with click-track intro
  - AI stem separation using a hybrid Demucs + BS-RoFormer approach (vocals, drums, bass, guitar, keys, other)
  - Selective stem separation — choose which stems to generate
  - Lyrics lookup via LRCLIB
  - Band details (guitar setup, vocals, who starts)
  - Review and finalize
- **Download Tracks** – Search YouTube, preview audio inline, and download tracks as FLAC files.
- **Calendar** – View the band's Google Calendar directly in BandMate.
- **Attributions** – Dedicated page with required backlinks for API partners (GetSongBPM, GetSongKey, LRCLIB).
- **Mobile-first UI** – Touch-friendly dark theme with fixed transport controls.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) installed and on `PATH` (required for audio format conversion)
- [rubberband](https://breakfastquay.com/rubberband/) installed and on `PATH` (required for audio quantization in the New Song Wizard)

## Quick start

```bash
# 1. Clone & enter the project
cd bandmate

# 2. Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Run migrations (creates the SQLite database)
python manage.py migrate

# 4. Create an admin / login user
python manage.py createsuperuser

# 5. Add songs
#    Create a "songs" directory (or set SONGS_DIR env var to an existing one).
#    Each subdirectory is a "song"; put audio tracks inside.
mkdir -p songs
#    Example layout:
#      songs/
#        My Song/
#          drums.wav
#          bass.mp3
#          guitar.flac
#          vocals.wav

# 6. Run the dev server
python manage.py runserver 0.0.0.0:8000
```

Open `http://<your-server>:8000` and sign in.

## Configuration

All settings can be overridden with environment variables:

| Variable | Default | Description |
|---|---|---|
| `SONGS_DIR` | `<project>/songs` | Absolute path to the directory containing song folders. |
| `WAVEFORM_CACHE_DIR` | `<project>/.waveform_cache` | Where cached waveform JSON files are stored. |
| `AUDIO_CACHE_DIR` | `<project>/.audio_cache` | Where cached compressed audio files (OGG) are stored. |
| `STAGING_DIR` | `<project>/.song_staging` | Temporary directory used by the New Song Wizard during song creation. |
| `DJANGO_SECRET_KEY` | *(insecure dev key)* | Set a strong secret for production. |
| `DJANGO_DEBUG` | `True` | Set to `False` in production. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated list of allowed hostnames. |

## Generating Song Metadata from a Spreadsheet

If you maintain song information in an Excel spreadsheet, the `generate_info.py` script can bulk-create `info.json` files for each song:

```bash
python generate_info.py /path/to/spreadsheet.xlsx --output-dir ./songs
```

This creates a directory per song named `[Artist] - [Title]` and writes an `info.json` inside it. The script reads the "Songs" sheet and extracts:

- Title, artist, tempo, key
- Guitar details (lead, rhythm, bass) with tuning, type (electric/acoustic), and capo
- Vocals (lead vs backing, detected from yellow cell highlighting in the spreadsheet)
- Who starts and what starts

If a song directory already exists (e.g. it already has audio tracks), the script adds the `info.json` alongside them.

## Downloading Lyrics from LRCLIB

The `download_lyrics.py` script searches [LRCLIB](https://lrclib.net/) for time-synced lyrics and saves them as `lyrics.lrc` files:

```bash
python download_lyrics.py /path/to/spreadsheet.xlsx --output-dir ./songs
```

For each row in the "Songs" sheet it looks up the track by artist and title, preferring synced (timestamped) lyrics. If only plain lyrics are available they are saved as a fallback. Existing `lyrics.lrc` files are skipped unless `--overwrite` is passed.

The directories follow the same `[Artist] - [Title]` naming convention used by `generate_info.py`, so running both scripts against the same spreadsheet populates each song folder with both `info.json` and `lyrics.lrc`.

## New Song Wizard

The New Song Wizard (staff-only, accessible from the sidebar or `/songs/new/`) walks through creating a fully prepared song directory with stems, click track, lyrics, and metadata.

### How it works

1. **Song Info** – Enter title and artist.
2. **Tempo/Key** – Look up tempo, key, and time signature via GetSongBPM (or enter manually).
3. **Track Source** – Choose to download from YouTube or upload your own audio files.
4. **Download/Upload** – Search YouTube and download audio, or upload a master track and individual stems.
5. **Beat Detection** – Analyze the master track for beat positions using librosa.
6. **Quantize + Click** – Time-stretch each beat segment to a uniform tempo and generate a click track with a 2-bar count-in.
7. **Stem Separation** – AI-powered stem separation using a hybrid approach: Demucs (`htdemucs_ft`) for vocals, drums, and bass; BS-RoFormer for guitar, keys, and other. Select which stems to generate — uploaded stems are preserved.
8. **Lyrics** – Search LRCLIB for time-synced lyrics, or paste your own.
9. **Band Details** – Guitar setup (tuning, type, capo), vocal assignments, who/what starts.
10. **Review** – Preview all stems, confirm details, and finalize. Audio files are converted to FLAC for storage efficiency.

If you navigate away from the wizard at any point, the in-progress session is automatically cleaned up.

### Dependencies for the wizard

The wizard requires several additional Python packages (included in `requirements.txt`):

- `yt-dlp` – YouTube search and audio download
- `librosa` – Beat detection and audio analysis
- `soundfile` – Audio file I/O
- `pyrubberband` – Time-stretching (requires `rubberband-cli` system package)
- `demucs` – Meta's AI stem separation
- `bs-roformer-infer` – BS-RoFormer stem separation for guitar/keys/other
- `torchcodec` – Audio codec support for PyTorch/torchaudio

### System dependencies

```bash
# macOS
brew install rubberband ffmpeg

# Ubuntu/Debian
sudo apt install rubberband-cli ffmpeg
```

## Download Tracks

The Download Tracks page (`/download-tracks/`, linked from the New Song Wizard start page) lets you search YouTube, preview audio inline with a seek bar, and download any result as a FLAC file. No wizard session or song creation required — useful for grabbing reference tracks.

## GetSongBPM API Setup (optional — tempo lookup)

The New Song Wizard can look up tempo and time signature automatically. To enable this:

1. Go to [getsongbpm.com/api](https://getsongbpm.com/api) and create a free account.
2. When registering, use `https://<your-domain>/attributions/` as the backlink URL.
3. Copy your API key from the dashboard.
4. In BandMate, go to **Settings** (sidebar) and paste the API key into the **GetSongBPM API Key** field, then save.

The free tier is sufficient for typical use. A link back to GetSongBPM.com is displayed in the wizard and on the Attributions page as required by their terms.

## GetSongKey API Setup (optional — key lookup)

The New Song Wizard can look up the musical key of a song automatically. This uses a separate API from GetSongBPM:

1. Go to [getsongkey.com/api](https://getsongkey.com/api) and create a free account.
2. When registering, use `https://<your-domain>/attributions/` as the backlink URL.
3. Copy your API key from the dashboard.
4. In BandMate, go to **Settings** (sidebar) and paste the API key into the **GetSongKey API Key** field, then save.

Both APIs are independent — you can configure one or both. If only one is configured, the wizard will perform whichever lookup is available and skip the other.

## Keyboard shortcuts (player page)

| Key | Action |
|---|---|
| `Space` | Play / Pause |
| `←` | Seek back 5 s |
| `→` | Seek forward 5 s |
| `Home` | Jump to start |
| `End` | Jump to end |

## Production notes

For a home-server deployment behind a reverse proxy (nginx, Caddy, etc.):

1. Set `DJANGO_DEBUG=False` and `DJANGO_SECRET_KEY` to a random string.
2. Run `python manage.py collectstatic` and serve the `staticfiles/` directory
   from your reverse proxy.
3. Use **gunicorn** as the WSGI server:
   ```bash
   pip install gunicorn
   gunicorn bandmate.wsgi:application --bind 0.0.0.0:8000
   ```
