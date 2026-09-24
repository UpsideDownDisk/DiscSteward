"""Timing regression checks; runnable with unittest without extra packages."""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from bluray_map.models import PlayItem, Playlist, Scan
from bluray_map.ripmatch import duration_details, match_rips


def playlist(name, clip, duration=2550.673111):
    return Playlist(name, int(name[:5]),
                    [PlayItem(clip, clip+'.m2ts', 0, round(duration*45000), duration)], duration)


class DurationTests(unittest.TestCase):
    def probe(self, video, container='2551.648'):
        data={'streams': [video], 'format': {'duration': container}}
        with patch('bluray_map.ripmatch.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, json.dumps(data))):
            return duration_details('ffprobe', Path('example.mkv'))

    def test_long_audio_does_not_change_video_match(self):
        result=self.probe({'tags': {'DURATION-eng':'00:42:30.673125000'}})
        self.assertAlmostEqual(result['duration_seconds'],2550.673125)
        self.assertEqual(result['duration_source'],'video_track_tag')
        self.assertEqual(result['container_duration_seconds'],2551.648)

    def test_plain_tag_and_stream_duration(self):
        self.assertEqual(self.probe({'tags':{'DURATION':'00:42:30.673125'}})['duration_source'],'video_track_tag')
        self.assertEqual(self.probe({'duration':'2550.673125'})['duration_source'],'video_stream')

    def test_invalid_tag_falls_back_without_widening_tolerance(self):
        for value in ['N/A', 'nan', '-1', '00:99:00']:
            with self.subTest(value=value):
                self.assertEqual(self.probe({'tags':{'DURATION-eng':value}})['duration_source'],'container_fallback')

    def test_no_valid_timing_and_failed_probe(self):
        self.assertIsNone(self.probe({},'nan')['duration_seconds'])
        with patch('bluray_map.ripmatch.subprocess.run',side_effect=OSError('missing')):
            self.assertIsNone(duration_details('missing',Path('example.mkv'))['duration_seconds'])

    def mapping(self, playlists):
        timing={'duration_seconds':2550.673125,'duration_source':'video_track_tag',
                'container_duration_seconds':2551.648}
        with patch.object(Path,'rglob',return_value=[Path('example.mkv')]), \
             patch('bluray_map.ripmatch.duration_details',return_value=timing):
            return match_rips(Scan('offline',playlists,[]),'unused','ffprobe',database_record={},local_record={})

    def test_alternates_match_but_close_unrelated_episode_does_not(self):
        result=self.mapping([playlist('00051.mpls','00001'),playlist('00061.mpls','00001'),
                             playlist('00056.mpls','00044',2550.339458)])
        self.assertEqual([len(e['matching_rips']) for e in result['episodes']],[1,0])
        self.assertNotIn('season',result['episodes'][0])
        self.assertEqual(result['unmatched_rips'],[])

    def test_same_duration_different_programmes_remain_unmatched(self):
        result=self.mapping([playlist('00051.mpls','00001'),playlist('00052.mpls','00040')])
        self.assertTrue(all(not e['matching_rips'] for e in result['episodes']))
        self.assertEqual(result['unmatched_rips'],['example.mkv'])
        self.assertTrue(result['warnings'])


if __name__ == '__main__':
    unittest.main()
