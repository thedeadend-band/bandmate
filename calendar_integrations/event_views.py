import uuid
from functools import wraps
from zoneinfo import ZoneInfo
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from player.group_permissions import member_of
from player.models import CalendarEvent
from .models import BandmateLocalCalendar, ConnectedCalendar, LocalEvent, GoogleEvent, CalendarWriteOperation, CalendarMutationAudit
from .forms import EventForm
from .permissions import source_write_allowed, require_source_write
from .services.mutations import submit, EventConflict


def audit_denials(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Http404:
            if request.method == 'POST' and request.active_group:
                CalendarMutationAudit.objects.create(group=request.active_group, actor=request.user,
                    action=view.__name__, outcome='denied')
            raise
    return wrapped


def require_member(request):
    if not member_of(request.user, request.active_group):
        raise Http404


def source_for(request, kind, pk):
    require_member(request)
    if kind == 'local':
        return get_object_or_404(BandmateLocalCalendar, pk=pk, group=request.active_group, is_enabled=True)
    if kind == 'google':
        return get_object_or_404(ConnectedCalendar, pk=pk, account__group=request.active_group, account__is_connected=True, is_enabled=True)
    raise Http404


def event_for(request, kind, pk):
    require_member(request)
    if kind == 'local':
        return get_object_or_404(LocalEvent.objects.select_related('calendar'), pk=pk, calendar__group=request.active_group, calendar__is_enabled=True, is_deleted=False)
    if kind == 'google':
        return get_object_or_404(GoogleEvent.objects.select_related('calendar__account'), pk=pk,
            calendar__account__group=request.active_group, calendar__account__is_connected=True, calendar__is_enabled=True, is_deleted=False)
    if kind == 'ics':
        return get_object_or_404(CalendarEvent, pk=pk, source__group=request.active_group, source__is_enabled=True)
    raise Http404


@login_required
@require_http_methods(['GET'])
def detail(request, kind, event_id):
    event = event_for(request, kind, event_id)
    editable = kind != 'ics' and source_write_allowed(request.user, request.active_group, event.calendar)
    if kind == 'google':
        editable = editable and event.mutable
    return render(request, 'calendar_integrations/event_detail.html', {
        'event': event, 'kind': kind, 'editable': editable, 'nav_active': 'calendar',
        'source_name': event.source.name if kind == 'ics' else event.calendar.name})


@login_required
@require_http_methods(['GET', 'POST'])
@audit_denials
def create(request):
    require_member(request)
    calendars = [('local', c) for c in BandmateLocalCalendar.objects.filter(group=request.active_group, is_enabled=True)]
    calendars += [('google', c) for c in ConnectedCalendar.objects.filter(account__group=request.active_group, account__is_connected=True, is_enabled=True)]
    calendars = [(kind, c) for kind, c in calendars if source_write_allowed(request.user, request.active_group, c)]
    selected = request.POST.get('target', '') if request.method == 'POST' else request.GET.get('target', '')
    if not selected and len(calendars) == 1:
        selected = f'{calendars[0][0]}:{calendars[0][1].pk}'
    source = next((c for kind, c in calendars if f'{kind}:{c.pk}' == selected), None)
    form = EventForm(request.POST if request.method == 'POST' else None,
                     initial={'operation_key': uuid.uuid4(), 'timezone': source.timezone if source else 'UTC'})
    if request.method == 'POST':
        if source is None:
            raise Http404
        if form.is_valid():
            try:
                operation = submit(request.user, request.active_group, source, 'create',
                    form.cleaned_data['operation_key'], form.event_payload())
                return redirect('calendar_events:operation', operation_id=operation.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    return render(request, 'calendar_integrations/event_form.html', {'form': form, 'calendars': calendars,
        'targets': [{'value': f'{kind}:{c.pk}', 'name': c.name, 'timezone': c.timezone} for kind, c in calendars], 'selected': selected, 'creating': True, 'nav_active': 'calendar'})


@login_required
@require_http_methods(['GET', 'POST'])
@audit_denials
def edit(request, kind, event_id):
    event = event_for(request, kind, event_id)
    if kind == 'ics':
        raise Http404
    require_source_write(request.user, request.active_group, event.calendar)
    if kind == 'google' and not event.mutable:
        raise Http404
    zone = ZoneInfo(event.timezone)
    start, end = event.starts_at.astimezone(zone), event.ends_at.astimezone(zone)
    initial = {field: getattr(event, field) for field in ['title', 'location', 'description', 'timezone', 'is_all_day']}
    initial.update(operation_key=uuid.uuid4(), version=event.version if kind == 'local' else event.etag,
        start=start.replace(tzinfo=None).isoformat(timespec='seconds'),
        end=end.replace(tzinfo=None).isoformat(timespec='seconds'), start_date=start.date(), end_date=end.date())
    form = EventForm(request.POST if request.method == 'POST' else None, initial=initial)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            operation = submit(request.user, request.active_group, event.calendar, 'update',
                form.cleaned_data['operation_key'], form.event_payload(), event, form.cleaned_data['version'])
            return redirect('calendar_events:operation', operation_id=operation.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409 if isinstance(exc, EventConflict) else 400
    return render(request, 'calendar_integrations/event_form.html', {'form': form, 'event': event,
        'kind': kind, 'nav_active': 'calendar'}, status=status)


@login_required
@require_http_methods(['GET', 'POST'])
@audit_denials
def delete(request, kind, event_id):
    event = event_for(request, kind, event_id)
    if kind == 'ics':
        raise Http404
    require_source_write(request.user, request.active_group, event.calendar)
    if kind == 'google' and not event.mutable:
        raise Http404
    error, status = '', 200
    if request.method == 'POST':
        try:
            if request.POST.get('confirm') != 'yes':
                raise ValidationError('Confirm this event deletion.')
            key = uuid.UUID(request.POST.get('operation_key', ''))
            operation = submit(request.user, request.active_group, event.calendar, 'delete', key,
                               event=event, version=request.POST.get('version', ''))
            return redirect('calendar_events:operation', operation_id=operation.pk)
        except (ValidationError, ValueError) as exc:
            error, status = str(exc), 409 if isinstance(exc, EventConflict) else 400
    return render(request, 'calendar_integrations/event_delete.html', {'event': event, 'kind': kind,
        'operation_key': uuid.uuid4(), 'version': event.version if kind == 'local' else event.etag,
        'error': error, 'nav_active': 'calendar'}, status=status)


@login_required
@require_http_methods(['GET'])
def operation(request, operation_id):
    require_member(request)
    op = get_object_or_404(CalendarWriteOperation, pk=operation_id, group=request.active_group)
    return render(request, 'calendar_integrations/operation.html', {'operation': op, 'nav_active': 'calendar'})
