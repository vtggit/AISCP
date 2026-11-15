"""Registered settings: read, change and reset with an audit trail.

``GET /api/settings`` lists every registered setting, ordered by key,
with its effective value for the caller: the stored system value for a
system setting, the caller's own stored value for a user setting, else
the default.  ``PUT /api/settings/{key}`` stores a value (a system
setting needs the admin role, a user setting is stored for the caller's
own subject) and ``DELETE /api/settings/{key}`` resets it to its
default; both append one row to the append-only ``settings_changes``
trail.  Unknown keys answer 404 and a value the setting cannot hold
answers 422 naming the key and what it needs: the allowed values for an
enum, a boolean, or the number's bounds.
"""

import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import require_authenticated_user
from app.auth.display_name import display_name
from app.auth.models import AuthUser
from app.repositories.settings_postgres_repository import (
    SettingsPostgresRepository,
)
from app.settings.registry import Setting, all_settings, get_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])

_repo = SettingsPostgresRepository()


class SettingEntry(BaseModel):
    """One registered setting as served by the settings routes."""

    key: str
    scope: str
    type: str
    allowed: list[str] | None
    min: float | None
    max: float | None
    default: Any
    description: str
    value: Any
    source: str
    editable: bool
    updated_by: str | None
    updated_by_name: str | None = None
    updated_at: str | None


class SettingValueBody(BaseModel):
    """The body of PUT /api/settings/{key}."""

    value: Any


def require_any_authenticated_user(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The settings routes' own authentication dependency.

    The settings routes are open to any signed-in user (the admin-role
    check for a system setting happens in the handler, where it can
    also tell system and user settings apart): the dependency answers
    401 without a valid Bearer token even when the router is mounted
    without the app's include-time gate, and passes the authenticated
    user through unchanged.
    """
    return current_user


def _entry(setting: Setting, stored: dict | None, editable: bool) -> dict:
    """The GET shape of one setting for the caller.

    The effective value is the stored value for the caller (the system
    row for a system setting, the caller's own row for a user setting)
    and the registry default while nothing is stored; source names
    where the effective value comes from.
    """
    if stored is not None:
        value = stored["value"]
        updated_by = stored["updated_by"]
        updated_by_name = stored["updated_by_name"]
        updated_at = stored["updated_at"].isoformat()
        source = setting.scope
    else:
        value = setting.default
        updated_by = None
        updated_by_name = None
        updated_at = None
        source = "default"
    return {
        "key": setting.key,
        "scope": setting.scope,
        "type": setting.type,
        "allowed": list(setting.allowed) if setting.allowed is not None else None,
        "min": setting.min,
        "max": setting.max,
        "default": setting.default,
        "description": setting.description,
        "value": value,
        "source": source,
        "editable": editable,
        "updated_by": updated_by,
        "updated_by_name": updated_by_name,
        "updated_at": updated_at,
    }


def _owner(setting: Setting, user: AuthUser) -> str:
    """The owner of a setting for the caller: '' for system, the sub for user."""
    return "" if setting.scope == "system" else user.sub


def _require_admin_for(setting: Setting, user: AuthUser) -> None:
    """A system setting needs the admin role; a user setting never does."""
    if setting.scope == "system" and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail="Role 'admin' is required to perform this action.",
        )


def _validate_value(setting: Setting, value: Any) -> None:
    """422 naming the key and what the setting needs for a value it
    cannot hold: a non-boolean for a boolean setting, a non-number or
    an out-of-bounds number for a number setting, or a value outside
    the enum for an enum setting.  Any other JSON value (huge strings,
    NUL bytes, objects, booleans for a number) is simply not what the
    setting holds."""
    if setting.type == "boolean":
        if value is True or value is False:
            return
        raise HTTPException(
            status_code=422,
            detail=(
                f"Setting '{setting.key}' must be a boolean "
                f"(allowed values: true, false)."
            ),
        )
    if setting.type == "number":
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not (setting.min <= value <= setting.max)
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Setting '{setting.key}' must be a number between "
                    f"{setting.min} and {setting.max}."
                ),
            )
        return
    if isinstance(value, str) and value in setting.allowed:
        return
    raise HTTPException(
        status_code=422,
        detail=(
            f"Setting '{setting.key}' must be one of: " f"{', '.join(setting.allowed)}."
        ),
    )


@router.get("", response_model=list[SettingEntry])
def list_settings(
    user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """Every registered setting, ordered by key.

    Each entry carries the registry metadata (type, the enum's allowed
    values or null, the default, the description), the effective value
    for the caller with its source (default, system or user), whether
    the caller may change it (a user setting always, a system setting
    only for the admin role) and who/when the effective value was set
    (null while the default applies).
    """
    entries = []
    for setting in all_settings():
        stored = _repo.get(setting.scope, _owner(setting, user), setting.key)
        entries.append(
            _entry(
                setting,
                stored,
                editable=setting.scope == "user" or user.is_admin,
            )
        )
    return entries


@router.put("/{key}", response_model=SettingEntry)
def set_setting(
    key: str,
    body: SettingValueBody,
    user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Store a value for one registered setting.

    A system setting needs the admin role (403 otherwise); a user
    setting is stored for the caller's own subject only.  An unknown
    key answers 404, a value of the wrong type or outside the enum
    answers 422.  Returns the updated entry in the GET shape and appends
    one row to settings_changes — except when the value already stored
    is the one given again, which changes nothing and appends no row.
    """
    setting = get_setting(key)
    if setting is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    _require_admin_for(setting, user)
    _validate_value(setting, body.value)
    value = float(body.value) if setting.type == "number" else body.value

    owner = _owner(setting, user)
    stored = _repo.get(setting.scope, owner, setting.key)
    if stored is not None and stored["value"] == value:
        # Same value as stored: nothing changes, no change row.
        return _entry(
            setting,
            stored,
            editable=setting.scope == "user" or user.is_admin,
        )
    _repo.store(
        scope=setting.scope,
        owner=owner,
        key=setting.key,
        value=value,
        updated_by=user.sub,
        updated_by_name=display_name(user),
        old_value=stored["value"] if stored is not None else None,
    )
    updated = _repo.get(setting.scope, owner, setting.key)
    return _entry(
        setting,
        updated,
        editable=setting.scope == "user" or user.is_admin,
    )


@router.delete("/{key}", response_model=SettingEntry)
def reset_setting(
    key: str,
    user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """Reset one registered setting to its default.

    Same authorization as PUT: a system setting needs the admin role, a
    user setting is the caller's own.  Returns the entry with source
    "default" and appends a change row with a null new_value — except
    when nothing is stored, which changes nothing and appends no row.
    """
    setting = get_setting(key)
    if setting is None:
        raise HTTPException(status_code=404, detail=f"Unknown setting: {key}")
    _require_admin_for(setting, user)

    owner = _owner(setting, user)
    stored = _repo.get(setting.scope, owner, setting.key)
    if stored is None:
        # Nothing stored: the default already applies, no change row.
        return _entry(
            setting,
            None,
            editable=setting.scope == "user" or user.is_admin,
        )
    _repo.reset(
        scope=setting.scope,
        owner=owner,
        key=setting.key,
        changed_by=user.sub,
        changed_by_name=display_name(user),
        old_value=stored["value"],
    )
    return _entry(
        setting,
        None,
        editable=setting.scope == "user" or user.is_admin,
    )
