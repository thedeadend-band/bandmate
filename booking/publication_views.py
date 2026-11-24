import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from player.group_permissions import audit
from calendar_integrations.forms import EventForm
from calendar_integrations.models import BandmateLocalCalendar, ConnectedCalendar, LocalEvent, GoogleEvent
from calendar_integrations.permissions import source_write_allowed, require_source_write
from calendar_integrations.services.mutations import submit
from .models import BookingOpportunity, BookingCalendarPublication
from .permissions import require_booking
from .services.records import EDIT_ROLES, lock_band, SuppressionIndex


def event_for(publication):
    if publication.local_calendar_id:
        return LocalEvent.objects.filter(calendar=publication.local_calendar, pk=publication.event_id, is_deleted=False).first()
    return GoogleEvent.objects.filter(calendar=publication.google_calendar, external_id=publication.event_id, is_deleted=False).first()


@login_required
@require_http_methods(['GET', 'POST'])
def publication(request, opportunity_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    group = request.active_group
    opportunity = get_object_or_404(BookingOpportunity, group=group, pk=opportunity_id)
    targets = [('local', c) for c in BandmateLocalCalendar.objects.filter(group=group, is_enabled=True)]
    targets += [('google', c) for c in ConnectedCalendar.objects.filter(account__group=group, is_enabled=True)]
    targets = [(kind, c) for kind, c in targets if source_write_allowed(request.user, group, c)]
    selected = request.POST.get('target', '') if request.method == 'POST' else request.GET.get('target', '')
    if not selected and len(targets) == 1:
        selected = f'{targets[0][0]}:{targets[0][1].pk}'
    calendar = next((c for kind, c in targets if f'{kind}:{c.pk}' == selected), None)
    link = BookingCalendarPublication.objects.filter(group=group, opportunity=opportunity, target=selected).select_related('operation').first()
    event = event_for(link) if link else None
    initial = {'operation_key': uuid.uuid4(), 'title': group.name + ' — ' + opportunity.name,
        'timezone': calendar.timezone if calendar else 'UTC', 'location': opportunity.venue.address if opportunity.venue_id else opportunity.city,
        'is_all_day': True, 'start_date': opportunity.event_date,
        'end_date': opportunity.event_date + timedelta(days=1) if opportunity.event_date else None}
    if event:
        start, end = event.starts_at.astimezone(ZoneInfo(event.timezone)), event.ends_at.astimezone(ZoneInfo(event.timezone))
        initial.update({key: getattr(event, key) for key in ('title', 'timezone', 'location', 'description', 'is_all_day')})
        initial.update(version=event.version if link.local_calendar_id else event.etag, start_date=start.date(), end_date=end.date(),
            start=start.replace(tzinfo=None).isoformat(), end=end.replace(tzinfo=None).isoformat())
    form = EventForm(request.POST if request.method == 'POST' else None, initial=initial)
    error = ''
    if request.method == 'POST':
        action = request.POST.get('action')
        try:
            if not calendar:
                raise Http404
            if request.POST.get('confirm') != 'reviewed_calendar_action':
                raise ValidationError('Confirm this exact calendar action.')
            with transaction.atomic():
                lock_band(group)
                require_booking(request.user, group, EDIT_ROLES)
                require_source_write(request.user, group, calendar)
                opportunity.refresh_from_db()
                link = BookingCalendarPublication.objects.filter(group=group, opportunity=opportunity, target=selected).select_related('operation').first()
                if link and str(link.revision) != request.POST.get('publication_revision'):
                    raise ValidationError('The publication changed; reload and review it.')
                if link and link.operation.status in ('pending', 'processing', 'ambiguous'):
                    raise ValidationError('Resolve the pending or uncertain calendar operation before another action.')
                if action in ('create', 'update', 'relink') and (opportunity.is_archived or opportunity.stage != 'confirmed' or SuppressionIndex(group).blocked(opportunity)):
                    raise ValidationError('Only an active, unsuppressed confirmed gig can be published or updated.')
                if action in ('unlink', 'relink'):
                    if not link:
                        raise ValidationError('No publication link exists.')
                    if action == 'relink' and not event_for(link):
                        raise ValidationError('The linked event is no longer available. Review its calendar status.')
                    link.linked = action == 'relink'
                    link.revision += 1
                    link.save(update_fields=['linked', 'revision'])
                else:
                    key = uuid.UUID(request.POST.get('operation_key', ''))
                    event = event_for(link) if link else None
                    if action == 'create':
                        if link and not (link.operation.action == 'delete' and link.operation.status == 'succeeded'):
                            raise ValidationError('This gig already has a publication for that calendar. Update or relink it instead.')
                    elif action in ('update', 'delete'):
                        if not link or not link.linked or not event:
                            raise ValidationError('Choose an existing linked event.')
                    else:
                        raise ValidationError('Choose a calendar action.')
                    if action != 'delete' and not form.is_valid():
                        raise ValidationError('Correct the event fields below.')
                    operation = submit(request.user, group, calendar, action, key,
                        form.event_payload() if action != 'delete' else None, event if action != 'create' else None,
                        request.POST.get('version', ''))
                    if link:
                        link.operation, link.event_id, link.linked = operation, operation.event_id, True
                        link.revision += 1
                        link.save()
                    else:
                        BookingCalendarPublication.objects.create(group=group, opportunity=opportunity, target=selected,
                            local_calendar=calendar if selected.startswith('local:') else None,
                            google_calendar=calendar if selected.startswith('google:') else None,
                            operation=operation, event_id=operation.event_id)
                audit(group, request.user, 'booking.calendar.' + action, opportunity.identity, new=selected)
            return redirect(request.path + '?target=' + selected)
        except (ValidationError, ValueError) as exc:
            error = ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Invalid operation identifier.'
    return render(request, 'booking/publication.html', {'nav_active': 'booking', 'opportunity': opportunity,
        'targets': [{'value': f'{kind}:{c.pk}', 'name': c.name} for kind, c in targets], 'selected': selected,
        'form': form, 'link': link, 'error': error,
        'publications': BookingCalendarPublication.objects.filter(group=group, opportunity=opportunity).select_related('operation')},
        status=400 if error else 200)
