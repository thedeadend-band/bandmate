from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Google sync foundation, independent of Booking; no provider calls in Phase 0.'

    def handle(self, *args, **options):
        self.stdout.write('Phase 0: synced 0 Google calendars; OAuth and sync are not implemented.')
