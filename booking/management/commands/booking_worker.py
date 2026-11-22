import fcntl
from pathlib import Path
import signal
import threading

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Singleton worker foundation; idles without external work in Phase 0.'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Check startup and exit.')
        parser.add_argument('--lock-file', default=str(Path(settings.BASE_DIR) / '.booking-worker.lock'))

    def handle(self, *args, **options):
        with open(options['lock_file'], 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CommandError('Another Booking worker holds the lock.') from exc
            self.stdout.write('Phase 0 worker: idle; no external task handlers are installed.')
            if options['once']:
                return
            stopping = threading.Event()
            previous = {}
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    previous[sig] = signal.signal(sig, lambda *_: stopping.set())
                while not stopping.wait(30):
                    pass
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
