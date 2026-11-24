from django.core.management.base import BaseCommand
from booking.models import BookingSettings


class Command(BaseCommand):
    help = 'Report foundation status; never print credentials or make remote calls.'

    def handle(self, *args, **options):
        count = BookingSettings.objects.filter(enabled=True).count()
        self.stdout.write(f'Booking enabled bands: {count}')
        self.stdout.write('Connection checks and approved research/import jobs are available. AI extraction requires separate consent, configured pricing and band budget. Email sending is not implemented.')
