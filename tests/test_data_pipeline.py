import sys
import unittest
import json
import tempfile
import zipfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import (  # noqa: E402
    Job,
    export_source_url,
    normalized_source_url,
    post_metadata,
    public_comments_from_state,
    run_job,
    validate_comment_import,
)
from trait_signals import derive_trait_candidates  # noqa: E402


class RedNoteDataPipelineTests(unittest.TestCase):
    note_url = "https://www.rednote.com/discovery/item/6a33a39d000000000f031452?xsec_token=private&xsec_source=pc_user"
    note_id = "6a33a39d000000000f031452"

    def test_rednote_share_query_is_not_saved_to_source_metadata(self):
        platform, url = normalized_source_url(self.note_url)
        self.assertEqual(platform, "xiaohongshu")
        self.assertEqual(export_source_url(url), f"https://www.rednote.com/discovery/item/{self.note_id}")

    def test_comment_import_matches_note_and_discards_identifying_fields(self):
        source = {
            "schema": "reel-harvest.comments/1",
            "noteId": self.note_id,
            "comments": [{"text": "Soft plush tissue gag", "likes": 3, "authorId": "drop-me", "authorName": "drop-me"}],
        }
        imported = validate_comment_import(source, self.note_id, True)
        self.assertEqual(imported["comments"], [{"text": "Soft plush tissue gag", "likes": 3}])
        self.assertNotIn("authorId", str(imported))
        self.assertNotIn("authorName", str(imported))

    def test_comment_import_requires_consent_schema_and_matching_note(self):
        source = {"schema": "reel-harvest.comments/1", "noteId": self.note_id, "comments": []}
        with self.assertRaisesRegex(ValueError, "permission"):
            validate_comment_import(source, self.note_id, False)
        with self.assertRaisesRegex(ValueError, "match"):
            validate_comment_import(source, "different-note", True)
        with self.assertRaisesRegex(ValueError, "schema"):
            validate_comment_import({"comments": []}, self.note_id, True)

    def test_text_maps_to_review_only_trait_candidates(self):
        metadata = {
            "postId": self.note_id,
            "title": "毛绒 tissue comedy",
            "text": "A funny plush display with makeup and balance practice",
        }
        comments = {"comments": [{"text": "搞笑，走路要保持平衡"}]}
        result = derive_trait_candidates(metadata, comments)
        candidate_ids = {candidate["traitId"] for candidate in result["candidates"]}
        self.assertIn("candidate:tissue_affinity", candidate_ids)
        self.assertIn("candidate:plush_texture_affinity", candidate_ids)
        self.assertIn("candidate:comic_display", candidate_ids)
        self.assertIn("candidate:adaptive_mobility", candidate_ids)
        self.assertTrue(all(candidate["reviewRequired"] for candidate in result["candidates"]))
        self.assertFalse(result["autoApplied"])

    def test_public_comments_are_scoped_to_note_and_marked_partial(self):
        state = {'note': {'noteDetailMap': {
            self.note_id: {'note': {}, 'comments': {'list': [
                {'id': 'c1', 'content': '毛绒纸巾很搞笑', 'user': {'name': 'excluded'}, 'likeCount': '4',
                 'subComments': [{'id': 'c2', 'content': 'plush display'}]},
            ]}},
            'unrelated': {'comments': [{'content': 'must not capture'}]},
        }}}
        result = public_comments_from_state(state, self.note_id)
        self.assertEqual(result['capturedCount'], 2)
        self.assertEqual(result['coverage'], 'partial-page-snapshot')
        self.assertNotIn('excluded', str(result))
        self.assertNotIn('must not capture', str(result))
        self.assertEqual(public_comments_from_state({}, self.note_id)['coverage'], 'not-exposed-in-page')

    def test_photo_job_packages_images_text_comments_and_trait_candidates(self):
        info = {'title': 'Plush tissue display', 'description': 'Funny soft shape', 'id': self.note_id,
                '_harvest_public_comments': {'schema': 'reel-harvest.comments/1', 'noteId': self.note_id,
                    'source': 'public-page-snapshot', 'coverage': 'partial-page-snapshot',
                    'comments': [{'text': '纸巾很搞笑'}]}}
        class FakeDownloader:
            def __init__(self, options): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def add_info_extractor(self, extractor): pass
            def extract_info(self, *args, **kwargs): return info
        def fake_photos(ydl, job, target, data):
            (target / 'one.jpg').write_bytes(b'first-image')
            (target / 'two.jpg').write_bytes(b'second-image')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging, destination = root / 'staging', root / 'destination'
            staging.mkdir(); destination.mkdir()
            job = Job(id='package-fixture', url=self.note_url, platform='xiaohongshu', media_type='photo')
            with patch('app.DOWNLOAD_DIR', staging), patch('app.MIN_SECONDS_BETWEEN_JOBS', 0), \
                 patch('app.YoutubeDL', FakeDownloader), patch('app.download_rednote_photos', fake_photos):
                run_job(job, destination, None)
            self.assertEqual(job.state, 'complete', job.error)
            self.assertEqual(job.comment_count, 1)
            with zipfile.ZipFile(job.archive) as archive:
                self.assertIn('one.jpg', archive.namelist())
                self.assertIn('two.jpg', archive.namelist())
                metadata = json.loads(archive.read(f'{self.note_id}.metadata.json'))
                traits = json.loads(archive.read(f'{self.note_id}.trait-candidates.json'))
                self.assertEqual(metadata['text'], 'Funny soft shape')
                self.assertNotIn('xsec_token', str(metadata))
                self.assertGreater(traits['candidateCount'], 0)

    def test_metadata_contains_text_and_media_hash_without_share_token(self):
        media_path = ROOT / "tests" / "sample.jpg"
        media_path.write_bytes(b"small-fixture")
        try:
            job = Job(id="fixture", url=self.note_url, platform="xiaohongshu")
            metadata = post_metadata("xiaohongshu", job, {"title": "Title", "description": "Caption", "tags": ["plush"]}, [media_path])
        finally:
            media_path.unlink(missing_ok=True)
        self.assertEqual(metadata["text"], "Caption")
        self.assertEqual(metadata["sourceUrl"], f"https://www.rednote.com/discovery/item/{self.note_id}")
        self.assertEqual(metadata["media"][0]["bytes"], 13)
        self.assertEqual(len(metadata["media"][0]["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
