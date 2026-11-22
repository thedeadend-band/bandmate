"""Consistent, exclusive, private SQLite backups. Does not restore or overwrite."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    # Read-only prevents accidentally creating an empty 'source' on typo.
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
        if original.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Source database integrity check failed.')
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            if copied.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                raise ValueError('Backup integrity check failed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('destination')
    args = parser.parse_args()
    backup(args.source, args.destination)
    print('SQLite backup completed and integrity verified.')
