"""Preset persistence and HTTP validation; isolated files, no Bose requests."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import bose_radio as radio


class PresetConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for constant in ('SPEAKERS_PATH', 'STATIONS_PATH', 'PRESETS_PATH'):
            replacement = patch.object(radio, constant, os.path.join(directory.name, constant + '.json'))
            replacement.start()
            self.addCleanup(replacement.stop)
        self.original = {'default': {'1': 'radio'}, 'Living': {'2': 'other'},
                         'Removed': {'3': 'radio'}}
        radio.save_json(radio.SPEAKERS_PATH, {'Living': {'ip': '192.0.2.1'}})
        radio.save_json(radio.STATIONS_PATH, {'radio': {'note': 'Radio'}, 'other': {}})
        radio.save_json(radio.PRESETS_PATH, self.original)

    def test_global_update_retains_individual_and_dormant_scopes(self):
        result = radio.configure_presets({'default': {'6': 'other'}})
        self.assertEqual(result, {**self.original, 'default': {'6': 'other'}})
        self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), result)

    def test_individual_override_disable_and_reset(self):
        result = radio.configure_presets({'Living': {'1': None, '6': 'other'}})
        self.assertEqual(result['Living'], {'1': None, '6': 'other'})
        result = radio.configure_presets({'Living': {}})
        self.assertEqual(result['Living'], {})
        self.assertEqual(result['default'], {'1': 'radio'})

    def test_invalid_payload_is_atomic(self):
        for invalid in (None, [], {'Missing': {}}, {'Living': []},
                        {'default': {'0': 'radio'}}, {'default': {'7': 'radio'}},
                        {'default': {'1': None}}, {'Living': {'1': ''}},
                        {'Living': {'1': ['radio']}},
                        {'default': {'1': 'other'}, 'Living': {'1': 'missing'}}):
            with self.subTest(payload=invalid), self.assertRaises(ValueError):
                radio.configure_presets(invalid)
            self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), self.original)

    def test_write_failure_preserves_file(self):
        with patch.object(radio.os, 'replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                radio.configure_presets({'default': {}})
        self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), self.original)

    def test_http_read_save_validation_and_write_errors(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), radio.ProxyHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request(method, '/api/presets', body=body, headers=headers or {})
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result

        code, snapshot = request('GET')
        self.assertEqual(code, 200)
        self.assertEqual(snapshot['presets'], self.original)
        self.assertEqual(snapshot['stations'], [{'id': 'radio', 'note': 'Radio'}, {'id': 'other', 'note': ''}])
        self.assertEqual(list(snapshot['speakers']), ['Living'])
        body = json.dumps({'presets': {'default': {'2': 'other'}, 'Living': {'1': None}}})
        headers = {'Content-Type': 'application/json'}
        self.assertEqual(request('POST', body, headers)[0], 200)
        self.assertEqual(request('POST', '{broken', headers)[0], 400)
        self.assertEqual(request('POST', '{"presets": []}', headers)[0], 400)
        self.assertEqual(request('POST', body)[0], 415)
        self.assertEqual(request('POST', body, {**headers, 'Origin': 'http://example.com'})[0], 403)
        # A speaker renamed while the editor is open must not receive stale presets.
        radio.save_json(radio.SPEAKERS_PATH, {'Salon': {'ip': '192.0.2.1'}})
        self.assertEqual(request('POST', body, headers)[0], 400)
        with patch.object(radio, 'save_json', side_effect=OSError('read only')):
            code, result = request('POST', '{"presets": {"default": {}}}', headers)
            self.assertEqual(code, 500)
            self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
