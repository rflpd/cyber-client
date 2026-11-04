import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from cyber_client import Client, CyberError, read_token

TOKEN = 'test_' + 'x' * 40

class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.handle_request()
            def do_POST(self):
                self.handle_request()
            def handle_request(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                cls.requests.append((self.path, self.headers.get('Authorization'), json.loads(body) if body else None))
                code, data = cls.responses.pop(0)
                self.send_response(code)
                if code == 302:
                    self.send_header('Location', '/redirect-target')
                self.end_headers()
                self.wfile.write(data if isinstance(data, bytes) else json.dumps(data).encode())
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%s' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        type(self).requests = []
        type(self).responses = []
        self.client = Client(self.base, TOKEN)

    def test_discovery_does_not_send_token(self):
        self.responses.append((200, {'models': []}))
        self.assertEqual(self.client.models(), {'models': []})
        self.assertIsNone(self.requests[0][1])

    def test_authenticated_job_and_completed_replay(self):
        completed = {'quote_id': 'q1', 'state': 'completed', 'output': 'HELLO', 'semantic_acceptance': False}
        self.responses.extend([(200, {'quote_id': 'q1', 'state': 'quoted'}), (202, {'state': 'running'}), (200, completed), (200, completed), (200, completed)])
        for _ in range(2):
            self.assertEqual(self.client.run(request_id='same-job', model='small', prompt='Hello'), completed)
        self.assertEqual([x[0] for x in self.requests], ['/quotes', '/execute', '/status', '/quotes', '/status'])
        self.assertTrue(all(x[1] == 'Bearer ' + TOKEN for x in self.requests))
        self.assertEqual(self.requests[0][2], self.requests[3][2])

    def test_redirect_is_not_followed(self):
        self.responses.append((302, {}))
        with self.assertRaisesRegex(CyberError, 'redirect_refused'):
            self.client.status('q1')
        self.assertEqual(len(self.requests), 1)

    def test_error_body_cannot_echo_secret(self):
        self.responses.append((401, {'error': 'secret: ' + TOKEN}))
        with self.assertRaises(CyberError) as caught:
            self.client.status('q1')
        self.assertEqual(str(caught.exception), 'http_error')
        self.assertEqual(caught.exception.status, 401)

    def test_conflict_is_not_retried(self):
        self.responses.append((409, {'error': 'request_id_conflict'}))
        with self.assertRaisesRegex(CyberError, 'request_id_conflict'):
            self.client.run(request_id='same', model='small', prompt='changed')
        self.assertEqual(len(self.requests), 1)

    def test_failed_job_is_not_reexecuted(self):
        self.responses.append((200, {'quote_id': 'q1', 'state': 'failed'}))
        with self.assertRaisesRegex(CyberError, 'operator_review'):
            self.client.run(request_id='same', model='small', prompt='Hello')
        self.assertEqual(len(self.requests), 1)

    def test_network_failure_is_not_retried(self):
        with patch.object(self.client._http, 'open', side_effect=TimeoutError) as transport:
            with self.assertRaisesRegex(CyberError, 'reuse_request_id'):
                self.client.execute('q1')
            self.assertEqual(transport.call_count, 1)

    def test_poll_timeout_does_not_reexecute(self):
        self.responses.extend([(200, {'quote_id': 'q1', 'state': 'running'}), (200, {'state': 'running'})])
        with patch('cyber_client.time.monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(CyberError, 'poll_timeout'):
                self.client.run(request_id='same', model='small', prompt='Hello', poll_timeout=1)
        self.assertEqual([x[0] for x in self.requests], ['/quotes', '/status'])

    def test_response_limits_and_shapes(self):
        for data, message in [(b'x' * (1024 * 1024 + 1), 'response_size_limit'), (b'not json', 'invalid_json_response'), ([], 'response_object_required')]:
            self.responses.append((200, data))
            with self.assertRaisesRegex(CyberError, message):
                self.client.models()

    def test_input_validation(self):
        for url in ['http://example.com', 'https://u:p@example.com', 'https://example.com?token=x', 'https://example.com#fragment']:
            with self.assertRaises(CyberError):
                Client(url)
        for change in [{'prompt': '界' * 683}, {'max_output_tokens': True}, {'model': 'other'}]:
            args = dict(request_id='job', model='small', prompt='hello')
            args.update(change)
            with self.assertRaises(CyberError):
                self.client.quote(**args)
        for limit in [True, '1', float('nan')]:
            with self.assertRaises(CyberError):
                self.client.run(request_id='job', model='small', prompt='hello', poll_timeout=limit)
        self.assertEqual(self.requests, [])

    def test_private_token_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invitation'
            path.write_text(TOKEN)
            path.chmod(0o600)
            self.assertEqual(read_token(path), TOKEN)
            if os.name == 'posix':
                path.chmod(0o644)
                with self.assertRaises(CyberError):
                    read_token(path)
                path.chmod(0o600)
                link = Path(directory) / 'link'
                link.symlink_to(path)
                with self.assertRaises(CyberError):
                    read_token(link)

if __name__ == '__main__':
    unittest.main()
