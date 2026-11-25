# Lightweight authentication models
#
# These models represent the authenticated user context derived from a
# validated token.  They are intentionally minimal — full RBAC will be
# added in a later step.

from typing import Any

from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    """Represents the currently authenticated user derived from token claims."""

    sub: str = Field(..., description="Unique user identifier (token subject)")
    username: str | None = Field(
        default=None, description="Preferred username or display name"
    )
    email: str | None = Field(default=None)
    roles: list[str] = Field(
        default_factory=list, description="Normalized application roles"
    )
    groups: list[str] = Field(default_factory=list, description="Normalized IdP groups")
    raw_claims: dict[str, Any] = Field(
        default_factory=dict, description="Full token claims for future extension"
    )

    @property
    def display_name(self) -> str:
        """Best-effort human-readable name."""
        return self.username or self.email or self.sub

    @property
    def is_admin(self) -> bool:
        """The admin role is the only thing that makes an admin.

        ``is_admin`` is true exactly when ``"admin"`` is in the
        user's ``roles``.  The user's ``groups`` grant nothing:
        ``"admin"`` appearing only in ``groups`` does not make the
        user an admin.
        """
        return "admin" in self.roles


class MeResponse(BaseModel):
    """Response body for GET /api/auth/me."""

    authenticated: bool = True
    user: AuthUser
