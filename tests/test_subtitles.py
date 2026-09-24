"""Subtitle confidence, archive safety, file identity and disc-change regressions."""
import copy
import json
from pathlib import Path
import random
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from bluray_map.subtitles import (Reference, SOURCE, apply_matches, compare_times,
    embedded_timings, load_references, match_video, parse_identity, standalone_result,
    visible_pgs, packet_bytes)
from bluray_map.episode_order import verified_identity
from bluray_map.automation import DiscChanged, require_disc, run_rip, run_rip_and_eject
from test_discsteward import UI, test_directory


def irregular(seed=1):
    rng=random.Random(seed)
    events=[]; now=0
    while now < 1200:
        now += rng.uniform(2,7)
        events.append(round(now,3))
    return events


def srt(events):
    rows=[]
    for i,t in enumerate(events):
        ms=round(t*1000); seconds,ms=divmod(ms,1000); minutes,seconds=divmod(seconds,60)
        hours,minutes=divmod(minutes,60)
        stamp=f'{hours:02}:{minutes:02}:{seconds:02},{ms:03}'
        rows.append(f'{i+1}\n{stamp} --> {stamp}\nCaption {i+1}\n')
    return '\n'.join(rows).encode()


def accepted(path='a.mkv',episode=1):
    return {'path':path,'accepted':True,'duration_seconds':1200,'reason':'Strong agreement',
            'best':{'series':'Example','season':2,'episode':episode,'coverage':.96,
                    'quarter_coverage':[.95,.97],'offset_seconds':0,'speed_factor':1,'subtitle':'Example.S02E01.srt'}}


class SubtitleTests(unittest.TestCase):
    def test_zip_and_loose_files_deduplicate_without_extracting_paths(self):
        with test_directory() as temp:
            folder=Path(temp)
            data=srt(irregular())
            (folder/'Example.S02E01.srt').write_bytes(data)
            with zipfile.ZipFile(folder/'download.zip','w') as archive:
                archive.writestr('../../Example.S02E01.srt',data)
                archive.writestr('nested/Example.S02E02.srt',srt(irregular(2)))
            (folder/'broken.zip').write_bytes(b'not a zip')
            references,warnings=load_references(folder)
            self.assertEqual(len(references),2)
            self.assertTrue(any('broken.zip' in w for w in warnings))
            self.assertFalse((folder/'nested').exists())
            self.assertEqual(sorted(r.episode for r in references),[1,2])

    def test_explicit_identity_required_and_multi_episode_rejected(self):
        self.assertEqual(parse_identity('Example_Show_S02E19.srt'),('Example Show',2,19))
        self.assertEqual(parse_identity('Example.2x19.srt'),('Example',2,19))
        self.assertEqual(parse_identity('Example.S02E01.480p.HDTV.srt'),('Example',2,1))
        for name in ('_t14.srt','Example.S02E01E02.srt','Example.S02E01-E02.srt','Example.S02E01-02.srt','S02E01.srt'):
            self.assertIsNone(parse_identity(name),name)

    def test_both_middle_quarters_with_offset_and_pal_speed(self):
        events=irregular()
        score=compare_times(events,[(t-3.2)/(25/24) for t in events],1200)
        self.assertTrue(score['strong'])
        self.assertGreater(score['coverage'],.98)
        self.assertAlmostEqual(score['offset_seconds'],3.2)
        self.assertAlmostEqual(score['speed_factor'],25/24,places=6)
        self.assertEqual(score['events'],len([t for t in events if 300<=t<900]))

    def test_repeated_opening_and_ending_cannot_identify_an_episode(self):
        events=irregular()
        reference=[t for t in events if t<300 or t>=900]+[t for t in irregular(99) if 300<=t<900]
        self.assertFalse(compare_times(events,sorted(reference),1200)['strong'])

    def test_single_quarter_and_dense_incidental_overlap_are_rejected(self):
        events=irregular()
        self.assertFalse(compare_times(events,[t for t in events if t<600],1200)['strong'])
        self.assertFalse(compare_times(events,[i*.2 for i in range(6000)],1200)['strong'])

    def test_duplicate_versions_do_not_compete_but_different_episode_does(self):
        events=irregular()
        track={'times':events,'stream_index':3,'codec':'subrip'}
        refs=[Reference('first','Example',2,1,events,'a'),Reference('version','Example',2,1,events,'b')]
        with patch('bluray_map.subtitles.embedded_timings',return_value=(1200,[track])):
            self.assertTrue(match_video(Path('wrong.s09e99.mkv'),'probe',refs)['accepted'])
            refs.append(Reference('conflicting','Example',2,2,events,'c'))
            self.assertFalse(match_video(Path('wrong.s09e99.mkv'),'probe',refs)['accepted'])

    def test_embedded_window_and_pgs_clear_packets(self):
        # Minimal PGS presentation composition, with and without an object.
        shown=b'\x16\x00\x0b'+b'\0'*10+b'\x01'
        clear=b'\x16\x00\x0b'+b'\0'*11
        self.assertTrue(visible_pgs(shown)); self.assertFalse(visible_pgs(clear))
        self.assertEqual(packet_bytes('00000000: 1600 0b00 0000 0000 0000 0000 0001  ..............'),shown)
        info={'format':{'duration':'1200','start_time':'0'},'streams':[
            {'codec_type':'subtitle','codec_name':'subrip','index':3,'tags':{'language':'eng'}}]}
        packets={'packets':[{'pts_time':str(t)} for t in (299,300,600,899,900,1000)]}
        with patch('bluray_map.subtitles.probe_json',side_effect=[info,packets]) as probe:
            duration,tracks=embedded_timings(Path('bad-name.mkv'),'probe')
        self.assertEqual(tracks[0]['times'],[300,600,899])
        self.assertIn('300.000%900.000',probe.call_args.args[0])

    def test_preferred_language_selects_non_english_subtitles(self):
        streams=[{'codec_type':'subtitle','codec_name':'subrip','index':2,'tags':{'language':'eng'}},
                 {'codec_type':'subtitle','codec_name':'subrip','index':3,'tags':{'language':'fre'}},
                 {'codec_type':'subtitle','codec_name':'subrip','index':4,'tags':{'language':'fr'},
                  'disposition':{'forced':1}}]
        info={'format':{'duration':'1200','start_time':'0'},'streams':streams}
        packets={'packets':[{'pts_time':'350'}]}
        with patch('bluray_map.subtitles.probe_json',side_effect=[info,packets]) as probe:
            _,tracks=embedded_timings(Path('video.mkv'),'probe','fra')
        self.assertEqual([track['stream_index'] for track in tracks],[3])
        self.assertEqual(probe.call_count,2)
        with patch('bluray_map.subtitles.probe_json',return_value=info):
            with self.assertRaisesRegex(ValueError,'German subtitle track'):
                embedded_timings(Path('video.mkv'),'probe','deu')
        with patch('bluray_map.subtitles.probe_json',side_effect=[info,packets,packets]) as probe:
            _,tracks=embedded_timings(Path('video.mkv'),'probe','')
        self.assertEqual([track['stream_index'] for track in tracks],[2,3])

    def test_french_embedded_track_can_identify_an_episode(self):
        events=irregular()
        info={'format':{'duration':'1200','start_time':'0'},'streams':[
            {'codec_type':'subtitle','codec_name':'subrip','index':3,'tags':{'language':'fre'}}]}
        packets={'packets':[{'pts_time':str(event)} for event in events]}
        reference=Reference('Example.S02E01.fr.srt','Example',2,1,events,'digest')
        with patch('bluray_map.subtitles.probe_json',side_effect=[info,packets]):
            result=match_video(Path('unnamed.mkv'),'probe',[reference],'fra')
        self.assertTrue(result['accepted'])
        self.assertEqual(result['best']['episode'],1)

    def test_missing_subtitles_and_short_tracks_stay_unresolved(self):
        with patch('bluray_map.subtitles.probe_json',return_value={'format':{'duration':'1200'},'streams':[]}):
            result=match_video(Path('file.mkv'),'probe',[])
        self.assertFalse(result['accepted']); self.assertIn('English',result['reason'])
        self.assertFalse(compare_times([310,450,700],[310,450,700],1200)['strong'])

    def test_database_precedence_and_no_unverified_duplicate_inheritance(self):
        verified={'source':'TheDiscDb','series':'Example','season':2,'episode':9,'playlist':'known',
                  'matching_rips':[{'path':'known.mkv'}]}
        unverified={'source':'unknown','playlist':'unknown','matching_rips':[{'path':'a.mkv'},{'path':'b.mkv'}]}
        result={'episodes':[verified,unverified],'extras':[],'unmatched_rips':[]}
        before=copy.deepcopy(verified)
        apply_matches(result,{'files':[accepted('known.mkv'),accepted('a.mkv')]})
        self.assertEqual(verified,before)
        self.assertEqual(unverified['matching_rips'],[{'path':'b.mkv'}])
        matched=result['episodes'][-1]
        self.assertTrue(verified_identity(matched)); self.assertEqual(matched['matching_rips'],[{'path':'a.mkv'}])

    def test_standalone_proposes_names_from_subtitles_not_mkv_names(self):
        report={'files':[accepted('wrong episode.mkv')], 'note':'local reference'}
        result=standalone_result(report)
        UI['add_proposed_names'](result,'Plex TV Series','')
        self.assertEqual(result['episodes'][0]['proposed_filename'],'Example - s02e01.mkv')
        text=UI['human_report'](result)
        self.assertIn('middle two quarters',text)
        self.assertIn('Example.S02E01.srt',text)
        self.assertIn('Subtitle timing coverage',text)
        self.assertFalse(verified_identity({'source':SOURCE,'series':'Example','season':2,'episode':1}))

    def test_main_page_remembers_subtitles_and_opens_existing_file_window(self):
        with test_directory() as temp, patch.dict(UI['App'].__init__.__globals__,load_settings=lambda:{},SETTINGS=Path(temp)/'settings.json'):
            app=UI['App'](); app.withdraw()
            try:
                app.subtitles.set(temp); app.ffprobe.set(sys.executable)
                app.existing_files.set(temp); app.save_settings()
                saved=json.loads((Path(temp)/'settings.json').read_text())
                self.assertEqual(saved['subtitles'],temp)
                self.assertEqual(saved['existing_files'],temp)
                app.match_existing(); app.update_idletasks()
                windows=[w for w in app.winfo_children() if w.winfo_class()=='Toplevel']
                self.assertEqual(len(windows),1)
                self.assertTrue(app.automation_busy)
                # Exercise the actual Run button, worker callback and report
                # writer without probing or moving any user media.
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                button=next(w for w in descendants(windows[0]) if w.winfo_class()=='TButton' and w.cget('text')=='Run matching')
                report={'files':[accepted(str(Path(temp)/'misnamed.mkv'))]}
                def poll():
                    if app.last_result is not None: app.quit()
                    else: app.after(20,poll)
                with patch.dict(UI['App'].match_existing.__globals__,match_folder=Mock(return_value=report)):
                    app.after(0,button.invoke); app.after(20,poll); app.after(5000,app.quit)
                    app.mainloop()
                self.assertTrue((Path(temp)/'subtitle-episode-mapping.txt').is_file())
                saved_result=json.loads((Path(temp)/'subtitle-episode-mapping.json').read_text())
                self.assertEqual(saved_result['episodes'][0]['episode'],1)
                windows[0].tk.call(windows[0].protocol('WM_DELETE_WINDOW'))
                self.assertFalse(app.automation_busy)
            finally: app.destroy()

    def test_main_mapping_runs_subtitles_after_database_and_saves_provenance(self):
        with test_directory() as temp:
            path=str(Path(temp)/'a.mkv')
            programme={'episode':1,'playlist':'00001.mpls','source':'unknown','clips':[],
                       'duration_seconds':1200,'confidence':80,'matching_rips':[{'path':path}]}
            result={'episodes':[programme],'extras':[],'unmatched_rips':[],'rip_inventory':[{'path':path}]}
            app=SimpleNamespace(active_rips=temp,prepared_database={},prepared_local={},prepared_context=None,
                ffprobe=Mock(),convention=Mock(),library=Mock(),after=Mock(),subtitles=Mock(),status=Mock(),
                audio_language=Mock())
            app.ffprobe.get.return_value='probe'; app.convention.get.return_value='Plex TV Series'
            app.library.get.return_value=''; app.subtitles.get.return_value=temp
            app.audio_language.get.return_value='French'
            with patch.dict(UI['App'].worker.__globals__,match_rips=Mock(return_value=result),
                            match_folder=Mock(return_value={'files':[accepted(path)]}),audio_tracks=Mock(return_value=[])):
                UI['App'].worker(app,SimpleNamespace(bdmv='ejected'),True)
                self.assertEqual(UI['App'].worker.__globals__['match_folder'].call_args.kwargs['preferred_language'],'fra')
            saved=json.loads((Path(temp)/'disc-episode-mapping.json').read_text())
            self.assertEqual(saved['episodes'][0]['source'],SOURCE)
            self.assertTrue(saved['episodes'][0]['subtitle_evidence']['accepted'])
            self.assertIn('Example.S02E01.srt',(Path(temp)/'disc-episode-mapping.txt').read_text(encoding='utf-8'))


class DiscChangeTests(unittest.TestCase):
    def test_require_disc_detects_empty_and_replacement(self):
        for value in (ValueError('empty'),'replacement'):
            with patch('bluray_map.automation.disc_fingerprint',side_effect=value if isinstance(value,Exception) else None,
                       return_value=value):
                with self.assertRaises(DiscChanged): require_disc('F:/','original')

    def test_drive_is_checked_once_before_ripping(self):
        with test_directory() as temp:
            notify=Mock(); check=Mock(side_effect=[None,DiscChanged('temporary read error')])
            log=Path(temp)/'rip.log'
            run_rip([sys.executable,'-c','print("copied")'],log,notify,check_disc=check)
            check.assert_called_once()
            self.assertIn('MakeMKV finished (exit code 0)',log.read_text())

    def test_missing_or_replaced_disc_after_success_skips_eject_and_maps(self):
        for reason in ('Disc removed', 'Disc replaced'):
            with self.subTest(reason=reason), test_directory() as temp:
                messages=[]; log=Path(temp)/'rip.log'
                with patch('bluray_map.automation.run_rip',return_value={}), \
                     patch('bluray_map.automation.require_disc',side_effect=DiscChanged(reason)), \
                     patch('bluray_map.automation.eject_disc') as eject:
                    self.assertFalse(run_rip_and_eject(['mkv','disc:0'],log,messages.append,'F:/','original'))
                eject.assert_not_called()
                self.assertIn('Continuing with mapping',messages[-1])
                self.assertIn(reason,log.read_text())

    def test_interruption_clears_prepared_evidence_and_starts_watch(self):
        app=SimpleNamespace(automation_busy=True,prepared_scan='old',prepared_database='old',prepared_local='old',
             prepared_context='old',last_result='old',last_disc_signature='old',watch_new=Mock(),disc=Mock(),
             append_rip_message=Mock(),watch_worker=Mock())
        app.watch_new.get.return_value=False; app.disc.get.return_value='F:/BDMV'
        with patch('threading.Thread') as thread:
            UI['App'].disc_interrupted(app,'Removed')
            thread.return_value.start.assert_called_once()
        self.assertFalse(app.automation_busy); self.assertIsNone(app.prepared_scan)
        self.assertIsNone(app.last_result); self.assertIsNone(app.last_disc_signature)
        self.assertEqual(app.watch_source,'F:/BDMV')

    def test_queued_watch_callbacks_do_not_start_duplicate_rips(self):
        app=SimpleNamespace(watch_stop=threading.Event(),automation_busy=False,
                            last_disc_signature=None,disc=Mock(),automate=Mock())
        app.disc.get.return_value='F:/'
        UI['App'].start_watched_disc(app,'F:/','new',app.watch_stop)
        UI['App'].start_watched_disc(app,'F:/','new',app.watch_stop)
        app.automate.assert_called_once()


if __name__ == '__main__': unittest.main()
