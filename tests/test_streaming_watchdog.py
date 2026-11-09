"""Caractérisation sans réseau ni attente des flux et de la reprise."""
import io
import unittest
from unittest.mock import Mock, patch
from soundtouch import config, device, playback, server as web_server, streaming, watchdog as watchdog_module


class EndPolling(BaseException):
    pass


class StreamTests(unittest.TestCase):
    def test_header_errors_close_upstream_without_changing_connection_count(self):
        ip = '192.0.2.1'
        for stage in ('send_response', 'content_type', 'icy_name', 'end_headers'):
            for error_type in (OSError, BrokenPipeError, ConnectionResetError):
                for existing in (0, 1):
                    with self.subTest(stage=stage, error=error_type, existing=existing):
                        handler = Mock()
                        handler.client_address = (ip, 1234)
                        error = error_type('headers')
                        if stage in ('content_type', 'icy_name'):
                            handler.send_header.side_effect = (
                                [error] if stage == 'content_type' else [None, error])
                        else:
                            getattr(handler, stage).side_effect = error
                        upstream = Mock()
                        initial = {ip: existing} if existing else {}
                        with patch.dict(streaming._ACTIVE, initial, clear=True), \
                                patch.object(config, 'load_json', return_value={'radio': 'https://example.com/live'}), \
                                patch.object(streaming, 'open_upstream', return_value=('audio/mpeg', upstream)), \
                                patch.object(streaming, '_active_inc', wraps=streaming._active_inc) as inc, \
                                patch.object(streaming, '_active_dec', wraps=streaming._active_dec) as dec:
                            with self.assertRaises(error_type) as raised:
                                streaming.stream_audio(handler, 'radio')
                            self.assertIs(raised.exception, error)
                            self.assertEqual(streaming._ACTIVE, initial)
                            inc.assert_not_called()
                            dec.assert_not_called()
                            upstream.recv.assert_not_called()
                            handler.wfile.write.assert_not_called()
                            upstream.close.assert_called_once_with()

    def test_transfer_cleanup_preserves_other_active_connections(self):
        ip = '192.0.2.1'
        for failure in ('normal', 'read', 'write', 'reset', 'close'):
            for existing in (0, 1):
                with self.subTest(failure=failure, existing=existing):
                    handler = Mock()
                    handler.client_address = (ip, 1234)
                    upstream = Mock()
                    chunks = iter([b'audio', b''])

                    def recv(size):
                        self.assertEqual(size, 16384)
                        self.assertEqual(streaming._ACTIVE[ip], existing + 1)
                        if failure == 'read':
                            raise OSError('read')
                        return next(chunks)

                    def write(chunk):
                        self.assertEqual(streaming._ACTIVE[ip], existing + 1)
                        if failure == 'write':
                            raise BrokenPipeError('write')
                        if failure == 'reset':
                            raise ConnectionResetError('write')

                    upstream.recv.side_effect = recv
                    handler.wfile.write.side_effect = write
                    if failure == 'close':
                        upstream.close.side_effect = OSError('close')
                    initial = {ip: existing} if existing else {}
                    with patch.dict(streaming._ACTIVE, initial, clear=True), \
                            patch.object(config, 'load_json', return_value={'radio': 'https://example.com/live'}), \
                            patch.object(streaming, 'open_upstream', return_value=('audio/mpeg', upstream)), \
                            patch.object(streaming, '_active_inc', wraps=streaming._active_inc) as inc, \
                            patch.object(streaming, '_active_dec', wraps=streaming._active_dec) as dec:
                        streaming.stream_audio(handler, 'radio')
                        self.assertEqual(streaming._ACTIVE, initial)
                        inc.assert_called_once_with(ip)
                        dec.assert_called_once_with(ip)
                        upstream.close.assert_called_once_with()
                    handler.send_response.assert_called_once_with(200)
                    self.assertEqual(handler.send_header.call_args_list,
                                     [unittest.mock.call('Content-Type', 'audio/mpeg'),
                                      unittest.mock.call('icy-name', 'radio')])
                    handler.end_headers.assert_called_once_with()

    def test_transfer_always_closes_upstream_and_removes_connection(self):
        for failure in (None, BrokenPipeError(), ConnectionResetError(), OSError('read')):
            with self.subTest(failure=failure):
                upstream = Mock()
                upstream.recv.side_effect = [b'audio', failure] if failure else [b'audio', b'']
                handler = object.__new__(web_server.ProxyHandler)
                handler.path = '/stream/radio'
                handler.client_address = ('192.0.2.1', 1234)
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                with patch.object(config, 'get_config', return_value={}), \
                        patch.object(config, 'load_json', return_value={'radio': 'https://example.com/live'}), \
                        patch.object(streaming, 'open_upstream', return_value=('audio/mpeg', upstream)):
                    handler.do_GET()
                self.assertEqual(handler.wfile.getvalue(), b'audio')
                upstream.close.assert_called_once()
                self.assertFalse(streaming._active_has('192.0.2.1'))

    def test_client_write_error_closes_upstream_and_clears_count(self):
        handler = Mock()
        handler.client_address = ('192.0.2.1', 1234)
        handler.wfile.write.side_effect = BrokenPipeError()
        upstream = Mock()
        upstream.recv.return_value = b'audio'
        with patch.object(config, 'load_json', return_value={'radio': 'https://example.com/live'}), \
                patch.object(streaming, 'open_upstream', return_value=('audio/mpeg', upstream)):
            streaming.stream_audio(handler, 'radio')
        upstream.close.assert_called_once()
        self.assertFalse(streaming._active_has('192.0.2.1'))

    def test_open_failure_returns_502_without_active_connection(self):
        handler = Mock()
        handler.client_address = ('192.0.2.1', 1234)
        with patch.object(config, 'load_json', return_value={'radio': 'https://example.com/live'}), \
                patch.object(streaming, 'open_upstream', side_effect=OSError('offline')):
            streaming.stream_audio(handler, 'radio')
        handler.send_response.assert_called_once_with(502)
        handler.wfile.write.assert_not_called()
        self.assertFalse(streaming._active_has('192.0.2.1'))

    def test_parallel_connection_count(self):
        ip = '192.0.2.2'
        streaming._active_inc(ip)
        streaming._active_inc(ip)
        streaming._active_dec(ip)
        self.assertTrue(streaming._active_has(ip))
        streaming._active_dec(ip)
        self.assertFalse(streaming._active_has(ip))


class WatchdogTests(unittest.TestCase):
    def run_polls(self, states, fields, active=False, times=None, play_error=None):
        intention = {'station': 'radio', 'master_ip': '192.0.2.1', 'volume': 12}
        with patch.object(watchdog_module.time, 'sleep', side_effect=[None] * len(states) + [EndPolling()]), \
                patch.object(watchdog_module.time, 'time', side_effect=times or [1000] * 30), \
                patch.object(config, 'load_json', side_effect=[intention if s else None for s in states]), \
                patch.object(device, 'zone_master_ip', return_value='192.0.2.2'), \
                patch.object(device, 'now_playing_fields', side_effect=fields), \
                patch.object(streaming, '_active_has', return_value=active), \
                patch.object(playback, 'play', side_effect=play_error) as play:
            with self.assertRaises(EndPolling):
                watchdog_module.watchdog({})
            return play

    def test_heals_only_sustained_stall_and_current_master(self):
        play = self.run_polls([True, True], [('UPNP', 'PLAY_STATE', '')] * 2)
        play.assert_called_once_with({}, 'radio', '192.0.2.2', 12)
        play = self.run_polls([True, True], [('INVALID_SOURCE', '', '')] * 2)
        self.assertEqual(play.call_count, 1)

    def test_intent_absence_other_sources_live_stream_and_errors_reset_grace(self):
        for middle in [('STANDBY', '', ''), ('AUX', '', ''), OSError('offline')]:
            with self.subTest(middle=middle):
                play = self.run_polls([True] * 3, [('UPNP', 'PLAY_STATE', ''), middle, ('UPNP', 'BUFFERING_STATE', '')])
                play.assert_not_called()
        self.run_polls([True, False, True], [('UPNP', 'PLAY_STATE', '')] * 2).assert_not_called()
        self.run_polls([True] * 3, [('UPNP', 'PLAY_STATE', '')] * 3, active=True).assert_not_called()

    def test_cooldown_and_failed_heal(self):
        fields = [('INVALID_SOURCE', '', '')] * 4
        self.assertEqual(self.run_polls([True] * 4, fields, times=[1000, 1000, 1010]).call_count, 1)
        for error in (device.RendererWedged('wedged'), SystemExit('unknown'), OSError('offline')):
            with self.subTest(error=error):
                self.assertEqual(self.run_polls([True] * 3, fields[:3], play_error=error).call_count, 2)
