"""Report-style programme cards with persistent manual choices and batch moves."""
import json
import os
import queue
import re
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from .manual import manual_target, move_manual
from .ripmatch import duration_details, audio_tracks
from .timeformat import format_duration
from .episode_order import apply_episode_order, verified_identity, ASSUMPTION
from .renames import completed_renames, rename_summary


LANGUAGES = {'eng': 'English', 'fra': 'French', 'fre': 'French',
             'deu': 'German', 'ger': 'German', 'spa': 'Spanish',
             'ita': 'Italian', 'jpn': 'Japanese', 'und': 'Unknown'}
TIMING = {'video_track_tag': 'video-track timing', 'video_stream': 'video-stream timing',
          'container_fallback': 'whole-file timing'}


class ManualMatchWindow(tk.Toplevel):
    """Each programme owns its draft; switching the dropdown never loses edits.

    Programme order is only an editable manual suggestion. Only a confirmed
    batch moves files, with collision checks before the first move.
    """

    def __init__(self, parent, result, folder, library, ffprobe):
        super().__init__(parent)
        self.title('DiscSteward — Manual match, rename and move')
        self.geometry('1120x850'); self.minsize(800, 600)
        self.transient(parent)
        self.folder = Path(folder); self.result = result; self.ffprobe = ffprobe
        first = (result.get('episode_numbering') or {}).get('first_episode', 1)
        apply_episode_order(result, first)
        self.first_episode = tk.StringVar(value=str(first) if first != 1 else '')
        self.moving = False; self.loading = False; self.current_index = None
        self.drafts = {}; self.events = queue.Queue()
        self.episodes = list(result.get('episodes', []))
        self.programmes = [None] + self.episodes + list(result.get('extras', []))
        self.context = self.disc_context()
        self.inventory = {}
        for item in result.get('rip_inventory', []):
            self.inventory[str(Path(item['path']).resolve())] = dict(item)
        # Match records have audio metadata that the timing inventory may lack.
        for programme in self.programmes[1:]:
            for item in programme.get('matching_rips', []):
                self.inventory.setdefault(str(Path(item['path']).resolve()), {}).update(item)
        paths = set(result.get('unmatched_rips', [])) | set(self.inventory)
        paths.update(str(p) for p in self.folder.rglob('*.mkv'))
        completed_paths={Path(r['destination']).resolve() for r in completed_renames(result)}
        self.files = sorted({Path(path).resolve() for path in paths if Path(path).is_file() and Path(path).resolve() not in completed_paths},
                            key=lambda p: str(p).lower())
        self.library = tk.StringVar(value=library or str(self.folder))
        self.series = tk.StringVar(); self.season = tk.StringVar(); self.episode = tk.StringVar()
        self.episode_title = tk.StringVar(); self.filename = tk.StringVar()
        self.suggest_series = tk.BooleanVar(value=True)
        self.suggest_season = tk.BooleanVar(value=True)
        self.suggest_episode = tk.BooleanVar(value=True)
        self.show_other = tk.BooleanVar(value=False)
        self.selected_path = ''; self.file_vars = {}; self.file_checks = {}
        self.fields = {'series': self.series, 'season': self.season, 'episode': self.episode,
                       'title': self.episode_title, 'custom_filename': self.filename}
        self.flags = {'suggest_series': self.suggest_series, 'suggest_season': self.suggest_season,
                      'suggest_episode': self.suggest_episode}
        self.protocol('WM_DELETE_WINDOW', self.close)

        # Fixed footer plus a scrollable body keeps actions reachable on small
        # screens and Windows displays using larger font scaling.
        footer = ttk.Frame(self, padding=10); footer.pack(side='bottom', fill='x')
        self.summary = tk.StringVar()
        ttk.Label(footer, textvariable=self.summary).pack(anchor='w', pady=(0, 6))
        ttk.Button(footer, text='Preview selected names and destinations', command=self.preview_move).pack(side='left')
        self.move_button = ttk.Button(footer, text='Confirm rename and move selected…', command=self.confirm_move)
        self.move_button.pack(side='left', padx=8)
        ttk.Button(footer, text='Close', command=self.close).pack(side='right')
        outer = ttk.Frame(self); outer.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y'); self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        body = ttk.Frame(self.canvas, padding=12)
        item = self.canvas.create_window((0, 0), window=body, anchor='nw')
        body.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(item, width=e.width))
        self.bind('<MouseWheel>', self.scroll_body)
        ttk.Label(body, text='Choose a programme, tick one file, then choose the next programme. '
                  'Your choices and edits are kept until you close this window.', wraplength=950).pack(anchor='w')
        if completed_renames(result):
            ttk.Button(body,text=f'View completed renames and moves ({len(completed_renames(result))})',
                       command=self.show_completed_renames).pack(anchor='w',pady=8)
        numbering = ttk.Frame(body); numbering.pack(fill='x', pady=6)
        ttk.Label(numbering, text='First episode on this disc (optional; blank = 1):').pack(side='left')
        ttk.Entry(numbering, textvariable=self.first_episode, width=7).pack(side='left', padx=8)
        ttk.Button(numbering, text='Apply numbering', command=self.apply_numbering).pack(side='left')
        ttk.Label(body, text=ASSUMPTION + ' — check suggestions before moving.', wraplength=950).pack(anchor='w')
        nav = ttk.Frame(body); nav.pack(fill='x', pady=8)
        self.choices = ['Unmatched file / extra without a programme']
        for index, p in enumerate(self.programmes[1:], 1):
            self.choices.append(f"{self.programme_label(index)} — {p['playlist']} — {format_duration(p['duration_seconds'])}")
        self.playlist = ttk.Combobox(nav, values=self.choices, state='readonly')
        self.playlist.pack(side='left', fill='x', expand=True)
        self.playlist.bind('<<ComboboxSelected>>', self.select_playlist)
        ttk.Button(nav, text='Next programme →', command=self.next_programme).pack(side='left', padx=(8, 0))
        self.evidence = tk.Text(body, height=4, wrap='word', state='disabled', font=('Segoe UI', 10))
        self.evidence.pack(fill='x')
        ttk.Label(body, text='Ripped files — tick one file for this programme:').pack(anchor='w', pady=(12, 4))
        self.file_frame = ttk.Frame(body); self.file_frame.pack(fill='x')
        ttk.Checkbutton(body, text='Show other ripped files for a manual choice',
                        variable=self.show_other, command=self.refresh_files).pack(anchor='w', pady=4)
        self.audio = tk.Text(body, height=4, wrap='word', state='disabled', font=('Segoe UI', 10))
        self.audio.pack(fill='x', pady=6)
        self.file_info = tk.StringVar(value='Tick a file to select it.')
        ttk.Label(body, textvariable=self.file_info, wraplength=950).pack(anchor='w')
        actions = ttk.Frame(body); actions.pack(fill='x', pady=6)
        ttk.Button(actions, text='Read selected file details', command=self.inspect_file).pack(side='left')
        ttk.Button(actions, text='Play selected file', command=self.play).pack(side='left', padx=8)
        form = ttk.Frame(body); form.pack(fill='x', pady=10); form.columnconfigure(1, weight=1)
        labels = [('Show / series', self.series), ('Season', self.season), ('Episode', self.episode),
                  ('Episode title (optional)', self.episode_title), ('Or custom filename', self.filename),
                  ('Destination / Plex library folder', self.library)]
        for row, (label, var) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=4)
            ttk.Entry(form, textvariable=var).grid(row=row, column=1, sticky='ew', padx=8, pady=4)
        for row, label, flag, field in [(0, 'Use suggested show / series', self.suggest_series, 'series'),
                                       (1, 'Use suggested season', self.suggest_season, 'season'),
                                       (2, 'Use suggested episode / Programme number', self.suggest_episode, 'episode')]:
            ttk.Checkbutton(form, text=label, variable=flag, command=lambda f=field: self.apply_suggestion(f)).grid(row=row, column=2, sticky='w')
        ttk.Button(form, text='Browse', command=self.browse).grid(row=5, column=2, sticky='w')
        self.hint = tk.StringVar()
        ttk.Label(body, textvariable=self.hint, wraplength=950).pack(anchor='w', pady=4)
        self.name_preview = tk.StringVar()
        ttk.Label(body, textvariable=self.name_preview, wraplength=950).pack(anchor='w', pady=8)
        for field, var in self.fields.items():
            var.trace_add('write', lambda *args, f=field: self.field_changed(f))
        self.library.trace_add('write', lambda *args: self.update_name_preview())
        self.playlist.current(1 if self.episodes else 0); self.select_playlist()
        self.poll_id = self.after(100, self.poll_events)
        self.show_id = self.after_idle(self.show_modal)

    def show_modal(self):
        """Raise the review after Windows has actually displayed the window."""
        if self.winfo_exists() and self.winfo_viewable():
            self.lift()
            self.focus_force()
            self.grab_set()

    def show_completed_renames(self):
        if self.winfo_exists():
            messagebox.showinfo('Files already renamed / moved',rename_summary(completed_renames(self.result)),parent=self)

    def disc_context(self):
        if self.result.get('disc_context'): return self.result['disc_context']
        for name in ('prepared-disc.json', 'prepared-bluray-disc.json'):
            try:
                prepared = json.loads((self.folder/name).read_text(encoding='utf-8'))
                if prepared.get('disc_context'): return prepared['disc_context']
            except (OSError, ValueError, TypeError): pass
        database = self.result.get('database_lookup') or {}
        if database.get('series') and database.get('season'):
            return {'series': database['series'], 'season': database['season'], 'source': 'Saved database lookup'}
        # Older reports lack disc-title context. Explicit folder labels can
        # prefill manual fields but never establish episode identity.
        match = re.match(r'^(.*?)\s+(?:BD\s+)?Season\s*(\d+)\b', self.folder.name, re.I)
        if match:
            return {'series': re.sub(r'\s+BD$', '', match[1], flags=re.I).strip(),
                    'season': int(match[2]), 'source': 'Rip folder name (suggestion)'}
        return {}

    def programme_number(self, index=None):
        index = self.current_index if index is None else index
        if index is not None and 1 <= index <= len(self.episodes):
            p = self.programmes[index]
            return p.get('programme_number', index)
        return None

    def programme_label(self, index):
        number = self.programme_number(index)
        return f'Programme {number}' if number is not None else ('Unmatched file' if index == 0 else f"Extra {self.programmes[index]['playlist']}")

    def suggestions(self, index):
        p = self.programmes[index] or {}
        episode = p.get('episode') if verified_identity(p) else p.get('suggested_episode')
        return {'series': p.get('series') or self.context.get('series') or '',
                'season': str(p.get('season') or self.context.get('season') or ''),
                'episode': str(episode) if episode is not None else '',
                'title': p.get('title') or '', 'custom_filename': ''}

    def apply_numbering(self):
        if self.moving: return False
        try:
            first = int(self.first_episode.get().strip() or '1')
            if first < 1: raise ValueError()
        except ValueError:
            messagebox.showerror('Check first episode', 'Enter a positive whole number, or leave blank for 1.', parent=self)
            return False
        self.save_current()
        apply_episode_order(self.result, first)
        for index, draft in self.drafts.items():
            if draft['suggest_episode'] and not verified_identity(self.programmes[index] or {}):
                draft['episode'] = self.suggestions(index)['episode']
        draft = self.drafts.get(self.current_index)
        if draft:
            self.loading = True; self.episode.set(draft['episode']); self.loading = False
        self.update_name_preview()
        return True

    def save_current(self):
        if self.current_index is None: return
        self.drafts[self.current_index] = {**{key: var.get() for key, var in self.fields.items()},
                                          **{key: var.get() for key, var in self.flags.items()},
                                          'path': self.selected_path, 'show_other': self.show_other.get()}

    def select_playlist(self, event=None):
        if self.moving:
            self.playlist.current(self.current_index); return
        self.save_current(); self.current_index = self.playlist.current()
        draft = self.drafts.get(self.current_index) or {**self.suggestions(self.current_index),
                    **{key: True for key in self.flags}, 'path': '', 'show_other': False}
        self.loading = True
        for key, var in self.fields.items(): var.set(draft[key])
        for key, var in self.flags.items(): var.set(draft[key])
        self.selected_path = draft['path']; self.show_other.set(draft['show_other'])
        self.loading = False
        p = self.programmes[self.current_index]
        if p:
            identity = (f"{p.get('series', '')} — Season {p['season']}, Episode {p.get('episode', '')}"
                        if verified_identity(p) else 'episode number not verified')
            text = (f"{self.programme_label(self.current_index)} — {identity} — {format_duration(p['duration_seconds'])}\n"
                    f"{self.result.get('programme_kind', 'Blu-ray playlist')}: {p['playlist']}\nDisc video segments: {' + '.join(p.get('clips', []))}\n"
                    f"Identification source: {p.get('source', 'Not verified')}")
            history=completed_renames(self.result,p)
            if history: text+='\nAlready completed:\n'+rename_summary(history)
        else: text = 'Choose a ripped file and enter its episode details or a custom filename.'
        self.set_text(self.evidence, text); self.refresh_files(); self.update_name_preview()

    def next_programme(self):
        if self.moving: return
        index = self.current_index + 1
        if index >= len(self.programmes): index = 1 if self.episodes else 0
        self.playlist.current(index); self.select_playlist()

    def field_changed(self, field):
        if self.loading: return
        if field in ('series', 'season', 'episode'): self.flags['suggest_' + field].set(False)
        self.update_name_preview()

    def apply_suggestion(self, field):
        if self.flags['suggest_' + field].get():
            self.loading = True; self.fields[field].set(self.suggestions(self.current_index)[field]); self.loading = False
        self.update_name_preview()

    @staticmethod
    def set_text(widget, text):
        widget.config(state='normal'); widget.delete('1.0', 'end')
        widget.insert('end', text); widget.config(state='disabled')

    def scroll_body(self, event):
        if isinstance(event.widget, (ttk.Combobox, tk.Text)): return
        self.canvas.yview_scroll(-int(event.delta/120), 'units')

    def refresh_files(self):
        for child in self.file_frame.winfo_children(): child.destroy()
        self.file_vars = {}; self.file_checks = {}
        p = self.programmes[self.current_index] or {}
        matches = {str(Path(item['path']).resolve()): item for item in p.get('matching_rips', [])}
        available_matches = [path for path in self.files if str(path) in matches and path.is_file()]
        visible = available_matches[:]
        if self.show_other.get() or not available_matches:
            visible += [path for path in self.files if path.is_file() and path not in visible]
        if not available_matches:
            ttk.Label(self.file_frame, text='No available matched files. Other ripped files are shown for you to choose from.').pack(anchor='w')
        for path in visible:
            key = str(path); data = self.inventory.get(key, {}); match = matches.get(key)
            if match is not None:
                details = TIMING.get(match.get('duration_source'), 'matched timing')
                if match.get('duration_seconds') is not None: details += f"; length {format_duration(match['duration_seconds'])}"
                if match.get('difference_seconds') is not None: details += f"; difference {format_duration(match['difference_seconds'], difference=True)}"
            else:
                details = 'manual choice — no confirmed match to this programme'
                if data.get('duration_seconds') is not None: details += f"; length {format_duration(data['duration_seconds'])}"
            var = tk.BooleanVar(value=key == self.selected_path)
            check = ttk.Checkbutton(self.file_frame, text=f'{path.name} — {details}', variable=var,
                                    command=lambda k=key: self.tick_file(k))
            check.pack(anchor='w', fill='x', pady=2)
            self.file_vars[key] = var; self.file_checks[key] = check
        audio_lines = ['Audio tracks for the matching files:' if available_matches else 'Audio tracks:']
        for path in visible:
            tracks = self.inventory.get(str(path), {}).get('audio_tracks', [])
            description = ', '.join(LANGUAGES.get(t.get('language', 'und'), t.get('language', 'Unknown')) +
                                     (' (default)' if t.get('default') else '') for t in tracks)
            audio_lines.append(f'{path.name}: {description or "details unavailable — use Read selected file details"}')
        self.set_text(self.audio, '\n'.join(audio_lines))
        self.select_file(); self.update_summary()

    def tick_file(self, key):
        checked = self.file_vars[key].get()
        if checked:
            for index, draft in self.drafts.items():
                if index != self.current_index and draft['path'] == key:
                    self.file_vars[key].set(False)
                    messagebox.showinfo('File already selected',
                        f'This file is already ticked for {self.programme_label(index)}. Untick it there first.', parent=self)
                    return
        self.selected_path = key if checked else ''
        for path, var in self.file_vars.items(): var.set(path == self.selected_path)
        self.save_current(); self.select_file(); self.update_summary(); self.update_name_preview()

    def selected(self):
        if not self.selected_path: raise ValueError('Tick a ripped file first.')
        return Path(self.selected_path)

    def select_file(self):
        self.file_info.set(f'Selected: {self.selected_path}' if self.selected_path else 'Tick one file, then choose the next programme.')

    def inspect_file(self):
        try: path = self.selected()
        except ValueError as error:
            messagebox.showinfo('Select a file', str(error), parent=self); return
        self.file_info.set('Reading video and audio details…')
        def work():
            try:
                data = {'path': str(path), **duration_details(self.ffprobe, path), 'audio_tracks': audio_tracks(self.ffprobe, path)}
                self.events.put(('details', path, data))
            except Exception as error: self.events.put(('inspect_error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def play(self):
        try: os.startfile(str(self.selected()))
        except (ValueError, OSError) as error: messagebox.showerror('Cannot play file', str(error), parent=self)

    def browse(self):
        folder = filedialog.askdirectory(parent=self, initialdir=self.library.get())
        if folder: self.library.set(folder)

    def update_name_preview(self):
        if self.loading or self.current_index is None: return
        try:
            target = manual_target(self.library.get(), self.series.get(), self.season.get(),
                                   self.episode.get(), self.episode_title.get(), self.filename.get())
            self.name_preview.set(f'Proposed name: {target.name}\nDestination: {target.parent}')
        except ValueError: self.name_preview.set('Fill in the missing name details, or enter a custom filename.')
        p = self.programmes[self.current_index] or {}
        suggestion = ('Episode identity from ' + p.get('source', 'saved mapping') if verified_identity(p) else
                      ASSUMPTION + '; check the suggested episode before moving.')
        self.hint.set(f"Show / season suggestion: {self.context.get('source', 'saved programme information')}. {suggestion}")

    def update_summary(self):
        self.save_current()
        count = sum(bool(d['path']) for d in self.drafts.values())
        self.summary.set(f'{count} file(s) selected across programmes. Selections are kept when you change the dropdown.')
        self.playlist.configure(values=[label + ('  [file ticked]' if self.drafts.get(i, {}).get('path') else '')
                                        for i, label in enumerate(self.choices)])
        self.playlist.current(self.current_index)

    def plan(self):
        return self.selected(), manual_target(self.library.get(), self.series.get(), self.season.get(),
                                              self.episode.get(), self.episode_title.get(), self.filename.get())

    def plans(self):
        """Validate the whole batch before the first move, including collisions."""
        self.save_current()
        plans = []; sources = set(); destinations = set()
        for index, draft in sorted(self.drafts.items()):
            if not draft['path']: continue
            label = self.programme_label(index); source = Path(draft['path']).resolve()
            try:
                target = manual_target(self.library.get(), draft['series'], draft['season'], draft['episode'],
                                       draft['title'], draft['custom_filename'])
            except ValueError as error: raise ValueError(f'{label}: {error}') from error
            if not source.is_file(): raise ValueError(f'{label}: the selected file is no longer there: {source.name}')
            if target.exists(): raise ValueError(f'{label}: destination already exists: {target}')
            source_key = str(source).casefold(); target_key = str(target).casefold()
            if source_key in sources: raise ValueError(f'{label}: the same file has been selected twice.')
            if target_key in destinations: raise ValueError(f'{label}: two selected files would have the same destination: {target}')
            sources.add(source_key); destinations.add(target_key)
            p = self.programmes[index] or {}
            decision = {key: draft[key] for key in self.fields}
            decision.update(playlist=p.get('playlist'), programme_number=self.programme_number(index),
                            used_programme_number_suggestion=bool(not verified_identity(p) and draft['suggest_episode']),
                            numbering_source=ASSUMPTION if not verified_identity(p) and draft['suggest_episode'] else 'User choice or verified identity',
                            first_episode_on_disc=self.result['episode_numbering']['first_episode'],
                            suggestion_source=self.context.get('source'))
            plans.append((index, source, target, decision))
        if not plans: raise ValueError('Tick a file for at least one programme first.')
        return plans

    def preview_move(self):
        if self.moving: return
        if not self.apply_numbering(): return
        try: plans = self.plans()
        except ValueError as error:
            messagebox.showerror('Check selected files', str(error), parent=self); return
        preview = tk.Toplevel(self); preview.title('DiscSteward — Review selected files')
        preview.geometry('900x550'); preview.transient(self); preview.grab_set()
        ttk.Label(preview, padding=10, text=f'Review {len(plans)} file(s). These are your manual assignments.').pack(anchor='w')
        text_frame = ttk.Frame(preview); text_frame.pack(fill='both', expand=True, padx=10)
        text = tk.Text(text_frame, wrap='word'); text.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(text_frame, command=text.yview); scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        self.set_text(text, '\n\n'.join(f'{self.programme_label(i)}\n{source}\n→ {target}' for i, source, target, _ in plans))
        actions = ttk.Frame(preview, padding=10); actions.pack(fill='x')
        def dismiss():
            preview.destroy(); self.grab_set()
        def accept():
            # Recheck after the preview; destinations may have changed on disk.
            try: fresh = self.plans()
            except ValueError as error:
                messagebox.showerror('Cannot move selected files', str(error), parent=preview); return
            dismiss(); self.start_moves(fresh)
        ttk.Button(actions, text=f'Rename and move {len(plans)} file(s)', command=accept).pack(side='left')
        ttk.Button(actions, text='Back to choices', command=dismiss).pack(side='right')
        preview.protocol('WM_DELETE_WINDOW', dismiss)
        return plans

    def confirm_move(self):
        self.preview_move()

    def start_moves(self, plans):
        self.moving = True; self.disabled_states = []
        def disable(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Entry, ttk.Combobox)):
                    self.disabled_states.append((child, child.state())); child.state(['disabled'])
                disable(child)
        disable(self); self.file_info.set(f'Moving {len(plans)} file(s)…')
        def work():
            completed = []; errors = []; warnings = []
            for index, source, target, decision in plans:
                try:
                    warning = move_manual(source, target, self.folder/'DiscSteward-manual-decisions.jsonl', decision)
                    completed.append((index, source, target))
                    if warning: warnings.append(warning)
                except Exception as error:
                    errors.append(f'{source.name}: {error}'); break
            self.events.put(('moves_done', completed, errors, warnings))
        threading.Thread(target=work, daemon=True).start()

    def poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == 'details':
                    _, path, data = event
                    self.inventory.setdefault(str(path), {}).update(data)
                    if not self.moving: self.refresh_files()
                elif event[0] == 'inspect_error': self.file_info.set(f'Could not read file details: {event[1]}')
                elif event[0] == 'moves_done': self.moves_done(*event[1:])
        except queue.Empty: pass
        self.poll_id = self.after(100, self.poll_events)

    def moves_done(self, completed, errors, warnings):
        self.moving = False
        for widget, states in self.disabled_states:
            widget.state(['!disabled']); widget.state(states)
        for index, source, target in completed:
            self.drafts[index]['path'] = ''
            if self.current_index == index: self.selected_path = ''
            self.files = [p for p in self.files if p != source]
        self.refresh_files(); self.update_name_preview()
        self.file_info.set(f'Moved {len(completed)} file(s).' + (' Remaining choices are kept for retry.' if errors else ''))
        if errors or warnings: messagebox.showwarning('Move results', '\n'.join(errors + warnings), parent=self)

    def close(self):
        if self.moving: return
        self.after_cancel(self.show_id)
        self.after_cancel(self.poll_id); self.destroy()
