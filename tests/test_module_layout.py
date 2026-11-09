"""Vérifier l’extraction, ses chemins et le service web sur boucle locale."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from soundtouch import config, server

ROOT = Path(__file__).resolve().parent.parent


class ModuleLayoutTests(unittest.TestCase):
    def test_imports_and_paths_from_other_directory(self):
        code = '''
import importlib, json, pathlib, socket, subprocess, threading, urllib.request
from unittest.mock import patch
with patch.object(socket, "socket", side_effect=AssertionError("network")), \\
     patch.object(urllib.request, "urlopen", side_effect=AssertionError("HTTP")), \\
     patch.object(threading.Thread, "start", side_effect=AssertionError("thread")), \\
     patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
    for name in ("soundtouch", "config", "discovery", "device", "playback",
                 "streaming", "watchdog", "server", "cli", "bose_radio"):
        importlib.import_module(name if name in ("soundtouch", "bose_radio") else "soundtouch." + name)
from soundtouch import config, server
print(json.dumps([config.CONFIG_DIR, config.DATA_DIR, str(server.WEB_DIR)]))
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {k: v for k, v in os.environ.items() if k not in ('BOSE_CONFIG_DIR', 'BOSE_DATA_DIR')}
            env['PYTHONPATH'] = str(ROOT)
            def inspect_paths():
                return json.loads(subprocess.check_output([sys.executable, '-c', code], cwd=directory, env=env, text=True))
            self.assertEqual(inspect_paths(), [str(ROOT / 'config'), str(ROOT / 'data'), str(ROOT / 'web')])
            env.update(BOSE_CONFIG_DIR=str(Path(directory) / 'settings'), BOSE_DATA_DIR=str(Path(directory) / 'state'))
            self.assertEqual(inspect_paths(), [env['BOSE_CONFIG_DIR'], env['BOSE_DATA_DIR'], str(ROOT / 'web')])
            env.update(BOSE_CONFIG_DIR='settings', BOSE_DATA_DIR='state')
            self.assertEqual(inspect_paths(), [str(Path(directory).resolve() / 'settings'), str(Path(directory).resolve() / 'state'), str(ROOT / 'web')])
            output = subprocess.check_output([sys.executable, str(ROOT / 'bose_radio.py'), '--help'], cwd=directory, env=env, text=True)
            self.assertIn('{discover,list,serve,play,stop,status}', output)

    def test_dev_launcher_preserves_entrypoint_and_environment(self):
        import runpy
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            env_file = Path(directory) / '.env'
            env_file.write_text('BOSE_CONFIG_DIR=settings\nBOSE_DATA_DIR=state\n')
            dev = runpy.run_path(str(ROOT / 'scripts/dev.py'))
            with patch.object(sys, 'argv', ['dev.py', 'server', '--env-file', str(env_file)]), \
                    patch.object(runpy, 'run_path') as launch, \
                    patch.object(sys, 'path', list(sys.path)):
                dev['main']()
                launch.assert_called_once_with(str(ROOT / 'bose_radio.py'), run_name='__main__')
                self.assertEqual(sys.argv, [str(ROOT / 'bose_radio.py'), 'serve'])
                self.assertEqual(os.environ['BOSE_CONFIG_DIR'], str(ROOT / 'settings'))
                self.assertEqual(os.environ['BOSE_DATA_DIR'], str(ROOT / 'state'))

    def test_web_files_and_allowlist_with_empty_temporary_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / 'config'
            settings.mkdir()
            for name in ('config', 'speakers', 'stations', 'presets'):
                (settings / (name + '.json')).write_text('{}')
            with patch.multiple(config, **{name.upper() + '_PATH': str(settings / (name + '.json'))
                                         for name in ('config', 'speakers', 'stations', 'presets')},
                                STATE_PATH=str(Path(directory) / 'data/state.json')):
                httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.ProxyHandler)
                worker = threading.Thread(target=httpd.serve_forever, daemon=True)
                worker.start()
                try:
                    connection = http.client.HTTPConnection(*httpd.server_address, timeout=3)
                    for route, filename, content_type in (
                            ('/', 'index.html', 'text/html; charset=utf-8'),
                            ('/index.html', 'index.html', 'text/html; charset=utf-8'),
                            ('/styles.css', 'styles.css', 'text/css; charset=utf-8'),
                            ('/app.js', 'app.js', 'text/javascript; charset=utf-8')):
                        connection.request('GET', route)
                        response = connection.getresponse()
                        body = response.read()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.getheader('Content-Type'), content_type)
                        self.assertEqual(int(response.getheader('Content-Length')), len(body))
                        self.assertEqual(body, (ROOT / 'web' / filename).read_bytes())
                    connection.request('GET', '/api/status')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read())['speakers'], [])
                    for route in ('/config/config.json', '/data/state.json', '/soundtouch/config.py',
                                  '/../README.md', '/%2e%2e/README.md', '/web/app.js', '/absent.css'):
                        connection.request('GET', route)
                        response = connection.getresponse()
                        response.read()
                        self.assertEqual(response.status, 404, route)
                    connection.close()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    worker.join()
