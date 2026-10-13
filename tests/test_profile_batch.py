import json
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import app as harvest_app
from desktop_bridge import Workspace


PROFILE_ID = '643d335e000000000d01942b'
POST_ID = '6a33a39d000000000f031452'


class ProfileBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_download_dir = harvest_app.DOWNLOAD_DIR
        harvest_app.DOWNLOAD_DIR = Path(self.temp.name)
        self.workspace = Workspace()
        self.workspace._source = Mock()
        self.workspace._source.get_current_url.return_value = f'https://www.rednote.com/user/profile/{PROFILE_ID}'

    def tearDown(self):
        harvest_app.DOWNLOAD_DIR = self.previous_download_dir
        if self.workspace._profile_batch_job:
            with harvest_app.jobs_lock:
                harvest_app.jobs.pop(self.workspace._profile_batch_job.id, None)
        self.temp.cleanup()

    def test_profile_preview_reads_visible_page_and_caps_results(self):
        self.workspace._source.evaluate_js.return_value = {
            'profileUrl': f'https://www.rednote.com/user/profile/{PROFILE_ID}',
            'profileId': PROFILE_ID,
            'profileName': 'Lady with the Cat',
            'posts': [{'url': f'https://www.rednote.com/discovery/item/{POST_ID}', 'title': 'Post'}] * 30,
        }
        result = self.workspace.profile_preview()
        self.assertEqual(result['profileName'], 'Lady with the Cat')
        self.assertEqual(len(result['posts']), 25)

    def test_batch_requires_permission_and_an_open_profile(self):
        url = f'https://www.rednote.com/discovery/item/{POST_ID}'
        with self.assertRaisesRegex(ValueError, 'permission'):
            self.workspace.start_profile_batch('Lady with the Cat', [url])
        self.workspace._source.get_current_url.return_value = url
        with self.assertRaisesRegex(ValueError, 'profile'):
            self.workspace.start_profile_batch('Lady with the Cat', [url], True)

    def test_batch_is_ordered_and_creates_profile_named_folder_and_manifest(self):
        urls = [
            f'https://www.rednote.com/discovery/item/{POST_ID}',
            'https://www.rednote.com/explore/6a2fab41000000001003cc51',
        ]
        with patch('desktop_bridge.threading.Thread') as thread:
            result = self.workspace.start_profile_batch('Lady / Cat', urls, True)
        self.assertEqual(result['queuedPosts'], 2)
        self.assertIn('Lady Cat', result['profileFolder'])
        manifest = json.loads((Path(result['batchFolder']) / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['pacingSeconds'], 60)
        self.assertEqual(manifest['perMediaCooldownSeconds'], harvest_app.MIN_SECONDS_BETWEEN_JOBS)
        thread.return_value.start.assert_called_once()

    def test_batch_rejects_foreign_urls_and_large_batches(self):
        valid = f'https://www.rednote.com/discovery/item/{POST_ID}'
        with self.assertRaisesRegex(ValueError, 'HTTPS post links'):
            self.workspace.start_profile_batch('Profile', ['https://example.com/post'], True)
        with self.assertRaisesRegex(ValueError, 'limited to 25'):
            self.workspace.start_profile_batch('Profile', [valid] * 26, True)

    def test_profile_run_saves_post_text_loaded_comments_and_all_exposed_media(self):
        current = [f'https://www.rednote.com/user/profile/{PROFILE_ID}']
        self.workspace._source.get_current_url.side_effect = lambda: current[0]
        self.workspace._source.load_url.side_effect = lambda url: current.__setitem__(0, url)
        data = {
            'postId': POST_ID,
            'sourceUrl': f'https://www.rednote.com/discovery/item/{POST_ID}',
            'title': 'Visible note title',
            'text': 'Visible note text',
            'comments': [{'id': 'private-comment-id', 'author': 'Visible name', 'text': 'Loaded comment', 'reply': False}],
            'images': ['https://sns-img.xhscdn.com/photo.jpg'],
            'videos': [{'url': 'https://sns-video.xhscdn.com/video.mp4'}],
            'reportedComments': '18',
        }
        self.workspace.capture_post = Mock(side_effect=lambda: (self.workspace._posts.__setitem__(POST_ID, data) or {'postId': POST_ID}))
        requested_media = []
        def save_media(url, destination, kind, notify):
            requested_media.append((url, kind))
            extension = '.jpg' if kind == 'photo' else '.mp4'
            return {'file': Path(str(destination) + extension).name, 'bytes': 10, 'sha256': 'a' * 64, 'mime': 'image/jpeg' if kind == 'photo' else 'video/mp4'}
        def synchronous_thread(target, args, daemon=True):
            return type('InlineThread', (), {'start': staticmethod(lambda: target(*args))})()
        with patch('desktop_bridge.threading.Thread', side_effect=synchronous_thread), \
             patch('desktop_bridge.save_media', side_effect=save_media), \
             patch('desktop_bridge.app.capture_process_guard', return_value=nullcontext()), \
             patch.object(harvest_app, 'MIN_SECONDS_BETWEEN_JOBS', 0), \
             patch('desktop_bridge.time.sleep'):
            result = self.workspace.start_profile_batch('Lady with the Cat', [f'https://www.rednote.com/discovery/item/{POST_ID}'], True)
        job = harvest_app.jobs[result['jobId']]
        self.assertEqual(job.state, 'complete')
        self.assertEqual([kind for _, kind in requested_media], ['photo', 'video'])
        folder = Path(result['batchFolder'])
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['queue'][0]['order'], 1)
        self.assertEqual(manifest['queue'][0]['status'], 'captured')
        capture = json.loads((folder / 'posts' / POST_ID / 'capture.json').read_text(encoding='utf-8'))
        self.assertEqual(capture['text'], 'Visible note text')
        self.assertEqual(capture['comments'][0]['text'], 'Loaded comment')
        self.assertNotIn('author', capture['comments'][0])
        self.assertNotIn('id', capture['comments'][0])
        self.assertEqual(capture['savedMedia'], 2)


if __name__ == '__main__':
    unittest.main()
