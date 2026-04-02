"""
Business logic for the New Song Wizard.

Covers: YouTube search/download, tempo/key lookup, lyrics lookup.
Future phases add: beat detection, quantization, click generation, Demucs.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

USER_AGENT = 'BandMate SongWizard/1.0'

# ---------------------------------------------------------------------------
# Tempo / Key lookup via GetSongBPM.com + GetSongKey.com APIs
# ---------------------------------------------------------------------------

GETSONGBPM_BASE = 'https://api.getsongbpm.com'
GETSONGKEY_BASE = 'https://api.getsongkey.com'


def _api_get(base_url: str, path: str, params: dict) -> dict:
    """Make a GET request to a GetSongBPM / GetSongKey API endpoint."""
    qs = urllib.parse.urlencode(params)
    url = f'{base_url}{path}?{qs}'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _lookup_tempo(title: str, artist: str, api_key: str) -> dict:
    """Look up tempo and time signature via GetSongBPM.com."""
    result: dict = {'tempo': None, 'time_signature': None, 'error': None}
    try:
        search_data = _api_get(GETSONGBPM_BASE, '/search/', {
            'api_key': api_key,
            'type': 'both',
            'lookup': f'song:{title} artist:{artist}',
        })

        search_results = search_data.get('search', [])
        if not search_results:
            result['error'] = 'No results on GetSongBPM.'
            return result

        song_id = search_results[0].get('id')
        if not song_id:
            result['error'] = 'GetSongBPM search returned no song ID.'
            return result

        song_data = _api_get(GETSONGBPM_BASE, '/song/', {
            'api_key': api_key,
            'id': song_id,
        })
        song = song_data.get('song', {})

        tempo = song.get('tempo')
        if tempo:
            try:
                result['tempo'] = int(round(float(tempo)))
            except (ValueError, TypeError):
                pass

        time_sig = song.get('time_sig')
        if time_sig:
            result['time_signature'] = str(time_sig)

        if not result['tempo']:
            result['error'] = 'Track found on GetSongBPM but no tempo data.'

    except urllib.error.HTTPError as e:
        result['error'] = f'GetSongBPM error: HTTP {e.code}'
    except Exception as e:
        logger.exception('GetSongBPM lookup failed')
        result['error'] = f'GetSongBPM error: {e}'

    return result


def _lookup_key(title: str, artist: str, api_key: str) -> dict:
    """Look up song key via GetSongKey.com."""
    result: dict = {'key': None, 'error': None}
    try:
        search_data = _api_get(GETSONGKEY_BASE, '/search/', {
            'api_key': api_key,
            'type': 'both',
            'lookup': f'song:{title} artist:{artist}',
        })

        search_results = search_data.get('search', [])
        if not search_results:
            result['error'] = 'No results on GetSongKey.'
            return result

        song_id = search_results[0].get('id')
        if not song_id:
            result['error'] = 'GetSongKey search returned no song ID.'
            return result

        song_data = _api_get(GETSONGKEY_BASE, '/song/', {
            'api_key': api_key,
            'id': song_id,
        })
        song = song_data.get('song', {})

        key_info = song.get('key_of')
        if key_info:
            result['key'] = key_info
        else:
            result['error'] = 'Track found on GetSongKey but no key data.'

    except urllib.error.HTTPError as e:
        result['error'] = f'GetSongKey error: HTTP {e.code}'
    except Exception as e:
        logger.exception('GetSongKey lookup failed')
        result['error'] = f'GetSongKey error: {e}'

    return result


def lookup_tempo_key(
    title: str,
    artist: str,
    bpm_api_key: str = '',
    key_api_key: str = '',
) -> dict:
    """
    Look up tempo via GetSongBPM.com and key via GetSongKey.com.

    Each API is called independently; if only one key is configured the
    other lookup is skipped gracefully.

    Returns a dict with keys: tempo, key, time_signature, source, error.
    """
    result = {'tempo': None, 'key': None, 'time_signature': None, 'source': None, 'error': None}

    if not bpm_api_key and not key_api_key:
        result['error'] = 'No API keys configured. Add GetSongBPM and/or GetSongKey keys in Settings.'
        return result

    errors = []
    sources = []

    if bpm_api_key:
        bpm = _lookup_tempo(title, artist, bpm_api_key)
        result['tempo'] = bpm.get('tempo')
        result['time_signature'] = bpm.get('time_signature')
        if bpm.get('error'):
            errors.append(bpm['error'])
        if result['tempo']:
            sources.append('getsongbpm')
    else:
        errors.append('GetSongBPM API key not configured.')

    if key_api_key:
        key_result = _lookup_key(title, artist, key_api_key)
        result['key'] = key_result.get('key')
        if key_result.get('error'):
            errors.append(key_result['error'])
        if result['key']:
            sources.append('getsongkey')
    else:
        errors.append('GetSongKey API key not configured.')

    if sources:
        result['source'] = ' + '.join(sources)

    if not result['tempo'] and not result['key']:
        result['error'] = ' '.join(errors) if errors else 'No tempo or key data found.'
    elif errors:
        result['error'] = ' '.join(errors)

    return result


# ---------------------------------------------------------------------------
# Lyrics lookup (reuses LRCLIB logic from download_lyrics.py)
# ---------------------------------------------------------------------------

LRCLIB_SEARCH = 'https://lrclib.net/api/search'


def search_lyrics(track_name: str, artist_name: str) -> tuple[dict | None, str | None]:
    """Search LRCLIB and return (result_dict, error_string)."""
    params = urllib.parse.urlencode({
        'track_name': track_name,
        'artist_name': artist_name,
    })
    url = f'{LRCLIB_SEARCH}?{params}'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return None, str(e)

    if not data:
        return None, 'no results'

    for item in data:
        if item.get('syncedLyrics'):
            return item, None

    if data[0].get('plainLyrics'):
        return data[0], 'synced lyrics not available, plain only'

    return None, 'no lyrics in results'


# ---------------------------------------------------------------------------
# YouTube search via yt-dlp
# ---------------------------------------------------------------------------

def youtube_search(query: str, max_results: int = 8) -> list[dict]:
    """
    Search YouTube for videos matching *query*.

    Returns a list of dicts with: video_id, title, duration, duration_str,
    channel, thumbnail.
    """
    try:
        import yt_dlp
    except ImportError:
        logger.error('yt-dlp is not installed')
        return []

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'extract_flat': 'in_playlist',
        'default_search': f'ytsearch{max_results}',
    }

    results = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            entries = info.get('entries', []) if info else []
            for entry in entries:
                if not entry:
                    continue
                vid_id = entry.get('id', '')
                duration = entry.get('duration') or 0
                mins, secs = divmod(int(duration), 60)
                results.append({
                    'video_id': vid_id,
                    'title': entry.get('title', ''),
                    'duration': duration,
                    'duration_str': f'{mins}:{secs:02d}',
                    'channel': entry.get('channel', entry.get('uploader', '')),
                    'thumbnail': entry.get('thumbnail', f'https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg'),
                    'url': f'https://www.youtube.com/watch?v={vid_id}',
                })
    except Exception:
        logger.exception('YouTube search failed')

    return results


# ---------------------------------------------------------------------------
# YouTube audio download via yt-dlp (runs in background thread)
# ---------------------------------------------------------------------------

def download_youtube_audio(video_id: str, output_dir: str, wizard_model=None) -> str | None:
    """
    Download a YouTube video's audio as WAV to *output_dir*/Master.wav.

    If *wizard_model* is provided, updates bg_task_status/progress/message
    on that model instance for AJAX polling.

    Returns the path to the downloaded file, or None on failure.
    """
    try:
        import yt_dlp
    except ImportError:
        if wizard_model:
            wizard_model.bg_task_status = 'error'
            wizard_model.bg_task_message = 'yt-dlp is not installed'
            wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return None

    out_path = Path(output_dir) / 'Master'
    final_path = Path(output_dir) / 'Master.wav'

    def _progress_hook(d):
        if wizard_model and d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                pct = min(int(downloaded / total * 80), 80)
                wizard_model.bg_task_progress = pct
                wizard_model.bg_task_message = f'Downloading... {pct}%'
                wizard_model.save(update_fields=['bg_task_progress', 'bg_task_message'])
        elif wizard_model and d.get('status') == 'finished':
            wizard_model.bg_task_progress = 80
            wizard_model.bg_task_message = 'Converting to WAV...'
            wizard_model.save(update_fields=['bg_task_progress', 'bg_task_message'])

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(out_path),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'wav',
        }],
        'progress_hooks': [_progress_hook],
        'quiet': True,
        'no_warnings': True,
    }

    try:
        url = f'https://www.youtube.com/watch?v={video_id}'
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if final_path.exists():
            if wizard_model:
                wizard_model.bg_task_status = 'done'
                wizard_model.bg_task_progress = 100
                wizard_model.bg_task_message = 'Download complete'
                wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])
            return str(final_path)
        else:
            raise FileNotFoundError(f'Expected output not found: {final_path}')

    except Exception as e:
        logger.exception('YouTube download failed')
        if wizard_model:
            wizard_model.bg_task_status = 'error'
            wizard_model.bg_task_message = f'Download failed: {e}'
            wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return None


def start_youtube_download(video_id: str, output_dir: str, wizard_model) -> None:
    """Kick off download_youtube_audio in a background thread."""
    wizard_model.bg_task_status = 'running'
    wizard_model.bg_task_progress = 0
    wizard_model.bg_task_message = 'Starting download...'
    wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    t = threading.Thread(
        target=download_youtube_audio,
        args=(video_id, output_dir, wizard_model),
        daemon=True,
    )
    t.start()


def download_youtube_as_flac(video_id: str, output_dir: str) -> str:
    """
    Download a YouTube video's audio as FLAC to *output_dir*/<video_id>.flac.

    Returns the path to the downloaded file.
    Raises on failure.
    """
    import yt_dlp

    out_path = Path(output_dir) / video_id
    final_path = Path(output_dir) / f'{video_id}.flac'

    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': str(out_path),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'flac',
        }],
        'quiet': True,
        'no_warnings': True,
    }

    url = f'https://www.youtube.com/watch?v={video_id}'
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    if not final_path.exists():
        raise FileNotFoundError(f'Expected output not found: {final_path}')
    return str(final_path)


def get_youtube_audio_stream_url(video_id: str) -> str | None:
    """
    Extract the best audio-only stream URL for a YouTube video.

    Returns a direct URL that can be proxied/streamed, or None.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(
                f'https://www.youtube.com/watch?v={video_id}',
                download=False,
            )
            return info.get('url') if info else None
    except Exception:
        logger.exception('Failed to extract audio stream URL for %s', video_id)
        return None


# ---------------------------------------------------------------------------
# Beat detection via librosa
# ---------------------------------------------------------------------------

def detect_beats(audio_path: str, wizard_model=None) -> list[float]:
    """
    Detect beat positions in an audio file using librosa.

    Returns a list of beat times in seconds.  Updates wizard_model
    progress if provided.
    """
    import librosa

    if wizard_model:
        wizard_model.bg_task_status = 'running'
        wizard_model.bg_task_progress = 10
        wizard_model.bg_task_message = 'Loading audio...'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    try:
        y, sr = librosa.load(audio_path, sr=22050, mono=True)

        if wizard_model:
            wizard_model.bg_task_progress = 40
            wizard_model.bg_task_message = 'Detecting beats...'
            wizard_model.save(update_fields=['bg_task_progress', 'bg_task_message'])

        tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
        beat_times = librosa.frames_to_time(beat_frames, sr=sr).tolist()
        beat_times = [round(t, 4) for t in beat_times]

        if wizard_model:
            wizard_model.bg_task_status = 'done'
            wizard_model.bg_task_progress = 100
            wizard_model.bg_task_message = f'Detected {len(beat_times)} beats (est. {int(tempo) if not hasattr(tempo, "__len__") else int(tempo[0])} BPM)'
            wizard_model.detected_beats = beat_times
            wizard_model.adjusted_beats = beat_times
            wizard_model.save(update_fields=[
                'bg_task_status', 'bg_task_progress', 'bg_task_message',
                'detected_beats', 'adjusted_beats',
            ])

        return beat_times

    except Exception as e:
        logger.exception('Beat detection failed')
        if wizard_model:
            wizard_model.bg_task_status = 'error'
            wizard_model.bg_task_message = f'Beat detection failed: {e}'
            wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return []


def start_beat_detection(audio_path: str, wizard_model) -> None:
    """Kick off beat detection in a background thread."""
    wizard_model.bg_task_status = 'running'
    wizard_model.bg_task_progress = 0
    wizard_model.bg_task_message = 'Starting beat detection...'
    wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    t = threading.Thread(
        target=detect_beats,
        args=(audio_path, wizard_model),
        daemon=True,
    )
    t.start()


def generate_waveform_peaks(audio_path: str, num_peaks: int = 800) -> list[float]:
    """Generate waveform peak data for visualization."""
    import librosa
    y, sr = librosa.load(audio_path, sr=22050, mono=True)
    samples_per_peak = max(1, len(y) // num_peaks)
    peaks = []
    for i in range(num_peaks):
        start = i * samples_per_peak
        end = min(start + samples_per_peak, len(y))
        if start >= len(y):
            break
        chunk = y[start:end]
        peaks.append(round(float(max(abs(chunk.min()), abs(chunk.max()))), 4))
    return peaks


def get_audio_duration(audio_path: str) -> float:
    """Get duration of an audio file in seconds."""
    import librosa
    return float(librosa.get_duration(path=audio_path))


# ---------------------------------------------------------------------------
# Quantization + click track generation
# ---------------------------------------------------------------------------

def quantize_audio(audio_path: str, beat_times: list[float], target_bpm: int,
                   time_sig_num: int = 4, intro_bars: int = 2,
                   wizard_model=None) -> str:
    """
    Quantize audio to a fixed tempo, prepend click intro silence.

    Returns the path to the quantized output file.
    """
    import librosa
    import numpy as np
    import soundfile as sf

    if wizard_model:
        wizard_model.bg_task_status = 'running'
        wizard_model.bg_task_progress = 5
        wizard_model.bg_task_message = 'Loading audio for quantization...'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    try:
        y, sr = librosa.load(audio_path, sr=None, mono=False)
        if y.ndim == 1:
            y = y[np.newaxis, :]
        channels = y.shape[0]

        target_interval = 60.0 / target_bpm
        intro_beats = time_sig_num * intro_bars
        intro_duration = intro_beats * target_interval
        intro_samples = int(intro_duration * sr)

        if wizard_model:
            wizard_model.bg_task_progress = 20
            wizard_model.bg_task_message = 'Quantizing segments...'
            wizard_model.save(update_fields=['bg_task_progress', 'bg_task_message'])

        segments = []
        for i in range(len(beat_times)):
            start_sample = int(beat_times[i] * sr)
            if i + 1 < len(beat_times):
                end_sample = int(beat_times[i + 1] * sr)
            else:
                end_sample = y.shape[1]
            segment = y[:, start_sample:end_sample]
            if segment.shape[1] == 0:
                continue

            actual_dur = segment.shape[1] / sr
            if actual_dur > 0 and target_interval > 0:
                stretch_ratio = actual_dur / target_interval
                if abs(stretch_ratio - 1.0) > 0.005:
                    try:
                        import pyrubberband as pyrb
                        stretched_channels = []
                        for ch in range(channels):
                            stretched = pyrb.time_stretch(segment[ch], sr, stretch_ratio)
                            stretched_channels.append(stretched)
                        min_len = min(s.shape[0] for s in stretched_channels)
                        segment = np.array([s[:min_len] for s in stretched_channels])
                    except Exception:
                        pass

            segments.append(segment)

            if wizard_model and len(beat_times) > 0:
                pct = 20 + int(60 * (i + 1) / len(beat_times))
                wizard_model.bg_task_progress = min(pct, 80)
                wizard_model.save(update_fields=['bg_task_progress'])

        if not segments:
            raise ValueError('No audio segments after quantization')

        intro_silence = np.zeros((channels, intro_samples))

        if beat_times and beat_times[0] > 0:
            pre_beat_samples = int(beat_times[0] * sr)
            pre_audio = y[:, :pre_beat_samples]
            quantized = np.concatenate([intro_silence, pre_audio] + segments, axis=1)
        else:
            quantized = np.concatenate([intro_silence] + segments, axis=1)

        if channels == 1:
            quantized = quantized[0]
        else:
            quantized = quantized.T

        out_path = Path(audio_path).parent / (Path(audio_path).stem + '_quantized.wav')
        sf.write(str(out_path), quantized, sr)

        if wizard_model:
            wizard_model.bg_task_progress = 90
            wizard_model.bg_task_message = 'Generating click track...'
            wizard_model.save(update_fields=['bg_task_progress', 'bg_task_message'])

        return str(out_path)

    except Exception as e:
        logger.exception('Quantization failed')
        if wizard_model:
            wizard_model.bg_task_status = 'error'
            wizard_model.bg_task_message = f'Quantization failed: {e}'
            wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return ''


def generate_click_track(output_dir: str, target_bpm: int, total_duration: float,
                         time_sig_num: int = 4, sr: int = 44100,
                         start_offset: float = 0.0) -> str:
    """
    Generate a click track as WAV file.

    *start_offset* shifts the entire click grid forward in time so that
    clicks land on actual beat positions when there is pre-beat audio
    between the intro silence and the first quantized beat.
    """
    import numpy as np
    import soundfile as sf

    target_interval = 60.0 / target_bpm
    total_samples = int(total_duration * sr)
    click = np.zeros(total_samples, dtype=np.float32)

    click_duration = 0.02
    click_samples = int(click_duration * sr)
    t_click = np.linspace(0, click_duration, click_samples, endpoint=False)

    tone = (0.7 * np.sin(2 * np.pi * 1000 * t_click) *
            np.exp(-t_click * 80)).astype(np.float32)

    pos = start_offset
    while pos < total_duration:
        sample_pos = int(pos * sr)
        if sample_pos + click_samples > total_samples:
            break
        click[sample_pos:sample_pos + click_samples] += tone
        pos += target_interval

    out_path = Path(output_dir) / 'Click.wav'
    sf.write(str(out_path), click, sr)
    return str(out_path)


def run_quantize_and_click(wizard_model) -> None:
    """Full quantize + click pipeline, run in a subprocess for stability."""
    import sys

    staging = Path(wizard_model.staging_dir)
    master = staging / 'Master.wav'
    if not master.exists():
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = 'Master.wav not found in staging'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return

    beats = wizard_model.adjusted_beats or wizard_model.detected_beats
    if not beats:
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = 'No beats detected. Run beat detection first.'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return

    bpm = wizard_model.tempo or 120
    ts_parts = wizard_model.time_signature.split('/')
    time_sig_num = int(ts_parts[0]) if ts_parts else 4

    try:
        import json as _json
        beats_json = _json.dumps(beats)
        script = f'''
import sys, shutil, json
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).resolve().parent.parent)!r})

import django, os
os.environ["DJANGO_SETTINGS_MODULE"] = "bandmate.settings"
django.setup()

from player.wizard_services import quantize_audio, get_audio_duration, generate_click_track
from player.models import SongWizard

wiz = SongWizard.objects.get(pk={wizard_model.pk})
staging = Path({str(staging)!r})
master = staging / "Master.wav"
beats = json.loads({beats_json!r})
bpm = {bpm}
time_sig_num = {time_sig_num}

quantized_path = quantize_audio(str(master), beats, bpm, time_sig_num=time_sig_num, wizard_model=wiz)
if not quantized_path:
    sys.exit(1)

backup = staging / "Master_original.wav"
shutil.move(str(master), str(backup))
shutil.move(quantized_path, str(master))

import soundfile as _sf
_master_info = _sf.info(str(master))
duration = _master_info.duration
master_sr = _master_info.samplerate
pre_beat_offset = beats[0] if beats and beats[0] > 0 else 0.0
generate_click_track(str(staging), bpm, duration, time_sig_num=time_sig_num, sr=master_sr, start_offset=pre_beat_offset)

all_stems = [f for f in staging.iterdir()
             if f.is_file() and f.suffix.lower() == ".wav"
             and f.name not in ("Master.wav", "Master_original.wav")
             and not f.name.endswith("_quantized.wav")]
for stem in all_stems:
    wiz.bg_task_message = f"Quantizing {{stem.stem}}..."
    wiz.save(update_fields=["bg_task_message"])
    q_path = quantize_audio(str(stem), beats, bpm, time_sig_num=time_sig_num)
    if q_path:
        stem_backup = staging / (stem.stem + "_original" + stem.suffix)
        shutil.move(str(stem), str(stem_backup))
        shutil.move(q_path, str(stem))

wiz.bg_task_status = "done"
wiz.bg_task_progress = 100
wiz.bg_task_message = "Quantization complete"
wiz.save(update_fields=["bg_task_status", "bg_task_progress", "bg_task_message"])

# Hard exit to avoid segfault during C extension cleanup (numba/librosa)
os._exit(0)
'''
        result = subprocess.run(
            [sys.executable, '-c', script],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            err_tail = (result.stderr or '')[-500:]
            logger.error('Quantize subprocess failed (rc=%d): %s', result.returncode, err_tail)
            wizard_model.refresh_from_db()
            if wizard_model.bg_task_status != 'done':
                wizard_model.bg_task_status = 'error'
                wizard_model.bg_task_message = f'Quantization crashed: {err_tail[-200:]}'
                wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])

    except Exception as e:
        logger.exception('Quantize pipeline failed')
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = f'Quantization failed: {e}'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])


def start_quantize(wizard_model) -> None:
    """Kick off quantization in a background thread."""
    wizard_model.bg_task_status = 'running'
    wizard_model.bg_task_progress = 0
    wizard_model.bg_task_message = 'Starting quantization...'
    wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    t = threading.Thread(
        target=run_quantize_and_click,
        args=(wizard_model,),
        daemon=True,
    )
    t.start()


# ---------------------------------------------------------------------------
# Stem separation  (hybrid: htdemucs_ft + BS-RoFormer-SW)
# ---------------------------------------------------------------------------

import re as _re
import sys as _sys
import time as _time

_DEMUCS_PCT_RE = _re.compile(r'(\d+)%\|')
_DEMUCS_BAG_RE = _re.compile(r'bag of (\d+) models')

_DEMUCS_STEM_LABELS = {
    0: 'vocals', 1: 'drums', 2: 'bass', 3: 'other',
}

_BS_ROFORMER_MODEL_NAME = 'roformer-model-bs-roformer-sw-by-jarredou'
_BS_ROFORMER_CKPT = 'BS-Rofo-SW-Fixed.ckpt'
_BS_ROFORMER_YAML = 'BS-Rofo-SW-Fixed.yaml'


def _get_models_dir() -> Path:
    """Return the project-level ``models/`` directory."""
    return Path(__file__).resolve().parent.parent / 'models'


def _ensure_bs_roformer_model() -> tuple[Path, Path]:
    """Ensure BS-RoFormer-SW weights and config are present, downloading if needed.

    Returns (ckpt_path, config_path).
    """
    models_dir = _get_models_dir()
    model_dir = models_dir / _BS_ROFORMER_MODEL_NAME
    ckpt = model_dir / _BS_ROFORMER_CKPT
    cfg = model_dir / _BS_ROFORMER_YAML

    if ckpt.exists() and cfg.exists():
        return ckpt, cfg

    logger.info('BS-RoFormer model not found – downloading…')
    download_bin = str(
        Path(_sys.executable).parent / 'bs-roformer-download')
    result = subprocess.run(
        [download_bin, '--model', _BS_ROFORMER_MODEL_NAME],
        capture_output=True, text=True, timeout=600,
        cwd=str(models_dir.parent),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'BS-RoFormer model download failed: {(result.stderr or "")[-300:]}'
        )
    if not ckpt.exists() or not cfg.exists():
        raise FileNotFoundError(
            f'Download succeeded but model files missing at {model_dir}'
        )
    return ckpt, cfg


def _detect_torch_device() -> str:
    """Return the best available torch device string."""
    try:
        import torch
        if torch.cuda.is_available():
            return 'cuda'
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            return 'mps'
    except ImportError:
        pass
    return 'cpu'


# -- Demucs helpers --------------------------------------------------------

def _run_demucs_with_progress(cmd: list, wizard_model,
                              progress_lo: int = 5,
                              progress_hi: int = 40) -> int:
    """Run a demucs command, parsing stderr progress bars to update the DB.

    Progress is mapped into the [progress_lo, progress_hi] range so callers
    can control which slice of the overall progress bar Demucs occupies.
    """
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    last_update = 0.0
    buf = ''
    total_passes = 1
    current_pass = 0
    prev_pct = 0
    span = progress_hi - progress_lo

    while True:
        chunk = proc.stderr.read(256)
        if not chunk and proc.poll() is not None:
            break
        if not chunk:
            continue
        buf += chunk

        bag_match = _DEMUCS_BAG_RE.search(buf)
        if bag_match:
            total_passes = int(bag_match.group(1))

        matches = _DEMUCS_PCT_RE.findall(buf)
        if matches:
            pct = int(matches[-1])
            if pct < prev_pct and prev_pct > 80:
                current_pass += 1
            prev_pct = pct

            overall = ((current_pass * 100) + pct) / total_passes
            progress = progress_lo + int(overall / 100.0 * span)
            stem_label = _DEMUCS_STEM_LABELS.get(
                current_pass, f'pass {current_pass + 1}')

            now = _time.monotonic()
            if now - last_update > 2.0:
                wizard_model.bg_task_progress = min(progress, progress_hi)
                wizard_model.bg_task_message = (
                    f'Demucs: separating {stem_label}… {int(overall)}%')
                wizard_model.save(
                    update_fields=['bg_task_progress', 'bg_task_message'])
                last_update = now

        buf = buf[-200:]

    proc.wait()
    return proc.returncode


# -- BS-RoFormer helpers ---------------------------------------------------

_ROFORMER_REMAINING_RE = _re.compile(
    r'Estimated time remaining:\s*([\d.]+)\s*seconds')


def _run_bs_roformer_with_progress(cmd: list, wizard_model,
                                   progress_lo: int = 45,
                                   progress_hi: int = 85) -> int:
    """Run bs-roformer-infer and update wizard progress from stdout."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    last_update = 0.0
    buf = ''
    total_est: float | None = None
    span = progress_hi - progress_lo

    while True:
        chunk = proc.stdout.read(128)
        if not chunk and proc.poll() is not None:
            break
        if not chunk:
            continue
        buf += chunk

        if total_est is None:
            m = _re.search(
                r'Estimated total processing time.*?:\s*([\d.]+)', buf)
            if m:
                total_est = float(m.group(1))

        matches = _ROFORMER_REMAINING_RE.findall(buf)
        if matches and total_est and total_est > 0:
            remaining = float(matches[-1])
            fraction = max(0.0, 1.0 - remaining / total_est)
            progress = progress_lo + int(fraction * span)
            now = _time.monotonic()
            if now - last_update > 2.0:
                wizard_model.bg_task_progress = min(progress, progress_hi)
                wizard_model.bg_task_message = (
                    f'BS-RoFormer: separating guitar/piano/other… '
                    f'{int(fraction * 100)}%')
                wizard_model.save(
                    update_fields=['bg_task_progress', 'bg_task_message'])
                last_update = now

        buf = buf[-400:]

    proc.wait()
    return proc.returncode


# -- Main stem separation entry point -------------------------------------

_DEMUCS_PROVIDES = {'Vocals', 'Drums', 'Bass'}
_ROFORMER_PROVIDES = {'Guitar', 'Keys', 'Other'}


def run_demucs(wizard_model) -> None:
    """
    Hybrid stem separation combining two models for best quality.

    Only the stems listed in ``wizard_model.selected_stems`` are generated.
    Demucs (htdemucs_ft) is used for Vocals/Drums/Bass; BS-RoFormer-SW is
    used for Guitar/Keys/Other.  Either phase is skipped entirely when none
    of its stems are requested.
    """
    staging = Path(wizard_model.staging_dir)
    master = staging / 'Master.wav'
    if not master.exists():
        master = staging / 'Master.flac'
    if not master.exists():
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = 'Master audio not found'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])
        return

    selected = set(wizard_model.selected_stems or [])
    if not selected:
        selected = _DEMUCS_PROVIDES | _ROFORMER_PROVIDES

    need_demucs = bool(selected & _DEMUCS_PROVIDES)
    need_roformer = bool(selected & _ROFORMER_PROVIDES)

    if need_demucs and need_roformer:
        demucs_lo, demucs_hi = 3, 40
        roformer_lo, roformer_hi = 46, 88
    elif need_demucs:
        demucs_lo, demucs_hi = 3, 90
        roformer_lo, roformer_hi = 0, 0
    else:
        demucs_lo, demucs_hi = 0, 0
        roformer_lo, roformer_hi = 3, 90

    try:
        python_bin = _sys.executable
        device = _detect_torch_device()

        # ── Phase 1: htdemucs_ft for vocals / drums / bass ───────────
        demucs_ok = False
        if need_demucs:
            wanted = sorted(selected & _DEMUCS_PROVIDES)
            wizard_model.bg_task_progress = 2
            wizard_model.bg_task_message = (
                f'Phase 1 – Demucs ({", ".join(wanted)})…')
            wizard_model.save(update_fields=[
                'bg_task_progress', 'bg_task_message'])

            demucs_out = staging / 'demucs_out'
            demucs_out.mkdir(exist_ok=True)

            for model_name in ('htdemucs_ft', 'htdemucs_6s', 'htdemucs'):
                cmd = [
                    python_bin, '-m', 'demucs',
                    '-n', model_name,
                    '-d', device,
                    '--shifts', '5',
                    '--flac',
                    '-o', str(demucs_out),
                    str(master),
                ]
                logger.info('Running demucs: %s', ' '.join(cmd))
                try:
                    rc = _run_demucs_with_progress(
                        cmd, wizard_model,
                        progress_lo=demucs_lo, progress_hi=demucs_hi)
                    if rc == 0:
                        logger.info('Demucs %s succeeded', model_name)
                        demucs_ok = True
                        break
                    logger.warning('Demucs %s failed (rc=%d)',
                                   model_name, rc)
                except Exception as exc:
                    logger.warning('Demucs %s exception: %s',
                                   model_name, exc)
                    continue

            if not demucs_ok:
                raise RuntimeError('All Demucs models failed. Check logs.')

            demucs_stem_map = {
                'vocals': 'Vocals', 'drums': 'Drums', 'bass': 'Bass',
                'guitar': 'Guitar', 'piano': 'Keys', 'other': 'Other',
            }
            for model_dir in demucs_out.iterdir():
                if not model_dir.is_dir():
                    continue
                for track_dir in model_dir.iterdir():
                    if not track_dir.is_dir():
                        continue
                    for stem_file in track_dir.iterdir():
                        if stem_file.suffix.lower() not in ('.wav', '.flac'):
                            continue
                        key = stem_file.stem.lower()
                        dest_name = demucs_stem_map.get(key)
                        if dest_name and dest_name in selected:
                            dest = staging / f'{dest_name}{stem_file.suffix}'
                            shutil.copy2(str(stem_file), str(dest))

            shutil.rmtree(str(demucs_out), ignore_errors=True)

        # ── Phase 2: BS-RoFormer-SW for guitar / piano / other ───────
        roformer_ok = False
        if need_roformer:
            try:
                ckpt, cfg = _ensure_bs_roformer_model()

                wanted = sorted(selected & _ROFORMER_PROVIDES)
                wizard_model.bg_task_progress = roformer_lo
                wizard_model.bg_task_message = (
                    f'Phase 2 – BS-RoFormer ({", ".join(wanted)})…')
                wizard_model.save(update_fields=[
                    'bg_task_progress', 'bg_task_message'])

                roformer_input = staging / '_roformer_in'
                roformer_input.mkdir(exist_ok=True)
                roformer_output = staging / '_roformer_out'
                roformer_output.mkdir(exist_ok=True)

                master_wav = roformer_input / 'Master.wav'
                if master.suffix.lower() == '.wav':
                    shutil.copy2(str(master), str(master_wav))
                else:
                    import soundfile as sf
                    data, sr = sf.read(str(master))
                    sf.write(str(master_wav), data, sr)

                roformer_bin = str(
                    Path(python_bin).parent / 'bs-roformer-infer')
                cmd = [
                    roformer_bin,
                    '--config_path', str(cfg),
                    '--model_path', str(ckpt),
                    '--input_folder', str(roformer_input),
                    '--store_dir', str(roformer_output),
                    '--device', device,
                ]
                logger.info('Running BS-RoFormer: %s', ' '.join(cmd))

                rc = _run_bs_roformer_with_progress(
                    cmd, wizard_model,
                    progress_lo=roformer_lo, progress_hi=roformer_hi)

                if rc == 0:
                    roformer_stem_map = {
                        'guitar': 'Guitar', 'piano': 'Keys',
                        'other': 'Other',
                    }
                    for wav_file in roformer_output.iterdir():
                        if wav_file.suffix.lower() != '.wav':
                            continue
                        stem_key = wav_file.stem.rsplit('_', 1)[-1].lower()
                        dest_name = roformer_stem_map.get(stem_key)
                        if dest_name and dest_name in selected:
                            dest = staging / f'{dest_name}.wav'
                            for old in staging.glob(f'{dest_name}.*'):
                                if old.suffix.lower() in ('.wav', '.flac'):
                                    old.unlink()
                            shutil.copy2(str(wav_file), str(dest))
                            logger.info('Using BS-RoFormer %s stem',
                                        dest_name)
                    roformer_ok = True
                else:
                    logger.warning('BS-RoFormer failed (rc=%d)', rc)

                shutil.rmtree(str(roformer_input), ignore_errors=True)
                shutil.rmtree(str(roformer_output), ignore_errors=True)

            except Exception as exc:
                logger.warning('BS-RoFormer phase skipped: %s', exc)
                for tmp in (staging / '_roformer_in',
                            staging / '_roformer_out'):
                    shutil.rmtree(str(tmp), ignore_errors=True)

        # ── Wrap up ──────────────────────────────────────────────────
        missing = [
            s for s in selected
            if not any((staging / f'{s}{ext}').exists()
                       for ext in ('.wav', '.flac'))
        ]
        if missing:
            raise FileNotFoundError(
                f'Missing stems after separation: {missing}')

        parts = []
        if demucs_ok:
            parts.append('Demucs')
        if roformer_ok:
            parts.append('BS-RoFormer')
        src = ' + '.join(parts) or 'N/A'
        wizard_model.bg_task_status = 'done'
        wizard_model.bg_task_progress = 100
        wizard_model.bg_task_message = f'Stem separation complete ({src})'
        wizard_model.save(update_fields=[
            'bg_task_status', 'bg_task_progress', 'bg_task_message'])

    except Exception as e:
        logger.exception('Stem separation failed')
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = f'Stem separation failed: {e}'
        wizard_model.save(update_fields=['bg_task_status', 'bg_task_message'])


def start_demucs(wizard_model) -> None:
    """Kick off Demucs in a background thread."""
    wizard_model.bg_task_status = 'running'
    wizard_model.bg_task_progress = 0
    wizard_model.bg_task_message = 'Starting stem separation...'
    wizard_model.save(update_fields=['bg_task_status', 'bg_task_progress', 'bg_task_message'])

    t = threading.Thread(
        target=run_demucs,
        args=(wizard_model,),
        daemon=True,
    )
    t.start()


def is_demucs_available() -> bool:
    """Check if demucs is importable."""
    try:
        import demucs
        return True
    except ImportError:
        return False


def is_bs_roformer_available() -> bool:
    """Check if bs-roformer-infer is importable."""
    try:
        import bs_roformer
        return True
    except ImportError:
        return False
