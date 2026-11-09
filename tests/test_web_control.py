"""Web targeting tests; no requests are sent to real speakers."""
import io
import unittest
from unittest.mock import call, patch
from soundtouch import config, device, playback


class WebControlTests(unittest.TestCase):
    def setUp(self):
        self.config = {config.SPEAKERS_PATH: {
            'Living': {'ip': '192.0.2.1', 'deviceID': 'AA'},
            'Bath': {'ip': '192.0.2.2', 'deviceID': 'BB'}},
            config.STATIONS_PATH: {'radio': {}},
            config.PRESETS_PATH: {'default': {'1': 'radio'}, 'Bath': {'2': 'radio'}},
            config.STATE_PATH: {'master_ip': '192.0.2.1', 'station': 'radio'}}
        self.loader = patch.object(config, 'load_json', side_effect=lambda p, d: self.config.get(p, d))
        self.loader.start()
        self.addCleanup(self.loader.stop)

    @patch.object(playback.time, 'sleep')
    @patch.object(playback, 'api_master_ip', return_value='192.0.2.1')
    @patch.object(device, 'set_volume')
    @patch.object(playback, 'build_zone')
    @patch.object(playback, 'play')
    @patch.object(device, 'force_invalid')
    @patch.object(playback, 'detach_speaker')
    def test_target_overrides_sync_and_group_volume(self, detach, reset, play, zone, volume, master, sleep):
        playback.api_play({'sync': True}, 'radio', 25, 'Bath')
        detach.assert_called_once_with('192.0.2.2')
        play.assert_called_once_with({'sync': True}, 'radio', '192.0.2.2', 25)
        zone.assert_not_called()
        volume.assert_not_called()
        master.assert_not_called()
        sleep.assert_called_once_with(1.5)

    @patch.object(playback.time, 'sleep')
    @patch.object(playback, 'api_master_ip', return_value='192.0.2.1')
    @patch.object(device, 'set_volume')
    @patch.object(playback, 'build_zone')
    @patch.object(playback, 'play')
    @patch.object(device, 'force_invalid')
    def test_all_overrides_sync_false(self, reset, play, zone, volume, master, sleep):
        playback.api_play({'sync': False}, 'radio', 25, 'all')
        zone.assert_called_once_with('192.0.2.1')
        volume.assert_called_once_with('192.0.2.2', 25)
        self.assertEqual(sleep.call_args_list, [call(1.5), call(1.5)])

    @patch.object(device, 'force_invalid')
    def test_invalid_target_or_station_has_no_playback_side_effect(self, reset):
        with self.assertRaises(ValueError):
            playback.api_play({}, 'radio', target='192.0.2.99')
        with self.assertRaises(ValueError):
            playback.api_play({}, 'missing', target='Bath')
        reset.assert_not_called()

    @patch.object(device, 'get_zone_master', return_value='192.0.2.1')
    def test_detaching_member_keeps_other_members(self, master):
        zone = b'<zone master="AA"><member ipaddress="192.0.2.1">AA</member><member ipaddress="192.0.2.2">BB</member></zone>'
        for target, expected in [('192.0.2.2', ['192.0.2.2']), ('192.0.2.1', ['192.0.2.1', '192.0.2.2'])]:
            with self.subTest(target=target), patch.object(playback.urllib.request, 'urlopen', side_effect=[io.BytesIO(zone), io.BytesIO(b'')]) as request:
                playback.detach_speaker(target)
                sent = request.call_args.args[0]
                self.assertEqual(sent.full_url, 'http://192.0.2.1:8090/removeZone')
                self.assertEqual([m.get('ipaddress') for m in playback.ET.fromstring(sent.data)], expected)

    @patch.object(playback.os, 'remove')
    @patch.object(device, 'standby')
    @patch.object(playback, 'detach_speaker')
    def test_targeted_stop_preserves_other_speaker_intent(self, detach, standby, remove):
        playback.api_stop_target('Bath')
        standby.assert_called_once_with('192.0.2.2')
        remove.assert_not_called()
        playback.api_stop_target('Living')
        remove.assert_called_once_with(config.STATE_PATH)

    @patch.object(device, 'get_volume', return_value=20)
    @patch.object(device, 'now_playing_fields', side_effect=[('UPNP', 'PLAY_STATE', 'radio'), ('UPNP', 'PLAY_STATE', 'other')])
    def test_status_exposes_presets_and_individual_sources(self, fields, volume):
        result = playback.api_status({})
        self.assertEqual(result['presets'], self.config[config.PRESETS_PATH])
        self.assertEqual(result['default_speaker'], 'Living')
        self.assertEqual(result['speakers'][0]['station'], 'radio')
        self.assertIsNone(result['speakers'][1]['station'])
        self.assertEqual(result['speakers'][1]['title'], 'other')


if __name__ == '__main__':
    unittest.main()
