import json
from datetime import date
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.core.serializers.json import DjangoJSONEncoder
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from player.group_permissions import audit
from .permissions import require_booking
from .models import BookingMembershipRole, BookingOpportunity, BookingEvidence, BookingFollowUp, BookingSuppression
from .record_forms import FORMS, FollowUpForm, SuppressionForm
from .services.records import RECORDS, EDIT_ROLES, SuppressionIndex, save_record, merge_records, lock_band


def record_model(kind):
    if kind not in RECORDS:
        raise Http404
    return RECORDS[kind]


def visible_rows(group, kind):
    rows = record_model(kind).objects.filter(group=group, is_archived=False)
    relations = [f.name for f in rows.model._meta.fields if f.is_relation and f.name != 'group']
    return rows.select_related(*relations)


@login_required
@require_GET
def directory(request, kind):
    role = require_booking(request.user, request.active_group)
    rows = visible_rows(request.active_group, kind)
    query = request.GET.get('q', '').strip()[:200]
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(city__icontains=query) | Q(tags__icontains=query))
    if kind == 'opportunities':
        year = request.GET.get('year', '')
        if year.isdigit():
            rows = rows.filter(event_year=int(year))
        if request.GET.get('stage') in BookingOpportunity.Stage.values:
            rows = rows.filter(stage=request.GET['stage'])
        rows = rows.order_by('-priority', 'verified_deadline', 'name')
    else:
        rows = rows.order_by('name')
    suppressions = SuppressionIndex(request.active_group)
    rows = [row for row in rows if not suppressions.blocked(row)]
    if kind == 'opportunities':
        from .models import BookingSettings
        from .services.ranking import assess
        config = BookingSettings.objects.get(group=request.active_group)
        for row in rows:
            row.ranking = assess(row, config)
        if request.GET.get('local_only') == 'yes':
            rows = [row for row in rows if not row.ranking['excluded'] and not row.ranking['outside_radius']]
        rows.sort(key=lambda row: (-row.ranking['score'], row.name.casefold()))
    return render(request, 'booking/directory.html', {'nav_active': 'booking', 'kind': kind,
        'page': Paginator(rows, 30).get_page(request.GET.get('page')), 'query': query,
        'stage_choices': BookingOpportunity.Stage.choices, 'can_edit': role in EDIT_ROLES,
        'is_manager': role == BookingMembershipRole.Role.MANAGER, 'kinds': RECORDS})


@login_required
@require_http_methods(['GET', 'POST'])
def edit_record(request, kind, record_id=None):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    model = record_model(kind)
    record = get_object_or_404(model, pk=record_id, group=request.active_group, is_archived=False) if record_id else model(group=request.active_group)
    initial = {'event_year': timezone.now().year + 1} if kind == 'opportunities' and not record_id else {}
    if kind == 'opportunities' and not record_id and request.method == 'GET' and request.GET.get('from'):
        original_id = request.GET['from']
        if not original_id.isdigit():
            raise Http404
        original = get_object_or_404(BookingOpportunity, pk=original_id, group=request.active_group)
        initial.update({field: getattr(original, field) for field in ['name', 'city', 'website', 'organization', 'venue', 'contact', 'series', 'category', 'priority', 'tags']})
        initial['event_year'] = original.event_year + 1
        # Deliberately do not copy last year's event/application/deadline dates.
    form = FORMS[kind](request.POST if request.method == 'POST' else None, instance=record, group=request.active_group, initial=initial)
    status = 200
    if request.method == 'POST' and form.is_valid():
        try:
            record = save_record(form, request.user, request.active_group, form.cleaned_data.get('revision'))
            return redirect('booking:record_detail', kind=kind, record_id=record.pk)
        except ValidationError as exc:
            form.add_error(None, exc)
            status = 409
        except IntegrityError:
            form.add_error(None, 'A record with this email or annual series edition already exists. Review or merge the existing records.')
            status = 409
    return render(request, 'booking/record_form.html', {'nav_active': 'booking', 'form': form, 'kind': kind,
        'record': record, 'title': ('Edit ' if record_id else 'Add ') + kind.rstrip('s')}, status=status)


@login_required
@require_GET
def record_detail(request, kind, record_id):
    role = require_booking(request.user, request.active_group)
    from .services.imports import purge_messages
    purge_messages(request.active_group, expired_only=True)
    record = get_object_or_404(record_model(kind), pk=record_id, group=request.active_group)
    index = SuppressionIndex(request.active_group)
    related = [(field.replace('_', ' ').title(), getattr(record, field)) for field in ['organization', 'venue', 'contact', 'series', 'artist'] if getattr(record, field + '_id', None)]
    from .models import BookingDraft
    return render(request, 'booking/record_detail.html', {'nav_active': 'booking', 'kind': kind, 'record': record,
        'related': related, 'blocked': index.blocked(record), 'no_contact': index.blocked(record, sending=True),
        'drafts': BookingDraft.objects.filter(group=request.active_group, opportunity=record).order_by('-created_at') if kind == 'opportunities' else [],
        'can_edit': role in EDIT_ROLES and not record.is_archived,
        'is_manager': role == BookingMembershipRole.Role.MANAGER,
        'evidence': BookingEvidence.objects.filter(group=request.active_group, record_identity=record.identity).order_by('-created_at'),
        'followups': BookingFollowUp.objects.filter(group=request.active_group, opportunity=record).order_by('due_date') if kind == 'opportunities' else [],
        'followup_form': FollowUpForm(), 'merge_targets': record_model(kind).objects.filter(group=request.active_group, is_archived=False).exclude(pk=record.pk).order_by('name')[:200]})


@login_required
@require_POST
def merge(request, kind, record_id):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    model = record_model(kind)
    get_object_or_404(model, group=request.active_group, pk=record_id, is_archived=False)
    target_id = request.POST.get('target', '')
    if not target_id.isdigit() or request.POST.get('confirm') != 'yes':
        return HttpResponseBadRequest('Choose a target and confirm the merge.')
    get_object_or_404(model, group=request.active_group, pk=target_id, is_archived=False)
    try:
        target = merge_records(request.active_group, request.user, kind, record_id, int(target_id))
    except (ValidationError, IntegrityError):
        return HttpResponseBadRequest('The merge conflicts with an annual edition or record identity. Review both records first.')
    return redirect('booking:record_detail', kind=kind, record_id=target.pk)


@login_required
@require_POST
def followup(request, record_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    opportunity = get_object_or_404(BookingOpportunity, group=request.active_group, pk=record_id, is_archived=False)
    form = FollowUpForm(request.POST)
    if not form.is_valid():
        return HttpResponseBadRequest('Provide a valid reminder date and note.')
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, EDIT_ROLES)
        reminder = form.save(commit=False)
        reminder.group, reminder.opportunity = request.active_group, opportunity
        reminder.save()
        audit(request.active_group, request.user, 'booking.reminder.created', reminder.pk)
    return redirect('booking:record_detail', kind='opportunities', record_id=record_id)


@login_required
@require_POST
def complete_followup(request, reminder_id):
    require_booking(request.user, request.active_group, EDIT_ROLES)
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, EDIT_ROLES)
        reminder = get_object_or_404(BookingFollowUp, group=request.active_group, pk=reminder_id)
        reminder.completed = True
        reminder.save(update_fields=['completed', 'updated_at'])
        audit(request.active_group, request.user, 'booking.reminder.completed', reminder.pk)
    return redirect('booking:record_detail', kind='opportunities', record_id=reminder.opportunity_id)


@login_required
@require_http_methods(['GET', 'POST'])
def suppressions(request):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    form = SuppressionForm(request.POST if request.method == 'POST' else None,
        instance=BookingSuppression(group=request.active_group))
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            lock_band(request.active_group)
            require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
            data = form.cleaned_data
            if data['kind'] == 'identity' and not any(model.objects.filter(group=request.active_group, identity=data['value']).exists() for model in RECORDS.values()):
                raise Http404
            row, _ = BookingSuppression.objects.get_or_create(group=request.active_group, kind=data['kind'], value=data['value'], defaults={'reason': data['reason']})
            row.active, row.reason, row.created_by = True, data['reason'], request.user
            row.do_not_contact = row.do_not_contact or data['do_not_contact']
            row.save()
            audit(request.active_group, request.user, 'booking.suppression.created', row.pk)
        return redirect('booking:suppressions')
    return render(request, 'booking/suppressions.html', {'nav_active': 'booking', 'form': form,
        'entries': BookingSuppression.objects.filter(group=request.active_group).order_by('-created_at')})


@login_required
@require_POST
def restore_suppression(request, suppression_id):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
        row = get_object_or_404(BookingSuppression, group=request.active_group, pk=suppression_id)
        row.active = False
        # Restoring visibility never clears an unsubscribe/do-not-contact flag.
        row.save(update_fields=['active'])
        audit(request.active_group, request.user, 'booking.suppression.restored', row.pk)
    return redirect('booking:suppressions')


@login_required
@require_GET
def export_records(request):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    from .services.imports import purge_messages
    purge_messages(request.active_group, expired_only=True)
    data = {kind: [dict(model_to_dict(row), identity=str(row.identity))
        for row in model.objects.filter(group=request.active_group)] for kind, model in RECORDS.items()}
    for label, model in [('evidence', BookingEvidence), ('suppressions', BookingSuppression), ('reminders', BookingFollowUp)]:
        data[label] = [model_to_dict(row) for row in model.objects.filter(group=request.active_group)]
    response = HttpResponse(json.dumps(data, cls=DjangoJSONEncoder, indent=2), content_type='application/json')
    response['Content-Disposition'] = 'attachment; filename="booking-records.json"'
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@require_POST
def resolve_opt_out(request, suppression_id):
    require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
    reason = request.POST.get('reason', '').strip()
    if request.POST.get('confirm') != 'renewed_permission' or not reason or len(reason) > 500:
        return HttpResponseBadRequest('Document the renewed contact permission and explicitly confirm resolving this opt-out.')
    with transaction.atomic():
        lock_band(request.active_group)
        require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])
        row = get_object_or_404(BookingSuppression, group=request.active_group, pk=suppression_id)
        row.do_not_contact, row.resolved_at = False, timezone.now()
        row.save(update_fields=['do_not_contact', 'resolved_at'])
        audit(request.active_group, request.user, 'booking.opt_out.resolved', row.pk, new=reason)
    return redirect('booking:suppressions')
