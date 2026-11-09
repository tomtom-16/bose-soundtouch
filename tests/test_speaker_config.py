"""Configuration persistence and HTTP tests using isolated files and no speakers."""
import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

from soundtouch import config, server as web_server


class SpeakerConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for constant, filename in [('SPEAKERS_PATH', 'speakers.json'),
                                   ('PRESETS_PATH', 'presets.json'), ('CONFIG_PATH', 'config.json')]:
            replacement = patch.object(config, constant, os.path.join(self.directory.name, filename))
            replacement.start()
            self.addCleanup(replacement.stop)
        self.original = {'Living': {'ip': '192.0.2.1', 'deviceID': 'AABBCCDDEEFF', 'extra': True}}
        config.save_json(config.SPEAKERS_PATH, self.original)
        config.save_json(config.PRESETS_PATH, {'default': {'1': 'radio'}, 'Living': {'2': 'other'}})
        config.save_json(config.CONFIG_PATH, {'master': 'Living', 'sync': False})
        self.row = {'original_name': 'Living', 'name': 'Salon', 'ip': '192.0.2.2',
                    'deviceID': 'aabbccddeeff', 'model': 'SoundTouch 10'}

    def test_rename_preserves_metadata_presets_and_master(self):
        result = config.configure_speakers([self.row])
        self.assertEqual(config.load_json(config.SPEAKERS_PATH, None), result)
        self.assertEqual(result['Salon']['deviceID'], 'AABBCCDDEEFF')
        self.assertTrue(result['Salon']['extra'])
        self.assertEqual(config.load_json(config.PRESETS_PATH, {}),
                         {'default': {'1': 'radio'}, 'Salon': {'2': 'other'}})
        self.assertEqual(config.load_json(config.CONFIG_PATH, {}), {'master': 'Salon', 'sync': False})

    def test_invalid_changes_do_not_write_any_files(self):
        invalid = [{**self.row, 'ip': '999.1.1.1'}, {**self.row, 'name': ''},
                   {**self.row, 'name': 'default'}, {**self.row, 'deviceID': 'bad'},
                   {**self.row, 'original_name': 'Missing'}, {**self.row, 'ip': 123}]
        for row in invalid:
            with self.subTest(row=row), self.assertRaises(ValueError):
                config.configure_speakers([row])
        for key in ['ip', 'name', 'deviceID']:
            second = {'name': 'Bath', 'ip': '192.0.2.3', 'deviceID': '112233445566'}
            second[key] = self.row[key]
            with self.subTest(duplicate=key), self.assertRaises(ValueError):
                config.configure_speakers([self.row, second])
        self.assertEqual(config.load_json(config.SPEAKERS_PATH, {}), self.original)
        self.assertIn('Living', config.load_json(config.PRESETS_PATH, {}))

    def test_add_and_remove_all_retains_dormant_presets(self):
        config.configure_speakers([self.row, {'name': 'Kitchen', 'ip': '192.0.2.3', 'deviceID': '112233445566'}])
        config.configure_speakers([])
        self.assertEqual(config.load_json(config.SPEAKERS_PATH, None), {})
        self.assertNotIn('master', config.load_json(config.CONFIG_PATH, {}))
        self.assertEqual(config.load_json(config.PRESETS_PATH, {})['Salon'],
                         {'2': 'other'})
        self.assertEqual(config.load_json(config.PRESETS_PATH, {})['Kitchen'],
                         {})

    def test_stale_revision_cannot_overwrite_concurrent_changes(self):
        for path in (config.SPEAKERS_PATH, config.PRESETS_PATH, config.CONFIG_PATH):
            with self.subTest(path=path):
                revision = config.speaker_snapshot()['revision']
                changed = config.load_json(path, {})
                changed['concurrent'] = {}
                config.save_json(path, changed)
                with self.assertRaisesRegex(ValueError, 'Configuration modifiée'):
                    config.configure_speakers([self.row], revision)
                self.assertEqual(config.load_json(path, {}), changed)

    def test_atomic_write_failure_keeps_original_file(self):
        with patch.object(config.os, 'replace', side_effect=OSError('read only')):
            with self.assertRaises(OSError):
                config.save_json(config.SPEAKERS_PATH, {'partial': {}})
        self.assertEqual(config.load_json(config.SPEAKERS_PATH, {}), self.original)
        self.assertFalse(any(name.startswith('.config-') for name in os.listdir(self.directory.name)))

    def test_atomic_save_preserves_existing_permissions(self):
        os.chmod(config.SPEAKERS_PATH, 0o640)
        config.save_json(config.SPEAKERS_PATH, self.original)
        self.assertEqual(os.stat(config.SPEAKERS_PATH).st_mode & 0o777, 0o640)

    def test_failed_speaker_write_rolls_back_renames(self):
        save = config.save_json
        def fail_speakers(path, value):
            if path == config.SPEAKERS_PATH: raise OSError('read only')
            save(path, value)
        with patch.object(config, 'save_json', side_effect=fail_speakers):
            with self.assertRaises(OSError): config.configure_speakers([self.row])
        self.assertEqual(config.load_json(config.SPEAKERS_PATH, {}), self.original)
        self.assertIn('Living', config.load_json(config.PRESETS_PATH, {}))
        self.assertEqual(config.load_json(config.CONFIG_PATH, {})['master'], 'Living')

    def test_http_read_save_invalid_and_write_error(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), web_server.ProxyHandler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(method, body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=3)
            conn.request(method, '/api/speakers', body=body, headers=headers or {})
            response = conn.getresponse()
            result = response.status, json.loads(response.read())
            conn.close()
            return result
        snapshot = request('GET')[1]
        self.assertEqual(snapshot['speakers'], self.original)
        body = json.dumps({'speakers': [self.row], 'revision': snapshot['revision']})
        headers = {'Content-Type': 'application/json'}
        code, saved = request('POST', body, headers)
        self.assertEqual(code, 200)
        self.assertNotEqual(saved['revision'], snapshot['revision'])
        code, rejected = request('POST', body, headers)
        self.assertEqual(code, 400)
        self.assertIn('Configuration modifiée', rejected['error'])
        self.assertEqual(request('POST', '{broken', headers)[0], 400)
        self.assertEqual(request('POST', body)[0], 415)
        self.assertEqual(request('POST', body, {**headers, 'Origin': 'http://example.com'})[0], 403)
        with patch.object(config, 'save_json', side_effect=OSError('read only')):
            valid = json.dumps({'speakers': []})
            code, result = request('POST', valid, headers)
            self.assertEqual(code, 500)
            self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
