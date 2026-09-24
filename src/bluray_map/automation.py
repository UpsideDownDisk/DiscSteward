"""DiscSteward helpers for per-disc destinations and MakeMKV progress."""
import csv
import hashlib
import ctypes
from ctypes import wintypes
import os
import queue
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PureWindowsPath
from .timeformat import format_duration


_GENERIC_DISC_NAMES = {
    'dvd', 'dvd disc', 'dvd video', 'dvd-video', 'video_ts', 'udf volume',
    'new volume', 'untitled', 'unknown', 'no disc',
}


def usable_disc_name(value):
    """Return a meaningful label, never MakeMKV/Windows' generic DVD label."""
    name = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not name or name.casefold() in _GENERIC_DISC_NAMES:
        return ''
    # A drive-letter fallback such as "DVD Disc F" is no more helpful than the
    # old DiscSteward fallback, so allow the timestamp path to handle it.
    if re.fullmatch(r'dvd\s+disc(?:\s+[a-z]:?)?', name, re.I):
        return ''
    return name


def makemkv_disc_name(executable, drive_index):
    """Read the current disc label from MakeMKV's documented DRV robot record.

    This is optional metadata: a timeout or an older MakeMKV version must never
    prevent a rip.  The sixth CSV value is the disc name; a seventh device-path
    value is used elsewhere only to bind the selected drive safely.
    """
    try:
        result = subprocess.run([executable, '--robot', '--cache=1', 'info', f'disc:{drive_index}'],
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired):
        return ''
    for line in result.stdout.splitlines():
        if not line.startswith('DRV:'):
            continue
        try:
            fields = next(csv.reader([line[4:]]))
            if int(fields[0]) == drive_index and len(fields) >= 6:
                return usable_disc_name(fields[5])
        except (csv.Error, IndexError, ValueError):
            continue
    return ''


def windows_volume_label(source):
    """Return a mounted optical volume's Windows label, if Windows exposes one."""
    drive = PureWindowsPath(source).drive
    if os.name != 'nt' or not re.fullmatch(r'[A-Za-z]:', drive):
        return ''
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        label = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        maximum_component_length = wintypes.DWORD()
        flags = wintypes.DWORD()
        kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                                  ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                                  ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetVolumeInformationW.restype = wintypes.BOOL
        if kernel.GetVolumeInformationW(drive + '\\', label, len(label), ctypes.byref(serial),
                                        ctypes.byref(maximum_component_length), ctypes.byref(flags), None, 0):
            return usable_disc_name(label.value)
    except (AttributeError, OSError):
        pass
    return ''


def resolve_rip_drive(executable, source):
    """Bind MakeMKV's current index to the selected optical root, never a guess.

    Older MakeMKV versions without device paths cannot be validated safely.
    Enumerating drives is read-only; disc:9999 is MakeMKV's documented listing.
    """
    path = PureWindowsPath(source)
    if (not re.fullmatch(r'[A-Za-z]:', path.drive) or not path.root
            or len(path.parts) > 2
            or (len(path.parts) == 2 and path.name.upper() not in ('BDMV', 'VIDEO_TS'))):
        raise ValueError('Automated ripping requires an optical drive root (for example F:/). '
                         'Use Make mapping for an extracted disc folder.')
    result = subprocess.run([executable, '--robot', '--cache=1', 'info', 'disc:9999'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # The deliberately out-of-range disc index can yield a nonzero exit after
    # printing a valid drive list. Trust only the explicit DRV device field.
    matches = set()
    for line in result.stdout.splitlines():
        if not line.startswith('DRV:'): continue
        try:
            fields = next(csv.reader([line[4:]]))
            if fields[6].rstrip('\\/').casefold() == path.drive.casefold():
                index = int(fields[0])
                if index >= 0: matches.add(index)
        except (csv.Error, IndexError, ValueError): pass
    if len(matches) != 1:
        raise ValueError(f'Cannot uniquely identify {path.drive} in MakeMKV. No ripping was started. '
                         'Check the source drive and update MakeMKV if its drive list lacks device paths.')
    return matches.pop()


def disc_fingerprint(source):
    """Small navigation-file snapshot detects removal/replacement during preparation."""
    root = Path(source)
    if root.name.upper() in ('BDMV', 'VIDEO_TS'): root = root.parent
    files = sorted((root/'BDMV'/'PLAYLIST').glob('*.mpls'))
    if not files: files = sorted((root/'VIDEO_TS').glob('*.IFO'))
    if not files: raise ValueError('No readable Blu-ray or DVD navigation files in the selected drive.')
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode('utf-8'))
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(65536), b''): digest.update(block)
    return digest.hexdigest()


class DiscChanged(RuntimeError):
    """The active disc disappeared or was replaced; return to insertion watch."""


def require_disc(source, expected):
    try:
        current = disc_fingerprint(source)
    except (OSError, ValueError) as error:
        raise DiscChanged('Disc removed or temporarily unreadable. Waiting for a readable disc.') from error
    if current != expected:
        raise DiscChanged('A different disc was inserted. Restarting preparation for that disc.')


def eject_disc(source):
    """Eject only the optical drive containing the captured rip source.

    Use Windows' IOCTL_STORAGE_EJECT_MEDIA after MakeMKV has released its
    handle. No volume dismount or forced unlocking is needed.
    https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ni-winioctl-ioctl_storage_eject_media
    """
    drive = PureWindowsPath(source).drive
    if os.name != 'nt' or not re.fullmatch(r'[A-Za-z]:', drive):
        raise ValueError('Automatic eject requires a Windows optical drive letter.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    if kernel.GetDriveTypeW(drive + '\\') != 5:  # DRIVE_CDROM
        raise ValueError(f'{drive} is not an optical drive; no eject command was sent.')
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW('\\\\.\\' + drive, 0x80000000, 3, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        returned = wintypes.DWORD()
        if not kernel.DeviceIoControl(handle, 0x2D4808, None, 0, None, 0, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)
    return drive


def run_rip_and_eject(command, log_path, notify, source, expected_fingerprint=None):
    """Map successful rips even if the disc was manually ejected afterward."""
    check = (lambda: require_disc(source, expected_fingerprint)) if expected_fingerprint else None
    drives = run_rip(command, log_path, notify, check_disc=check) if check else run_rip(command, log_path, notify)
    # Robot output identifies the actual MakeMKV drive when it supplies a
    # Windows device path. Older versions can omit that field.
    disc_id = next((int(arg[5:]) for arg in command if re.fullmatch(r'disc:\d+', arg)), None)
    if drives.get(disc_id) and PureWindowsPath(drives[disc_id]).drive.casefold() != PureWindowsPath(source).drive.casefold():
        raise RuntimeError('MakeMKV reported a different drive from the prepared source. '
                           'Automatic mapping and eject stopped; inspect the rip log before continuing.')
    eject_source = drives.get(disc_id) or source
    try:
        if check: check()  # Only eject when the original disc is still readable.
    except DiscChanged as error:
        message = f'MakeMKV completed. Automatic eject skipped: {error} Continuing with mapping.'
        ejected = False
    else:
        try:
            drive = eject_disc(eject_source)
            message = f'Disc ejected from {drive}. Continuing with mapping.'
            ejected = True
        except (OSError, ValueError) as error:
            message = f'Rip completed, but automatic eject failed: {error} You can eject it manually. Continuing with mapping.'
            ejected = False
    message = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}'
    notify(message)
    try:
        with Path(log_path).open('a', encoding='utf-8') as log: log.write(message + '\n')
    except OSError as error:
        notify(f'Could not save the eject message to the log: {error}')
    return ejected


def disc_folder_name(bdmv, reported_name='', timestamp=None):
    """Use disc metadata; DVDs prefer MakeMKV then their Windows volume label."""
    root = Path(bdmv)
    try:
        tree = ET.parse(root / 'META' / 'DL' / 'bdmt_eng.xml')
        name = next((node.text for node in tree.iter()
                     if node.tag.endswith('}name') and node.text), '')
    except (OSError, ET.ParseError):
        name = ''
    if not name:
        is_dvd = root.name.upper() == 'VIDEO_TS'
        if is_dvd:
            # DVD-Video has no bdmt XML. The two labels visible to the user are
            # more useful than a path/drive letter and do not infer a show name.
            name = usable_disc_name(reported_name) or windows_volume_label(root.drive)
        parent = root.parent
        if not name and parent.name:
            name = parent.name
        if not name:
            disc_type = 'DVD' if is_dvd else 'Blu-ray'
            stamp = timestamp or datetime.now()
            name = f'{stamp:%Y-%m-%d_%H-%M-%S} - {disc_type}'
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name).strip().rstrip('. ')[:120]
    if not name or name.upper().split('.')[0] in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        name = 'Disc ' + (name or 'unnamed')
    return name


def reserve_disc_folder(base, name):
    """Create a fresh run folder; a previous rip must never get mixed into it."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        destination = base / (name if index == 1 else f'{name} ({index})')
        try:
            destination.mkdir()
            return destination
        except FileExistsError:
            index += 1


class RipProgress:
    def __init__(self, start):
        self.start = start
        self.fraction = None
        self.last_progress = None

    def consume(self, line, now):
        if line.startswith('PRGV:'):
            try:
                _, total, maximum = map(int, line[5:].split(','))
                if maximum > 0 and 0 <= total <= maximum:
                    self.fraction = total / maximum
                    self.last_progress = now
            except ValueError:
                pass

    def describe(self, now):
        elapsed = max(0, now - self.start)
        percent = f'{100*self.fraction:.1f}%' if self.fraction is not None else 'waiting for progress'
        eta = 'calculating'
        # Estimate from overall progress, not the current title's progress.
        if self.fraction and elapsed >= 30 and now - self.last_progress < 300:
            remaining = elapsed * (1-self.fraction) / self.fraction
            eta = f'about {format_duration(round(remaining))}'
        if self.last_progress is not None and now - self.last_progress >= 300:
            eta = 'unavailable (no recent progress received)'
        return f'Ripping: {percent} overall | elapsed {format_duration(int(elapsed))} | remaining: {eta}'


def run_rip(command, log_path, notify, interval=300, check_disc=None):
    """Stream logs and messages; publish progress even when the process is quiet.

    notify receives timestamped plain text, and must dispatch onto the UI thread.
    MakeMKV MSG records and stderr are forwarded immediately, including errors.
    The full raw output is retained beside the rips for troubleshooting.
    """
    events = queue.Queue()
    started = time.monotonic()
    progress = RipProgress(started)
    drives = {}
    # Optical drives may briefly reject navigation reads while MakeMKV copies.
    # Verify once before launch, then use MakeMKV's result for rip completion.
    if check_disc: check_disc()

    def publish(text, log):
        message = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}'
        log.write(message + '\n'); log.flush()
        notify(message)

    with Path(log_path).open('w', encoding='utf-8') as log:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace', bufsize=1,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
            def read_output():
                try:
                    for line in process.stdout:
                        events.put(line.rstrip())
                finally:
                    events.put(None)
            reader = threading.Thread(target=read_output, daemon=True)
            reader.start()
            publish(progress.describe(started), log)
            next_update = started + interval
            first_progress = True
            while True:
                try:
                    line = events.get(timeout=max(0.01, min(1, next_update-time.monotonic())))
                except queue.Empty:
                    line = ''
                now = time.monotonic()
                if line is None: break
                if line:
                    log.write(line+'\n'); log.flush()
                    progress.consume(line, now)
                    if line.startswith('DRV:'):
                        try:
                            fields = next(csv.reader([line[4:]]))
                            if re.fullmatch(r'[A-Za-z]:[\\/]?', fields[6]):
                                drives[int(fields[0])] = fields[6]
                        except (csv.Error, IndexError, ValueError):
                            pass
                    if line.startswith('MSG:'):
                        try:
                            fields = next(csv.reader([line[4:]], escapechar='\\'))
                            publish(f'MakeMKV [{fields[0]}]: {fields[3]}', log)
                        except (csv.Error, IndexError):
                            publish(line, log)
                    elif not line.startswith(('PRG','DRV:','CINFO:','TINFO:','SINFO:','TCOUNT:')):
                        publish(line, log)
                if now >= next_update or (first_progress and progress.fraction is not None):
                    publish(progress.describe(now), log)
                    next_update = now + interval
                    first_progress = False
            code = process.wait()
            publish(f'MakeMKV finished (exit code {code}).', log)
            if code:
                raise RuntimeError(f'MakeMKV failed (exit code {code}). See the messages above and {log_path}')
    return drives
