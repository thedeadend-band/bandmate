from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from player.group_permissions import audit, change_role
from .forms import BookingProfileForm
from .models import BookingMembershipRole, BookingSettings
from .permissions import require_booking


@login_required
@require_http_methods(['GET'])
def overview(request):
    role = require_booking(request.user, request.active_group)
    from .models import BookingOpportunity, BookingFollowUp, BookingCandidate
    from .services.records import SuppressionIndex, EDIT_ROLES
    from django.utils import timezone
    from datetime import timedelta
    index = SuppressionIndex(request.active_group)
    today = timezone.now().date()
    opportunities = [row for row in BookingOpportunity.objects.filter(group=request.active_group, is_archived=False).select_related(
        'organization', 'venue', 'contact', 'series').order_by('-priority', 'name') if not index.blocked(row)]
    visible_ids = [row.pk for row in opportunities]
    return render(request, 'booking/overview.html', {
        'nav_active': 'booking', 'is_booking_manager': role == BookingMembershipRole.Role.MANAGER,
        'can_edit': role in EDIT_ROLES, 'opportunity_count': len(opportunities),
        'deadlines': [row for row in opportunities if row.verified_deadline and today <= row.verified_deadline <= today + timedelta(days=60)],
        'predictions': [row for row in opportunities if row.predicted_window_start and row.predicted_window_end >= today and row.predicted_window_start <= today + timedelta(days=60)],
        'followups': BookingFollowUp.objects.filter(group=request.active_group, opportunity_id__in=visible_ids,
            completed=False, due_date__lte=today + timedelta(days=14)).select_related('opportunity').order_by('due_date')[:30],
    })


@login_required
@require_http_methods(['GET', 'POST'])
def settings_view(request):
    group = request.active_group
    require_booking(request.user, group, [BookingMembershipRole.Role.MANAGER])
    config = BookingSettings.objects.get(group=group)
    form = BookingProfileForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            from .services.records import lock_band
            lock_band(group)
            require_booking(request.user, group, [BookingMembershipRole.Role.MANAGER])
            BookingSettings.objects.filter(pk=config.pk).update(updated_at=timezone.now(), **{
                field: form.cleaned_data[field] for field in form.Meta.fields})
            audit(group, request.user, 'booking.profile.updated')
        return redirect('booking:settings')
    return render(request, 'booking/settings.html', {'form': form, 'nav_active': 'booking'})


@login_required
@require_http_methods(['GET', 'POST'])
def roles(request):
    group = request.active_group
    require_booking(request.user, group, [BookingMembershipRole.Role.MANAGER])
    error = None
    if request.method == 'POST':
        membership_id = request.POST.get('membership', '')
        if not membership_id.isdigit():
            error = 'Choose a band member.'
        else:
            try:
                change_role(BookingMembershipRole, group, request.user,
                            int(membership_id), request.POST.get('role', ''))
                return redirect('booking:roles')
            except ValidationError as exc:
                error = '; '.join(exc.messages)
    members = group.memberships.select_related('user', 'booking_role').order_by('user__username')
    return render(request, 'booking/roles.html', {
        'members': members, 'role_choices': BookingMembershipRole.Role.choices,
        'error': error, 'nav_active': 'booking',
    })
