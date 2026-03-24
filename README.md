# BandMate

A self-hosted multi-track audio player and playlist manager for reviewing
mixes. Songs live as directories on your server; each directory contains
individual track files (MP3, WAV, FLAC, etc.) that are played back
simultaneously with per-track mute/solo controls, waveform visualization,
and a scrub-able playhead.

## Features

- **User authentication** – Django-based login; built-in admin console for user management.
- **Song browser** – Automatically discovers song folders in a configurable directory.
- **Multi-track player** – Web Audio API plays all tracks in perfect sync with volume and pan.
- **Playlists** – Create playlists of songs that play each song's "Master" track in sequence.
- **Waveform display** – Server-side peak generation (cached to disk) rendered on `<canvas>`.
- **Mute / Solo** – Standard DAW-style mute and solo per track.
- **Scrub / Seek** – Click or drag on any waveform to scrub. Keyboard shortcuts included.
- **Mobile-first UI** – Touch-friendly dark theme with fixed transport controls.

## Requirements

- Python 3.10+
- [FFmpeg](https://ffmpeg.org/) installed and on `PATH` (required by pydub for
  reading MP3/FLAC/etc.)

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
