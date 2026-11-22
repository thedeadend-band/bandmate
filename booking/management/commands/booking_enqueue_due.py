from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Scheduler foundation; no external jobs are enqueued in Phase 0.'

    def handle(self, *args, **options):
        self.stdout.write('Phase 0: scheduled 0 jobs; external integrations are unavailable.')
