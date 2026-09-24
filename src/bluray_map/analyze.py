from __future__ import annotations
from collections import defaultdict
from .models import Playlist

def classify(playlists: list[Playlist], minimum: float=900, maximum: float=1800):
    """Separate the dominant episode-duration cluster from longer extras.

    MakeMKV commonly rips every title above its duration threshold.  A playable
    24-minute featurette must not become an "episode" merely because it is long.
    Playlist numbers remain unrelated to episode identity.
    """
    in_range=[p for p in playlists if minimum <= p.duration_seconds <= maximum]
    # Find the duration neighbourhood with the most support.  Repeated episode
    # playlists (for alternate audio/chapter variants) reinforce that cluster.
    clusters=[([q for q in in_range if abs(q.duration_seconds-p.duration_seconds) <= max(90, p.duration_seconds*.12)],p)
              for p in in_range]
    members,anchor=max(clusters,key=lambda pair:len(pair[0]),default=([],None))
    cluster_size=len(members)
    def in_episode_cluster(playlist):
        return bool(anchor) and abs(playlist.duration_seconds-anchor.duration_seconds) <= max(90, anchor.duration_seconds*.12)
    results=[]; extras=[]; unknown=[]
    for p in playlists:
        reasons=[]; score=0
        if minimum <= p.duration_seconds <= maximum: score += 45; reasons.append("duration is in configured episode range")
        if cluster_size >= 2 and in_episode_cluster(p): score += 25; reasons.append("duration matches dominant episode cluster")
        if p.items: score += 10; reasons.append("has a playable clip chain")
        if p.duration_seconds < 300: extras.append((p, min(90, 55 + (300-p.duration_seconds)/10), ["very short playlist"])); continue
        if cluster_size >= 2 and minimum <= p.duration_seconds <= maximum and not in_episode_cluster(p):
            extras.append((p, 75, ["duration outside dominant episode cluster"])); continue
        if score >= 55: results.append((p, min(99, score), reasons))
        else: unknown.append((p, score, reasons or ["insufficient evidence"]))
    # Exact ordered cuts identify alternates without conflating different edits.
    groups=defaultdict(list)
    for p in playlists: groups[p.cut_signature].append(p.filename)
    alternates=[v for k,v in groups.items() if k and len(v)>1]
    # Present one representative for an exact clip-chain alternate group.  The
    # complete relationship remains in ``alternates``; this avoids claiming an
    # audio/chapter variant is another episode.
    representatives = {}
    for candidate in results:
        key = candidate[0].cut_signature or (candidate[0].filename,)
        old = representatives.get(key)
        if old is None or candidate[0].filename < old[0].filename:
            representatives[key] = candidate
    results = sorted(representatives.values(), key=lambda x: x[0].filename)
    return results, extras, unknown, alternates
