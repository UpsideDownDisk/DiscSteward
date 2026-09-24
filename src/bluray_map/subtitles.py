"""Local SRT/ZIP episode identification using the middle half of subtitle timing.

This is a timing fingerprint, not OCR or proof that a downloaded label is true.
Require agreement in BOTH middle quarters, substantially above incidental timing
overlap, and reject competing episode labels. No video filenames enter scoring.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile
import zlib

from .ripmatch import HIDE_CONSOLE
from .timeformat import format_duration
from .languages import canonical_language, LANGUAGE_CODES
from .episode_order import verified_identity
from .manual import move_file_no_replace

SOURCE = 'Local subtitle timing match'
GUIDANCE = ('Download subtitles in your preferred language for the show and season from OpenSubtitles.com '
            'or OpenSubtitles.org using your browser. For Blu-ray rips, prefer BDRip '
            'or BluRay releases; for DVD rips, prefer DVDRip. Include several versions '
            'if available, and subtitles for all possible episodes. Keep names such as '
            'Show.Name.S02E01.srt. Select their folder here; subfolders and ZIP files '
            'are supported. No API key is needed. Keep the full subtitle '
            'track in your preferred language selected in MakeMKV. Matching uses 25%–75% of each video. Weak '
            'or conflicting results require manual review. Subtitle filenames must '
            'include the series name. Successful references are moved into '
            '"previously used" and searched there only when fresh subtitles do not match. '
            'For a match inside a ZIP, the whole ZIP is moved so its other subtitles remain available.')
USED_FOLDER = 'previously used'
MAX_SRT = 5 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILES = 2000
TIMESTAMP = re.compile(r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->')
IDENTITY = re.compile(r'(?i)\bs(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)|\b(\d{1,2})x(\d{1,3})(?!\d)')


@dataclass
class Reference:
    origin: str
    series: str
    season: int
    episode: int
    times: list[float]
    digest: str
    container: str = ''

    @property
    def key(self):
        return (re.sub(r'\W+', '', self.series).casefold(), self.season, self.episode)


def parse_identity(name):
    """Use explicit SxxEyy labels only; reject multi-episode references."""
    stem = Path(name.replace('\\', '/')).stem.replace('_', '.')
    match = IDENTITY.search(stem)
    if not match:
        return None
    tail = stem[match.end():]
    if (re.match(r'(?i)[ ._-]*(?:e\d|s\d|\d+x\d)', tail)
            or re.match(r'\s*-\s*\d{1,3}(?![a-zA-Z0-9])', tail)):
        return None
    series = re.sub(r'[._]+', ' ', stem[:match.start()]).strip(' -')
    season, episode = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    if not series or int(episode) < 1: return None
    return series, int(season), int(episode)


def srt_times(data):
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16')
    else:
        try: text = data.decode('utf-8-sig')
        except UnicodeDecodeError: text = data.decode('cp1252', errors='replace')
    return sorted(set(int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
                      for h, m, s, ms in TIMESTAMP.findall(text)
                      if int(m) < 60 and int(s) < 60))


def series_key(value):
    return re.sub(r'[^\w]+', '', value.replace('_',' '), flags=re.UNICODE).casefold()


def load_references(folder, expected_series=None, scope='all', allow_empty=False):
    """Read bounded ZIP members in memory, never extract archive paths to disk."""
    root = Path(folder)
    if not root.is_dir(): raise ValueError('Choose an existing subtitle folder.')
    references, warnings, seen = [], [], set()
    total = count = 0

    def add(data, name, origin, container):
        nonlocal total, count
        total += len(data); count += 1
        if total > MAX_TOTAL or count > MAX_FILES:
            raise ValueError('Too many subtitles. Choose a smaller folder for this show/season.')
        identity = parse_identity(name)
        if not identity:
            warnings.append(f'{origin}: skipped; filename needs one show and SxxEyy label.')
            return
        if expected_series and series_key(identity[0]) != series_key(expected_series):
            warnings.append(f'{origin}: ignored; subtitle filename is not for {expected_series}.')
            return
        times = srt_times(data)
        if len(times) < 30:
            warnings.append(f'{origin}: too few valid subtitle entries.'); return
        digest = hashlib.sha256(data).hexdigest()
        key = (identity, digest)
        if key in seen: return
        seen.add(key)
        references.append(Reference(origin, *identity, times, digest, str(container)))

    for path in sorted(root.rglob('*')):
        if path.suffix.lower() not in ('.srt', '.zip') or not path.is_file(): continue
        used = any(part.casefold()==USED_FOLDER for part in path.relative_to(root).parts[:-1])
        if (scope=='active' and used) or (scope=='used' and not used): continue
        try:
            if path.suffix.lower() == '.srt':
                if path.stat().st_size > MAX_SRT:
                    warnings.append(f'{path}: subtitle exceeds size limit.'); continue
                add(path.read_bytes(), path.name, str(path), path)
            else:
                with zipfile.ZipFile(path) as archive:
                    members = archive.infolist()
                    if len(members) > MAX_FILES:
                        warnings.append(f'{path}: too many archive entries.'); continue
                    for member in members:
                        if member.is_dir() or not member.filename.lower().endswith('.srt'): continue
                        origin = f'{path} :: {member.filename}'
                        if member.file_size > MAX_SRT or member.flag_bits & 1:
                            warnings.append(f'{origin}: oversized or password-protected subtitle.'); continue
                        with archive.open(member) as stream:
                            data = stream.read(MAX_SRT + 1)
                        if len(data) > MAX_SRT:
                            warnings.append(f'{origin}: subtitle exceeds size limit.'); continue
                        add(data, member.filename, origin, path)
        except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError, UnicodeError, zlib.error) as error:
            warnings.append(f'{path}: could not read subtitle: {error}')
    if not references and not allow_empty:
        raise ValueError('No usable SRT subtitles found. Use filenames like Show.Name.S02E01.srt '
                         '(also inside ZIP files). ' + ' '.join(warnings[:3]))
    return references, warnings


def probe_json(command, timeout=180):
    process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=timeout, **HIDE_CONSOLE)
    if process.returncode: raise ValueError(process.stderr.strip()[-600:] or 'ffprobe could not read this file.')
    return json.loads(process.stdout)


def packet_bytes(dump):
    # ffprobe's hex dump has an address, 16 hex bytes, two spaces, then ASCII.
    result = bytearray()
    for line in dump.splitlines():
        if ':' not in line: continue
        hex_part = line.split(':', 1)[1].strip().split('  ', 1)[0].replace(' ', '')
        result.extend(bytes.fromhex(hex_part))
    return bytes(result)


def visible_pgs(data):
    """Presentation segments with objects are captions; empty ones clear them."""
    pos = 0
    while pos + 3 <= len(data):
        kind, size = data[pos], int.from_bytes(data[pos+1:pos+3], 'big')
        body = data[pos+3:pos+3+size]
        if kind == 0x16 and len(body) >= 11: return body[10] > 0
        pos += 3 + size
    return False


def embedded_timings(video, ffprobe, preferred_language='eng'):
    """Read subtitle packets from 25–75%; support PGS, DVD and text subtitles.

    ffprobe seeks to nearby keyframes, so explicitly discard out-of-window
    packets afterwards. PGS clear-screen packets must not become fake captions.
    """
    info = probe_json([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)], 90)
    from .ripmatch import _seconds
    video_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
    tags = {k.upper(): v for k, v in video_stream.get('tags', {}).items()}
    duration = next((v for value in (tags.get('DURATION-ENG'), tags.get('DURATION'),
                                    video_stream.get('duration'), info.get('format', {}).get('duration'))
                     if (v := _seconds(value)) is not None), None)
    if not duration: raise ValueError('Video duration is unavailable.')
    start = float(info.get('format', {}).get('start_time') or 0)
    lo, hi = start + duration/4, start + duration*3/4
    supported = {'hdmv_pgs_subtitle', 'dvd_subtitle', 'subrip', 'ass', 'ssa', 'webvtt', 'mov_text'}
    preferred_language = canonical_language(preferred_language) if preferred_language else ''
    streams = [s for s in info.get('streams', []) if s.get('codec_type') == 'subtitle'
               and s.get('codec_name') in supported
               and (not preferred_language or canonical_language(s.get('tags', {}).get('language')) == preferred_language)
               and not s.get('disposition', {}).get('forced')]
    if not streams:
        language = next((name for name, code in LANGUAGE_CODES.items() if code == preferred_language), preferred_language)
        if preferred_language:
            raise ValueError(f'No supported full {language} subtitle track. Keep that subtitle track when ripping in MakeMKV.')
        raise ValueError('No supported full subtitle track. Keep a full subtitle track when ripping in MakeMKV.')
    tracks = []
    for stream in streams:
        codec = stream['codec_name']
        command = [ffprobe, '-v', 'error', '-select_streams', str(stream['index']),
                   '-read_intervals', f'{lo:.3f}%{hi:.3f}', '-show_packets',
                   '-show_entries', 'packet=pts_time,data', '-of', 'json']
        if codec == 'hdmv_pgs_subtitle': command.append('-show_data')
        data = probe_json(command + [str(video)])
        times = []
        for packet in data.get('packets', []):
            try: timestamp = float(packet['pts_time'])
            except (KeyError, TypeError, ValueError): continue
            if not lo <= timestamp < hi: continue
            if codec == 'hdmv_pgs_subtitle' and not visible_pgs(packet_bytes(packet.get('data', ''))): continue
            times.append(timestamp - start)
        tracks.append({'stream_index': stream['index'], 'codec': codec, 'times': sorted(set(times))})
    return duration, tracks


def aligned_hits(events, reference, offset, tolerance=.35):
    """One-to-one matching prevents dense subtitle sets inflating the score."""
    hits, i = [], 0
    for event in events:
        while i < len(reference) and reference[i] + offset < event - tolerance: i += 1
        if i < len(reference) and abs(reference[i] + offset - event) <= tolerance:
            hits.append(event); i += 1
    return hits


def compare_times(events, reference, duration):
    lo, mid, hi = duration/4, duration/2, duration*3/4
    events = sorted(set(t for t in events if lo <= t < hi))
    halves = [sum(t < mid for t in events), sum(t >= mid for t in events)]
    empty = {'coverage': 0.0, 'quarter_coverage': [0.0, 0.0], 'matched_events': 0,
             'events': len(events), 'offset_seconds': 0, 'speed_factor': 1, 'background_coverage': 0, 'strong': False}
    if min(halves) < 30: return empty
    best = empty
    # Common PAL/film speed differences, plus equal-speed Blu-ray/DVD releases.
    for scale in (1.0, 25/24, 25/(24000/1001), 24/25, (24000/1001)/25):
        ref = [t*scale for t in reference]
        offsets = Counter()
        for event in events:
            for t in ref[bisect_left(ref, event-120):bisect_right(ref, event+120)]:
                offsets[round((event-t)*10)/10] += 1
        for offset, _ in offsets.most_common(5):
            hits = aligned_hits(events, ref, offset)
            coverage = len(hits)/len(events)
            quarters = [sum(t < mid for t in hits)/halves[0], sum(t >= mid for t in hits)/halves[1]]
            if coverage <= best['coverage']: continue
            background = sum(len(aligned_hits(events, ref, offset+shift))/len(events)
                             for shift in (-53, -37, -19, 19, 37, 53))/6
            best = {'coverage': round(coverage, 4), 'quarter_coverage': [round(q, 4) for q in quarters],
                    'matched_events': len(hits), 'events': len(events), 'offset_seconds': offset,
                    'speed_factor': round(scale, 7), 'background_coverage': round(background, 4),
                    'strong': coverage >= .75 and min(quarters) >= .65 and coverage-background >= .25}
    return best


def match_video(video, ffprobe, references, preferred_language='eng'):
    result = {'path': str(video), 'accepted': False, 'method': 'Subtitle timing fingerprint; middle two quarters (25–75%)'}
    try:
        duration, tracks = embedded_timings(video, ffprobe, preferred_language)
        result['duration_seconds'] = duration
        ranked = []
        for reference in references:
            scores = [dict(compare_times(track['times'], reference.times, duration),
                           stream_index=track['stream_index'], codec=track['codec']) for track in tracks]
            best = max(scores, key=lambda s: s['coverage'])
            ranked.append({**best, 'series': reference.series, 'season': reference.season, 'episode': reference.episode,
                           'subtitle': reference.origin, 'subtitle_sha256': reference.digest,
                           'subtitle_container': reference.container, '_key': reference.key})
        ranked.sort(key=lambda s: s['coverage'], reverse=True)
        if not ranked: raise ValueError('No subtitle references available.')
        winner = ranked[0]
        runner = next((s for s in ranked[1:] if s['_key'] != winner['_key']), None)
        margin = winner['coverage'] - runner['coverage'] if runner else None
        accepted = winner['strong'] and (runner is None or (margin >= .12 and not runner['strong']))
        for item in ranked: item.pop('_key')
        result.update(accepted=accepted, best=winner, candidates=ranked,
                      different_episode_margin=round(margin, 4) if margin is not None else None,
                      reason=('Strong timing agreement in both middle quarters.' if accepted else
                              'Weak or conflicting subtitle timing; manual review required.'))
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        result['reason'] = str(error)
    return result


def subtitle_identity(match):
    best = match['best']
    return {key: best[key] for key in ('series', 'season', 'episode')}


def archive_references(report, subtitle_folder):
    """Move successful source files once, after the batch has finished reading.

    ZIPs stay intact. Their archive member names are never used as filesystem
    paths. Keep the original reference and record its new location in the report.
    """
    root=Path(subtitle_folder).resolve()
    moved={}
    for match in report['files']:
        if not match.get('accepted'): continue
        best=match['best']; container=best.get('subtitle_container')
        if not container: continue
        source=Path(container).resolve()
        if source in moved: continue
        try:
            relative=source.relative_to(root)
            if any(part.casefold()==USED_FOLDER for part in relative.parts[:-1]): continue
            if source.suffix.lower() not in ('.srt','.zip'): continue
            target=root/USED_FOLDER/relative
            original_target=target; number=2
            while True:
                try:
                    move_file_no_replace(source,target)
                    break
                except FileExistsError:
                    target=original_target.with_name(f'{original_target.stem} ({number}){original_target.suffix}')
                    number+=1
            moved[source]=target
        except (OSError,ValueError) as error:
            report['warnings'].append(f'Subtitle matched but could not be moved to "previously used": {source}: {error}')
    report['archived_subtitles']=[{'original_path':str(source),'destination':str(target)} for source,target in moved.items()]
    for match in report['files']:
        for candidate in match.get('candidates',[]):
            container=candidate.get('subtitle_container')
            if container and Path(container).resolve() in moved:
                target=moved[Path(container).resolve()]
                member=candidate['subtitle'].partition(' :: ')[2]
                candidate['subtitle_current_location']=str(target)+(f' :: {member}' if member else '')


def match_folder(folder, subtitle_folder, ffprobe, notify=lambda message: None, paths=None,
                 expected_series=None, preferred_language='eng'):
    references, warnings = load_references(subtitle_folder,expected_series,scope='active',allow_empty=True)
    used=None
    if not references:
        used,more=load_references(subtitle_folder,expected_series,scope='used',allow_empty=True)
        warnings.extend(more)
        if not used:
            raise ValueError('No usable subtitles for this series. The SRT filename must include the show and SxxEyy, '
                             'for example Nikita.S02E19.srt. '+ ' '.join(warnings[:3]))
    if not expected_series:
        names={series_key(ref.series):ref.series for ref in references+(used or [])}
        if len(names)>1:
            raise ValueError('Subtitles contain multiple series. Enter the show / series in Match existing files, '
                             'or select a subtitle folder containing just this show.')
        expected_series=next(iter(names.values()))
    videos = sorted(Path(folder).rglob('*')) if paths is None else [Path(p) for p in paths]
    videos = [p for p in videos if p.suffix.lower() == '.mkv' and p.is_file()]
    if not videos: raise ValueError('No MKV files found in the selected video folder.')
    matches = []
    for number, video in enumerate(videos, 1):
        notify(f'Subtitle matching {number}/{len(videos)}: {video.name} (middle 25–75%)')
        match=match_video(video, ffprobe, references, preferred_language) if references else {'accepted':False}
        match['search_stage']='subtitle folder'
        if not match['accepted'] and used is None:
            used,more=load_references(subtitle_folder,expected_series,scope='used',allow_empty=True)
            warnings.extend(more)
        if not match['accepted'] and used:
            notify(f'Checking previously used subtitles: {video.name}')
            # Keep fresh candidates when checking fallback conflicts.
            match=match_video(video,ffprobe,references+used,preferred_language)
            match['search_stage']='previously used fallback (including fresh candidates)'
        matches.append(match)
    report = {'source': SOURCE, 'subtitle_folder': str(subtitle_folder), 'reference_count': len(references)+len(used or []),
            'expected_series':expected_series, 'preferred_language':preferred_language,
            'note': 'Episode labels come from local subtitle filenames; timing matches are not independent catalogue verification.',
            'files': matches, 'warnings': warnings}
    archive_references(report,subtitle_folder)
    return report


def apply_matches(result, report):
    """Attach file-level evidence. Never spread one file's identity to alternates."""
    result['subtitle_matching'] = report
    by_path = {m['path']: m for m in report['files']}
    added, used = [], set()
    for programme in result['episodes'] + result.get('extras', []):
        if verified_identity(programme): continue
        remaining = []
        for rip in programme['matching_rips']:
            match = by_path.get(rip['path'])
            if not match or not match['accepted']:
                remaining.append(rip); continue
            added.append({**programme, **subtitle_identity(match), 'matching_rips': [rip],
                          'source': SOURCE, 'identity_verified': True, 'subtitle_evidence': match,
                          'confidence': round(match['best']['coverage']*100), 'title': ''})
            used.add(rip['path'])
        programme['matching_rips'] = remaining
    for match in report['files']:
        if not match['accepted'] or match['path'] in used or match['path'] not in result['unmatched_rips']: continue
        added.append({'playlist': 'File identified by subtitles', 'clips': [], 'duration_seconds': match['duration_seconds'],
                      **subtitle_identity(match), 'matching_rips': [{'path': match['path']}], 'title': '',
                      'source': SOURCE, 'identity_verified': True, 'subtitle_evidence': match,
                      'confidence': round(match['best']['coverage']*100), 'reasons': []})
        used.add(match['path'])
    # Remove an unlabelled programme only if all its original rips were identified.
    replaced = {e['playlist'] for e in added}
    result['episodes'] = [e for e in result['episodes'] if e['matching_rips'] or e['playlist'] not in replaced]
    result['extras'] = [e for e in result.get('extras', []) if e['matching_rips'] or e['playlist'] not in replaced]
    # Group only independently accepted files for the same playlist/identity.
    grouped = {}
    for episode in added:
        key = (episode['playlist'], episode['series'].casefold(), episode['season'], episode['episode'])
        if key in grouped:
            grouped[key]['matching_rips'].extend(episode['matching_rips'])
        else: grouped[key] = episode
    result['episodes'].extend(grouped.values())
    result['unmatched_rips'] = [p for p in result['unmatched_rips'] if p not in used]


def standalone_result(report):
    result = {'episodes': [], 'extras': [], 'unmatched_rips': [m['path'] for m in report['files']],
              'programme_kind': 'Existing video', 'warnings': [],
              'database_lookup': {'source': SOURCE, 'status': 'Local files; no disc required'}}
    apply_matches(result, report)
    return result


def report_lines(report):
    lines = ['Local subtitle matching — middle two quarters (25–75%)', report.get('note', '')]
    for item in report.get('files', []):
        lines.append(f"{Path(item['path']).name}: {'MATCH' if item['accepted'] else 'UNRESOLVED'} — {item['reason']}")
        best = item.get('best')
        if best:
            lines.append(f"  {'Matched' if item['accepted'] else 'Best candidate only'}: {best['series']} S{best['season']:02}E{best['episode']:02}")
            lines.append(f"  Subtitle: {best['subtitle']}")
            if best.get('subtitle_current_location'):
                lines.append(f"  Subtitle now stored at: {best['subtitle_current_location']}")
            lines.append(f"  Timing coverage: {best['coverage']:.1%}; quarters {best['quarter_coverage'][0]:.1%} / {best['quarter_coverage'][1]:.1%}; "
                         f"offset {format_duration(best['offset_seconds'], signed=True)}; speed factor {best['speed_factor']:.5f}")
    if report.get('archived_subtitles'):
        lines.append('Successfully matched subtitles moved to previously used:')
        lines.extend(f"  {item['original_path']} → {item['destination']}" for item in report['archived_subtitles'])
    lines.extend(report.get('warnings', []))
    return lines
