"""Recover committed WAL transactions before caching the research SQLite file."""
from pathlib import Path
import sqlite3


def checkpoint(path):
    path = Path(path).resolve()
    if not path.exists():
        return
    conn = sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=30)
    try:
        conn.execute('PRAGMA busy_timeout=30000')
        result = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if result[0]:
            raise RuntimeError('Research database is still busy; refusing an incomplete cache snapshot')
        if conn.execute('PRAGMA quick_check').fetchone()[0]!='ok':
            raise RuntimeError('Research database integrity check failed; refusing cache update')
    finally:
        conn.close()


if __name__=='__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=Path(__file__).resolve().parents[1]/'data/msac.sqlite3')
    checkpoint(parser.parse_args().db)
