from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def setting(name: str, default: str = "") -> str:
    """Read local environment first, then Streamlit Cloud root secrets."""
    value = os.getenv(name)
    if value is not None:
        return str(value)
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return default


def setting_bool(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return setting(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def setting_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(setting(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))
