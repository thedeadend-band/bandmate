import json
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from django.http import Http404
from bandmate.credential_vault import VaultUnavailable
from calendar_integrations.services.google import ProviderError
from player.group_permissions import audit
from booking.models import BookingJob, BookingResearchRun, BookingResearchSchedule, BookingMembershipRole, BookingGmailConnection
from booking.permissions import require_booking, booking_role
from .records import lock_band, digest
from .connections import authorized
from .imports import import_page, purge_messages
from .discovery import discover

RESEARCH_KINDS = ('gmail_import', 'web_discovery', 'web_search', 'geocode', 'ai_extract')


def new_preview(group, actor, kind, scope):
    require_booking(actor, group, [BookingMembershipRole.Role.MANAGER])
    if kind not in RESEARCH_KINDS:
        raise ProviderError('unsupported_research')
    generation, mailbox_hash = 0, ''
    if kind in ('web_search', 'geocode'):
        from .providers import configured
        configured('brave' if kind == 'web_search' else 'opencage')
    if kind == 'ai_extract':
        from .extraction import extraction_scope
        # Build scope from server-side evidence and policy, never posted prices.
        scope = extraction_scope(group, scope['candidate_id'])
        generation = scope['credential_generation']
    if kind == 'gmail_import':
        connection = BookingGmailConnection.objects.filter(group=group, is_connected=True).first()
        if not connection:
            raise ProviderError('gmail_disconnected')
        generation, mailbox_hash = connection.generation, digest(connection.mailbox)
    return BookingResearchRun.objects.create(group=group, actor=actor, kind=kind, scope=scope,
        scope_hash=digest(json.dumps(scope, sort_keys=True)), credential_generation=generation,
        mailbox_hash=mailbox_hash, expires_at=timezone.now() + timedelta(minutes=15))


@transaction.atomic
def confirm_preview(group, actor, run_id):
    lock_band(group)
    require_booking(actor, group, [BookingMembershipRole.Role.MANAGER])
    run = BookingResearchRun.objects.get(group=group, actor=actor, pk=run_id)
    if run.job_id:
        return run
    if run.status != 'preview' or run.expires_at <= timezone.now():
        raise ProviderError('preview_expired')
    if run.kind == 'ai_extract':
        from .extraction import extraction_scope
        if extraction_scope(group, run.scope['candidate_id']) != run.scope:
            raise ProviderError('ai_scope_changed')
    if run.kind == 'gmail_import' and not BookingGmailConnection.objects.filter(group=group,
            is_connected=True, generation=run.credential_generation).exists():
        raise ProviderError('credential_changed')
    today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if BookingJob.objects.filter(group=group, kind__in=RESEARCH_KINDS, created_at__gte=today).count() >= 10:
        raise ProviderError('daily_research_limit')
    job = BookingJob.objects.create(group=group, actor=actor, kind=run.kind,
        idempotency_key=run.request_key, credential_generation=run.credential_generation, run_after=timezone.now())
    run.job, run.status, run.expires_at = job, 'queued', timezone.now() + timedelta(days=2)
    run.save(update_fields=['job', 'status', 'expires_at'])
    audit(group, actor, 'booking.research.approved', run.pk, new=run.kind)
    return run


def process_research_job(job):
    run = BookingResearchRun.objects.filter(job=job, group_id=job.group_id).first()
    if not run:
        BookingJob.objects.filter(pk=job.pk, attempts=job.attempts).update(status='failed', error_code='missing_research_scope')
        return
    result, error, retry = None, '', False
    try:
        authorized(job)
        with transaction.atomic():
            lock_band(job.group)
            if not BookingResearchRun.objects.filter(pk=run.pk, status__in=['queued', 'running'], expires_at__gt=timezone.now()).update(status='running'):
                raise ProviderError('research_cancelled_or_expired')
        if job.kind == 'gmail_import':
            result = import_page(job)
        elif job.kind == 'web_discovery':
            result = discover(job)
        elif job.kind in ('web_search', 'geocode'):
            from .providers import research_public
            result = research_public(job)
        elif job.kind == 'ai_extract':
            from .extraction import extract
            result = extract(job)
        authorized(job)
    except ProviderError as exc:
        error = exc.code
        retry = job.kind != 'ai_extract' and (exc.status in (429, 500, 502, 503, 504) or exc.code.endswith('_transport')) and run.failures < 3
    except VaultUnavailable:
        error = 'vault_unavailable'
    except Exception:
        error = 'research_failed'
    with transaction.atomic():
        lock_band(job.group)
        if not BookingJob.objects.filter(pk=job.pk, status='processing', attempts=job.attempts).update(lease_until=None):
            return
        run.refresh_from_db()
        if run.status == 'cancelled':
            status = 'cancelled'
        elif error:
            run.failures += 1
            status = 'queued' if retry else 'failed'
        else:
            run.failures = 0
            status = 'limited' if result == 'limited' else 'succeeded' if result is True else 'queued'
        run.status, run.last_error_code = status, error
        run.completed_at = None if status == 'queued' else timezone.now()
        run.save(update_fields=['status', 'failures', 'last_error_code', 'completed_at'])
        BookingJob.objects.filter(pk=job.pk).update(status='pending' if status == 'queued' else status,
            error_code=error, run_after=timezone.now() + timedelta(minutes=2 ** run.failures if retry else 0),
            lease_until=None, finished_at=run.completed_at)
        if status != 'queued':
            audit(job.group, job.actor, 'booking.research.' + status, run.pk, new=job.kind)


def enqueue_due():
    count = 0
    for schedule in BookingResearchSchedule.objects.filter(enabled=True, next_due__lte=timezone.now()).select_related('actor', 'group', 'source_run').order_by('next_due')[:25]:
        with transaction.atomic():
            lock_band(schedule.group)
            current = BookingResearchSchedule.objects.get(pk=schedule.pk)
            if not current.enabled or current.next_due > timezone.now():
                continue
            if not schedule.actor or booking_role(schedule.actor, schedule.group) != BookingMembershipRole.Role.MANAGER:
                current.enabled = False
                current.save(update_fields=['enabled'])
                continue
            # Recurring discovery only revisits the exact approved public URL.
            # Gmail imports always require their own deliberate preview/consent.
            if schedule.source_run.kind != 'web_discovery':
                continue
            run = new_preview(schedule.group, schedule.actor, 'web_discovery', schedule.source_run.scope)
            try:
                confirm_preview(schedule.group, schedule.actor, run.pk)
                count += 1
            except ProviderError:
                run.status = 'failed'
                run.last_error_code = 'daily_research_limit'
                run.save(update_fields=['status', 'last_error_code'])
            current.next_due = timezone.now() + timedelta(days=current.interval_days)
            current.save(update_fields=['next_due'])
    # Retention applies even while Booking is OFF. It is local cleanup, never a
    # remote read or a reason to re-enable a band or schedule.
    from booking.models import BookingConversationReference
    from player.models import Group
    for group in Group.objects.filter(pk__in=BookingConversationReference.objects.filter(
            purged=False, expires_at__lte=timezone.now()).values('group_id')).distinct():
        purge_messages(group, expired_only=True)
    return count
