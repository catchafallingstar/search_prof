import os
from typing import Any

import streamlit as st

from db import get_site_admin, upsert_authenticated_user


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def development_auth_enabled() -> bool:
    """Allow a local fake identity only in an explicit development environment."""
    return os.getenv("APP_ENV", "").strip().casefold() == "development" and _truthy(
        os.getenv("DEV_AUTH_BYPASS")
    )


def _development_identity() -> dict[str, str]:
    email = os.getenv("DEV_USER_EMAIL", "owner@example.test").strip().casefold()
    name = os.getenv("DEV_USER_NAME", "Local Owner").strip()
    if not email or "@" not in email:
        raise RuntimeError("DEV_USER_EMAIL must contain a valid local-testing email address.")
    return {"subject": f"local-dev:{email}", "email": email, "name": name or email}


def auth_is_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def is_logged_in() -> bool:
    if development_auth_enabled():
        return True
    return auth_is_configured() and bool(getattr(st.user, "is_logged_in", False))


def login_button() -> None:
    if development_auth_enabled():
        st.info("Local development identity is enabled through .env.")
    elif auth_is_configured():
        st.button("Sign in", on_click=st.login, type="primary")
    else:
        st.info("Authentication is not configured yet. See README.md and .streamlit/secrets.example.toml.")


def require_user() -> dict[str, Any]:
    """Return the database user for either local development or real OIDC login."""
    if development_auth_enabled():
        identity = _development_identity()
        return upsert_authenticated_user(identity["subject"], identity["email"], identity["name"])

    if not is_logged_in():
        st.subheader("Sign in required")
        st.write("Faculty and university submissions require an authenticated identity.")
        login_button()
        st.stop()

    claims = st.user.to_dict()
    subject = str(claims.get("sub") or claims.get("email") or "").strip()
    email = str(claims.get("email") or "").strip()
    name = str(claims.get("name") or claims.get("preferred_username") or email).strip()
    if not subject or not email:
        st.error("Your identity provider did not return both a subject and email address.")
        st.stop()
    return upsert_authenticated_user(subject, email, name)


def require_site_admin(owner_only: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Authorize an active moderator, or specifically the single owner."""
    user = require_user()
    admin = get_site_admin(user["id"])
    if not admin:
        st.error("Site-administrator access is required.")
        st.stop()
    if owner_only and admin["admin_role"] != "owner":
        st.error("Only the site owner can manage moderator accounts.")
        st.stop()
    return user, admin


def account_controls() -> None:
    if development_auth_enabled():
        identity = _development_identity()
        st.warning(
            f"Local development login: {identity['email']}. "
            "DEV_AUTH_BYPASS must be disabled outside local development."
        )
    elif is_logged_in():
        left, right = st.columns([4, 1])
        left.caption(f"Signed in as {getattr(st.user, 'email', '')}")
        right.button("Sign out", on_click=st.logout, width="stretch")
