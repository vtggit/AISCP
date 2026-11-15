# FastAPI route dependencies for authentication
#
# These are the gatekeepers — every protected route should declare one of
# these as a dependency.  Route handlers never touch tokens directly.

import logging

from fastapi import Header, HTTPException, status

from app.auth.config import AUTH_ENABLED
from app.auth.models import AuthUser
from app.auth.security import validate_token
from app.observability.logging import get_request_id

logger = logging.getLogger(__name__)


def _req() -> str:
    """Return a request-ID suffix for log lines, or empty string."""
    rid = get_request_id()
    return f" request_id={rid}" if rid else ""


def _extract_bearer_token(authorization: str | None = None) -> str | None:
    """
    Pull the raw token string out of an ``Authorization: Bearer <token>``
    header.  Returns ``None`` when the header is absent or malformed.
    """
    if not authorization:
        logger.warning("auth: missing Authorization header%s", _req())
        return None
    parts = authorization.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    logger.warning("auth: malformed Authorization header%s", _req())
    return None


def get_current_user(
    authorization: str | None = Header(default=None),
) -> AuthUser | None:
    """
    Optional authentication dependency.

    Returns the ``AuthUser`` when a valid token is present, or ``None``
    when no token is supplied.  Useful for routes that *can* work
    anonymously but want to know who the caller is when authenticated.
    """
    if not AUTH_ENABLED:
        # Auth disabled — return a synthetic "anonymous" user
        return AuthUser(
            sub="anonymous",
            username="anonymous",
            roles=[],
            groups=[],
            raw_claims={"mode": "auth-disabled"},
        )

    token = _extract_bearer_token(authorization)
    if not token:
        return None

    user = validate_token(token)
    return user


def _unauthorized() -> HTTPException:
    """The single 401 response body used by the mandatory auth gate."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a valid Bearer token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_authenticated_user(
    authorization: str | None = Header(default=None),
) -> AuthUser:
    """
    Mandatory authentication dependency — fails closed.

    Extracts the Bearer token from the ``Authorization`` header itself
    and validates it with ``app.auth.security.validate_token``.  It
    deliberately does NOT build on ``get_current_user``: that dependency
    hands out a synthetic "anonymous" principal when ``AUTH_ENABLED`` is
    false, and this gate must never accept it.  Whatever the value of
    ``AUTH_ENABLED``, a missing header, a malformed header (anything but
    ``Bearer <token>``), or a token that ``validate_token`` rejects
    results in 401 with the ``WWW-Authenticate: Bearer`` header before
    the route handler is ever invoked.
    """
    token = _extract_bearer_token(authorization)
    if not token:
        logger.warning(
            "auth: unauthenticated access attempt — missing or malformed "
            "Authorization header%s",
            _req(),
        )
        raise _unauthorized()

    user = validate_token(token)
    if user is None:
        logger.warning(
            "auth: unauthenticated access attempt — token rejected by "
            "validate_token%s",
            _req(),
        )
        raise _unauthorized()
    return user
