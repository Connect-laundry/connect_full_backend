"""Throttle rate parsing without Django/DRF imports (used by settings)."""
import re

_RATE_RE = re.compile(r'^\s*(\d+)\s*/\s*(\d*)\s*([smhd])[a-z]*\s*$', re.IGNORECASE)
_UNIT_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def parse_rate(rate):
    """'30/5m' -> (30, 300). Also accepts DRF forms: '10/minute', '100/hour'."""
    if rate is None:
        return (None, None)
    match = _RATE_RE.match(str(rate))
    if not match:
        raise ValueError(f'Invalid throttle rate {rate!r}; expected e.g. "30/5m" or "100/hour".')
    num, multiplier, unit = match.groups()
    num, multiplier = int(num), int(multiplier or 1)
    if num <= 0 or multiplier <= 0:
        raise ValueError('Throttle count and window must be positive.')
    return num, multiplier * _UNIT_SECONDS[unit.lower()]
