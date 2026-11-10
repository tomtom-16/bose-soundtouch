"""Contrôle du listener simulé et routes HTTP sur boucle locale uniquement."""
import http.client
import io
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from soundtouch import buttons, config, server


class ListenerClientTests(unittest.TestCase):
    @patch.object(buttons.urllib.request, 'build_opener')
    def test_passive_status_and_explicit_check(self, build):
        opener = build.return_value
        result = {'ok': True, 'available': True, 'speakers': {}}
        def response(*args, **kwargs):
            return io.BytesIO(json.dumps(result).encode())
        opener.open.side_effect = response
        with patch.dict(buttons.os.environ, {'BOSE_BUTTONS_PORT': '18078'}):
            self.assertEqual(buttons.listener_status(), result)
            req = opener.open.call_args.args[0]
            self.assertEqual(req.full_url, 'http://127.0.0.1:18078/status')
            self.assertEqual(req.method, 'GET')
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 1)
            buttons.listener_status(check=True, name='Séjour & cuisine')
            req = opener.open.call_args.args[0]
            self.assertEqual(req.method, 'POST')
            self.assertEqual(req.full_url, 'http://127.0.0.1:18078/check?name=S%C3%A9jour+%26+cuisine')
            self.assertEqual(opener.open.call_args.kwargs['timeout'], 8)
            self.assertEqual(build.call_args.args[0].proxies, {})

    @patch.object(buttons.urllib.request, 'build_opener')
    def test_listener_failure_is_reported_without_breaking_interface(self, build):
        build.return_value.open.side_effect = OSError('offline')
        self.assertFalse(buttons.listener_status()['available'])
        build.return_value.open.side_effect = lambda *a, **kw: io.BytesIO(b'{}')
        self.assertFalse(buttons.listener_status()['available'])
        with patch.dict(buttons.os.environ, {'BOSE_BUTTONS_PORT': 'invalid'}):
            self.assertFalse(buttons.listener_status()['available'])


class ButtonsRoutesTests(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.ProxyHandler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        cfg = patch.object(config, 'get_config', return_value={})
        cfg.start()
        self.addCleanup(cfg.stop)
        speakers = patch.object(config, 'load_json', return_value={'Salon': {'ip': '192.0.2.1'}})
        speakers.start()
        self.addCleanup(speakers.stop)
        self.result = {'ok': True, 'available': True, 'speakers': {'Salon': {'state': 'connected'}}}
        listener = patch.object(buttons, 'listener_status', return_value=self.result)
        self.listener = listener.start()
        self.addCleanup(listener.stop)

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection(*self.srv.server_address, timeout=2)
        try:
            conn.request(method, path, None if payload is None else json.dumps(payload),
                         headers or {'Content-Type': 'application/json'})
            response = conn.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            conn.close()

    def test_passive_and_active_routes(self):
        self.assertEqual(self.request('GET', '/api/buttons'), (200, self.result))
        self.listener.assert_called_once_with()
        self.assertEqual(self.request('POST', '/api/buttons/check', {}), (200, self.result))
        self.listener.assert_called_with(check=True, name=None)
        self.request('POST', '/api/buttons/check', {'name': 'Salon'})
        self.listener.assert_called_with(check=True, name='Salon')

    def test_invalid_and_cross_origin_checks_do_not_contact_listener(self):
        for payload in ({'name': 'Unknown'}, {'name': []}, []):
            self.assertEqual(self.request('POST', '/api/buttons/check', payload)[0], 400)
        self.assertEqual(self.request('POST', '/api/buttons/check', {},
            {'Content-Type': 'application/json', 'Origin': 'http://other.example'})[0], 403)
        self.assertEqual(self.request('GET', '/api/buttons/check')[0], 404)
        self.listener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
