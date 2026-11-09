"""Découverte simulée : aucun paquet n'est envoyé vers le réseau local."""
import http.client
import json
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from soundtouch import config, discovery, server as web_server


class SpeakerDiscoveryTests(unittest.TestCase):
    def test_info_parses_identity_and_closes_connection(self):
        with patch.object(discovery.http.client, 'HTTPConnection') as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = (b'<info deviceID="aabbccddeeff"><name>Salon &amp; cuisine</name>'
                                          b'<type>SoundTouch 10</type></info>')
            self.assertEqual(discovery.speaker_info('192.0.2.1'), {
                'name': 'Salon & cuisine', 'ip': '192.0.2.1',
                'deviceID': 'AABBCCDDEEFF', 'model': 'SoundTouch 10'})
            factory.assert_called_once_with('192.0.2.1', 8090, timeout=1.5)
            connection.request.assert_called_once_with('GET', '/info')
            connection.close.assert_called_once()

    def test_rejects_non_lan_addresses_before_contact(self):
        with patch.object(discovery.http.client, 'HTTPConnection') as factory:
            for ip in (None, 123, 'example.com', '8.8.8.8', '127.0.0.1', '0.0.0.0',
                       '239.255.255.250', '255.255.255.255', '::1', '192.0.2.1/path'):
                with self.subTest(ip=ip), self.assertRaises(ValueError):
                    discovery.speaker_info(ip)
            factory.assert_not_called()

    def test_info_rejects_redirects_invalid_xml_and_non_speakers(self):
        with patch.object(discovery.http.client, 'HTTPConnection') as factory:
            connection = factory.return_value
            response = connection.getresponse.return_value
            for code, body in [(302, b''), (200, b'not XML'), (200, b'<info/>'),
                               (200, b'<other deviceID="AABBCCDDEEFF"><name>Fake</name></other>'),
                               (200, b'x' * 65537)]:
                with self.subTest(code=code, body=body[:30]), self.assertRaises(ValueError):
                    response.status = code
                    response.read.return_value = body
                    discovery.speaker_info('192.0.2.1')
            connection.getresponse.side_effect = socket.timeout()
            with self.assertRaisesRegex(ValueError, 'Impossible'):
                discovery.speaker_info('192.0.2.1')
            self.assertEqual(connection.close.call_count, 6)

    def test_scan_deduplicates_identity_ignores_invalid_devices_and_never_writes(self):
        sock = MagicMock()
        sock.recvfrom.side_effect = [
            (b'HTTP/1.1 200 OK\r\nLOCATION: http://8.8.8.8/evil', ('192.0.2.1', 1900)),
            (b'HTTP/1.1 200 OK', ('192.0.2.1', 1900)),
            (b'HTTP/1.1 200 OK', ('192.0.2.2', 1900)),
            (b'HTTP/1.1 200 OK', ('192.0.2.3', 1900)),
            (b'NOTIFY * HTTP/1.1', ('192.0.2.4', 1900)), socket.timeout()]
        def identify(ip):
            if ip == '192.0.2.3': raise ValueError('Not Bose')
            return {'name': 'Salon', 'ip': ip, 'deviceID': 'AABBCCDDEEFF', 'model': 'ST10'}
        with patch.object(discovery.socket, 'socket') as factory, \
                patch.object(discovery, 'speaker_info', side_effect=identify) as info, \
                patch.object(config, 'save_json') as save:
            factory.return_value.__enter__.return_value = sock
            found = discovery.scan_speakers()
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]['ip'], '192.0.2.1')
            self.assertEqual({call.args[0] for call in info.call_args_list},
                             {'192.0.2.1', '192.0.2.2', '192.0.2.3'})
            self.assertIn(b'ST: ssdp:all\r\n', sock.sendto.call_args.args[0])
            save.assert_not_called()

    def test_scan_empty_busy_and_network_failure_release_lock(self):
        with discovery._DISCOVERY_LOCK:
            with self.assertRaisesRegex(ValueError, 'déjà en cours'):
                discovery.scan_speakers()
        with patch.object(discovery.socket, 'socket', side_effect=OSError('No network')):
            with self.assertRaises(OSError): discovery.scan_speakers()
        with patch.object(discovery.socket, 'socket') as factory:
            factory.return_value.__enter__.return_value.recvfrom.side_effect = socket.timeout()
            self.assertEqual(discovery.scan_speakers(), [])

    def test_http_discovery_and_identify_guard_origin_and_do_not_save(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), web_server.ProxyHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(path, payload, headers=None):
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            connection.request('POST', path, json.dumps(payload),
                               headers or {'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        with patch.object(discovery, 'scan_speakers', return_value=[]) as scan, \
                patch.object(discovery, 'speaker_info', return_value={'name': 'Salon'}) as info, \
                patch.object(config, 'save_json') as save:
            path = '/api/speakers/discover'
            self.assertEqual(request(path, {}, {'Content-Type': 'application/json',
                                                'Origin': 'http://elsewhere.example'})[0], 403)
            scan.assert_not_called()
            self.assertEqual(request(path, {})[1]['speakers'], [])
            self.assertEqual(request('/api/speakers/identify', {'ip': '192.0.2.1'})[0], 200)
            info.assert_called_once_with('192.0.2.1')
            with patch.object(discovery, 'scan_speakers', side_effect=OSError()):
                code, result = request(path, {})
                self.assertEqual(code, 400)
                self.assertIn('réseau', result['error'])
            save.assert_not_called()


if __name__ == '__main__':
    unittest.main()
