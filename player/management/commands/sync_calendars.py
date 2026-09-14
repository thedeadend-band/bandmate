from django.core.management.base import BaseCommand

from player.calendar_sync import sync_due_calendar_sources


class Command(BaseCommand):
    help = 'Fetch configured iCal/ICS calendar sources into the local cache.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Sync all enabled calendar sources regardless of interval.',
        )

    def handle(self, *args, **options):
        results = sync_due_calendar_sources(force=options['force'])
        if not results:
            self.stdout.write('No calendar sources due for sync.')
            return
        for source_id, message in results.items():
            self.stdout.write(f'{source_id}: {message}')
