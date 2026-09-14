from __future__ import annotations

from datetime import date, datetime, time, timedelta
from urllib import request as urlrequest

from django.db import transaction
from django.utils import timezone

from .models import CalendarEvent, CalendarSource


SYNC_PAST_DAYS = 90
SYNC_FUTURE_DAYS = 540


def _fetch_calendar(url: str) -> bytes:
    if url.startswith('webcal://'):
        url = 'https://' + url[len('webcal://'):]
    req = urlrequest.Request(
        url,
        headers={'User-Agent': 'BandMate Calendar Sync/1.0'},
    )
    with urlrequest.urlopen(req, timeout=30) as resp:
        return resp.read()


def _as_aware(value):
    tz = timezone.get_current_timezone()
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value, tz)
        return value.astimezone(tz)
    if isinstance(value, date):
        return timezone.make_aware(datetime.combine(value, time.min), tz)
    return None


def _text(component, field: str) -> str:
    value = component.get(field)
    return str(value or '').strip()


def _event_from_component(source: CalendarSource, component) -> CalendarEvent | None:
    dtstart = component.get('DTSTART')
    if not dtstart:
        return None

    raw_start = dtstart.dt
    raw_end = component.get('DTEND').dt if component.get('DTEND') else None
    all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)

    starts_at = _as_aware(raw_start)
    if not starts_at:
        return None

    if raw_end:
        ends_at = _as_aware(raw_end)
    else:
        ends_at = starts_at + (timedelta(days=1) if all_day else timedelta(hours=1))

    if not ends_at or ends_at <= starts_at:
        ends_at = starts_at + (timedelta(days=1) if all_day else timedelta(hours=1))

    uid = _text(component, 'UID') or f'{source.pk}:{starts_at.isoformat()}:{_text(component, "SUMMARY")}'
    return CalendarEvent(
        source=source,
        uid=uid[:500],
        title=_text(component, 'SUMMARY')[:500] or '(Untitled)',
        starts_at=starts_at,
        ends_at=ends_at,
        is_all_day=all_day,
        location=_text(component, 'LOCATION')[:500],
        description=_text(component, 'DESCRIPTION'),
        event_url=_text(component, 'URL')[:2000],
    )


def sync_calendar_source(source: CalendarSource) -> int:
    from icalendar import Calendar
    import recurring_ical_events

    now = timezone.now()
    window_start = now - timedelta(days=SYNC_PAST_DAYS)
    window_end = now + timedelta(days=SYNC_FUTURE_DAYS)

    payload = _fetch_calendar(source.ics_url)
    calendar = Calendar.from_ical(payload)
    components = recurring_ical_events.of(calendar).between(window_start, window_end)

    events = []
    seen = set()
    for component in components:
        event = _event_from_component(source, component)
        if not event:
            continue
        key = (event.uid, event.starts_at)
        if key in seen:
            continue
        seen.add(key)
        events.append(event)

    with transaction.atomic():
        CalendarEvent.objects.filter(source=source).delete()
        CalendarEvent.objects.bulk_create(events, batch_size=500)
        source.last_synced_at = now
        source.last_error = ''
        source.save(update_fields=['last_synced_at', 'last_error', 'updated_at'])

    return len(events)


def sync_due_calendar_sources(force: bool = False) -> dict[int, str]:
    results = {}
    now = timezone.now()
    for source in CalendarSource.objects.filter(is_enabled=True):
        due_at = None
        if source.last_synced_at:
            due_at = source.last_synced_at + timedelta(
                minutes=source.sync_interval_minutes)
        if not force and due_at and due_at > now:
            continue
        try:
            count = sync_calendar_source(source)
            results[source.pk] = f'synced {count} events'
        except Exception as exc:
            source.last_error = str(exc)
            source.save(update_fields=['last_error', 'updated_at'])
            results[source.pk] = f'failed: {exc}'
    return results
