from __future__ import annotations
import csv, json, sys
from dataclasses import asdict
from .timeformat import format_duration

def data(scan, classified):
    eps, extras, unknown, alternates = classified
    return {"disc":{"path":scan.bdmv,"bdmv":scan.bdmv,"type":scan.disc_type}, "playlists":[asdict(p) for p in scan.playlists], "clips":scan.clips,
      "detected_episodes":[{"playlist":p.filename,"duration_seconds":p.duration_seconds,"clips":p.clip_chain,"confidence":round(s),"reasons":r} for p,s,r in eps],
      "extras":[{"playlist":p.filename,"confidence":round(s),"reasons":r} for p,s,r in extras],
      "unknown":[{"playlist":p.filename,"confidence":round(s),"reasons":r} for p,s,r in unknown], "alternate_playlists":alternates, "warnings":scan.warnings}

def human(d):
    kind="DVD" if d['disc'].get('type') == 'dvd' else "Blu-ray"
    print(f"{kind}: {d['disc']['path']}")
    print(f"Detected: Programmes: {len(d['playlists'])}  Video files: {len(d['clips'])}  Likely episodes: {len(d['detected_episodes'])}  Likely extras: {len(d['extras'])}")
    print("\nEPISODES")
    for i,e in enumerate(d['detected_episodes'],1):
        print(f"{i:>2}  {e['playlist']}  {format_duration(e['duration_seconds'])}  {' + '.join(e['clips'])}  {e['confidence']}%")
        print("    Reason: " + "; ".join(e['reasons']))
    if d['alternate_playlists']: print("\nALTERNATE PLAYLIST GROUPS\n" + "\n".join("  " + ", ".join(g) for g in d['alternate_playlists']))
    if d['warnings']: print("\nWARNINGS\n" + "\n".join("  " + w for w in d['warnings']))

def emit_csv(d, out=sys.stdout):
    w=csv.DictWriter(out, fieldnames=["episode","playlist","duration","duration_seconds","clips","confidence","reasons"]); w.writeheader()
    for i,e in enumerate(d['detected_episodes'],1): w.writerow({"episode":i,"playlist":e['playlist'],"duration":format_duration(e['duration_seconds']),"duration_seconds":e['duration_seconds'],"clips":" + ".join(e['clips']),"confidence":e['confidence'],"reasons":"; ".join(e['reasons'])})
