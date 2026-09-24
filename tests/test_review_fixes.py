"""Regressions from the safety review; no real media is ripped or renamed."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from bluray_map.models import Playlist, PlayItem, Scan
from bluray_map.analyze import classify
from bluray_map.ripmatch import match_rips
from bluray_map.local_evidence import local_assignments
from bluray_map.episode_order import apply_episode_order, verified_identity, ASSUMPTION
from bluray_map.automation import resolve_rip_drive, run_rip_and_eject, disc_fingerprint, DiscChanged
from test_discsteward import UI, test_directory
from test_manual import review_fixture


def playlist(name='00001.mpls', start=0, duration=1500):
    return Playlist(name, int(name[:5]), [PlayItem('00001','00001.m2ts',start,
                    start+int(duration*45000),duration)],duration)


class ReviewFixTests(unittest.TestCase):
    # These pre-existing plain-function checks also run without installing
    # pytest on a clean Windows machine.
    def test_legacy_mpls_fixture(self):
        from test_mpls import test_parses_playitems_and_durations
        with test_directory() as temp: test_parses_playitems_and_durations(Path(temp))

    def test_legacy_classification_examples(self):
        from test_analyze import test_episode_candidates_do_not_use_playlist_number, test_long_outliers_are_extras_not_episode_candidates
        test_episode_candidates_do_not_use_playlist_number()
        test_long_outliers_are_extras_not_episode_candidates()

    def test_same_clip_different_ranges_are_not_collapsed(self):
        first, cut, alternate = playlist(), playlist('00002.mpls',45000), playlist('00101.mpls')
        episodes, _, _, alternates = classify([first,cut,alternate])
        self.assertEqual([p.filename for p,_,_ in episodes],['00001.mpls','00002.mpls'])
        self.assertEqual(alternates,[['00001.mpls','00101.mpls']])
        with patch.object(Path,'rglob',return_value=[Path('file.mkv')]), patch('bluray_map.ripmatch.duration_details',
                    return_value={'duration_seconds':1500}):
            result=match_rips(Scan('ejected',[first,cut,alternate],[]),'.','ffprobe',database_record={})
        self.assertTrue(all(not p['matching_rips'] for p in result['episodes']))
        self.assertTrue(result['warnings'])

    def test_no_database_match_survives_ejection_and_ignores_old_local_correlation(self):
        disc=Scan('F:/BDMV',[playlist()],[])
        status={'source':'TheDiscDb','status':'no matching disc or lookup unavailable'}
        local={'00001.mpls':{'series':'Show','season':2,'episode':19,
                            'source':'Local BD-J menu/playlist number correlation'}}
        with patch.object(Path,'rglob',return_value=[]), patch('bluray_map.ripmatch.discdb_lookup') as lookup:
            result=match_rips(disc,'.','ffprobe',database_record=status,local_record=local)
        lookup.assert_not_called()
        self.assertFalse(verified_identity(result['episodes'][0]))
        self.assertEqual(result['episodes'][0]['suggested_episode'],1)
        self.assertEqual(local_assignments('missing',[(playlist(),80,[])]),{})

    def test_real_automated_mapping_worker_handles_saved_no_match_status(self):
        with test_directory() as temp:
            app=SimpleNamespace(active_rips=temp,prepared_database={'source':'TheDiscDb','status':'no match'},
                prepared_local={},prepared_context={'series':'Show','season':2},
                ffprobe=Mock(),convention=Mock(),library=Mock(),after=Mock())
            app.ffprobe.get.return_value='ffprobe'; app.convention.get.return_value='Plex TV Series'
            app.library.get.return_value=''
            with patch('bluray_map.ripmatch.discdb_lookup') as lookup:
                UI['App'].worker(app,Scan('ejected',[playlist()],[]),True)
            lookup.assert_not_called()
            self.assertEqual(app.last_result['episodes'][0]['suggested_episode'],1)
            self.assertIn(ASSUMPTION,(Path(temp)/'disc-episode-mapping.txt').read_text(encoding='utf-8'))

    def test_order_suggestions_preserve_verified_database_numbers(self):
        known={'series':'Show','season':2,'episode':8,'source':'TheDiscDb'}
        legacy={'series':'Show','season':2,'episode':19,'source':'Local BD-J menu/playlist number correlation'}
        result=apply_episode_order({'episodes':[known,legacy,{}]},7)
        self.assertTrue(verified_identity(known)); self.assertEqual(known['episode'],8)
        self.assertNotIn('suggested_episode',known)
        self.assertFalse(verified_identity(legacy)); self.assertEqual(legacy['suggested_episode'],8)
        self.assertEqual(result['episodes'][2]['suggested_episode'],9)
        with self.assertRaises(ValueError): apply_episode_order(result,0)

    def test_old_menu_correlation_cannot_trigger_automatic_rename(self):
        app=SimpleNamespace(last_result={'episodes':[{'playlist':'00001.mpls','source':'Local BD-J menu/playlist number correlation',
                            'series':'Show','season':2,'episode':1}]},library=Mock(),convention=Mock(),audio_language=Mock(),
                            save_settings=Mock(),choose_rip=Mock())
        app.library.get.return_value='library'; app.convention.get.return_value='Plex TV Series'
        app.audio_language.get.return_value='English'
        with patch('tkinter.messagebox.showinfo'),patch('shutil.move') as move:
            UI['App'].rename(app,auto_confirm=True)
            move.assert_not_called(); app.choose_rip.assert_not_called()

    def test_start_number_updates_suggestions_but_keeps_individual_edits_and_files(self):
        with review_fixture() as (window,files):
            window.file_checks[str(files[0])].invoke(); window.episode.set('19')
            window.next_programme(); window.file_checks[str(files[2])].invoke()
            window.first_episode.set('7'); self.assertTrue(window.apply_numbering())
            self.assertEqual(window.episode.get(),'8')
            window.playlist.current(1); window.select_playlist()
            self.assertEqual(window.episode.get(),'19')
            self.assertEqual(window.selected_path,str(files[0]))
            plans=window.plans()
            self.assertEqual(plans[1][3]['numbering_source'],ASSUMPTION)
            self.assertEqual(plans[1][3]['first_episode_on_disc'],7)
            window.first_episode.set(''); window.apply_numbering()
            self.assertEqual(window.drafts[2]['episode'],'2')

    def test_resolves_selected_drive_not_default_zero(self):
        response=SimpleNamespace(returncode=1,stdout='DRV:0,1,1,0,"Drive","Disc","G:"\nDRV:2,1,1,0,"Drive","Disc","F:"')
        with patch('bluray_map.automation.subprocess.run',return_value=response) as run:
            self.assertEqual(resolve_rip_drive('makemkv','f:/BDMV'),2)
            self.assertEqual(run.call_args.args[0][-1],'disc:9999')
            with self.assertRaises(ValueError): resolve_rip_drive('makemkv','H:/')
            with self.assertRaises(ValueError): resolve_rip_drive('makemkv','F:/some-folder/BDMV')

    def test_missing_or_ambiguous_drive_path_cannot_start_rip(self):
        for output in ('DRV:0,1,1,0,"Drive","Disc"',
                       'DRV:0,1,1,0,"Drive","Disc","F:"\nDRV:1,1,1,0,"Drive","Disc","F:"'):
            with patch('bluray_map.automation.subprocess.run',return_value=SimpleNamespace(stdout=output)):
                with self.assertRaises(ValueError): resolve_rip_drive('makemkv','F:/')

    def test_reported_wrong_drive_stops_mapping_and_eject(self):
        with patch('bluray_map.automation.run_rip',return_value={1:'G:'}), patch('bluray_map.automation.eject_disc') as eject:
            with self.assertRaisesRegex(RuntimeError,'different drive'):
                run_rip_and_eject(['mkv','disc:1'],'unused',Mock(),'F:/')
            eject.assert_not_called()

    def test_changed_drive_or_disc_blocks_launch(self):
        for drive, fingerprint in [(1,'original'),(0,'changed')]:
            app=SimpleNamespace(rip_settings={'executable':'makemkv','source':'F:/','drive':0,'fingerprint':'original'},
                                after=Mock(),automation_failed=Mock(),disc_interrupted=Mock(),disc_was_changed=Mock(return_value=False))
            with patch.dict(UI['App'].rip_worker.__globals__,resolve_rip_drive=Mock(return_value=drive),
                            require_disc=Mock(side_effect=DiscChanged('Disc replaced') if fingerprint != 'original' else None),
                            run_rip_and_eject=Mock()) as values:
                UI['App'].rip_worker(app)
                UI['App'].rip_worker.__globals__['run_rip_and_eject'].assert_not_called()
            callback=app.after.call_args.args
            callback[1](*callback[2:])
            if fingerprint != 'original': app.disc_interrupted.assert_called_once()
            else: self.assertIn('No rip started',app.automation_failed.call_args.args[1])

    def test_successful_rip_still_starts_mapping_after_manual_eject(self):
        app=SimpleNamespace(rip_settings={'executable':'makemkv','source':'F:/','drive':0,
                                          'fingerprint':'original'}, rip_command=['mkv','disc:0'],
                            active_rips='destination',prepare_source='F:/',
                            after=Mock(),append_rip_message=Mock(),rip_done=Mock(),
                            disc_interrupted=Mock(),automation_failed=Mock(),
                            last_disc_signature='original')
        with patch.dict(UI['App'].rip_worker.__globals__,require_disc=Mock(),
                        resolve_rip_drive=Mock(return_value=0),
                        run_rip_and_eject=Mock(return_value=False)):
            UI['App'].rip_worker(app)
        app.after.assert_called_once_with(0,app.rip_done)
        app.disc_interrupted.assert_not_called()
        app.automation_failed.assert_not_called()

    def test_command_uses_captured_settings_not_live_controls(self):
        app=SimpleNamespace(rip_settings={'executable':'captured.exe','profile':'saved.xml','source':'F:/','drive':2},
                            drive_number=Mock(),active_rips='destination',append_rip_message=Mock(),rip_worker=Mock())
        with patch('threading.Thread'):
            UI['App'].start_automated_rip(app)
        self.assertEqual(app.rip_command[0],'captured.exe')
        self.assertIn('--profile=saved.xml',app.rip_command)
        self.assertEqual(app.rip_command[-4:],['mkv','disc:2','all','destination'])

    def test_disc_fingerprint_detects_replacement_and_empty_source(self):
        with test_directory() as temp:
            root=Path(temp); directory=root/'BDMV'/'PLAYLIST'; directory.mkdir(parents=True)
            with self.assertRaises(ValueError): disc_fingerprint(root)
            file=directory/'00001.mpls'; file.write_bytes(b'disc one')
            before=disc_fingerprint(root); file.write_bytes(b'disc two')
            self.assertNotEqual(before,disc_fingerprint(root))
            self.assertEqual(disc_fingerprint(root),disc_fingerprint(root/'BDMV'))
