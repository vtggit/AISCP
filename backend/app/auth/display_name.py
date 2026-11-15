# Display-name resolution for audit columns
#
# The audit columns (decided_by, updated_by, changed_by) store the token
# subject (sub), which is a machine identifier.  The display-name
# columns beside them store a human-readable name resolved from the
# same token: the username when it is non-empty, else the email when it
# is non-empty, else the sub.  The result is truncated to the length of
# the column it will be stored in, so a hostile token can never push a
# value past the column's width (and never a 500).

from app.auth.models import AuthUser

# The length of every display-name column added by the
# 0012_display_names migration (the subject columns beside them are
# String(255) as well).
MAX_DISPLAY_NAME_LENGTH = 255


def display_name(user: AuthUser, max_length: int = MAX_DISPLAY_NAME_LENGTH) -> str:
    """The caller's display name, truncated to ``max_length``.

    The token's ``username`` when it is non-empty, else its ``email``
    when it is non-empty, else its ``sub``.  ``None`` and empty strings
    are treated as absent; the winner is truncated to ``max_length``
    characters.
    """
    name = user.username or user.email or user.sub
    return name[:max_length]
