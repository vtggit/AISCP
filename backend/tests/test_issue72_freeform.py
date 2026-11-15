"""Issue #72 proof: auth configuration is read lazily at request time.

Before the fix, ``app.auth.security`` bound ``AUTH_MODE`` and
``AUTH_DEV_TOKEN`` from ``app.auth.config`` at import time, so flipping
the configuration at runtime (e.g. ``AUTH_MODE`` from development to
production) had no effect until the auth modules were re-imported — a
dev bearer token kept being accepted in "production".

This test proves both acceptance criteria against the served app
(``app.main.app``) over real requests, without re-importing any auth
module and without restarting the process:

* AC-1 — ``AUTH_MODE`` and ``AUTH_DEV_TOKEN`` are read from a lazily
  evaluated config source / module-level accessor, so runtime changes
  are observable without re-importing the auth module.
* AC-2 — changing ``AUTH_MODE`` from dev to prod at runtime makes a
  bearer token that was accepted under dev mode rejected on the very
  next request.

An independent review additionally confirmed that
``AUTH_JWKS_CACHE_TTL=0`` raised a ``ZeroDivisionError`` in the JWKS
cache bucketing, which made ``validate_token`` reject *every* JWT in
production mode.  The end of the test proves both sides of that fix:
with the TTL flipped to 0 at runtime the JWKS lookup no longer crashes
and a validly signed JWT is still accepted (while a JWT signed with the
wrong key is still rejected).
"""

import time

from fastapi.testclient import TestClient

from app import main as app_main
from app.auth import config as auth_config
from app.auth import security

DEV_TOKEN = "dev-secret-token"
ROTATED_TOKEN = "rotated-dev-token"
PROTECTED_PATH = "/api/inventory-items"
AUTH_DETAIL = "Authentication required. Provide a valid Bearer token."


def _unauth_client() -> TestClient:
    """A TestClient with no credentials, aimed at the served app."""
    return TestClient(app_main.app)


def _assert_401(response) -> None:
    assert response.status_code == 401
    assert response.json()["detail"] == AUTH_DETAIL
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def _jwks_fixture():
    """Build a local RSA key, its JWKS document, and a JWT minter.

    Stands in for the IdP so the production (JWT) auth path can be
    exercised without network access.  Returns ``(jwks, mint,
    wrong_key)``: ``mint(private_key=None)`` signs a token whose issuer,
    audience, sub and expiry all satisfy
    ``security._validate_token_jwt``; ``wrong_key`` signs a token with
    the same claims that must fail signature verification.
    """
    from base64 import urlsafe_b64encode

    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()

    def _b64(value: int) -> str:
        raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "kid": "proof-key-1",
                "use": "sig",
                "alg": "RS256",
                "n": _b64(numbers.n),
                "e": _b64(numbers.e),
            }
        ]
    }

    def mint(private_key=None) -> str:
        now = int(time.time())
        payload = {
            "iss": security.AUTH_ISSUER,
            "aud": security.AUTH_AUDIENCE,
            "sub": "jwks-proof-user",
            "preferred_username": "jwks-proof",
            "iat": now,
            "exp": now + 300,
        }
        return pyjwt.encode(
            payload,
            private_key or key,
            algorithm="RS256",
            headers={"kid": "proof-key-1"},
        )

    return jwks, mint, wrong_key


def test_issue72_freeform(client, monkeypatch):
    # Preconditions set by tests/conftest.py: development mode with the
    # default dev token.  `client` sends "Bearer dev-secret-token".
    assert auth_config.AUTH_MODE == "development"
    assert auth_config.AUTH_DEV_TOKEN == DEV_TOKEN

    # Baseline — the dev bearer token is accepted under dev mode.
    accepted = client.get(PROTECTED_PATH)
    assert accepted.status_code == 200

    # No auth module is re-imported and no process restart happens from
    # here on; every observation below is a fresh request.

    # --- AC-2: flip AUTH_MODE from dev to prod at runtime ----------------
    monkeypatch.setattr(auth_config, "AUTH_MODE", "production")

    # The very next request, carrying the same bearer token that was
    # accepted moments ago, must now be rejected.
    _assert_401(client.get(PROTECTED_PATH))

    # An unauthenticated request is still rejected with the same body.
    _assert_401(_unauth_client().get(PROTECTED_PATH))

    # --- AC-1: AUTH_DEV_TOKEN is equally lazily observed -----------------
    monkeypatch.setattr(auth_config, "AUTH_MODE", "development")
    monkeypatch.setattr(auth_config, "AUTH_DEV_TOKEN", ROTATED_TOKEN)

    # The old token (still sent by `client`) is no longer accepted ...
    _assert_401(client.get(PROTECTED_PATH))

    # ... while the token configured at runtime is accepted.
    rotated = TestClient(
        app_main.app, headers={"Authorization": f"Bearer {ROTATED_TOKEN}"}
    )
    assert rotated.get(PROTECTED_PATH).status_code == 200

    # Both runtime changes are also observable through the public token
    # entry point itself — no re-import of the auth modules involved.
    assert security.validate_token(DEV_TOKEN) is None
    assert security.validate_token(ROTATED_TOKEN).sub == "dev-user-1"

    # --- Review fix: AUTH_JWKS_CACHE_TTL=0 must not break JWT validation
    # The old bucket computation ``int(time.time()) // AUTH_JWKS_CACHE_TTL``
    # raised ZeroDivisionError when the TTL was 0 — crashing the JWKS cache
    # bucketing and making validate_token reject every JWT.  The TTL is
    # flipped at runtime on the config source (no re-import), exactly like
    # the AC-1/AC-2 flips above.
    monkeypatch.setattr(auth_config, "AUTH_MODE", "production")
    monkeypatch.setattr(auth_config, "AUTH_JWKS_CACHE_TTL", 0)

    jwks, mint, wrong_key = _jwks_fixture()
    monkeypatch.setattr(security, "_fetch_jwks", lambda: jwks)

    # Defect 2 — with TTL=0 the JWKS lookup must not raise: bucketing is
    # disabled and the document comes straight from the (stubbed) fetch.
    assert security._get_jwks() == jwks

    # Defect 1 — a validly signed JWT is still accepted under TTL=0
    # (previously the ZeroDivisionError made validate_token reject all
    # JWTs, valid or not).
    user = security.validate_token(mint())
    assert user is not None
    assert user.sub == "jwks-proof-user"
    assert user.username == "jwks-proof"

    # Fail-closed is preserved: a JWT signed with the wrong key is still
    # rejected, so the acceptance above is real signature validation.
    assert security.validate_token(mint(wrong_key)) is None

    # Regression guard: a normal TTL still buckets and serves the JWKS.
    monkeypatch.setattr(auth_config, "AUTH_JWKS_CACHE_TTL", 3600)
    security._cached_jwks.cache_clear()
    assert security._get_jwks() == jwks
    security._cached_jwks.cache_clear()
