"""Review intent, persist idempotency, and apply local edits atomically."""
import hashlib
import json
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.utils.dateparse import parse_datetime
from calendar_integrations.models import CalendarWriteOperation, CalendarMutationAudit, LocalEvent, BandmateLocalCalendar
from calendar_integrations.permissions import require_source_write


class EventConflict(ValidationError):
    pass


def mutation_audit(operation, outcome):
    CalendarMutationAudit.objects.create(group=operation.group, actor=operation.actor,
        operation=operation, action=operation.action, outcome=outcome)


@transaction.atomic
def submit(user, group, calendar, action, key, payload=None, event=None, version=''):
    require_source_write(user, group, calendar)
    local = isinstance(calendar, BandmateLocalCalendar)
    if event and (event.calendar_id != calendar.pk or event.is_deleted):
        raise Http404
    if not local and event and not event.mutable:
        raise ValidationError('This Google event cannot be edited here (private, recurring, invited or specialized event).')
    event_id = str(event.pk if local else event.external_id) if event else ''
    body = payload or {}
    fingerprint = hashlib.sha256(json.dumps([str(calendar.pk), local, action, event_id, str(version), body], sort_keys=True).encode()).hexdigest()
    existing = CalendarWriteOperation.objects.filter(group=group, key=key).first()
    if existing:
        if existing.fingerprint != fingerprint or existing.actor_id != user.pk:
            raise ValidationError('This operation key was already used for another change. Reload the form.')
        return existing
    if event:
        current = type(event).objects.get(pk=event.pk)
        if str(current.version if local else current.etag) != str(version) or current.is_deleted:
            raise EventConflict('This event changed. Reload and compare before saving.')
    operation = CalendarWriteOperation.objects.create(
        group=group, actor=user, key=key, fingerprint=fingerprint, action=action,
        local_calendar=calendar if local else None, google_calendar=None if local else calendar,
        event_id=event_id, expected_version=str(version), payload=body,
        before_payload=({name: (getattr(event, name).isoformat() if name in ('starts_at', 'ends_at') else getattr(event, name))
            for name in ['title', 'location', 'description', 'starts_at', 'ends_at', 'timezone', 'is_all_day']} if event else {}))
    if not local:
        # A deterministic Google ID prevents duplicate creates after uncertain responses.
        if action == 'create':
            operation.event_id = 'bm' + key.hex
            operation.save(update_fields=['event_id'])
        mutation_audit(operation, 'queued')
        return operation
    if action == 'create':
        values = dict(body, starts_at=parse_datetime(body['starts_at']), ends_at=parse_datetime(body['ends_at']))
        event = LocalEvent.objects.create(calendar=calendar, created_by=user, updated_by=user, **values)
        operation.event_id = str(event.pk)
    else:
        updates = {'updated_by': user, 'version': int(version) + 1}
        if action == 'delete':
            updates['is_deleted'] = True
        else:
            updates.update(body, starts_at=parse_datetime(body['starts_at']), ends_at=parse_datetime(body['ends_at']))
        if not LocalEvent.objects.filter(pk=event.pk, calendar=calendar, version=version, is_deleted=False).update(**updates):
            raise EventConflict('This event changed. Reload before saving.')
    operation.status = 'succeeded'
    operation.save(update_fields=['status', 'event_id', 'updated_at'])
    mutation_audit(operation, 'succeeded')
    return operation
