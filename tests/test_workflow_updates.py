"""Real temporary files exercise archival, rename modes and updated UI reports."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import zipfile

from bluray_map.subtitles import load_references, match_folder, standalone_result
from bluray_map.renames import record_rename, completed_renames
from bluray_map.manual_dialog import ManualMatchWindow
from test_discsteward import UI, test_directory
from test_subtitles import irregular, srt, accepted


def widgets(parent):
    for widget in parent.winfo_children():
        yield widget
        yield from widgets(widget)


class WorkflowUpdates(unittest.TestCase):
    def test_series_filter_requires_name_in_srt_even_inside_zip(self):
        with test_directory() as temp:
            root=Path(temp)
            (root/'S02E01.srt').write_bytes(srt(irregular()))
            (root/'Other.Show.S02E01.srt').write_bytes(srt(irregular()))
            with zipfile.ZipFile(root/'Example.Show.S02E01.zip','w') as archive:
                archive.writestr('S02E01.srt',srt(irregular()))
                archive.writestr('Example.Show.S02E02.srt',srt(irregular(2)))
            refs,warnings=load_references(root,'example show')
            self.assertEqual([(r.series,r.episode) for r in refs],[('Example Show',2)])
            self.assertTrue(any('Other.Show' in message for message in warnings))

    def test_fresh_first_then_used_fallback_and_no_failed_match_archival(self):
        with test_directory() as temp:
            root=Path(temp); videos=root/'videos'; videos.mkdir(); subs=root/'subtitles'; subs.mkdir()
            video=videos/'wrong name.mkv'; video.write_bytes(b'test video')
            chosen=subs/'Example.S02E01.srt'; chosen.write_bytes(srt(irregular()))
            unused=subs/'Example.S02E02.srt'; unused.write_bytes(srt(irregular(4)))
            used=subs/'previously used'; used.mkdir()
            (used/'Example.S02E99.srt').write_bytes(srt(irregular()))
            tracks=(1200,[{'times':irregular(),'stream_index':3,'codec':'subrip'}])
            with patch('bluray_map.subtitles.embedded_timings',return_value=tracks), \
                 patch('bluray_map.subtitles.load_references',wraps=load_references) as loader:
                report=match_folder(videos,subs,'probe',expected_series='Example')
                self.assertTrue(report['files'][0]['accepted'])
                self.assertEqual(report['files'][0]['best']['episode'],1)
                self.assertEqual([c.kwargs['scope'] for c in loader.call_args_list],['active'])
            self.assertFalse(chosen.exists()); self.assertTrue((used/chosen.name).is_file())
            self.assertTrue(unused.is_file())
            self.assertIn('previously used',report['files'][0]['best']['subtitle_current_location'])
            # A used reference with another episode label now creates a real
            # conflict during fallback: neither a rename nor a fresh-file move.
            with patch('bluray_map.subtitles.embedded_timings',return_value=tracks):
                again=match_folder(videos,subs,'probe',expected_series='Example')
            self.assertFalse(again['files'][0]['accepted']); self.assertTrue(unused.exists())
            self.assertIn('fallback',again['files'][0]['search_stage'])

    def test_zip_moves_intact_and_is_reusable_next_time(self):
        with test_directory() as temp:
            root=Path(temp); videos=root/'videos'; videos.mkdir(); subs=root/'subtitles'; subs.mkdir()
            (videos/'wrong.mkv').write_bytes(b'video')
            source=subs/'download.zip'
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('nested/Example.S02E01.srt',srt(irregular()))
                archive.writestr('nested/Example.S02E02.srt',srt(irregular(8)))
            original=source.read_bytes()
            tracks=(1200,[{'times':irregular(),'stream_index':3,'codec':'subrip'}])
            with patch('bluray_map.subtitles.embedded_timings',return_value=tracks):
                first=match_folder(videos,subs,'probe',expected_series='Example')
                second=match_folder(videos,subs,'probe',expected_series='Example')
            target=subs/'previously used'/'download.zip'
            self.assertEqual(target.read_bytes(),original); self.assertFalse(source.exists())
            self.assertTrue(first['files'][0]['accepted']); self.assertTrue(second['files'][0]['accepted'])
            self.assertEqual(len(first['archived_subtitles']),1)
            self.assertEqual(second['archived_subtitles'],[])

    def test_archive_collision_keeps_both_files(self):
        with test_directory() as temp:
            root=Path(temp); videos=root/'videos'; videos.mkdir(); subs=root/'subtitles'; subs.mkdir()
            (videos/'video.mkv').write_bytes(b'video')
            filename='Example.S02E01.srt'; (subs/filename).write_bytes(srt(irregular()))
            used=subs/'previously used'; used.mkdir(); (used/filename).write_bytes(b'keep me')
            tracks=(1200,[{'times':irregular(),'stream_index':3,'codec':'subrip'}])
            with patch('bluray_map.subtitles.embedded_timings',return_value=tracks):
                report=match_folder(videos,subs,'probe',expected_series='Example')
            self.assertEqual((used/filename).read_bytes(),b'keep me')
            self.assertTrue((used/'Example.S02E01 (2).srt').is_file())
            self.assertTrue(report['files'][0]['accepted'])

    def test_rename_collision_keeps_video_and_does_not_record_success(self):
        with test_directory() as temp:
            root=Path(temp); source=root/'source.mkv'; target=root/'target.mkv'
            source.write_bytes(b'source'); target.write_bytes(b'existing')
            result=standalone_result({'files':[accepted(str(source))]})
            with self.assertRaises(FileExistsError):
                record_rename(result,result['episodes'][0],source,target,'rename in current folder',root/'audit.jsonl')
            self.assertEqual(source.read_bytes(),b'source'); self.assertEqual(target.read_bytes(),b'existing')
            self.assertEqual(completed_renames(result),[])

    def test_both_ui_rename_modes_update_report_and_manual_popup(self):
        with test_directory() as temp, patch.dict(UI['App'].__init__.__globals__,load_settings=lambda:{},SETTINGS=Path(temp)/'settings.json'):
            root=Path(temp); source=root/'wrong name.mkv'; source.write_bytes(b'test video')
            app=UI['App'](); app.withdraw()
            try:
                app.library.set(str(root/'library'))
                app.last_result=standalone_result({'files':[accepted(str(source))]})
                app.last_report_folder=root; app.last_report_stem='subtitle-episode-mapping'
                app.choose_rip=lambda candidates,preferred: (candidates[0],'test selection')
                with patch('tkinter.messagebox.askyesno',return_value=False):
                    app.rename(in_place=True)
                self.assertTrue(source.exists())
                with patch('tkinter.messagebox.askyesno',return_value=True), patch('tkinter.messagebox.showinfo') as popup:
                    app.rename(in_place=True)
                    local=root/'Example - s02e01.mkv'
                    self.assertTrue(local.exists()); self.assertFalse(source.exists())
                    self.assertFalse((root/'library').exists())
                    self.assertIn(str(source),popup.call_args.args[1])
                    app.rename(in_place=False)
                    target=root/'library'/'Example'/'Season 02'/'Example - s02e01.mkv'
                    self.assertTrue(target.exists()); self.assertFalse(local.exists())
                    count=len(app.last_result['rename_history'])
                    app.rename(in_place=False)  # Repeat must not move an alternate.
                    self.assertEqual(len(app.last_result['rename_history']),count)
                    saved=json.loads((root/'subtitle-episode-mapping.json').read_text())
                    self.assertEqual(saved['episodes'][0]['matching_rips'][0]['path'],str(target))
                    self.assertEqual(len(saved['rename_history']),2)
                    self.assertIn(str(target),(root/'subtitle-episode-mapping.txt').read_text(encoding='utf-8'))
                    window=ManualMatchWindow(app,saved,root,str(root/'library'),'probe')
                    window.withdraw(); window.update_idletasks()
                    window.show_completed_renames()
                    self.assertIn('Files already renamed',popup.call_args.args[0])
                    self.assertIn(str(source),popup.call_args.args[1]); self.assertIn(str(target),popup.call_args.args[1])
                    self.assertNotIn(target,window.files)
                    self.assertIn('Already completed',window.evidence.get('1.0','end'))
                    window.close()
            finally: app.destroy()

    def test_main_layout_order_and_existing_window_actions(self):
        with test_directory() as temp, patch.dict(UI['App'].__init__.__globals__,load_settings=lambda:{},SETTINGS=Path(temp)/'settings.json'):
            app=UI['App'](); app.withdraw()
            try:
                labels=[w for w in widgets(app) if w.winfo_class()=='TLabel']
                self.assertFalse(any('MakeMKV drive' in w.cget('text') for w in labels))
                selected=[w.cget('text') for w in labels if w.grid_info()]
                self.assertEqual(selected[:5],['Disc source (Blu-ray BDMV or DVD VIDEO_TS):',
                    'Local subtitle folder (SRT or ZIP, optional):','Plex TV library folder:',
                    'Naming convention:','Preferred language:'])
                app.subtitles.set(temp); app.ffprobe.set(sys.executable)
                app.match_existing()
                window=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                buttons={w.cget('text'):w for w in widgets(window) if w.winfo_class()=='TButton'}
                self.assertIn('Rename and move…',buttons); self.assertIn('Rename in current folder…',buttons)
                self.assertTrue(buttons['Rename and move…'].instate(['disabled']))
                window.tk.call(window.protocol('WM_DELETE_WINDOW'))
            finally: app.destroy()


if __name__=='__main__': unittest.main()
