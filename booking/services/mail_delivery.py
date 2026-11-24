"""Send once; reconcile uncertain delivery with read-only Gmail operations."""
import base64
from datetime import timedelta
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from urllib.parse import quote, urlencode
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.http import Http404
from django.utils import timezone
from bandmate.credential_vault import unseal, VaultUnavailable
from calendar_integrations.services.google import http_json, ProviderError
from player.group_permissions import audit
from booking.models import (BookingJob, BookingOutreachMessage, BookingMailRead, BookingDraft,
    BookingOpportunity, BookingConversationReference, BookingReply, BookingGmailConnection)
from .connections import authorized, GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE
from .records import lock_band, digest
from .outreach import eligible, fingerprint


class GmailDelivery:
    def __init__(self, job, message):
        self.job, self.message, self.token = job, message, None

    def check(self, *, send=False):
        authorized(self.job)
        if not BookingJob.objects.filter(pk=self.job.pk, status='processing', attempts=self.job.attempts,
                lease_until__gt=timezone.now()).exists():
            raise ProviderError('job_lease_changed')
        account = BookingGmailConnection.objects.filter(group=self.job.group, generation=self.job.credential_generation,
            is_connected=True, mailbox=self.message.mailbox).first()
        required = {GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE} if send else {GMAIL_READ_SCOPE}
        if not account or not required.issubset(account.granted_scopes):
            raise ProviderError('gmail_authorization_changed')
        if self.message.group_id != self.job.group_id:
            raise ProviderError('invalid_mail_scope')
        if self.job.kind != 'gmail_send' and not BookingMailRead.objects.filter(job=self.job, group=self.job.group,
                message=self.message, expires_at__gt=timezone.now()).exists():
            raise ProviderError('mail_read_expired')
        return account

    def request(self, path, *, body=None, params=None, send=False):
        account = self.check(send=send)
        if not self.token:
            result = http_json('https://oauth2.googleapis.com/token', method='POST', form=True, body={
                'client_id': settings.GMAIL_OAUTH_CLIENT_ID, 'client_secret': settings.GMAIL_OAUTH_CLIENT_SECRET,
                'refresh_token': unseal(self.job.group_id, 'gmail-refresh', account.refresh_ciphertext),
                'grant_type': 'refresh_token'})
            self.token = result.get('access_token')
            if not isinstance(self.token, str) or not self.token:
                raise ProviderError('invalid_token_response')
        self.check(send=send)
        url = 'https://gmail.googleapis.com/gmail/v1/users/me/' + path
        if params:
            url += '?' + urlencode(params, doseq=True)
        return http_json(url, token=self.token, method='POST' if body is not None else 'GET', body=body)

    def verify_mailbox(self):
        profile = self.request('profile')
        if profile.get('emailAddress', '').strip().lower() != self.message.mailbox:
            raise ProviderError('mailbox_identity_changed')
        self.check()


def send_gate(client, message):
    client.check(send=True)
    if settings.BOOKING_SEND_DAILY_LIMIT <= 0:
        raise ProviderError('sending_disabled')
    if message.expires_at <= timezone.now():
        raise ProviderError('send_approval_expired')
    draft = BookingDraft.objects.get(group=message.group, pk=message.draft_id)
    eligible(draft)
    from .outreach import snapshot
    if fingerprint(message.snapshot) != message.content_hash or snapshot(draft, message.mailbox) != message.snapshot:
        raise ProviderError('approved_content_changed')
    if draft.status != 'approved':
        raise ProviderError('draft_approval_changed')


def confirmed(message, result, actor):
    if not all(isinstance(result.get(key), str) and 0 < len(result[key]) <= 100 for key in ('id', 'threadId')):
        raise ProviderError('send_confirmation_invalid')
    # Once the provider confirms, preserve delivery proof even if permissions were
    # revoked during the call. Never convert a successful send into a retry.
    with transaction.atomic():
        lock_band(message.group)
        BookingOutreachMessage.objects.filter(pk=message.pk).update(status='sent', gmail_message_id=result['id'],
            gmail_thread_id=result['threadId'], sent_at=timezone.now(), error_code='')
        BookingDraft.objects.filter(pk=message.draft_id, group=message.group).update(status='sent')
        BookingOpportunity.objects.filter(pk=message.draft.opportunity_id, group=message.group,
            stage__in=['discovered', 'needs_review', 'researching', 'qualified', 'ready_to_contact']).update(
                stage='contacted', revision=F('revision')+1)
        audit(message.group, actor, 'booking.email.sent', message.pk)


def send_once(job, message, client):
    message.refresh_from_db()
    if message.status == 'sent':
        return
    if message.status in ('sending', 'uncertain'):
        BookingOutreachMessage.objects.filter(pk=message.pk).update(status='uncertain', error_code='confirmation_missing')
        raise ProviderError('send_needs_reconciliation')
    if message.status != 'approved':
        raise ProviderError('send_not_approved')
    send_gate(client, message)
    client.verify_mailbox()
    with transaction.atomic():
        lock_band(job.group)
        send_gate(client, message)
        if not BookingOutreachMessage.objects.filter(pk=message.pk, status='approved').update(status='sending', started_at=timezone.now()):
            raise ProviderError('send_already_started')
    # A crash from this point onward is uncertain. The immutable RFC Message-ID
    # supports later lookup; it is not a provider idempotency guarantee.
    send_gate(client, message)
    result = client.request('messages/send', body={'raw': message.raw,
        **({'threadId': message.snapshot['thread_id']} if message.snapshot['thread_id'] else {})}, send=True)
    confirmed(message, result, job.actor)


def matches_raw(message, data):
    try:
        if 'SENT' not in data.get('labelIds', []):
            return False
        parsed = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(data['raw'] + '==='))
        expected = message.snapshot
        if parsed['Message-ID'] != message.rfc_message_id or str(parsed['Subject']) != expected['subject']:
            return False
        for header, addresses in [('From', [expected['from']]), ('To', [expected['to']]), ('Cc', expected['cc']), ('Bcc', expected['bcc'])]:
            found = [email.lower() for _, email in getaddresses(parsed.get_all(header, []))]
            if sorted(found) != sorted(addresses):
                return False
        body = parsed.get_body(preferencelist=('plain',))
        return body is not None and body.get_content().replace('\r\n', '\n').rstrip('\n') == expected['body'].replace('\r\n', '\n').rstrip('\n')
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def reconcile(job, message, client):
    if message.status not in ('sending', 'uncertain'):
        raise ProviderError('send_not_uncertain')
    client.verify_mailbox()
    data = client.request('messages', params={'q': 'in:sent rfc822msgid:' + message.rfc_message_id,
        'maxResults': 10, 'includeSpamTrash': 'false'})
    rows = data.get('messages', [])
    if not isinstance(rows, list) or len(rows) != 1 or data.get('nextPageToken'):
        raise ProviderError('send_still_uncertain')
    result = client.request('messages/' + quote(rows[0]['id'], safe=''), params={'format': 'raw'})
    if not matches_raw(message, result):
        raise ProviderError('send_still_uncertain')
    client.check()
    confirmed(message, result, job.actor)


def replies(job, message, client):
    read = BookingMailRead.objects.get(group=job.group, job=job)
    client.verify_mailbox()
    data = client.request('threads/' + quote(message.gmail_thread_id, safe=''), params={'format': 'metadata',
        'metadataHeaders': ['From', 'To', 'Cc', 'Subject', 'Date', 'Message-ID', 'In-Reply-To']})
    rows = data.get('messages')
    if data.get('id') != message.gmail_thread_id or not isinstance(rows, list) or len(rows) > 100:
        raise ProviderError('invalid_or_large_thread')
    from .imports import parse_message
    from .records import SuppressionIndex
    from datetime import datetime, timezone as dt_timezone
    with transaction.atomic():
        lock_band(job.group)
        client.check()
        for row in rows:
            if row.get('threadId') != message.gmail_thread_id or not isinstance(row.get('id'), str):
                raise ProviderError('invalid_thread_message')
            if 'SENT' in row.get('labelIds', []):
                continue
            headers = {h['name'].lower(): h['value'] for h in row.get('payload', {}).get('headers', [])}
            senders = [email.lower() for _, email in getaddresses([headers.get('from', '')])]
            if not senders or message.mailbox in senders:
                continue
            parsed = parse_message(row, message.mailbox)
            key = digest(digest(message.mailbox) + ':' + row['id'])
            if SuppressionIndex(job.group).blocks_source(emails=parsed['correspondents']):
                continue
            reference, created = BookingConversationReference.objects.get_or_create(group=job.group, source_key=key,
                defaults={'message_id': row['id'], 'thread_id': message.gmail_thread_id,
                    'subject': parsed['subject'], 'excerpt': parsed['excerpt'],
                    'correspondents': parsed['correspondents'], 'sent_at': parsed['sent_at'],
                    'expires_at': timezone.now()+timedelta(days=read.retention_days)})
            if not reference.purged and reference.expires_at > timezone.now():
                BookingReply.objects.get_or_create(group=job.group, reference=reference, defaults={'message': message})
        audit(job.group, job.actor, 'booking.email.replies_checked', message.pk)


def process_mail_job(job):
    message = (BookingOutreachMessage.objects.get(group=job.group, job=job) if job.kind == 'gmail_send'
        else BookingMailRead.objects.get(group=job.group, job=job).message)
    client = GmailDelivery(job, message)
    code = ''
    try:
        {'gmail_send': send_once, 'gmail_reconcile': reconcile, 'gmail_replies': replies}[job.kind](job, message, client)
    except ProviderError as exc:
        code = exc.code
    except (ValidationError, Http404, VaultUnavailable):
        code = 'authorization_or_content_changed'
    except Exception:
        code = 'mail_operation_failed'
    with transaction.atomic():
        lock_band(job.group)
        if not BookingJob.objects.filter(pk=job.pk, status='processing', attempts=job.attempts).exists():
            return
        if job.kind == 'gmail_send' and code:
            message.refresh_from_db()
            if message.status != 'sent':
                uncertain = message.status in ('sending', 'uncertain')
                BookingOutreachMessage.objects.filter(pk=message.pk).update(
                    status='uncertain' if uncertain else 'failed', error_code=code)
                BookingDraft.objects.filter(pk=message.draft_id).update(status='uncertain' if uncertain else 'failed')
        BookingJob.objects.filter(pk=job.pk).update(status='failed' if code else 'succeeded', error_code=code,
            lease_until=None, finished_at=timezone.now())
        audit(job.group, job.actor, 'booking.email.operation_finished', message.pk, new=code or job.kind)
