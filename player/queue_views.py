"""
Views for the Stem Separation Queue.
"""

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import StemSeparationJob


def _song_url(job):
    dir_name = f'{job.artist} - {job.title}'.replace('/', '-').replace('\\', '-')
    return reverse('song_player', kwargs={'song_name': dir_name})


def _can_manage_job(user, job):
    return user.is_staff or job.created_by_id == user.id


@login_required
def queue_list(request):
    jobs = StemSeparationJob.objects.select_related('created_by')
    for job in jobs:
        if job.status == 'done':
            job.song_page_url = _song_url(job)
        job.can_manage = _can_manage_job(request.user, job)
    return render(request, 'player/queue.html', {
        'nav_active': 'queue',
        'jobs': jobs,
    })


@login_required
@require_POST
def queue_delete(request, job_id):
    import shutil
    from pathlib import Path

    job = get_object_or_404(StemSeparationJob, pk=job_id)
    if not _can_manage_job(request.user, job):
        raise Http404
    if job.staging_dir and Path(job.staging_dir).exists():
        shutil.rmtree(job.staging_dir, ignore_errors=True)
    job.delete()
    return redirect('queue_list')


@login_required
@require_POST
def queue_pause(request, job_id):
    job = get_object_or_404(StemSeparationJob, pk=job_id)
    if not _can_manage_job(request.user, job):
        raise Http404
    if job.status == 'processing':
        job.status = 'paused'
        job.message = 'Pausing…'
        job.save()
    return redirect('queue_list')


@login_required
@require_POST
def queue_resume(request, job_id):
    from .wizard_services import start_queue_worker
    job = get_object_or_404(StemSeparationJob, pk=job_id)
    if not _can_manage_job(request.user, job):
        raise Http404
    if job.status in ('paused', 'failed'):
        job.status = 'queued'
        job.message = 'Resuming…'
        job.save()
        start_queue_worker()
    return redirect('queue_list')


@login_required
def queue_job_status(request, job_id):
    job = get_object_or_404(StemSeparationJob, pk=job_id)
    return JsonResponse({
        'status': job.status,
        'progress': job.progress,
        'message': job.message,
        'gpu_used': job.gpu_used,
    })


@login_required
def queue_notifications(request):
    jobs = list(StemSeparationJob.objects.filter(
        status__in=('done', 'failed'),
        notified=False,
    ))
    results = []
    for job in jobs:
        results.append({
            'id': job.pk,
            'title': job.title,
            'artist': job.artist,
            'status': job.status,
            'song_url': _song_url(job) if job.status == 'done' else None,
        })
    StemSeparationJob.objects.filter(
        pk__in=[j.pk for j in jobs]).update(notified=True)
    return JsonResponse({'notifications': results})
