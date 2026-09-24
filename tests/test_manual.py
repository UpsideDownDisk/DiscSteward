"""Manual decisions never silently invent identity or overwrite video files."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import errno
import time
from contextlib import contextmanager
import tkinter as tk
from bluray_map.manual import manual_target, move_manual
from bluray_map.manual_dialog import ManualMatchWindow
from bluray_map.local_evidence import disc_identity
from test_discsteward import test_directory


@contextmanager
def review_fixture():
    """Two programmes, alternate audio versions, and one unrelated file."""
    with test_directory() as temp:
        folder=Path(temp)/'Nikita Season 2 Disc 1'; folder.mkdir()
        files=[folder/name for name in ['t00.mkv','t14.mkv','t05.mkv','extra.mkv']]
        for file in files: file.write_bytes(b'fixture video')
        def match(path, language):
            return {'path':str(path),'duration_source':'video_track_tag','difference_seconds':.000014,
                    'audio_tracks':[{'language':'eng','default':True},{'language':language,'default':False}]}
        result={'episodes':[
            {'playlist':'00051.mpls','episode':1,'duration_seconds':2550,'clips':['00001.m2ts'],
             'matching_rips':[match(files[0],'jpn'),match(files[1],'eng')]},
            {'playlist':'00052.mpls','episode':2,'duration_seconds':2600,'clips':['00002.m2ts'],
             'matching_rips':[match(files[2],'eng')]}],
            'rip_inventory':[{'path':str(p),'duration_seconds':2550} for p in files]}
        app=tk.Tk(); app.withdraw()
        window=ManualMatchWindow(app,result,folder,str(Path(temp)/'library'),'ffprobe')
        window.withdraw()
        try: yield window,files
        finally:
            window.close(); app.destroy()


class ManualTests(unittest.TestCase):
    def test_disc_title_context_is_not_an_episode_assignment(self):
        with test_directory() as temp:
            meta=Path(temp)/'BDMV'/'META'/'DL'; meta.mkdir(parents=True)
            (meta/'bdmt_eng.xml').write_text('<disc><name>Nikita BD Season 2 Disc 1</name></disc>')
            self.assertEqual(disc_identity(meta.parents[1]),('Nikita',2))

    def test_plex_and_custom_names(self):
        self.assertEqual(manual_target('library','Show','2','3','Title').name,'Show - s02e03 - Title.mkv')
        self.assertEqual(manual_target('library','','','','','My extra').name,'My extra.mkv')
        self.assertEqual(manual_target('library','Show','0','1').parent.name,'Season 00')
        for name in ['../escape.mkv','CON.mkv','folder/file.mkv']:
            with self.assertRaises(ValueError): manual_target('library','','','','',name)
        with self.assertRaises(ValueError): manual_target('library','Show','','')

    def test_move_and_audit_preserve_manual_provenance(self):
        with test_directory() as temp:
            root=Path(temp); source=root/'original.mkv'; source.write_bytes(b'test video')
            target=manual_target(str(root/'library'),'Show','1','2')
            audit=root/'decisions.jsonl'
            move_manual(source,target,audit,{'playlist':'00051.mpls'})
            self.assertFalse(source.exists()); self.assertEqual(target.read_bytes(),b'test video')
            entries=[json.loads(line) for line in audit.read_text().splitlines()]
            self.assertEqual([e['status'] for e in entries],['planned','completed'])
            self.assertIn('not automatically verified',entries[-1]['source'])
            self.assertEqual(entries[-1]['playlist'],'00051.mpls')

    def test_existing_destination_is_never_replaced(self):
        with test_directory() as temp:
            root=Path(temp); source=root/'source.mkv'; target=root/'target.mkv'
            source.write_bytes(b'source'); target.write_bytes(b'existing')
            with self.assertRaises(FileExistsError): move_manual(source,target,root/'log',{})
            self.assertEqual(source.read_bytes(),b'source'); self.assertEqual(target.read_bytes(),b'existing')

    def test_cross_volume_copy_then_remove_source(self):
        with test_directory() as temp:
            root=Path(temp); source=root/'source.mkv'; source.write_bytes(b'cross volume')
            target=root/'target.mkv'
            with patch('bluray_map.manual.os.name','nt'), patch.object(Path,'rename',side_effect=OSError(errno.EXDEV,'cross volume')):
                move_manual(source,target,root/'audit',{})
            self.assertEqual(target.read_bytes(),b'cross volume'); self.assertFalse(source.exists())

    def test_unwritable_audit_prevents_move(self):
        with test_directory() as temp:
            root=Path(temp); source=root/'source.mkv'; source.write_bytes(b'keep')
            with self.assertRaises(OSError): move_manual(source,root/'target.mkv',root/'absent'/'audit',{})
            self.assertTrue(source.exists())

    def test_dialog_prefills_editable_programme_suggestion(self):
        with test_directory() as temp:
            source=Path(temp)/'file.mkv'; source.write_bytes(b'fixture')
            result={'disc_context':{'series':'Example Show','season':2},
                    'episodes':[{'playlist':'00051.mpls','duration_seconds':1500,'episode':1,
                                'source':'unverified','matching_rips':[]}], 'unmatched_rips':[str(source)]}
            app=tk.Tk(); app.withdraw()
            try:
                window=ManualMatchWindow(app,result,temp,'','ffprobe')
                window.withdraw(); app.update_idletasks()
                self.assertEqual(window.series.get(),'Example Show')
                self.assertEqual(window.season.get(),'2')
                self.assertEqual(window.episode.get(),'1')
                self.assertIn('episode number not verified',window.evidence.get('1.0','end'))
                window.episode.set('19')
                self.assertFalse(window.suggest_episode.get())
                self.assertEqual(len(window.file_checks),1)
                window.file_checks[str(source.resolve())].invoke()
                window.filename.set('Chosen title')
                original,target=window.plan()
                self.assertEqual(original,source); self.assertEqual(target.name,'Chosen title.mkv')
                window.close()
            finally: app.destroy()

    def test_programme_cards_filter_files_show_audio_and_remember_choices(self):
        with review_fixture() as (window,files):
            self.assertEqual(set(window.file_checks),{str(files[0]),str(files[1])})
            self.assertIn('English (default), Japanese',window.audio.get('1.0','end'))
            self.assertIn('<00:00:00.001',window.file_checks[str(files[0])].cget('text'))
            self.assertEqual((window.series.get(),window.season.get()),('Nikita','2'))
            window.file_checks[str(files[0])].invoke()
            window.file_checks[str(files[1])].invoke()
            self.assertFalse(window.file_vars[str(files[0])].get())
            window.episode.set('7'); window.episode_title.set('Chosen title')
            window.next_programme()
            self.assertEqual(set(window.file_checks),{str(files[2])})
            window.file_checks[str(files[2])].invoke()
            window.playlist.current(1); window.select_playlist()
            self.assertTrue(window.file_vars[str(files[1])].get())
            self.assertEqual(window.episode.get(),'7')
            self.assertEqual(window.episode_title.get(),'Chosen title')
            plans=window.plans()
            self.assertEqual([p[1] for p in plans],[files[1],files[2]])
            self.assertEqual([p[2].name for p in plans],['Nikita - s02e07 - Chosen title.mkv','Nikita - s02e02.mkv'])
            self.assertFalse(plans[0][3]['used_programme_number_suggestion'])
            self.assertTrue(all(p.exists() for p in files))

    def test_batch_rejects_duplicate_destinations_and_existing_files(self):
        with review_fixture() as (window,files):
            window.file_checks[str(files[0])].invoke()
            window.next_programme(); window.file_checks[str(files[2])].invoke()
            window.episode.set('1')
            with self.assertRaisesRegex(ValueError,'same destination'): window.plans()
            window.episode.set('2')
            target=window.plans()[1][2]; target.parent.mkdir(parents=True); target.write_bytes(b'existing')
            with self.assertRaisesRegex(ValueError,'destination already exists'): window.plans()
            self.assertTrue(all(p.exists() for p in files))

    def test_cannot_tick_same_file_for_two_programmes(self):
        with review_fixture() as (window,files):
            window.file_checks[str(files[0])].invoke(); window.next_programme()
            window.show_other.set(True); window.refresh_files()
            with patch('bluray_map.manual_dialog.messagebox.showinfo'):
                window.file_checks[str(files[0])].invoke()
            self.assertFalse(window.file_vars[str(files[0])].get())
            self.assertEqual(window.selected_path,'')
            self.assertEqual(len(window.plans()),1)

    def test_confirmed_batch_moves_only_ticked_files_and_logs_each_choice(self):
        with review_fixture() as (window,files):
            window.file_checks[str(files[0])].invoke()
            window.next_programme(); window.file_checks[str(files[2])].invoke()
            plans=window.plans(); window.start_moves(plans)
            deadline=time.monotonic()+5
            while window.moving and time.monotonic()<deadline:
                window.update(); time.sleep(.01)
            self.assertFalse(window.moving)
            self.assertTrue(files[1].exists()); self.assertTrue(files[3].exists())
            self.assertFalse(files[0].exists()); self.assertFalse(files[2].exists())
            self.assertTrue(all(plan[2].exists() for plan in plans))
            audit=[json.loads(line) for line in (window.folder/'DiscSteward-manual-decisions.jsonl').read_text().splitlines()]
            self.assertEqual([r['programme_number'] for r in audit if r['status']=='completed'],[1,2])
            self.assertTrue(all(not draft['path'] for draft in window.drafts.values()))

    def test_partial_failure_clears_only_completed_choices(self):
        with review_fixture() as (window,files):
            window.file_checks[str(files[0])].invoke()
            window.next_programme(); window.file_checks[str(files[2])].invoke()
            plans=window.plans()
            with patch('bluray_map.manual_dialog.move_manual',side_effect=['',OSError('File locked')]), \
                 patch('bluray_map.manual_dialog.messagebox.showwarning'):
                window.start_moves(plans)
                deadline=time.monotonic()+5
                while window.moving and time.monotonic()<deadline:
                    window.update(); time.sleep(.01)
            self.assertFalse(window.moving)
            self.assertEqual(window.drafts[1]['path'],'')
            self.assertEqual(window.drafts[2]['path'],str(files[2]))


if __name__ == '__main__': unittest.main()
