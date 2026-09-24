"""Editable numbering assumptions must never silently become verified identity."""

ASSUMPTION = 'Episode numbering assumed from programme order'


def verified_identity(programme):
    # Subtitle evidence requires an explicit accepted file-level match. Old
    # menu/playlist-number correlations still cannot authorize a rename.
    evidence = (programme.get('source') == 'TheDiscDb' or
                (programme.get('source') == 'Local subtitle timing match'
                 and programme.get('subtitle_evidence', {}).get('accepted') is True))
    return (evidence
            and programme.get('identity_verified') is not False
            and bool(programme.get('series'))
            and programme.get('season') is not None
            and programme.get('episode') is not None)


def apply_episode_order(result, first_episode=1):
    first_episode = int(first_episode)
    if first_episode < 1:
        raise ValueError('First episode must be a positive whole number.')
    result['episode_numbering'] = {'first_episode': first_episode, 'source': ASSUMPTION}
    for number, programme in enumerate(result.get('episodes', []), 1):
        programme['programme_number'] = number
        programme['identity_verified'] = bool(verified_identity(programme))
        if not programme['identity_verified']:
            programme['suggested_episode'] = first_episode + number - 1
            programme['numbering_source'] = ASSUMPTION
    return result
