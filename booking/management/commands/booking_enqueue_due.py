from django.core.management.base import BaseCommand
from booking.services.research_jobs import enqueue_due


class Command(BaseCommand):
    help = 'Enqueue approved public-source revisits and purge expired email excerpts; no network calls.'

    def handle(self, *args, **options):
        self.stdout.write(f'Scheduled public-source research jobs: {enqueue_due()}. No provider calls made by scheduler.')
