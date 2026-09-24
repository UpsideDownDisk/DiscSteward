"""Fail-closed episode evidence from a disc's own BD-J menu resources."""
from __future__ import annotations
from pathlib import Path
import re, zipfile

EPISODE_LABEL=re.compile(r"(?:button[_-]?)?(?:episode|ep)(?:[_-]?disc\d+)?[_-]?0*(\d{1,3})(?:\D|$)",re.I)

def _labels(bdmv: Path) -> set[int]:
    """Find explicit episode-like labels in accessible BD-J resource names/text.

    JAR bytecode is not decompiled: only text files and archive member names are
    used, which keeps this fallback conservative and reproducible.
    """
    found=set(); jar=bdmv/"JAR"
    if not jar.is_dir(): return found
    def add(text): found.update(int(x) for x in EPISODE_LABEL.findall(text) if 0 < int(x) < 1000)
    for f in jar.rglob("*.txt"):
        try: add(f.name + "\n" + f.read_text(encoding="utf-8",errors="ignore"))
        except OSError: pass
    for f in jar.glob("*.jar"):
        try:
            with zipfile.ZipFile(f) as z:
                for name in z.namelist(): add(name)
        except (OSError,zipfile.BadZipFile): pass
    return found

def _disc_identity(bdmv: Path):
    try: text=(bdmv/"META"/"DL"/"bdmt_eng.xml").read_text(encoding="utf-8",errors="ignore")
    except OSError: return None,None
    # Different authoring tools use either ``<di:name>`` or an unprefixed
    # ``<name>`` element, so accept both without guessing at other metadata.
    name=re.search(r"<(?:[A-Za-z_][\w.-]*:)?name\b[^>]*>(.*?)</(?:[A-Za-z_][\w.-]*:)?name>",text,re.S)
    value=re.sub(r"\s+"," ",name.group(1)).strip() if name else ""
    match=re.match(r"^(.*?)(?:\s+(?:BD\s+)?Season\s*)(\d+)\b",value,re.I)
    if not match: return value or None,None
    return re.sub(r"\s+BD$","",match.group(1),flags=re.I).strip(),int(match.group(2))

def disc_identity(bdmv: str | Path):
    """Return the show and season named by the disc itself, if it has them.

    This is deliberately separate from :func:`local_assignments`: a disc title
    can help fill in the manual-review form without claiming that Programme 1
    is automatically Episode 1.
    """
    return _disc_identity(Path(bdmv))

def local_assignments(bdmv: str|Path, candidates) -> dict[str,dict]:
    """Legacy entry point: numeric label coincidence does not link menu to video.

    Retained for saved preparation records/callers. Programme-order suggestions
    are now separate from verified identity; title metadata still fills the UI.
    """
    return {}
