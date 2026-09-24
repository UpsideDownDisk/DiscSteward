"""DiscSteward: Windows disc ripping, mapping and Plex naming application."""
from __future__ import annotations
import json, os, queue, re, shutil, subprocess, threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
try:
    import winreg
except ImportError:  # The graphical application is Windows-only, but keep imports harmless for code checks.
    winreg=None

ROOT=Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT / "src"))
from bluray_map.discovery import scan
from bluray_map.analyze import classify
from bluray_map.discdb import lookup as discdb_lookup
from bluray_map.languages import LANGUAGE_CODES
from bluray_map.timeformat import format_duration, readable_timings
from bluray_map.local_evidence import local_assignments, disc_identity
from bluray_map.ripmatch import match_rips, audio_tracks
from bluray_map.automation import disc_folder_name, reserve_disc_folder, run_rip_and_eject, makemkv_disc_name
from bluray_map.manual_dialog import ManualMatchWindow
from bluray_map.episode_order import verified_identity, ASSUMPTION
from bluray_map.automation import resolve_rip_drive, disc_fingerprint
from bluray_map.automation import DiscChanged, require_disc
from bluray_map.subtitles import GUIDANCE, match_folder, apply_matches, standalone_result, report_lines
from bluray_map.renames import completed_renames, rename_summary, record_rename

SETTINGS=Path(os.environ.get("APPDATA",Path.home())) / "DiscSteward" / "settings.json"
LEGACY_SETTINGS=SETTINGS.parent.parent / "BluRayEpisodeMapper" / "settings.json"
# Settings are intentionally stored outside the copied tool folder so an update
# to the tool does not make the user choose their disc, rip, and FFmpeg paths again.

def load_settings():
    try: return json.loads((SETTINGS if SETTINGS.exists() else LEGACY_SETTINGS).read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return {}
def safe_name(value): return re.sub(r'[<>:"/\\|?*]+'," ",value).strip().rstrip(".")
def plex_target(root, episode):
    """Build Plex's TV-series folder and sXXeYY filename for one verified episode."""
    series=safe_name(episode["series"]); season=int(episode["season"]); ext=Path(episode["matching_rips"][0]["path"]).suffix
    title=(" - " + safe_name(episode.get("title") or "")) if episode.get("title") else ""
    filename=f"{series} - s{season:02}e{int(episode['episode']):02}{title}{ext}"
    return Path(root)/series/f"Season {season:02}"/filename

def add_proposed_names(result, convention, library):
    """Provide copyable names only when an episode identity is available."""
    for episode in result['episodes']:
        episode['proposed_filename'] = None
        episode['proposed_relative_path'] = None
        if verified_identity(episode) and episode['matching_rips']:
            relative = NAMING_CONVENTIONS[convention]('', episode)
            episode['proposed_filename'] = relative.name
            episode['proposed_relative_path'] = str(relative)
            episode['proposed_destination'] = str(Path(library)/relative) if library else None

NAMING_CONVENTIONS={"Plex TV Series":plex_target}
AUDIO_LANGUAGE_NAMES={"eng":"English","fra":"French","deu":"German","spa":"Spanish","ita":"Italian","jpn":"Japanese","und":"Unknown"}

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("DiscSteward"); self.geometry("1180x900"); self.minsize(850,600)
        icon_path = ROOT / "assets" / "discsteward-icon.png"
        if icon_path.is_file():
            try:
                self._discsteward_icon = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._discsteward_icon)
            except tk.TclError:
                # Keep the application usable if a copied installation omitted
                # the optional artwork or an older Tk lacks PNG support.
                self._discsteward_icon = None
        saved=load_settings()
        self.subtitles=tk.StringVar(value=saved.get('subtitles',''))
        self.existing_files=tk.StringVar(value=saved.get('existing_files',''))
        self.existing_series=tk.StringVar(value=saved.get('existing_series',''))
        self.disc=tk.StringVar(value=saved.get("disc","")); self.rips=tk.StringVar(value=saved.get("rips","")); self.library=tk.StringVar(value=saved.get("library","")); self.makemkv=tk.StringVar(value=saved.get("makemkv","")); self.makemkvcon=tk.StringVar(value=saved.get("makemkvcon","")); self.profile=tk.StringVar(value=saved.get("profile","")); self.drive_number=tk.StringVar(value=saved.get("drive_number","0")); self.ffprobe=tk.StringVar(value=saved.get("ffprobe") or shutil.which("ffprobe") or ""); self.convention=tk.StringVar(value=saved.get("convention","Plex TV Series")); self.audio_language=tk.StringVar(value=saved.get("audio_language","English")); self.auto_accept=tk.BooleanVar(value=saved.get("auto_accept",False)); self.watch_new=tk.BooleanVar(value=False); self.last_result=None; self.prepared_scan=None; self.prepared_database=None; self.prepared_local=None; self.prepared_context=None; self.automation_busy=False; self.watch_stop=threading.Event(); self.last_disc_signature=None
        # Keep the settings only as tall as their contents; give spare height
        # to the log. On smaller screens the settings canvas can still scroll.
        self.columnconfigure(0,weight=1); self.rowconfigure(2,weight=1)
        controls=ttk.Frame(self); controls.grid(row=0,column=0,sticky='nsew')
        canvas=tk.Canvas(controls,highlightthickness=0,height=1)
        scrollbar=ttk.Scrollbar(controls,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y'); canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        form=ttk.Frame(canvas,padding=14)
        content=canvas.create_window((0,0),window=form,anchor='nw')
        form.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(content,width=event.width))
        actions=ttk.Frame(form); actions.grid(row=0,column=0,columnspan=3,sticky='w',pady=(0,12))
        options=ttk.Frame(actions); options.pack(anchor='w',pady=(0,8))
        ttk.Checkbutton(options,text="Automatically accept safe Plex renames",variable=self.auto_accept).pack(side="left")
        ttk.Checkbutton(options,text="Watch for a newly loaded disc",variable=self.watch_new,command=self.toggle_watch).pack(side="left",padx=(16,0))
        for group in [
            [('Automated rip, map & preview',self.automate),('Open MakeMKV',self.open_makemkv)],
            [('Prepare disc',self.prepare_disc),('Make mapping',self.run),('Preview and rename',self.rename),('Open saved mapping…',self.open_saved_mapping)],
            [('Manual match, rename and move',self.manual_review)],
            [('Match existing / misnamed files…',self.match_existing)]]:
            line=ttk.Frame(actions); line.pack(anchor='w',pady=4)
            for label,command in group:
                ttk.Button(line,text=label,command=command).pack(side='left',padx=(0,8))
        self.row(form,"Disc source (Blu-ray BDMV or DVD VIDEO_TS):",self.disc,True)
        self.row(form,'Local subtitle folder (SRT or ZIP, optional):',self.subtitles,True,
                 help_command=lambda:messagebox.showinfo('Local subtitle guidance',GUIDANCE))
        self.row(form,"Plex TV library folder:",self.library,True)
        for label,var,values in [('Naming convention:',self.convention,list(NAMING_CONVENTIONS)),
                                 ('Preferred language:',self.audio_language,list(LANGUAGE_CODES))]:
            i=form.grid_size()[1]
            ttk.Label(form,text=label).grid(row=i,column=0,sticky='w',pady=4)
            ttk.Combobox(form,textvariable=var,values=values,state='readonly',width=30).grid(row=i,column=1,sticky='w',padx=8,pady=4)
        self.row(form,"Rip folder (auto: parent of disc folders):",self.rips,True)
        self.row(form,"MakeMKV program (optional):",self.makemkv,False)
        self.row(form,"MakeMKV automation program:",self.makemkvcon,False)
        self.row(form,"Saved MakeMKV profile:",self.profile,False)
        self.row(form,"ffprobe program:",self.ffprobe,False)
        self.status=tk.StringVar(value="1. Choose the disc folder and MakeMKV output folder.  2. Open MakeMKV.  3. When finished, make the mapping.")
        status_label=ttk.Label(self,textvariable=self.status,wraplength=1000)
        status_label.grid(row=1,column=0,sticky='ew',padx=14,pady=10)
        self.output=tk.Text(self,height=10,wrap="word",state="disabled"); self.output.grid(row=2,column=0,sticky='nsew',padx=14,pady=(0,14))
        self._controls_canvas=canvas; self._controls_form=form; self._status_label=status_label
        self.bind('<Configure>',self._size_controls,add='+')
        self.after_idle(self._size_controls)
        self.protocol("WM_DELETE_WINDOW",self.close)
    def _size_controls(self,event=None):
        if event is not None and event.widget is not self: return
        # Reserve enough room for the status and log when the window is short.
        available=max(220,self.winfo_height()-self.output.winfo_reqheight()
                      -self._status_label.winfo_reqheight()-36)
        height=min(self._controls_form.winfo_reqheight(),available)
        if int(self._controls_canvas.cget('height')) != height:
            self._controls_canvas.configure(height=height)
    def save_settings(self):
        try:
            SETTINGS.parent.mkdir(parents=True,exist_ok=True)
            SETTINGS.write_text(json.dumps({"existing_series":self.existing_series.get(),"subtitles":self.subtitles.get(),"existing_files":self.existing_files.get(),"disc":self.disc.get(),"rips":self.rips.get(),"library":self.library.get(),"makemkv":self.makemkv.get(),"makemkvcon":self.makemkvcon.get(),"profile":self.profile.get(),"drive_number":self.drive_number.get(),"ffprobe":self.ffprobe.get(),"convention":self.convention.get(),"audio_language":self.audio_language.get(),"auto_accept":self.auto_accept.get()},indent=2),encoding="utf-8")
        except OSError: pass
    def close(self):
        if self.automation_busy:
            messagebox.showinfo('DiscSteward is busy','Please wait for the current rip or mapping to finish before closing.'); return
        self.watch_stop.set(); self.save_settings(); self.destroy()
    def row(self,parent,label,var,folder,button="Browse",help_command=None):
        i=parent.grid_size()[1]; ttk.Label(parent,text=label).grid(row=i,column=0,sticky="w",pady=4)
        ttk.Entry(parent,textvariable=var,width=68).grid(row=i,column=1,sticky="ew",pady=4,padx=8)
        buttons=ttk.Frame(parent); buttons.grid(row=i,column=2,sticky='e',pady=4)
        if help_command: ttk.Button(buttons,text='Subtitle help',command=help_command).pack(side='left',padx=(8,0))
        ttk.Button(buttons,text=button,command=lambda:self.choose(var,folder)).pack(side='left')
        parent.columnconfigure(1,weight=1)
    def choose(self,var,folder):
        current=Path(var.get().strip()) if var.get().strip() else None
        # Reopen at the saved selection.  For an executable, its parent folder is
        # the sensible starting point; for a folder, it is the folder itself.
        initialdir=(current if folder else current.parent) if current and current.exists() else None
        options={"initialdir":str(initialdir)} if initialdir else {}
        filetypes=[("MakeMKV profiles","*.xml;*.mmcp.xml"), ("All files","*.*")] if var is self.profile else [("Programs","*.exe"), ("All files","*.*")]
        v=(filedialog.askdirectory(**options) if folder else
           filedialog.askopenfilename(filetypes=filetypes,**options))
        if v: var.set(v)
    def open_makemkv(self):
        exe=self.makemkv.get().strip()
        if not exe:
            for p in [r"C:\Program Files (x86)\MakeMKV\makemkv.exe",r"C:\Program Files\MakeMKV\makemkv.exe"]:
                if Path(p).is_file(): exe=p; self.makemkv.set(p); break
        if not exe or not Path(exe).is_file(): messagebox.showinfo("MakeMKV not found","Install MakeMKV, or use Browse beside MakeMKV program to select makemkv.exe."); return
        subprocess.Popen([exe]); self.status.set("MakeMKV opened. Rip the disc, then return here and click Make mapping.")

    def automation_requirements(self):
        """Validate the inputs needed before starting an unattended MakeMKV run."""
        # Watch mode must be usable with an empty drive.  The source only needs
        # to name the drive (for example F:\\); its disc structure is checked after insertion.
        if not self.disc.get().strip():
            messagebox.showerror("Automation setup needed","Choose the disc drive or source folder first."); return False
        if self.profile.get().strip() and not Path(self.profile.get()).is_file():
            messagebox.showerror("Profile not found","Choose an existing MakeMKV profile, or leave this field blank to use MakeMKV's saved settings."); return False
        if not self.find_makemkvcon():
            messagebox.showerror("MakeMKV automation program not found","Browse to makemkvcon64.exe (or makemkvcon.exe) in the MakeMKV program folder."); return False
        if not Path(self.ffprobe.get()).is_file() and not shutil.which(self.ffprobe.get()):
            messagebox.showerror("ffprobe not found","Install FFmpeg, then choose ffprobe.exe."); return False
        return True

    def makemkv_destination(self):
        """Read MakeMKV's configured default destination when the UI is blank."""
        configured=self.rips.get().strip()
        if configured: return configured
        # MakeMKV's Windows GUI stores this setting in HKCU.  Consult it first:
        # it is the exact Default Destination the user sees in MakeMKV.
        if winreg:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\MakeMKV") as key:
                    value,_=winreg.QueryValueEx(key,"app_DestinationDir")
                    if isinstance(value,str) and value.strip(): return value.strip()
            except OSError: pass
        candidates=[Path.home()/".MakeMKV"/"settings.conf",
                    Path(os.environ.get("APPDATA", ""))/"MakeMKV"/"settings.conf"]
        for settings in candidates:
            try: text=settings.read_text(encoding="utf-8",errors="ignore")
            except OSError: continue
            match=re.search(r'^\s*app_DestinationDir\s*=\s*"?(.*?)"?\s*$',text,re.MULTILINE)
            if match and match.group(1).strip():
                # MakeMKV settings escape Windows backslashes and quotes.
                return match.group(1).strip().replace(r"\\", "\\").replace(r'\"', '"')
        return ""

    def find_makemkvcon(self):
        """Find MakeMKV's non-interactive executable without assuming one install path."""
        chosen=self.makemkvcon.get().strip()
        candidates=[chosen] if chosen else []
        candidates += [r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe",r"C:\Program Files\MakeMKV\makemkvcon64.exe",r"C:\Program Files (x86)\MakeMKV\makemkvcon.exe",r"C:\Program Files\MakeMKV\makemkvcon.exe"]
        for candidate in candidates:
            if candidate and Path(candidate).is_file(): self.makemkvcon.set(candidate); return candidate
        return None

    def prepare_disc(self, then_rip=False):
        """Read and persist Blu-ray or DVD title evidence before MakeMKV starts."""
        if self.automation_busy: return
        if not self.disc.get().strip():
            messagebox.showerror("Missing folder","Choose the Blu-ray or DVD source first."); return
        if then_rip and not self.automation_requirements(): return
        output=self.makemkv_destination()
        if not output:
            messagebox.showerror("MakeMKV output folder needed","Set MakeMKV's Default Destination in MakeMKV, or choose MakeMKV output / ripped MKVs here."); return
        self.active_rips=output
        self.prepare_source=self.disc.get()
        self.prepared_scan=None; self.prepared_database=None; self.prepared_local=None; self.prepared_context=None
        if then_rip:
            source_path=Path(self.prepare_source)
            if source_path.name.upper() in ('BDMV','VIDEO_TS'):
                self.prepare_source=str(source_path.parent)
            # Freeze the source/program/profile for this run. Editing controls
            # during the lookup must not switch the disc that MakeMKV rips.
            self.rip_settings={'executable':self.find_makemkvcon(),
                               'profile':self.profile.get().strip(), 'source':self.prepare_source}
        self.save_settings(); self.automation_busy=True; self.status.set("Preparing disc: detecting type and reading title information…")
        threading.Thread(target=self.prepare_worker,args=(then_rip,),daemon=True).start()

    def prepare_worker(self, then_rip):
        try:
            if then_rip:
                try: self.rip_settings['fingerprint']=disc_fingerprint(self.prepare_source)
                except (OSError,ValueError) as error: raise DiscChanged('No readable disc. Waiting for a disc to be inserted.') from error
                self.last_disc_signature=self.rip_settings['fingerprint']
                self.rip_settings['drive']=resolve_rip_drive(self.rip_settings['executable'], self.prepare_source)
                self.rip_settings['disc_name']=makemkv_disc_name(self.rip_settings['executable'], self.rip_settings['drive'])
                require_disc(self.prepare_source,self.rip_settings['fingerprint'])
            prepared=scan(self.prepare_source)
            if then_rip: require_disc(self.prepare_source,self.rip_settings['fingerprint'])
            destination=reserve_disc_folder(
                self.active_rips,
                disc_folder_name(prepared.bdmv, self.rip_settings.get('disc_name', '') if then_rip else '')
            ) if then_rip else Path(self.active_rips)
            destination.mkdir(parents=True,exist_ok=True)
            self.active_rips=str(destination)
            # This record lets the user retain the disc evidence even if it is
            # ejected after MakeMKV finishes.  The in-memory Scan is used to map.
            database=discdb_lookup(prepared.bdmv)
            candidates,_,_,_=classify(prepared.playlists,900,7200)
            local=local_assignments(prepared.bdmv,candidates) if prepared.disc_type == "blu-ray" and not database else {}
            series,season=disc_identity(prepared.bdmv) if prepared.disc_type == "blu-ray" else (None,None)
            context={"series":series,"season":season,"source":"Disc metadata title"} if series and season else None
            database_record=database or {"source":"TheDiscDb","status":"no matching disc or lookup unavailable"}
            record={"scan":prepared.to_dict(),"database_lookup":database_record,"local_assignments":local,"disc_context":context}
            if then_rip: require_disc(self.prepare_source,self.rip_settings['fingerprint'])
            prepared_file=destination/"prepared-disc.json"
            prepared_file.write_text(json.dumps(readable_timings(record),indent=2),encoding="utf-8")
            self.prepared_scan=prepared; self.prepared_database=database_record; self.prepared_local=local
            self.prepared_context=context
            self.after(0,lambda:self.prepared_done(prepared_file,then_rip))
        except DiscChanged as e:
            self.after(0,self.disc_interrupted,str(e))
        except Exception as e:
            if then_rip and self.disc_was_changed():
                self.after(0,self.disc_interrupted,'Disc removed or replaced during preparation.'); return
            self.after(0,lambda detail=str(e): self.automation_failed("Preparation failed",detail))

    def prepared_done(self,prepared_file,then_rip):
        self.status.set(f"Disc prepared: {prepared_file.name} saved beside the rips.")
        if then_rip: self.start_automated_rip()
        else: self.automation_busy=False

    def automate(self): self.prepare_disc(then_rip=True)

    def start_automated_rip(self):
        settings=self.rip_settings
        self.drive_number.set(str(settings['drive']))
        self.rip_command=[settings['executable'],'--robot','--messages=-stdout','--progress=-same']
        if settings['profile']: self.rip_command.append(f"--profile={settings['profile']}")
        self.rip_command += ['mkv',f"disc:{settings['drive']}",'all',self.active_rips]
        self.append_rip_message(f'Saving this disc to: {self.active_rips}')
        threading.Thread(target=self.rip_worker,daemon=True).start()

    def append_rip_message(self,message):
        self.status.set(message)
        self.output.config(state='normal'); self.output.insert('end',message+'\n')
        self.output.see('end'); self.output.config(state='disabled')

    def rip_worker(self):
        try:
            settings=self.rip_settings
            require_disc(settings['source'], settings['fingerprint'])
            if resolve_rip_drive(settings['executable'], settings['source']) != settings['drive']:
                raise ValueError('The selected drive or disc changed after preparation. No rip started; retry preparation.')
            ejected=run_rip_and_eject(self.rip_command,Path(self.active_rips)/'DiscSteward-MakeMKV.log',
                    lambda message:self.after(0,self.append_rip_message,message),self.prepare_source,
                    expected_fingerprint=settings['fingerprint'])
            if ejected: self.last_disc_signature=None
            self.after(0,self.rip_done)
        except DiscChanged as e:
            self.after(0,self.disc_interrupted,str(e))
        except Exception as e:
            if self.disc_was_changed():
                self.after(0,self.disc_interrupted,'Disc removed or replaced while starting or running MakeMKV.'); return
            self.after(0,lambda detail=str(e): self.automation_failed("MakeMKV rip failed",detail))

    def disc_was_changed(self):
        settings=getattr(self,'rip_settings',{})
        if not settings.get('fingerprint'): return False
        try: require_disc(settings['source'],settings['fingerprint'])
        except DiscChanged: return True
        return False

    def disc_interrupted(self,detail):
        """Discard stale evidence; retain partial rips and restart from insertion."""
        self.automation_busy=False
        self.prepared_scan=None; self.prepared_database=None; self.prepared_local=None; self.prepared_context=None
        self.last_result=None; self.last_disc_signature=None
        self.append_rip_message(detail+' Partial files are kept; no mapping or rename was performed. Waiting for a disc…')
        if not self.watch_new.get():
            self.watch_new.set(True)
            self.watch_source=self.disc.get()
            self.watch_stop=threading.Event()
            threading.Thread(target=self.watch_worker,args=(self.watch_stop,),daemon=True).start()

    def rip_done(self):
        self.status.set("MakeMKV completed. Matching prepared disc programmes to MKVs…")
        threading.Thread(target=self.worker,args=(self.prepared_scan,True),daemon=True).start()

    def automation_failed(self,title,detail):
        self.automation_busy=False; self.status.set(title); messagebox.showerror(title,detail)

    def toggle_watch(self):
        if self.watch_new.get():
            if not self.automation_requirements(): self.watch_new.set(False); return
            self.watch_stop=threading.Event(); self.status.set("Watching for a newly loaded disc…")
            self.watch_source=self.disc.get()
            threading.Thread(target=self.watch_worker,args=(self.watch_stop,),daemon=True).start()
        else:
            self.watch_stop.set(); self.status.set("Disc watch stopped.")

    def watch_worker(self,stop=None):
        """Watch navigation files on the selected drive, not another drive index."""
        stop=stop or self.watch_stop
        stable=None
        while not stop.wait(3):
            if self.automation_busy: continue
            try:
                # After automatic eject, an empty optical drive is expected.
                # MakeMKV can print nonempty diagnostics even with no disc;
                # do not mistake that output for a newly inserted disc.
                source=getattr(self,'watch_source',None) or self.disc.get()
                watch_path=Path(source)
                if watch_path.name.upper() in ('BDMV','VIDEO_TS'): watch_path=watch_path.parent
                if not watch_path.exists():
                    self.last_disc_signature=None
                    stable=None
                    continue
                signature=disc_fingerprint(source)
                if not signature:
                    # A later insertion of the same title is still a new disc run.
                    self.last_disc_signature=None
                elif signature != self.last_disc_signature and signature == stable:
                    self.after(0,self.start_watched_disc,source,signature,stop)
                stable=signature
            except (OSError,ValueError): self.last_disc_signature=None; stable=None

    def start_watched_disc(self, source, signature=None, stop=None):
        if self.watch_stop.is_set() or (stop is not None and stop is not self.watch_stop): return
        if self.automation_busy: return
        if signature and signature == self.last_disc_signature: return
        if self.disc.get() != source:
            self.watch_stop.set(); self.watch_new.set(False)
            self.status.set('Disc source changed. Enable disc watch again for the new source.'); return
        self.last_disc_signature=signature
        self.automate()
    def run(self):
        if self.automation_busy: return
        if not all([self.disc.get(),self.rips.get()]): messagebox.showerror("Missing folder","Please choose the disc source and ripped-MKV folder."); return
        if not Path(self.ffprobe.get()).is_file() and not shutil.which(self.ffprobe.get()): messagebox.showerror("ffprobe not found","Install FFmpeg, then choose ffprobe.exe."); return
        self.save_settings(); self.automation_busy=True; self.status.set("Detecting disc type and comparing programme timings to MKVs…")
        # Playlist parsing and ffprobe can take time on a full disc.  Keep it off
        # Tkinter's UI thread so the window remains usable while mapping runs.
        threading.Thread(target=self.worker,args=(None,False),daemon=True).start()
    def worker(self, prepared_scan=None, open_rename=False):
        try:
            # Automated runs reuse the pre-rip scan so mapping does not depend on
            # the disc still being mounted after MakeMKV has completed.
            rip_folder=self.active_rips if prepared_scan else self.rips.get()
            active_scan=prepared_scan or scan(self.disc.get())
            result=match_rips(active_scan,rip_folder,self.ffprobe.get(),database_record=self.prepared_database if prepared_scan else None,local_record=self.prepared_local if prepared_scan else None)
            # The disc title is useful context for manual review, but is never
            # treated as proof of an episode number.
            if prepared_scan:
                context=self.prepared_context
            else:
                series,season=disc_identity(active_scan.bdmv) if active_scan.disc_type == "blu-ray" else (None,None)
                context={"series":series,"season":season,"source":"Disc metadata title"} if series and season else None
            if context: result["disc_context"]=context
            subtitle_folder=self.subtitles.get().strip() if getattr(self,'subtitles',None) else ''
            if subtitle_folder:
                known={rip['path'] for e in result['episodes'] if verified_identity(e) for rip in e['matching_rips']}
                paths=[p['path'] for p in result.get('rip_inventory',[]) if p['path'] not in known]
                if paths:
                    try:
                        report=match_folder(rip_folder,subtitle_folder,self.ffprobe.get(),
                            lambda text:self.after(0,self.status.set,text),paths=paths,
                            expected_series=(context or {}).get('series') or (result.get('database_lookup') or {}).get('series'),
                            preferred_language=LANGUAGE_CODES[self.audio_language.get()])
                        apply_matches(result,report)
                    except (OSError,ValueError) as error:
                        result.setdefault('warnings',[]).append(f'Local subtitle matching unavailable: {error}')
            # Keep the audio inventory with each match so the saved report can
            # explain why same-duration MakeMKV files may be alternate versions.
            for programme in result["episodes"] + result.get("extras",[]):
                for matched_rip in programme["matching_rips"]:
                    matched_rip["audio_tracks"]=audio_tracks(self.ffprobe.get(),Path(matched_rip["path"]))
            destination=Path(rip_folder)
            add_proposed_names(result,self.convention.get(),self.library.get())
            out=destination/"disc-episode-mapping.json"
            text_out=destination/"disc-episode-mapping.txt"
            out.write_text(json.dumps(readable_timings(result),indent=2),encoding="utf-8")
            text_out.write_text(human_report(result),encoding="utf-8")
            self.last_result=result
            self.after(0,lambda:self.done(human_report(result),out,text_out,open_rename))
        except Exception as e: self.after(0,lambda detail=str(e):self.automation_failed("Mapping failed",detail))
    def done(self,text,path,text_path,open_rename=False):
        self.last_report_folder=path.parent
        self.last_report_stem=path.stem
        self.output.config(state="normal")
        if not open_rename: self.output.delete("1.0","end")
        self.output.insert("end",'\n'+text); self.output.config(state="disabled")
        self.status.set(f"Saved beside the rips: {path.name} and {text_path.name}")
        needs_manual=(self.last_result.get('unmatched_rips') or
                any(not verified_identity(e) or not e.get('matching_rips') for e in self.last_result['episodes']))
        watching=bool(open_rename and self.watch_new.get())
        auto_accept=bool(self.auto_accept.get()) if open_rename else False
        try:
            # A confirmation or manual review dialog blocks the watch loop's
            # busy guard. In watch mode, retain the report for later review.
            if open_rename and (not watching or auto_accept):
                self.rename(auto_confirm=auto_accept,show_summary=not needs_manual and not watching)
        finally:
            self.automation_busy=False
        if watching:
            if needs_manual or not auto_accept:
                self.status.set('Mapping saved. Review this disc later with Open saved mapping; watching for the next disc…')
        elif open_rename and needs_manual:
            self.manual_review()

    def save_mapping(self):
        folder=Path(self.last_report_folder)
        stem=getattr(self,'last_report_stem','disc-episode-mapping')
        (folder/(stem+'.json')).write_text(json.dumps(readable_timings(self.last_result),indent=2),encoding='utf-8')
        (folder/(stem+'.txt')).write_text(human_report(self.last_result),encoding='utf-8')

    def open_saved_mapping(self):
        if self.automation_busy: return
        path=filedialog.askopenfilename(title='Open a saved DiscSteward mapping',
            initialdir=self.rips.get() or str(ROOT),filetypes=[('Mapping report','*.json')])
        if not path: return
        try:
            result=json.loads(Path(path).read_text(encoding='utf-8'))
            if not isinstance(result,dict) or not isinstance(result.get('episodes'),list):
                raise ValueError('Choose disc-episode-mapping.json, not the prepared-disc file.')
            self.last_result=result; self.last_report_folder=Path(path).parent
            self.last_report_stem=Path(path).stem
            self.manual_review()
        except (OSError,ValueError,KeyError,TypeError) as error:
            messagebox.showerror('Cannot open mapping',str(error))

    def manual_review(self):
        if self.automation_busy: return
        if not self.last_result:
            self.open_saved_mapping(); return
        folder=getattr(self,'last_report_folder',None)
        if folder is None:
            messagebox.showinfo('Mapping needed','Make a mapping or open a saved mapping first.'); return
        # Pause new-disc jobs for the full review, including confirmation/moving.
        self.automation_busy=True
        try:
            window=ManualMatchWindow(self,self.last_result,folder,self.library.get(),self.ffprobe.get())
            self.wait_window(window)
            # Persist the optional starting number, including its assumption
            # label, so reopening a report retains the user's numbering choice.
            stem=getattr(self,'last_report_stem','disc-episode-mapping')
            (Path(folder)/(stem+'.json')).write_text(json.dumps(readable_timings(self.last_result),indent=2),encoding='utf-8')
            (Path(folder)/(stem+'.txt')).write_text(human_report(self.last_result),encoding='utf-8')
        except OSError as error:
            messagebox.showerror('Could not save updated mapping',str(error))
        finally:
            self.automation_busy=False

    def match_existing(self):
        if self.automation_busy: return
        if not self.subtitles.get().strip():
            messagebox.showinfo('Subtitle folder needed','Choose Local subtitle folder on the main page first.\n\n'+GUIDANCE); return
        if not Path(self.ffprobe.get()).is_file() and not shutil.which(self.ffprobe.get()):
            messagebox.showerror('ffprobe needed','Choose ffprobe.exe from FFmpeg on the main page.'); return
        self.automation_busy=True
        window=tk.Toplevel(self); window.title('Match existing or misnamed MKV files'); window.geometry('880x560')
        window.transient(self); window.grab_set()
        form=ttk.Frame(window,padding=12); form.pack(fill='x')
        self.row(form,'Video folder:',self.existing_files,True)
        ttk.Label(form,text='Show / series:').grid(row=1,column=0,sticky='w',pady=4)
        ttk.Entry(form,textvariable=self.existing_series).grid(row=1,column=1,sticky='ew',padx=8)
        ttk.Label(form,text='Leave blank only if your subtitle folder contains one series. Uses the main page’s subtitle and Plex library folders.',wraplength=810).grid(row=2,column=0,columnspan=3,sticky='w',pady=8)
        status=tk.StringVar(value='Choose the video folder and click Run matching. No disc is needed.')
        ttk.Label(window,textvariable=status,wraplength=820).pack(fill='x',padx=12)
        output=tk.Text(window,wrap='word',state='disabled'); output.pack(fill='both',expand=True,padx=12,pady=12)
        busy=False
        ready=False

        def close():
            if busy: return
            self.save_settings(); self.automation_busy=False; window.destroy()

        def complete(result,folder,error=None):
            nonlocal busy,ready
            busy=False; button.config(state='normal')
            if error:
                status.set(error); return
            self.last_result=result; self.last_report_folder=folder
            self.last_report_stem='subtitle-episode-mapping'
            ready=True
            move_button.config(state='normal'); rename_button.config(state='normal')
            text=human_report(result)
            output.config(state='normal'); output.delete('1.0','end'); output.insert('end',text); output.config(state='disabled')
            status.set('Mapping saved. Choose Rename and move, or Rename in current folder. Both show a preview before changing files.')
            self.status.set('Existing-file subtitle mapping ready. Use Preview and rename to review proposed names.')

        def work(folder,subtitles,probe,convention,library,series,language):
            try:
                report=match_folder(folder,subtitles,probe,lambda text:self.after(0,status.set,text),
                                    expected_series=series or None,preferred_language=language)
                result=standalone_result(report)
                add_proposed_names(result,convention,library)
                (folder/'subtitle-episode-mapping.json').write_text(json.dumps(readable_timings(result),indent=2),encoding='utf-8')
                (folder/'subtitle-episode-mapping.txt').write_text(human_report(result),encoding='utf-8')
                self.after(0,complete,result,folder)
            except Exception as error: self.after(0,complete,None,folder,str(error))

        def start():
            nonlocal busy,ready
            if busy: return
            if not self.existing_files.get().strip() or not Path(self.existing_files.get()).is_dir():
                status.set('Choose an existing video folder.'); return
            busy=True; ready=False; button.config(state='disabled')
            move_button.config(state='disabled'); rename_button.config(state='disabled'); self.save_settings()
            threading.Thread(target=work,args=(Path(self.existing_files.get()),self.subtitles.get(),self.ffprobe.get(),
                                             self.convention.get(),self.library.get(),self.existing_series.get().strip(),
                                             LANGUAGE_CODES[self.audio_language.get()]),daemon=True).start()

        def rename_files(in_place):
            nonlocal busy
            if busy or not ready: return
            busy=True
            try:
                self.rename(in_place=in_place,parent=window)
                output.config(state='normal'); output.delete('1.0','end')
                output.insert('end',human_report(self.last_result)); output.config(state='disabled')
                status.set(self.status.get())
            finally: busy=False

        buttons=ttk.Frame(form); buttons.grid(row=3,column=0,columnspan=3,sticky='w',pady=8)
        button=ttk.Button(buttons,text='Run matching',command=start); button.pack(side='left',padx=(0,8))
        move_button=ttk.Button(buttons,text='Rename and move…',command=lambda:rename_files(False),state='disabled'); move_button.pack(side='left',padx=(0,8))
        rename_button=ttk.Button(buttons,text='Rename in current folder…',command=lambda:rename_files(True),state='disabled'); rename_button.pack(side='left')
        window.protocol('WM_DELETE_WINDOW',close)

    def rename(self, auto_confirm=False, in_place=False, parent=None, show_summary=True):
        """Preview and, only after confirmation, move verified files for Plex.

        A move is deliberately used instead of copying so the Plex library gets
        the selected rip without silently leaving two large copies behind.
        """
        if not self.last_result: messagebox.showinfo("Make a mapping first","Click Make mapping before previewing a rename."); return
        if not in_place and not self.library.get().strip(): messagebox.showerror("Plex library folder needed","Choose the root folder of your Plex TV library first.",parent=parent); return
        self.save_settings()
        mode='rename in current folder' if in_place else 'rename and move'
        plans=[]; skipped=[]; target_fn=NAMING_CONVENTIONS[self.convention.get()]; preferred=LANGUAGE_CODES[self.audio_language.get()]
        for e in self.last_result["episodes"]:
            if not verified_identity(e): skipped.append(f"{e['playlist']}: episode number not verified; use manual review for programme-order suggestions"); continue
            previous=completed_renames(self.last_result,e)
            if any(r['mode']==mode and Path(r['destination']).is_file() for r in previous):
                skipped.append(f"{e['playlist']}: already renamed; see completed renames"); continue
            previous_paths={str(Path(r['destination']).resolve()) for r in previous if Path(r['destination']).is_file()}
            candidates=[r for r in e['matching_rips'] if str(Path(r['path']).resolve()) in previous_paths] if previous_paths else e['matching_rips']
            selected,reason=self.choose_rip(candidates,preferred)
            if not selected: skipped.append(f"{e['playlist']}: no matching file has {self.audio_language.get()} audio"); continue
            source=Path(selected["path"]); target=target_fn(self.library.get(),e)
            if in_place: target=source.parent/target.name
            if source.resolve()==target.resolve():
                skipped.append(f'{source.name}: already has the requested name and location'); continue
            plans.append((source,target,reason,e))
        preview="\n".join(f"{s.name} ({why})\n  → {t}" for s,t,why,_ in plans) or "No safe rename candidates."
        if skipped: preview += "\n\nNot renamed:\n" + "\n".join(skipped)
        if not plans:
            if not auto_confirm: messagebox.showinfo("Plex rename preview",preview,parent=parent or self)
            return
        # Manual runs require confirmation.  Automatic runs remain constrained to
        # the same verified, audio-matching, non-overwriting plan above.
        action='Rename these files in their current folders?' if in_place else 'Rename and move the listed files now?'
        if not auto_confirm and not messagebox.askyesno("Confirm Plex rename",preview + '\n\n'+action,parent=parent or self): return
        errors=[]; completed=[]; events=queue.Queue()
        was_busy=self.automation_busy; self.automation_busy=True
        progress=tk.Toplevel(parent or self); progress.title('Renaming files')
        progress.transient(parent or self); progress.grab_set()
        progress.protocol('WM_DELETE_WINDOW',lambda:None)
        progress_text=tk.StringVar(value='Preparing file operations…')
        ttk.Label(progress,textvariable=progress_text,padding=20,wraplength=620).pack()

        def move_files():
            try:
                for source,target,_,programme in plans:
                    events.put(f'{source.name}\n→ {target}')
                    try:
                        warning=record_rename(self.last_result,programme,source,target,mode,
                                              Path(self.last_report_folder)/'DiscSteward-renames.jsonl')
                        completed.append(self.last_result['rename_history'][-1])
                        if warning: errors.append(warning)
                        self.save_mapping()
                    except (OSError,ValueError) as error: errors.append(f"{source.name}: {error}")
            except Exception as error: errors.append(str(error))
            finally: events.put(None)

        def poll_moves():
            while True:
                try: event=events.get_nowait()
                except queue.Empty: break
                if event is None:
                    progress.destroy(); return
                progress_text.set(event)
            progress.after(50,poll_moves)

        threading.Thread(target=move_files,daemon=True).start()
        progress.after(50,poll_moves)
        try: self.wait_window(progress)
        finally:
            self.automation_busy=was_busy
            if parent and parent.winfo_exists(): parent.grab_set()
        self.status.set("Rename complete." if not errors else "Rename completed with errors; see report.")
        self.output.config(state="normal"); self.output.insert("end","\n\nPlex rename:\n"+preview+("\nErrors:\n"+"\n".join(errors) if errors else "")); self.output.config(state="disabled")
        if show_summary:
            messagebox.showinfo('Rename results',rename_summary(completed)+('\n\nErrors:\n'+'\n'.join(errors) if errors else ''),parent=parent or self)

    def choose_rip(self,candidates,preferred):
        """Choose one duration-matched rip using the user's audio preference.

        This is a deterministic tie-breaker, not evidence that either candidate
        is a different episode.  Unselected alternates stay in the rip folder.
        """
        choices=[]
        for item in candidates:
            path=Path(item["path"])
            if not path.is_file(): continue  # May have been moved during manual review.
            tracks=audio_tracks(self.ffprobe.get(),path)
            matching=[t for t in tracks if not preferred or t["language"]==preferred]
            if preferred and not matching: continue
            # A selected-language default track is preferred over merely having
            # that language.  File size is used only after the audio comparison.
            score=(100 if matching else 0)+(100 if any(t["default"] for t in matching) else 0)
            choices.append((score,path.stat().st_size,path,tracks))
        if not choices: return None,""
        score,size,path,tracks=max(choices,key=lambda x:(x[0],x[1],x[2].name))
        language=self.audio_language.get()
        reason=(f"selected for default {language} audio" if score>=200 else f"selected for {language} audio; larger duplicate used to break tie")
        return {"path":str(path)},reason

def human_report(result):
    def match_description(item):
        description=f"  {Path(item['path']).name}"
        if item.get("duration_source"):
            source={"video_track_tag":"video-track timing", "video_stream":"video-stream timing",
                    "container_fallback":"whole-file timing (video timing unavailable)"}.get(item["duration_source"],item["duration_source"])
            description += f" — {source}; difference {format_duration(item.get('difference_seconds'), difference=True)}"
        if item.get('duration_seconds') is not None:
            description += f"; length {format_duration(item['duration_seconds'])}"
        return description

    def audio_description(tracks):
        if not tracks: return "audio details unavailable"
        return ", ".join(f"{AUDIO_LANGUAGE_NAMES.get(track['language'],track['language'])}{' (default)' if track['default'] else ''}"
                         for track in tracks)

    def append_audio_comparison(lines, matches):
        """Describe alternate-rip audio only when the report has useful evidence."""
        if len(matches) < 2: return
        details=[(Path(item["path"]).name,audio_description(item.get("audio_tracks",[]))) for item in matches]
        known=[description for _,description in details if description != "audio details unavailable"]
        if not known: return
        heading="Audio tracks (same for all matching files):" if len(set(known)) == 1 else "Audio-track differences between matching files:"
        lines.append(heading)
        lines.extend(f"  {name}: {description}" for name,description in details)

    database=result.get("database_lookup") or {}
    context=result.get("disc_context") or {}
    context_line=(f"Disc metadata: {context['series']} — Season {context['season']} (helps manual review; does not verify episode numbers)"
                  if context.get("series") and context.get("season") else "Disc metadata: no show/season title available")
    lines=["DiscSteward — disc mapping", "=" * 26, f"Database check: {database.get('source','not used')} — {database.get('series') or database.get('status','no result')}", context_line, ""]
    if completed_renames(result):
        lines.extend(['Completed renames and moves',rename_summary(completed_renames(result)),''])
    for episode in result["episodes"]:
        label=(f"{episode['series']} — Season {episode['season']}, Episode {episode['episode']}: {episode.get('title','')}" if verified_identity(episode) else f"Programme {episode.get('programme_number',episode['episode'])} — episode number not verified")
        programme_kind=result.get("programme_kind","Blu-ray playlist")
        lines.extend([f"{label} — {format_duration(episode['duration_seconds'])}",
                      f"{programme_kind}: {episode['playlist']}",
                      f"Disc video segments: {' + '.join(episode['clips'])}",
                      f"Identification source: {episode['source']}",
                      f"{'Subtitle timing coverage' if episode['source'] == 'Local subtitle timing match' else 'Confidence'}: {episode['confidence']}%", "Ripped files:"])
        if episode["matching_rips"]:
            lines.extend(match_description(item) for item in episode["matching_rips"])
        else: lines.append("  No reliable MKV duration match found.")
        append_audio_comparison(lines,episode["matching_rips"])
        if not verified_identity(episode) and episode.get('suggested_episode') is not None:
            lines.append(f"Suggested episode: {episode['suggested_episode']} — {ASSUMPTION}; editable in manual review.")
        if verified_identity(episode) and episode.get('proposed_filename'):
            lines.extend(['Proposed filename (copy this):',episode['proposed_filename'],
                          'Library-relative path:',episode['proposed_relative_path'],
                          'Alternative matching files share this name; rename only one.'])
        else:
            lines.append('Proposed filename: unavailable — verified identity and a matching file are required.')
        lines.append("")
    if result.get("extras"):
        lines.extend(["Extras / non-episode programmes", "-------------------------------"])
        for extra in result["extras"]:
            lines.extend([f"{extra['playlist']} — {format_duration(extra['duration_seconds'])}",
                          f"Reason: {'; '.join(extra['reasons'])}", "Ripped files:"])
            if extra["matching_rips"]:
                lines.extend(match_description(item) for item in extra["matching_rips"])
            else: lines.append("  No reliable MKV duration match found.")
            append_audio_comparison(lines,extra["matching_rips"])
            lines.append("")
    if result["unmatched_rips"]:
        inventory = {item['path']:item for item in result.get('rip_inventory', [])}
        lines.extend(["Unmatched files", "---------------"] + [f"  {Path(path).name} — {format_duration(inventory.get(path, {}).get('duration_seconds'))}" for path in result["unmatched_rips"]])
    if result.get("warnings"):
        lines.extend(["Warnings", "--------"] + result["warnings"])
    if result.get('subtitle_matching'):
        lines.extend(['']+report_lines(result['subtitle_matching']))
    return "\n".join(lines) + "\n"

if __name__ == "__main__": App().mainloop()
