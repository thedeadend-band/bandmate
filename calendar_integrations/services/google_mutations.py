"""Apply only persisted human intent, with fresh local and provider gates."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from bandmate.credential_vault import VaultUnavailable
from calendar_integrations.models import CalendarWriteOperation, ConnectedCalendar
from calendar_integrations.permissions import require_source_write, require_calendar_manager, GOOGLE_EVENT_WRITE_SCOPE, GOOGLE_WRITER_ROLES
from .google import GoogleCalendarClient, ProviderError
from .sync import event_values, cache_event
from .mutations import mutation_audit


def google_payload(op):
    payload = op.payload
    if op.action == 'delete':
        return {}
    result = {'summary': payload['title'], 'location': payload['location'], 'description': payload['description']}
    for target, source in [('start', 'starts_at'), ('end', 'ends_at')]:
        value = parse_datetime(payload[source]).astimezone(ZoneInfo(payload['timezone']))
        result[target] = {'date': value.date().isoformat()} if payload['is_all_day'] else {'dateTime': value.isoformat(), 'timeZone': payload['timezone']}
    result['extendedProperties'] = {'private': {'bandmateOperation': str(op.key)}}
    if op.action == 'create':
        result['id'] = op.event_id
    return result


def finish(op, status, error=''):
    with transaction.atomic():
        CalendarWriteOperation.objects.filter(pk=op.pk).update(status=status, error_code=error, updated_at=timezone.now())
        mutation_audit(op, status)


def process_operation(op, client=None):
    if op.status not in ('pending', 'processing'):
        return
    # `processing` at command startup means a prior process may have written.
    recovering = op.status == 'processing'
    attempting_write = False
    calendar = ConnectedCalendar.objects.select_related('account').get(pk=op.google_calendar_id)
    client = client or GoogleCalendarClient(calendar.account)
    try:
        if not op.actor:
            raise Http404
        if op.action == 'enable':
            require_calendar_manager(op.actor, op.group)
            if calendar.account.group_id != op.group_id or not calendar.is_enabled or GOOGLE_EVENT_WRITE_SCOPE not in calendar.account.granted_scopes:
                raise Http404
        else:
            require_source_write(op.actor, op.group, calendar)
        acl = client.calendar(calendar.external_calendar_id)
        role = acl.get('accessRole', '')
        ConnectedCalendar.objects.filter(pk=calendar.pk).update(access_role=role)
        if role not in GOOGLE_WRITER_ROLES or acl.get('deleted'):
            raise ProviderError('calendar_write_denied', 403)
        if op.action == 'enable':
            with transaction.atomic():
                require_calendar_manager(op.actor, op.group)
                changed = ConnectedCalendar.objects.filter(pk=calendar.pk, policy_version=op.expected_version,
                    is_enabled=True, account__is_connected=True, account__generation=calendar.account.generation).update(write_enabled=True)
                finish(op, 'succeeded' if changed else 'failed', '' if changed else 'policy_changed')
            return
        payload = google_payload(op)
        current = None
        try:
            current = client.event(calendar.external_calendar_id, op.event_id)
        except ProviderError as exc:
            if exc.status not in (404, 410):
                raise
        if current and current.get('status') == 'cancelled':
            current = None
        marker = (current or {}).get('extendedProperties', {}).get('private', {}).get('bandmateOperation')
        if current and marker == str(op.key):
            # A prior call succeeded but confirmation/cache persistence was interrupted.
            if op.action != 'delete':
                cache_event(calendar, current)
                finish(op, 'succeeded')
                return
        if op.action == 'create' and current:
            finish(op, 'conflict', 'event_id_already_exists')
            return
        if op.action != 'create':
            if not current:
                if op.action == 'delete':
                    calendar.events.filter(external_id=op.event_id).update(is_deleted=True)
                    finish(op, 'succeeded')
                else:
                    finish(op, 'conflict', 'event_removed')
                return
            calendar.access_role = role
            if not event_values(current, calendar)['mutable']:
                raise ProviderError('event_read_only', 403)
            if current.get('etag') != op.expected_version:
                cache_event(calendar, current)
                finish(op, 'conflict', 'google_event_changed')
                return
            if op.action == 'update':
                payload['extendedProperties']['private'] = {
                    **current.get('extendedProperties', {}).get('private', {}),
                    'bandmateOperation': str(op.key)}
        if recovering and op.action != 'create':
            # Do not automatically repeat a patch/delete whose prior outcome is uncertain.
            finish(op, 'ambiguous', 'confirmation_missing')
            return
        # Re-read permissions immediately before the write, not only at enqueue time.
        require_source_write(op.actor, op.group, calendar)
        CalendarWriteOperation.objects.filter(pk=op.pk).update(status='processing', attempts=op.attempts + 1, updated_at=timezone.now())
        attempting_write = True
        response = client.write(calendar.external_calendar_id, op.event_id, op.action, payload,
                                etag=op.expected_version if op.action != 'create' else '')
        with transaction.atomic():
            if op.action == 'delete':
                calendar.events.filter(external_id=op.event_id).update(is_deleted=True)
            else:
                cache_event(calendar, response)
            finish(op, 'succeeded')
    except (Http404, VaultUnavailable):
        finish(op, 'failed', 'authorization_or_vault_unavailable')
    except ProviderError as exc:
        if exc.status == 412:
            finish(op, 'conflict', 'google_event_changed')
        elif exc.status == 429 and op.attempts < 4:
            CalendarWriteOperation.objects.filter(pk=op.pk).update(status='pending', error_code='rate_limited',
                attempts=op.attempts + 1, retry_at=timezone.now() + timedelta(minutes=2 ** (op.attempts + 1)))
        elif exc.status == 0 or exc.status >= 500 or exc.status == 409:
            if not attempting_write and not recovering:
                if op.attempts < 4:
                    CalendarWriteOperation.objects.filter(pk=op.pk).update(status='pending', error_code='provider_unavailable',
                        attempts=op.attempts + 1, retry_at=timezone.now() + timedelta(minutes=2 ** (op.attempts + 1)))
                else:
                    finish(op, 'failed', 'provider_unavailable')
                return
            # Retain durable processing state; next timer reconciles before any retry.
            CalendarWriteOperation.objects.filter(pk=op.pk).update(status='processing', error_code='awaiting_reconciliation')
            if recovering:
                finish(op, 'ambiguous', 'provider_result_unknown')
        else:
            finish(op, 'failed', exc.code)
