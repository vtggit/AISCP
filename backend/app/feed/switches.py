"""Shared environment-switch parsing for the feed clients.

The feed clients gate optional behaviour — ``http://`` base URLs and
the write paths — on environment switches. ``env_switch`` answers, for
any such variable, whether it is enabled right now: the variable is
read at call time, so a process can flip a switch without re-importing,
and the answer never raises.

Enabled means the value, trimmed of surrounding whitespace and
lower-cased, is one of ``TRUE_VALUES`` — ``1``, ``true``, ``yes``, or
``on``. Unset, blank, or any other value is disabled.
"""

import os

__all__ = ["TRUE_VALUES", "env_switch"]

TRUE_VALUES = ("1", "true", "yes", "on")


def env_switch(name: str) -> bool:
    """Return True when the environment variable ``name`` enables a switch.

    Enabled only for ``1``, ``true``, ``yes``, or ``on``
    (case-insensitive, whitespace-trimmed); unset, blank, or any other
    value is disabled.
    """
    raw = os.environ.get(name)
    if raw is None:
        return False
    return raw.strip().lower() in TRUE_VALUES
