from __future__ import annotations
import argparse, json, sys
from pathlib import Path

# Also support direct execution from the Windows launcher, without requiring a
# Python package installation or PATH configuration.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from bluray_map.discovery import scan
    from bluray_map.analyze import classify
    from bluray_map.report import data, human, emit_csv
else:
    from .discovery import scan
    from .analyze import classify
    from .report import data, human, emit_csv

def _time(v):
    try:
        m,s=v.split(":"); return int(m)*60+int(s)
    except ValueError: return float(v)
def main(argv=None):
    ap=argparse.ArgumentParser(prog="discsteward")
    ap.add_argument("root"); ap.add_argument("--json", action="store_true"); ap.add_argument("--csv", action="store_true")
    ap.add_argument("--min-duration", type=_time, default=900); ap.add_argument("--max-duration", type=_time, default=1800)
    ap.add_argument("--include-extras", action="store_true"); ap.add_argument("--verbose", action="store_true"); ap.add_argument("--ffprobe", help="Path to ffprobe for optional stream enrichment")
    a=ap.parse_args(argv)
    if a.json and a.csv: ap.error("choose only one of --json or --csv")
    try: s=scan(a.root, a.ffprobe)
    except ValueError as e: ap.error(str(e))
    d=data(s, classify(s.playlists,a.min_duration,a.max_duration))
    if a.json:
        from .timeformat import readable_timings
        print(json.dumps(readable_timings(d), indent=2))
    elif a.csv: emit_csv(d)
    else: human(d)
if __name__ == "__main__": main()
