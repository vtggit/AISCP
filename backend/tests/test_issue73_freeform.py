"""Issue #73 proof: development-token validation is constant-time and fails closed.

``app.auth.security._validate_token_dev`` must:

* accept the exact dev token, its ``:admin`` and its ``:user`` forms with
  the documented roles and subjects;
* reject a wrong token, an unknown suffix (``:root``), a prefix of the
  token, the token plus one extra character, and the empty string —
  each with a single warning that never contains the presented token;
* perform the token check through ``hmac.compare_digest`` on UTF-8
  bytes (proven by recording calls that delegate to the real
  function);
* reject every token when ``AUTH_DEV_TOKEN`` is empty (fail closed);
* fail closed (no 500) when the presented token cannot be UTF-8
  encoded, e.g. it holds unpaired surrogates;
* resolve ``{client_id}`` claim-path placeholders correctly when the
  client ID itself contains a dot.
"""

import logging

from app.auth import security

DEV_TOKEN = "dev-secret-token"
LOGGER_NAME = "app.auth.security"

REJECTED = (
    "totally-wrong-token",  # a wrong token
    DEV_TOKEN + ":root",  # unknown role suffix
    DEV_TOKEN[:-1],  # a prefix of the token
    DEV_TOKEN + "!",  # the token plus one extra character
    "",  # the empty string
)


def test_issue73_freeform(monkeypatch, caplog):
    # The shared session fixture runs ``alembic upgrade``, and
    # migrations/env.py calls ``fileConfig(alembic.ini)`` whose
    # disable_existing_loggers default turns off every logger that
    # already exists — including this one.  Re-enable it so the
    # warning assertions below observe what the code actually logs.
    monkeypatch.setattr(logging.getLogger(LOGGER_NAME), "disabled", False)

    monkeypatch.setattr(security, "AUTH_DEV_TOKEN", DEV_TOKEN)
    monkeypatch.setattr(security, "AUTH_DEV_ROLES", ["user"])

    # --- accepted forms: roles and subjects are unchanged ----------------
    exact = security._validate_token_dev(DEV_TOKEN)
    assert exact is not None
    assert exact.sub == "dev-user-1"
    assert exact.roles == ["user"]

    admin = security._validate_token_dev(DEV_TOKEN + ":admin")
    assert admin is not None
    assert admin.sub == "dev-admin-1"
    assert admin.roles == ["admin", "user"]

    user = security._validate_token_dev(DEV_TOKEN + ":user")
    assert user is not None
    assert user.sub == "dev-user-1"
    assert user.roles == ["user"]

    # --- rejected forms: one warning each, never containing the token ----
    for bad in REJECTED:
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert security._validate_token_dev(bad) is None
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING
        if bad:
            assert bad not in caplog.records[0].getMessage()

    # --- the check really goes through hmac.compare_digest ----------------
    real_compare_digest = security.hmac.compare_digest
    calls: list[tuple[object, object]] = []

    def recording_compare_digest(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(security.hmac, "compare_digest", recording_compare_digest)
    result = security._validate_token_dev(DEV_TOKEN)
    assert result is not None
    assert result.sub == "dev-user-1"
    assert result.roles == ["user"]
    assert len(calls) >= 1
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)
    # The presented token is compared against the exact full form (UTF-8 bytes).
    assert any(
        a == DEV_TOKEN.encode("utf-8") and b == DEV_TOKEN.encode("utf-8")
        for a, b in calls
    )

    # --- defect proof: non-UTF-8-encodable presented tokens fail closed ----
    # Unpaired surrogates are the only characters a Python str may hold
    # that UTF-8 cannot encode.  Presenting them must be rejected with a
    # single token-free warning — never raised as a 500.
    for bad in (DEV_TOKEN + "\ud800", "\udcff" + DEV_TOKEN, "\ud800"):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert security._validate_token_dev(bad) is None
        assert len(caplog.records) == 1
        assert caplog.records[0].levelno == logging.WARNING
        assert bad not in caplog.records[0].getMessage()

    # Same hostile input through the public entry point (development
    # mode) must also fail closed instead of raising.
    monkeypatch.setattr(security, "AUTH_MODE", "development")
    assert security.validate_token(DEV_TOKEN + "\ud800") is None

    # --- defect proof: client IDs containing dots ---------------------------
    # ``{client_id}`` must be substituted per path segment so a client ID
    # with a dot stays a single claim key, not nested segments.
    monkeypatch.setattr(security, "AUTH_CLIENT_ID", "aierp.client")
    monkeypatch.setattr(
        security, "AUTH_ROLE_CLAIMS", ["resource_access.{client_id}.roles"]
    )
    assert security._normalize_roles(
        {"resource_access": {"aierp.client": {"roles": ["auditor", "user"]}}}
    ) == ["auditor", "user"]
    assert (
        security._normalize_roles(
            {"resource_access": {"aierp": {"client": {"roles": ["wrong"]}}}}
        )
        == []
    )

    # --- fail closed when AUTH_DEV_TOKEN is empty --------------------------
    monkeypatch.setattr(security, "AUTH_DEV_TOKEN", "")
    for presented in (DEV_TOKEN, DEV_TOKEN + ":admin", DEV_TOKEN + ":user", ""):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert security._validate_token_dev(presented) is None
        assert len(caplog.records) == 1
        if presented:
            assert presented not in caplog.records[0].getMessage()
