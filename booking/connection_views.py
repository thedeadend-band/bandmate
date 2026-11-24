import hashlib
import uuid
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import F, Sum, Count, Case, When, DecimalField
from django.db.models.functions import TruncDate
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from bandmate.credential_vault import seal, VaultUnavailable
from player.group_permissions import audit
from .forms import OpenAIKeyForm, AIPolicyForm
from .models import BookingSettings, BookingMembershipRole, BookingOpenAICredential, BookingGmailConnection, BookingJob, BookingAIUsage, BookingBudgetReservation
from .permissions import require_booking
from .services.jobs import enqueue


def manager(request):
    return require_booking(request.user, request.active_group, [BookingMembershipRole.Role.MANAGER])


@login_required
@require_http_methods(['GET', 'POST'])
@sensitive_post_parameters('api_key')
@sensitive_variables()
def connections(request):
    manager(request)
    group = request.active_group
    config = BookingSettings.objects.get(group=group)
    key_form, policy_form = OpenAIKeyForm(), AIPolicyForm(instance=config)
    error = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'key':
            key_form = OpenAIKeyForm(request.POST)
            if key_form.is_valid():
                try:
                    key = key_form.cleaned_data['api_key']
                    ciphertext = seal(group.pk, 'booking-openai', key)
                    with transaction.atomic():
                        BookingSettings.objects.filter(group=group).update(updated_at=F('updated_at'))
                        manager(request)
                        credential, _ = BookingOpenAICredential.objects.get_or_create(group=group)
                        credential.key_ciphertext = ciphertext
                        credential.fingerprint = hashlib.sha256(key.encode()).hexdigest()
                        credential.project_label = key_form.cleaned_data['project_label']
                        credential.project_id = key_form.cleaned_data['project_id']
                        credential.generation += 1
                        credential.tested_at = None
                        credential.test_status = 'untested'
                        credential.available_models = []
                        credential.save()
                        audit(group, request.user, 'booking.openai.key_updated', credential.pk)
                    return redirect('booking:connections')
                except VaultUnavailable:
                    error = 'The credential vault must be configured before storing a key.'
        elif action == 'policy':
            policy_form = AIPolicyForm(request.POST, instance=config)
            if policy_form.is_valid():
                with transaction.atomic():
                    manager(request)
                    BookingSettings.objects.filter(pk=config.pk).update(**{
                        field: policy_form.cleaned_data[field] for field in policy_form.Meta.fields})
                    audit(group, request.user, 'booking.ai_policy.updated')
                return redirect('booking:connections')
        else:
            return HttpResponseBadRequest('Choose a connection action.')
    credential = BookingOpenAICredential.objects.filter(group=group).first()
    # Only a short hash fragment is displayed; neither plaintext nor ciphertext.
    return render(request, 'booking/connections.html', {'nav_active': 'booking',
        'key_form': key_form, 'policy_form': policy_form, 'error': error,
        'credential': credential, 'fingerprint': credential.fingerprint[:12] if credential else '',
        'gmail': BookingGmailConnection.objects.filter(group=group).first(),
        'gmail_send_enabled': any('https://www.googleapis.com/auth/gmail.send' in row.granted_scopes
            for row in BookingGmailConnection.objects.filter(group=group, is_connected=True)),
        'jobs': BookingJob.objects.filter(group=group).order_by('-created_at')[:10],
        'openai_test_key': uuid.uuid4(), 'gmail_test_key': uuid.uuid4()})


@login_required
@require_POST
def remove_key(request):
    manager(request)
    with transaction.atomic():
        BookingSettings.objects.filter(group=request.active_group).update(ai_enabled=False)
        manager(request)
        BookingOpenAICredential.objects.filter(group=request.active_group).update(key_ciphertext='',
            fingerprint='', project_label='', project_id='', generation=F('generation') + 1,
            test_status='removed', tested_at=None, available_models=[])
        audit(request.active_group, request.user, 'booking.openai.key_removed')
    return redirect('booking:connections')


@login_required
@require_POST
def test_connection(request, provider):
    manager(request)
    try:
        key = uuid.UUID(request.POST.get('operation_key', ''))
    except (ValueError, AttributeError):
        return HttpResponseBadRequest('A valid request identifier is required.')
    if provider == 'openai':
        credential = get_object_or_404(BookingOpenAICredential.objects.exclude(key_ciphertext=''), group=request.active_group)
        kind = BookingJob.Kind.OPENAI_TEST
    elif provider == 'gmail':
        credential = get_object_or_404(BookingGmailConnection, group=request.active_group, is_connected=True)
        kind = BookingJob.Kind.GMAIL_TEST
    else:
        return HttpResponseBadRequest('Unknown provider.')
    try:
        enqueue(request.active_group, request.user, kind, key, credential.generation)
    except ValueError:
        return HttpResponseBadRequest('This request identifier has already been used.')
    return redirect('booking:connections')


@login_required
@require_GET
def usage(request):
    require_booking(request.user, request.active_group)
    now = timezone.now()
    rows = BookingAIUsage.objects.filter(group=request.active_group)
    windows = []
    for label, start in [('Last 24 hours', now - timedelta(days=1)), ('Last 7 days', now - timedelta(days=7)),
                         ('Last 30 days', now - timedelta(days=30)),
                         ('This month (UTC)', now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))]:
        totals = rows.filter(created_at__gte=start).aggregate(requests=Count('id'), cost=Sum('estimated_usd'),
            input_tokens=Sum('input_tokens'), output_tokens=Sum('output_tokens'))
        windows.append({'label': label, **totals})
    month = now.date().replace(day=1)
    committed = BookingBudgetReservation.objects.filter(group=request.active_group, period=month).exclude(status='released').aggregate(
        total=Sum(Case(When(status='settled', then=F('charged_usd')), default=F('authorized_usd'),
            output_field=DecimalField(max_digits=12, decimal_places=6))))['total'] or 0
    config = BookingSettings.objects.get(group=request.active_group)
    daily = list(rows.filter(created_at__gte=now-timedelta(days=30)).annotate(day=TruncDate('created_at')).values('day').annotate(cost=Sum('estimated_usd')).order_by('day'))
    peak = max([float(row['cost']) for row in daily] or [0]) or 1
    for row in daily:
        row['percent'] = float(row['cost']) / peak * 100
    return render(request, 'booking/usage.html', {'nav_active': 'booking', 'windows': windows,
        'models': rows.values('model').annotate(requests=Count('pk'), cost=Sum('estimated_usd')).order_by('model'),
        'categories': rows.values('task_type').annotate(requests=Count('pk'), cost=Sum('estimated_usd')).order_by('task_type'),
        'daily': daily, 'remaining': max(0, config.monthly_budget_usd-committed),
        'jobs': BookingJob.objects.filter(group=request.active_group).order_by('-created_at')[:100],
        'entries': rows.order_by('-created_at')[:50],
        'reserved': BookingBudgetReservation.objects.filter(group=request.active_group,
            status__in=['reserved', 'started']).aggregate(total=Sum('authorized_usd'))['total'] or 0})


@login_required
@require_GET
def usage_export(request):
    require_booking(request.user, request.active_group)
    response = JsonResponse({'usage': list(BookingAIUsage.objects.filter(group=request.active_group).values(
        'created_at', 'task_type', 'model', 'input_tokens', 'output_tokens', 'estimated_usd', 'pricing_version', 'result')),
        'jobs': list(BookingJob.objects.filter(group=request.active_group).values(
            'id', 'kind', 'status', 'attempts', 'error_code', 'created_at', 'finished_at'))})
    response['Content-Disposition'] = 'attachment; filename="booking-usage.json"'
    response['Cache-Control'] = 'no-store'
    return response
