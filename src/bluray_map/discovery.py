from __future__ import annotations
from pathlib import Path
from .models import Scan
from .mpls import parse_mpls, ParseError
from .dvdifo import parse_dvd_titles
from .probe import probe_file

def locate_bdmv(root: str | Path) -> Path:
    root = Path(root)
    candidates = [root, root / "BDMV"] if root.name.upper() != "BDMV" else [root]
    for p in candidates:
        if (p / "PLAYLIST").is_dir() and (p / "STREAM").is_dir(): return p
    raise ValueError(f"No usable BDMV directory below {root}")

def locate_video_ts(root: str | Path) -> Path:
    root = Path(root)
    candidates = [root, root / "VIDEO_TS"] if root.name.upper() != "VIDEO_TS" else [root]
    for p in candidates:
        if (p / "VIDEO_TS.IFO").is_file() and any(p.glob("VTS_*_0.IFO")):
            return p
    raise ValueError(f"No usable DVD VIDEO_TS directory below {root}")

def scan(root: str | Path, ffprobe: str | None = None) -> Scan:
    try:
        bdmv = locate_bdmv(root)
    except ValueError:
        try: return scan_dvd(root, ffprobe)
        except ValueError as error:
            raise ValueError(f"No supported disc structure below {root}; expected Blu-ray BDMV or DVD VIDEO_TS") from error
    warnings=[]; playlists=[]
    for f in sorted((bdmv / "PLAYLIST").glob("*.mpls")):
        try: playlists.append(parse_mpls(f))
        except (OSError, ParseError) as e: warnings.append(f"{f.name}: {e}")
    clips=[]
    clipinf = bdmv / "CLIPINF"
    for f in sorted((bdmv / "STREAM").glob("*.m2ts")):
        record={"filename": f.name, "clip_id": f.stem, "size_bytes": f.stat().st_size,
                "clpi_filename": f.stem + ".clpi" if (clipinf / (f.stem + ".clpi")).is_file() else None}
        if ffprobe:
            try: record["ffprobe"] = probe_file(ffprobe, f)
            except RuntimeError as e: warnings.append(f"{f.name}: ffprobe: {e}")
        clips.append(record)
    if not clipinf.is_dir(): warnings.append("CLIPINF directory absent; stream metadata unavailable")
    return Scan(str(bdmv), playlists, clips, warnings, "blu-ray")

def scan_dvd(root: str | Path, ffprobe: str | None = None) -> Scan:
    """Scan a DVD VIDEO_TS folder into title programme chains."""
    video_ts = locate_video_ts(root)
    playlists, warnings = parse_dvd_titles(video_ts)
    titles_complete = not warnings and bool(playlists)
    clips=[]
    for file in sorted(video_ts.glob("VTS_*_*.VOB")):
        record={"filename":file.name, "clip_id":file.stem, "size_bytes":file.stat().st_size}
        if ffprobe:
            try: record["ffprobe"] = probe_file(ffprobe, file)
            except RuntimeError as error: warnings.append(f"{file.name}: ffprobe: {error}")
        clips.append(record)
    if not playlists: warnings.append("No usable DVD title program chains found in VIDEO_TS")
    return Scan(str(video_ts), playlists, clips, warnings, "dvd", titles_complete)
