"""MCP transport and tool checks using a tiny fake app, never the real database/UI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
if ROOT.name == 'tests':
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request

from app_mcp import create_mcp_blueprint
from harvest_mcp import HarvestClient, HarvestError, TOOLS, dispatch, invoke


class DirectMcpTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.bridge_token = 'local-test-capability-with-at-least-32-characters'
        self.calls = []

        @self.app.route('/api/bridge/<path:path>', methods=['GET', 'POST'])
        def bridge(path):
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + self.app.bridge_token)
            self.assertEqual(request.remote_addr, '127.0.0.1')
            self.calls.append((path, request.get_json(silent=True), dict(request.args)))
            return jsonify(path=path, bridgeToken=self.app.bridge_token,
                           sourceUrl='https://www.rednote.com/explore/123?xsec_token=private-share-value')

        self.app.register_blueprint(create_mcp_blueprint())
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + self.app.bridge_token,
                        'Accept': 'application/json, text/event-stream',
                        'MCP-Protocol-Version': '2025-06-18'}

    def post(self, message, **kwargs):
        return self.client.post('/mcp', headers=self.headers, json=message, **kwargs)

    def test_auth_origin_and_loopback_are_required(self):
        msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        self.assertEqual(self.client.post('/mcp', json=msg).status_code, 403)
        for origin in ('https://rednote.com', ''):
            self.assertEqual(self.client.post('/mcp', json=msg,
                headers={**self.headers, 'Origin': origin}).status_code, 403)
        self.assertEqual(self.post(msg, environ_overrides={'REMOTE_ADDR': '203.0.113.1'}).status_code, 403)

    def test_initialize_list_notifications_and_http_semantics(self):
        init = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                          'params': {'protocolVersion': '2025-06-18'}})
        self.assertEqual(init.status_code, 200)
        self.assertEqual(init.json['result']['protocolVersion'], '2025-06-18')
        listed = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})
        self.assertEqual(listed.json['result']['tools'], TOOLS)
        note = self.post({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        self.assertEqual((note.status_code, note.data), (202, b''))
        self.assertEqual(self.client.get('/mcp', headers=self.headers).status_code, 405)
        self.assertEqual(self.client.delete('/mcp', headers=self.headers).status_code, 405)
        self.assertNotIn(self.app.bridge_token, listed.get_data(as_text=True))

    def test_http_rejects_bad_headers_json_and_batches(self):
        msg = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
        self.assertEqual(self.client.post('/mcp', json=msg,
            headers={**self.headers, 'Accept': 'text/event-stream'}).status_code, 406)
        self.assertEqual(self.client.post('/mcp', json=msg,
            headers={**self.headers, 'MCP-Protocol-Version': '2099-01-01'}).status_code, 400)
        self.assertEqual(self.client.post('/mcp', data='broken', content_type='text/plain',
            headers=self.headers).status_code, 415)
        invalid = self.client.post('/mcp', data='{', content_type='application/json', headers=self.headers)
        self.assertEqual((invalid.status_code, invalid.json['error']['code']), (400, -32700))
        self.assertEqual(self.post([msg]).status_code, 400)

    def test_clone_archive_and_context_calls_use_same_registry(self):
        cases = [
            ('codex_red_note_clone_start', {'url': 'https://www.rednote.com/user/profile/'+'a'*24, 'confirmPermission': True}, 'browser/clone-start'),
            ('codex_red_note_clone_status', {}, 'browser/clone-status'),
            ('codex_red_note_clone_pause', {'jobId': 'job-1'}, 'browser/clone-pause'),
            ('codex_red_note_clone_resume', {'jobId': 'job-1'}, 'browser/clone-resume'),
            ('codex_red_note_clone_retry', {'jobId': 'job-1', 'postId': 'b'*24}, 'browser/clone-retry'),
            ('codex_red_note_archive_profiles', {}, 'archive/profiles'),
            ('codex_red_note_archive_profile', {'profileId': 'a'*24}, 'archive/profile/'+'a'*24),
            ('codex_red_note_archive_post', {'postId': 'b'*24}, 'archive/post/'+'b'*24),
            ('codex_red_note_context_pack', {'profileId': 'a'*24, 'postIds': ['b'*24]}, 'archive/context'),
            ('codex_red_note_current_selection', {}, 'archive/selection'),
            ('codex_red_note_diagnostics', {}, 'diagnostics'),
            ('codex_red_note_clone_stop', {'jobId': 'job-1'}, 'browser/clone-stop'),
        ]
        for index, (name, args, path) in enumerate(cases):
            with self.subTest(name=name):
                response = self.post({'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
                                      'params': {'name': name, 'arguments': args}})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json['result']['isError'])
                self.assertEqual(self.calls[-1][0], path)
                output = response.get_data(as_text=True)
                self.assertNotIn(self.app.bridge_token, output)
                self.assertNotIn('private-share-value', output)
        self.assertEqual(self.calls[8][2], {'profileId': 'a'*24, 'postIds': 'b'*24})

    def test_tool_validation_and_permission_stop_before_bridge(self):
        denied = self.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'codex_red_note_clone_start', 'arguments': {
                'url': 'https://www.rednote.com/user/profile/'+'a'*24, 'confirmPermission': False}}})
        self.assertTrue(denied.json['result']['isError'])
        invalid = self.post({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
            'params': {'name': 'codex_red_note_archive_post', 'arguments': {'postId': '../private', 'path': 'secret'}}})
        self.assertTrue(invalid.json['result']['isError'])
        self.assertEqual(self.calls, [])
        for options in ({'unknownOption': True}, {'maxPosts': True}, {'maxPosts': -1}, {'pageWaitSeconds': 4000},
                        {'pageDelaySeconds': 2.9}, {'postPacingSeconds': 89}, {'mediaPacingSeconds': 44}, {'pageWaitSeconds': float('nan')}):
            bad_option = self.post({'jsonrpc':'2.0','id':4,'method':'tools/call',
                'params': {'name':'codex_red_note_clone_start','arguments': {
                    'url':'https://www.rednote.com/user/profile/'+'a'*24,
                    'confirmPermission': True, 'options': options}}})
            self.assertTrue(bad_option.json['result']['isError'])
        self.assertEqual(self.calls, [])
        unknown = self.post({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'evaluate_python', 'arguments': {}}})
        self.assertEqual(unknown.json['error']['code'], -32602)

    def test_invalid_rpc_does_not_execute_tool_notifications(self):
        fake = Mock()
        self.assertEqual(dispatch(fake, [])['error']['code'], -32600)
        self.assertEqual(dispatch(fake, {'jsonrpc':'1.0','id':1,'method':'ping'})['error']['code'], -32600)
        self.assertEqual(dispatch(fake, {'jsonrpc':'2.0','id':1,'method':'tools/call','params':[]})['error']['code'], -32602)
        self.assertIsNone(dispatch(fake, {'jsonrpc':'2.0','method':'tools/call',
            'params': {'name':'codex_red_note_capture_post'}}))
        fake.call.assert_not_called()

    def test_stdio_handshake_and_read_only_call_against_mock_bridge(self):
        token = self.app.bridge_token
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                if handler.headers.get('Authorization') != 'Bearer '+token:
                    handler.send_error(403)
                    return
                body = json.dumps({'browserControl': True, 'currentPage':'https://www.rednote.com/'}).encode()
                handler.send_response(200)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                host = Path(directory)/'harvest-host.json'
                host.write_text(json.dumps({'schema':'noir.harvest-host/1','port':server.server_port,
                                            'browserControl':True,'bridgeToken':token}),encoding='utf-8')
                messages = [
                    {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
                    {'jsonrpc':'2.0','method':'notifications/initialized'},
                    {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                    {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'codex_red_note_status','arguments':{}}},
                ]
                completed = subprocess.run([sys.executable, '-B', str(ROOT / 'harvest_mcp.py'),
                    '--host', str(host)], input=''.join(json.dumps(m)+'\n' for m in messages).encode(),
                    capture_output=True, timeout=10, check=True,
                    env={**os.environ, 'HTTP_PROXY':'http://127.0.0.1:1', 'http_proxy':'http://127.0.0.1:1',
                         'NO_PROXY':'', 'no_proxy':''})
                rows = [json.loads(line) for line in completed.stdout.splitlines()]
                self.assertEqual([r['id'] for r in rows], [1,2,3])
                self.assertEqual(rows[1]['result']['tools'], TOOLS)
                self.assertTrue(rows[2]['result']['structuredContent']['browserControl'])
                self.assertNotIn(token.encode(), completed.stdout)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_stdio_http_client_rejects_redirects(self):
        requested = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(handler):
                requested.append(handler.path)
                if handler.path == '/api/bridge/diagnostics':
                    handler.send_response(302)
                    handler.send_header('Location', '/external-target')
                    handler.end_headers()
                else:
                    body=b'{"error":"capability escaped bridge"}'
                    handler.send_response(200)
                    handler.send_header('Content-Type','application/json')
                    handler.send_header('Content-Length',str(len(body)))
                    handler.end_headers()
                    handler.wfile.write(body)
            def log_message(self,*args): pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                host=Path(directory)/'host.json'
                host.write_text(json.dumps({'schema':'noir.harvest-host/1','port':server.server_port,
                    'browserControl':True,'bridgeToken':self.app.bridge_token}),encoding='utf-8')
                with self.assertRaises(HarvestError):
                    HarvestClient(host).call('/api/bridge/diagnostics',timeout=2)
            self.assertEqual(requested,['/api/bridge/diagnostics'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)


if __name__ == '__main__':
    unittest.main()
