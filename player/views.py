import hashlib
import json
import re
import shutil
import subprocess
import zipfile
from functools import wraps
from pathlib import Path

import numpy as np

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django_ratelimit.decorators import ratelimit

from .models import Setlist, SetlistEntry, SiteSettings


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
            data['lyric_offset_secs'] = _parse_lrc_time(data['lyric_offset'])
        else:
            data['lyric_offset_secs'] = 0
        return data
    except (json.JSONDecodeError, OSError):
        return None


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
                    }
                    if info:
                        if info.get('artist'):
                            entry['artist'] = info['artist']
                        if info.get('title'):
                            entry['title'] = info['title']
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
    })


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


# ---------------------------------------------------------------------------
# Setlists
# ---------------------------------------------------------------------------

BREAK_SENTINEL = '__BREAK__'


@login_required
def setlist_list(request):
    setlists = Setlist.objects.select_related('owner').prefetch_related('entries')
    return render(request, 'player/setlist_list.html', {
        'setlists': setlists,
        'nav_active': 'setlists',
    })


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
            else (sl.date.isoformat() if sl.date else '')
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


# ---------------------------------------------------------------------------
# Song upload
# ---------------------------------------------------------------------------

@_staff_required
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

    # Determine folder name: use common top-level directory, or zip filename
    top_dirs = set()
    for n in names:
        parts = n.split('/')
        if len(parts) > 1 and parts[0]:
            top_dirs.add(parts[0])

    if len(top_dirs) == 1:
        folder_name = top_dirs.pop()
        has_wrapper = True
    else:
        folder_name = Path(uploaded.name).stem
        has_wrapper = False

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

    songs_dir = _songs_dir()
    dest = songs_dir / folder_name

    # Conflict detection
    if dest.exists() and not request.POST.get('confirm'):
        return JsonResponse({'exists': True, 'name': folder_name})

    if dest.exists():
        shutil.rmtree(dest)

    dest.mkdir(parents=True, exist_ok=True)

    for member in names:
        if member.endswith('/'):
            continue
        # Strip the wrapper directory if present
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
