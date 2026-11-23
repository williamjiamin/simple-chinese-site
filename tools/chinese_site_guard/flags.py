"""One default and a process-local emergency override for all adapters."""

import os

DEFAULT_ENABLED = True
KILL_SWITCH = "CHINESE_SITE_GUARD_DISABLED"


def is_enabled(requested: bool | None = None) -> bool:
    """Evaluate on every call; an explicit kill always wins a caller's ON value."""
    if requested is not None and type(requested) is not bool:
        raise TypeError("enabled must be a boolean or None")
    value = os.environ.get(KILL_SWITCH, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return False
    if value not in {"", "0", "false", "no", "off"}:
        raise ValueError(f"{KILL_SWITCH} must contain a boolean value")
    return DEFAULT_ENABLED if requested is None else requested
