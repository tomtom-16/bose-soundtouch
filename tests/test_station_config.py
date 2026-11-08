"""Catalogue edits use isolated files and never contact speakers."""
import json
import http.client
import threading
from http.server import ThreadingHTTPServer
import os
import tempfile
import unittest
from unittest.mock import patch
import bose_radio as radio


class StationConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for key in ('STATIONS_PATH', 'PRESETS_PATH', 'SPEAKERS_PATH'):
            mock = patch.object(radio, key, os.path.join(directory.name, key + '.json'))
            mock.start()
            self.addCleanup(mock.stop)
        radio.save_json(radio.STATIONS_PATH, {'radio': {'url': 'https://example.com/live', 'note': 'Radio', 'extra': 1}, 'other': 'tunein:s123'})
        radio.save_json(radio.SPEAKERS_PATH, {'Salon': {}, 'Cuisine': {}, 'Chambre': {}})
        self.presets = {'default': {'1': 'radio', '2': 'other'}, 'Salon': {'1': 'other', '2': 'radio'},
                        'Chambre': {'1': None}, 'Removed': {'3': 'radio'}}
        radio.save_json(radio.PRESETS_PATH, self.presets)

    def write(self, **payload):
        return radio.configure_station({'revision': radio.station_snapshot()['revision'], **payload})

    def test_create_edit_and_legacy_sources(self):
        result = self.write(action='save', name='Nouvelle', url='tunein:s42')
        key = next(k for k in result if k not in ('radio', 'other'))
        self.assertEqual(result[key]['note'], 'Nouvelle')
        self.write(action='save', id='radio', name='Renommée', url='https://example.com/new')
        self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), self.presets)
        self.assertEqual(radio.load_json(radio.STATIONS_PATH, {})['radio']['extra'], 1)
        self.write(action='save', id='other', name='Autre', url='http://example.com/live')

    def test_usage_inheritance_and_delete(self):
        usage = radio.station_snapshot()['usages']['radio']
        self.assertEqual(usage['effective'], [{'scope': 'Salon', 'numbers': ['2']}, {'scope': 'Cuisine', 'numbers': ['1']}])
        self.assertIn({'scope': 'Removed', 'numbers': ['3']}, usage['direct'])
        self.write(action='delete', id='radio', confirmed=True)
        self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), {
            'default': {'2': 'other'}, 'Salon': {'1': 'other', '2': None},
            'Chambre': {'1': None}, 'Removed': {'3': None}})
        self.assertNotIn('radio', radio.load_json(radio.STATIONS_PATH, {}))

    def test_validation_and_confirmation_do_not_write(self):
        original = radio.station_snapshot()
        for payload in [dict(action='delete', id='radio'), dict(action='save', name='', url='https://example.com'),
                        dict(action='save', name='radio', url='https://example.com'),
                        dict(action='save', name='New', url='file:///tmp/audio'),
                        dict(action='save', name='New', url='tunein:bad'),
                        dict(action='save', id=[], name='New', url='https://example.com')]:
            with self.subTest(payload=payload), self.assertRaises(ValueError): self.write(**payload)
            self.assertEqual(radio.station_snapshot(), original)

    def test_stale_confirmation_rejected(self):
        revision = radio.station_snapshot()['revision']
        radio.save_json(radio.PRESETS_PATH, {'default': {'6': 'radio'}})
        with self.assertRaisesRegex(ValueError, 'Configuration modifiée'):
            radio.configure_station(dict(action='delete', id='radio', confirmed=True, revision=revision))
        self.assertIn('radio', radio.load_json(radio.STATIONS_PATH, {}))

    def test_http_catalogue_crud_and_guards(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), radio.ProxyHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(method, payload=None, headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            connection.request(method, '/api/stations', body=json.dumps(payload) if payload is not None else None,
                               headers=headers if headers is not None else {'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        code, snapshot = request('GET')
        self.assertEqual(code, 200)
        payload = dict(action='save', id='radio', name='Updated', url='https://example.com/new', revision=snapshot['revision'])
        self.assertEqual(request('POST', payload, {})[0], 415)
        self.assertEqual(request('POST', payload, {'Content-Type': 'application/json', 'Origin': 'https://example.com'})[0], 403)
        self.assertEqual(request('POST', payload)[0], 200)
        self.assertEqual(request('POST', payload)[0], 400)
        snapshot = request('GET')[1]
        self.assertEqual(snapshot['stations']['radio']['note'], 'Updated')
        payload = dict(action='delete', id='radio', confirmed=True, revision=snapshot['revision'])
        with patch.object(radio.os, 'replace', side_effect=OSError('read only')):
            self.assertEqual(request('POST', payload)[0], 500)
        self.assertEqual(request('POST', payload)[0], 200)
        self.assertNotIn('radio', request('GET')[1]['stations'])

    def test_catalogue_write_failure_restores_presets(self):
        save = radio.save_json
        def failing_save(path, value):
            if path == radio.STATIONS_PATH: raise OSError('read only')
            save(path, value)
        with patch.object(radio, 'save_json', side_effect=failing_save), self.assertRaises(OSError):
            self.write(action='delete', id='radio', confirmed=True)
        self.assertEqual(radio.load_json(radio.PRESETS_PATH, {}), self.presets)
        self.assertIn('radio', radio.load_json(radio.STATIONS_PATH, {}))


if __name__ == '__main__': unittest.main()
