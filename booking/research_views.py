import json
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.core.serializers.json import DjangoJSONEncoder
from calendar_integrations.services.google import ProviderError
from player.group_permissions import audit
from .models import (BookingMembershipRole, BookingResearchRun, BookingResearchSchedule, BookingCandidate,
    BookingOpportunity, BookingContact, BookingConversationReference, BookingJob)
from .permissions import require_booking
from .research_forms import GmailImportForm, DiscoveryForm
from .record_forms import OpportunityForm
from .services.records import EDIT_ROLES, lock_band, SuppressionIndex, save_record, remember_aliases
from .services.research_jobs import new_preview, confirm_preview
from .services.imports import purge_messages


def manager(request):
    return require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])


@login_required
@require_GET
def research(request):
    role = require_booking(request.user, request.active_group)
    purge_messages(request.active_group, expired_only=True)
    index = SuppressionIndex(request.active_group)
    candidates = [c for c in BookingCandidate.objects.filter(group=request.active_group, status='pending').order_by('-created_at')
        if not index.blocks_source(url=c.proposed.get('website', ''),
            emails=[c.proposed['contact_email']] if c.proposed.get('contact_email') else [], name=c.title, city=c.proposed.get('city', ''))]
    from django.core.paginator import Paginator
    return render(request, 'booking/research.html', {'nav_active': 'booking',
        'page': Paginator(candidates, 30).get_page(request.GET.get('page')),
        'runs': BookingResearchRun.objects.filter(group=request.active_group).order_by('-created_at')[:20],
        'schedules': BookingResearchSchedule.objects.filter(group=request.active_group),
        'is_manager': role == BookingMembershipRole.Role.MANAGER})


@login_required
@require_http_methods(['GET', 'POST'])
def preview(request, kind):
    manager(request)
    if kind not in ('gmail_import', 'web_discovery'):
        raise Http404
    form_class = GmailImportForm if kind == 'gmail_import' else DiscoveryForm
    form = form_class(request.POST if request.method == 'POST' else None,
        initial={'end_date': timezone.now().date()} if kind == 'gmail_import' else {})
    if request.method == 'POST' and form.is_valid():
        try:
            scope = form.scope() if kind == 'gmail_import' else {'url': form.cleaned_data['url'], 'ai_processing': False}
            run = new_preview(request.active_group, request.user, kind, scope)
            return redirect('booking:research_run', run_id=run.pk)
        except ProviderError:
            form.add_error(None, 'Connect this band’s Gmail account before requesting an import.')
    return render(request, 'booking/research_form.html', {'nav_active': 'booking', 'form': form, 'kind': kind})


@login_required
@require_GET
def run_detail(request, run_id):
    role = require_booking(request.user, request.active_group)
    run = get_object_or_404(BookingResearchRun, pk=run_id, group=request.active_group)
    return render(request, 'booking/research_run.html', {'nav_active': 'booking', 'run': run,
        'is_manager': role == BookingMembershipRole.Role.MANAGER,
        'can_confirm': role == BookingMembershipRole.Role.MANAGER and run.actor_id == request.user.pk and run.status == 'preview',
        'schedule': BookingResearchSchedule.objects.filter(group=request.active_group, source_run=run).first()})


@login_required
@require_POST
def confirm(request, run_id):
    manager(request)
    get_object_or_404(BookingResearchRun, pk=run_id, group=request.active_group, actor=request.user)
    if request.POST.get('consent') != 'scoped_read':
        return HttpResponseBadRequest('Confirm this exact read scope and retention policy.')
    try:
        confirm_preview(request.active_group, request.user, run_id)
    except ProviderError as exc:
        return HttpResponseBadRequest('Research could not be queued: ' + exc.code)
    return redirect('booking:research_run', run_id=run_id)


@login_required
@require_POST
def cancel(request, run_id):
    manager(request)
    with transaction.atomic():
        lock_band(request.active_group)
        manager(request)
        run = get_object_or_404(BookingResearchRun, pk=run_id, group=request.active_group)
        if run.status in ('preview', 'queued', 'running'):
            run.status = 'cancelled'
            run.save(update_fields=['status'])
            if run.job_id:
                BookingJob.objects.filter(pk=run.job_id, group=request.active_group).update(status='cancelled', lease_until=None)
            audit(request.active_group, request.user, 'booking.research.cancelled', run.pk)
    return redirect('booking:research_run', run_id=run_id)


@login_required
@require_http_methods(['GET', 'POST'])
def review(request, candidate_id):
    role = require_booking(request.user, request.active_group)
    purge_messages(request.active_group, expired_only=True)
    candidate = get_object_or_404(BookingCandidate, pk=candidate_id, group=request.active_group)
    if candidate.status == 'purged':
        raise Http404
    proposed = candidate.proposed
    initial = {key: proposed[key] for key in ('name', 'city', 'website', 'event_year', 'event_date') if key in proposed}
    initial['event_year'] = initial.get('event_year') or timezone.now().year + 1
    form = OpportunityForm(request.POST if request.method == 'POST' and request.POST.get('action') == 'accept' else None,
        instance=BookingOpportunity(group=request.active_group), group=request.active_group, initial=initial)
    if request.method == 'POST':
        require_booking(request.user, request.active_group, EDIT_ROLES)
        action = request.POST.get('action')
        if action not in ('accept', 'reject'):
            return HttpResponseBadRequest('Choose accept or reject.')
        if action == 'reject' or form.is_valid():
            try:
                with transaction.atomic():
                    lock_band(request.active_group)
                    require_booking(request.user, request.active_group, EDIT_ROLES)
                    candidate.refresh_from_db()
                    if candidate.status != 'pending':
                        return HttpResponseBadRequest('This candidate has already been reviewed or removed.')
                    index = SuppressionIndex(request.active_group)
                    if index.blocks_source(url=proposed.get('website', ''), emails=[proposed['contact_email']] if proposed.get('contact_email') else [],
                            name=candidate.title, city=proposed.get('city', '')):
                        return HttpResponseBadRequest('This source or identity is suppressed. A manager must review the blacklist first.')
                    if action == 'accept':
                        if request.POST.get('create_contact') == 'yes' and proposed.get('contact_email') and not form.instance.contact_id:
                            contact, _ = BookingContact.objects.get_or_create(group=request.active_group, email=proposed['contact_email'],
                                defaults={'name': proposed['contact_email']})
                            if contact.is_archived or index.blocked(contact, sending=True):
                                raise ValidationError('The suggested contact is archived or suppressed.')
                            remember_aliases(contact)
                            form.instance.contact = contact
                        if index.blocked(form.instance, sending=True):
                            raise ValidationError('A linked record is suppressed.')
                        record = save_record(form, request.user, request.active_group, source=candidate)
                        candidate.opportunity, candidate.status = record, 'accepted'
                    else:
                        candidate.status = 'rejected'
                    candidate.save(update_fields=['opportunity', 'status'])
                    audit(request.active_group, request.user, 'booking.candidate.' + candidate.status, candidate.pk)
                return redirect('booking:research')
            except (ValidationError, IntegrityError):
                form.add_error(None, 'The proposed record conflicts with an existing or suppressed record. Review its identities and annual edition.')
    return render(request, 'booking/candidate.html', {'nav_active': 'booking', 'candidate': candidate,
        'form': form, 'can_edit': role in EDIT_ROLES and candidate.status == 'pending'})


@login_required
@require_POST
def schedule_source(request, run_id):
    manager(request)
    with transaction.atomic():
        lock_band(request.active_group)
        manager(request)
        run = get_object_or_404(BookingResearchRun, group=request.active_group, pk=run_id, kind='web_discovery', status='succeeded')
        enabled = request.POST.get('enable') == 'yes'
        if enabled and request.POST.get('consent') != 'weekly_same_url':
            return HttpResponseBadRequest('Confirm weekly reads of this exact public URL.')
        BookingResearchSchedule.objects.update_or_create(group=request.active_group, source_run=run, defaults={
            'actor': request.user, 'enabled': enabled, 'interval_days': 7, 'next_due': timezone.now() + timedelta(days=7)})
        audit(request.active_group, request.user, 'booking.research.schedule', run.pk, new='enabled' if enabled else 'disabled')
    return redirect('booking:research_run', run_id=run_id)


@login_required
@require_POST
def purge_mail(request):
    manager(request)
    if request.POST.get('confirm') != 'delete_imported_email':
        return HttpResponseBadRequest('Confirm removal of imported email metadata and excerpts.')
    with transaction.atomic():
        lock_band(request.active_group)
        manager(request)
        runs = BookingResearchRun.objects.filter(group=request.active_group, kind='gmail_import', status__in=['preview', 'queued', 'running'])
        BookingJob.objects.filter(pk__in=runs.values('job_id'), group=request.active_group).update(status='cancelled', lease_until=None)
        runs.update(status='cancelled')
        count = purge_messages(request.active_group)
        audit(request.active_group, request.user, 'booking.gmail.imported_data_removed', new=count)
    return redirect('booking:research')


@login_required
@require_GET
def export_mail(request):
    manager(request)
    purge_messages(request.active_group, expired_only=True)
    rows = list(BookingConversationReference.objects.filter(group=request.active_group, purged=False).values(
        'subject', 'excerpt', 'correspondents', 'sent_at', 'message_id', 'thread_id', 'expires_at'))
    response = HttpResponse(json.dumps(rows, cls=DjangoJSONEncoder, indent=2), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="booking-mail-excerpts.json"'
    response['Cache-Control'] = 'no-store'
    return response
