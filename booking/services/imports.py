import html
import json
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import getaddresses
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from calendar_integrations.services.google import ProviderError
from booking.models import (BookingResearchRun, BookingGmailConnection, BookingConversationReference,
    BookingCandidate, BookingEvidence)
from .connections import authorized
from .records import digest, SuppressionIndex, lock_band
from .gmail import GmailReader


def live_run(job):
    authorized(job)
    run = BookingResearchRun.objects.filter(job=job, group_id=job.group_id, status='running').first()
    if not run or run.expires_at <= timezone.now():
        raise ProviderError('research_cancelled_or_expired')
    if not type(job).objects.filter(pk=job.pk, status='processing', attempts=job.attempts).exists():
        raise ProviderError('lease_changed')
    if run.kind == 'gmail_import' and not BookingGmailConnection.objects.filter(group_id=job.group_id,
            generation=job.credential_generation, is_connected=True).exists():
        raise ProviderError('credential_changed')
    return run


def save_checkpoint(job, checkpoint):
    with transaction.atomic():
        lock_band(job.group)
        run = live_run(job)
        run.checkpoint = checkpoint
        run.save(update_fields=['checkpoint'])


def parse_message(data, mailbox):
    if not isinstance(data, dict) or not isinstance(data.get('payload', {}).get('headers', []), list):
        raise ProviderError('invalid_message_metadata')
    headers = {}
    for header in data.get('payload', {}).get('headers', []):
        if isinstance(header, dict) and isinstance(header.get('name'), str) and isinstance(header.get('value'), str):
            headers.setdefault(header['name'].lower(), []).append(header['value'])
    partners = []
    for name, email in getaddresses(headers.get('from', []) + headers.get('to', [])):
        email = email.strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            continue
        if email != mailbox and email not in partners:
            partners.append(email)
    try:
        sent = datetime.fromtimestamp(int(data['internalDate']) / 1000, dt_timezone.utc)
    except (ValueError, KeyError, TypeError, OverflowError, OSError):
        raise ProviderError('invalid_message_date') from None
    return {'subject': ' '.join(headers.get('subject', ['(No subject)']))[:500],
        'excerpt': html.unescape(str(data.get('snippet', '')))[:1000], 'correspondents': partners[:20],
        'sent_at': sent, 'thread_id': str(data.get('threadId', ''))[:100]}


@transaction.atomic
def store_message(job, message_id, data):
    lock_band(job.group)
    run = live_run(job)
    source_key = digest(run.mailbox_hash + ':' + message_id)
    if BookingConversationReference.objects.filter(group=job.group, source_key=source_key).exists():
        return
    connection = BookingGmailConnection.objects.get(group=job.group, generation=job.credential_generation)
    message = parse_message(data, connection.mailbox)
    start, end = run.scope['start_date'], run.scope['end_date']
    day = message['sent_at'].date().isoformat()
    if not start <= day <= end:
        return
    blocked = SuppressionIndex(job.group).blocks_source(emails=message['correspondents'], name=message['subject'])
    expires = timezone.now() + timedelta(days=run.scope['retention_days'])
    if blocked:
        BookingConversationReference.objects.create(group=job.group, source_key=source_key, expires_at=expires, purged=True)
        return
    BookingConversationReference.objects.create(group=job.group, source_key=source_key,
        message_id=message_id, expires_at=expires, **message)
    BookingCandidate.objects.get_or_create(group=job.group, source_key=source_key, defaults={'run': run,
        'source_type': 'gmail', 'source_ref': source_key, 'title': message['subject'][:200],
        'proposed': {'name': message['subject'][:200], 'contact_email': next(iter(message['correspondents']), ''),
                     'history_date': day}, 'excerpt': message['excerpt'], 'content_hash': digest(message['excerpt'])})


def import_page(job, client=None):
    run = live_run(job)
    client = client or GmailReader(job)
    profile = client.profile()  # Identity rechecked at every bounded worker slice.
    cp = dict(run.checkpoint)
    if not cp:
        connection = BookingGmailConnection.objects.get(group=job.group, generation=job.credential_generation)
        delta = bool(connection.history_id and connection.history_scope_hash == run.scope_hash)
        cp = {'phase': 'history' if delta else 'search', 'delta': delta, 'changed': [], 'page': '',
              'history_start': connection.history_id if delta else '', 'anchor': profile['historyId'], 'pages': 0}
    if cp['phase'] == 'history':
        try:
            data = client.history(cp['history_start'], cp.get('page', ''))
        except ProviderError as exc:
            if exc.status != 404:
                raise
            # Expired Gmail history: repeat only the approved targeted search.
            cp.update(phase='search', delta=False, changed=[], page='', pages=0)
            save_checkpoint(job, cp)
            return False
        changed = set(cp['changed'])
        deleted = []
        for history in data.get('history', []):
            for item in history.get('messagesAdded', []):
                value = item.get('message', {}).get('id')
                if isinstance(value, str) and len(value) <= 100:
                    changed.add(value)
            for item in history.get('messagesDeleted', []):
                value = item.get('message', {}).get('id')
                if isinstance(value, str) and len(value) <= 100:
                    deleted.append(digest(run.mailbox_hash + ':' + value))
        if deleted:
            with transaction.atomic():
                lock_band(job.group)
                live_run(job)
                purge_messages(job.group, source_keys=deleted)
        cp['pages'] += 1
        cp['changed'], cp['page'] = sorted(changed), data.get('nextPageToken', '')
        if len(changed) > 2000 or cp['pages'] >= 20:
            cp.update(phase='search', delta=False, changed=[], page='', pages=0)
        elif not cp['page']:
            cp.update(phase='search', page='', pages=0)
        save_checkpoint(job, cp)
        return False
    if not cp.get('pending'):
        data = client.messages(run.scope['query'], cp.get('page', ''))
        items = data.get('messages', [])
        if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) or len(item['id']) > 100 for item in items):
            raise ProviderError('invalid_message_list')
        cp['pending'] = [item['id'] for item in items if not cp['delta'] or item['id'] in cp['changed']]
        cp['next_page'] = data.get('nextPageToken', '')
        cp['pages'] += 1
        save_checkpoint(job, cp)
    # A page contains at most five messages: fixed upper bound on each lease's I/O.
    for message_id in list(cp['pending']):
        run = live_run(job)
        if run.processed >= run.scope['max_messages']:
            return 'limited'
        source_key = digest(run.mailbox_hash + ':' + message_id)
        if not BookingConversationReference.objects.filter(group=job.group, source_key=source_key).exists():
            try:
                data = client.message(message_id)
            except ProviderError as exc:
                if exc.status != 404:
                    raise
                data = None
            if data:
                if data.get('id') != message_id:
                    raise ProviderError('message_identity_changed')
                store_message(job, message_id, data)
            with transaction.atomic():
                lock_band(job.group)
                run = live_run(job)
                run.processed += 1
                run.save(update_fields=['processed'])
        cp['pending'].remove(message_id)
        save_checkpoint(job, cp)
    cp['page'] = cp.pop('next_page', '')
    save_checkpoint(job, cp)
    if cp['page'] and cp['pages'] >= 100:
        return 'limited'
    if cp['page']:
        return False
    with transaction.atomic():
        lock_band(job.group)
        live_run(job)
        BookingGmailConnection.objects.filter(group=job.group, generation=job.credential_generation).update(
            history_id=cp['anchor'], history_scope_hash=run.scope_hash)
    return True


@transaction.atomic
def purge_messages(group, *, expired_only=False, source_keys=None):
    lock_band(group)
    rows = BookingConversationReference.objects.filter(group=group, purged=False)
    if expired_only:
        rows = rows.filter(expires_at__lte=timezone.now())
    if source_keys is not None:
        rows = rows.filter(source_key__in=source_keys)
    keys = list(rows.values_list('source_key', flat=True))
    # Keep only content-free hashes to prevent a later scheduled scan from
    # resurrecting deleted mail. Human-reviewed records remain separate.
    rows.update(message_id='', thread_id='', subject='', excerpt='', correspondents=[], sent_at=None, purged=True)
    BookingCandidate.objects.filter(group=group, source_type='gmail', source_key__in=keys).update(
        title='Removed email evidence', source_ref='', excerpt='', proposed={}, status='purged', content_hash='')
    BookingEvidence.objects.filter(group=group, source_type='gmail', source_ref__in=keys).update(excerpt='', source_ref='', content_hash='')
    return len(keys)
