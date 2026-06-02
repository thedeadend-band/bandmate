import hashlib
import base64
import json
import re
import shutil
import subprocess
import zipfile
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

import numpy as np
from django.utils import timezone
from datetime import timedelta
from urllib import request as urlrequest
from urllib import error as urlerror

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import Setlist, SetlistEntry, SiteSettings, SpotifyConnection


def _staff_required(view_func):
    """Allow only authenticated staff (is_staff) users."""
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        return view_func(request, *args, **kwargs)
    return wrapper

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.aiff', '.aif'}
LOSSLESS_EXTENSIONS = {'.flac', '.wav', '.aiff', '.aif'}

CONTENT_TYPES = {
    '.mp3': 'audio/mpeg',
    '.wav': 'audio/wav',
    '.flac': 'audio/flac',
    '.ogg': 'audio/ogg',
    '.aiff': 'audio/aiff',
    '.aif': 'audio/aiff',
}


def _songs_dir() -> Path:
    return Path(settings.SONGS_DIR)


def _safe_song_path(song_name: str) -> Path:
    """Resolve song directory path, guarding against path traversal."""
    songs_dir = _songs_dir()
    song_path = (songs_dir / song_name).resolve()
    try:
        song_path.relative_to(songs_dir.resolve())
    except ValueError:
        raise Http404
    if not song_path.is_dir():
        raise Http404
    return song_path


def _safe_track_path(song_path: Path, track_filename: str) -> Path:
    """Resolve track file path, guarding against path traversal."""
    track_path = (song_path / track_filename).resolve()
    try:
        track_path.relative_to(song_path.resolve())
    except ValueError:
        raise Http404
    if not track_path.is_file():
        raise Http404
    return track_path


def _get_tracks(song_path: Path) -> list[dict]:
    tracks = []
    for f in sorted(song_path.iterdir()):
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
            tracks.append({
                'name': f.stem,
                'filename': f.name,
                'extension': f.suffix.lower(),
            })
    tracks.sort(key=lambda t: (0 if t['name'].lower() == 'master' else 1, t['name'].lower()))
    return tracks


def _find_master_track(song_path: Path):
    """Return the first file whose stem is 'master' (case-insensitive)."""
    for f in song_path.iterdir():
        if (
            f.is_file()
            and f.stem.lower() == 'master'
            and f.suffix.lower() in AUDIO_EXTENSIONS
        ):
            return f
    return None


def _parse_lrc_time(time_str):
    """Convert 'mm:ss.ms' to float seconds."""
    m = re.match(r'(\d+):(\d+)\.(\d+)', time_str.strip())
    if not m:
        return 0.0
    mins, secs, frac = m.groups()
    return int(mins) * 60 + int(secs) + int(frac) / (10 ** len(frac))


def _load_song_info(song_path: Path):
    """Read info.json from a song directory. Returns dict or None."""
    info_path = song_path / 'info.json'
    if not info_path.is_file():
        return None
    try:
        data = json.loads(info_path.read_text(encoding='utf-8'))
        if data.get('lyric_offset'):
            offset = data['lyric_offset']
            if isinstance(offset, (int, float)):
                data['lyric_offset_secs'] = float(offset)
            else:
                data['lyric_offset_secs'] = _parse_lrc_time(str(offset))
        else:
            data['lyric_offset_secs'] = 0
        # Normalize optional nested keys to avoid template lookup noise.
        guitars = data.get('guitars')
        if isinstance(guitars, dict):
            for role in ('lead_guitar', 'rhythm_guitar', 'bass_guitar'):
                g = guitars.get(role)
                if isinstance(g, dict):
                    g.setdefault('type', '')
                    g.setdefault('capo', '')
                    g.setdefault('tuning', '')
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_song_info(song_path: Path, data: dict):
    info_path = song_path / 'info.json'
    info_path.write_text(json.dumps(data, indent=2), encoding='utf-8')


def _load_lyrics(song_path: Path):
    """Read lyrics.lrc and return list of {time, text} dicts, or None."""
    lrc_path = song_path / 'lyrics.lrc'
    if not lrc_path.is_file():
        return None
    try:
        lines = lrc_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return None
    result = []
    pattern = re.compile(r'^\[(\d+:\d+\.\d+)\]\s?(.*)')
    for line in lines:
        m = pattern.match(line.strip())
        if m:
            result.append({
                'time': round(_parse_lrc_time(m.group(1)), 3),
                'text': m.group(2),
            })
    return result if result else None


SPOTIFY_SCOPES = (
    'playlist-read-private '
    'playlist-read-collaborative '
    'playlist-modify-private '
    'playlist-modify-public '
    'user-read-private'
)
SPOTIFY_BREAK_TRACK_URI = 'spotify:track:5kiJ9x6eX37H5J9lyyXkua'


def _spotify_config():
    site = SiteSettings.load()
    client_id = (site.spotify_client_id or '').strip()
    client_secret = (site.spotify_client_secret or '').strip()
    return client_id, client_secret


def _spotify_redirect_uri(request):
    return request.build_absolute_uri('/spotify/callback/')


def _spotify_call(method, url, access_token, payload=None):
    data = None
    headers = {'Authorization': f'Bearer {access_token}'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urlrequest.Request(url, data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8') if resp.readable() else ''
            if not raw:
                return {}
            return json.loads(raw)
    except urlerror.HTTPError as e:
        detail = e.read().decode('utf-8', errors='replace') if hasattr(e, 'read') else str(e)
        raise RuntimeError(f'Spotify API error ({e.code}): {detail[:300]}')


def _spotify_refresh(connection):
    client_id, client_secret = _spotify_config()
    if not client_id or not client_secret:
        raise RuntimeError('Spotify client ID/secret are not configured in admin settings.')
    if not connection.refresh_token:
        raise RuntimeError('Missing Spotify refresh token. Reconnect your account.')

    token_url = 'https://accounts.spotify.com/api/token'
    creds = base64.b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('utf-8')
    body = urlencode({
        'grant_type': 'refresh_token',
        'refresh_token': connection.refresh_token,
    }).encode('utf-8')
    req = urlrequest.Request(
        token_url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        token_data = json.loads(resp.read().decode('utf-8'))

    connection.access_token = token_data.get('access_token', '')
    expires_in = int(token_data.get('expires_in', 3600))
    connection.token_expires_at = timezone.now() + timedelta(seconds=max(60, expires_in - 60))
    new_refresh = token_data.get('refresh_token')
    if new_refresh:
        connection.refresh_token = new_refresh
    connection.save(update_fields=['access_token', 'refresh_token', 'token_expires_at', 'updated_at'])


def _spotify_access_token(request):
    conn = getattr(request.user, 'spotify_connection', None)
    if not conn:
        raise RuntimeError('Spotify is not connected for this user.')
    if conn.token_expired():
        _spotify_refresh(conn)
        conn.refresh_from_db()
    return conn.access_token


def _spotify_playlist_id(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    if raw.startswith('spotify:playlist:'):
        return raw.split(':')[-1]
    if '/playlist/' in raw:
        p = urlparse(raw)
        try:
            return p.path.split('/playlist/')[1].split('/')[0]
        except Exception:
            return ''
    return raw


def _spotify_track_uri(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    if raw.startswith('spotify:track:'):
        return raw
    if '/track/' in raw:
        p = urlparse(raw)
        try:
            tid = p.path.split('/track/')[1].split('/')[0]
            return f'spotify:track:{tid}'
        except Exception:
            return ''
    if re.fullmatch(r'[A-Za-z0-9]{22}', raw):
        return f'spotify:track:{raw}'
    return raw if raw.startswith('spotify:') else ''


def _youtube_video_id(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', raw):
        return raw
    if 'youtu.be/' in raw:
        try:
            return raw.split('youtu.be/')[1].split('?')[0].split('&')[0]
        except Exception:
            return ''
    if 'youtube.com' in raw and 'v=' in raw:
        try:
            return raw.split('v=')[1].split('&')[0]
        except Exception:
            return ''
    return ''


def _spotify_search_tracks(access_token, query, limit=5):
    data = _spotify_call(
        'GET',
        f'https://api.spotify.com/v1/search?{urlencode({"q": query, "type": "track", "limit": limit})}',
        access_token,
    )
    tracks = []
    for t in (data.get('tracks', {}).get('items') or []):
        artists = ', '.join([a.get('name', '') for a in (t.get('artists') or []) if a.get('name')])
        title = t.get('name', '')
        label = f'{artists} - {title}' if artists else title
        tracks.append({
            'uri': t.get('uri', ''),
            'name': title,
            'artist': artists,
            'label': label.strip(' -'),
        })
    return tracks


def _get_available_songs() -> list[dict]:
    """Return all song directories that contain at least one audio track."""
    songs_dir = _songs_dir()
    songs = []
    if songs_dir.exists():
        for d in sorted(songs_dir.iterdir()):
            if d.is_dir() and not d.name.startswith('.'):
                tracks = _get_tracks(d)
                if tracks:
                    has_master = _find_master_track(d) is not None
                    info = _load_song_info(d)
                    entry = {
                        'name': d.name,
                        'track_count': len(tracks),
                        'has_master': has_master,
                        'artist': '',
                        'title': d.name,
                    }
                    if info:
                        if info.get('artist'):
                            entry['artist'] = info['artist']
                        if info.get('title'):
                            entry['title'] = info['title']
                        if info.get('spotify_track_uri'):
                            entry['spotify_track_uri'] = info['spotify_track_uri']
                            if info['spotify_track_uri'].startswith('spotify:track:'):
                                entry['spotify_track_url'] = (
                                    'https://open.spotify.com/track/'
                                    + info['spotify_track_uri'].split(':')[-1]
                                )
                        if info.get('youtube_video_id'):
                            entry['youtube_video_id'] = info['youtube_video_id']
                            entry['youtube_video_url'] = (
                                f'https://www.youtube.com/watch?v={info["youtube_video_id"]}'
                            )
                    songs.append(entry)
    return songs


# ---------------------------------------------------------------------------
# Auth views
# ---------------------------------------------------------------------------

@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def login_view(request):
    if request.user.is_authenticated:
        return redirect('song_list')

    error = None
    if request.method == 'POST':
        if getattr(request, 'limited', False):
            error = 'Too many login attempts. Please wait a minute and try again.'
        else:
            username = request.POST.get('username', '')
            password = request.POST.get('password', '')
            user = authenticate(request, username=username, password=password)
            if user is not None:
                login(request, user)
                return redirect(request.GET.get('next', '/'))
            error = 'Invalid username or password.'

    return render(request, 'player/login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

@login_required
@ensure_csrf_cookie
def song_list(request):
    songs = _get_available_songs()

    if request.user.is_staff:
        used_songs = set(
            SetlistEntry.objects.filter(is_break=False).values_list('song_name', flat=True).distinct()
        )
        for s in songs:
            s['in_setlist'] = s['name'] in used_songs

    return render(request, 'player/song_list.html', {
        'songs': songs,
        'nav_active': 'multitrack',
    })




@login_required
def song_player(request, song_name: str):
    song_path = _safe_song_path(song_name)
    tracks = _get_tracks(song_path)
    if not tracks:
        raise Http404
    song_info = _load_song_info(song_path)
    lyrics = _load_lyrics(song_path)
    return render(request, 'player/song_player.html', {
        'song_name': song_name,
        'tracks': tracks,
        'song_info': song_info,
        'lyrics': lyrics,
        'nav_active': 'multitrack',
    })


@login_required
def song_download_zip(request, song_name: str):
    """Stream all audio tracks for a song as a ZIP archive."""
    import io
    import zipfile

    song_path = _safe_song_path(song_name)
    tracks = _get_tracks(song_path)
    if not tracks:
        raise Http404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as zf:
        for track in tracks:
            file_path = song_path / track['filename']
            if file_path.is_file():
                zf.write(file_path, track['filename'])

        info_path = song_path / 'info.json'
        if info_path.is_file():
            zf.write(info_path, 'info.json')

        lyrics_path = song_path / 'lyrics.lrc'
        if lyrics_path.is_file():
            zf.write(lyrics_path, 'lyrics.lrc')

    buf.seek(0)
    safe_name = re.sub(r'[^\w\s\-]', '', song_name).strip()
    response = HttpResponse(buf.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{safe_name}.zip"'
    return response


# ---------------------------------------------------------------------------
# API views
# ---------------------------------------------------------------------------

def _get_compressed_audio(track_path: Path) -> tuple:
    """Return (file_path, content_type) — transcodes lossless to OGG if needed."""
    if track_path.suffix.lower() not in LOSSLESS_EXTENSIONS:
        ct = CONTENT_TYPES.get(track_path.suffix.lower(), 'application/octet-stream')
        return track_path, ct

    cache_dir = Path(settings.AUDIO_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_stat = track_path.stat()
    cache_key = hashlib.sha256(
        f'{track_path}:{file_stat.st_mtime_ns}'.encode()
    ).hexdigest()
    cache_path = cache_dir / f'{cache_key}.ogg'

    if not cache_path.exists():
        subprocess.run(
            [
                'ffmpeg', '-v', 'quiet', '-y',
                '-i', str(track_path),
                '-c:a', 'libopus', '-b:a', '192k',
                str(cache_path),
            ],
            check=True,
        )

    return cache_path, 'audio/ogg'


@login_required
def track_audio(request, song_name: str, track_filename: str):
    song_path = _safe_song_path(song_name)
    track_path = _safe_track_path(song_path, track_filename)
    serve_path, content_type = _get_compressed_audio(track_path)
    response = FileResponse(open(serve_path, 'rb'), content_type=content_type)
    response['Accept-Ranges'] = 'bytes'
    return response


WAVEFORM_CACHE_VERSION = 2  # bump to invalidate old caches


def _compute_channel_peaks(samples, num_peaks):
    """Normalise and compute [min, max] peaks from a 1-D float32 array."""
    max_val = np.max(np.abs(samples)) if len(samples) else 0
    if max_val > 0:
        samples = samples / max_val
    chunk_size = max(1, len(samples) // num_peaks)
    usable = chunk_size * min(num_peaks, len(samples) // chunk_size)
    if usable == 0:
        return []
    matrix = samples[:usable].reshape(-1, chunk_size)
    return np.column_stack([
        np.round(matrix.min(axis=1), 4),
        np.round(matrix.max(axis=1), 4),
    ]).tolist()


@login_required
def track_waveform(request, song_name: str, track_filename: str):
    song_path = _safe_song_path(song_name)
    track_path = _safe_track_path(song_path, track_filename)
    num_peaks = min(int(request.GET.get('peaks', 1000)), 4000)

    cache_dir = Path(settings.WAVEFORM_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)

    file_stat = track_path.stat()
    cache_key = hashlib.sha256(
        f'v{WAVEFORM_CACHE_VERSION}:{track_path}:{file_stat.st_mtime_ns}:{num_peaks}'.encode()
    ).hexdigest()
    cache_path = cache_dir / f'{cache_key}.json'

    if cache_path.exists():
        return JsonResponse(json.loads(cache_path.read_text()), safe=False)

    # --- probe duration + channel count -------------------------------------
    probe = subprocess.run(
        [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', str(track_path),
        ],
        capture_output=True, text=True,
    )
    probe_data = json.loads(probe.stdout)
    duration = float(probe_data['format']['duration'])

    src_channels = 1
    for stream in probe_data.get('streams', []):
        if stream.get('codec_type') == 'audio':
            src_channels = int(stream.get('channels', 1))
            break

    WAVEFORM_SR = 8000

    if src_channels >= 2:
        # Keep stereo — interleaved L R L R …
        raw = subprocess.run(
            [
                'ffmpeg', '-v', 'quiet',
                '-i', str(track_path),
                '-ac', '2',
                '-ar', str(WAVEFORM_SR),
                '-f', 's16le',
                '-',
            ],
            capture_output=True,
        )
        interleaved = np.frombuffer(raw.stdout, dtype=np.int16).astype(np.float32)
        left = interleaved[0::2]
        right = interleaved[1::2]

        # Normalise both channels with the same factor for accurate balance
        max_val = max(np.max(np.abs(left)) if len(left) else 0,
                      np.max(np.abs(right)) if len(right) else 0)
        if max_val > 0:
            left = left / max_val
            right = right / max_val

        chunk_size = max(1, len(left) // num_peaks)
        usable = chunk_size * min(num_peaks, len(left) // chunk_size)

        def _peaks_from(arr):
            if usable == 0:
                return []
            m = arr[:usable].reshape(-1, chunk_size)
            return np.column_stack([
                np.round(m.min(axis=1), 4),
                np.round(m.max(axis=1), 4),
            ]).tolist()

        data = {
            'channels': 2,
            'peaks_left': _peaks_from(left),
            'peaks_right': _peaks_from(right),
            'duration': duration,
        }
    else:
        raw = subprocess.run(
            [
                'ffmpeg', '-v', 'quiet',
                '-i', str(track_path),
                '-ac', '1',
                '-ar', str(WAVEFORM_SR),
                '-f', 's16le',
                '-',
            ],
            capture_output=True,
        )
        samples = np.frombuffer(raw.stdout, dtype=np.int16).astype(np.float32)
        data = {
            'channels': 1,
            'peaks': _compute_channel_peaks(samples, num_peaks),
            'duration': duration,
        }

    cache_path.write_text(json.dumps(data))
    return JsonResponse(data)


# ---------------------------------------------------------------------------
# Admin console – user management
# ---------------------------------------------------------------------------

@_staff_required
def admin_users(request):
    users = User.objects.order_by('username')
    return render(request, 'player/admin_users.html', {'users': users, 'nav_active': 'settings'})


@_staff_required
def admin_user_add(request):
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        is_staff = request.POST.get('is_staff') == '1'

        if not username:
            error = 'Username is required.'
        elif User.objects.filter(username=username).exists():
            error = 'A user with that username already exists.'
        elif len(password) < 4:
            error = 'Password must be at least 4 characters.'
        else:
            User.objects.create_user(
                username=username, password=password, is_staff=is_staff,
            )
            return redirect('admin_users')

    return render(request, 'player/admin_user_form.html', {
        'form_title': 'Add User',
        'error': error,
        'form_username': request.POST.get('username', ''),
        'form_is_staff': request.POST.get('is_staff') == '1',
    })


@_staff_required
def admin_user_edit(request, user_id: int):
    target = get_object_or_404(User, pk=user_id)
    error = None
    success = None

    if request.method == 'POST':
        new_username = request.POST.get('username', '').strip()
        new_password = request.POST.get('password', '')
        is_staff = request.POST.get('is_staff') == '1'

        if not new_username:
            error = 'Username is required.'
        elif (
            new_username != target.username
            and User.objects.filter(username=new_username).exists()
        ):
            error = 'A user with that username already exists.'
        else:
            target.username = new_username
            target.is_staff = is_staff
            if new_password:
                if len(new_password) < 4:
                    error = 'Password must be at least 4 characters.'
                else:
                    target.set_password(new_password)
            if not error:
                target.save()
                success = 'User updated.'

    return render(request, 'player/admin_user_form.html', {
        'form_title': f'Edit User – {target.username}',
        'edit_user': target,
        'error': error,
        'success': success,
        'form_username': target.username,
        'form_is_staff': target.is_staff,
    })


@_staff_required
def admin_user_delete(request, user_id: int):
    target = get_object_or_404(User, pk=user_id)
    if request.method == 'POST':
        if target.pk == request.user.pk:
            return redirect('admin_users')
        target.delete()
    return redirect('admin_users')


@_staff_required
def admin_settings(request):
    site = SiteSettings.load()
    success = None
    if request.method == 'POST':
        site.google_calendar_url = request.POST.get('google_calendar_url', '').strip()
        site.song_api_key = request.POST.get('song_api_key', '').strip()
        site.spotify_client_id = request.POST.get('spotify_client_id', '').strip()
        site.spotify_client_secret = request.POST.get('spotify_client_secret', '').strip()
        site.save()
        success = 'Settings saved.'
    return render(request, 'player/admin_settings.html', {
        'site': site,
        'success': success,
        'nav_active': 'settings',
    })


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@login_required
def calendar_view(request):
    site = SiteSettings.load()
    return render(request, 'player/calendar.html', {
        'calendar_url': site.google_calendar_url,
        'nav_active': 'calendar',
    })


def attributions(request):
    return render(request, 'player/attributions.html', {
        'nav_active': 'attributions',
    })


# ---------------------------------------------------------------------------
# Setlists
# ---------------------------------------------------------------------------

BREAK_SENTINEL = '__BREAK__'


@login_required
def setlist_list(request):
    setlists = Setlist.objects.select_related('owner').prefetch_related('entries')
    has_spotify = SpotifyConnection.objects.filter(user=request.user).exists()
    spotify_configured = bool(_spotify_config()[0] and _spotify_config()[1])
    return render(request, 'player/setlist_list.html', {
        'setlists': setlists,
        'has_spotify': has_spotify,
        'spotify_configured': spotify_configured,
        'nav_active': 'setlists',
    })


@login_required
def lyrics_home(request):
    setlists = Setlist.objects.select_related('owner').prefetch_related('entries')
    songs = _get_available_songs()
    return render(request, 'player/lyrics_list.html', {
        'setlists': setlists,
        'songs': songs,
        'nav_active': 'lyrics',
    })


@login_required
def lyrics_setlist_player(request, setlist_id: int):
    sl = get_object_or_404(Setlist, pk=setlist_id)
    entries = list(sl.entries.order_by('position'))

    items = []
    for entry in entries:
        if entry.is_break:
            continue
        try:
            song_path = _safe_song_path(entry.song_name)
        except Http404:
            continue
        master = _find_master_track(song_path)
        if not master:
            continue
        info = _load_song_info(song_path) or {}
        artist = info.get('artist') or ''
        title = info.get('title') or entry.song_name
        detail_bits = []
        if info.get('tempo'):
            detail_bits.append(f"{info['tempo']} BPM")
        if info.get('key'):
            detail_bits.append(str(info['key']))
        if info.get('time_signature'):
            detail_bits.append(str(info['time_signature']))
        items.append({
            'song_name': entry.song_name,
            'artist': artist,
            'title': title,
            'detail': ' · '.join(detail_bits),
            'display': f'{artist} – {title}' if artist else title,
        })

    return render(request, 'player/lyrics_setlist_player.html', {
        'setlist': sl,
        'items': items,
        'nav_active': 'lyrics',
    })


@login_required
def lyrics_song_player(request, song_name: str):
    song_path = _safe_song_path(song_name)
    master = _find_master_track(song_path)
    if not master:
        raise Http404
    info = _load_song_info(song_path) or {}
    artist = info.get('artist') or ''
    title = info.get('title') or song_name
    detail_bits = []
    if info.get('tempo'):
        detail_bits.append(f"{info['tempo']} BPM")
    if info.get('key'):
        detail_bits.append(str(info['key']))
    if info.get('time_signature'):
        detail_bits.append(str(info['time_signature']))
    item = {
        'song_name': song_name,
        'artist': artist,
        'title': title,
        'detail': ' · '.join(detail_bits),
        'display': f'{artist} – {title}' if artist else title,
    }
    return render(request, 'player/lyrics_song_player.html', {
        'item': item,
        'nav_active': 'lyrics',
    })


@login_required
def spotify_connect(request):
    client_id, _ = _spotify_config()
    if not client_id:
        return JsonResponse({'error': 'Spotify client ID is not configured by admin.'}, status=400)
    state = hashlib.sha256(f'{request.user.pk}:{timezone.now().timestamp()}'.encode('utf-8')).hexdigest()
    request.session['spotify_oauth_state'] = state
    request.session['spotify_oauth_next'] = request.GET.get('next', '/setlists/')
    params = urlencode({
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': _spotify_redirect_uri(request),
        'scope': SPOTIFY_SCOPES,
        'state': state,
        'show_dialog': 'true',
    })
    return redirect(f'https://accounts.spotify.com/authorize?{params}')


@login_required
def spotify_callback(request):
    state = request.GET.get('state', '')
    expected = request.session.get('spotify_oauth_state', '')
    if not state or state != expected:
        return JsonResponse({'error': 'Invalid Spotify OAuth state.'}, status=400)
    code = request.GET.get('code', '')
    if not code:
        return JsonResponse({'error': 'Missing Spotify authorization code.'}, status=400)

    client_id, client_secret = _spotify_config()
    if not client_id or not client_secret:
        return JsonResponse({'error': 'Spotify client credentials are not configured.'}, status=400)

    token_url = 'https://accounts.spotify.com/api/token'
    creds = base64.b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('utf-8')
    body = urlencode({
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': _spotify_redirect_uri(request),
    }).encode('utf-8')
    req = urlrequest.Request(
        token_url,
        data=body,
        method='POST',
        headers={
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        token_data = json.loads(resp.read().decode('utf-8'))

    access = token_data.get('access_token', '')
    refresh = token_data.get('refresh_token', '')
    expires_in = int(token_data.get('expires_in', 3600))
    me = _spotify_call('GET', 'https://api.spotify.com/v1/me', access)

    conn, _ = SpotifyConnection.objects.get_or_create(user=request.user)
    conn.spotify_user_id = me.get('id', '')
    conn.access_token = access
    conn.refresh_token = refresh or conn.refresh_token
    conn.token_expires_at = timezone.now() + timedelta(seconds=max(60, expires_in - 60))
    conn.save()

    next_url = request.session.pop('spotify_oauth_next', '/setlists/')
    request.session.pop('spotify_oauth_state', None)
    return redirect(next_url)


@login_required
@require_POST
def spotify_disconnect(request):
    SpotifyConnection.objects.filter(user=request.user).delete()
    return redirect('setlist_list')


@login_required
def setlist_import_spotify(request):
    error = None
    review_rows = None
    playlist_name = ''
    playlist_ref = ''
    available = _get_available_songs()
    available_choices = sorted([s['name'] for s in available], key=lambda x: x.lower())
    editable_setlists = Setlist.objects.filter(owner=request.user).order_by('-updated_at')

    if request.method == 'POST':
        action = request.POST.get('action', 'load')
        if action == 'load':
            playlist_id = _spotify_playlist_id(request.POST.get('playlist', ''))
            if not playlist_id:
                error = 'Enter a Spotify playlist URL, URI, or ID.'
            else:
                try:
                    token = _spotify_access_token(request)
                    playlist = _spotify_call(
                        'GET',
                        f'https://api.spotify.com/v1/playlists/{quote(playlist_id)}',
                        token,
                    )
                    playlist_name = (playlist.get('name') or 'Spotify Playlist').strip()[:255]
                    tracks = playlist.get('tracks', {}).get('items', [])
                    rows = []

                    by_norm = {}
                    by_uri = {}
                    for s in available:
                        key = s.get('name', '').lower().strip()
                        by_norm[key] = s['name']
                        if s.get('artist') and s.get('title'):
                            by_norm[f"{s['artist']} - {s['title']}".lower().strip()] = s['name']
                        if s.get('spotify_track_uri'):
                            by_uri[s['spotify_track_uri']] = s['name']

                    for item in tracks:
                        tr = (item or {}).get('track') or {}
                        tname = (tr.get('name') or '').strip()
                        artists = tr.get('artists') or []
                        aname = (artists[0].get('name') if artists else '') or ''
                        spotify_label = f'{aname} - {tname}'.strip(' -')
                        spotify_uri = tr.get('uri', '')
                        match = by_uri.get(spotify_uri, '')
                        candidates = [spotify_label.lower().strip(), tname.lower().strip()]
                        for c in candidates:
                            if not match and c in by_norm:
                                match = by_norm[c]
                                break
                        rows.append({
                            'spotify_label': spotify_label,
                            'spotify_uri': spotify_uri,
                            'bandmate_match': match,
                        })

                    review_rows = rows
                    playlist_ref = playlist_id
                except RuntimeError as e:
                    error = str(e)
                except Exception:
                    error = 'Failed to load Spotify playlist.'

        elif action == 'apply':
            playlist_ref = request.POST.get('playlist_ref', '').strip()
            playlist_name = (request.POST.get('playlist_name', '').strip() or 'Spotify Import')[:255]
            target = request.POST.get('target_setlist', '__new__').strip()
            labels = request.POST.getlist('spotify_label')
            uris = request.POST.getlist('spotify_uri')
            matches = request.POST.getlist('bandmate_match')
            review_rows = []
            for i, label in enumerate(labels):
                review_rows.append({
                    'spotify_label': label,
                    'spotify_uri': uris[i] if i < len(uris) else '',
                    'bandmate_match': matches[i] if i < len(matches) else '',
                })

            selected = [m.strip() for m in matches if m.strip()]
            if not selected:
                error = 'Select at least one BandMate track (or load another playlist).'
            else:
                if target == '__new__':
                    sl = Setlist.objects.create(name=playlist_name, owner=request.user)
                else:
                    sl = get_object_or_404(Setlist, pk=int(target), owner=request.user)
                    sl.name = playlist_name or sl.name
                    sl.save(update_fields=['name', 'updated_at'])
                    sl.entries.all().delete()
                for i, song_name in enumerate(selected):
                    SetlistEntry.objects.create(
                        setlist=sl, song_name=song_name, position=i, is_break=False
                    )
                # Persist imported Spotify links for future import/export matching.
                for i, song_name in enumerate(matches):
                    song_name = song_name.strip()
                    if not song_name:
                        continue
                    spotify_uri = (uris[i] if i < len(uris) else '').strip()
                    parsed_uri = _spotify_track_uri(spotify_uri)
                    if not parsed_uri:
                        continue
                    try:
                        spath = _safe_song_path(song_name)
                        sinfo = _load_song_info(spath) or {}
                        sinfo['spotify_track_uri'] = parsed_uri
                        if not sinfo.get('title'):
                            sinfo['title'] = song_name
                        _save_song_info(spath, sinfo)
                    except Http404:
                        continue
                return redirect('setlist_edit', setlist_id=sl.pk)

    return render(request, 'player/setlist_import_spotify.html', {
        'error': error,
        'review_rows': review_rows,
        'playlist_name': playlist_name,
        'playlist_ref': playlist_ref,
        'available_choices': available_choices,
        'editable_setlists': editable_setlists,
        'nav_active': 'setlists',
    })


@login_required
def setlist_export_spotify(request, setlist_id: int):
    sl = get_object_or_404(Setlist, pk=setlist_id)
    if sl.owner != request.user and not request.user.is_staff:
        raise Http404
    error = None
    rows = []
    playlist_name = sl.name
    target_playlist = ''
    create_new = True

    try:
        token = _spotify_access_token(request)
    except RuntimeError as e:
        token = None
        error = str(e)

    if request.method == 'POST':
        action = request.POST.get('action', 'search')
        labels = request.POST.getlist('bandmate_label')
        uris = request.POST.getlist('spotify_uri')
        queries = request.POST.getlist('spotify_query')
        for i, label in enumerate(labels):
            rows.append({
                'bandmate_label': label,
                'spotify_uri': uris[i] if i < len(uris) else '',
                'spotify_query': queries[i] if i < len(queries) else '',
            })
        playlist_name = request.POST.get('playlist_name', sl.name).strip() or sl.name
        target_playlist = request.POST.get('target_playlist', '').strip()
        create_new = request.POST.get('create_new') == '1'

        if action == 'apply':
            final_uris = [r['spotify_uri'].strip() for r in rows if r['spotify_uri'].strip()]
            try:
                if create_new or not target_playlist:
                    me = _spotify_call('GET', 'https://api.spotify.com/v1/me', token)
                    user_id = me.get('id')
                    if not user_id:
                        raise RuntimeError('Could not determine Spotify user.')
                    playlist = _spotify_call(
                        'POST',
                        f'https://api.spotify.com/v1/users/{quote(user_id)}/playlists',
                        token,
                        payload={
                            'name': playlist_name[:255],
                            'description': f'Exported from BandMate setlist #{sl.pk}',
                            'public': False,
                        },
                    )
                    playlist_id = playlist.get('id')
                    if not playlist_id:
                        raise RuntimeError('Failed to create Spotify playlist.')
                else:
                    playlist_id = _spotify_playlist_id(target_playlist)
                    if not playlist_id:
                        raise RuntimeError('Invalid target Spotify playlist URL/ID.')

                # Replace all tracks in target playlist.
                _spotify_call(
                    'PUT',
                    f'https://api.spotify.com/v1/playlists/{quote(playlist_id)}/tracks',
                    token,
                    payload={'uris': final_uris},
                )
                return redirect('setlist_list')
            except RuntimeError as e:
                error = str(e)
            except Exception:
                error = 'Failed to export to Spotify.'

    if not rows and token:
        for entry in sl.entries.order_by('position'):
            if entry.is_break:
                rows.append({
                    'bandmate_label': '— Break —',
                    'spotify_uri': SPOTIFY_BREAK_TRACK_URI,
                    'spotify_query': '',
                    'is_break': True,
                })
                continue
            try:
                song_path = _safe_song_path(entry.song_name)
                info = _load_song_info(song_path) or {}
            except Http404:
                info = {}
            title = info.get('title') or entry.song_name
            artist = info.get('artist') or ''
            label = f'{artist} - {title}'.strip(' -')
            q = f'track:{title}'
            if artist:
                q += f' artist:{artist}'
            match_uri = ''
            linked_uri = info.get('spotify_track_uri', '').strip()
            if linked_uri:
                match_uri = linked_uri
            try:
                if not match_uri:
                    results = _spotify_search_tracks(token, q, limit=1)
                else:
                    results = []
                if not match_uri and results:
                    match_uri = results[0]['uri']
            except Exception:
                pass
            rows.append({
                'bandmate_label': label,
                'spotify_uri': match_uri,
                'spotify_query': f'{artist} {title}'.strip(),
                'is_break': False,
            })

    return render(request, 'player/setlist_export_spotify_review.html', {
        'setlist': sl,
        'rows': rows,
        'playlist_name': playlist_name,
        'target_playlist': target_playlist,
        'create_new': create_new,
        'error': error,
        'nav_active': 'setlists',
    })


@login_required
def spotify_track_search_api(request):
    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})
    try:
        token = _spotify_access_token(request)
        results = _spotify_search_tracks(token, q, limit=8)
        return JsonResponse({'results': results})
    except Exception:
        return JsonResponse({'results': []})


@login_required
def setlist_create(request):
    songs = _get_available_songs()
    error = None
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        date = request.POST.get('date', '').strip() or None
        song_names = request.POST.getlist('songs')
        if not name:
            error = 'Setlist name is required.'
        elif not song_names:
            error = 'Add at least one song.'
        else:
            seen = set()
            deduped = []
            for sn in song_names:
                if sn == BREAK_SENTINEL or sn not in seen:
                    deduped.append(sn)
                    if sn != BREAK_SENTINEL:
                        seen.add(sn)
            sl = Setlist.objects.create(name=name, date=date, owner=request.user)
            for i, sn in enumerate(deduped):
                is_break = sn == BREAK_SENTINEL
                SetlistEntry.objects.create(
                    setlist=sl, song_name='' if is_break else sn,
                    position=i, is_break=is_break,
                )
            return redirect('setlist_list')

    return render(request, 'player/setlist_form.html', {
        'form_title': 'New Setlist',
        'available_songs': songs,
        'selected_songs': request.POST.getlist('songs') if request.method == 'POST' else [],
        'form_name': request.POST.get('name', '') if request.method == 'POST' else '',
        'form_date': request.POST.get('date', '') if request.method == 'POST' else '',
        'error': error,
    })


@login_required
def setlist_edit(request, setlist_id: int):
    sl = get_object_or_404(Setlist, pk=setlist_id)
    if sl.owner != request.user and not request.user.is_staff:
        raise Http404
    songs = _get_available_songs()
    error = None
    success = None

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        date = request.POST.get('date', '').strip() or None
        song_names = request.POST.getlist('songs')
        if not name:
            error = 'Setlist name is required.'
        elif not song_names:
            error = 'Add at least one song.'
        else:
            seen = set()
            deduped = []
            for sn in song_names:
                if sn == BREAK_SENTINEL or sn not in seen:
                    deduped.append(sn)
                    if sn != BREAK_SENTINEL:
                        seen.add(sn)
            sl.name = name
            sl.date = date
            sl.save()
            sl.entries.all().delete()
            for i, sn in enumerate(deduped):
                is_break = sn == BREAK_SENTINEL
                SetlistEntry.objects.create(
                    setlist=sl, song_name='' if is_break else sn,
                    position=i, is_break=is_break,
                )
            success = 'Setlist saved.'

    current_songs = []
    for entry in sl.entries.order_by('position'):
        current_songs.append(BREAK_SENTINEL if entry.is_break else entry.song_name)

    return render(request, 'player/setlist_form.html', {
        'form_title': f'Edit – {sl.name}',
        'setlist': sl,
        'available_songs': songs,
        'selected_songs': request.POST.getlist('songs') if request.method == 'POST' and error else current_songs,
        'form_name': request.POST.get('name', sl.name) if request.method == 'POST' and error else sl.name,
        'form_date': (
            request.POST.get('date', '') if request.method == 'POST' and error
            else (sl.date if isinstance(sl.date, str) else sl.date.isoformat() if sl.date else '')
        ),
        'error': error,
        'success': success,
    })


@login_required
def setlist_delete(request, setlist_id: int):
    sl = get_object_or_404(Setlist, pk=setlist_id)
    if sl.owner != request.user and not request.user.is_staff:
        raise Http404
    if request.method == 'POST':
        sl.delete()
    return redirect('setlist_list')


@login_required
def setlist_player(request, setlist_id: int):
    sl = get_object_or_404(Setlist, pk=setlist_id)
    entries = list(sl.entries.order_by('position'))

    setlist_items = []
    for entry in entries:
        if entry.is_break:
            setlist_items.append({'is_break': True})
            continue
        try:
            song_path = _safe_song_path(entry.song_name)
        except Http404:
            continue
        master = _find_master_track(song_path)
        if master:
            setlist_items.append({
                'name': entry.song_name,
                'master_filename': master.name,
                'is_break': False,
            })

    return render(request, 'player/setlist_player.html', {
        'setlist': sl,
        'setlist_items': setlist_items,
    })


@login_required
def setlist_export(request, setlist_id: int):
    """Export a setlist as a PDF with one page per set (split by breaks)."""
    from .export import render_setlist_pdf
    sl = get_object_or_404(Setlist, pk=setlist_id)
    entries = list(sl.entries.order_by('position'))

    song_data = []
    for entry in entries:
        if entry.is_break:
            song_data.append({'is_break': True})
            continue
        info = None
        try:
            song_path = _safe_song_path(entry.song_name)
            info = _load_song_info(song_path)
        except Http404:
            pass
        song_data.append({
            'is_break': False,
            'song_name': entry.song_name,
            'info': info,
        })

    pdf_bytes = render_setlist_pdf(sl, song_data)
    safe_name = sl.name.replace(' ', '_')
    response = FileResponse(
        pdf_bytes,
        content_type='application/pdf',
        filename=f'{safe_name}_setlist.pdf',
    )
    return response


@login_required
def setlist_export_midi(request, setlist_id: int):
    """Export a setlist as a MIDI file with tempo map and markers."""
    from .export import render_setlist_midi
    sl = get_object_or_404(Setlist, pk=setlist_id)
    entries = list(sl.entries.order_by('position'))

    song_data = []
    for entry in entries:
        if entry.is_break:
            song_data.append({'is_break': True})
            continue
        info = None
        try:
            song_path = _safe_song_path(entry.song_name)
            info = _load_song_info(song_path)
        except Http404:
            pass
        song_data.append({
            'is_break': False,
            'song_name': entry.song_name,
            'info': info,
        })

    midi_bytes = render_setlist_midi(sl, song_data)
    safe_name = sl.name.replace(' ', '_')
    response = FileResponse(
        midi_bytes,
        content_type='audio/midi',
        filename=f'{safe_name}_setlist.mid',
    )
    return response


@login_required
def master_audio(request, song_name: str):
    song_path = _safe_song_path(song_name)
    master = _find_master_track(song_path)
    if not master:
        raise Http404
    serve_path, content_type = _get_compressed_audio(master)
    response = FileResponse(open(serve_path, 'rb'), content_type=content_type)
    response['Accept-Ranges'] = 'bytes'
    return response


@login_required
def master_waveform(request, song_name: str):
    """Reuses the same waveform logic but for the master track specifically."""
    song_path = _safe_song_path(song_name)
    master = _find_master_track(song_path)
    if not master:
        raise Http404
    return track_waveform(request, song_name, master.name)


@login_required
def song_info_api(request, song_name: str):
    """Return info.json + lyrics.lrc data as JSON for a given song."""
    song_path = _safe_song_path(song_name)
    info = _load_song_info(song_path)
    lyrics = _load_lyrics(song_path)
    return JsonResponse({'info': info, 'lyrics': lyrics})


@login_required
@require_POST
def song_links_update(request, song_name: str):
    song_path = _safe_song_path(song_name)
    info = _load_song_info(song_path) or {}

    spotify_uri = _spotify_track_uri(request.POST.get('spotify_track', ''))
    youtube_id = _youtube_video_id(request.POST.get('youtube_video', ''))
    youtube_title = request.POST.get('youtube_title', '').strip()

    if spotify_uri:
        info['spotify_track_uri'] = spotify_uri
    else:
        info.pop('spotify_track_uri', None)

    if youtube_id:
        info['youtube_video_id'] = youtube_id
        if youtube_title:
            info['youtube_title'] = youtube_title
    else:
        info.pop('youtube_video_id', None)
        info.pop('youtube_title', None)

    if not info.get('title'):
        info['title'] = song_name
    _save_song_info(song_path, info)
    return JsonResponse({
        'ok': True,
        'spotify_track_uri': info.get('spotify_track_uri', ''),
        'youtube_video_id': info.get('youtube_video_id', ''),
        'youtube_title': info.get('youtube_title', ''),
    })


# ---------------------------------------------------------------------------
# Song upload
# ---------------------------------------------------------------------------

@login_required
def song_upload(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    uploaded = request.FILES.get('zipfile')
    if not uploaded:
        return JsonResponse({'error': 'No file provided'}, status=400)

    max_size = getattr(settings, 'DATA_UPLOAD_MAX_MEMORY_SIZE', 500 * 1024 * 1024)
    if uploaded.size > max_size:
        limit_mb = max_size // (1024 * 1024)
        return JsonResponse(
            {'error': f'File too large. Maximum upload size is {limit_mb} MB.'},
            status=413,
        )

    try:
        zf = zipfile.ZipFile(uploaded)
    except zipfile.BadZipFile:
        return JsonResponse({'error': 'Invalid zip file'}, status=400)

    names = [n for n in zf.namelist() if not n.startswith('__MACOSX')]
    if not names:
        return JsonResponse({'error': 'Zip file is empty'}, status=400)

    # Determine wrapper directory structure
    top_dirs = set()
    for n in names:
        parts = n.split('/')
        if len(parts) > 1 and parts[0]:
            top_dirs.add(parts[0])

    has_wrapper = len(top_dirs) == 1

    # Validate: at least one audio file in the zip
    audio_found = False
    for n in names:
        if not n.endswith('/') and Path(n).suffix.lower() in AUDIO_EXTENSIONS:
            audio_found = True
            break
    if not audio_found:
        return JsonResponse(
            {'error': 'Zip contains no audio files (mp3, wav, flac, etc.)'},
            status=400,
        )

    # Try to read info.json from the zip to get artist/title
    info_json_path = None
    for n in names:
        rel = '/'.join(n.split('/')[1:]) if has_wrapper else n
        if rel == 'info.json':
            info_json_path = n
            break

    song_artist = ''
    song_title = ''
    if info_json_path:
        try:
            info_data = json.loads(zf.read(info_json_path).decode('utf-8'))
            song_artist = info_data.get('artist', '').strip()
            song_title = info_data.get('title', '').strip()
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            pass

    if song_artist and song_title:
        folder_name = f'{song_artist} - {song_title}'.replace('/', '-').replace('\\', '-')
    elif has_wrapper:
        folder_name = top_dirs.pop()
    else:
        folder_name = Path(uploaded.name).stem

    songs_dir = _songs_dir()
    dest = songs_dir / folder_name

    # Conflict detection — error if song already exists
    if dest.exists():
        return JsonResponse(
            {'error': f'Song "{folder_name}" already exists. Delete it first or rename.'},
            status=409,
        )

    dest.mkdir(parents=True, exist_ok=True)

    for member in names:
        if member.endswith('/'):
            continue
        if has_wrapper:
            rel = '/'.join(member.split('/')[1:])
        else:
            rel = member
        if not rel:
            continue
        out_path = (dest / rel).resolve()
        if not str(out_path).startswith(str(dest.resolve())):
            continue
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(member) as src, open(out_path, 'wb') as dst:
            shutil.copyfileobj(src, dst)

    return JsonResponse({'ok': True, 'name': folder_name})


@_staff_required
def song_delete(request, song_name: str):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    song_path = _safe_song_path(song_name)

    in_setlists = SetlistEntry.objects.filter(song_name=song_name, is_break=False).exists()
    if in_setlists:
        return JsonResponse(
            {'error': 'Cannot delete: song is used in one or more setlists.'},
            status=400,
        )

    if song_path.exists():
        shutil.rmtree(song_path)

    return redirect('song_list')


# ---------------------------------------------------------------------------
# Download Tracks page – search YouTube, preview, download as FLAC
# ---------------------------------------------------------------------------

@login_required
def download_tracks(request):
    return render(request, 'player/download_tracks.html')


@login_required
def download_tracks_search(request):
    from .wizard_services import youtube_search

    q = request.GET.get('q', '').strip()
    if not q:
        return JsonResponse({'results': []})
    results = youtube_search(q, max_results=10)
    return JsonResponse({'results': results})


@login_required
def download_tracks_preview(request, video_id: str):
    """Redirect to a streamable audio URL extracted by yt-dlp."""
    from .wizard_services import get_youtube_audio_stream_url

    stream_url = get_youtube_audio_stream_url(video_id)
    if not stream_url:
        raise Http404
    return redirect(stream_url)


@login_required
def download_tracks_flac(request, video_id: str):
    """Download a YouTube video's audio as FLAC."""
    import tempfile
    from .wizard_services import download_youtube_as_flac

    title = request.GET.get('title', video_id)
    safe_title = re.sub(r'[^\w\s\-]', '', title).strip() or video_id

    tmp_dir = tempfile.mkdtemp(prefix='bandmate_dl_')
    try:
        flac_path = download_youtube_as_flac(video_id, tmp_dir)
        with open(flac_path, 'rb') as f:
            data = f.read()
        response = HttpResponse(data, content_type='audio/flac')
        response['Content-Disposition'] = f'attachment; filename="{safe_title}.flac"'
        return response
    except Exception:
        raise Http404
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
