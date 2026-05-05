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

GETSONG_API_BASE = 'https://api.getsong.co'


def _api_get(base_url: str, path: str, params: dict) -> dict:
    """Make a GET request to the GetSong API."""
    qs = urllib.parse.urlencode(params)
    url = f'{base_url}{path}?{qs}'
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def lookup_tempo_key(title: str, artist: str, api_key: str = '') -> dict:
    """
    Look up tempo, key, and time signature via api.getsong.co.

    GetSongBPM and GetSongKey share the same API. The search endpoint
    returns tempo, key, and time signature directly in results.

    Returns a dict with keys: tempo, key, time_signature, source, error.
    """
    result = {'tempo': None, 'key': None, 'time_signature': None, 'source': None, 'error': None}

    if not api_key:
        result['error'] = 'No API key configured. Add a GetSongBPM/GetSongKey key in Settings.'
        return result

    try:
        search_data = _api_get(GETSONG_API_BASE, '/search/', {
            'api_key': api_key,
            'type': 'both',
            'lookup': f'song:{title} artist:{artist}',
        })

        search_results = search_data.get('search', [])
        if not search_results:
            result['error'] = 'No results found.'
            return result

        # search_results should be a list; handle dict edge case
        if isinstance(search_results, dict):
            search_results = list(search_results.values())
        if not isinstance(search_results, list) or not search_results:
            result['error'] = 'Unexpected search response format.'
            return result

        song = search_results[0]

        tempo = song.get('tempo')
        if tempo:
            try:
                result['tempo'] = int(round(float(tempo)))
            except (ValueError, TypeError):
                pass

        key_info = song.get('key_of')
        if key_info:
            result['key'] = key_info

        time_sig = song.get('time_sig')
        if time_sig:
            result['time_signature'] = str(time_sig)

        if result['tempo'] or result['key']:
            result['source'] = 'getsongbpm'
        else:
            result['error'] = 'Track found but no tempo or key data available.'

    except urllib.error.HTTPError as e:
        result['error'] = f'API error: HTTP {e.code}'
    except Exception as e:
        logger.exception('Tempo/key lookup failed')
        result['error'] = f'Lookup error: {e}'

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
# Stem separation via audio-separator (multi-pass, best model per stem)
# ---------------------------------------------------------------------------

import os as _os
import re as _re
import sys as _sys
import time as _time


# Model configuration: which model provides each stem at best quality.
# Stems are grouped by model so each model only loads once.
_MODEL_CONFIGS = [
    {
        'model': 'model_bs_roformer_ep_317_sdr_12.9755.ckpt',
        'label': 'RoFormer',
        'provides': {'Vocals': 'Vocals'},
    },
    {
        'model': 'htdemucs_ft.yaml',
        'label': 'htdemucs_ft',
        'provides': {'Drums': 'Drums', 'Bass': 'Bass', 'Other': 'Other'},
    },
    {
        'model': 'htdemucs_6s.yaml',
        'label': 'htdemucs_6s',
        'provides': {'Guitar': 'Guitar', 'Keys': 'Piano'},
    },
]

ALL_STEMS = set()
for _cfg in _MODEL_CONFIGS:
    ALL_STEMS.update(_cfg['provides'].keys())

_PROGRESS_RE = _re.compile(r'(\d+)%')


def _run_separator_pass(
    master_path: str,
    model_filename: str,
    output_dir: str,
    wizard_model,
    progress_lo: int,
    progress_hi: int,
    label: str,
    single_stem: str | None = None,
) -> list[str]:
    """Run audio-separator and return output file paths.

    If single_stem is None, all stems the model produces are output.
    """
    separator_bin = str(Path(_sys.executable).parent / 'audio-separator')

    cmd = [
        separator_bin,
        master_path,
        '--model_filename', model_filename,
        '--output_dir', output_dir,
        '--output_format', 'FLAC',
    ]
    if single_stem:
        cmd.extend(['--single_stem', single_stem])
    logger.info('Running audio-separator: %s', ' '.join(cmd))

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False,
    )
    _os.set_blocking(proc.stdout.fileno(), False)

    last_update = 0.0
    last_pause_check = 0.0
    buf = ''
    span = progress_hi - progress_lo
    all_output = ''
    current_pct = 0
    pass_num = 1
    idle_since = None

    while True:
        # Periodically check for pause (every 3 seconds)
        now_mono = _time.monotonic()
        if (now_mono - last_pause_check > 3.0
                and hasattr(wizard_model, 'is_paused')
                and wizard_model.is_paused()):
            logger.info('Pause detected for %s, killing subprocess', label)
            proc.kill()
            proc.wait()
            raise JobPausedError('Job paused by user')
        if now_mono - last_pause_check > 3.0:
            last_pause_check = now_mono

        try:
            raw = proc.stdout.read(512)
        except (OSError, IOError):
            raw = None
        if raw:
            text = raw.decode('utf-8', errors='replace')
            buf += text
            all_output += text
            idle_since = None
        elif proc.poll() is not None:
            try:
                remaining = proc.stdout.read()
                if remaining:
                    text = remaining.decode('utf-8', errors='replace')
                    all_output += text
            except (OSError, IOError):
                pass
            break
        else:
            if idle_since is None:
                idle_since = _time.monotonic()
                if current_pct >= 100:
                    wizard_model.bg_task_message = (
                        f'[{label}] Writing output…')
                elif current_pct == 0:
                    wizard_model.bg_task_message = (
                        f'[{label}] Loading model…')
                else:
                    wizard_model.bg_task_message = (
                        f'[{label}] Processing… {current_pct}%')
                try:
                    wizard_model.save(update_fields=['bg_task_message'])
                except Exception:
                    pass
            # Check if output files exist (process may be hung on cleanup)
            if idle_since and (_time.monotonic() - idle_since) > 10:
                out_files = [
                    f for f in Path(output_dir).iterdir()
                    if f.suffix.lower() in ('.wav', '.flac') and f.stat().st_size > 1000
                ]
                if out_files:
                    sizes = [f.stat().st_size for f in out_files]
                    _time.sleep(10)
                    new_sizes = [f.stat().st_size for f in out_files]
                    if sizes == new_sizes:
                        logger.info(
                            'Output files ready for %s, killing hung process',
                            label)
                        proc.kill()
                        proc.wait()
                        break
            if idle_since and (_time.monotonic() - idle_since) > 600:
                logger.warning(
                    'audio-separator stuck for 10min for %s, killing', label)
                proc.kill()
                proc.wait()
                break
            _time.sleep(0.2)
            continue

        matches = _PROGRESS_RE.findall(buf)
        if matches:
            pct = int(matches[-1])
            if pct < current_pct - 10:
                pass_num += 1
            current_pct = pct
            progress = progress_lo + int(pct / 100.0 * span)
            now = _time.monotonic()
            if now - last_update > 2.0:
                wizard_model.bg_task_progress = min(progress, progress_hi)
                pass_str = f' (pass {pass_num})' if pass_num > 1 else ''
                wizard_model.bg_task_message = (
                    f'[{label}] Processing… {pct}%{pass_str}')
                try:
                    wizard_model.save(
                        update_fields=['bg_task_progress', 'bg_task_message'])
                except Exception:
                    pass
                last_update = now

        buf = buf[-400:]

    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    if proc.returncode not in (0, -9, None):
        raise RuntimeError(
            f'audio-separator failed for {label} (rc={proc.returncode}): '
            f'{all_output[-300:]}')

    output_path = Path(output_dir)
    return [str(f) for f in output_path.iterdir()
            if f.suffix.lower() in ('.wav', '.flac')]


def run_stem_separation(wizard_model) -> None:
    """
    Multi-pass stem separation using audio-separator with best model per stem.

    Each model is loaded only once and all needed stems are extracted from its
    output.  This minimizes model loads (3 max) for maximum speed:
      - RoFormer → Vocals
      - htdemucs_ft → Drums, Bass, Other
      - htdemucs_6s → Guitar, Keys (Piano)
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
        selected = ALL_STEMS.copy()

    # Skip stems that already exist in staging (supports resume after pause)
    already_done = set()
    for stem in list(selected):
        if any((staging / f'{stem}{ext}').exists()
               for ext in ('.wav', '.flac')):
            already_done.add(stem)
    remaining = selected - already_done

    # Determine which models need to run and which stems to extract from each
    runs = []
    for cfg in _MODEL_CONFIGS:
        needed = {
            stem: output_name
            for stem, output_name in cfg['provides'].items()
            if stem in remaining
        }
        if needed:
            runs.append({
                'model': cfg['model'],
                'label': cfg['label'],
                'stems': needed,
            })

    total_runs = len(runs)
    if total_runs == 0:
        msg = 'All stems already separated' if already_done else 'No stems to separate'
        wizard_model.bg_task_status = 'done'
        wizard_model.bg_task_progress = 100
        wizard_model.bg_task_message = msg
        wizard_model.save(update_fields=[
            'bg_task_status', 'bg_task_progress', 'bg_task_message'])
        return

    try:
        separated_stems = []

        for run_idx, run in enumerate(runs):
            model_name = run['model']
            model_label = run['label']
            stems_needed = run['stems']
            stem_names_str = ', '.join(sorted(stems_needed.keys()))

            progress_lo = 2 + int(run_idx / total_runs * 90)
            progress_hi = 2 + int((run_idx + 1) / total_runs * 90)

            # If only one stem needed from this model, use --single_stem
            single_stem = None
            if len(stems_needed) == 1:
                single_stem = list(stems_needed.values())[0]

            wizard_model.bg_task_progress = progress_lo
            wizard_model.bg_task_message = (
                f'[{stem_names_str}] Running {model_label} '
                f'({run_idx + 1}/{total_runs})')
            try:
                wizard_model.save(update_fields=[
                    'bg_task_progress', 'bg_task_message'])
            except Exception:
                pass

            sep_out = staging / f'_sep_run_{run_idx}'
            sep_out.mkdir(exist_ok=True)

            try:
                output_files = _run_separator_pass(
                    master_path=str(master),
                    model_filename=model_name,
                    output_dir=str(sep_out),
                    wizard_model=wizard_model,
                    progress_lo=progress_lo,
                    progress_hi=progress_hi,
                    label=stem_names_str,
                    single_stem=single_stem,
                )

                # Match output files to requested stems
                # Output naming: {input_stem}_({StemName})_{model}.flac
                for stem_name, output_stem_name in stems_needed.items():
                    target_tag = f'({output_stem_name})'
                    matched = False
                    for out_file in output_files:
                        if target_tag.lower() in out_file.lower():
                            out_path = Path(out_file)
                            dest = staging / f'{stem_name}{out_path.suffix}'
                            for old in staging.glob(f'{stem_name}.*'):
                                if old.suffix.lower() in ('.wav', '.flac'):
                                    old.unlink()
                            shutil.copy2(str(out_path), str(dest))
                            separated_stems.append(stem_name)
                            logger.info('Separated %s → %s', stem_name,
                                        dest.name)
                            matched = True
                            break
                    if not matched:
                        logger.warning(
                            'Stem %s (%s) not found in output files: %s',
                            stem_name, target_tag, output_files)

                wizard_model.bg_task_message = (
                    f'[{stem_names_str}] Done ✓')
                try:
                    wizard_model.save(update_fields=['bg_task_message'])
                except Exception:
                    pass

            finally:
                shutil.rmtree(str(sep_out), ignore_errors=True)

            # Check for pause between model runs
            if hasattr(wizard_model, 'is_paused') and wizard_model.is_paused():
                raise JobPausedError('Job paused by user')

        # ── Wrap up ──────────────────────────────────────────────────
        missing = [
            s for s in selected
            if not any((staging / f'{s}{ext}').exists()
                       for ext in ('.wav', '.flac'))
        ]
        if missing:
            raise FileNotFoundError(
                f'Missing stems after separation: {missing}')

        wizard_model.bg_task_status = 'done'
        wizard_model.bg_task_progress = 100
        wizard_model.bg_task_message = (
            f'Stem separation complete ({len(separated_stems)} stems)')
        wizard_model.save(update_fields=[
            'bg_task_status', 'bg_task_progress', 'bg_task_message'])

    except JobPausedError:
        raise
    except Exception as e:
        logger.exception('Stem separation failed')
        wizard_model.bg_task_status = 'error'
        wizard_model.bg_task_message = f'Stem separation failed: {e}'
        try:
            wizard_model.save(
                update_fields=['bg_task_status', 'bg_task_message'])
        except Exception:
            pass


def start_demucs(wizard_model) -> None:
    """Kick off stem separation in a background thread (legacy, unused)."""
    wizard_model.bg_task_status = 'running'
    wizard_model.bg_task_progress = 0
    wizard_model.bg_task_message = 'Starting stem separation...'
    wizard_model.save(update_fields=[
        'bg_task_status', 'bg_task_progress', 'bg_task_message'])

    t = threading.Thread(
        target=run_stem_separation,
        args=(wizard_model,),
        daemon=True,
    )
    t.start()


def is_demucs_available() -> bool:
    """Check if audio-separator is installed."""
    separator_bin = Path(_sys.executable).parent / 'audio-separator'
    return separator_bin.exists()


# ---------------------------------------------------------------------------
# Queue-based stem separation
# ---------------------------------------------------------------------------

_queue_lock = threading.Lock()
_queue_thread_active = False


class JobPausedError(Exception):
    """Raised when a queue job is paused mid-processing."""
    pass


class _JobProgressAdapter:
    """Adapter so run_stem_separation / _run_separator_pass can update a
    StemSeparationJob using the same interface as a SongWizard model."""

    def __init__(self, job):
        self._job = job

    _STATUS_MAP = {'error': 'failed', 'running': 'processing'}

    @property
    def bg_task_status(self):
        return self._job.status

    @bg_task_status.setter
    def bg_task_status(self, value):
        self._job.status = self._STATUS_MAP.get(value, value)

    @property
    def bg_task_progress(self):
        return self._job.progress

    @bg_task_progress.setter
    def bg_task_progress(self, value):
        self._job.progress = value

    @property
    def bg_task_message(self):
        return self._job.message

    @bg_task_message.setter
    def bg_task_message(self, value):
        self._job.message = value

    @property
    def staging_dir(self):
        return self._job.staging_dir

    @property
    def selected_stems(self):
        return self._job.selected_stems

    def is_paused(self) -> bool:
        """Check the database for whether this job has been paused."""
        from .models import StemSeparationJob
        try:
            current = StemSeparationJob.objects.only('status').get(pk=self._job.pk)
            return current.status == 'paused'
        except StemSeparationJob.DoesNotExist:
            return False

    def save(self, update_fields=None):
        from .models import StemSeparationJob
        field_map = {
            'bg_task_progress': 'progress',
            'bg_task_message': 'message',
            'bg_task_status': 'status',
        }
        actual_fields = []
        for f in (update_fields or []):
            actual_fields.append(field_map.get(f, f))
        StemSeparationJob.objects.filter(pk=self._job.pk).update(
            **{f: getattr(self._job, f) for f in actual_fields})


def _finalize_song(job) -> None:
    """Move stems from staging to the final song directory and create metadata."""
    import soundfile as sf
    staging = Path(job.staging_dir)
    song_dir = Path(job.song_dir)
    song_dir.mkdir(parents=True, exist_ok=True)

    audio_extensions = {'.mp3', '.wav', '.flac', '.ogg', '.aiff', '.aif'}
    for f in staging.iterdir():
        if f.is_file() and not f.name.endswith('_original.wav'):
            shutil.copy2(str(f), str(song_dir / f.name))

    for wav_file in list(song_dir.glob('*.wav')):
        flac_file = wav_file.with_suffix('.flac')
        if flac_file.exists():
            wav_file.unlink()
            continue
        data, sr = sf.read(str(wav_file))
        sf.write(str(flac_file), data, sr)
        wav_file.unlink()

    info = {
        'title': job.title,
        'artist': job.artist,
        'tempo': job.tempo,
        'key': job.key,
        'time_signature': job.time_signature,
        'lyric_offset': job.lyric_offset_secs,
    }
    if job.band_details:
        info.update(job.band_details)

    info_path = song_dir / 'info.json'
    with open(info_path, 'w') as f:
        import json as _json
        _json.dump(info, f, indent=2)

    if job.lyrics_content:
        lrc_path = song_dir / 'lyrics.lrc'
        lrc_path.write_text(job.lyrics_content.strip() + '\n', encoding='utf-8')


def _queue_worker_loop() -> None:
    """Process queued jobs one at a time until none remain.

    Uses the DB to ensure only one job runs at a time across all processes:
    skips starting if any job is already in 'processing' state.
    """
    global _queue_thread_active
    from .models import StemSeparationJob
    from django.db import transaction

    try:
        while True:
            with transaction.atomic():
                already_processing = StemSeparationJob.objects.filter(
                    status='processing').exists()
                if already_processing:
                    break

                job = (StemSeparationJob.objects
                       .select_for_update(skip_locked=True)
                       .filter(status='queued')
                       .order_by('created_at')
                       .first())
                if not job:
                    break
                job.status = 'processing'
                job.progress = 0
                job.message = 'Starting stem separation...'
                job.save()

            _process_queue_job(job)

            # If the job was paused, don't auto-start the next one
            if _job_still_exists(job):
                job.refresh_from_db()
                if job.status == 'paused':
                    break
    finally:
        with _queue_lock:
            _queue_thread_active = False


def _job_still_exists(job) -> bool:
    from .models import StemSeparationJob
    return StemSeparationJob.objects.filter(pk=job.pk).exists()


def _process_queue_job(job) -> None:
    """Process a single queue job: run stem separation then finalize."""
    from .models import StemSeparationJob
    from django.utils import timezone

    adapter = _JobProgressAdapter(job)

    try:
        selected = set(job.selected_stems or [])
        if selected:
            run_stem_separation(adapter)
            if not _job_still_exists(job):
                logger.info('Queue job %d was deleted during processing', job.pk)
                return
            job.refresh_from_db()
            if job.status in ('failed', 'paused'):
                return
        else:
            job.message = 'Skipping separation (stems uploaded)'
            job.progress = 90
            job.save()

        if not _job_still_exists(job):
            logger.info('Queue job %d was deleted during processing', job.pk)
            return

        job.message = 'Finalizing song...'
        job.progress = 95
        job.save()

        _finalize_song(job)

        if job.staging_dir and Path(job.staging_dir).exists():
            shutil.rmtree(job.staging_dir, ignore_errors=True)

        job.status = 'done'
        job.progress = 100
        job.message = 'Complete'
        job.completed_at = timezone.now()
        job.save()

    except JobPausedError:
        logger.info('Queue job %d paused by user', job.pk)
        if _job_still_exists(job):
            job.refresh_from_db()
            job.status = 'paused'
            job.message = 'Paused'
            job.save()
    except StemSeparationJob.DoesNotExist:
        logger.info('Queue job %d was deleted during processing', job.pk)
    except Exception as e:
        logger.exception('Queue job %d failed', job.pk)
        if _job_still_exists(job):
            job.status = 'failed'
            job.message = f'Failed: {e}'
            try:
                job.save()
            except Exception:
                pass


def start_queue_worker() -> None:
    """Start the queue worker thread if not already running in this process.

    The DB-level check in _queue_worker_loop ensures only one job runs
    at a time even across multiple gunicorn workers.
    """
    global _queue_thread_active
    with _queue_lock:
        if _queue_thread_active:
            return
        _queue_thread_active = True

    t = threading.Thread(target=_queue_worker_loop, daemon=True)
    t.start()
