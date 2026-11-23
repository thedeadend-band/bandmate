import uuid
import hashlib
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from player.group_permissions import audit, change_role
from .forms import LocalCalendarForm
from .models import BandmateLocalCalendar, ConnectedCalendar, GroupCalendarRole, CalendarWriteOperation
from .permissions import require_calendar_manager, GOOGLE_EVENT_WRITE_SCOPE


@login_required
@require_http_methods(['GET', 'POST'])
def sources(request):
    group = request.active_group
    require_calendar_manager(request.user, group)
    form = LocalCalendarForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            require_calendar_manager(request.user, group)
            source = form.save(commit=False)
            source.group = group
            source.save()
            audit(group, request.user, 'calendar.local.created', source.pk,
                  new=source.write_enabled)
        return redirect('calendar_integrations:sources')
    return render(request, 'calendar_integrations/sources.html', {
        'form': form, 'local_calendars': BandmateLocalCalendar.objects.filter(group=group),
        'google_calendars': ConnectedCalendar.objects.filter(account__group=group),
        'nav_active': 'calendar',
    })


@login_required
@require_POST
def write_mode(request, kind, source_id):
    group = request.active_group
    require_calendar_manager(request.user, group)
    value = request.POST.get('write_enabled')
    if value not in ('true', 'false'):
        return HttpResponseBadRequest('Choose an explicit write mode.')
    with transaction.atomic():
        require_calendar_manager(request.user, group)
        if kind == 'local':
            source = get_object_or_404(BandmateLocalCalendar, pk=source_id, group=group)
        elif kind == 'google':
            source = get_object_or_404(ConnectedCalendar, pk=source_id, account__group=group)
            if value == 'true':
                account = source.account
                if not account.is_connected or not account.refresh_ciphertext or GOOGLE_EVENT_WRITE_SCOPE not in account.granted_scopes:
                    audit(group, request.user, 'calendar.google.write_enable_blocked', source.pk)
                    return HttpResponseBadRequest('Authorize event editing for this Google account first.')
                key = uuid.uuid4()
                operation = CalendarWriteOperation.objects.create(group=group, actor=request.user, key=key,
                    fingerprint=hashlib.sha256(str(key).encode()).hexdigest(), google_calendar=source,
                    action='enable', expected_version=str(source.policy_version))
                return redirect('calendar_events:operation', operation_id=operation.pk)
        else:
            raise Http404
        old = source.write_enabled
        source.write_enabled = value == 'true'
        source.policy_version += 1
        source.save(update_fields=['write_enabled', 'policy_version', 'updated_at'])
        if old != source.write_enabled:
            audit(group, request.user, f'calendar.{kind}.write_mode', source.pk,
                  old, source.write_enabled)
    return redirect('calendar_integrations:sources')


@login_required
@require_http_methods(['GET', 'POST'])
def roles(request):
    group = request.active_group
    require_calendar_manager(request.user, group)
    error = None
    if request.method == 'POST':
        membership_id = request.POST.get('membership', '')
        if not membership_id.isdigit():
            error = 'Choose a band member.'
        else:
            try:
                change_role(GroupCalendarRole, group, request.user,
                            int(membership_id), request.POST.get('role', ''))
                return redirect('calendar_integrations:roles')
            except ValidationError as exc:
                error = '; '.join(exc.messages)
    return render(request, 'calendar_integrations/roles.html', {
        'members': group.memberships.select_related('user', 'calendar_role').order_by('user__username'),
        'role_choices': GroupCalendarRole.Role.choices, 'error': error, 'nav_active': 'calendar',
    })
