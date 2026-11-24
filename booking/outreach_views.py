import uuid
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction, IntegrityError
from django.http import HttpResponseBadRequest, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from player.group_permissions import audit
from .models import (BookingEmailTemplate, BookingTemplateVersion, BookingDraft, BookingOpportunity,
    BookingOutreachMessage, BookingReply, BookingJob)
from .permissions import require_booking
from .outreach_forms import TemplateForm, ComposeForm, DraftForm, PLACEHOLDERS
from .services.records import EDIT_ROLES, lock_band
from .services import outreach


@login_required
@require_GET
def templates(request):
    role = require_booking(request.user, request.active_group)
    return render(request, 'booking/templates.html', {'nav_active': 'booking', 'is_manager': role in outreach.MANAGERS,
        'rows': BookingEmailTemplate.objects.filter(group=request.active_group).order_by('name')})


@login_required
@require_http_methods(['GET', 'POST'])
def template_edit(request, template_id=None):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    row = get_object_or_404(BookingEmailTemplate, pk=template_id, group=request.active_group) if template_id else None
    version = row.versions.order_by('-number').first() if row else None
    initial = {'name': row.name, 'category': row.category, 'revision': row.revision,
        'subject': version.subject, 'body': version.body} if version else {}
    form = TemplateForm(request.POST if request.method == 'POST' else None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            outreach.save_template(request.active_group, request.user, form.cleaned_data, row)
            return redirect('booking:templates')
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, 'booking/outreach_form.html', {'nav_active': 'booking', 'form': form,
        'title': 'Edit template' if row else 'New template', 'button': 'Save new version',
        'placeholders': sorted(PLACEHOLDERS), 'versions': row.versions.order_by('-number') if row else []})


@login_required
@require_GET
def drafts(request):
    role = require_booking(request.user, request.active_group)
    return render(request, 'booking/drafts.html', {'nav_active': 'booking',
        'is_manager': role in outreach.MANAGERS,
        'rows': BookingDraft.objects.filter(group=request.active_group).select_related('opportunity').order_by('-updated_at')[:100]})


@login_required
@require_GET
def outreach_export(request):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    from player.models import GroupAccessAudit
    response = JsonResponse({'messages': list(BookingOutreachMessage.objects.filter(group=request.active_group).values(
        'id', 'draft_id', 'actor_id', 'snapshot', 'content_hash', 'rfc_message_id', 'mailbox', 'status',
        'gmail_message_id', 'gmail_thread_id', 'error_code', 'created_at', 'sent_at')),
        'activity': list(GroupAccessAudit.objects.filter(group=request.active_group,
            action__startswith='booking.').values('actor_id', 'action', 'target', 'previous_value', 'new_value', 'created_at'))})
    response['Content-Disposition'] = 'attachment; filename="booking-outreach.json"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_http_methods(['GET', 'POST'])
def compose(request, opportunity_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    opportunity = get_object_or_404(BookingOpportunity, group=request.active_group, pk=opportunity_id, is_archived=False)
    parent_id = request.POST.get('reply_to') if request.method == 'POST' else request.GET.get('reply_to')
    parent = get_object_or_404(BookingOutreachMessage, group=request.active_group, pk=parent_id,
        draft__opportunity=opportunity, status='sent') if parent_id else None
    form = ComposeForm(request.POST if request.method == 'POST' else None, group=request.active_group,
        initial={'contact': parent.draft.contact_id if parent else opportunity.contact_id})
    if request.method == 'POST' and form.is_valid():
        try:
            row = outreach.compose(request.active_group, request.user, opportunity, form.cleaned_data, parent)
            return redirect('booking:draft', draft_id=row.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
    return render(request, 'booking/outreach_form.html', {'nav_active': 'booking', 'form': form,
        'title': 'Draft for ' + opportunity.name, 'button': 'Create editable draft', 'reply_to': parent})


@login_required
@require_http_methods(['GET', 'POST'])
def draft(request, draft_id):
    role = require_booking(request.user, request.active_group)
    row = get_object_or_404(BookingDraft, group=request.active_group, pk=draft_id)
    can_edit = role in EDIT_ROLES and row.status == 'draft'
    form = DraftForm(request.POST if request.method == 'POST' else None, instance=row,
        initial={'revision': row.revision, 'cc': ', '.join(row.cc), 'bcc': ', '.join(row.bcc)})
    if request.method == 'POST':
        require_booking(request.user, request.active_group, EDIT_ROLES)
        if not can_edit:
            return HttpResponseBadRequest('Approved messages are locked. Inspect their delivery status.')
        if form.is_valid():
            try:
                with transaction.atomic():
                    lock_band(request.active_group)
                    require_booking(request.user, request.active_group, EDIT_ROLES)
                    current = BookingDraft.objects.get(group=request.active_group, pk=row.pk)
                    if current.status != 'draft' or current.revision != form.cleaned_data['revision']:
                        raise ValidationError('Draft changed; reload before saving.')
                    outreach.eligible(row)
                    row.revision = current.revision + 1
                    row.ai_suggestion = {}
                    form.save()
                    audit(request.active_group, request.user, 'booking.draft.saved', row.pk)
                return redirect('booking:draft', draft_id=row.pk)
            except ValidationError as exc:
                form.add_error(None, exc)
    message = BookingOutreachMessage.objects.filter(group=request.active_group, draft=row).first()
    return render(request, 'booking/draft.html', {'nav_active': 'booking', 'draft': row, 'form': form,
        'can_edit': can_edit, 'is_manager': role in outreach.MANAGERS, 'message': message})


@login_required
@require_http_methods(['GET', 'POST'])
def send(request, draft_id):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    row = get_object_or_404(BookingDraft, group=request.active_group, pk=draft_id)
    existing = BookingOutreachMessage.objects.filter(group=request.active_group, draft=row).first()
    if existing:
        return redirect('booking:message', message_id=existing.pk)
    error, data, generation, approval_hash = '', None, None, ''
    try:
        outreach.eligible(row)
        account = outreach.connection(request.active_group)
        data = outreach.snapshot(row, account.mailbox)
        generation, approval_hash = account.generation, outreach.fingerprint(data)
        if request.method == 'POST':
            if request.POST.get('confirm') != 'send_exact_message':
                raise ValidationError('Confirm the complete message and recipients before sending.')
            message = outreach.approve_send(request.active_group, request.user, row.pk,
                int(request.POST.get('revision', '')), request.POST.get('approval_hash', ''),
                uuid.UUID(request.POST.get('operation_key', '')), int(request.POST.get('generation', '')))
            return redirect('booking:message', message_id=message.pk)
    except (ValidationError, ValueError, IntegrityError) as exc:
        error = ' '.join(exc.messages) if isinstance(exc, ValidationError) else 'The approval is invalid or already used. Reload the draft.'
    return render(request, 'booking/send_preview.html', {'nav_active': 'booking', 'draft': row, 'data': data,
        'error': error, 'approval_hash': approval_hash, 'generation': generation, 'operation_key': uuid.uuid4(),
        'previous': BookingOutreachMessage.objects.filter(group=request.active_group,
            draft__opportunity=row.opportunity).order_by('-created_at')[:20]}, status=400 if error and request.method == 'POST' else 200)


@login_required
@require_GET
def message(request, message_id):
    role = require_booking(request.user, request.active_group)
    row = get_object_or_404(BookingOutreachMessage, group=request.active_group, pk=message_id)
    from .services.imports import purge_messages
    purge_messages(request.active_group, expired_only=True)
    return render(request, 'booking/message.html', {'nav_active': 'booking', 'message': row,
        'is_manager': role in outreach.MANAGERS, 'can_edit': role in EDIT_ROLES,
        'replies': BookingReply.objects.filter(group=request.active_group, message__gmail_thread_id=row.gmail_thread_id,
            message__mailbox=row.mailbox, reference__purged=False, reference__expires_at__gt=timezone.now()).select_related('reference'),
        'checks': BookingJob.objects.filter(bookingmailread__message=row, group=request.active_group).order_by('-created_at')[:10]})


@login_required
@require_POST
def mail_read(request, message_id, kind):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    row = get_object_or_404(BookingOutreachMessage, group=request.active_group, pk=message_id)
    if kind not in ('gmail_reconcile', 'gmail_replies'):
        raise Http404
    if request.POST.get('confirm') != kind:
        return HttpResponseBadRequest('Approve this exact conversation read first.')
    try:
        outreach.queue_read(request.active_group, request.user, row, kind, int(request.POST.get('retention_days', '30')))
    except (ValidationError, ValueError) as exc:
        return HttpResponseBadRequest(' '.join(exc.messages) if isinstance(exc, ValidationError) else 'Invalid retention period.')
    return redirect('booking:message', message_id=row.pk)


@login_required
@require_POST
def cancel_send(request, message_id):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, outreach.MANAGERS)
        row = get_object_or_404(BookingOutreachMessage, group=request.active_group, pk=message_id)
        if row.status != 'approved':
            return HttpResponseBadRequest('A started send cannot be cancelled. Inspect delivery or reconcile it.')
        BookingOutreachMessage.objects.filter(pk=row.pk).update(status='cancelled')
        BookingDraft.objects.filter(pk=row.draft_id).update(status='cancelled')
        BookingJob.objects.filter(pk=row.job_id).update(status='cancelled', lease_until=None)
        audit(request.active_group, request.user, 'booking.email.cancelled', row.pk)
    return redirect('booking:message', message_id=row.pk)


@login_required
@require_POST
def ai_preview(request, draft_id):
    require_booking(request.user, request.active_group, outreach.MANAGERS)
    row = get_object_or_404(BookingDraft, group=request.active_group, pk=draft_id, status='draft')
    from .services.research_jobs import new_preview
    from calendar_integrations.services.google import ProviderError
    try:
        run = new_preview(request.active_group, request.user, 'ai_draft', {'draft_id': row.pk})
    except (ProviderError, ValidationError):
        return HttpResponseBadRequest('AI drafting is unavailable. Check the draft, suppression, key, pricing and budget settings.')
    return redirect('booking:research_run', run_id=run.pk)


@login_required
@require_POST
def adopt_ai(request, draft_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    if request.POST.get('confirm') != 'reviewed_draft_suggestion':
        return HttpResponseBadRequest('Review and confirm the suggested draft first.')
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, EDIT_ROLES)
        row = get_object_or_404(BookingDraft, group=request.active_group, pk=draft_id, status='draft')
        suggestion = row.ai_suggestion
        if not suggestion or str(suggestion['run_id']) != request.POST.get('run_id') or row.revision != suggestion['scope']['revision']:
            return HttpResponseBadRequest('The draft or suggestion changed; review it again.')
        outreach.eligible(row)
        row.body, row.ai_suggestion = suggestion['body'], {}
        row.revision += 1
        row.save(update_fields=['body', 'ai_suggestion', 'revision', 'updated_at'])
        audit(request.active_group, request.user, 'booking.draft.ai_adopted', row.pk, new=suggestion['run_id'])
    return redirect('booking:draft', draft_id=row.pk)
