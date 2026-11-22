import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import tempfile

from django.conf import settings
from django.test import SimpleTestCase

from ops.backup_sqlite import backup


class OperationsTests(SimpleTestCase):
    def test_sqlite_backup_integrity_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / 'source.db', Path(root) / 'backup.db'
            with sqlite3.connect(source) as db:
                db.execute('CREATE TABLE sample (value TEXT)')
                db.execute("INSERT INTO sample VALUES ('retained')")
            backup(source, target)
            with sqlite3.connect(target) as db:
                self.assertEqual(db.execute('SELECT value FROM sample').fetchone(), ('retained',))
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                backup(source, target)
            with self.assertRaises(sqlite3.OperationalError):
                backup(Path(root) / 'missing.db', Path(root) / 'empty.db')
            self.assertFalse((Path(root) / 'missing.db').exists())

    def test_unit_rendering_is_idempotent_and_does_not_start_services(self):
        user = pwd.getpwuid(os.getuid()).pw_name
        if user == 'root':
            user = 'nobody'
        with tempfile.TemporaryDirectory() as root:
            env = dict(os.environ, BANDMATE_UNIT_DIR=root, BANDMATE_SERVICE_USER=user)
            script = str(settings.BASE_DIR / 'ops/install-feature-units.sh')
            for _ in range(2):
                result = subprocess.run(['bash', script], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('no services started', result.stdout)
            units = list(Path(root).iterdir())
            self.assertEqual(len(units), 5)
            self.assertNotIn('@APP_DIR@', ''.join(p.read_text() for p in units))
            self.assertIn('manage.py sync_google_calendars',
                          (Path(root) / 'bandmate-google-calendar-sync.service').read_text())
            worker = Path(root) / 'booking-worker.service'
            worker.write_text('operator customization')
            result = subprocess.run(['bash', script], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(worker.read_text(), 'operator customization')

    def test_updater_failure_boundaries_and_service_restore_with_fake_host(self):
        """Never run host/systemd/package/git operations: all are local fakes."""
        for failure in ['', 'dirty', 'backup', 'pip', 'migration', 'check', 'static']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root_string:
                root = Path(root_string)
                app, bins, backups = root / 'app', root / 'bin', root / 'backups'
                (app / '.git').mkdir(parents=True)
                (app / '.venv/bin').mkdir(parents=True)
                (app / 'ops').mkdir()
                bins.mkdir()
                (app / '.env').write_text('DJANGO_DEBUG=False\n')
                with sqlite3.connect(app / 'db.sqlite3') as db:
                    db.execute('CREATE TABLE preserved (id INTEGER)')
                    db.execute('INSERT INTO preserved VALUES (7)')
                shutil.copy(settings.BASE_DIR / 'ops/backup_sqlite.py', app / 'ops/backup_sqlite.py')
                updater = (settings.BASE_DIR / 'update-service.sh').read_text()
                updater = updater.replace('APP_DIR="/srv/bandmate"', f'APP_DIR="{app}"')
                updater = updater.replace('BACKUP_ROOT="/var/backups/bandmate"', f'BACKUP_ROOT="{backups}"')
                updater = updater.replace('/run/lock/bandmate-update.lock', str(root / 'update.lock'))
                script = app / 'update-service.sh'
                script.write_text(updater)
                fake = bins / 'fake'
                fake.write_text('''#!/usr/bin/env python3
import os, pathlib, subprocess, sys
root = pathlib.Path(os.environ['FAKE_ROOT'])
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
fail = os.environ['FAKE_FAILURE']
with (root / 'calls').open('a') as log:
    log.write(name + ' ' + ' '.join(args) + '\\n')
changed = (root / 'changed').exists()
if name == 'id': print('0')
elif name == 'git':
    if args[0] == 'status' and fail == 'dirty': print(' M user-work.py')
    if args[0] == 'rev-parse': print(('b' if changed else 'a') * 40)
    if args[0] == 'merge': (root / 'changed').touch()
elif name == 'systemctl':
    if args[0] == 'show': print('loaded')
    if args[0] == 'is-active': sys.exit(0 if args[-1] in ['bandmate.service', 'bandmate-google-calendar-sync.timer'] else 3)
elif name == 'pip' and fail == 'pip': sys.exit(1)
elif name == 'python':
    if args[0].endswith('backup_sqlite.py'):
        if fail == 'backup': sys.exit(1)
        sys.exit(subprocess.call([sys.executable] + args))
    if changed and fail == 'migration' and 'migrate' in args and '--noinput' in args: sys.exit(1)
    if changed and fail == 'check' and 'check' in args: sys.exit(1)
    if changed and fail == 'static' and 'collectstatic' in args: sys.exit(1)
''')
                fake.chmod(0o755)
                for name in ['id', 'git', 'systemctl', 'flock', 'rubberband']:
                    (bins / name).symlink_to(fake)
                for name in ['python', 'pip']:
                    (app / '.venv/bin' / name).symlink_to(fake)
                env = dict(os.environ, PATH=str(bins) + os.pathsep + os.environ['PATH'],
                           FAKE_ROOT=str(root), FAKE_FAILURE=failure)
                result = subprocess.run(['bash', str(script), 'b' * 40], env=env,
                                        capture_output=True, text=True)
                calls = (root / 'calls').read_text()
                self.assertEqual(result.returncode == 0, not failure, result.stderr)
                if failure:
                    self.assertNotIn('systemctl start ', calls)
                else:
                    self.assertIn('systemctl start bandmate.service', calls)
                    self.assertIn('systemctl start bandmate-google-calendar-sync.timer', calls)
                    self.assertNotIn('systemctl start booking-worker', calls)
                if failure in ['dirty', 'backup']:
                    self.assertNotIn('git merge --ff-only', calls)
                else:
                    snapshot = next(backups.glob('*/db.sqlite3'))
                    with sqlite3.connect(snapshot) as db:
                        self.assertEqual(db.execute('SELECT id FROM preserved').fetchone(), (7,))
