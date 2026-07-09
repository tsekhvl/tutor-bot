"""Хранение сессии контрольной в context.user_data."""
from __future__ import annotations

from telegram.ext import ContextTypes

from .models import ControlSession

CONTROL_KEY = "control"


def get_session(context: ContextTypes.DEFAULT_TYPE) -> ControlSession | None:
    raw = context.user_data.get(CONTROL_KEY)
    if not raw:
        return None
    if isinstance(raw, ControlSession):
        return raw
    return ControlSession.from_dict(raw)


def set_session(context: ContextTypes.DEFAULT_TYPE, session: ControlSession | None) -> None:
    if session is None:
        context.user_data.pop(CONTROL_KEY, None)
    else:
        context.user_data[CONTROL_KEY] = session.to_dict()


def clear_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    set_session(context, None)


def is_control_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    s = get_session(context)
    return s is not None and s.phase not in ("finished",)
