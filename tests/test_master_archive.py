"""Archive UI routes use a temporary data root and never seed the real library."""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask


class MasterArchiveRoutesTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        fake = types.ModuleType('app')
        fake.BASE_DIR = self.base
        module_path = Path(__file__).resolve().parents[1] / 'master_service.py'
        spec = importlib.util.spec_from_file_location('master_archive_test_service', module_path)
        self.service = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'app': fake}):
            spec.loader.exec_module(self.service)
        application = Flask(__name__)
        application.register_blueprint(self.service.master_bp)
        self.client = application.test_client()
        self.profile = '6082a828000000000101ef8d'
        self.service.archive_store.store_profile({'profileId': self.profile, 'name': 'Captured author', 'posts': [{'postId': 'note'}]})
        video = self.base / 'clone_media' / self.profile / 'note' / 'video.mp4'
        video.parent.mkdir(parents=True)
        video.write_bytes(b'local video bytes')
        self.service.archive_store.store_post(self.profile, {'postId': 'note', 'title': 'Captured note', 'reportedComments': 2, 'comments': [{'id': 'root', 'text': 'Root', 'replies': [{'id': 'reply', 'text': 'Reply'}]}]}, [{'kind': 'video', 'file': video.relative_to(self.base).as_posix()}], {'status': 'partial'})

    def test_profile_scope_and_selection_context(self):
        self.service.archive_store.store_profile({'profileId': 'other', 'name': 'Other author'})
        self.service.archive_store.store_post('other', {'postId': 'othernote', 'comments': []}, [], {})
        response = self.client.post('/api/master/selection', json={'profileId': self.profile, 'postIds': ['note', 'othernote'], 'excludedPostIds': []})
        self.assertEqual(['note'], response.json['selection']['postIds'])
        posts = self.client.get('/api/master/posts').json['posts']
        self.assertEqual(['note'], [post['post_id'] for post in posts])
        pack = self.client.get('/api/master/context-pack').json['pack']
        self.assertEqual(1, pack['includedNotesCount'])
        self.assertEqual(2, pack['totalCommentsCount'])
        self.assertEqual('root', pack['posts'][0]['comments'][1]['parentId'])

    def test_media_and_hosted_offline_html_resolve_contained_local_files(self):
        detail = self.client.get('/api/master/posts/note').json
        url = detail['media'][0]['url']
        response = self.client.get(url)
        self.assertEqual(200, response.status_code)
        self.assertEqual(b'local video bytes', response.data)
        response.close()
        for url in ['/api/master/media/rednote.sqlite', '/api/master/media/../../rednote.sqlite', '/api/master/media/https://cdn.example/video.mp4']:
            self.assertEqual(404, self.client.get(url).status_code)
        offline = self.client.get('/api/master/archive/' + self.profile + '/posts/note.html')
        self.assertEqual(200, offline.status_code)
        self.assertIn('/api/master/media/clone_media/', offline.get_data(as_text=True))
        self.assertNotIn('src="../../../', offline.get_data(as_text=True))
        offline.close()
        self.assertEqual(404, self.client.get('/api/master/archive/' + self.profile + '/../rednote.sqlite').status_code)

    def test_pending_notes_are_browsable_before_capture(self):
        self.service.archive_store.store_profile({'profileId': self.profile, 'name': 'Captured author', 'posts': [{'postId': 'pending', 'title': 'Discovered'}]})
        response = self.client.get('/api/master/posts/pending')
        self.assertEqual(200, response.status_code)
        self.assertEqual('pending', response.json['coverage']['state'])
        self.assertEqual([], response.json['media'])

    def test_media_range_seeking_returns_inline_partial_response(self):
        url = self.client.get('/api/master/posts/note').json['media'][0]['url']
        response = self.client.get(url, headers={'Range': 'bytes=6-10'})
        self.assertEqual(206, response.status_code)
        self.assertEqual(b'video', response.data)
        self.assertEqual('bytes 6-10/17', response.headers['Content-Range'])
        self.assertEqual('bytes', response.headers['Accept-Ranges'])
        self.assertTrue(response.headers['Content-Type'].startswith('video/mp4'))
        self.assertTrue(response.headers['Content-Disposition'].startswith('inline;'))
        response.close()

    def test_hosted_video_poster_and_all_gallery_items_resolve_local_routes(self):
        store = self.service.archive_store
        media = self.client.get('/api/master/posts/note').json['media']
        for kind, index, filename in [('cover', 0, 'cover.jpg'), ('photo', 0, 'a.jpg'), ('photo', 1, 'b.jpg')]:
            path = self.base / 'clone_media' / self.profile / 'note' / filename
            path.write_bytes(b'local image bytes')
            media.append({'kind': kind, 'sourceIndex': index, 'file': path.relative_to(self.base).as_posix()})
        store.store_post(self.profile, {'postId': 'note'}, media, {})
        response = self.client.get('/api/master/archive/' + self.profile + '/posts/note.html')
        markup = response.get_data(as_text=True)
        self.assertIn('poster="/api/master/media/clone_media/', markup)
        import re
        urls = re.findall(r'(?:src|poster)="([^"]+)"', markup)
        self.assertEqual(4, len(urls))
        for url in urls:
            asset = self.client.get(url)
            self.assertEqual(200, asset.status_code)
            asset.close()
        response.close()

    def test_attachment_only_comments_are_exposed_as_empty_text_plus_metadata(self):
        self.service.archive_store.store_post(self.profile, {'postId': 'note', 'comments': [{'id': 'emoji', 'parentId': 'root', 'text': '', 'attachments': [{'kind': 'emoji', 'sourceIndex': 1, 'sourceUrl': 'https://cdn.example/emoji.png', 'archiveStatus': 'remote_only'}]}]}, [], {})
        detail = self.client.get('/api/master/posts/note').json
        reply = next(c for c in detail['comments'] if c['comment_id'] == 'emoji')
        self.assertEqual('', reply['text_chinese'])
        self.assertEqual('root', reply['parent_comment_id'])
        self.assertEqual('emoji', reply['attachments'][0]['kind'])
        self.assertEqual('', reply['attachments'][0]['url'])
        self.assertEqual(3, len(detail['comments']))


if __name__ == '__main__':
    unittest.main()
