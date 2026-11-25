"""Human-only approvals, immutable email proof and conservative send recovery."""
import base64
import json
import uuid
from datetime import timedelta
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from player.group_permissions import audit
from calendar_integrations.services.google import ProviderError
from booking.permissions import require_booking
from booking.models import (BookingMembershipRole, BookingEmailTemplate, BookingTemplateVersion, BookingDraft,
    BookingOutreachMessage, BookingJob, BookingGmailConnection, BookingMailRead, BookingOpportunity)
from booking.outreach_forms import PATTERN, validate_template
from .records import lock_band, SuppressionIndex, EDIT_ROLES, digest
from .connections import GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE

MANAGERS = [BookingMembershipRole.Role.MANAGER]


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')))


def eligible(draft):
    index = SuppressionIndex(draft.group)
    for row in (draft.opportunity, draft.contact):
        if row.group_id != draft.group_id or row.is_archived or index.blocked(row, sending=True):
            raise ValidationError('This opportunity or contact is unavailable or suppressed.')
    recipients = [draft.to, *draft.cc, *draft.bcc]
    if not 1 <= len(recipients) <= 9 or len(set(recipients)) != len(recipients):
        raise ValidationError('Use distinct recipients within the message limit.')
    for email in recipients:
        validate_email(email)
        if '\n' in email or '\r' in email:
            raise ValidationError('Invalid recipient.')
    if index.blocks_source(emails=recipients):
        raise ValidationError('A recipient or related identity is suppressed.')
    if not draft.subject.strip() or '\r' in draft.subject or '\n' in draft.subject or not draft.body.strip():
        raise ValidationError('A single-line subject and message body are required.')
    if len(draft.subject) > 300 or len(draft.body) > 12000:
        raise ValidationError('Message exceeds the permitted size.')


@transaction.atomic
def save_template(group, actor, data, template=None):
    lock_band(group)
    require_booking(actor, group, MANAGERS)
    if template:
        template = BookingEmailTemplate.objects.get(group=group, pk=template.pk)
        if template.revision != data['revision']:
            raise ValidationError('Template changed; reload before saving.')
        template.revision += 1
    else:
        template = BookingEmailTemplate(group=group)
    template.name, template.category = data['name'], data['category']
    template.save()
    for name in ('subject', 'body'):
        validate_template(data[name])
    version = BookingTemplateVersion.objects.create(group=group, template=template, number=template.revision,
        subject=data['subject'], body=data['body'], created_by=actor)
    audit(group, actor, 'booking.template.version_created', version.pk)
    return template


@transaction.atomic
def compose(group, actor, opportunity, data, parent=None):
    lock_band(group)
    require_booking(actor, group, EDIT_ROLES)
    contact = data['contact']
    resource = None
    if data.get('epk_resource'):
        from booking.models import BookingResource
        resource = BookingResource.objects.filter(group=group, pk=data['epk_resource'].pk,
            is_archived=False, approved_for_outreach=True, kind='epk').first()
        if not resource or data.get('epk_url'):
            raise ValidationError('Choose an active, approved EPK resource or enter a separate URL.')
    context = {'band_name': group.name, 'event_name': opportunity.name, 'year': str(opportunity.event_year),
        'venue_name': opportunity.venue.name if opportunity.venue_id else '',
        'organizer_name': opportunity.organization.name if opportunity.organization_id else '',
        'contact_name': contact.name, 'prior_contact_context': opportunity.history_evidence,
        'epk_url': resource.url if resource else data.get('epk_url', ''), 'available_dates': data.get('available_dates', '')}
    if resource:
        context.update(epk_resource_identity=str(resource.identity), epk_resource_revision=resource.revision)
    version = None
    if data.get('template'):
        version = BookingTemplateVersion.objects.filter(group=group, template=data['template']).order_by('-number').first()
        if not version:
            raise ValidationError('No template version is available.')
        def render(value):
            validate_template(value)
            missing = [name for name in PATTERN.findall(value) if not context[name]]
            if missing:
                raise ValidationError('Missing template values: ' + ', '.join(sorted(set(missing))))
            return PATTERN.sub(lambda match: context[match[1]], value)
        subject, body = render(version.subject), render(version.body)
    else:
        subject, body = 'Booking inquiry: ' + opportunity.name, 'Hello ' + contact.name + ',\n\n'
    if parent:
        if parent.group_id != group.pk or parent.draft.opportunity_id != opportunity.pk or parent.status != 'sent':
            raise ValidationError('Choose a confirmed sent conversation for this opportunity.')
        subject = parent.snapshot['subject']
    draft = BookingDraft(group=group, opportunity=opportunity, contact=contact, template_version=version,
        created_by=actor, to=contact.email, subject=subject, body=body, context=context, reply_to=parent)
    eligible(draft)
    draft.full_clean()
    draft.save()
    audit(group, actor, 'booking.draft.created', draft.pk)
    return draft


def connection(group, *, sending=True):
    row = BookingGmailConnection.objects.filter(group=group, is_connected=True).first()
    scopes = {GMAIL_READ_SCOPE, GMAIL_SEND_SCOPE} if sending else {GMAIL_READ_SCOPE}
    if not row or not row.mailbox or not scopes.issubset(row.granted_scopes):
        raise ValidationError('Connect this band’s Gmail account with the required read/send permission.')
    return row


def snapshot(draft, mailbox):
    parent = draft.reply_to
    if parent and (parent.group_id != draft.group_id or parent.status != 'sent' or parent.mailbox != mailbox):
        raise ValidationError('The conversation belongs to another mailbox or is not confirmed sent.')
    if parent and draft.subject != parent.snapshot['subject']:
        raise ValidationError('A threaded follow-up must retain the original subject. Start a separate draft for a new subject.')
    return {'to': draft.to, 'cc': draft.cc, 'bcc': draft.bcc, 'subject': draft.subject, 'body': draft.body,
        'from': mailbox, 'draft_revision': draft.revision, 'opportunity_id': draft.opportunity_id,
        'contact_id': draft.contact_id, 'thread_id': parent.gmail_thread_id if parent else '',
        'in_reply_to': parent.rfc_message_id if parent else ''}


def raw_message(data, message_id):
    message = EmailMessage(policy=SMTP)
    for field, key in [('From', 'from'), ('To', 'to'), ('Subject', 'subject')]:
        message[field] = data[key]
    for name in ('cc', 'bcc'):
        if data[name]:
            message[name] = ', '.join(data[name])
    message['Message-ID'] = message_id
    message['Date'] = format_datetime(timezone.now())
    if data['in_reply_to']:
        message['In-Reply-To'] = data['in_reply_to']
        message['References'] = data['in_reply_to']
    message.set_content(data['body'])
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


@transaction.atomic
def approve_send(group, actor, draft_id, revision, approval_hash, key, generation):
    lock_band(group)
    require_booking(actor, group, MANAGERS)
    draft = BookingDraft.objects.get(group=group, pk=draft_id)
    existing = BookingOutreachMessage.objects.filter(group=group, draft=draft).first()
    if existing:
        if existing.actor_id == actor.pk and existing.idempotency_key == key:
            return existing
        raise ValidationError('This draft already has a send approval. Open its delivery status.')
    if draft.status != 'draft' or draft.revision != revision:
        raise ValidationError('The draft changed; preview it again before sending.')
    eligible(draft)
    account = connection(group)
    if account.generation != generation:
        raise ValidationError('Gmail authorization changed; review the sender again.')
    data = snapshot(draft, account.mailbox)
    if fingerprint(data) != approval_hash:
        raise ValidationError('The preview changed; review the complete message again.')
    now = timezone.now()
    recent = BookingOutreachMessage.objects.filter(group=group, created_at__gte=now-timedelta(days=1))
    if recent.count() >= settings.BOOKING_SEND_DAILY_LIMIT:
        raise ValidationError('The band’s daily send approval limit has been reached.')
    recipients = {data['to'], *data['cc'], *data['bcc']}
    for previous in recent:
        if recipients & {previous.snapshot['to'], *previous.snapshot['cc'], *previous.snapshot['bcc']}:
            raise ValidationError('A message to one of these recipients was approved in the last 24 hours. Review delivery history first.')
    job = BookingJob.objects.create(group=group, actor=actor, kind='gmail_send', idempotency_key=key,
        credential_generation=account.generation, run_after=now)
    message_id = '<' + key.hex + '@bandmate.invalid>'
    message = BookingOutreachMessage.objects.create(group=group, draft=draft, actor=actor, job=job,
        idempotency_key=key, snapshot=data, content_hash=fingerprint(data), raw=raw_message(data, message_id),
        rfc_message_id=message_id, mailbox=account.mailbox, credential_generation=account.generation,
        expires_at=now+timedelta(hours=24))
    draft.status = 'approved'
    draft.save(update_fields=['status', 'updated_at'])
    audit(group, actor, 'booking.email.approved', message.pk)
    return message


@transaction.atomic
def queue_read(group, actor, message, kind, retention_days=30):
    lock_band(group)
    require_booking(actor, group, MANAGERS)
    account = connection(group, sending=False)
    if message.group_id != group.pk or account.mailbox != message.mailbox:
        raise ValidationError('Reconnect the original mailbox to inspect this conversation.')
    if kind == 'gmail_reconcile' and message.status not in ('uncertain', 'sending'):
        raise ValidationError('Only uncertain sends need reconciliation.')
    if kind == 'gmail_replies' and (message.status != 'sent' or not message.gmail_thread_id):
        raise ValidationError('Only confirmed sent conversations can be checked for replies.')
    if BookingMailRead.objects.filter(message=message, job__kind=kind, job__status__in=['pending', 'processing']).exists():
        raise ValidationError('A conversation check is already queued.')
    if not 1 <= retention_days <= 365:
        raise ValidationError('Choose 1–365 days of snippet retention.')
    job = BookingJob.objects.create(group=group, actor=actor, kind=kind, idempotency_key=uuid.uuid4(),
        credential_generation=account.generation, run_after=timezone.now())
    BookingMailRead.objects.create(group=group, message=message, job=job, retention_days=retention_days,
        expires_at=timezone.now()+timedelta(hours=24))
    audit(group, actor, 'booking.email.' + kind + '_approved', message.pk)
    return job
