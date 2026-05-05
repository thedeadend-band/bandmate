"""
Views for the New Song Wizard.

All views are staff-only.  The wizard persists state in SongWizard and uses a
staging directory for intermediate audio files.
"""

import json
import os
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import SiteSettings, SongWizard, StemSeparationJob
from .wizard_services import (
    lookup_tempo_key, youtube_search, start_youtube_download,
    start_beat_detection, generate_waveform_peaks, get_audio_duration,
    start_quantize, is_demucs_available, search_lyrics,
    start_queue_worker,
)


def _staff_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        return view_func(request, *args, **kwargs)
    return wrapper


def _staging_dir() -> Path:
    return Path(getattr(settings, 'STAGING_DIR', settings.BASE_DIR / '.song_staging'))



# ---------------------------------------------------------------------------
# Wizard entry point
# ---------------------------------------------------------------------------

@_staff_required
def wizard_start(request):
    """Start a new wizard, always fresh.  Any existing in-progress wizard
    for this user is cancelled and cleaned up first."""
    import shutil

    for old in SongWizard.objects.filter(
        created_by=request.user,
    ).exclude(current_step__in=('complete', 'failed')):
        if old.staging_dir and Path(old.staging_dir).exists():
            shutil.rmtree(old.staging_dir, ignore_errors=True)
        old.delete()

    if request.method == 'POST':
        wiz = SongWizard.objects.create(created_by=request.user)
        staging = _staging_dir() / str(wiz.pk)
        staging.mkdir(parents=True, exist_ok=True)
        wiz.staging_dir = str(staging)
        wiz.save(update_fields=['staging_dir'])
        return redirect('wizard_step', wizard_id=wiz.pk, step_name='song_info')

    return render(request, 'player/wizard/start.html', {
        'nav_active': 'multitrack',
    })


# ---------------------------------------------------------------------------
# Step router
# ---------------------------------------------------------------------------

STEP_VIEWS = {}


def _register_step(step_name):
    def decorator(fn):
        STEP_VIEWS[step_name] = fn
        return fn
    return decorator


@_staff_required
def wizard_step(request, wizard_id, step_name):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)

    if wiz.current_step in ('complete', 'failed'):
        return redirect('wizard_start')

    if step_name not in STEP_VIEWS:
        raise Http404

    step_idx = SongWizard.STEP_KEYS.index(step_name) if step_name in SongWizard.STEP_KEYS else 999
    current_idx = wiz.get_step_index()
    if step_idx > current_idx:
        return redirect('wizard_step', wizard_id=wiz.pk, step_name=wiz.current_step)

    return STEP_VIEWS[step_name](request, wiz)


def _wizard_context(wiz, step_name, extra=None):
    ctx = {
        'wizard': wiz,
        'steps': wiz.get_navigable_steps(),
        'current_step': step_name,
        'nav_active': 'multitrack',
        'wizard_cancel_url': f'/songs/new/{wiz.pk}/cancel/',
    }
    if extra:
        ctx.update(extra)
    return ctx


# ---------------------------------------------------------------------------
# Step 1: Song Info
# ---------------------------------------------------------------------------

@_register_step('song_info')
def step_song_info(request, wiz):
    errors = {}
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        artist = request.POST.get('artist', '').strip()

        if not title:
            errors['title'] = 'Title is required.'
        if not artist:
            errors['artist'] = 'Artist is required.'

        if not errors:
            wiz.title = title
            wiz.artist = artist
            wiz.current_step = 'tempo_key'
            wiz.save(update_fields=['title', 'artist', 'current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='tempo_key')

    return render(request, 'player/wizard/song_info.html', _wizard_context(wiz, 'song_info', {
        'errors': errors,
    }))


# ---------------------------------------------------------------------------
# Step 2: Tempo / Key Lookup
# ---------------------------------------------------------------------------

@_register_step('tempo_key')
def step_tempo_key(request, wiz):
    errors = {}
    lookup_result = None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'lookup':
            site = SiteSettings.load()
            api_key = (site.song_api_key or site.getsongbpm_api_key
                       or site.getsongkey_api_key
                       or os.environ.get('SONG_API_KEY', ''))
            lookup_result = lookup_tempo_key(wiz.title, wiz.artist, api_key)
            if lookup_result.get('tempo'):
                wiz.tempo = lookup_result['tempo']
            if lookup_result.get('key'):
                wiz.key = lookup_result['key']
            if lookup_result.get('time_signature'):
                wiz.time_signature = lookup_result['time_signature']
            wiz.save(update_fields=['tempo', 'key', 'time_signature'])

            if lookup_result.get('error'):
                errors['lookup'] = lookup_result['error']

        elif action == 'next':
            tempo_str = request.POST.get('tempo', '').strip()
            key_val = request.POST.get('key', '').strip()
            time_sig = request.POST.get('time_signature', '4/4').strip()

            if not tempo_str:
                errors['tempo'] = 'Tempo is required.'
            else:
                try:
                    wiz.tempo = int(tempo_str)
                except ValueError:
                    errors['tempo'] = 'Tempo must be a number.'

            if not key_val:
                errors['key'] = 'Key is required.'

            if not errors:
                wiz.key = key_val
                wiz.time_signature = time_sig
                wiz.current_step = 'lyrics'
                wiz.save(update_fields=['tempo', 'key', 'time_signature', 'current_step'])
                return redirect('wizard_step', wizard_id=wiz.pk, step_name='lyrics')

        elif action == 'back':
            wiz.current_step = 'song_info'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='song_info')

    site = SiteSettings.load()
    has_api_key = bool(site.song_api_key or site.getsongbpm_api_key
                       or site.getsongkey_api_key
                       or os.environ.get('SONG_API_KEY'))

    return render(request, 'player/wizard/tempo_key.html', _wizard_context(wiz, 'tempo_key', {
        'errors': errors,
        'lookup_result': lookup_result,
        'has_api_key': has_api_key,
    }))


# ---------------------------------------------------------------------------
# Step 5: Track Source Selection (Upload or YouTube)
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.aiff', '.aif'}

@_register_step('track_source')
def step_track_source(request, wiz):
    errors = {}

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'back':
            wiz.current_step = 'band_details'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='band_details')

        elif action == 'youtube':
            wiz.track_source = 'youtube'
            wiz.current_step = 'download_upload'
            wiz.save(update_fields=['track_source', 'current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='download_upload')

        elif action == 'upload':
            files = request.FILES.getlist('audio_files')
            if not files:
                errors['files'] = 'Please select at least one audio file.'
            else:
                staging = Path(wiz.staging_dir)
                staging.mkdir(parents=True, exist_ok=True)
                has_master = False
                for f in files:
                    ext = Path(f.name).suffix.lower()
                    if ext not in AUDIO_EXTENSIONS:
                        errors['files'] = f'Unsupported format: {f.name}'
                        break
                    dest = staging / f.name
                    with open(dest, 'wb') as out:
                        for chunk in f.chunks():
                            out.write(chunk)
                    if f.name.lower().startswith('master'):
                        has_master = True

                if not errors and not has_master:
                    errors['files'] = 'A Master track is required. Name it Master.wav (or Master.mp3, etc.).'

                if not errors:
                    wiz.track_source = 'upload'
                    wiz.current_step = 'download_upload'
                    wiz.save(update_fields=['track_source', 'current_step'])
                    return redirect('wizard_step', wizard_id=wiz.pk, step_name='download_upload')

    return render(request, 'player/wizard/track_source.html', _wizard_context(wiz, 'track_source', {
        'errors': errors,
    }))


# ---------------------------------------------------------------------------
# Step 6: Download / Upload (progress + confirmation)
# ---------------------------------------------------------------------------

@_register_step('download_upload')
def step_download_upload(request, wiz):
    errors = {}
    staging = Path(wiz.staging_dir) if wiz.staging_dir else None

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'back':
            wiz.current_step = 'track_source'
            wiz.bg_task_status = 'idle'
            wiz.bg_task_progress = 0
            wiz.bg_task_message = ''
            wiz.save(update_fields=['current_step', 'bg_task_status', 'bg_task_progress', 'bg_task_message'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='track_source')

        elif action == 'next':
            if staging and (staging / 'Master.wav').exists():
                wiz.current_step = 'beat_detect'
                wiz.save(update_fields=['current_step'])
                return redirect('wizard_step', wizard_id=wiz.pk, step_name='beat_detect')
            else:
                errors['download'] = 'Master audio not found. Download or upload it first.'

    staged_files = []
    if staging and staging.exists():
        staged_files = sorted([
            f.name for f in staging.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        ])

    has_master = 'Master.wav' in staged_files

    return render(request, 'player/wizard/download_upload.html', _wizard_context(wiz, 'download_upload', {
        'errors': errors,
        'staged_files': staged_files,
        'has_master': has_master,
        'is_youtube': wiz.track_source == 'youtube',
    }))


# ---------------------------------------------------------------------------
# Step 7: Beat Detection
# ---------------------------------------------------------------------------

@_register_step('beat_detect')
def step_beat_detect(request, wiz):
    errors = {}
    staging = Path(wiz.staging_dir) if wiz.staging_dir else None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'back':
            wiz.current_step = 'download_upload'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='download_upload')
        elif action == 'next':
            if wiz.adjusted_beats:
                wiz.current_step = 'quantize'
                wiz.save(update_fields=['current_step'])
                return redirect('wizard_step', wizard_id=wiz.pk, step_name='quantize')
            else:
                wiz.current_step = 'stem_separation'
                wiz.save(update_fields=['current_step'])
                return redirect('wizard_step', wizard_id=wiz.pk, step_name='stem_separation')

    has_beats = bool(wiz.detected_beats)
    return render(request, 'player/wizard/beat_detect.html', _wizard_context(wiz, 'beat_detect', {
        'errors': errors,
        'has_beats': has_beats,
        'beat_count': len(wiz.adjusted_beats or wiz.detected_beats or []),
    }))


# ---------------------------------------------------------------------------
# Step 8: Quantize + Click Intro
# ---------------------------------------------------------------------------

@_register_step('quantize')
def step_quantize(request, wiz):
    errors = {}
    staging = Path(wiz.staging_dir) if wiz.staging_dir else None

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'back':
            wiz.current_step = 'beat_detect'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='beat_detect')
        elif action == 'next':
            if staging and ((staging / 'Click.wav').exists() or (staging / 'Click.ogg').exists()):
                wiz.current_step = 'stem_separation'
                wiz.save(update_fields=['current_step'])
                return redirect('wizard_step', wizard_id=wiz.pk, step_name='stem_separation')
            else:
                errors['quantize'] = 'Run quantization first.'

    has_click = staging and ((staging / 'Click.wav').exists() or (staging / 'Click.ogg').exists()) if staging else False
    return render(request, 'player/wizard/quantize.html', _wizard_context(wiz, 'quantize', {
        'errors': errors,
        'has_click': has_click,
    }))


# ---------------------------------------------------------------------------
# Step 9: Stem Separation (submit to queue)
# ---------------------------------------------------------------------------

@_register_step('stem_separation')
def step_stem_separation(request, wiz):
    errors = {}
    staging = Path(wiz.staging_dir) if wiz.staging_dir else None

    stem_names = ['Vocals', 'Drums', 'Bass', 'Guitar', 'Keys', 'Other']
    existing_stems = []
    if staging and staging.exists():
        existing_stems = [
            s for s in stem_names
            if any((staging / f'{s}{ext}').exists() for ext in ('.wav', '.flac'))
        ]

    has_all_stems = len(existing_stems) >= 3
    can_skip = wiz.track_source == 'upload' and has_all_stems
    demucs_available = is_demucs_available()

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'back':
            wiz.current_step = 'quantize'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='quantize')

        elif action == 'submit_queue':
            stems = request.POST.getlist('stems')
            valid_stems = {'Vocals', 'Drums', 'Bass', 'Guitar', 'Keys', 'Other'}
            selected = [s for s in stems if s in valid_stems] or list(valid_stems)

            songs_dir = Path(settings.SONGS_DIR)
            dir_name = f'{wiz.artist} - {wiz.title}'.replace('/', '-').replace('\\', '-')
            song_dir = songs_dir / dir_name

            if song_dir.exists():
                errors['stems'] = (
                    f'A song named "{dir_name}" already exists. '
                    f'Delete it first or change the artist/title.')
            elif not demucs_available and not can_skip:
                errors['stems'] = 'audio-separator is not installed.'
            else:
                job = StemSeparationJob.objects.create(
                    title=wiz.title,
                    artist=wiz.artist,
                    created_by=request.user,
                    selected_stems=selected,
                    staging_dir=wiz.staging_dir,
                    song_dir=str(song_dir),
                    tempo=wiz.tempo,
                    key=wiz.key,
                    time_signature=wiz.time_signature,
                    lyrics_content=wiz.lyrics_content,
                    lyric_offset_secs=wiz.lyric_offset_secs,
                    band_details=wiz.band_details or {},
                )
                wiz.current_step = 'complete'
                wiz.staging_dir = ''
                wiz.save(update_fields=['current_step', 'staging_dir'])

                start_queue_worker()
                return redirect('queue_list')

        elif action == 'skip' and can_skip:
            songs_dir = Path(settings.SONGS_DIR)
            dir_name = f'{wiz.artist} - {wiz.title}'.replace('/', '-').replace('\\', '-')
            song_dir = songs_dir / dir_name

            if song_dir.exists():
                errors['stems'] = (
                    f'A song named "{dir_name}" already exists. '
                    f'Delete it first or change the artist/title.')
            else:
                job = StemSeparationJob.objects.create(
                    title=wiz.title,
                    artist=wiz.artist,
                    created_by=request.user,
                    selected_stems=[],
                    staging_dir=wiz.staging_dir,
                    song_dir=str(song_dir),
                    tempo=wiz.tempo,
                    key=wiz.key,
                    time_signature=wiz.time_signature,
                    lyrics_content=wiz.lyrics_content,
                    lyric_offset_secs=wiz.lyric_offset_secs,
                    band_details=wiz.band_details or {},
                    status='queued',
                )
                wiz.current_step = 'complete'
                wiz.staging_dir = ''
                wiz.save(update_fields=['current_step', 'staging_dir'])

                start_queue_worker()
                return redirect('queue_list')

    return render(request, 'player/wizard/stem_separation.html', _wizard_context(wiz, 'stem_separation', {
        'errors': errors,
        'existing_stems': existing_stems,
        'stem_names': stem_names,
        'can_skip': can_skip,
        'demucs_available': demucs_available,
        'has_all_stems': has_all_stems,
    }))


# ---------------------------------------------------------------------------
# Step 3: Lyrics
# ---------------------------------------------------------------------------

@_register_step('lyrics')
def step_lyrics(request, wiz):
    errors = {}
    lookup_msg = ''

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'back':
            wiz.current_step = 'tempo_key'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='tempo_key')
        elif action == 'lookup':
            result, err = search_lyrics(wiz.title, wiz.artist)
            if result:
                synced = result.get('syncedLyrics', '')
                plain = result.get('plainLyrics', '')
                wiz.lyrics_content = synced or plain
                wiz.save(update_fields=['lyrics_content'])
                lookup_msg = 'Lyrics found!' if synced else 'Plain lyrics found (no sync timestamps).'
            else:
                lookup_msg = f'No lyrics found: {err}'
        elif action == 'next':
            wiz.lyrics_content = request.POST.get('lyrics', '')
            offset_str = request.POST.get('lyric_offset', '0')
            try:
                wiz.lyric_offset_secs = float(offset_str)
            except ValueError:
                wiz.lyric_offset_secs = 0
            wiz.current_step = 'band_details'
            wiz.save(update_fields=['lyrics_content', 'lyric_offset_secs', 'current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='band_details')

    bpm = wiz.tempo or 120
    ts_parts = wiz.time_signature.split('/')
    time_sig_num = int(ts_parts[0]) if ts_parts else 4
    auto_offset = round(time_sig_num * 2 * (60.0 / bpm), 3)

    return render(request, 'player/wizard/lyrics.html', _wizard_context(wiz, 'lyrics', {
        'errors': errors,
        'lookup_msg': lookup_msg,
        'auto_offset': auto_offset,
    }))


# ---------------------------------------------------------------------------
# Step 4: Band Details
# ---------------------------------------------------------------------------

@_register_step('band_details')
def step_band_details(request, wiz):
    errors = {}

    if request.method == 'POST':
        action = request.POST.get('action', '')
        if action == 'back':
            wiz.current_step = 'lyrics'
            wiz.save(update_fields=['current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='lyrics')
        elif action == 'next':
            details = {}

            for role in ('lead_guitar', 'rhythm_guitar', 'bass_guitar'):
                gtype = request.POST.get(f'{role}_type', '').strip()
                tuning = request.POST.get(f'{role}_tuning', '').strip()
                capo = request.POST.get(f'{role}_capo', '').strip()
                if gtype or tuning or capo:
                    details[role] = {}
                    if gtype:
                        details[role]['type'] = gtype
                    if tuning:
                        details[role]['tuning'] = tuning
                    if capo:
                        details[role]['capo'] = capo

            lead_vocal = request.POST.get('lead_vocal', '').strip()
            backing_vocal = request.POST.get('backing_vocal', '').strip()
            if lead_vocal:
                details['lead_vocal'] = lead_vocal
            if backing_vocal:
                details['backing_vocal'] = backing_vocal

            starts = request.POST.get('starts', '').strip()
            starts_with = request.POST.get('starts_with', '').strip()
            if starts:
                details['starts'] = starts
            if starts_with:
                details['starts_with'] = starts_with

            wiz.band_details = details
            wiz.current_step = 'track_source'
            wiz.save(update_fields=['band_details', 'current_step'])
            return redirect('wizard_step', wizard_id=wiz.pk, step_name='track_source')

    return render(request, 'player/wizard/band_details.html', _wizard_context(wiz, 'band_details', {
        'errors': errors,
        'details': wiz.band_details or {},
    }))




# ---------------------------------------------------------------------------
# AJAX: beat detection
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def wizard_detect_beats(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    if wiz.bg_task_status == 'running':
        return JsonResponse({'error': 'A task is already running'}, status=409)
    staging = Path(wiz.staging_dir)
    master = staging / 'Master.wav'
    if not master.exists():
        return JsonResponse({'error': 'Master.wav not found'}, status=400)
    start_beat_detection(str(master), wiz)
    return JsonResponse({'ok': True})


@_staff_required
def wizard_get_beats(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    return JsonResponse({
        'detected': wiz.detected_beats or [],
        'adjusted': wiz.adjusted_beats or [],
    })


@_staff_required
@require_POST
def wizard_save_beats(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    beats = body.get('beats', [])
    wiz.adjusted_beats = [round(float(b), 4) for b in beats]
    wiz.save(update_fields=['adjusted_beats'])
    return JsonResponse({'ok': True, 'count': len(wiz.adjusted_beats)})


@_staff_required
def wizard_waveform(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    staging = Path(wiz.staging_dir)
    master = staging / 'Master.wav'
    if not master.exists():
        return JsonResponse({'error': 'Master.wav not found'}, status=404)
    peaks = generate_waveform_peaks(str(master))
    duration = get_audio_duration(str(master))
    return JsonResponse({'peaks': peaks, 'duration': duration})


# ---------------------------------------------------------------------------
# AJAX: quantization
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def wizard_start_quantize(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    if wiz.bg_task_status == 'running':
        return JsonResponse({'error': 'A task is already running'}, status=409)
    start_quantize(wiz)
    return JsonResponse({'ok': True})




# ---------------------------------------------------------------------------
# AJAX: serve staging audio for playback
# ---------------------------------------------------------------------------

@_staff_required
def wizard_stem_audio(request, wizard_id, stem_name):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    staging = Path(wiz.staging_dir)
    content_types = {'.wav': 'audio/wav', '.flac': 'audio/flac', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg'}
    for ext, ct in content_types.items():
        audio_file = staging / f'{stem_name}{ext}'
        if audio_file.exists():
            from django.http import FileResponse
            return FileResponse(open(audio_file, 'rb'), content_type=ct)
    raise Http404


# ---------------------------------------------------------------------------
# AJAX: YouTube search
# ---------------------------------------------------------------------------

@_staff_required
def wizard_youtube_search(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    q = request.GET.get('q', '').strip()
    if not q:
        q = f'{wiz.artist} {wiz.title} official audio'
    results = youtube_search(q, max_results=8)
    return JsonResponse({'results': results})


# ---------------------------------------------------------------------------
# AJAX: start YouTube download
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def wizard_start_download(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)

    if wiz.bg_task_status == 'running':
        return JsonResponse({'error': 'A task is already running'}, status=409)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        body = {}

    video_id = body.get('video_id', '').strip()
    if not video_id:
        return JsonResponse({'error': 'video_id is required'}, status=400)

    video_title = body.get('title', '')
    wiz.youtube_selection = {'video_id': video_id, 'title': video_title}
    wiz.save(update_fields=['youtube_selection'])

    staging = Path(wiz.staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    start_youtube_download(video_id, str(staging), wiz)
    return JsonResponse({'ok': True})


# ---------------------------------------------------------------------------
# AJAX: upload audio files (for upload path within download_upload step)
# ---------------------------------------------------------------------------

@_staff_required
@require_POST
def wizard_upload_files(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    files = request.FILES.getlist('audio_files')
    if not files:
        return JsonResponse({'error': 'No files provided'}, status=400)

    staging = Path(wiz.staging_dir)
    staging.mkdir(parents=True, exist_ok=True)

    saved = []
    for f in files:
        ext = Path(f.name).suffix.lower()
        if ext not in AUDIO_EXTENSIONS:
            return JsonResponse({'error': f'Unsupported format: {f.name}'}, status=400)
        dest = staging / f.name
        with open(dest, 'wb') as out:
            for chunk in f.chunks():
                out.write(chunk)
        saved.append(f.name)

    return JsonResponse({'ok': True, 'files': saved})


# ---------------------------------------------------------------------------
# AJAX: task status polling
# ---------------------------------------------------------------------------

@_staff_required
def wizard_task_status(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    return JsonResponse({
        'status': wiz.bg_task_status,
        'progress': wiz.bg_task_progress,
        'message': wiz.bg_task_message,
    })


# ---------------------------------------------------------------------------
# Cancel / discard wizard
# ---------------------------------------------------------------------------

@csrf_exempt
@_staff_required
def wizard_cancel(request, wizard_id):
    wiz = get_object_or_404(SongWizard, pk=wizard_id, created_by=request.user)
    if request.method == 'POST':
        _cleanup_wizard(wiz)
    if request.headers.get('Sec-Fetch-Mode') == 'no-cors':
        return JsonResponse({'ok': True})
    return redirect('wizard_start')


def _cleanup_wizard(wiz):
    """Delete a wizard's staging directory and DB record."""
    import shutil
    if wiz.staging_dir and Path(wiz.staging_dir).exists():
        shutil.rmtree(wiz.staging_dir, ignore_errors=True)
    wiz.delete()
