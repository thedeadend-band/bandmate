"""SQLite-safe reservations for inference handlers. No provider calls here."""
from decimal import Decimal, InvalidOperation
from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum, Case, When, DecimalField
from django.utils import timezone
from booking.models import BookingSettings, BookingOpenAICredential, BookingBudgetReservation, BookingAIUsage


class BudgetPaused(ValueError):
    pass


def amount(value):
    try:
        value = Decimal(str(value))
        if not value.is_finite() or value < 0 or value >= Decimal('1000000'):
            raise ValueError
        # Reject excess precision instead of rounding a reservation down.
        if value != value.quantize(Decimal('.000001')):
            raise ValueError
        return value
    except (ValueError, InvalidOperation):
        raise BudgetPaused('invalid_cost') from None


def locked_config(group):
    # First statement is a write, avoiding deferred SQLite read->write upgrade
    # races. Also serializes budget mutations on row-locking databases.
    if not BookingSettings.objects.filter(group=group).update(updated_at=F('updated_at')):
        raise BudgetPaused('booking_disabled')
    config = BookingSettings.objects.get(group=group)
    if not config.enabled or not config.ai_enabled:
        raise BudgetPaused('ai_disabled')
    return config


def check_allowance(group, config, model, extra_cost=Decimal(0), extra_requests=0):
    today = timezone.now().date()  # UTC periods, independent of server/user TZ.
    credential = BookingOpenAICredential.objects.filter(group=group, test_status='succeeded').exclude(key_ciphertext='').first()
    if not credential or model not in settings.BOOKING_OPENAI_MODELS or model != config.ai_model or model not in credential.available_models:
        raise BudgetPaused('model_or_credential_unavailable')
    rows = BookingBudgetReservation.objects.filter(group=group).exclude(status='released')
    cost = rows.filter(period=today.replace(day=1)).aggregate(total=Sum(Case(
        When(status='settled', then=F('charged_usd')), default=F('authorized_usd'),
        output_field=DecimalField(max_digits=12, decimal_places=6))))['total'] or Decimal(0)
    if config.monthly_budget_usd <= 0 or cost + extra_cost > config.monthly_budget_usd:
        raise BudgetPaused('monthly_budget_paused')
    if config.daily_request_limit <= 0 or rows.filter(day=today).count() + extra_requests > config.daily_request_limit:
        raise BudgetPaused('daily_budget_paused')
    return credential, today


@transaction.atomic
def reserve(group, job_key, model, maximum_usd):
    maximum_usd = amount(maximum_usd)
    config = locked_config(group)
    existing = BookingBudgetReservation.objects.filter(group=group, job_key=job_key).first()
    if existing:
        if existing.model != model or existing.authorized_usd != maximum_usd:
            raise BudgetPaused('reservation_mismatch')
        return existing
    credential, today = check_allowance(group, config, model, maximum_usd, 1)
    return BookingBudgetReservation.objects.create(group=group, job_key=job_key, model=model,
        credential_generation=credential.generation, authorized_usd=maximum_usd,
        key_fingerprint=credential.fingerprint,
        day=today, period=today.replace(day=1))


@transaction.atomic
def begin(group, job_key):
    config = locked_config(group)
    row = BookingBudgetReservation.objects.get(group=group, job_key=job_key)
    if row.status != 'reserved':
        raise BudgetPaused('reservation_already_started')
    credential, today = check_allowance(group, config, row.model)
    if row.day != today or row.credential_generation != credential.generation:
        raise BudgetPaused('reservation_expired')
    row.status = 'started'
    row.save(update_fields=['status'])
    # Handler must recheck actor permissions immediately before the remote call;
    # never hold this transaction across network I/O. Uncertain calls stay charged.
    return row


@transaction.atomic
def release(group, job_key):
    BookingSettings.objects.filter(group=group).update(updated_at=F('updated_at'))
    return BookingBudgetReservation.objects.filter(group=group, job_key=job_key, status='reserved').update(status='released')


@transaction.atomic
def settle(group, job_key, actual_usd, *, input_tokens, output_tokens, pricing_version, result):
    actual_usd = amount(actual_usd)
    if input_tokens < 0 or output_tokens < 0 or not pricing_version:
        raise BudgetPaused('invalid_usage')
    BookingSettings.objects.filter(group=group).update(updated_at=F('updated_at'))
    row = BookingBudgetReservation.objects.get(group=group, job_key=job_key)
    if row.status == 'settled':
        usage = BookingAIUsage.objects.get(group=group, reservation=row)
        if (usage.estimated_usd, usage.input_tokens, usage.output_tokens, usage.pricing_version, usage.result) != (
                actual_usd, input_tokens, output_tokens, pricing_version, result):
            raise BudgetPaused('usage_mismatch')
        return usage
    if row.status != 'started':
        raise BudgetPaused('reservation_not_started')
    row.status, row.charged_usd = 'settled', actual_usd
    row.save(update_fields=['status', 'charged_usd'])
    return BookingAIUsage.objects.create(group=group, reservation=row, task_type='inference', model=row.model,
        key_fingerprint=row.key_fingerprint, input_tokens=input_tokens,
        output_tokens=output_tokens, estimated_usd=actual_usd, pricing_version=pricing_version, result=result)
