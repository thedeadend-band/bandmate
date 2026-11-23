"""Bounded, leased read-only checks; independent of audio and Calendar workers."""
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from bandmate.credential_vault import VaultUnavailable
from calendar_integrations.services.google import ProviderError
from player.group_permissions import audit
from booking.models import BookingJob, BookingOpenAICredential, BookingGmailConnection, BookingAIUsage
from .connections import authorized, test_openai, test_gmail


def enqueue(group, actor, kind, key, generation):
    job, created = BookingJob.objects.get_or_create(group=group, idempotency_key=key, defaults={
        'actor': actor, 'kind': kind, 'credential_generation': generation, 'run_after': timezone.now()})
    if job.actor_id != actor.pk or job.kind != kind or job.credential_generation != generation:
        raise ValueError('This request identifier has already been used.')
    if created:
        audit(group, actor, 'booking.connection_test.queued', job.pk, new=kind)
    return job


def process_batch():
    now = timezone.now()
    # Only idempotent read-only jobs can recover an expired lease automatically.
    eligible = Q(status='pending', run_after__lte=now) | Q(status='processing', lease_until__lt=now)
    ids = list(BookingJob.objects.filter(eligible).order_by('run_after', 'pk').values_list('pk', flat=True)[:settings.BOOKING_JOB_BATCH_SIZE])
    for pk in ids:
        if not BookingJob.objects.filter(eligible, pk=pk).update(status='processing',
                attempts=F('attempts') + 1, lease_until=now + timedelta(minutes=5)):
            continue
        job = BookingJob.objects.select_related('actor', 'group').get(pk=pk)
        from .research_jobs import RESEARCH_KINDS, process_research_job
        if job.kind in RESEARCH_KINDS:
            process_research_job(job)
            continue
        fingerprint = BookingOpenAICredential.objects.filter(group_id=job.group_id,
            generation=job.credential_generation).values_list('fingerprint', flat=True).first() or ''
        status, code, result = 'succeeded', '', None
        try:
            authorized(job)
            if job.attempts > 4:
                raise ProviderError('retry_limit')
            if job.kind == BookingJob.Kind.OPENAI_TEST:
                result = test_openai(job)
            elif job.kind == BookingJob.Kind.GMAIL_TEST:
                result = test_gmail(job)
            else:
                raise ProviderError('unsupported_job')
            authorized(job)
        except ProviderError as exc:
            code = exc.code
            status = 'pending' if job.attempts < 4 and (exc.status == 429 or exc.status >= 500 or exc.code.endswith('_transport')) else 'failed'
        except VaultUnavailable:
            status, code = 'failed', 'vault_unavailable'
        except Exception:
            # Persist a safe failure and continue other bands. No provider payload
            # or traceback enters logs, HTML or job metadata.
            status, code = 'failed', 'connection_check_failed'
        with transaction.atomic():
            # Fence completion against a newer lease after a stalled process.
            if not BookingJob.objects.filter(pk=job.pk, status='processing', attempts=job.attempts).update(
                    lease_until=timezone.now() + timedelta(minutes=5)):
                continue
            credential = None
            if job.kind == BookingJob.Kind.OPENAI_TEST:
                credential = BookingOpenAICredential.objects.filter(group_id=job.group_id, generation=job.credential_generation).first()
                if credential:
                    BookingOpenAICredential.objects.filter(pk=credential.pk, generation=job.credential_generation).update(
                        test_status=status, tested_at=timezone.now(),
                        available_models=result if status == 'succeeded' else [])
            elif job.kind == BookingJob.Kind.GMAIL_TEST:
                credential = BookingGmailConnection.objects.filter(group_id=job.group_id, generation=job.credential_generation).first()
                if credential:
                    BookingGmailConnection.objects.filter(pk=credential.pk, generation=job.credential_generation).update(
                        tested_at=timezone.now(), last_error_code=code)
            if not credential:
                status, code = 'failed', 'credential_changed'
            BookingJob.objects.filter(pk=job.pk).update(status=status, error_code=code, lease_until=None,
                run_after=timezone.now() + timedelta(minutes=2 ** job.attempts),
                finished_at=None if status == 'pending' else timezone.now())
            if status != 'pending':
                audit(job.group, job.actor, 'booking.connection_test.' + status, job.pk, new=job.kind)
            if job.kind == BookingJob.Kind.OPENAI_TEST:
                BookingAIUsage.objects.get_or_create(job=job, attempt=job.attempts, defaults={'group': job.group,
                    'task_type': 'connection_test', 'key_fingerprint': fingerprint, 'result': code or status})
    return len(ids)
