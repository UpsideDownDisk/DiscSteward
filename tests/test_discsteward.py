"""Checks for progress, safe disc folders and copyable rename proposals."""
import runpy
import sys
import tempfile
import os
import uuid
import shutil
from contextlib import contextmanager
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from types import SimpleNamespace
from bluray_map.automation import RipProgress, run_rip, reserve_disc_folder, disc_folder_name, run_rip_and_eject, eject_disc, makemkv_disc_name

UI = runpy.run_path(str(Path(__file__).resolve().parents[1]/'discsteward-ui.py'))


@contextmanager
def test_directory():
    # An optional workspace location supports hosts with restricted temp ACLs.
    base=os.environ.get('DISCSTEWARD_TEST_TEMP')
    if not base:
        with tempfile.TemporaryDirectory() as temp:
            yield temp
        return
    root=Path(base).resolve()
    child=root/str(uuid.uuid4())
    child.mkdir(parents=True)
    try:
        yield str(child)
    finally:
        if child.resolve().parent != root:
            raise RuntimeError('Test cleanup path escaped its dedicated root')
        shutil.rmtree(child)


class DiscStewardTests(unittest.TestCase):
    def test_watch_continues_after_unmatched_files_and_defers_review(self):
        result={'episodes':[], 'unmatched_rips':['unmatched.mkv']}
        for auto_accept in (False, True):
            with self.subTest(auto_accept=auto_accept):
                app=SimpleNamespace(last_result=result,output=Mock(),status=Mock(),
                                    watch_new=Mock(),auto_accept=Mock(),rename=Mock(),
                                    manual_review=Mock(),automation_busy=True)
                app.watch_new.get.return_value=True
                app.auto_accept.get.return_value=auto_accept
                UI['App'].done(app,'report',Path('mapping.json'),Path('mapping.txt'),True)
                self.assertFalse(app.automation_busy)
                app.manual_review.assert_not_called()
                if auto_accept:
                    app.rename.assert_called_once_with(auto_confirm=True,show_summary=False)
                else:
                    app.rename.assert_not_called()
                self.assertIn('watching for the next disc',app.status.set.call_args.args[0])

    def test_non_watched_run_still_opens_manual_review(self):
        app=SimpleNamespace(last_result={'episodes':[], 'unmatched_rips':['unmatched.mkv']},
                            output=Mock(),status=Mock(),watch_new=Mock(),auto_accept=Mock(),
                            rename=Mock(),manual_review=Mock(),automation_busy=True)
        app.watch_new.get.return_value=False
        app.auto_accept.get.return_value=False
        UI['App'].done(app,'report',Path('mapping.json'),Path('mapping.txt'),True)
        app.rename.assert_called_once_with(auto_confirm=False,show_summary=False)
        app.manual_review.assert_called_once()

    def test_watch_waits_quietly_after_disc_ejection(self):
        app=SimpleNamespace(watch_stop=Mock(),automation_busy=False,disc=Mock(),last_disc_signature='previous')
        app.watch_stop.wait.side_effect=[False,True]; app.disc.get.return_value='F:/'
        with patch.object(Path,'exists',return_value=False), patch('subprocess.run') as run:
            UI['App'].watch_worker(app)
        run.assert_not_called(); self.assertIsNone(app.last_disc_signature)

    def test_auto_rip_ejects_actual_makemkv_drive_after_success(self):
        with test_directory() as temp:
            events=[]; messages=[]
            def rip(*args): events.append('rip finished'); return {1:'G:'}
            def eject(source): events.append(source); return source
            with patch('bluray_map.automation.run_rip',side_effect=rip), \
                 patch('bluray_map.automation.eject_disc',side_effect=eject):
                self.assertTrue(run_rip_and_eject(['mkv','disc:1','all'],Path(temp)/'log',messages.append,'G:/BDMV'))
            self.assertEqual(events,['rip finished','G:'])
            self.assertIn('Disc ejected from G:',messages[-1])
            self.assertIn('Disc ejected',(Path(temp)/'log').read_text())

    def test_failed_rip_does_not_eject(self):
        with patch('bluray_map.automation.run_rip',side_effect=RuntimeError('rip failed')), \
             patch('bluray_map.automation.eject_disc') as eject:
            with self.assertRaisesRegex(RuntimeError,'rip failed'):
                run_rip_and_eject(['mkv','disc:0'],'unused',lambda msg: None,'F:/')
            eject.assert_not_called()

    def test_failed_makemkv_run_does_not_map_or_eject_after_disc_loss(self):
        with test_directory() as temp:
            log=Path(temp)/'rip.log'
            command=[sys.executable,'-c','import sys; print("drive lost"); sys.exit(2)']
            with patch('bluray_map.automation.eject_disc') as eject:
                with self.assertRaisesRegex(RuntimeError,'exit code 2'):
                    run_rip_and_eject(command,log,lambda message: None,'F:/')
            eject.assert_not_called()
            self.assertIn('drive lost',log.read_text())

    def test_eject_failure_is_reported_without_failing_mapping(self):
        with test_directory() as temp:
            messages=[]
            with patch('bluray_map.automation.run_rip',return_value={}), \
                 patch('bluray_map.automation.eject_disc',side_effect=OSError('Drive busy')) as eject:
                self.assertFalse(run_rip_and_eject(['mkv','disc:0'],Path(temp)/'log',messages.append,'F:/'))
            eject.assert_called_once_with('F:/')
            self.assertIn('Drive busy',messages[-1]); self.assertIn('Continuing with mapping',messages[-1])

    def test_eject_native_call_closes_handle_and_rejects_non_optical_drives(self):
        kernel=Mock(); kernel.GetDriveTypeW.return_value=3
        with patch('bluray_map.automation.ctypes.WinDLL',return_value=kernel):
            with self.assertRaisesRegex(ValueError,'not an optical drive'): eject_disc('C:/rips/BDMV')
            kernel.CreateFileW.assert_not_called()
            kernel.GetDriveTypeW.return_value=5; kernel.CreateFileW.return_value=123
            kernel.DeviceIoControl.return_value=True
            self.assertEqual(eject_disc('F:/BDMV'),'F:')
            self.assertEqual(kernel.CreateFileW.call_args.args[0],r'\\.\F:')
            self.assertEqual(kernel.DeviceIoControl.call_args.args[:2],(123,0x2D4808))
            kernel.CloseHandle.assert_called_once_with(123)
            kernel.CloseHandle.reset_mock(); kernel.DeviceIoControl.return_value=False
            with self.assertRaises(OSError): eject_disc('F:/BDMV')
            kernel.CloseHandle.assert_called_once_with(123)

    def test_robot_output_retains_actual_drive_letter(self):
        with test_directory() as temp:
            line='DRV:1,2,999,0,"Drive","Disc","G:"'
            drives=run_rip([sys.executable,'-c',f'print({line!r})'],Path(temp)/'log',lambda msg: None)
            self.assertEqual(drives,{1:'G:'})

    def test_mapping_after_eject_uses_saved_context_and_no_database_retry(self):
        with test_directory() as temp:
            app=SimpleNamespace(active_rips=temp,prepared_database={},prepared_local={},
                prepared_context={'series':'Nikita','season':2,'source':'Disc metadata title'},
                ffprobe=Mock(),convention=Mock(),library=Mock(),after=Mock())
            app.ffprobe.get.return_value='ffprobe'; app.convention.get.return_value='Plex TV Series'
            app.library.get.return_value=''
            result={'episodes':[],'extras':[],'unmatched_rips':[]}
            with patch.dict(UI['App'].worker.__globals__,match_rips=Mock(return_value=result),
                            disc_identity=Mock(side_effect=AssertionError('disc already ejected'))):
                UI['App'].worker(app,SimpleNamespace(bdmv='F:/BDMV'),True)
                matcher=UI['App'].worker.__globals__['match_rips']
                self.assertEqual(matcher.call_args.kwargs['database_record'],{})
            self.assertEqual(app.last_result['disc_context'],app.prepared_context)
            self.assertTrue((Path(temp)/'disc-episode-mapping.txt').is_file())

    def test_overall_progress_not_current_title_and_eta(self):
        progress = RipProgress(0)
        progress.consume('PRGV:900,250,1000',300)
        self.assertIn('25.0% overall',progress.describe(300))
        self.assertIn('about 00:15:00.000',progress.describe(300))
        self.assertIn('unavailable',progress.describe(601))
        progress.consume('PRGV:bad',602)
        self.assertEqual(progress.fraction,.25)

    def test_no_progress_is_not_claimed_complete(self):
        self.assertIn('waiting for progress',RipProgress(0).describe(300))

    def test_disc_folder_uses_metadata_and_never_reuses_folder(self):
        with test_directory() as temp:
            root=Path(temp); meta=root/'BDMV'/'META'/'DL'; meta.mkdir(parents=True)
            (meta/'bdmt_eng.xml').write_text('<disc xmlns="urn:test"><name>Nikita: Season 2 Disc 1</name></disc>')
            name=disc_folder_name(root/'BDMV')
            self.assertEqual(name,'Nikita  Season 2 Disc 1')
            first=reserve_disc_folder(root/'rips',name)
            second=reserve_disc_folder(root/'rips',name)
            self.assertEqual(second.name,name+' (2)')
            self.assertNotEqual(first,second)

    def test_dvd_folder_uses_the_disc_folder_name_when_no_blu_ray_title_exists(self):
        with test_directory() as temp:
            video_ts=Path(temp)/'Nikita Season 2 Disc 1'/'VIDEO_TS'; video_ts.mkdir(parents=True)
            self.assertEqual(disc_folder_name(video_ts),'Nikita Season 2 Disc 1')

    def test_dvd_auto_folder_prefers_makemkv_disc_name_over_volume_label(self):
        with patch('bluray_map.automation.windows_volume_label', return_value='Windows Label'):
            self.assertEqual(disc_folder_name('F:/VIDEO_TS', 'Nikita Season 2 Disc 1'), 'Nikita Season 2 Disc 1')

    def test_dvd_auto_folder_uses_windows_label_then_timestamp_type(self):
        with patch('bluray_map.automation.windows_volume_label', return_value='DVD Season Two'):
            self.assertEqual(disc_folder_name('F:/VIDEO_TS'), 'DVD Season Two')
        with patch('bluray_map.automation.windows_volume_label', return_value=''):
            self.assertEqual(disc_folder_name('F:/VIDEO_TS', timestamp=__import__('datetime').datetime(2026,9,20,14,35,10)),
                             '2026-09-20_14-35-10 - DVD')

    def test_makemkv_disc_name_uses_disc_name_field_and_rejects_generic_labels(self):
        output='DRV:2,1,1,0,"Optical drive","Nikita Season 2 Disc 1","F:"\n'
        with patch('bluray_map.automation.subprocess.run', return_value=SimpleNamespace(stdout=output)):
            self.assertEqual(makemkv_disc_name('makemkvcon', 2), 'Nikita Season 2 Disc 1')
        generic='DRV:2,1,1,0,"Optical drive","DVD Disc F","F:"\n'
        with patch('bluray_map.automation.subprocess.run', return_value=SimpleNamespace(stdout=generic)):
            self.assertEqual(makemkv_disc_name('makemkvcon', 2), '')

    def test_streamed_errors_and_periodic_updates_while_quiet(self):
        with test_directory() as temp:
            messages=[]
            script='import time,sys; print("PRGV:900,250,1000",flush=True); time.sleep(.15); print(\'MSG:5003,0,0,"Failed to save title","Failed to save title"\',flush=True); sys.exit(1)'
            log=Path(temp)/'rip.log'
            with self.assertRaises(RuntimeError):
                run_rip([sys.executable,'-u','-c',script],log,messages.append,interval=.04)
            self.assertTrue(any('Failed to save title' in message for message in messages))
            self.assertGreaterEqual(sum('25.0% overall' in message for message in messages),2)
            self.assertIn('PRGV:900,250,1000',log.read_text())
            self.assertTrue(all(message.startswith('[') for message in messages))

    def test_successful_process_completes(self):
        with test_directory() as temp:
            messages=[]
            run_rip([sys.executable,'-c','print("PRGV:100,100,100")'],Path(temp)/'rip.log',messages.append)
            self.assertIn('exit code 0',messages[-1])

    def test_copyable_names_only_for_verified_identity(self):
        base={'episode':1,'playlist':'00051.mpls','duration_seconds':1500,'clips':['00001.m2ts'],
              'source':'test','confidence':80,'matching_rips':[{'path':'title_t00.mkv'}]}
        verified={**base,'series':'Example','season':2,'title':'Pilot','source':'TheDiscDb'}
        result={'episodes':[verified,dict(base)],'unmatched_rips':[]}
        UI['add_proposed_names'](result,'Plex TV Series','')
        self.assertEqual(verified['proposed_filename'],'Example - s02e01 - Pilot.mkv')
        self.assertIsNone(result['episodes'][1]['proposed_filename'])
        text=UI['human_report'](result)
        self.assertIn('Proposed filename (copy this):\nExample - s02e01 - Pilot.mkv',text)
        self.assertIn('unavailable — verified identity',text)
        self.assertEqual(UI['plex_target']('',verified).name,verified['proposed_filename'])

    def test_legacy_settings_import_without_overwriting_new_settings(self):
        with test_directory() as temp:
            legacy=Path(temp)/'old.json'; new=Path(temp)/'new.json'
            legacy.write_text('{"rips":"original folder"}')
            with patch.dict(UI['load_settings'].__globals__,SETTINGS=new,LEGACY_SETTINGS=legacy):
                self.assertEqual(UI['load_settings']()['rips'],'original folder')
                new.write_text('{"rips":"new folder"}')
                self.assertEqual(UI['load_settings']()['rips'],'new folder')


if __name__ == '__main__':
    unittest.main()
