from pathlib import Path
from bluray_map.mpls import parse_mpls

def item(clip, start, end):
    body=clip.encode()+b"M2TS"+bytes([0,1,0])+start.to_bytes(4,'big')+end.to_bytes(4,'big')
    return len(body).to_bytes(2,'big')+body
def test_parses_playitems_and_durations(tmp_path):
    items=item('00042',0,45000*60)+item('00043',45000*60,45000*90)
    section=b'\0\0'+(2).to_bytes(2,'big')+b'\0\0'+items
    header=b'MPLS0200'+(20).to_bytes(4,'big')+b'\0'*8
    f=tmp_path/'00842.mpls'; f.write_bytes(header+len(section).to_bytes(4,'big')+section)
    p=parse_mpls(f)
    assert p.clip_chain == ['00042.m2ts','00043.m2ts']
    assert p.duration_seconds == 90
