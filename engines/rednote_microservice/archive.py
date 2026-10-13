"""Read-only archive adapter for previews against an already-owned library."""
import contextlib
from pathlib import Path
import sqlite3
import threading
from rednote_v2.store import Store


class ReadOnlyArchive(Store):
    def __init__(self, base):
        self.base=Path(base).resolve()
        self.root=self.base/'data_v2'
        self.path=self.root/'rednote-v2.sqlite'
        if not self.path.is_file():raise ValueError('Archive database not found')
        self.lock=threading.RLock()
        self.sync_lock=threading.Lock()
        self.stop=threading.Event()

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=10)
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            try:yield db
            finally:db.close()

    def event(self,*args,**kwargs):
        # Preview diagnostics belong in the new build log, never the old DB.
        return None
