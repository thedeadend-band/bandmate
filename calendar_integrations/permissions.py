"""Source policy foundation. Provider event rights must also pass in Phase 1."""
from django.http import Http404

from player.group_permissions import require_role, role_for
from .models import BandmateLocalCalendar, ConnectedCalendar, GroupCalendarRole

GOOGLE_EVENT_WRITE_SCOPE = 'https://www.googleapis.com/auth/calendar.events'
GOOGLE_READ_SCOPES = (
    'https://www.googleapis.com/auth/calendar.events.readonly',
    'https://www.googleapis.com/auth/calendar.calendarlist.readonly',
)
GOOGLE_WRITER_ROLES = {'owner', 'writer', 'writerWithoutPrivateAccess'}


def require_calendar_manager(user, group):
    require_role(GroupCalendarRole, user, group, [GroupCalendarRole.Role.MANAGER])


def source_write_allowed(user, group, source, *, private=False):
    """Fresh local policy gate, NOT a substitute for live provider authorization.

    Re-fetch source/account on every check so stale model/form instances cannot
    retain write access after a policy change. ICS and unknown types fail closed.
    """
    if role_for(GroupCalendarRole, user, group) not in (
            GroupCalendarRole.Role.MANAGER, GroupCalendarRole.Role.EDITOR):
        return False
    if isinstance(source, BandmateLocalCalendar):
        return BandmateLocalCalendar.objects.filter(
            pk=source.pk, group=group, is_enabled=True, write_enabled=True).exists()
    if isinstance(source, ConnectedCalendar):
        current = ConnectedCalendar.objects.select_related('account').filter(
            pk=source.pk, account__group=group, account__is_connected=True,
            is_enabled=True, write_enabled=True).first()
        return bool(current and GOOGLE_EVENT_WRITE_SCOPE in current.account.granted_scopes
                    and current.access_role in GOOGLE_WRITER_ROLES
                    and not (private and current.access_role == 'writerWithoutPrivateAccess'))
    return False


def require_source_write(user, group, source, *, private=False):
    if not source_write_allowed(user, group, source, private=private):
        raise Http404
