"""Match MakeMKV output to Blu-ray playlists or DVD title chains by timing."""
from __future__ import annotations
from pathlib import Path
import subprocess
import json
import math
from .analyze import classify
from .discdb import lookup as discdb_lookup, assignments as discdb_assignments
from .languages import canonical_language
from .episode_order import apply_episode_order

# ffprobe is launched once per MKV while mapping.  On Windows it is a console
# program, so explicitly suppress its otherwise momentary Command Prompt window.
HIDE_CONSOLE={"creationflags":getattr(subprocess,"CREATE_NO_WINDOW",0)}

def _seconds(value) -> float | None:
    """Accept seconds or an HH:MM:SS duration, rejecting missing/invalid data."""
    try:
        if isinstance(value, str) and ":" in value:
            hours, minutes, seconds = value.split(":")
            if int(hours) < 0 or not 0 <= int(minutes) < 60 or not 0 <= float(seconds) < 60:
                return None
            result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        else:
            result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def duration_details(ffprobe: str, path: Path) -> dict:
    """Prefer the main video duration; audio may extend the MKV container.

    MakeMKV stores video statistics as DURATION-eng (or DURATION). Keep the
    container duration for reporting, but do not try it as a second candidate
    when valid video timing exists: that can identify a different episode.
    """
    result = {"duration_seconds": None, "duration_source": "unavailable",
              "container_duration_seconds": None}
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=duration:stream_tags:format=duration",
                            "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=90, **HIDE_CONSOLE)
        if r.returncode: return result
        data = json.loads(r.stdout)
        result["container_duration_seconds"] = _seconds(data.get("format", {}).get("duration"))
        streams = data.get("streams") or []
        if not streams: return result
        video = streams[0]
        tags = {key.upper(): value for key, value in video.get("tags", {}).items()}
        options = [(tags.get("DURATION-ENG"), "video_track_tag"),
                   (tags.get("DURATION"), "video_track_tag"),
                   (video.get("duration"), "video_stream"),
                   (result["container_duration_seconds"], "container_fallback")]
        for value, source in options:
            seconds = _seconds(value)
            if seconds is not None:
                result.update(duration_seconds=seconds, duration_source=source)
                break
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        pass
    return result


def media_duration(ffprobe: str, path: Path) -> float | None:
    """Return the video duration, falling back to container timing if unavailable."""
    return duration_details(ffprobe, path)["duration_seconds"]

def audio_tracks(ffprobe: str, path: Path) -> list[dict]:
    """Return language/default details without decoding media."""
    try:
        r=subprocess.run([ffprobe,"-v","error","-select_streams","a","-show_entries",
                          "stream_tags=language:stream_disposition=default","-of","json",str(path)],
                         capture_output=True,text=True,timeout=90,**HIDE_CONSOLE)
        if r.returncode: return []
        return [{"language":canonical_language(s.get("tags",{}).get("language")),
                 "default":bool(s.get("disposition",{}).get("default"))}
                for s in json.loads(r.stdout).get("streams",[])]
    except (OSError,ValueError,subprocess.TimeoutExpired,json.JSONDecodeError): return []

def match_rips(scan, rip_folder: str | Path, ffprobe: str, minimum=900, maximum=7200, tolerance=0.05, database_record=None, local_record=None):
    """Match Blu-ray playlists or DVD IFO titles to MKVs, fail-closed.

    Duration establishes playlist-to-file relationships.  Series and episode
    Verified labels come from TheDiscDb. Programme-order numbers are separate,
    editable suggestions and never qualify for automatic verified renames.
    """
    candidates, extras, unknown, alternates = classify(scan.playlists, minimum, maximum)
    disc_type=getattr(scan,"disc_type","blu-ray")
    # Use the same narrow allowance for both formats: BCD frame timing is now
    # decoded correctly. Never relax to seconds merely because the disc is DVD.
    # The automated UI supplies this lookup before either disc type is ejected.
    database=database_record if database_record is not None else discdb_lookup(scan.bdmv)
    verified=discdb_assignments(database, scan.playlists)
    # A database answer takes precedence.  The pre-rip local result is used when
    # supplied, allowing an automated run to continue after disc ejection.
    # Old preparation records may contain numeric BD-J correlations. They are
    # not evidence of a menu-to-video link and must not grant automatic renames.
    files = sorted(Path(rip_folder).rglob("*.mkv"))
    measured = [(f, duration_details(ffprobe, f)) for f in files]
    warnings = list(scan.warnings)
    incomplete_dvd = disc_type == 'dvd' and not getattr(scan, 'title_scan_complete', True)
    if incomplete_dvd:
        warnings.append('DVD title scan incomplete: automatic duration matching disabled because '
                        'unresolved titles could have the same timing. Use manual review.')

    def signature(playlist):
        # Audio/language alternates share the same ordered clip cuts. Different
        # in/out points are different programmes even if their clip IDs agree.
        return playlist.cut_signature

    ambiguous = set()
    for f, timing in measured:
        duration = timing["duration_seconds"]
        if duration is None: continue
        possible = {signature(p) for p in scan.playlists
                    if abs(duration - p.duration_seconds) <= tolerance}
        if len(possible) > 1:
            ambiguous.add(f)
            warnings.append(f"{f.name}: duration matches different playlist clip sequences/cuts; not assigned")

    def matches_for(playlist):
        if incomplete_dvd or playlist.streams.get('matchable') is False: return []
        return [{"path": str(f), **timing,
                 "difference_seconds": round(abs(timing["duration_seconds"] - playlist.duration_seconds), 6)}
                for f, timing in measured
                if f not in ambiguous and timing["duration_seconds"] is not None
                and abs(timing["duration_seconds"] - playlist.duration_seconds) <= tolerance]

    episodes=[]
    for number, (playlist, confidence, reasons) in enumerate(candidates, 1):
        matches=matches_for(playlist)
        unverified=("DVD IFO title/PGC timing (episode number unverified)" if disc_type == "dvd"
                    else "Local Blu-ray analysis (episode number unverified)")
        identity=verified.get(playlist.filename) or {"source":unverified}
        episodes.append({"episode":number, "playlist":playlist.filename, "clips":playlist.clip_chain,
                         "duration_seconds":round(playlist.duration_seconds,3), "confidence":round(confidence),
                         "reasons":reasons, "matching_rips":matches, **identity})
    # Extras are reported separately so their MakeMKV files do not look like
    # mysterious failures, and so they cannot block episode identification.
    extra_programmes=[]
    for playlist, confidence, reasons in extras:
        matches=matches_for(playlist)
        extra_programmes.append({"playlist":playlist.filename, "clips":playlist.clip_chain,
                                "duration_seconds":round(playlist.duration_seconds,3), "confidence":round(confidence),
                                "reasons":reasons, "matching_rips":matches})
    recognised=episodes + extra_programmes
    matched_paths={match["path"] for item in recognised for match in item["matching_rips"]}
    unmatched=[str(f) for f,_ in measured if str(f) not in matched_paths]
    return apply_episode_order({"disc_type":disc_type,
            "programme_kind":"DVD title" if disc_type == "dvd" else "Blu-ray playlist",
            "database_lookup":database or {"source":"TheDiscDb","status":"no matching disc or lookup unavailable"},
            "rip_inventory":[{"path":str(f),**timing} for f,timing in measured],
            "episodes":episodes, "extras":extra_programmes, "unmatched_rips":unmatched, "alternate_playlists":alternates,
            "warnings":warnings})
