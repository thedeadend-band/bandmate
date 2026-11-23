"""Bounded read reconciliation. Remote work never holds a DB transaction."""
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from .google import ProviderError, GoogleCalendarClient
from calendar_integrations.models import CalendarGoogleAccount, ConnectedCalendar, GoogleEvent
from calendar_integrations.permissions import GOOGLE_WRITER_ROLES


def event_values(data, calendar):
    if not isinstance(data, dict):
        raise ProviderError('invalid_event_response')
    for field in ['summary', 'description', 'location', 'htmlLink', 'etag', 'iCalUID', 'recurringEventId']:
        if field in data and not isinstance(data[field], str):
            raise ProviderError('invalid_event_response')
    def parse(value):
        if 'date' in value:
            return datetime.combine(parse_date(value['date']), time.min, ZoneInfo(calendar.timezone))
        result = parse_datetime(value.get('dateTime', ''))
        if not result or timezone.is_naive(result):
            raise ValueError('Missing event timezone')
        return result
    try:
        start, end = parse(data['start']), parse(data['end'])
        if end <= start:
            raise ValueError('Invalid duration')
        zone = data.get('start', {}).get('timeZone') or calendar.timezone
        ZoneInfo(zone)
    except (KeyError, TypeError, ValueError):
        raise ProviderError('invalid_event_dates') from None
    private = data.get('visibility') == 'private' or calendar.access_role == 'freeBusyReader'
    link = data.get('htmlLink', '')
    parsed_link = urlparse(link)
    if private or parsed_link.scheme != 'https' or parsed_link.hostname not in ('calendar.google.com', 'www.google.com'):
        link = ''
    recurring = bool(data.get('recurringEventId') or data.get('recurrence'))
    mutable = (not private and not recurring and data.get('eventType', 'default') == 'default'
        and not data.get('attendees')
        and not data.get('locked') and data.get('status') != 'cancelled')
    return {'title': 'Busy' if private else data.get('summary', '(Untitled)')[:500],
        'starts_at': start, 'ends_at': end, 'timezone': zone, 'is_all_day': 'date' in data['start'],
        'description': '' if private else data.get('description', '')[:20000],
        'location': '' if private else data.get('location', '')[:500],
        'etag': data.get('etag', ''), 'ical_uid': data.get('iCalUID', '')[:1024],
        'recurring_id': data.get('recurringEventId', '')[:1024], 'is_recurring': recurring,
        'is_private': private, 'mutable': bool(mutable), 'html_link': link[:2000], 'is_deleted': False}


def cache_event(calendar, data):
    if not isinstance(data, dict):
        raise ProviderError('invalid_event_response')
    event_id = data.get('id')
    if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
        raise ProviderError('invalid_event_id')
    if data.get('status') == 'cancelled':
        GoogleEvent.objects.filter(calendar=calendar, external_id=event_id).update(is_deleted=True)
        return
    GoogleEvent.objects.update_or_create(calendar=calendar, external_id=event_id, defaults=event_values(data, calendar))


def sync_account(account, client=None):
    client = client or GoogleCalendarClient(account)
    entries = client.list_calendars()
    safe = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('id'), str) or any(
                not isinstance(entry.get(key, ''), str) for key in ['summary', 'timeZone', 'accessRole']):
            raise ProviderError('invalid_calendar_list')
        if not entry.get('deleted'):
            safe.append({key: entry.get(key, '') for key in ['id', 'summary', 'timeZone', 'accessRole']})
    CalendarGoogleAccount.objects.filter(pk=account.pk, is_connected=True, generation=account.generation).update(calendar_list=safe, last_error_code='')
    return client


def sync_calendar(calendar, client=None):
    client = client or GoogleCalendarClient(calendar.account)
    generation = calendar.account.generation
    acl = client.calendar(calendar.external_calendar_id)
    role = acl.get('accessRole', '')
    if role not in {'owner', 'writer', 'writerWithoutPrivateAccess', 'reader', 'freeBusyReader'} or acl.get('deleted'):
        raise ProviderError('calendar_access_removed', 403)
    role_changed = role != calendar.access_role
    calendar.access_role = role
    if role == 'freeBusyReader':
        # A failed incremental/full fetch must never leave previously readable
        # details visible after a provider ACL downgrade.
        GoogleEvent.objects.filter(calendar=calendar).update(title='Busy', description='', location='',
            html_link='', is_private=True, mutable=False)
    ConnectedCalendar.objects.filter(pk=calendar.pk).update(access_role=role,
        **({'write_enabled': False} if role not in GOOGLE_WRITER_ROLES else {}))
    full = role_changed or not calendar.sync_token or not calendar.last_full_sync_at or calendar.last_full_sync_at < timezone.now() - timedelta(days=7)
    token = '' if full else calendar.sync_token
    window = ((timezone.now() - timedelta(days=90)).isoformat(), (timezone.now() + timedelta(days=540)).isoformat())
    for reset in range(2):
        page, rows, final_token = '', [], ''
        try:
            for _ in range(settings.GOOGLE_CALENDAR_MAX_PAGES):
                response = client.events(calendar.external_calendar_id, sync_token=token, page_token=page, window=window)
                items = response.get('items', [])
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ProviderError('invalid_event_response')
                rows.extend(items)
                page = response.get('nextPageToken', '')
                if not page:
                    final_token = response.get('nextSyncToken', '')
                    break
            if not final_token or page:
                raise ProviderError('sync_page_limit_or_missing_cursor')
            break
        except ProviderError as exc:
            if exc.status == 410 and token and reset == 0:
                token, full = '', True
                continue
            raise
    # Validate every row before changing the cache/cursor. Preserve cache on partial failure.
    for row in rows:
        if row.get('status') != 'cancelled':
            event_values(row, calendar)
    with transaction.atomic():
        current = ConnectedCalendar.objects.filter(pk=calendar.pk, is_enabled=True,
            account__is_connected=True, account__generation=generation).first()
        if not current:
            return
        if full:
            GoogleEvent.objects.filter(calendar=calendar).update(is_deleted=True)
        for row in rows:
            cache_event(calendar, row)
        updates = {'sync_token': final_token, 'last_synced_at': timezone.now(), 'last_error_code': ''}
        if full:
            updates['last_full_sync_at'] = timezone.now()
        ConnectedCalendar.objects.filter(pk=calendar.pk).update(**updates)
