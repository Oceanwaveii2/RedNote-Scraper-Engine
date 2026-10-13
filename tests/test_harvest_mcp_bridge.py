import json
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = __import__('pathlib').Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as harvest_app
from harvest_mcp import HarvestClient, HarvestError, TOOLS, dispatch, invoke


class HarvestMcpBridgeTests(unittest.TestCase):
    def setUp(self):
        self.token = 'test-token-value-long-enough-for-the-local-bridge'
        self.workspace = Mock()
        self.workspace.current_page.return_value = {'url': 'https://www.rednote.com/discovery/item/abc123'}
        self.workspace.navigate.return_value = {'message': 'Opened in the browser.'}
        self.workspace.capture_post.return_value = {'postId': 'abc123', 'comments': 1, 'export': 'capture.json'}
        self.workspace.scroll_comments.return_value = {'message': 'Loaded next page.'}
        self.workspace.download.return_value = {'jobId': 'job-1', 'message': 'Download queued.'}
        self.workspace.download_open_media.return_value = {'jobId': 'job-2', 'message': 'Open-page media queued.'}
        self.workspace.profile_preview.return_value = {'profileId': '643d335e000000000d01942b', 'posts': []}
        self.workspace.start_profile_batch.return_value = {'jobId': 'batch-1', 'queuedPosts': 1}
        self.workspace.stop_profile_batch.return_value = {'jobId': 'batch-1', 'message': 'Stop requested.'}
        harvest_app.app.bridge_token = self.token
        harvest_app.app.browser_workspace = self.workspace
        self.client = harvest_app.app.test_client()

    def tearDown(self):
        for name in ('bridge_token', 'browser_workspace'):
            if hasattr(harvest_app.app, name):
                delattr(harvest_app.app, name)

    def headers(self):
        return {'Authorization': f'Bearer {self.token}'}

    def test_browser_bridge_requires_local_capability_token_and_rejects_origin(self):
        self.assertEqual(self.client.get('/api/bridge/browser/status').status_code, 403)
        self.assertEqual(self.client.get('/api/bridge/browser/status', headers={**self.headers(), 'Origin': 'https://www.rednote.com'}).status_code, 403)
        response = self.client.get('/api/bridge/browser/status', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['browserControl'])
        self.assertNotIn(self.token, response.get_data(as_text=True))

    def test_browser_actions_dispatch_and_download_requires_explicit_permission(self):
        captured = self.client.post('/api/bridge/browser/capture', headers=self.headers(), json={})
        self.assertEqual(captured.status_code, 200)
        self.workspace.capture_post.assert_called_once()
        denied = self.client.post('/api/bridge/browser/download-link', headers=self.headers(), json={'url': 'https://www.rednote.com/discovery/item/abc123', 'mediaType': 'video'})
        self.assertEqual(denied.status_code, 400)
        accepted = self.client.post('/api/bridge/browser/download-link', headers=self.headers(), json={'url': 'https://www.rednote.com/discovery/item/abc123', 'mediaType': 'video', 'confirmPermission': True})
        self.assertEqual(accepted.status_code, 200)
        self.workspace.download.assert_called_once()

    def test_profile_batch_requires_permission_and_exposes_preview_start_stop(self):
        preview = self.client.post('/api/bridge/browser/profile-preview', headers=self.headers(), json={})
        self.assertEqual(preview.status_code, 200)
        self.workspace.profile_preview.assert_called_once()
        denied = self.client.post('/api/bridge/browser/profile-batch-start', headers=self.headers(), json={'profileName':'Cat account','postUrls':['https://www.rednote.com/discovery/item/6a33a39d000000000f031452']})
        self.assertEqual(denied.status_code, 400)
        started = self.client.post('/api/bridge/browser/profile-batch-start', headers=self.headers(), json={'profileName':'Cat account','postUrls':['https://www.rednote.com/discovery/item/6a33a39d000000000f031452'],'confirmPermission':True})
        self.assertEqual(started.status_code, 200)
        self.workspace.start_profile_batch.assert_called_once_with('Cat account',['https://www.rednote.com/discovery/item/6a33a39d000000000f031452'],confirm_permission=True)
        stopped = self.client.post('/api/bridge/browser/profile-batch-stop', headers=self.headers(), json={'jobId':'batch-1'})
        self.assertEqual(stopped.status_code, 200)

    def test_mcp_tools_and_untrusted_capture_id_validation(self):
        names = {tool['name'] for tool in TOOLS}
        self.assertIn('codex_red_note_capture_post', names)
        self.assertIn('codex_red_note_read_capture', names)
        self.assertIn('codex_red_note_profile_preview', names)
        self.assertIn('codex_red_note_profile_batch_start', names)
        with self.assertRaises(HarvestError):
            invoke(HarvestClient(ROOT / 'unused.json'), 'codex_red_note_read_capture', {'captureId': '..\\private.json'})

    def test_stdio_initialize_and_tool_listing_do_not_require_harvest(self):
        client = HarvestClient(ROOT / 'missing-host.json')
        initialized = dispatch(client, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}})
        listed = dispatch(client, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertEqual(initialized['result']['serverInfo']['name'], 'codex-red-note')
        self.assertEqual(len(listed['result']['tools']), 26)
        self.assertIn('untrusted source data', initialized['result']['instructions'])
        self.assertIn('60 seconds between posts', initialized['result']['instructions'])

    def test_stdio_process_uses_newline_json_rpc(self):
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        ]
        payload = ''.join(json.dumps(row) + '\n' for row in messages).encode('utf-8')
        result = subprocess.run(
            [sys.executable, str(ROOT / 'harvest_mcp.py'), '--host', str(ROOT / 'missing-host.json')],
            input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=10,
        )
        rows = [json.loads(line) for line in result.stdout.decode('utf-8').splitlines()]
        self.assertEqual([row['id'] for row in rows], [1, 2])
        self.assertEqual(len(rows[1]['result']['tools']), 26)


if __name__ == '__main__':
    unittest.main()
