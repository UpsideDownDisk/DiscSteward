"""Explicit user decisions are recorded separately from automatic evidence."""
import json
import os
import re
import shutil
import errno
from datetime import datetime
from pathlib import Path


def valid_component(value):
    value=value.strip()
    if (not value or value in {'.','..'} or value.endswith(('.', ' '))
        or re.search(r'[<>:"/\\|?*\x00-\x1f]',value)
        or value.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}):
        raise ValueError('Use a valid Windows name without slashes or reserved characters.')
    return value


def manual_target(library, series, season, episode, title='', filename=''):
    if not library.strip(): raise ValueError('Choose a destination folder.')
    root=Path(library).resolve()
    if filename.strip():
        name=valid_component(filename)
        if not name.lower().endswith('.mkv'): name += '.mkv'
        return root/name
    series=valid_component(series)
    try: season=int(season); episode=int(episode)
    except (TypeError,ValueError): raise ValueError('Enter a season and episode number, or use a custom filename.')
    if season < 0 or episode < 1: raise ValueError('Season must be 0 or greater; episode must be 1 or greater.')
    title=(' - '+valid_component(title)) if title.strip() else ''
    return root/series/f'Season {season:02}'/f'{series} - s{season:02}e{episode:02}{title}.mkv'


def move_manual(source, target, audit_path, decision):
    """Move without overwriting. Persist the user's intent before touching video."""
    source=Path(source).resolve(); target=Path(target).resolve()
    if not source.is_file() or source.suffix.lower() != '.mkv': raise ValueError('Select an existing MKV file.')
    if source == target: raise ValueError('The file already has that name and location.')
    if target.exists(): raise FileExistsError(f'Destination already exists: {target}')
    record={**decision,'source':'Manual user assignment (not automatically verified)',
            'original_path':str(source),'destination':str(target),'time':datetime.now().isoformat(timespec='seconds')}
    def log(status):
        with Path(audit_path).open('a',encoding='utf-8') as f:
            f.write(json.dumps({**record,'status':status},ensure_ascii=False)+'\n')
    log('planned')
    move_file_no_replace(source, target)
    try: log('completed')
    except OSError as error:
        return f'File moved, but the completion log could not be written: {error}'
    return ''


def move_file_no_replace(source, target):
    """Move a single file without replacing anything, including across volumes."""
    source=Path(source).resolve(); target=Path(target).resolve()
    if not source.is_file(): raise FileNotFoundError(str(source))
    if source == target: raise ValueError('The file already has that name and location.')
    if target.exists(): raise FileExistsError(f'Destination already exists: {target}')
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        if os.name == 'nt':
            # Windows rename fails if the destination already exists.
            source.rename(target)
        else:
            os.link(source,target)
            source.unlink()
    except OSError as error:
        if error.errno != errno.EXDEV: raise
        # Cross-volume copy reserves the destination exclusively, then removes
        # the source only after a complete copy. Existing files cannot be replaced.
        with target.open('xb') as output:
            try:
                with source.open('rb') as input_file: shutil.copyfileobj(input_file,output,1024*1024)
            except Exception:
                output.close(); target.unlink(); raise
        source.unlink()
