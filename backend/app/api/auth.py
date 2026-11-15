"""Authentication API router: public auth config and the caller's identity.

Mounted in ``app.main`` WITHOUT the include-time ``auth_gate`` so that
``GET /api/auth/config`` stays public: the frontend needs the IdP
metadata before the caller has any token.  ``GET /api/auth/me``
protects itself with ``require_authenticated_user``.

The config response is limited to exactly seven public fields — it
never includes ``AUTH_DEV_TOKEN``, ``AUTH_DEV_ROLES`` or any other
secret, in any mode.
"""

from fastapi import APIRouter, Depends, Security
from fastapi.security import HTTPBearer
from pydantic import BaseModel

from app.auth import config as auth_config
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthConfigResponse(BaseModel):
    """The public authentication configuration served to the frontend.

    Exactly these fields — never ``AUTH_DEV_TOKEN``, ``AUTH_DEV_ROLES``
    or any other secret, in any mode.
    """

    authEnabled: bool
    authMode: str
    issuer: str
    clientId: str
    audience: str
    jwksUrl: str
    algorithms: list[str]


@router.get(
    "/config",
    response_model=AuthConfigResponse,
    # Documents the bearer scheme in the OpenAPI schema (auto_error=False
    # keeps the route public) — the same pairing the app's include-time
    # auth_gate uses for every other /api operation.
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="bearerAuth"))],
)
def get_auth_config() -> AuthConfigResponse:
    """Public auth configuration; no token required.

    The values are read from ``app.auth.config`` at request time, so a
    runtime change to the configuration is served on the next request.
    """
    return AuthConfigResponse(
        authEnabled=auth_config.AUTH_ENABLED,
        authMode=auth_config.AUTH_MODE,
        issuer=auth_config.AUTH_ISSUER,
        clientId=auth_config.AUTH_CLIENT_ID,
        audience=auth_config.AUTH_AUDIENCE,
        jwksUrl=auth_config.AUTH_JWKS_URL,
        algorithms=list(auth_config.AUTH_ALGORITHMS),
    )


@router.get(
    "/me",
    response_model=MeResponse,
    # Documents the bearer scheme in the OpenAPI schema, mirroring the
    # app's include-time auth_gate.
    dependencies=[Security(HTTPBearer(auto_error=False, scheme_name="bearerAuth"))],
)
def get_me(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> MeResponse:
    """The authenticated caller's identity, with normalised roles and groups."""
    return MeResponse(authenticated=True, user=current_user)
