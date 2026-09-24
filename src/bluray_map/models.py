from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import Any

@dataclass
class PlayItem:
    clip_id: str
    clip_filename: str
    in_time: int
    out_time: int
    duration_seconds: float
    connection_condition: int | None = None

@dataclass
class Playlist:
    filename: str
    number: int
    items: list[PlayItem]
    duration_seconds: float
    streams: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    @property
    def clip_chain(self): return [x.clip_filename for x in self.items]
    @property
    def cut_signature(self):
        """Ordered media ranges, not just filenames, identify an alternate."""
        return tuple((x.clip_id, x.in_time, x.out_time) for x in self.items)

@dataclass
class Scan:
    bdmv: str
    playlists: list[Playlist]
    clips: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    # ``bdmv`` is retained as the historic generic source path.  For a DVD it
    # contains the VIDEO_TS directory so existing callers remain compatible.
    disc_type: str = "blu-ray"
    # If some DVD titles cannot be resolved, unseen alternatives could share
    # the timing of a parsed title. Do not claim unique duration matches then.
    title_scan_complete: bool = True
    def to_dict(self): return asdict(self)
