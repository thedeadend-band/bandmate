import fcntl
import logging
import threading
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone


logger = logging.getLogger(__name__)

_thread_lock = threading.Lock()
_thread_active = False


def _next_job():
    from .models import PitchShiftJob, StemSeparationJob

    stem_job = StemSeparationJob.objects.filter(
        status='queued').order_by('created_at').first()
    pitch_job = PitchShiftJob.objects.filter(
        status='queued').order_by('created_at').first()
    candidates = [
        (job.created_at, kind, job.pk)
        for kind, job in (('stem', stem_job), ('pitch', pitch_job))
        if job is not None
    ]
    if not candidates:
        return None
    _, kind, job_id = min(candidates, key=lambda item: item[0])

    model = StemSeparationJob if kind == 'stem' else PitchShiftJob
    with transaction.atomic():
        job = model.objects.select_for_update().filter(
            pk=job_id, status='queued').first()
        if not job:
            return 'retry', None
        job.status = 'processing'
        job.progress = 0
        job.message = (
            'Starting stem separation...'
            if kind == 'stem' else 'Preparing original tracks'
        )
        job.save()
    return kind, job


def _process_pitch_job(job) -> None:
    from .pitch_services import _process_job, _update_job

    try:
        _process_job(job)
    except Exception as exc:
        logger.exception('Pitch shift job %d failed', job.pk)
        _update_job(
            job,
            status='failed',
            message=f'Failed: {exc}',
            completed_at=timezone.now(),
        )


def _worker_loop() -> None:
    global _thread_active

    lock_path = Path(settings.SONGS_DIR) / '.audio-job-queue.lock'
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

            while True:
                next_job = _next_job()
                if next_job is None:
                    break
                kind, job = next_job
                if kind == 'retry':
                    continue
                if kind == 'stem':
                    from .wizard_services import _job_still_exists, _process_queue_job
                    _process_queue_job(job)
                    if _job_still_exists(job):
                        job.refresh_from_db()
                        if job.status == 'paused':
                            break
                else:
                    _process_pitch_job(job)
    finally:
        with _thread_lock:
            _thread_active = False


def start_audio_queue_worker() -> None:
    global _thread_active

    with _thread_lock:
        if _thread_active:
            return
        _thread_active = True
    threading.Thread(target=_worker_loop, daemon=True).start()
