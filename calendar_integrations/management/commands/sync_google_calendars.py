import fcntl
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from bandmate.credential_vault import VaultUnavailable
from calendar_integrations.models import CalendarGoogleAccount, ConnectedCalendar, CalendarWriteOperation
from calendar_integrations.services.google import ProviderError
from calendar_integrations.services.sync import sync_account, sync_calendar
from calendar_integrations.services.google_mutations import process_operation


class Command(BaseCommand):
    help = 'Bounded Google sync and explicitly requested event writes; independent of Booking.'

    def add_arguments(self, parser):
        parser.add_argument('--lock-file', default=str(Path(settings.BASE_DIR) / '.google-calendar-sync.lock'))

    def handle(self, *args, **options):
        with open(options['lock_file'], 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CommandError('Another Google sync process holds the lock.') from None
            limit = settings.GOOGLE_CALENDAR_BATCH_SIZE
            for account in CalendarGoogleAccount.objects.filter(is_connected=True, calendar_list_requested=True)[:limit]:
                try:
                    sync_account(account)
                    CalendarGoogleAccount.objects.filter(pk=account.pk, generation=account.generation).update(calendar_list_requested=False)
                except (ProviderError, VaultUnavailable) as exc:
                    CalendarGoogleAccount.objects.filter(pk=account.pk).update(last_error_code=getattr(exc, 'code', 'vault_unavailable'), calendar_list_requested=False)
            count = 0
            for op in CalendarWriteOperation.objects.filter(google_calendar__isnull=False,
                    status__in=['pending', 'processing']).filter(Q(retry_at__isnull=True) | Q(retry_at__lte=timezone.now())).order_by('created_at')[:limit]:
                process_operation(op)
            for calendar in ConnectedCalendar.objects.filter(is_enabled=True, account__is_connected=True).select_related('account').order_by('last_attempted_at', 'pk')[:limit]:
                ConnectedCalendar.objects.filter(pk=calendar.pk).update(last_attempted_at=timezone.now())
                try:
                    sync_calendar(calendar)
                    count += 1
                except (ProviderError, VaultUnavailable) as exc:
                    changes = {'last_error_code': getattr(exc, 'code', 'vault_unavailable')}
                    if getattr(exc, 'status', 0) in (401, 403, 404):
                        changes['write_enabled'] = False
                        # Hide stale private/provider data after access is revoked.
                        calendar.events.update(is_deleted=True)
                    ConnectedCalendar.objects.filter(pk=calendar.pk).update(**changes)
            self.stdout.write(f'Google calendars synchronized: {count}. No Booking or AI jobs executed.')
