from zoneinfo import ZoneInfo
from django.urls import reverse
from player.group_permissions import member_of
from .models import BandmateLocalCalendar, ConnectedCalendar, LocalEvent, GoogleEvent
from .permissions import source_write_allowed


def calendar_data(user, group, start, end):
    if not member_of(user, group):
        return [], [], False
    sources, events, can_create = [], [], False
    for kind, calendars, model in [
        ('local', BandmateLocalCalendar.objects.filter(group=group, is_enabled=True), LocalEvent),
        ('google', ConnectedCalendar.objects.filter(account__group=group, account__is_connected=True, is_enabled=True), GoogleEvent),
    ]:
        for calendar in calendars:
            writable = source_write_allowed(user, group, calendar)
            can_create = can_create or writable
            source_id = f'{kind}:{calendar.pk}'
            sources.append({'id': source_id, 'name': calendar.name, 'color': calendar.color,
                'mode': 'Editable' if writable else 'View only', 'kind': kind,
                'last_synced_at': calendar.last_synced_at.isoformat() if kind == 'google' and calendar.last_synced_at else '',
                'last_error': calendar.last_error_code if kind == 'google' else ''})
            for event in model.objects.filter(calendar=calendar, is_deleted=False, starts_at__lte=end, ends_at__gte=start):
                zone = ZoneInfo(event.timezone)
                events.append({'id': f'{kind}:{event.pk}', 'source_id': source_id, 'source_name': calendar.name,
                    'color': calendar.color, 'title': event.title, 'all_day': event.is_all_day,
                    'start': event.starts_at.astimezone(zone).date().isoformat() if event.is_all_day else event.starts_at.isoformat(),
                    'end': event.ends_at.astimezone(zone).date().isoformat() if event.is_all_day else event.ends_at.isoformat(),
                    'location': event.location, 'description': event.description,
                    'url': reverse('calendar_events:detail', args=[kind, event.pk])})
    return sources, events, can_create
