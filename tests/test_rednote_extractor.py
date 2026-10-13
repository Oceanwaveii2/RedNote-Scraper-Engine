import sys
import json
import unittest
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import RedNoteIE, classify_rednote_page, normalized_source_url, Job, run_job
from yt_dlp import YoutubeDL
from yt_dlp.utils import ExtractorError

class ExtractorTests(unittest.TestCase):
    note_id = '6ab090fb0000000011031bfd'
    def extractor(self, state):
        ie = RedNoteIE(YoutubeDL({'quiet': True}))
        page = 'window.__INITIAL_STATE__=' + json.dumps(state) + ';'
        return ie, patch.object(ie, '_download_webpage', return_value=page)

    def test_declared_video_streams_and_metadata_without_guessed_cdn_probe(self):
        state = {'note': {'noteDetailMap': {self.note_id: {'note': {
            'title': 'fixture', 'desc': 'caption', 'video': {'media': {'stream': {'h264': [
                {'masterUrl': 'https://media.example.test/video.mp4', 'backupUrls': ['https://media.example.test/backup.mp4']}
            ]}}}, 'imageList': [], 'tagList': [{'name': 'fixture-tag'}]
        }}}}}
        ie, page = self.extractor(state)
        with page, patch.object(ie, '_request_webpage', side_effect=AssertionError('unexpected probe')):
            result = ie._real_extract('https://www.rednote.com/discovery/item/' + self.note_id)
        self.assertEqual(len(result['formats']), 2)
        self.assertEqual(result['description'], 'caption')
        self.assertEqual(result['tags'], ['fixture-tag'])

    def test_photo_note_preserves_images_without_video_formats(self):
        state = {'note': {'noteDetailMap': {self.note_id: {'note': {'title': 'fixture', 'imageList': [
            {'urlDefault': 'https://media.example.test/image.jpg'}]}}}}}
        ie, page = self.extractor(state)
        with page:
            result = ie._real_extract('https://www.rednote.com/discovery/item/' + self.note_id)
        self.assertEqual(result['formats'], [])
        self.assertEqual(len(result['_harvest_image_urls']), 1)

    def test_generic_page_is_access_failure_not_download_success(self):
        ie, page = self.extractor({'note': {'noteDetailMap': {}}})
        with page, self.assertRaisesRegex(ExtractorError, 'generic-page-or-session-required'):
            ie._real_extract('https://www.rednote.com/discovery/item/' + self.note_id)

    def test_rate_limit_challenge_and_format_are_distinct(self):
        self.assertEqual(classify_rednote_page('too many requests', {}), 'rate-limited')
        self.assertEqual(classify_rednote_page('<title>安全验证</title>', {}), 'challenge')
        self.assertEqual(classify_rednote_page('new markup', {}), 'page-format-changed')

    def test_profile_note_link_is_supported_without_dropping_share_query(self):
        _, url = normalized_source_url('https://www.rednote.com/user/profile/' + 'a'*24 + '/' + self.note_id + '?xsec_token=fixture')
        self.assertIn('/discovery/item/' + self.note_id, url)
        self.assertTrue(url.endswith('?xsec_token=fixture'))

    def test_previous_process_start_is_respected_before_a_new_capture(self):
        class NoNetworkDownloader:
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def add_info_extractor(self, extractor): pass
            def extract_info(self, *args, **kwargs): return None
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); destination = root / 'out'; destination.mkdir()
            previous = time.time(); (root / '.capture-clock').write_text(str(previous))
            job = Job(id='cooldown-fixture', url='https://www.rednote.com/discovery/item/' + self.note_id, platform='xiaohongshu')
            with patch('app.BASE_DIR', root), patch('app.DOWNLOAD_DIR', root / 'staging'), \
                 patch('app.last_job_started', 0), patch('app.MIN_SECONDS_BETWEEN_JOBS', .18), \
                 patch('app.YoutubeDL', NoNetworkDownloader):
                run_job(job, destination, None)
            self.assertGreaterEqual(float((root / '.capture-clock').read_text()) - previous, .16)
            self.assertEqual(job.state, 'failed')  # fixture intentionally provides no media

if __name__ == '__main__': unittest.main()
