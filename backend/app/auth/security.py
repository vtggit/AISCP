# Token validation helpers
#
# This module owns the token → AuthUser translation.  Route-level code
# should never import this directly; always go through dependencies.py.
#
# Design intent
# -------------
# The public interface is the single function `validate_token(raw_token)`.
# Two modes are supported via ``AUTH_MODE``:
#
# * ``development`` → simple bearer-token comparison (safe for local dev)
# * ``production``  → real JWT validation against IdP JWKS
#
# The production path fetches JWKS from the configured IdP, caches the
# keys, verifies the JWT signature and claims, and normalises roles/
# groups from the decoded payload into ``AuthUser``.

import hmac
import logging
import time
from functools import lru_cache
from typing import Any

import jwt  # pyjwt
import requests

from app.auth import config as auth_config
from app.auth.config import (
    AUTH_ALGORITHMS,
    AUTH_AUDIENCE,
    AUTH_CLIENT_ID,
    AUTH_DEV_ROLES,
    AUTH_DEV_TOKEN,
    AUTH_GROUP_CLAIMS,
    AUTH_ISSUER,
    AUTH_JWKS_URL,
    AUTH_ROLE_CLAIMS,
)
from app.auth.models import AuthUser
from app.observability.logging import get_request_id

logger = logging.getLogger(__name__)


def _req() -> str:
    """Return a request-ID prefix for log lines, or empty string."""
    rid = get_request_id()
    return f" request_id={rid}" if rid else ""


# ---------------------------------------------------------------------------
# Public interface — the ONLY function callers should use
# ---------------------------------------------------------------------------


def validate_token(raw_token: str) -> AuthUser | None:
    """
    Validate *raw_token* and return an ``AuthUser`` on success, or
    ``None`` when the token is missing / expired / malformed.

    The implementation is selected by ``AUTH_MODE``:

    * ``development`` → simple bearer-token comparison (safe for local dev)
    * ``production``  → real JWT validation against IdP JWKS

    Any other value for ``AUTH_MODE`` is treated as a configuration error
    and fails closed (returns ``None``).

    ``AUTH_MODE`` and ``AUTH_DEV_TOKEN`` are refreshed from
    ``app.auth.config`` on every call (issue #72), so a runtime change to
    the configuration is observed on the next request without re-importing
    this module.
    """
    # The ``from app.auth.config import ...`` bindings at the top of this
    # module captured the values at import time.  Re-read them through the
    # lazy accessors so runtime changes to the configuration are observed
    # here, and propagate them to the module-level names that the helpers
    # below read.
    global AUTH_MODE, AUTH_DEV_TOKEN
    AUTH_MODE = auth_config.get_auth_mode()
    AUTH_DEV_TOKEN = auth_config.get_auth_dev_token()

    if not raw_token:
        logger.warning("auth: missing or empty token%s", _req())
        return None

    if AUTH_MODE == "production":
        return _validate_token_jwt(raw_token)
    if AUTH_MODE == "development":
        return _validate_token_dev(raw_token)

    # Fail closed for unknown modes — a typo like "prod" or "staging"
    # should never silently fall back to dev-token behavior.
    logger.error("auth: unsupported AUTH_MODE=%s — failing closed%s", AUTH_MODE, _req())
    return None


# ---------------------------------------------------------------------------
# Development stub — intentionally simple, clearly marked for replacement
# ---------------------------------------------------------------------------


def _validate_token_dev(token: str) -> AuthUser | None:
    """
    Validate the development bearer token.

    In development this lets you test the auth boundary without a real
    IdP.  The token value comes from the ``AUTH_DEV_TOKEN`` environment
    variable, which has no default: when it is unset or empty, no token
    is accepted.

    Supported identities (``T`` is the configured ``AUTH_DEV_TOKEN``):

        T           → uses AUTH_DEV_ROLES (default: ["user"])
        T:admin     → ["admin", "user"]
        T:user      → ["user"]

    The presented token is checked against each accepted full form
    (the exact token plus the two role-suffixed variants) with a
    constant-time ``hmac.compare_digest`` comparison on the UTF-8
    encoded bytes of both sides — never with ``==``, ``!=`` or
    ``startswith`` on the secret.  All three forms are always
    evaluated so timing does not reveal which one matched, and no
    part of the presented token is ever written to the logs.  When
    ``AUTH_DEV_TOKEN`` is empty or unset the function fails closed
    and rejects everything.  Presented tokens that cannot be UTF-8
    encoded (e.g. unpaired surrogates) are rejected with a warning
    instead of raising.
    """
    if not AUTH_DEV_TOKEN:
        # Fail closed — without a configured dev token no token is valid.
        logger.warning(
            "auth: AUTH_DEV_TOKEN is not configured — rejecting development token%s",
            _req(),
        )
        return None

    if not isinstance(token, str) or not token:
        logger.warning("auth: missing or empty token%s", _req())
        return None

    try:
        presented = token.encode("utf-8")
        base = AUTH_DEV_TOKEN.encode("utf-8")
    except UnicodeError:
        # A str holding unpaired surrogates (or a mis-decoded
        # AUTH_DEV_TOKEN) cannot be UTF-8 encoded.  Fail closed with
        # one warning that never echoes the presented token — never
        # raise, or a hostile token would become a 500.
        logger.warning("auth: dev token is not valid UTF-8 — rejecting%s", _req())
        return None

    matched_exact = hmac.compare_digest(presented, base)
    matched_admin = hmac.compare_digest(presented, base + b":admin")
    matched_user = hmac.compare_digest(presented, base + b":user")

    if matched_exact:
        # Exact match — use configured default roles
        roles = AUTH_DEV_ROLES
        sub = "dev-user-1"
        username = "developer"
    elif matched_admin:
        # Role-suffixed token — admin identity
        roles = ["admin", "user"]
        sub = "dev-admin-1"
        username = "admin"
    elif matched_user:
        # Role-suffixed token — plain user identity
        roles = ["user"]
        sub = "dev-user-1"
        username = "developer"
    else:
        # Single warning that never contains the presented token.
        logger.warning("auth: dev token mismatch%s", _req())
        return None

    return AuthUser(
        sub=sub,
        username=username,
        email=f"{username}@aicrm.local",
        roles=roles,
        groups=[],
        raw_claims={"mode": "development"},
    )


# ---------------------------------------------------------------------------
# Production JWT validation — real IdP integration
# ---------------------------------------------------------------------------


def _fetch_jwks() -> dict[str, Any]:
    """
    Fetch the JWKS document from the IdP.

    Returns the raw JWKS JSON (``{"keys": [...]}``).  Raises on network
    errors so the caller can fail closed.
    """
    logger.debug("Fetching JWKS from %s", AUTH_JWKS_URL)
    resp = requests.get(AUTH_JWKS_URL, timeout=10)
    resp.raise_for_status()
    return resp.json()


# Cache JWKS for AUTH_JWKS_CACHE_TTL seconds.  The cache is keyed on a
# time-bucket so it automatically expires and refreshes.
@lru_cache(maxsize=1)
def _cached_jwks(_bucket: int) -> dict[str, Any]:
    return _fetch_jwks()


def _get_jwks() -> dict[str, Any]:
    """Return cached JWKS, refreshing when the TTL has elapsed."""
    # Read the TTL from the config module at call time (issue #72) so a
    # runtime change takes effect without re-importing this module.
    ttl = auth_config.AUTH_JWKS_CACHE_TTL
    if ttl <= 0:
        # A non-positive TTL disables cache bucketing: fetch fresh on every
        # call.  Bucketing divides by the TTL, so TTL=0 raised a
        # ZeroDivisionError that made validate_token reject every JWT.
        return _fetch_jwks()
    bucket = int(time.time()) // ttl
    try:
        return _cached_jwks(bucket)
    except ValueError:
        # Cache was invalidated — fetch fresh
        _cached_jwks.cache_clear()
        return _cached_jwks(bucket)


def _get_verification_key(jwks: dict[str, Any], header: dict[str, Any]) -> str | None:
    """
    Resolve the correct verification key from the JWKS set.

    Matches on ``kid`` (key ID).  Returns a PEM-encoded public key
    string that ``pyjwt`` can use for verification, or ``None`` when
    no matching key is found.
    """
    kid = header.get("kid")
    for key_entry in jwks.get("keys", []):
        if kid and key_entry.get("kid") != kid:
            continue
        pem = _jwk_to_pem(key_entry)
        if pem:
            return pem
    logger.warning("No matching key found in JWKS for kid=%s", kid)
    return None


def _jwk_to_pem(jwk: dict[str, Any]) -> str | None:
    """
    Convert a single JWK entry to a PEM-encoded public key string.

    Supports RSA (kty=RSA) and EC (kty=EC) keys, which cover the
    overwhelming majority of enterprise IdP deployments.
    """
    from base64 import urlsafe_b64decode

    kty = jwk.get("kty")

    if kty == "RSA":
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

            n = int.from_bytes(urlsafe_b64decode(jwk["n"] + "=="), "big")
            e = int.from_bytes(urlsafe_b64decode(jwk["e"] + "=="), "big")

            pub = rsa_mod.RSAPublicNumbers(e, n).public_key()
            return pub.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to convert RSA JWK to PEM: %s", exc)
            return None

    if kty == "EC":
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec

            crv = jwk.get("crv", "P-256")
            curve_map = {
                "P-256": ec.SECP256R1(),
                "P-384": ec.SECP384R1(),
                "P-521": ec.SECP521R1(),
            }
            curve = curve_map.get(crv)
            if not curve:
                logger.warning("Unsupported EC curve: %s", crv)
                return None

            x = urlsafe_b64decode(jwk["x"] + "==")
            y = urlsafe_b64decode(jwk["y"] + "==")

            pub = ec.EllipticCurvePublicNumbers(
                x=int.from_bytes(x, "big"),
                y=int.from_bytes(y, "big"),
                curve=curve,
            ).public_key()
            return pub.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
        except Exception as exc:
            logger.error("Failed to convert EC JWK to PEM: %s", exc)
            return None

    logger.warning("Unsupported JWK key type: %s", kty)
    return None


def _resolve_nested_claim(claims: dict[str, Any], path: str) -> Any:
    """
    Resolve a dot-notation claim path against the token payload.

    Supports ``{client_id}`` substitution so paths like
    ``resource_access.{client_id}.roles`` resolve correctly.  The
    placeholder is substituted on a per-segment basis after the path
    has been split on ``.`` so a client ID that itself contains dots
    stays a single claim key (exactly as it appears in the payload)
    instead of being split into nested segments.
    """
    current: Any = claims
    for part in path.split("."):
        part = part.replace("{client_id}", AUTH_CLIENT_ID)
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _extract_string_list(value: Any) -> list[str]:
    """
    Normalise a claim value into a list of strings.

    Handles ``None``, plain strings, and lists of strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    return []


def _normalize_roles(claims: dict[str, Any]) -> list[str]:
    """
    Scan the configured role claim paths and collect unique role strings.

    Returns a deduplicated, sorted list of normalised roles.
    """
    roles: list[str] = []
    for claim_path in AUTH_ROLE_CLAIMS:
        raw = _resolve_nested_claim(claims, claim_path)
        if isinstance(raw, dict) and "roles" in raw:
            # Keycloak resource_access returns {"roles": [...]}
            roles.extend(_extract_string_list(raw["roles"]))
        else:
            roles.extend(_extract_string_list(raw))
    # Deduplicate while preserving order
    seen: set = set()
    unique: list[str] = []
    for r in roles:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _normalize_groups(claims: dict[str, Any]) -> list[str]:
    """
    Scan the configured group claim paths and collect unique group strings.
    """
    groups: list[str] = []
    for claim_path in AUTH_GROUP_CLAIMS:
        raw = _resolve_nested_claim(claims, claim_path)
        groups.extend(_extract_string_list(raw))
    seen: set = set()
    unique: list[str] = []
    for g in groups:
        if g not in seen:
            seen.add(g)
            unique.append(g)
    return unique


def _validate_token_jwt(raw_token: str) -> AuthUser | None:
    """
    Real JWT validation against the configured IdP.

    Steps:
    1. Unsign the JWT header to get ``kid``
    2. Fetch JWKS (cached) and locate the matching key
    3. Verify signature, issuer, audience, and expiry
    4. Normalise roles and groups from claims
    5. Return ``AuthUser`` — or ``None`` on any failure (fail closed)
    """
    try:
        # Step 1 — decode header without verification to get kid/alg
        header = jwt.get_unverified_header(raw_token)

        # Step 2 — fetch JWKS and find the right key
        jwks = _get_jwks()
        key = _get_verification_key(jwks, header)
        if key is None:
            logger.warning(
                "auth: JWT signature verification failed — no matching key in JWKS%s",
                _req(),
            )
            return None

        # Step 3 — decode and verify
        payload: dict[str, Any] = jwt.decode(
            raw_token,
            key,
            algorithms=AUTH_ALGORITHMS,
            audience=AUTH_AUDIENCE,
            issuer=AUTH_ISSUER,
            options={
                "require": ["exp", "sub"],
                "verify_exp": True,
                "verify_signature": True,
            },
        )

        # Step 4 — extract identity claims
        sub = payload.get("sub")
        if not sub:
            logger.warning("auth: JWT missing required 'sub' claim%s", _req())
            return None

        username = (
            payload.get("preferred_username")
            or payload.get("name")
            or payload.get("email")
        )

        # Step 5 — normalise roles and groups
        roles = _normalize_roles(payload)
        groups = _normalize_groups(payload)

        # Ensure every authenticated user has at least a baseline role
        if not roles:
            roles = ["user"]

        return AuthUser(
            sub=str(sub),
            username=str(username) if username else None,
            email=payload.get("email"),
            roles=roles,
            groups=groups,
            raw_claims=payload,
        )

    except jwt.ExpiredSignatureError:
        logger.warning("auth: JWT validation failed — token expired%s", _req())
        return None
    except jwt.InvalidAudienceError:
        logger.warning("auth: JWT validation failed — invalid audience%s", _req())
        return None
    except jwt.InvalidIssuerError:
        logger.warning("auth: JWT validation failed — invalid issuer%s", _req())
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("auth: JWT validation failed — %s%s", exc, _req())
        return None
    except requests.RequestException as exc:
        logger.error("auth: failed to fetch JWKS — %s%s", exc, _req())
        return None
    except Exception as exc:
        # Fail closed — never leak internals
        logger.error("auth: unexpected JWT validation error — %s%s", exc, _req())
        return None
