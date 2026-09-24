"""Language labels and ffprobe tag aliases shared by audio and subtitles."""

LANGUAGE_CODES = {
    'English': 'eng', 'French': 'fra', 'German': 'deu',
    'Spanish': 'spa', 'Italian': 'ita', 'Japanese': 'jpn',
    'No preference': '',
}

ALIASES = {
    'en': 'eng', 'english': 'eng',
    'fr': 'fra', 'fre': 'fra', 'french': 'fra',
    'de': 'deu', 'ger': 'deu', 'german': 'deu',
    'es': 'spa', 'spanish': 'spa',
    'it': 'ita', 'italian': 'ita',
    'ja': 'jpn', 'japanese': 'jpn',
}


def canonical_language(value):
    tag = str(value or 'und').strip().lower()
    return ALIASES.get(tag, tag)
