"""Offline database fixtures: no discs, ripping, or live service required."""
import hashlib
import json
import struct
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from bluray_map.discdb import content_hash, dvd_disc_id, lookup, assignments
from bluray_map.discovery import scan
from bluray_map.ripmatch import match_rips
from test_discsteward import test_directory
from test_dvd import make_dvd


class DiscDbTests(unittest.TestCase):
    def record(self, identifier, kind='global_disc_id'):
        return {kind:identifier, 'format':'DVD', 'media':{'title':'Example'},
                'release':{'title':'DVD edition'}, 'disc':{'name':'Disc 1'},
                'titles':[{'source_file':'01', 'duration':'0:25:00',
                           'item':{'season':'2', 'episode':'7', 'title':'Example episode'}}]}

    def response(self, value):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(value).encode()
        return response

    def test_dvd_hash_uses_all_direct_files_in_filename_order(self):
        with test_directory() as temp:
            root = Path(temp)/'VIDEO_TS'; root.mkdir()
            for name, size in [('VTS_01_1.VOB', 9), ('VIDEO_TS.IFO', 4), ('VIDEO_TS.BUP', 3)]:
                (root/name).write_bytes(b'x'*size)
            (root/'nested').mkdir(); (root/'nested'/'ignored').write_bytes(b'ignore')
            expected = hashlib.md5(struct.pack('<qqq', 3, 4, 9)).hexdigest().upper()
            self.assertEqual(content_hash(root), expected)
            self.assertEqual(content_hash(temp), expected)

    def test_dvd_id_uses_ifo_bytes_and_caps_title_sets_at_nine(self):
        with test_directory() as temp:
            root = Path(temp)/'VIDEO_TS'; root.mkdir()
            vmg = bytearray(2048); vmg[:12] = b'DVDVIDEO-VMG'; vmg[63] = 10
            (root/'VIDEO_TS.IFO').write_bytes(vmg)
            for n in range(1, 11): (root/f'VTS_{n:02}_0.IFO').write_bytes(bytes([n])*2048)
            expected = hashlib.md5(vmg+b''.join(bytes([n])*2048 for n in range(1,10))).hexdigest().upper()
            self.assertEqual(dvd_disc_id(temp), expected)

    def test_lookup_id_then_hash_fallback_and_rejects_wrong_edition(self):
        with test_directory() as temp:
            root = make_dvd(temp)
            with patch('bluray_map.discdb.dvd_disc_id', return_value='A'*32), patch('bluray_map.discdb.urllib.request.urlopen') as url:
                url.side_effect = [OSError('not found'), self.response(self.record(content_hash(root), 'content_hash'))]
                result = lookup(root)
                self.assertEqual(result['match_method'], 'dischash')
                self.assertEqual(result['series'], 'Example')
                self.assertIn('/discid/', url.call_args_list[0].args[0].full_url)
                url.side_effect = None; url.return_value = self.response(self.record('B'*32))
                self.assertIsNone(lookup(root))

    def test_conflicting_releases_do_not_verify_episode(self):
        with test_directory() as temp:
            root = make_dvd(temp); first = self.record('A'*32); second = self.record('A'*32)
            second['titles'][0]['item']['episode'] = '8'
            with patch('bluray_map.discdb.dvd_disc_id', return_value='A'*32), patch('bluray_map.discdb.urllib.request.urlopen', return_value=self.response([first,second])):
                result = lookup(root)
            self.assertIn('conflicting', result['status'])
            self.assertEqual(assignments(result, scan(root).playlists), {})

    def test_saved_dvd_match_survives_ejection_and_checks_title_and_timing(self):
        with test_directory() as temp:
            root = make_dvd(temp); disc = scan(root)
            with patch('bluray_map.discdb.dvd_disc_id', return_value='A'*32), patch('bluray_map.discdb.urllib.request.urlopen', return_value=self.response([self.record('A'*32)])):
                record = lookup(root)
            self.assertEqual(assignments(record, disc.playlists)[disc.playlists[0].filename]['episode'], 7)
            disc.bdmv = 'ejected/VIDEO_TS'
            rip = Path(temp)/'example.mkv'
            with patch('bluray_map.ripmatch.discdb_lookup') as query, patch.object(Path,'rglob',return_value=[rip]), patch('bluray_map.ripmatch.duration_details',return_value={'duration_seconds':1500}):
                result = match_rips(disc,temp,'ffprobe',database_record=record)
            query.assert_not_called()
            self.assertTrue(result['episodes'][0]['identity_verified'])
            self.assertEqual(result['episodes'][0]['episode'],7)
            self.assertEqual(result['episodes'][0]['matching_rips'][0]['path'],str(rip))
            record['titles'][0]['duration'] = '0:26:00'
            self.assertEqual(assignments(record,disc.playlists),{})
            record['titles'][0]['duration'] = '0:25:00'
            record['titles'].append(dict(record['titles'][0]))
            self.assertEqual(assignments(record,disc.playlists),{})

    def test_bluray_hash_unchanged(self):
        with test_directory() as temp:
            root = Path(temp)/'BDMV'; (root/'STREAM').mkdir(parents=True)
            (root/'STREAM'/'00001.m2ts').write_bytes(b'123')
            self.assertEqual(content_hash(root),hashlib.md5(struct.pack('<q',3)).hexdigest().upper())
