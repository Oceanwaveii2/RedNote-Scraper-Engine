import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from browser_capture import merge_comments,allowed_media_url,save_media
from desktop_bridge import Workspace
import app

class BrowserCaptureTests(unittest.TestCase):
    def test_comments_dedup_keep_authors(self):
        a={'text':'same','author':'one','id':''}
        b={'text':'same','author':'two','id':''}
        self.assertEqual(len(merge_comments([a],[a,b])),2)

    def test_media_url_boundary(self):
        self.assertTrue(allowed_media_url('https://sns-img.xhscdn.com/a'))
        for url in ['blob:https://www.rednote.com/a','https://xhscdn.com.evil.example/a','http://sns-img.xhscdn.com/a','https://localhost/a']:
            self.assertFalse(allowed_media_url(url))

    def test_save_rejects_html(self):
        response=Mock();response.headers.get_content_type.return_value='text/html'
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response
        with tempfile.TemporaryDirectory() as root,patch('browser_capture.build_opener',return_value=opener):
            with self.assertRaises(ValueError):save_media('https://sns-img.xhscdn.com/a',Path(root)/'a','photo')
            self.assertEqual(list(Path(root).iterdir()),[])

    def test_capture_exports_text_comments_without_media_query(self):
        workspace=Workspace();workspace._source=Mock()
        workspace._source.evaluate_js.return_value={'postId':'abc','sourceUrl':'https://www.rednote.com/discovery/item/abc','title':'title','text':'soft tissue','comments':[{'text':'laugh','author':'a'}],'images':['https://sns-img.xhscdn.com/a?secret=hidden'],'videos':[]}
        with tempfile.TemporaryDirectory() as root,patch.object(app,'BASE_DIR',Path(root)):
            first=workspace.capture_post();second=workspace.capture_post()
            self.assertEqual(second['comments'],1)
            content=Path(first['export']).read_text(encoding='utf-8')
            self.assertIn('soft tissue',content)
            self.assertNotIn('hidden',content)
            self.assertIn('loaded-comments-only',content)

    def test_local_download_requires_authorization_and_blocks_foreign_origin(self):
        client=app.app.test_client()
        self.assertEqual(client.post('/api/bridge/download',json={}).status_code,400)
        self.assertEqual(client.post('/api/bridge/download',json={},headers={'Origin':'https://www.rednote.com'}).status_code,403)
        with patch('app.threading.Thread'):
            response=client.post('/api/bridge/download',json={'authorized':True,'url':'https://www.rednote.com/discovery/item/6ab090fb0000000011031bfd'})
            self.assertEqual(response.status_code,202)

    def test_manual_attachment_export_keeps_request_urls_in_memory_only(self):
        workspace=Workspace();workspace._source=Mock()
        workspace._source.evaluate_js.return_value={'postId':'abc','sourceUrl':'https://www.rednote.com/discovery/item/abc',
            'title':'fixture','text':'','images':[],'videos':[],
            'comments':[{'id':'native-reply','author':'fixture','text':'','attachments':[{
                'kind':'emoji','sourceIndex':1,'sourceUrl':'https://sns-img.xhscdn.com/emoji.png',
                'requestUrl':'https://sns-img.xhscdn.com/emoji.png?xsec_token=fixture-sensitive'}]}]}
        with tempfile.TemporaryDirectory() as root,patch.object(app,'BASE_DIR',Path(root)):
            result=workspace.capture_post()
            self.assertEqual(result['comments'],1)
            for path in Path(root).rglob('*.json'):
                payload=path.read_text(encoding='utf-8')
                self.assertNotIn('fixture-sensitive',payload)
                self.assertNotIn('requestUrl',payload)
            self.assertIn('fixture-sensitive',workspace._posts['abc']['comments'][0]['attachments'][0]['requestUrl'])
