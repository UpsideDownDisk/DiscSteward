"""Persist actual file moves separately from proposed names and identity evidence."""
from datetime import datetime
import json
from pathlib import Path

from .manual import move_file_no_replace


def completed_renames(result, programme=None):
    records=[r for r in result.get('rename_history',[]) if r.get('status')=='completed']
    if programme is None: return records
    return [r for r in records if all(r.get(k)==programme.get(k) for k in ('playlist','series','season','episode'))]


def rename_summary(records):
    if not records: return 'No completed renames have been recorded for this mapping.'
    return '\n\n'.join(f"{r['original_path']}\n→ {r['destination']}\n({r.get('source','')}; {r.get('mode','rename and move')})" for r in records)


def record_rename(result, programme, source, target, mode, audit_path):
    """Log intent before the move, then update paths only after a successful move."""
    source=Path(source).resolve(); target=Path(target).resolve()
    record={k:programme.get(k) for k in ('playlist','series','season','episode','source')}
    record.update(original_path=str(source),destination=str(target),mode=mode,
                  time=datetime.now().isoformat(timespec='seconds'))
    def log(status):
        with Path(audit_path).open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({**record,'status':status},ensure_ascii=False)+'\n')
    log('planned')
    move_file_no_replace(source,target)
    record['status']='completed'
    result.setdefault('rename_history',[]).append(record)
    # Preserve original paths as provenance while making subsequent review and
    # saved reports describe where the files actually are now.
    items=list(result.get('rip_inventory',[]))+list(result.get('subtitle_matching',{}).get('files',[]))
    for item in result.get('episodes',[])+result.get('extras',[]):
        items.extend(item.get('matching_rips',[]))
    for item in items:
        if item.get('path') and Path(item['path']).resolve()==source:
            item.setdefault('original_path',item['path']); item['path']=str(target)
            item['rename_status']='completed'
    result['unmatched_rips']=[p for p in result.get('unmatched_rips',[]) if Path(p).resolve()!=source]
    try: log('completed')
    except OSError as error:
        return f'File renamed, but completion log could not be saved: {error}'
    return ''
