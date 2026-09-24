"""Shared human-readable timing; never use rounded display values for matching."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def format_duration(seconds, *, difference=False, signed=False):
    """HH:MM:SS.mmm, with explicit sub-millisecond differences and unknowns."""
    try:
        value = Decimal(str(seconds))
        if not value.is_finite(): return 'Unknown'
        milliseconds = int((abs(value) * 1000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return 'Unknown'
    sign = '-' if value < 0 else '+' if signed else ''
    if difference and value != 0 and milliseconds == 0:
        return f'{sign}<00:00:00.001'
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    secs, millis = divmod(remainder, 1000)
    return f'{sign}{hours:02}:{minutes:02}:{secs:02}.{millis:03}'


def readable_timings(value):
    """Add readable companions in exports, preserving original numeric fields."""
    if isinstance(value, list): return [readable_timings(item) for item in value]
    if not isinstance(value, dict): return value
    result = {key: readable_timings(item) for key, item in value.items()}
    for key, item in value.items():
        if key.endswith('_seconds'):
            result[key[:-8] + '_display'] = format_duration(item, difference=key == 'difference_seconds', signed=key == 'offset_seconds')
    return result
