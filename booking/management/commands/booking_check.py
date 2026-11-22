from django.core.management.base import BaseCommand
from booking.models import BookingSettings


class Command(BaseCommand):
    help = 'Report foundation status; never print credentials or make remote calls.'

    def handle(self, *args, **options):
        count = BookingSettings.objects.filter(enabled=True).count()
        self.stdout.write(f'Booking enabled bands: {count}')
        self.stdout.write('Phase 0: external integrations and job execution are not implemented.')
