"""Release notification policy, independent of the presentation and network check."""

import re


def release_version(value):
    """Compare numeric public releases, including 4.10 versus 4.9 and trailing zeros."""
    if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)*", value):
        return None
    parts = [int(part) for part in value.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def should_notify_update(version, current_version, dismissed_version):
    available = release_version(version)
    current = release_version(current_version)
    dismissed = release_version(dismissed_version)
    return bool(available is not None and current is not None and available > current
                and (dismissed is None or available > dismissed))
