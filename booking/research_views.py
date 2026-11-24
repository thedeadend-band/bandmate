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
from .research_forms import GmailImportForm, DiscoveryForm, SearchForm, GeocodeForm
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
        if not index.blocks_proposal(c.proposed, title=c.title)]
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
    if kind not in ('gmail_import', 'web_discovery', 'web_search', 'geocode'):
        raise Http404
    form_class = {'gmail_import': GmailImportForm, 'web_discovery': DiscoveryForm,
        'web_search': SearchForm, 'geocode': GeocodeForm}[kind]
    from .models import BookingSettings
    config = BookingSettings.objects.get(group=request.active_group)
    initial = {'end_date': timezone.now().date()} if kind == 'gmail_import' else {}
    if kind == 'web_search':
        initial['query'] = ' '.join(filter(None, [config.search_center, config.genres, config.target_years, 'live music applications']))[:500]
    if kind == 'geocode':
        initial['query'] = config.search_center
    form = form_class(request.POST if request.method == 'POST' else None,
        initial=initial, **({'group': request.active_group} if kind == 'geocode' else {}))
    if request.method == 'POST' and form.is_valid():
        try:
            if kind == 'gmail_import':
                scope = form.scope()
            elif kind == 'web_discovery':
                scope = {'url': form.cleaned_data['url'], 'ai_processing': False}
            elif kind == 'web_search':
                scope = dict(form.cleaned_data, ai_processing=False)
            else:
                venue = form.cleaned_data.get('venue')
                if venue and SuppressionIndex(request.active_group).blocked(venue, sending=True):
                    raise ProviderError('location_suppressed')
                scope = {'query': form.cleaned_data['query'], 'venue_id': venue.pk if venue else None,
                    'venue_label': venue.name if venue else '',
                    'venue_revision': venue.revision if venue else None, 'center_revision': config.updated_at.isoformat(), 'ai_processing': False}
            run = new_preview(request.active_group, request.user, kind, scope)
            return redirect('booking:research_run', run_id=run.pk)
        except ProviderError as exc:
            form.add_error(None, 'Research is unavailable: ' + exc.code)
    return render(request, 'booking/research_form.html', {'nav_active': 'booking', 'form': form, 'kind': kind})


@login_required
@require_GET
def run_detail(request, run_id):
    role = require_booking(request.user, request.active_group)
    run = get_object_or_404(BookingResearchRun, pk=run_id, group=request.active_group)
    evidence = None
    if run.kind == 'ai_draft' and run.status == 'preview':
        from .services.draft_ai import draft_scope
        from types import SimpleNamespace
        try:
            _, value = draft_scope(request.active_group, run.scope['draft_id'])
            evidence = SimpleNamespace(excerpt=json.dumps(value))
        except (ProviderError, ValidationError):
            pass
    if run.kind == 'ai_extract' and run.status == 'preview':
        from .services.extraction import candidate_for_ai
        try:
            evidence = candidate_for_ai(request.active_group, run.scope['candidate_id'])
        except ProviderError:
            pass
    return render(request, 'booking/research_run.html', {'nav_active': 'booking', 'run': run,
        'is_manager': role == BookingMembershipRole.Role.MANAGER,
        'can_confirm': role == BookingMembershipRole.Role.MANAGER and run.actor_id == request.user.pk and run.status == 'preview',
        'ai_evidence': evidence,
        'schedule': BookingResearchSchedule.objects.filter(group=request.active_group, source_run=run).first()})


@login_required
@require_POST
def confirm(request, run_id):
    manager(request)
    run = get_object_or_404(BookingResearchRun, pk=run_id, group=request.active_group, actor=request.user)
    if request.POST.get('consent') != ('analyze_evidence' if run.kind in ('ai_extract', 'ai_draft') else 'scoped_read'):
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
    if candidate.source_type == 'geocode':
        from .models import BookingVenue
        target = BookingVenue.objects.filter(group=request.active_group, pk=candidate.run.scope.get('venue_id')).first()
        return render(request, 'booking/geocode_candidate.html', {'nav_active': 'booking', 'candidate': candidate,
            'target_venue': target,
            'can_apply': role == BookingMembershipRole.Role.MANAGER and candidate.status == 'pending'})
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
                    if index.blocks_proposal(proposed, title=candidate.title):
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
        'form': form, 'can_edit': role in EDIT_ROLES and candidate.status == 'pending',
        'is_manager': role == BookingMembershipRole.Role.MANAGER})


@login_required
@require_POST
def extraction_preview(request, candidate_id):
    manager(request)
    get_object_or_404(BookingCandidate, group=request.active_group, pk=candidate_id, status='pending')
    try:
        run = new_preview(request.active_group, request.user, 'ai_extract', {'candidate_id': candidate_id})
    except ProviderError as exc:
        return HttpResponseBadRequest('AI extraction is unavailable: ' + exc.code)
    return redirect('booking:research_run', run_id=run.pk)


@login_required
@require_POST
def source_preview(request, candidate_id):
    manager(request)
    candidate = get_object_or_404(BookingCandidate, group=request.active_group, pk=candidate_id, source_type__in=['url', 'search'])
    form = DiscoveryForm({'url': candidate.source_ref})
    if not form.is_valid() or SuppressionIndex(request.active_group).blocks_source(url=candidate.source_ref, name=candidate.title):
        return HttpResponseBadRequest('This source is unavailable or suppressed.')
    run = new_preview(request.active_group, request.user, 'web_discovery', {'url': form.cleaned_data['url'], 'ai_processing': False})
    return redirect('booking:research_run', run_id=run.pk)


@login_required
@require_POST
def save_artist_gig(request, candidate_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    if request.POST.get('confirm') != 'public_gig_reviewed':
        return HttpResponseBadRequest('Review the public artist, venue, date and source before saving.')
    from .models import BookingArtistReference, BookingArtistGig, BookingVenue, BookingEvidence
    from .services.records import digest
    from datetime import date
    try:
        with transaction.atomic():
            lock_band(request.active_group)
            require_booking(request.user, request.active_group, EDIT_ROLES)
            candidate = get_object_or_404(BookingCandidate, group=request.active_group, pk=candidate_id, status='pending', source_type='url')
            data = candidate.proposed
            if not data.get('artist_name') or not data.get('event_date'):
                return HttpResponseBadRequest('This source has no structured public artist and event date.')
            index = SuppressionIndex(request.active_group)
            if index.blocks_source(url=candidate.source_ref, name=data['artist_name'], city=data.get('city', '')):
                return HttpResponseBadRequest('This artist or source is suppressed.')
            # Ambiguous existing names require explicit manual resolution.
            def entity(model, name):
                rows = model.objects.filter(group=request.active_group, name__iexact=name, city=data.get('city', ''))
                if rows.count() > 1:
                    raise ValidationError('Ambiguous identity')
                record = rows.first() or model.objects.create(group=request.active_group, name=name,
                    city=data.get('city', ''))
                if record.is_archived or index.blocked(record, sending=True):
                    raise ValidationError('Suppressed identity')
                remember_aliases(record)
                return record
            artist = entity(BookingArtistReference, data['artist_name'])
            venue = entity(BookingVenue, data['venue_name']) if data.get('venue_name') else None
            gig, _ = BookingArtistGig.objects.get_or_create(group=request.active_group, artist=artist,
                name=data['name'], event_date=date.fromisoformat(data['event_date']), defaults={
                    'venue': venue, 'city': data.get('city', ''), 'website': candidate.source_ref,
                    'source_excerpt': candidate.excerpt})
            if gig.is_archived or index.blocked(gig, sending=True):
                raise ValidationError('Suppressed gig')
            remember_aliases(gig)
            BookingEvidence.objects.create(group=request.active_group, record_identity=gig.identity,
                field_name='public_artist_gig', source_type='url', source_ref=candidate.source_ref,
                excerpt=candidate.excerpt, content_hash=digest(candidate.excerpt), event_year=gig.event_date.year)
            candidate.status = 'accepted'
            candidate.save(update_fields=['status'])
            audit(request.active_group, request.user, 'booking.artist_gig.reviewed', gig.identity)
    except (ValidationError, IntegrityError, ValueError):
        return HttpResponseBadRequest('This public gig conflicts with an existing or suppressed identity. Review the records first.')
    return redirect('booking:record_detail', kind='artist_gigs', record_id=gig.pk)


@login_required
@require_POST
def apply_location(request, candidate_id):
    manager(request)
    if request.POST.get('confirm') != 'reviewed_location':
        return HttpResponseBadRequest('Review and confirm the selected place before saving its coordinates.')
    from .models import BookingSettings, BookingVenue, BookingEvidence
    from .services.records import digest
    with transaction.atomic():
        lock_band(request.active_group)
        manager(request)
        candidate = get_object_or_404(BookingCandidate, group=request.active_group, pk=candidate_id, source_type='geocode', status='pending')
        proposed, scope = candidate.proposed, candidate.run.scope
        note = 'OpenCage: ' + proposed['name'] + '; precision ' + str(proposed['precision']) + '/10; reviewed ' + timezone.now().date().isoformat()
        if scope.get('venue_id'):
            venue = get_object_or_404(BookingVenue, group=request.active_group, pk=scope['venue_id'], is_archived=False)
            if venue.revision != scope['venue_revision'] or SuppressionIndex(request.active_group).blocked(venue, sending=True):
                return HttpResponseBadRequest('The venue changed or is suppressed. Request a fresh location review.')
            venue.latitude, venue.longitude = proposed['latitude'], proposed['longitude']
            venue.location_evidence, venue.revision = note, venue.revision + 1
            venue.full_clean()
            venue.save()
            BookingEvidence.objects.create(group=request.active_group, record_identity=venue.identity,
                field_name='coordinates', source_type='geocode', source_ref=candidate.source_ref,
                excerpt=candidate.excerpt, content_hash=digest(candidate.excerpt))
        else:
            config = BookingSettings.objects.get(group=request.active_group)
            if config.updated_at.isoformat() != scope['center_revision']:
                return HttpResponseBadRequest('Band preferences changed. Request a fresh location review.')
            config.search_center = proposed['name']
            config.search_latitude, config.search_longitude = proposed['latitude'], proposed['longitude']
            config.save(update_fields=['search_center', 'search_latitude', 'search_longitude', 'updated_at'])
        candidate.status = 'accepted'
        candidate.save(update_fields=['status'])
        audit(request.active_group, request.user, 'booking.location.reviewed', candidate.pk)
    return redirect('booking:research')


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
            return HttpResponseBadRequest('Confirm recurring reads of this exact public URL.')
        interval = request.POST.get('interval_days', '7')
        if interval not in ('1', '7', '30'):
            return HttpResponseBadRequest('Choose daily, weekly or every 30 days.')
        BookingResearchSchedule.objects.update_or_create(group=request.active_group, source_run=run, defaults={
            'actor': request.user, 'enabled': enabled, 'interval_days': int(interval), 'next_due': timezone.now() + timedelta(days=int(interval))})
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
        BookingJob.objects.filter(group=request.active_group, kind='gmail_replies',
            status__in=['pending', 'processing']).update(status='cancelled', lease_until=None)
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
