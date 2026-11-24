from django.core.management.base import BaseCommand
from booking.models import BookingSettings


class Command(BaseCommand):
    help = 'Report foundation status; never print credentials or make remote calls.'

    def handle(self, *args, **options):
        count = BookingSettings.objects.filter(enabled=True).count()
        self.stdout.write(f'Booking enabled bands: {count}')
        self.stdout.write('Connection checks, approved research/import, reviewed outreach and conversation checks are available. AI requires separate consent, configured pricing and band budget. Sending requires explicit manager approval and Gmail send authorization.')
