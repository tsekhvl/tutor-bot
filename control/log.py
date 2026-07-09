"""Поля журнала ответов контрольной для SQLite."""
from __future__ import annotations

from .models import AnswerCheck, Question, QuestionType


def enrich_answer_log(
    entry: dict,
    *,
    question: Question | None,
    student_answer: str,
    check: AnswerCheck,
) -> dict:
    entry["student_answer"] = (student_answer or "").strip()
    entry["feedback"] = check.feedback
    if question:
        entry["prompt"] = question.prompt
        if question.thesis:
            entry["thesis"] = question.thesis
        if question.type == QuestionType.CHOICE and student_answer:
            oid = student_answer.strip()
            entry["option_id"] = oid
            opt = question.option_by_id(oid)
            if opt:
                entry["option_text"] = opt.text
    return entry
