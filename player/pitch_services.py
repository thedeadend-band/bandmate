import json
import logging
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from django.conf import settings
from django.db import transaction
from django.utils import timezone


logger = logging.getLogger(__name__)

PITCH_VARIANTS_DIR = 'pitch-variants'
PITCH_MANIFEST = 'manifest.json'
UNSHIFTED_TRACK_WORDS = ('drum', 'percussion', 'click', 'metronome')

_worker_lock = threading.Lock()
_worker_active = False


def _safe_song_dir(song_name: str) -> Path:
    songs_dir = Path(settings.SONGS_DIR).resolve()
    song_dir = (songs_dir / song_name).resolve()
    try:
        song_dir.relative_to(songs_dir)
    except ValueError as exc:
        raise ValueError('Invalid song path') from exc
    if not song_dir.is_dir():
        raise FileNotFoundError(f'Song not found: {song_name}')
    return song_dir


def _audio_files(song_dir: Path) -> list[Path]:
    extensions = {'.mp3', '.wav', '.flac', '.ogg', '.aiff', '.aif'}
    return sorted(
        path for path in song_dir.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    )


def _is_unshifted_track(path: Path) -> bool:
    name = path.stem.lower()
    return any(word in name for word in UNSHIFTED_TRACK_WORDS)


def _fit_frames(audio: np.ndarray, frame_count: int) -> np.ndarray:
    if audio.shape[0] > frame_count:
        return audio[:frame_count]
    if audio.shape[0] < frame_count:
        padding = np.zeros((frame_count - audio.shape[0], audio.shape[1]), dtype=audio.dtype)
        return np.concatenate((audio, padding), axis=0)
    return audio


def _write_variant_track(source: Path, destination: Path, semitones: int) -> None:
    audio, sample_rate = sf.read(str(source), dtype='float32', always_2d=True)
    original_frames = audio.shape[0]

    if _is_unshifted_track(source):
        processed = audio
    else:
        import pyrubberband as pyrb
        processed = pyrb.pitch_shift(
            audio, sample_rate, semitones,
            rbargs={'--formant': ''},
        )
        if processed.ndim == 1:
            processed = processed[:, np.newaxis]

    processed = _fit_frames(np.asarray(processed, dtype=np.float32), original_frames)
    sf.write(
        str(destination), processed, sample_rate,
        format='FLAC', subtype='PCM_24',
    )

    written = sf.info(str(destination))
    if written.frames != original_frames or written.samplerate != sample_rate:
        raise RuntimeError(f'Generated track failed duration validation: {source.name}')


def _build_master(track_paths: list[Path], original_master: Path, destination: Path) -> None:
    if not track_paths:
        raise RuntimeError('No stems are available to build the shifted master')

    command = ['ffmpeg', '-v', 'error', '-y']
    for path in track_paths:
        command.extend(['-i', str(path)])
    command.extend([
        '-filter_complex',
        f'amix=inputs={len(track_paths)}:duration=longest:normalize=0,alimiter=limit=0.98',
        '-c:a', 'flac', str(destination),
    ])
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'Failed to build shifted master: {result.stderr[-500:]}')

    original_info = sf.info(str(original_master))
    mixed, sample_rate = sf.read(str(destination), dtype='float32', always_2d=True)
    if sample_rate != original_info.samplerate:
        raise RuntimeError('Shifted master sample rate does not match the original')
    mixed = _fit_frames(mixed, original_info.frames)
    sf.write(
        str(destination), mixed, sample_rate,
        format='FLAC', subtype='PCM_24',
    )


def _update_job(job, **values) -> None:
    from .models import PitchShiftJob
    PitchShiftJob.objects.filter(pk=job.pk).update(**values)
    for key, value in values.items():
        setattr(job, key, value)


def _process_job(job) -> None:
    from .models import PitchShiftJob

    song_dir = _safe_song_dir(job.song_name)
    sources = _audio_files(song_dir)
    original_master = next((p for p in sources if p.stem.lower() == 'master'), None)
    stems = [p for p in sources if p.stem.lower() != 'master']
    if not original_master:
        raise RuntimeError('The song has no original Master track')
    if not stems:
        raise RuntimeError('The song has no stems to transpose')

    variants_root = song_dir / PITCH_VARIANTS_DIR
    variants_root.mkdir(exist_ok=True)
    building = variants_root / f'.building-{uuid.uuid4().hex}'
    final_dir = variants_root / f'{job.semitones:+d}'
    building.mkdir()

    try:
        track_map = {}
        generated_stems = []
        total_steps = len(stems) + 1
        for index, source in enumerate(stems, start=1):
            output_name = f'{index:02d}-{source.stem}.flac'
            destination = building / output_name
            action = 'Keeping original pitch' if _is_unshifted_track(source) else 'Transposing'
            _update_job(
                job,
                progress=int((index - 1) / total_steps * 90),
                message=f'{action}: {source.stem}',
            )
            _write_variant_track(source, destination, job.semitones)
            track_map[source.name] = output_name
            generated_stems.append(destination)

        _update_job(job, progress=90, message='Building shifted master')
        master_output = building / 'Master.flac'
        _build_master(generated_stems, original_master, master_output)
        track_map[original_master.name] = master_output.name

        manifest = {
            'version': 1,
            'semitones': job.semitones,
            'tracks': track_map,
        }
        (building / PITCH_MANIFEST).write_text(
            json.dumps(manifest, indent=2), encoding='utf-8')

        if final_dir.exists():
            shutil.rmtree(final_dir)
        building.rename(final_dir)

        info_path = song_dir / 'info.json'
        try:
            info = json.loads(info_path.read_text(encoding='utf-8')) if info_path.exists() else {}
        except (json.JSONDecodeError, OSError):
            info = {}
        info['pitch_shift_semitones'] = job.semitones
        info_path.write_text(json.dumps(info, indent=2), encoding='utf-8')

        for path in variants_root.iterdir():
            if path.is_dir() and path != final_dir:
                shutil.rmtree(path, ignore_errors=True)

        _update_job(
            job,
            status='done', progress=100, message='Pitch variant ready',
            completed_at=timezone.now(),
        )
    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise


def _worker_loop() -> None:
    global _worker_active
    from .models import PitchShiftJob

    try:
        while True:
            with transaction.atomic():
                job = (PitchShiftJob.objects.select_for_update(skip_locked=True)
                       .filter(status='queued').order_by('created_at').first())
                if not job:
                    break
                job.status = 'processing'
                job.progress = 0
                job.message = 'Preparing original tracks'
                job.save(update_fields=['status', 'progress', 'message', 'updated_at'])
            try:
                _process_job(job)
            except Exception as exc:
                logger.exception('Pitch shift job %d failed', job.pk)
                _update_job(
                    job, status='failed', message=f'Failed: {exc}',
                    completed_at=timezone.now(),
                )
    finally:
        with _worker_lock:
            _worker_active = False


def start_pitch_worker() -> None:
    global _worker_active
    with _worker_lock:
        if _worker_active:
            return
        _worker_active = True
    threading.Thread(target=_worker_loop, daemon=True).start()


def reset_pitch_variant(song_name: str) -> None:
    song_dir = _safe_song_dir(song_name)
    info_path = song_dir / 'info.json'
    try:
        info = json.loads(info_path.read_text(encoding='utf-8')) if info_path.exists() else {}
    except (json.JSONDecodeError, OSError):
        info = {}
    info.pop('pitch_shift_semitones', None)
    info_path.write_text(json.dumps(info, indent=2), encoding='utf-8')
    shutil.rmtree(song_dir / PITCH_VARIANTS_DIR, ignore_errors=True)
