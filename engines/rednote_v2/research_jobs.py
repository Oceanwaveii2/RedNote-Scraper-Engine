"""Durable, read-only account-library scanning in the research WebView.

Jobs keep progress separate from the captured mirror. They resume only after an
explicit request and never work around a website access gate or cooldown.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import app


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ResearchJobs:
    def __init__(self, browser):
        self.browser = browser
        self.path = Path(app.BASE_DIR) / 'research-jobs.json'
        self.lock = threading.RLock()
        self.jobs = {}
        self.stop_flags = {}
        self.workers = {}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if saved.get('schema') != 'rednote.research-jobs/1':
                raise ValueError('Unknown research checkpoint schema')
            self.jobs = {row['id']: row for row in saved.get('jobs', [])
                         if isinstance(row, dict) and isinstance(row.get('id'), str)}
            for row in self.jobs.values():
                if row.get('state') in {'running', 'pausing'}:
                    row.update(state='paused_restart', reason='App restarted; resume explicitly.', updatedAt=utc_now())
            if self.jobs:
                self._persist()
        except (OSError, ValueError, TypeError):
            # Keep unreadable job files for manual recovery; never replace them.
            if self.path.exists():
                self.jobs = {}
                self.unreadable = True
            else:
                self.unreadable = False

    def _persist(self):
        if getattr(self, 'unreadable', False):
            raise ValueError('The research job checkpoint is unreadable; recover it before starting a new scan.')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.partial')
        temporary.write_text(json.dumps({'schema': 'rednote.research-jobs/1',
                                         'jobs': list(self.jobs.values())}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)

    def _public(self, row):
        return dict(row)

    def status(self, job_id=None):
        with self.lock:
            if job_id:
                if job_id not in self.jobs:
                    raise ValueError('Unknown research job.')
                return self._public(self.jobs[job_id])
            return {'items': [self._public(row) for row in self.jobs.values()],
                    'checkpointReadable': not getattr(self, 'unreadable', False)}

    def active(self):
        with self.lock:
            return any(row.get('state') in {'running', 'pausing'} for row in self.jobs.values())

    def start(self, kind, profile_url=None):
        if getattr(self, 'unreadable', False):
            raise ValueError('The research job checkpoint is unreadable; recover it before starting a new scan.')
        if kind not in {'saved', 'favorites', 'likes'}:
            raise ValueError('Choose saved, favorites or likes.')
        if profile_url is not None:
            target = self.browser.workspace._explorer_target(profile_url)
            parsed = urlsplit(target)
            if parsed.hostname not in app.REDNOTE_HOSTS or not parsed.path.startswith('/user/profile/'):
                raise ValueError('Choose an HTTPS RedNote profile URL.')
            profile_url = parsed.scheme + '://' + parsed.netloc + parsed.path
        with self.lock:
            if any(row['state'] in {'running', 'pausing'} for row in self.jobs.values()):
                raise ValueError('Pause the current library scan before starting another.')
            job_id = uuid.uuid4().hex
            row = {'id': job_id, 'kind': kind, 'profileUrl': profile_url,
                   'accountSource': 'user_supplied_unverified' if profile_url else None,
                   'state': 'running', 'reason': '', 'cursor': 0,
                   'observedItems': 0, 'pages': 0, 'startedAt': utc_now(),
                   'updatedAt': utc_now(), 'coverage': 'normally rendered account-library items only'}
            self.jobs[job_id] = row
            self.stop_flags[job_id] = threading.Event()
            self._persist()
            self._launch(job_id)
            return self._public(row)

    def _launch(self, job_id):
        worker = threading.Thread(target=self._run, args=(job_id,), name='rednote-library-research', daemon=True)
        self.workers[job_id] = worker
        worker.start()

    def pause(self, job_id):
        with self.lock:
            row = self.jobs.get(job_id)
            if not row:
                raise ValueError('Unknown research job.')
            if row['state'] == 'running':
                row.update(state='pausing', reason='User requested pause.', updatedAt=utc_now())
                self.stop_flags[job_id].set()
                self._persist()
            return self._public(row)

    def resume(self, job_id):
        with self.lock:
            row = self.jobs.get(job_id)
            if not row:
                raise ValueError('Unknown research job.')
            if row['state'] in {'running', 'pausing'}:
                return self._public(row)
            if row['state'] == 'complete':
                raise ValueError('This scan already reached the site end marker.')
            if any(other['state'] in {'running', 'pausing'} for other in self.jobs.values()):
                raise ValueError('Another library scan is still active.')
            row.update(state='running', reason='', updatedAt=utc_now())
            self.stop_flags[job_id] = threading.Event()
            self._persist()
            self._launch(job_id)
            return self._public(row)

    def shutdown(self):
        with self.lock:
            for job_id, row in self.jobs.items():
                if row['state'] in {'running', 'pausing'}:
                    row.update(state='paused_restart', reason='App closed; resume explicitly.', updatedAt=utc_now())
                    self.stop_flags.get(job_id, threading.Event()).set()
            if self.jobs:
                self._persist()

    def _run(self, job_id):
        no_progress = 0
        while True:
            with self.lock:
                row = self.jobs[job_id]
                if self.stop_flags[job_id].is_set() or row['state'] != 'running':
                    if row['state'] == 'pausing':
                        row.update(state='paused', updatedAt=utc_now())
                        self._persist()
                    return
                kind, profile_url, cursor = row['kind'], row['profileUrl'], row['cursor']
            try:
                result = self.browser.library(kind, profile_url, cursor, 50)
            except Exception as error:
                with self.lock:
                    if self.jobs[job_id]['state'] == 'running':
                        self.jobs[job_id].update(state='paused_error', reason=type(error).__name__, updatedAt=utc_now())
                        self._persist()
                return
            with self.lock:
                row = self.jobs[job_id]
                if row['state'] != 'running':
                    continue
                if not row.get('profileUrl') and result.get('accountProfileUrl'):
                    row['profileUrl'] = result['accountProfileUrl']
                    row['accountSource'] = result.get('accountSource')
                next_cursor = result.get('nextCursor')
                page_end = cursor + len(result.get('items') or [])
                new_cursor = next_cursor if isinstance(next_cursor, int) and next_cursor >= cursor else page_end
                if new_cursor > cursor:
                    no_progress = 0
                    row.update(cursor=new_cursor, observedItems=max(row['observedItems'], new_cursor),
                               pages=row['pages'] + 1)
                else:
                    no_progress += 1
                state = result.get('state')
                if state == 'complete':
                    row.update(state='complete', reason='Site end marker observed.')
                elif state in {'paused_needs_attention', 'needs_profile_url', 'unavailable'}:
                    row.update(state=state, reason=str(result.get('reason') or result.get('detail') or state))
                elif result.get('stalled') or no_progress >= 3:
                    row.update(state='paused_no_progress', reason='No new normally rendered items; completeness is unknown.')
                row['updatedAt'] = utc_now()
                self._persist()
                if row['state'] != 'running':
                    return
