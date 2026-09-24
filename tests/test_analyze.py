from bluray_map.models import Playlist, PlayItem
from bluray_map.analyze import classify
def p(name, seconds, clip): return Playlist(name,int(name[:5]),[PlayItem(clip,clip+'.m2ts',0,int(seconds*45000),seconds)],seconds)
def test_episode_candidates_do_not_use_playlist_number():
    eps, _, _, alternates=classify([p('00842.mpls',1400,'00042'),p('00001.mpls',1410,'00043'),p('00002.mpls',60,'00001')])
    assert {x[0].filename for x in eps} == {'00842.mpls','00001.mpls'}
    assert not alternates

def test_long_outliers_are_extras_not_episode_candidates():
    playlists=[p('00019.mpls',2520,'00019'),p('00020.mpls',2486,'00020'),
               p('00021.mpls',2517,'00021'),p('00022.mpls',2552,'00022'),
               p('00254.mpls',1443,'00254'),p('00255.mpls',1739,'00255')]
    eps, extras, _, _=classify(playlists, minimum=900, maximum=7200)
    assert {x[0].filename for x in eps} == {'00019.mpls','00020.mpls','00021.mpls','00022.mpls'}
    assert {x[0].filename for x in extras} == {'00254.mpls','00255.mpls'}
