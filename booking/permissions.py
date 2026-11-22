from django.http import Http404

from player.group_permissions import role_for
from .models import BookingMembershipRole, BookingSettings


def booking_role(user, group):
    if not group or not BookingSettings.objects.filter(group=group, enabled=True).exists():
        return None
    return role_for(BookingMembershipRole, user, group)


def require_booking(user, group, roles=None):
    role = booking_role(user, group)
    if role not in (roles or BookingMembershipRole.Role.values):
        raise Http404
    return role
