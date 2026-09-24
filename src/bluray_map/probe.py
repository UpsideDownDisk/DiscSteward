"""Optional, bounded ffprobe integration; ffprobe reads stream headers/indexes, not decoded frames."""
from __future__ import annotations
import json, subprocess
from pathlib import Path

def probe_file(program: str, media: Path) -> dict:
    cmd=[program, "-v", "error", "-show_entries", "format=duration:stream=index,id,codec_type,codec_name,codec_tag_string,width,height,avg_frame_rate,channels,channel_layout:stream_tags=language", "-of", "json", str(media)]
    try: p=subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as e: raise RuntimeError(str(e))
    if p.returncode: raise RuntimeError(p.stderr.strip() or f"exit {p.returncode}")
    try: raw=json.loads(p.stdout)
    except json.JSONDecodeError as e: raise RuntimeError(f"invalid JSON: {e.msg}")
    return {"duration_seconds": raw.get("format",{}).get("duration"), "streams": raw.get("streams", [])}
