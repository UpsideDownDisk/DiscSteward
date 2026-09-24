"""Read-only TheDiscDb lookup using published Blu-ray and DVD identifiers."""
from __future__ import annotations
import hashlib, json, struct, urllib.request
from pathlib import Path

ENDPOINT="https://thediscdb.com/graphql/"
QUERY="""query($hash: String) { mediaItems(where: { releases: { some: { discs: { some: { contentHash: { eq: $hash } } } } } }) { nodes { title year type releases { slug title discs(order: { index: ASC }) { index name format contentHash titles(order: { index: ASC }) { sourceFile segmentMap duration item { title season episode type } } } } } } }"""

def _disc_folder(path):
    path = Path(path)
    for name in ("VIDEO_TS", "BDMV"):
        if (path / name).is_dir(): return path / name
    return path


def content_hash(bdmv: str | Path) -> str | None:
    root = _disc_folder(bdmv)
    try:
        # TheDiscDb hashes ALL direct VIDEO_TS files, including BUP and IFO;
        # Blu-rays use only STREAM/*.m2ts. Sizes are signed little-endian Int64.
        files = sorted((p for p in root.iterdir() if p.is_file()), key=lambda p:p.name) if root.name.upper() == 'VIDEO_TS' else sorted((root/'STREAM').glob('*.m2ts'), key=lambda p:p.name)
    except OSError: return None
    if not files: return None
    h=hashlib.md5()
    try:
        for f in files: h.update(struct.pack("<q",f.stat().st_size))
    except OSError: return None
    return h.hexdigest().upper()


def dvd_disc_id(path: str | Path) -> str | None:
    """libdvdread DVDDiscID: VMG IFO followed by at most nine VTS IFOs."""
    root = _disc_folder(path)
    try:
        vmg = (root/'VIDEO_TS.IFO').read_bytes()
        if len(vmg) < 64 or not vmg.startswith(b'DVDVIDEO-VMG'): return None
        count = int.from_bytes(vmg[62:64], 'big')
        if not 1 <= count <= 99: return None
        digest = hashlib.md5(vmg)
        for number in range(1, min(count, 9) + 1):
            file = root/f'VTS_{number:02}_0.IFO'
            if file.exists(): digest.update(file.read_bytes())
        return digest.hexdigest().upper()
    except OSError:
        return None


def _dvd_lookup(root, timeout):
    identifiers = [('discid', dvd_disc_id(root)), ('dischash', content_hash(root))]
    for kind, identifier in identifiers:
        if not identifier: continue
        url = f'https://thediscdb.com/api/{kind}/{identifier}'
        request = urllib.request.Request(url, headers={'Accept':'application/json', 'User-Agent':'DiscSteward/0.2'})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                records = json.loads(response.read())
        except Exception:
            continue
        if isinstance(records, dict): records = [records]
        if not isinstance(records, list): continue
        valid = []
        for record in records:
            if not isinstance(record, dict): continue
            field = 'global_disc_id' if kind == 'discid' else 'content_hash'
            if str(record.get(field) or '').upper() != identifier or str(record.get('format')).upper() != 'DVD': continue
            valid.append(record)
        if not valid: continue
        # One pressing may appear in multiple products. Conflicting episode
        # labels must not be resolved by whichever release the server returns first.
        def labels(record):
            return sorted((str(row.get('source_file')), json.dumps(row.get('item') or {}, sort_keys=True)) for row in record.get('titles', []))
        if any(labels(r) != labels(valid[0]) or r.get('media', {}).get('title') != valid[0].get('media', {}).get('title') for r in valid[1:]):
            return {'source':'TheDiscDb', 'status':'conflicting DVD release mappings; manual review required'}
        record = valid[0]
        return {'source':'TheDiscDb', 'url':url, 'disc_type':'dvd',
                'match_method':kind, 'content_hash':record.get('content_hash'),
                'global_disc_id':record.get('global_disc_id'),
                'series':record.get('media', {}).get('title'),
                'release':record.get('release', {}).get('title'),
                'disc':record.get('disc', {}).get('name'),
                'titles':[{'sourceFile':r.get('source_file'), 'duration':r.get('duration'),
                           'segmentMap':r.get('segment_map'), 'item':r.get('item')}
                          for r in record.get('titles', [])]}
    return None

def lookup(bdmv: str | Path, timeout: int=5) -> dict | None:
    """Return a matching disc record, or None. No filenames/paths are sent."""
    bdmv = _disc_folder(bdmv)
    if bdmv.name.upper() == 'VIDEO_TS': return _dvd_lookup(bdmv, timeout)
    disc_hash=content_hash(bdmv)
    if not disc_hash: return None
    payload=json.dumps({"query":QUERY,"variables":{"hash":disc_hash}}).encode()
    request=urllib.request.Request(ENDPOINT,data=payload,headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"bluray-map/0.1"})
    try:
        with urllib.request.urlopen(request,timeout=timeout) as response: data=json.loads(response.read())
    except Exception: return None
    for media in data.get("data",{}).get("mediaItems",{}).get("nodes",[]):
        for release in media.get("releases") or []:
            for disc in release.get("discs") or []:
                if (disc.get("contentHash") or "").upper()==disc_hash:
                    return {"source":"TheDiscDb","url":"https://thediscdb.com","content_hash":disc_hash,
                            "series":media.get("title"),"release":release.get("title") or release.get("slug"),
                            "disc":disc.get("name") or f"Disc {disc.get('index')}","titles":disc.get("titles") or []}
    return None

def assignments(record: dict | None, playlists) -> dict[str,dict]:
    """Join database rows to playlists by source file and clip/segment chain."""
    if not record: return {}
    if record.get('disc_type') == 'dvd':
        matched = {}
        for playlist in playlists:
            if not playlist.filename.startswith('DVD_TITLE_'): continue
            rows = [r for r in record.get('titles', []) if str(r.get('sourceFile') or '').isdigit()
                    and int(r['sourceFile']) == playlist.number]
            if len(rows) != 1: continue
            row = rows[0]; item = row.get('item') or {}
            try:
                h, m, s = map(float, row['duration'].split(':'))
                # Database runtimes are whole seconds, unlike parsed IFO timing.
                if abs(h*3600+m*60+s-playlist.duration_seconds) > 1: continue
                season, episode = int(item['season']), int(item['episode'])
                if season < 0 or episode < 1: continue
            except (KeyError, ValueError, TypeError, AttributeError): continue
            if not record.get('series'): continue
            matched[playlist.filename] = {'series':record['series'], 'season':season,
                'episode':episode, 'title':item.get('title') or '', 'source':'TheDiscDb',
                'source_url':record['url']}
        return matched
    rows={}
    for row in record.get("titles") or []:
        item=row.get("item") or {}
        if not item.get("season") or item.get("episode") is None: continue
        source=(row.get("sourceFile") or "").lower()
        segments=tuple(x.strip().zfill(5) for x in (row.get("segmentMap") or "").split(",") if x.strip())
        rows[(source,segments)]={"series":record["series"],"season":item["season"],"episode":item["episode"],"title":item.get("title") or "","source":"TheDiscDb","source_url":record["url"]}
    matched={}
    for playlist in playlists:
        key=(playlist.filename.lower(),tuple(item.clip_id for item in playlist.items))
        if key in rows: matched[playlist.filename]=rows[key]
    return matched
