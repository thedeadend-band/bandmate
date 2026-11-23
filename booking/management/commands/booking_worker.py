import fcntl
from pathlib import Path
import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from booking.services.jobs import process_batch


class Command(BaseCommand):
    help = 'Singleton Booking worker for approved connection checks and research.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process one bounded batch and exit.')
        parser.add_argument('--lock-file', default=str(Path(settings.BASE_DIR) / '.booking-worker.lock'))

    def handle(self, *args, **options):
        with open(options['lock_file'], 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError('Another Booking worker holds the lock.') from exc
            self.stdout.write(f'Booking jobs processed: {process_batch()}')
            if options['once']:
                return
            stopping = threading.Event()
            previous = {}
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous[sig] = signal.signal(sig, lambda *_: stopping.set())
                while not stopping.wait(30):
                    process_batch()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
