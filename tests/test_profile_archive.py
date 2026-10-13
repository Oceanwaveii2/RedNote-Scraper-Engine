import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from profile_archive import ProfileArchiveStore, _write_html


class ProfileArchiveTests(unittest.TestCase):
    def test_pending_current_manifest_does_not_inherit_unverified_legacy_comments(self):
        self.store.store_profile(self.profile())
        note='62370bd2000000000102b6b4'
        with self.store._conn() as conn:
            conn.execute("INSERT INTO rednote_posts(post_id,profile_id,title,source_url,media_type,crawled_at) VALUES(?,?,?,?,?,?)",(note,self.profile_id,'Earlier capture','https://www.rednote.com/explore/'+note,'photo','2026-10-01T00:00:00Z'))
            conn.execute("INSERT INTO rednote_comments(comment_id,post_id,username,text_chinese) VALUES(?,?,?,?)",('earlier',note,'Reader','opaque earlier source'))
        current=self.store.get_post(note)
        self.assertEqual(current['coverage']['state'],'pending')
        self.assertEqual(current['comments'],[])
        self.assertEqual(self.store.get_profile(self.profile_id)['posts'][0]['comments'],[])
        with self.store._conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rednote_comments WHERE post_id=?",(note,)).fetchone()[0],1)

    def test_captured_note_keeps_current_profile_pin_marker(self):
        p=self.profile();p['posts'][0]['pinned']=True;self.store.store_profile(p)
        self.store.store_post(self.profile_id,{'postId':p['posts'][0]['postId'],'profileId':self.profile_id,'comments':[]},[],{'status':'partial'})
        self.assertTrue(self.store.get_post(p['posts'][0]['postId'])['pinned'])
        self.assertTrue(self.store.get_profile(self.profile_id)['posts'][0]['pinned'])

    def test_comment_author_metadata_survives_normalization_without_signed_url(self):
        rows=self.store._comments([{'id':'native-comment','author':'Reader','authorId':'a'*24,'authorAvatar':'https://img.xhscdn.com/avatar.png?secret=value','location':'source location','idSource':'source','text':'opaque'}])
        self.assertEqual(rows[0]['authorId'],'a'*24)
        self.assertEqual(rows[0]['authorAvatar'],'https://img.xhscdn.com/avatar.png')
        self.assertEqual(rows[0]['location'],'source location');self.assertEqual(rows[0]['idSource'],'source')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.store = ProfileArchiveStore(self.base)
        self.profile_id = '6082a828000000000101ef8d'

    def profile(self):
        return {'profileId': self.profile_id, 'name': 'Captured person', 'displayId': '9523944423',
                'bio': '<script>unsafe</script>', 'followers': '48.4K', 'following': 422,
                'url': 'https://www.rednote.com/user/profile/' + self.profile_id + '?xsec_token=SECRET',
                'posts': [{'postId': '62370bd2000000000102b6b4', 'title': 'Note'}]}

    def file(self, name, kind='photo', index=0):
        path = self.base / 'clone_media' / self.profile_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'local-media')
        return {'kind': kind, 'file': path.relative_to(self.base).as_posix(), 'sourceIndex': index,
                'status': 'downloaded', 'mime': 'video/mp4' if kind == 'video' else 'image/jpeg'}

    def test_source_identity_tokens_and_immutable_snapshots(self):
        self.store.store_profile(self.profile(), {'status': 'partial'})
        self.store.store_profile({**self.profile(), 'followers': None, 'bio': 'Changed source bio'})
        directory = self.base / 'profile_archives' / self.profile_id
        snapshots = list((directory / 'snapshots').glob('*.json'))
        self.assertEqual(2, len(snapshots))
        for path in directory.rglob('*'):
            if path.is_file():
                self.assertNotIn('SECRET', path.read_text(encoding='utf-8'))
        result = self.store.get_profile(self.profile_id)
        self.assertEqual(48400, result['followers_count'])
        self.assertEqual(self.profile_id, result['profile_id'])
        self.assertNotIn('62370bd2000000000102b6b4', result['profile_id'])
        with self.assertRaises(ValueError):
            self.store.store_profile({**self.profile(), 'profileId': 'wrong'})

    def test_media_and_nested_comments_are_linked_and_resumable(self):
        self.store.store_profile(self.profile())
        post_id = '62370bd2000000000102b6b4'
        note = {'postId': post_id, 'profileId': self.profile_id, 'title': '<img onerror=oops>', 'mediaType': 'video',
                'comments': [{'id': 'c1', 'author': 'Reader', 'text': 'Root 😀', 'replies': [{'id': 'c2', 'text': 'Reply', 'replies': [{'id': 'c3', 'text': 'Nested'}]}]}]}
        self.store.store_post(self.profile_id, note, [self.file('video.mp4', 'video'), self.file('photo.jpg')], {'status': 'partial'})
        self.store.store_post(self.profile_id, {**note, 'comments': [{'id': 'c4', 'text': 'Later'}]}, [{'kind': 'video', 'status': 'failed', 'sourceIndex': 0}], {'status': 'partial'})
        result = self.store.get_post(post_id)
        self.assertEqual(4, len(result['comments']))
        self.assertTrue(all(m['available'] for m in result['media']))
        self.assertTrue(all(m['url'].startswith('/api/master/media/clone_media/') for m in result['media']))
        self.assertEqual('c2', next(c for c in result['comments'] if c['id'] == 'c3')['parentId'])
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual(2, conn.execute('SELECT COUNT(*) FROM rednote_media WHERE post_id=?', (post_id,)).fetchone()[0])
            self.assertEqual('c2', conn.execute("SELECT parent_comment_id FROM rednote_comments WHERE comment_id='c3'").fetchone()[0])
        conn.close()
        offline = self.base / 'profile_archives' / self.profile_id / 'posts' / (post_id + '.html')
        markup = offline.read_text(encoding='utf-8')
        self.assertIn('controls playsinline', markup)
        self.assertIn('<details class="discussion-replies">', markup)
        self.assertIn('Nested', markup)
        self.assertIn('&lt;img onerror=oops&gt;', markup)
        self.assertIn('../../../clone_media/', markup)
        self.assertNotIn('https://via.placeholder', markup)

    def test_media_missing_is_reported_and_paths_stay_inside_base(self):
        self.assertIsNone(self.store.local_path('../../outside.mp4'))
        self.assertIsNone(self.store.local_path('https://cdn.example/video.mp4'))
        self.assertIsNone(self.store.local_path('rednote.sqlite'))
        self.store.store_profile(self.profile())
        media = self.file('video.mp4', 'video')
        self.store.store_post(self.profile_id, {'postId': 'note'}, [media], {})
        (self.base / media['file']).unlink()
        result = self.store.get_post('note')
        self.assertEqual('missing', result['media'][0]['status'])
        self.assertFalse(result['media'][0]['available'])
        self.assertEqual('', result['media'][0]['url'])

    def test_single_note_capture_preserves_existing_header(self):
        self.store.database.upsert_profile({'profile_id': self.profile_id, 'author_name': 'Real name', 'profile_url': self.profile()['url'], 'bio': 'Original bio', 'followers_count': 48400, 'following_count': 422})
        self.store.store_post(self.profile_id, {'postId': 'note', 'text': 'Captured note'}, [], {'state': 'partial'})
        profile = self.store.get_profile(self.profile_id)
        self.assertEqual('Real name', profile['name'])
        self.assertEqual(48400, profile['followers_count'])
        self.assertEqual('Original bio', profile['bio'])

    def test_partial_profile_updates_do_not_discard_prior_notes(self):
        self.store.store_profile(self.profile())
        self.store.store_profile({'profileId': self.profile_id, 'name': 'Captured person', 'posts': [{'postId': 'newnote', 'title': 'New'}]})
        profile = self.store.get_profile(self.profile_id)
        self.assertEqual(2, len(profile['posts']))
        self.assertEqual(0, profile['manifest']['postsCaptured'])
        self.assertEqual(2, profile['manifest']['postsDiscovered'])

    def test_media_avatar_and_cover_use_only_verified_local_urls(self):
        profile = self.profile()
        profile['avatarMedia'] = self.file('avatar.jpg', 'avatar')
        profile['posts'][0]['coverMedia'] = self.file('cover.jpg', 'cover')
        self.store.store_profile(profile)
        result = self.store.get_profile(self.profile_id)
        self.assertIn('/api/master/media/', result['avatar_url'])
        self.assertIn('/api/master/media/', result['posts'][0]['cover_url'])
        markup = (self.base / 'profile_archives' / self.profile_id / 'index.html').read_text(encoding='utf-8')
        self.assertIn('../../clone_media/', markup)
        self.assertNotIn('<script>unsafe', markup)

    def test_unknown_likes_and_supplied_card_likes_are_distinct(self):
        profile = self.profile()
        profile['posts'] += [{'postId': 'known', 'likesCount': '7,231'}, {'postId': 'zero', 'likes': 0}, {'postId': 'unknown', 'likes': '—'}]
        self.store.store_profile(profile)
        self.assertIsNone(self.store.get_post('62370bd2000000000102b6b4')['likes_count'])
        self.assertEqual(7231, self.store.get_post('known')['likes_count'])
        self.assertEqual(0, self.store.get_post('zero')['likes_count'])
        self.assertIsNone(self.store.get_post('unknown')['likes_count'])
        self.store.store_post(self.profile_id, {'postId': 'known', 'likes': ''}, [], {})
        self.assertEqual(7231, self.store.get_post('known')['likes_count'])
        self.store.store_profile({'profileId': self.profile_id, 'posts': [{'postId': 'known', 'title': 'New title'}]})
        self.assertEqual(7231, self.store.get_post('known')['likes_count'])
        self.store.store_post(self.profile_id, {'postId': '62370bd2000000000102b6b4', 'likes': ''}, [], {})
        self.assertIsNone(self.store.get_post('62370bd2000000000102b6b4')['likes_count'])
        markup = (self.base / 'profile_archives' / self.profile_id / 'index.html').read_text(encoding='utf-8')
        self.assertIn('♡ —', markup)

    def test_standalone_gallery_and_video_poster_resolve_local_files(self):
        self.store.store_profile(self.profile())
        media = [self.file('video.mp4', 'video'), self.file('cover.jpg', 'cover'), self.file('a.jpg', 'photo', 0), self.file('b.jpg', 'photo', 1)]
        self.store.store_post(self.profile_id, {'postId': 'gallery', 'title': 'Gallery'}, media, {})
        path = self.base / 'profile_archives' / self.profile_id / 'posts' / 'gallery.html'
        markup = path.read_text(encoding='utf-8')
        self.assertIn('poster="../../../clone_media/', markup)
        self.assertIn('href="#media-2"', markup)
        self.assertIn('id="media-2"', markup)
        import re
        from urllib.parse import unquote
        urls = re.findall(r'(?:src|poster)="([^"]+)"', markup)
        self.assertEqual(4, len(urls))
        for url in urls:
            self.assertTrue((path.parent / unquote(url)).is_file())

    def test_emoji_only_reply_retains_native_identity_without_invented_body(self):
        self.store.store_profile(self.profile())
        comments = [{'id': 'root', 'text': 'Root', 'replies': [{'id': 'emoji', 'author': 'Source reader', 'text': '', 'attachments': [{'kind': 'emoji', 'sourceIndex': 1, 'sourceUrl': 'https://cdn.example/emoji.png?token=SECRET', 'alt': '', 'width': 20, 'height': 20, 'archiveStatus': 'remote_only'}]}]}]
        self.store.store_post(self.profile_id, {'postId': 'replynote', 'comments': comments}, [], {})
        post = self.store.get_post('replynote')
        self.assertEqual(2, post['comments_count'])
        reply = next(c for c in post['comments'] if c['id'] == 'emoji')
        self.assertEqual('', reply['text'])
        self.assertEqual('root', reply['parentId'])
        self.assertFalse(reply['derivedId'])
        self.assertEqual('https://cdn.example/emoji.png', reply['attachments'][0]['sourceUrl'])
        self.assertFalse(reply['attachments'][0]['available'])
        self.assertEqual('', reply['attachments'][0]['url'])
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual('', conn.execute("SELECT text_chinese FROM rednote_comments WHERE comment_id='emoji'").fetchone()[0])
        conn.close()
        markup = (self.base / 'profile_archives' / self.profile_id / 'posts' / 'replynote.html').read_text(encoding='utf-8')
        self.assertIn('Emoji attachment not saved locally', markup)
        self.assertNotIn('cdn.example', markup)
        self.store.store_post(self.profile_id, {'postId': 'replynote', 'comments': [{'id': 'emoji', 'parentId': 'root', 'text': ''}]}, [], {})
        reply = next(c for c in self.store.get_post('replynote')['comments'] if c['id'] == 'emoji')
        self.assertEqual(1, len(reply['attachments']))

    def test_comment_attachment_uses_verified_local_file_and_missing_file_placeholder(self):
        self.store.store_profile(self.profile())
        file = self.file('emoji.png', 'emoji')
        self.store.store_post(self.profile_id, {'postId': 'localreply', 'comments': [{'id': 'emoji', 'text': '', 'attachments': [file]}]}, [], {})
        attachment = self.store.get_post('localreply')['comments'][0]['attachments'][0]
        self.assertTrue(attachment['available'])
        self.assertTrue(attachment['url'].startswith('/api/master/media/clone_media/'))
        markup = (self.base / 'profile_archives' / self.profile_id / 'posts' / 'localreply.html').read_text(encoding='utf-8')
        self.assertIn('src="../../../clone_media/', markup)
        (self.base / file['file']).unlink()
        attachment = self.store.get_post('localreply')['comments'][0]['attachments'][0]
        self.assertFalse(attachment['available'])
        self.assertEqual('', attachment['url'])

    def test_one_note_update_leaves_other_offline_pages_untouched(self):
        profile = self.profile()
        profile['posts'] = [{'postId': 'note_a'}, {'postId': 'note_b'}]
        self.store.store_profile(profile)
        self.store.store_post(self.profile_id, {'postId': 'note_a', 'text': 'Original A'}, [], {})
        self.store.store_post(self.profile_id, {'postId': 'note_b', 'text': 'Original B'}, [], {})
        directory = self.base / 'profile_archives' / self.profile_id
        unchanged = directory / 'posts' / 'note_b.html'
        before = (unchanged.read_bytes(), unchanged.stat().st_mtime_ns)
        snapshots = {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')}
        self.store.store_post(self.profile_id, {'postId': 'note_a', 'text': 'Changed A', 'comments': [{'id': 'new_comment', 'text': 'Real new comment'}]}, [], {'state': 'partial'})
        self.assertEqual(before, (unchanged.read_bytes(), unchanged.stat().st_mtime_ns))
        self.assertIn('Changed A', (directory / 'posts' / 'note_a.html').read_text(encoding='utf-8'))
        self.assertIn('1 comments', (directory / 'index.html').read_text(encoding='utf-8'))
        latest = json.loads((directory / 'latest.json').read_text(encoding='utf-8'))
        self.assertEqual(1, latest['manifest']['commentsCaptured'])
        self.assertEqual(latest['manifest'], latest['profile']['manifest'])
        self.assertEqual(len(snapshots) + 1, len(list((directory / 'snapshots').glob('*.json'))))
        for name, original in snapshots.items():
            self.assertEqual(original, (directory / 'snapshots' / name).read_bytes())

    def test_identical_observations_do_not_rewrite_data_or_snapshots(self):
        profile = self.profile()
        note = {'postId': 'note', 'title': 'Source title', 'comments': [{'id': 'c', 'text': 'Comment'}]}
        media = [self.file('a.jpg')]
        self.store.store_profile(profile, {'state': 'partial'})
        self.store.store_post(self.profile_id, note, media, {'state': 'partial'})
        directory = self.base / 'profile_archives' / self.profile_id
        before = {p.relative_to(directory): (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}
        self.store.store_profile(profile, {'state': 'partial'})
        self.store.store_post(self.profile_id, note, media, {'state': 'partial'})
        after = {p.relative_to(directory): (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_coverage_round_checkpoints_do_not_duplicate_source_snapshots(self):
        self.store.store_profile(self.profile(), {'state': 'partial', 'rounds': 1})
        note = {'postId': 'note', 'comments': [{'id': 'c', 'text': 'Same observed comment'}]}
        self.store.store_post(self.profile_id, note, [], {'state': 'partial', 'rounds': 1})
        directory = self.base / 'profile_archives' / self.profile_id
        snapshots = {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')}
        self.store.store_profile(self.profile(), {'state': 'partial', 'rounds': 2})
        self.store.store_post(self.profile_id, note, [], {'state': 'partial', 'rounds': 2})
        self.assertEqual(snapshots, {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')})
        self.assertEqual(2, self.store.get_post('note')['coverage']['rounds'])
        self.assertIn('&quot;rounds&quot;: 2', (directory / 'posts' / 'note.html').read_text(encoding='utf-8'))
        self.store.store_post(self.profile_id, {**note, 'comments': note['comments'] + [{'id': 'new', 'text': 'New observed comment'}]}, [], {'state': 'partial', 'rounds': 3})
        self.assertEqual(len(snapshots) + 1, len(list((directory / 'snapshots').glob('*.json'))))

    def test_explicit_rebuild_repairs_missing_pages_without_rewriting_identical_pages(self):
        self.store.store_profile(self.profile())
        self.store.store_post(self.profile_id, {'postId': 'other', 'text': 'Source'}, [], {})
        directory = self.base / 'profile_archives' / self.profile_id
        existing = directory / 'posts' / 'other.html'
        before = (existing.read_bytes(), existing.stat().st_mtime_ns)
        missing = directory / 'posts' / '62370bd2000000000102b6b4.html'
        missing.unlink()
        self.store.rebuild(self.profile_id)
        self.assertTrue(missing.is_file())
        self.assertEqual(before, (existing.read_bytes(), existing.stat().st_mtime_ns))

    def test_remote_attachment_update_preserves_local_asset_and_order(self):
        self.store.store_profile(self.profile())
        saved = {**self.file('emoji.png', 'emoji', 1), 'sourceUrl': 'https://cdn.example/emoji.png?token=SECRET', 'alt': 'Original alt'}
        remote = {'kind': 'image', 'sourceIndex': 2, 'sourceUrl': 'https://cdn.example/second.jpg'}
        self.store.store_post(self.profile_id, {'postId': 'note', 'comments': [{'id': 'c', 'text': '', 'attachments': [saved, remote]}]}, [], {})
        self.store.store_post(self.profile_id, {'postId': 'note', 'comments': [{'id': 'c', 'text': '', 'attachments': [{'kind': 'emoji', 'sourceIndex': 1, 'sourceUrl': 'https://cdn.example/emoji.png', 'archiveStatus': 'remote_only', 'alt': 'Updated source alt'}]}]}, [], {})
        assets = self.store.get_post('note')['comments'][0]['attachments']
        self.assertEqual([1, 2], [a['sourceIndex'] for a in assets])
        self.assertTrue(assets[0]['available'])
        self.assertEqual('saved', assets[0]['archiveStatus'])
        self.assertEqual(saved['file'], assets[0]['file'])
        self.assertEqual('Updated source alt', assets[0]['alt'])
        self.assertFalse(assets[1]['available'])
        self.assertEqual(1, self.store.get_profile(self.profile_id)['manifest']['commentAttachmentsSaved'])

    def test_comment_sql_updates_only_changed_comment_fields(self):
        self.store.store_profile(self.profile())
        note = {'postId': 'note', 'comments': [{'id': 'c1', 'text': 'Unchanged'}, {'id': 'c2', 'text': 'Original'}]}
        self.store.store_post(self.profile_id, note, [], {})
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            conn.execute('CREATE TABLE observed_updates(comment_id TEXT)')
            conn.execute('CREATE TRIGGER track_comment_update AFTER UPDATE ON rednote_comments BEGIN INSERT INTO observed_updates VALUES(new.comment_id); END')
        conn.close()
        self.store.store_post(self.profile_id, {'postId': 'note', 'comments': [{'id': 'c1', 'text': 'Unchanged'}, {'id': 'c2', 'text': 'Updated source comment'}]}, [], {})
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual([('c2',)], conn.execute('SELECT comment_id FROM observed_updates').fetchall())
        conn.close()

    def test_transient_request_urls_are_never_persisted(self):
        profile = {**self.profile(), 'requestUrl': 'https://cdn.example/private-request?xsec_token=SECRET'}
        profile['posts'][0]['request_url'] = 'https://cdn.example/private-card?token=SECRET'
        self.store.store_profile(profile)
        descriptor = {**self.file('safe.jpg'), 'requestURL': 'https://cdn.example/private-media?token=SECRET'}
        self.store.store_post(self.profile_id, {'postId': 'note', 'request_url': 'https://cdn.example/private-note?token=SECRET', 'comments': [{'id': 'c', 'text': 'Source', 'attachments': [{'sourceUrl': 'https://cdn.example/emoji.png?token=SECRET', 'requestUrl': 'https://cdn.example/private-emoji?token=SECRET'}]}]}, [descriptor], {})
        directory = self.base / 'profile_archives' / self.profile_id
        for path in directory.rglob('*'):
            if path.is_file():
                content = path.read_text(encoding='utf-8').lower()
                self.assertNotIn('requesturl', content)
                self.assertNotIn('request_url', content)
                self.assertNotIn('private-request', content)
                self.assertNotIn('private-card', content)
                self.assertNotIn('private-media', content)
                self.assertNotIn('private-note', content)
                self.assertNotIn('private-emoji', content)
                self.assertNotIn('secret', content)

    def test_identical_capture_repairs_missing_and_truncated_derived_html(self):
        profile = self.profile()
        note = {'postId': 'note', 'text': 'Observed source'}
        self.store.store_profile(profile)
        self.store.store_post(self.profile_id, note, [], {})
        directory = self.base / 'profile_archives' / self.profile_id
        snapshots = {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')}
        raw = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in [directory / 'profile.json', directory / 'posts' / 'note.json']}
        detail = directory / 'posts' / 'note.html'
        detail.unlink()
        self.store.store_post(self.profile_id, note, [], {})
        self.assertIn('Observed source', detail.read_text(encoding='utf-8'))
        detail.write_text('<!doctype html><html>interrupted', encoding='utf-8')
        self.store.store_post(self.profile_id, note, [], {})
        self.assertTrue(detail.read_text(encoding='utf-8').endswith('</html>'))
        pending = directory / 'posts' / '62370bd2000000000102b6b4.html'
        pending.unlink()
        (directory / 'index.html').write_text('<!doctype html>', encoding='utf-8')
        self.store.store_profile(profile)
        self.assertTrue(pending.is_file())
        self.assertTrue((directory / 'index.html').read_text(encoding='utf-8').endswith('</html>'))
        self.assertEqual(snapshots, {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')})
        for path, before in raw.items():
            self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))

    def test_failed_html_replacement_preserves_previous_derived_page(self):
        path = self.base / 'synthetic.html'
        _write_html(path, '<!doctype html><html>Previous</html>')
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        with patch('profile_archive.os.replace', side_effect=OSError('Synthetic interrupted replacement')):
            with self.assertRaises(OSError):
                _write_html(path, '<!doctype html><html>Updated</html>')
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual([], list(self.base.glob('synthetic.html.*.tmp')))

    def test_identical_retry_repairs_sqlite_failure_after_raw_json_commit(self):
        self.store.store_profile(self.profile())
        note = {'postId': 'note', 'comments': [{'id': 'root', 'text': 'Observed'}, {'id': 'reply', 'parentId': 'root', 'text': 'Reply'}]}
        media = [self.file('safe.jpg')]
        with patch.object(self.store, '_conn', side_effect=sqlite3.OperationalError('Synthetic comment transaction failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.store_post(self.profile_id, note, media, {})
        directory = self.base / 'profile_archives' / self.profile_id
        raw = directory / 'posts' / 'note.json'
        before = (raw.read_bytes(), raw.stat().st_mtime_ns)
        snapshots = {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')}
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual(0, conn.execute('SELECT COUNT(*) FROM rednote_comments WHERE post_id=?', ('note',)).fetchone()[0])
        conn.close()
        self.store.store_post(self.profile_id, note, media, {})
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual(2, conn.execute('SELECT COUNT(*) FROM rednote_comments WHERE post_id=?', ('note',)).fetchone()[0])
            self.assertEqual(1, conn.execute('SELECT COUNT(*) FROM rednote_media WHERE post_id=?', ('note',)).fetchone()[0])
            self.assertEqual('root', conn.execute("SELECT parent_comment_id FROM rednote_comments WHERE comment_id='reply'").fetchone()[0])
        conn.close()
        self.assertEqual(before, (raw.read_bytes(), raw.stat().st_mtime_ns))
        self.assertEqual(snapshots, {p.name: p.read_bytes() for p in (directory / 'snapshots').glob('*.json')})
        self.assertTrue((directory / 'posts' / 'note.html').is_file())

    def test_identical_retry_updates_existing_sql_comment_after_failed_delta(self):
        self.store.store_profile(self.profile())
        self.store.store_post(self.profile_id, {'postId': 'note', 'comments': [{'id': 'c', 'text': 'Original'}]}, [], {})
        updated = {'postId': 'note', 'comments': [{'id': 'c', 'text': 'Changed observed source'}]}
        with patch.object(self.store, '_conn', side_effect=sqlite3.OperationalError('Synthetic delta transaction failure')):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.store_post(self.profile_id, updated, [], {})
        self.store.store_post(self.profile_id, updated, [], {})
        with sqlite3.connect(self.base / 'rednote.sqlite') as conn:
            self.assertEqual('Changed observed source', conn.execute("SELECT text_chinese FROM rednote_comments WHERE comment_id='c'").fetchone()[0])
        conn.close()


if __name__ == '__main__':
    unittest.main()
