"""Сессия тренажёра в context.user_data."""
from __future__ import annotations

from telegram.ext import ContextTypes

from .models import ExamTrainSession

EXAM_KEY = "exam_train"


def get_session(context: ContextTypes.DEFAULT_TYPE) -> ExamTrainSession | None:
    raw = context.user_data.get(EXAM_KEY)
    if not raw:
        return None
    if isinstance(raw, ExamTrainSession):
        return raw
    return ExamTrainSession.from_dict(raw)


def set_session(context: ContextTypes.DEFAULT_TYPE, session: ExamTrainSession | None) -> None:
    if session is None:
        context.user_data.pop(EXAM_KEY, None)
    else:
        context.user_data[EXAM_KEY] = session.to_dict()


def is_exam_active(context: ContextTypes.DEFAULT_TYPE) -> bool:
    s = get_session(context)
    return s is not None and s.phase != "finished"
