"""Bounded DVD title parsing: VMG titles -> VTS chapters -> programmes/cells.

This is not a DVD navigation VM. Multi-PGC, angle and dynamic navigation titles
are explicitly left unmatched, rather than treating every PGC as a title.
Table layouts: https://dvd.sourceforge.net/dvdinfo/ifo_vmg.html,
ifo_vts.html and pgc.html. Offsets are relative to their containing table.
"""
from pathlib import Path
from .models import Playlist, PlayItem


class DVDParseError(ValueError): pass


def _range(data, offset, size):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise DVDParseError('table or field outside IFO bounds')
    return data[offset:offset + size]


def _u16(data, offset): return int.from_bytes(_range(data, offset, 2), 'big')
def _u32(data, offset): return int.from_bytes(_range(data, offset, 4), 'big')


def _bcd(value):
    high, low = value >> 4, value & 15
    if high > 9 or low > 9: raise DVDParseError('invalid BCD playback time')
    return high * 10 + low


def dvd_time_seconds(raw):
    """The frame count is BCD too, after masking the two frame-rate bits."""
    if len(raw) != 4: raise DVDParseError('truncated DVD playback time')
    if raw == bytes(4): return 0.0  # Empty/dummy cell, not a running clock.
    hours, minutes, seconds = map(_bcd, raw[:3])
    frames = _bcd(raw[3] & 0x3f)
    rate = {1: 25, 3: 30000 / 1001}.get(raw[3] >> 6)
    if rate is None or minutes > 59 or seconds > 59 or frames >= rate:
        raise DVDParseError('invalid DVD playback time')
    return hours * 3600 + minutes * 60 + seconds + frames / rate


def _read(path, signature):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise DVDParseError('IFO exceeds parser size limit')
    data = path.read_bytes()
    if len(data) < 0xd0 or not data.startswith(signature):
        raise DVDParseError('missing DVD IFO signature/header')
    return data


def _table(data, pointer):
    sector = _u32(data, pointer)
    if not sector: raise DVDParseError('required title table is absent')
    start = sector * 2048
    size = _u32(data, start + 4) + 1
    if size < 8: raise DVDParseError('invalid title table length')
    return _range(data, start, size)


def _title(vts, ptt, pgcit, title_number):
    count = _u16(ptt, 0)
    if not 1 <= title_number <= count: raise DVDParseError('invalid VTS title reference')
    offsets = [_u32(ptt, 8 + n * 4) for n in range(count)] + [len(ptt)]
    if offsets[0] < 8 + count * 4 or any(b <= a for a, b in zip(offsets, offsets[1:])):
        raise DVDParseError('invalid chapter table offsets')
    chapters = _range(ptt, offsets[title_number-1], offsets[title_number]-offsets[title_number-1])
    if len(chapters) % 4: raise DVDParseError('truncated chapter reference')
    refs = [(_u16(chapters, n), _u16(chapters, n+2)) for n in range(0, len(chapters), 4)]
    if not refs or len({r[0] for r in refs}) != 1:
        raise DVDParseError('multi-PGC/empty title requires navigation; not automatically matched')
    pgcn = refs[0][0]; pgc_count = _u16(pgcit, 0)
    if not 1 <= pgcn <= pgc_count: raise DVDParseError('invalid PGC reference')
    starts = [_u32(pgcit, 12 + n * 8) for n in range(pgc_count)]
    if any(start < 8 + pgc_count * 8 or start >= len(pgcit) for start in starts):
        raise DVDParseError('invalid PGC offsets')
    start = starts[pgcn-1]
    end = min([x for x in starts if x > start] + [len(pgcit)])
    pgc = _range(pgcit, start, end-start)
    _range(pgc, 0, 0xec)
    programs, cells = pgc[2], pgc[3]
    if not programs or not cells: raise DVDParseError('empty title')
    if pgc[0xa2] or pgc[0xa3] or _u16(pgc, 0x9c):
        raise DVDParseError('still/random/linked PGC requires navigation; not automatically matched')
    command_offset = _u16(pgc, 0xe4)
    if command_offset:
        if command_offset < 0xec: raise DVDParseError('command table overlaps PGC header')
        pre, post, cell_commands = (_u16(pgc, command_offset+n) for n in (0,2,4))
        command_size = _u16(pgc, command_offset+6)+1
        if pre+post+cell_commands > 128 or command_size < 8+8*(pre+post+cell_commands):
            raise DVDParseError('invalid command table length')
        _range(pgc, command_offset, command_size)
        if pre or cell_commands:
            raise DVDParseError('pre/cell commands require navigation; not automatically matched')
    map_offset, playback_offset, position_offset = (_u16(pgc, x) for x in (0xe6, 0xe8, 0xea))
    if min(map_offset, playback_offset, position_offset) < 0xec:
        raise DVDParseError('cell/program table overlaps PGC header')
    program_map = list(_range(pgc, map_offset, programs))
    if (program_map[0] != 1 or program_map[-1] > cells or
            any(b <= a for a,b in zip(program_map, program_map[1:]))):
        raise DVDParseError('invalid programme-to-cell map')
    pgnums = [r[1] for r in refs]
    if not 1 <= pgnums[0] <= pgnums[-1] <= programs or any(b <= a for a,b in zip(pgnums, pgnums[1:])):
        raise DVDParseError('non-sequential chapter references; not automatically matched')
    playback = _range(pgc, playback_offset, cells * 24)
    positions = _range(pgc, position_offset, cells * 4)
    items = []
    # Chapters mark programme entry points, not the title's end. A simple
    # sequential title plays from its first chapter through the end of its PGC.
    for cell in range(program_map[pgnums[0]-1]-1, cells):
        entry = playback[cell*24:(cell+1)*24]
        if entry[0] & 0xf0 or entry[2] or entry[3]:
            raise DVDParseError('angle/still/cell-command playback unsupported; not automatically matched')
        duration = dvd_time_seconds(entry[4:8])
        first, last = _u32(entry, 8), _u32(entry, 20)
        if last < first: raise DVDParseError('invalid cell sector range')
        vob_id, cell_id = _u16(positions, cell*4), positions[cell*4+3]
        label = f'VTS_{vts:02}_VOB_{vob_id:02}_CELL_{cell_id:02}'
        # Sector bounds distinguish different cuts reusing the same cell IDs.
        items.append(PlayItem(label, label, first, last+1, duration))
    duration = sum(item.duration_seconds for item in items)
    if duration <= 0: raise DVDParseError('title has no timed video')
    return Playlist(f'VTS_{vts:02}_TITLE_{title_number:02}.IFO', title_number, items, duration,
                    streams={'vts':vts, 'vts_title':title_number, 'pgc':pgcn, 'chapters':len(refs)})


def parse_vts_ifo(path, title_number=None):
    """Read titles referenced by VTS_PTT_SRPT, never unreferenced PGCs."""
    path = Path(path); data = _read(path, b'DVDVIDEO-VTS')
    try: vts = int(path.name[4:6])
    except ValueError: raise DVDParseError('invalid VTS filename') from None
    ptt, pgcit = _table(data, 0xc8), _table(data, 0xcc)
    numbers = [title_number] if title_number is not None else range(1, _u16(ptt, 0)+1)
    return [_title(vts, ptt, pgcit, number) for number in numbers]


def parse_dvd_titles(video_ts):
    """Preserve VMG title order and report unsupported titles individually."""
    root = Path(video_ts); titles = []; warnings = []
    try:
        table = _table(_read(root/'VIDEO_TS.IFO', b'DVDVIDEO-VMG'), 0xc4)
        count = _u16(table, 0)
        if not 1 <= count <= 99: raise DVDParseError('invalid DVD title count')
        _range(table, 8, count*12)
    except (OSError, DVDParseError) as error:
        return [], [f'VIDEO_TS.IFO: {error}; no DVD titles automatically matched']
    for number in range(1, count+1):
        entry = _range(table, 8+(number-1)*12, 12)
        try:
            if entry[0] & 0x40 or entry[1] != 1:
                raise DVDParseError('non-sequential or multi-angle title requires navigation; not automatically matched')
            if not 1 <= entry[6] <= 99 or not entry[7]: raise DVDParseError('invalid title-set reference')
            title = parse_vts_ifo(root/f'VTS_{entry[6]:02}_0.IFO', entry[7])[0]
            if title.streams['chapters'] != _u16(entry, 2): raise DVDParseError('VMG/VTS chapter count mismatch')
            title.number = number
            title.filename = f'DVD_TITLE_{number:02}_{title.filename}'
            titles.append(title)
        except (OSError, DVDParseError) as error:
            warnings.append(f'DVD title {number}: {error}')
    return titles, warnings
