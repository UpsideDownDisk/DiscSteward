"""DVD VIDEO_TS detection, bounded IFO parsing and duration matching."""
import unittest
from pathlib import Path
from unittest.mock import patch
from bluray_map.discovery import scan, locate_bdmv
from bluray_map.dvdifo import parse_vts_ifo, dvd_time_seconds
from bluray_map.ripmatch import match_rips
from test_discsteward import test_directory


def dvd_time(hours, minutes, seconds, frames=0):
    def bcd(value): return (value // 10 << 4) | value % 10
    return bytes([bcd(hours), bcd(minutes), bcd(seconds), 0xC0 | bcd(frames)])


def pgc(data, start, duration, cells, vob_id):
    data[start + 2] = 1; data[start + 3] = len(cells)
    data[start + 4:start + 8] = dvd_time(0, duration // 60, duration % 60)
    data[start + 0xE6:start + 0xE8] = (0xEC).to_bytes(2, 'big')
    data[start + 0xEC] = 1
    data[start + 0xE8:start + 0xEA] = (0xF0).to_bytes(2, 'big')
    data[start + 0xEA:start + 0xEC] = (0x120).to_bytes(2, 'big')
    for number, length in enumerate(cells):
        playback = start + 0xF0 + number * 24
        data[playback + 4:playback + 8] = dvd_time(0, length // 60, length % 60)
        data[playback+8:playback+12] = (1000*vob_id+number*100).to_bytes(4,'big')
        data[playback+20:playback+24] = (1000*vob_id+number*100+99).to_bytes(4,'big')
        position = start + 0x120 + number * 4
        data[position:position + 2] = vob_id.to_bytes(2, 'big'); data[position + 3] = number + 1


def make_dvd(root):
    video_ts = Path(root) / 'VIDEO_TS'; video_ts.mkdir()
    vmg = bytearray(4096); vmg[:12] = b'DVDVIDEO-VMG'
    vmg[0xC4:0xC8] = (1).to_bytes(4,'big')
    vmg[2048:2050] = (2).to_bytes(2,'big')
    vmg[2052:2056] = (31).to_bytes(4,'big')
    for n in range(2):
        entry=2056+n*12
        vmg[entry+1]=1; vmg[entry+2:entry+4]=(1).to_bytes(2,'big')
        vmg[entry+6]=1; vmg[entry+7]=n+1
    (video_ts / 'VIDEO_TS.IFO').write_bytes(vmg)
    data = bytearray(6144); data[:12] = b'DVDVIDEO-VTS'
    data[0xC8:0xCC] = (1).to_bytes(4,'big')
    data[0xCC:0xD0] = (2).to_bytes(4, 'big')
    data[2048:2050] = (2).to_bytes(2,'big')
    data[2052:2056] = (23).to_bytes(4,'big')
    for n in range(2):
        data[2056+n*4:2060+n*4]=(16+n*4).to_bytes(4,'big')
        data[2064+n*4:2068+n*4]=(n+1).to_bytes(2,'big')+(1).to_bytes(2,'big')
    table = 4096; data[table:table + 2] = (2).to_bytes(2, 'big')
    data[table+4:table+8] = (0x33f).to_bytes(4,'big')
    starts = [table + 0x40, table + 0x1A0]
    for number, start in enumerate(starts):
        entry = table + 8 + number * 8
        data[entry + 4:entry + 8] = (start - table).to_bytes(4, 'big')
    pgc(data, starts[0], 1500, [750, 750], 1)
    pgc(data, starts[1], 1560, [780, 780], 2)
    (video_ts / 'VTS_01_0.IFO').write_bytes(data)
    for number in (1, 2): (video_ts / f'VTS_01_{number}.VOB').write_bytes(b'vob')
    return video_ts


class DVDTests(unittest.TestCase):
    def setUp(self):
        lookup = patch('bluray_map.ripmatch.discdb_lookup', return_value=None)
        lookup.start()
        self.addCleanup(lookup.stop)

    def test_detects_dvd_when_bdmv_is_absent_and_parses_title_chains(self):
        with test_directory() as temp:
            video_ts = make_dvd(temp)
            result = scan(temp)
            self.assertEqual(result.disc_type, 'dvd')
            self.assertEqual(result.bdmv, str(video_ts))
            self.assertEqual([p.filename for p in result.playlists], ['DVD_TITLE_01_VTS_01_TITLE_01.IFO', 'DVD_TITLE_02_VTS_01_TITLE_02.IFO'])
            self.assertEqual(result.warnings, [])
            self.assertEqual([p.duration_seconds for p in result.playlists], [1500, 1560])
            self.assertEqual(result.playlists[0].clip_chain, ['VTS_01_VOB_01_CELL_01', 'VTS_01_VOB_01_CELL_02'])
            with self.assertRaises(ValueError): locate_bdmv(temp)

    def test_rejects_invalid_dvd_bcd_timing(self):
        with self.assertRaises(ValueError): dvd_time_seconds(bytes([0, 0x6A, 0, 0xC0]))

    def test_unknown_folder_explains_both_supported_disc_types(self):
        with test_directory() as temp:
            with self.assertRaisesRegex(ValueError, 'Blu-ray BDMV or DVD VIDEO_TS'):
                scan(temp)

    def test_matches_dvd_titles_with_frame_rounding_but_rejects_ambiguous_titles(self):
        with test_directory() as temp:
            disc = scan(make_dvd(temp))
            first, second = Path(temp) / 'title_t00.mkv', Path(temp) / 'title_t01.mkv'
            timing = [{'duration_seconds': 1500.02, 'duration_source': 'video_track_tag'},
                      {'duration_seconds': 1560.03, 'duration_source': 'video_track_tag'}]
            with patch.object(Path, 'rglob', return_value=[first, second]), \
                 patch('bluray_map.ripmatch.duration_details', side_effect=timing):
                result = match_rips(disc, temp, 'ffprobe')
            self.assertEqual(result['disc_type'], 'dvd')
            self.assertEqual(result['programme_kind'], 'DVD title')
            self.assertEqual([len(item['matching_rips']) for item in result['episodes']], [1, 1])
            self.assertTrue(all('DVD IFO' in item['source'] for item in result['episodes']))

    def test_pal_and_ntsc_bcd_frames_and_invalid_frames(self):
        self.assertEqual(dvd_time_seconds(bytes([0,0,0,0x60])), .8)
        self.assertAlmostEqual(dvd_time_seconds(bytes([0,0,0,0xE9])), 29*1001/30000)
        for frames in (0x65, 0xDA, 0xF0, 0x80):
            with self.assertRaises(ValueError): dvd_time_seconds(bytes([0,0,0,frames]))

    def test_global_title_order_can_differ_from_pgc_order(self):
        with test_directory() as temp:
            root=make_dvd(temp); file=root/'VIDEO_TS.IFO'; data=bytearray(file.read_bytes())
            data[2063],data[2075]=2,1; file.write_bytes(data)
            self.assertEqual([p.duration_seconds for p in scan(root).playlists],[1560,1500])

    def test_title_can_start_at_later_program_and_unreferenced_pgc_is_not_title(self):
        with test_directory() as temp:
            root=make_dvd(temp); file=root/'VTS_01_0.IFO'; data=bytearray(file.read_bytes())
            start=4096+0x40; data[start+2]=2; data[start+0xec:start+0xee]=bytes([1,2])
            # Both title references point into PGC 1; PGC 2 must not be emitted.
            data[2068:2072]=bytes([0,1,0,2]); file.write_bytes(data)
            titles=scan(root).playlists
            self.assertEqual([p.duration_seconds for p in titles],[1500,750])
            self.assertEqual([p.streams['pgc'] for p in titles],[1,1])
            self.assertEqual(len(titles[1].items),1)

    def test_angles_dynamic_navigation_and_bad_offsets_are_warned_not_guessed(self):
        changes=[(4096+0x40+0xf0,0x50), (4096+0x40+0xa3,1), (2067,0), (4108,0xff)]
        for offset,value in changes:
            with self.subTest(offset=offset), test_directory() as temp:
                root=make_dvd(temp); file=root/'VTS_01_0.IFO'; data=bytearray(file.read_bytes())
                data[offset]=value; file.write_bytes(data)
                result=scan(root)
                self.assertTrue(result.warnings)
                self.assertLess(len(result.playlists),2)

    def test_missing_vmg_title_table_never_falls_back_to_all_pgcs(self):
        with test_directory() as temp:
            root=make_dvd(temp); (root/'VIDEO_TS.IFO').write_bytes(b'DVDVIDEO-VMG')
            result=scan(root)
            self.assertEqual(result.playlists,[])
            self.assertTrue(result.warnings)

    def test_multi_pgc_chapters_and_vmg_multi_angle_are_not_guessed(self):
        with test_directory() as temp:
            root=make_dvd(temp); file=root/'VTS_01_0.IFO'; data=bytearray(file.read_bytes())
            # Title 1 has two chapters in different PGCs; title 2 remains simple.
            data[2052:2056]=(27).to_bytes(4,'big'); data[2060:2064]=(24).to_bytes(4,'big')
            data[2064:2076]=bytes([0,1,0,1, 0,2,0,1, 0,2,0,1]); file.write_bytes(data)
            result=scan(root)
            self.assertEqual(len(result.playlists),1)
            self.assertTrue(any('multi-PGC' in warning for warning in result.warnings))
            self.assertFalse(result.title_scan_complete)
            # A missing title could collide with the surviving title's timing.
            rip=Path(temp)/'file.mkv'
            with patch.object(Path,'rglob',return_value=[rip]), patch('bluray_map.ripmatch.duration_details',
                    return_value={'duration_seconds':1560,'duration_source':'video_track_tag'}):
                report=match_rips(result,temp,'ffprobe')
            self.assertEqual(report['unmatched_rips'],[str(rip)])
        with test_directory() as temp:
            root=make_dvd(temp); file=root/'VIDEO_TS.IFO'; data=bytearray(file.read_bytes())
            data[2057]=2; file.write_bytes(data)
            self.assertTrue(any('multi-angle' in warning for warning in scan(root).warnings))

    def test_truncated_title_table_is_reported_without_crashing(self):
        with test_directory() as temp:
            root=make_dvd(temp); file=root/'VIDEO_TS.IFO'; data=bytearray(file.read_bytes())
            data[2052:2056]=(15).to_bytes(4,'big'); file.write_bytes(data)
            result=scan(root)
            self.assertEqual(result.playlists,[])
            self.assertFalse(result.title_scan_complete)

    def test_distinct_same_duration_dvd_titles_remain_ambiguous(self):
        with test_directory() as temp:
            disc=scan(make_dvd(temp)); disc.playlists[1].duration_seconds=1500
            file=Path(temp)/'ambiguous.mkv'
            with patch.object(Path,'rglob',return_value=[file]), patch('bluray_map.ripmatch.duration_details',
                    return_value={'duration_seconds':1500,'duration_source':'video_track_tag'}):
                result=match_rips(disc,temp,'ffprobe')
            self.assertEqual(result['unmatched_rips'],[str(file)])
            self.assertTrue(result['warnings'])

    def test_dvd_does_not_accept_two_second_mismatch(self):
        with test_directory() as temp:
            disc=scan(make_dvd(temp)); file=Path(temp)/'file.mkv'
            with patch.object(Path,'rglob',return_value=[file]), patch('bluray_map.ripmatch.duration_details',
                    return_value={'duration_seconds':1500.8,'duration_source':'video_track_tag'}):
                result=match_rips(disc,temp,'ffprobe')
            self.assertEqual(result['unmatched_rips'],[str(file)])
