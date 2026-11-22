from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from player.group_permissions import audit, change_role
from .forms import BookingProfileForm
from .models import BookingMembershipRole, BookingSettings
from .permissions import require_booking


@login_required
@require_http_methods(['GET'])
def overview(request):
    role = require_booking(request.user, request.active_group)
    return render(request, 'booking/overview.html', {
        'nav_active': 'booking', 'is_booking_manager': role == BookingMembershipRole.Role.MANAGER,
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
            require_booking(request.user, group, [BookingMembershipRole.Role.MANAGER])
            form.save()
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
