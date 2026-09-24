"""Subset MPLS parser: PlayItem clip references and 45 kHz in/out times.

Fields not safely decoded are deliberately omitted.  This parser follows the
playlist start address from the MPLS header, rather than assuming fixed layout.
"""
from __future__ import annotations
from pathlib import Path
from .models import Playlist, PlayItem

class ParseError(ValueError): pass

def _u16(b, p):
    if p + 2 > len(b): raise ParseError("truncated u16")
    return int.from_bytes(b[p:p+2], "big")
def _u32(b, p):
    if p + 4 > len(b): raise ParseError("truncated u32")
    return int.from_bytes(b[p:p+4], "big")

def parse_mpls(path: Path) -> Playlist:
    b = path.read_bytes()
    if len(b) < 20 or b[:4] != b"MPLS": raise ParseError("missing MPLS signature")
    playlist_start = _u32(b, 8)
    if playlist_start + 10 > len(b): raise ParseError("playlist section outside file")
    section_length = _u32(b, playlist_start)
    end = min(len(b), playlist_start + 4 + section_length)
    p = playlist_start + 4
    p += 2  # reserved
    count = _u16(b, p); p += 2
    p += 2  # subpath count
    items = []
    warnings = []
    for index in range(count):
        if p + 2 > end: raise ParseError(f"truncated before play item {index}")
        item_len = _u16(b, p); item_end = p + 2 + item_len
        if item_len < 20 or item_end > end: raise ParseError(f"invalid play item {index} length")
        q = p + 2
        clip = b[q:q+5].decode("ascii", "replace"); q += 5
        q += 4  # codec identifier
        # The flags are a 16-bit field (reserved, multi-angle and connection
        # condition), followed by the one-byte STC id.  Reading only one flag
        # byte shifts both timestamps and produces implausible multi-hour data.
        connection = b[q + 1] & 0x0F
        q += 2
        q += 1  # STC id
        in_time, out_time = _u32(b, q), _u32(b, q+4)
        if out_time < in_time: warnings.append(f"item {index}: end precedes start")
        duration = max(0, out_time - in_time) / 45000.0
        items.append(PlayItem(clip, clip + ".m2ts", in_time, out_time, duration, connection))
        p = item_end
    try: number = int(path.stem)
    except ValueError: number = -1
    return Playlist(path.name, number, items, sum(x.duration_seconds for x in items), warnings=warnings)
