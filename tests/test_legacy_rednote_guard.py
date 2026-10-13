"""Exercise real route/worker source in isolation; no app import or network."""
import ast
import json
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

from flask import Flask, jsonify, request


class LegacyRedNoteGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.busy = False
        self.cooldown = None
        self.lock_checks = []
        self.workspace = SimpleNamespace(_profile_batch_lock=threading.Lock())
        self.workspace._online_cooldown_error = lambda: self.cooldown
        def busy():
            self.lock_checks.append(self.workspace._profile_batch_lock.locked())
            return self.busy
        self.workspace._clone_busy = busy
        self.flask = Flask(__name__)
        self.flask.browser_workspace = self.workspace
        self.env = {
            'app': self.flask, 'BASE_DIR': self.base, 'DOWNLOAD_DIR': self.base,
            'Path': Path, 'json': json, 'time': time, 'dataclass': dataclass,
            'field': field, 'contextmanager': contextmanager, 'uuid': uuid,
            'request': request, 'jsonify': jsonify, 'urlparse': urlparse,
            'threading': SimpleNamespace(Thread=Mock()), 'jobs': {},
            'jobs_lock': threading.Lock(), 'worker_lock': threading.Lock(),
            'capture_process_guard': lambda: nullcontext(),
            'reserve_job_slot': Mock(), 'cleanup_old_jobs': Mock(),
            'normalized_source_url': lambda url: ('instagram' if 'instagram.com' in url else 'xiaohongshu', url),
            'validate_comment_import': lambda *args: None,
            'note_id_from_url': lambda url: None, 'folder_tokens': {'folder': self.base},
            'YoutubeDL': Mock(side_effect=RuntimeError('Synthetic downloader stop')),
            'resolve_rednote_share_url': Mock(return_value='https://www.rednote.com/explore/' + 'a' * 24),
            'redact_query_urls': lambda text: text,
        }
        tree = ast.parse((Path(__file__).parents[1] / 'app.py').read_text(encoding='utf-8-sig'))
        names = {'Job', 'rednote_download_block_reason', 'rednote_download_queue_guard', 'run_job', 'create_job', 'bridge_download'}
        tree.body = [node for node in tree.body if getattr(node, 'name', '') in names]
        exec(compile(tree, 'isolated-app.py', 'exec'), self.env)
        self.client = self.flask.test_client()

    def payload(self, url=None):
        return {'authorized': True, 'url': url or 'https://www.rednote.com/explore/' + 'a' * 24,
                'mediaType': 'photo', 'folderToken': 'folder'}

    def job(self, platform='xiaohongshu', url=None):
        return self.env['Job'](id=uuid.uuid4().hex, url=url or self.payload()['url'], platform=platform, media_type='photo')

    def run_job(self, job):
        self.env['run_job'](job, self.base, None)

    def test_both_routes_reject_busy_without_queue_or_thread(self):
        self.busy = True
        for path in ('/api/jobs', '/api/bridge/download'):
            response = self.client.post(path, json=self.payload())
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.env['jobs'], {})
        self.env['threading'].Thread.assert_not_called()
        self.env['cleanup_old_jobs'].assert_not_called()
        self.assertEqual(self.lock_checks, [True, True])
        self.assertFalse(self.workspace._profile_batch_lock.locked())

    def test_both_routes_reject_cooldown(self):
        self.cooldown = {'error': 'The website cooldown is active.'}
        for path in ('/api/jobs', '/api/bridge/download'):
            response = self.client.post(path, json=self.payload())
            self.assertEqual(response.status_code, 409)
            self.assertIn('cooldown', response.json['error'])
        self.assertEqual(self.env['jobs'], {})
        self.assertFalse(self.workspace._profile_batch_lock.locked())

    def test_ready_routes_assign_jobs_under_shared_queue_lock(self):
        for path in ('/api/jobs', '/api/bridge/download'):
            self.assertEqual(self.client.post(path, json=self.payload()).status_code, 202)
        self.assertEqual(len(self.env['jobs']), 2)
        self.assertEqual(self.lock_checks, [True, True])
        self.assertEqual(self.env['threading'].Thread.return_value.start.call_count, 2)

    def test_other_platform_queue_ignores_rednote_guard(self):
        self.busy = True
        self.cooldown = {'error': 'The website cooldown is active.'}
        response = self.client.post('/api/jobs', json=self.payload('https://www.instagram.com/p/test/'))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.lock_checks, [])

    def test_worker_blocked_before_pacing_or_network(self):
        self.busy = True
        job = self.job()
        self.run_job(job)
        self.assertEqual((job.state, job.phase), ('failed', 'failed'))
        self.assertIn('profile clone', job.error)
        self.env['reserve_job_slot'].assert_not_called()
        self.env['YoutubeDL'].assert_not_called()
        self.assertIsNone(job.started_at)

    def test_worker_rechecks_busy_after_pacing(self):
        self.env['reserve_job_slot'].side_effect = lambda job: setattr(self, 'busy', True)
        job = self.job()
        self.run_job(job)
        self.assertEqual((job.state, job.phase), ('failed', 'failed'))
        self.assertIn('profile clone', job.error)
        self.env['YoutubeDL'].assert_not_called()
        self.env['resolve_rednote_share_url'].assert_not_called()

    def test_worker_rechecks_cooldown_after_pacing(self):
        self.env['reserve_job_slot'].side_effect = lambda job: setattr(self, 'cooldown', {'error': 'Website cooldown active'})
        job = self.job()
        self.run_job(job)
        self.assertEqual(job.state, 'failed')
        self.assertIn('cooldown', job.error)
        self.env['YoutubeDL'].assert_not_called()

    def test_share_resolution_rechecked_before_extractor(self):
        def resolve(url):
            self.busy = True
            return self.payload()['url']
        self.env['resolve_rednote_share_url'].side_effect = resolve
        job = self.job(url='https://xhslink.com/a/test')
        self.run_job(job)
        self.env['resolve_rednote_share_url'].assert_called_once()
        self.env['YoutubeDL'].assert_not_called()
        self.assertEqual(job.state, 'failed')

    def test_other_platform_worker_reaches_mock_downloader(self):
        self.busy = True
        job = self.job(platform='threads')
        self.run_job(job)
        self.env['YoutubeDL'].assert_called_once()
        self.assertEqual(job.error, 'Synthetic downloader stop')
        self.assertEqual(self.lock_checks, [])

    def test_standalone_server_honors_persisted_pause_and_expiry(self):
        del self.flask.browser_workspace
        queue = self.base / 'clone_queue.json'
        queue.write_text(json.dumps({'safety': {'siteCooldownUntil': time.time() + 900}, 'jobs': []}))
        self.assertIn('cooldown', self.env['rednote_download_block_reason']('xiaohongshu'))
        queue.write_text(json.dumps({'safety': {'siteCooldownUntil': 0}, 'jobs': []}))
        self.assertIsNone(self.env['rednote_download_block_reason']('xiaohongshu'))

    def test_unreadable_pause_or_workspace_state_fails_closed(self):
        self.workspace._online_cooldown_error = Mock(side_effect=RuntimeError('private details'))
        result = self.env['rednote_download_block_reason']('xiaohongshu')
        self.assertIn('could not be checked', result)
        self.assertNotIn('private details', result)
        del self.flask.browser_workspace
        (self.base / 'clone_queue.json').write_text('invalid json')
        self.assertIn('could not be checked', self.env['rednote_download_block_reason']('xiaohongshu'))


if __name__ == '__main__':
    unittest.main()
