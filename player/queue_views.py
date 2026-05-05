"""
Views for the Stem Separation Queue.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import StemSeparationJob


def _song_url(job):
    dir_name = f'{job.artist} - {job.title}'.replace('/', '-').replace('\\', '-')
    return reverse('song_player', kwargs={'song_name': dir_name})


def _staff_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_staff:
            raise Http404
        return view_func(request, *args, **kwargs)
    return wrapper


@_staff_required
def queue_list(request):
    jobs = StemSeparationJob.objects.all()
    for job in jobs:
        if job.status == 'done':
            job.song_page_url = _song_url(job)
    return render(request, 'player/queue.html', {
        'nav_active': 'queue',
        'jobs': jobs,
    })


@_staff_required
@require_POST
def queue_delete(request, job_id):
    import shutil
    from pathlib import Path

    job = get_object_or_404(StemSeparationJob, pk=job_id)
    if job.staging_dir and Path(job.staging_dir).exists():
        shutil.rmtree(job.staging_dir, ignore_errors=True)
    job.delete()
    return redirect('queue_list')


@_staff_required
def queue_job_status(request, job_id):
    job = get_object_or_404(StemSeparationJob, pk=job_id)
    return JsonResponse({
        'status': job.status,
        'progress': job.progress,
        'message': job.message,
    })


@_staff_required
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
