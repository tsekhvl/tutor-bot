"""Проверка ответов: choice — алгоритм, open — Gemini."""
from __future__ import annotations

import json
import logging
import re

from config import (
    GOOGLE_CREDENTIALS_PATH,
    GOOGLE_LOCATION,
    GOOGLE_PROJECT_ID,
    TUTOR_GEMINI_MODEL,
    TUTOR_OPEN_ANSWER_PASS_SCORE,
)

from .models import AnswerCheck, CheckMethod, Question, QuestionType

logger = logging.getLogger(__name__)

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel

    VERTEX_AVAILABLE = True
except ImportError:
    vertexai = None
    GenerativeModel = None
    VERTEX_AVAILABLE = False


_OPEN_SYSTEM = """Ты проверяешь короткий ответ студента на вопрос контрольной по истории исламского Ближнего Востока (VII–XIII вв.).
Оцени по рубрике. Не зачитывай ответы без содержания.
Ответь СТРОГО JSON: {"score": 0-100, "accepted": true/false, "feedback": "краткий комментарий"}
accepted=true если score >= порога или ответ по существу верный по рубрике."""

_DEBATE_SYSTEM = """Ты судья академического диспута по истории исламского Ближнего Востока (VII–XIII вв.).
Оппонент выдвинул тезис. Студент должен контраргументировать — опровергнуть тезис по существу фактами курса.
Не требуй идеальной формулировки. Засчитывай сильный контраргумент даже если неполный.
Ответь СТРОГО JSON: {"score": 0-100, "accepted": true/false, "feedback": "краткий комментарий"}
accepted=true если студент убедительно парирует тезис по рубрике."""


def _init_vertex() -> None:
    if not VERTEX_AVAILABLE:
        raise RuntimeError("Vertex AI не установлен")
    import os

    if GOOGLE_CREDENTIALS_PATH:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDENTIALS_PATH
    vertexai.init(project=GOOGLE_PROJECT_ID, location=GOOGLE_LOCATION)


def check_choice(question: Question, option_id: str) -> AnswerCheck:
    """Алгоритмическая проверка варианта ответа."""
    if question.type != QuestionType.CHOICE:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="Вопрос не предполагает выбор варианта.",
            method=CheckMethod.ALGORITHMIC,
        )
    correct_ids = question.correct_option_ids()
    if not correct_ids:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="В пуле не задан правильный вариант (correct: true).",
            method=CheckMethod.ALGORITHMIC,
        )
    ok = option_id in correct_ids
    opt = question.option_by_id(option_id)
    if ok:
        feedback = "Верно."
    else:
        chosen = opt.text if opt else option_id
        feedback = f"Неверно. Выбрано: {chosen}"
    return AnswerCheck(
        correct=ok,
        score=100 if ok else 0,
        feedback=feedback,
        method=CheckMethod.ALGORITHMIC,
    )


def check_open_sync(question: Question, student_answer: str) -> AnswerCheck:
    """Проверка открытого ответа через Gemini."""
    text = (student_answer or "").strip()
    if not text:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="Пустой ответ.",
            method=CheckMethod.GEMINI,
        )
    if len(text) < 3:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="Слишком короткий ответ.",
            method=CheckMethod.GEMINI,
        )

    rubric = question.rubric.strip() or (
        "Ответ должен содержательно отвечать на вопрос по теме семинара."
    )
    threshold = TUTOR_OPEN_ANSWER_PASS_SCORE

    if not VERTEX_AVAILABLE:
        raise RuntimeError("Vertex AI не настроен для открытых вопросов")

    _init_vertex()
    model = GenerativeModel(TUTOR_GEMINI_MODEL, system_instruction=[_OPEN_SYSTEM])
    prompt = f"""Порог зачёта: score >= {threshold}.

ВОПРОС:
{question.prompt}

РУБРИКА (эталон):
{rubric}

Семинар: {question.seminar}

ОТВЕТ СТУДЕНТА:
{text}

Ответь только JSON."""

    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.1, "max_output_tokens": 25000},
    )
    raw = response.text.strip()
    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1).strip()
    data = json.loads(raw)
    score = min(100, max(0, int(data.get("score", 0))))
    accepted = bool(data.get("accepted", score >= threshold))
    if score >= threshold:
        accepted = True
    feedback = str(data.get("feedback", "")).strip() or (
        "Зачтено." if accepted else "Нужно точнее ответить по теме."
    )
    return AnswerCheck(
        correct=accepted,
        score=score,
        feedback=feedback,
        method=CheckMethod.GEMINI,
    )


def check_debate_sync(question: Question, student_answer: str) -> AnswerCheck:
    """Проверка контраргумента в дебатах с боссом."""
    text = (student_answer or "").strip()
    if not text:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="Пустой контраргумент.",
            method=CheckMethod.GEMINI,
        )
    if len(text) < 10:
        return AnswerCheck(
            correct=False,
            score=0,
            feedback="Слишком короткий контраргумент — разверните мысль.",
            method=CheckMethod.GEMINI,
        )

    rubric = question.rubric.strip() or (
        "Контраргумент должен по существу опровергать тезис оппонента фактами семинара."
    )
    thesis = (question.thesis or question.prompt).strip()
    threshold = TUTOR_OPEN_ANSWER_PASS_SCORE

    if not VERTEX_AVAILABLE:
        raise RuntimeError("Vertex AI не настроен для дебатов")

    _init_vertex()
    model = GenerativeModel(TUTOR_GEMINI_MODEL, system_instruction=[_DEBATE_SYSTEM])
    prompt = f"""Порог зачёта: score >= {threshold}.

ТЕЗИС ОППОНЕНТА:
{thesis}

РУБРИКА (что должно быть в сильном контраргументе):
{rubric}

Семинар: {question.seminar}

КОНТРАРГУМЕНТ СТУДЕНТА:
{text}

Ответь только JSON."""

    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.1, "max_output_tokens": 25000},
    )
    raw = response.text.strip()
    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1).strip()
    data = json.loads(raw)
    score = min(100, max(0, int(data.get("score", 0))))
    accepted = bool(data.get("accepted", score >= threshold))
    if score >= threshold:
        accepted = True
    feedback = str(data.get("feedback", "")).strip() or (
        "Тезис парирован." if accepted else "Контраргумент слабый — добавьте факты."
    )
    return AnswerCheck(
        correct=accepted,
        score=score,
        feedback=feedback,
        method=CheckMethod.GEMINI,
    )


async def check_answer(question: Question, answer: str) -> AnswerCheck:
    """Единая точка: choice по id варианта, open/debate по тексту."""
    if question.type == QuestionType.CHOICE:
        return check_choice(question, answer.strip())
    import asyncio

    if question.type == QuestionType.DEBATE:
        return await asyncio.to_thread(check_debate_sync, question, answer)
    return await asyncio.to_thread(check_open_sync, question, answer)
