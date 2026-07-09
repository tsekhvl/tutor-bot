"""Gemini: транскрипция голоса, уточняющий вопрос, разбор ответа."""
from __future__ import annotations

import json
import re

from config import (
    GOOGLE_CREDENTIALS_PATH,
    GOOGLE_LOCATION,
    GOOGLE_PROJECT_ID,
    TUTOR_GEMINI_MODEL,
)

from .models import ExamQuestion, TaskKind

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, Part

    VERTEX_AVAILABLE = True
except ImportError:
    vertexai = None
    GenerativeModel = None
    Part = None
    VERTEX_AVAILABLE = False


def _init_vertex() -> None:
    if not VERTEX_AVAILABLE:
        raise RuntimeError("Vertex AI не установлен")
    import os

    if GOOGLE_CREDENTIALS_PATH:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDENTIALS_PATH
    vertexai.init(project=GOOGLE_PROJECT_ID, location=GOOGLE_LOCATION)


def _parse_json_text(raw: str) -> dict:
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


def transcribe_audio_sync(audio_bytes: bytes, *, mime_type: str = "audio/ogg") -> str:
    if not audio_bytes:
        return ""
    _init_vertex()
    model = GenerativeModel(TUTOR_GEMINI_MODEL)
    response = model.generate_content(
        [
            Part.from_data(data=audio_bytes, mime_type=mime_type),
            "Распознай речь на русском языке. Верни только текст сказанного, без комментариев.",
        ],
        generation_config={"temperature": 0.1, "max_output_tokens": 8192},
    )
    return (response.text or "").strip()


def _block_label(block: str) -> str:
    if block == "3":
        return "ИРС, блок 3 (арабская часть, XIX–XXI вв.)"
    return "ИРС, блок 1 (доисламская и исламская история)"


def _followup_system(block: str) -> str:
    base = (
        f"Ты — член экзаменационной комиссии по истории Ближнего Востока ({_block_label(block)}). "
        "Студент тренируется к устному экзамену без подготовки. "
        "Сформулируй ОДИН короткий уточняющий вопрос по его ответу — как на реальном экзамене. "
        "Без оценки и без разбора. Только вопрос."
    )
    if block == "3":
        base += (
            " Проверяй фактологию: даты, связи событий, персоналии, термины. "
            "Можно спросить «а когда именно?», «а кто ещё?», «а последствия?»."
        )
    return base


def _review_system(block: str) -> str:
    return (
        f"Ты — преподаватель, разбирающий тренировочный ответ к устному экзамену ({_block_label(block)}). "
        "Дай конструктивный разбор на русском. Будь конкретным, опирайся на ответ студента. "
        "Не выдумывай факты, которых студент не говорил."
    )


def _task_context(question: ExamQuestion) -> str:
    body = question.context_text or question.prompt
    labels = {
        TaskKind.TOPIC: "Топик (~5 мин)",
        TaskKind.TERMS: "Два термина",
        TaskKind.ESSAY: "Рассуждение",
        TaskKind.DATE: "Дата и событие",
        TaskKind.PERSONALITY: "Персоналия",
        TaskKind.TERM: "Термин",
        TaskKind.PERIOD: "Исторический период",
    }
    label = labels.get(question.task_type, question.task_type.value)
    return f"Тип: {label}. {body}"


def generate_followup_sync(
    question: ExamQuestion,
    main_answer: str,
    *,
    student_hint: str = "",
) -> str:
    _init_vertex()
    model = GenerativeModel(
        TUTOR_GEMINI_MODEL,
        system_instruction=[_followup_system(question.block)],
    )
    hint = f"\n\nКритерии экзамена:\n{student_hint}" if student_hint else ""
    prompt = f"""{_task_context(question)}{hint}

ОТВЕТ СТУДЕНТА:
{main_answer}

Ответь СТРОГО JSON: {{"question": "уточняющий вопрос одним предложением"}}"""
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.4, "max_output_tokens": 1024},
    )
    data = _parse_json_text(response.text)
    q = str(data.get("question", "")).strip()
    if not q:
        raise ValueError("Пустой уточняющий вопрос от модели")
    return q


def generate_review_sync(
    question: ExamQuestion,
    main_answer: str,
    followup_question: str,
    followup_answer: str,
    *,
    student_hint: str = "",
) -> str:
    _init_vertex()
    model = GenerativeModel(
        TUTOR_GEMINI_MODEL,
        system_instruction=[_review_system(question.block)],
    )
    hint = f"\n\nКритерии экзамена:\n{student_hint}" if student_hint else ""
    prompt = f"""{_task_context(question)}{hint}

ОСНОВНОЙ ОТВЕТ СТУДЕНТА:
{main_answer}

УТОЧНЯЮЩИЙ ВОПРОС КОМИССИИ:
{followup_question}

ОТВЕТ НА УТОЧНЕНИЕ:
{followup_answer}

Составь разбор в HTML для Telegram (теги <b>, <i>, списки через •).
Структура:
<b>✅ Сильные стороны</b>
<b>⚠️ Что упущено или слабо</b>
<b>💡 Что улучшить к экзамену</b>
<b>🌟 Что особенно хорошо</b>

Для дат проверяй точность; для персоналий — полноту; для терминов — определение и контекст.
Без вводных «как ИИ». 150–350 слов."""
    response = model.generate_content(
        prompt,
        generation_config={"temperature": 0.35, "max_output_tokens": 8192},
    )
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Пустой разбор от модели")
    return text
