"""Пул билетов тренажёра."""
from __future__ import annotations

import json
import random
from pathlib import Path

from .models import ExamQuestion, ExamTrainPool, TaskKind

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_VOICE_HINT = (
    "\n\n<i>Отвечайте как на экзамене: можно текстом или голосовым.</i>"
)


def load_block_pool(block: str) -> ExamTrainPool | None:
    path = DATA_DIR / f"exam_train_block{block}.json"
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return ExamTrainPool(
        block=str(data.get("block", block)),
        title=str(data.get("title", "")),
        student_hint=str(data.get("student_hint", "")),
        task_types=[str(x) for x in (data.get("task_types") or [])],
        topics=[str(x) for x in (data.get("topics") or [])],
        terms=[str(x) for x in (data.get("terms") or [])],
        essays=[str(x) for x in (data.get("essays") or [])],
        dates=[str(x) for x in (data.get("dates") or [])],
        personalities=[str(x) for x in (data.get("personalities") or [])],
        periods=[str(x) for x in (data.get("periods") or [])],
    )


def pick_question(pool: ExamTrainPool, task_type: TaskKind, *, rng: random.Random) -> ExamQuestion:
    block = pool.block

    if task_type == TaskKind.TOPIC:
        idx = rng.randrange(len(pool.topics))
        topic = pool.topics[idx]
        prompt = (
            f"<b>Топик (~5 минут)</b>\n\n"
            f"Расскажите по теме:\n<b>{topic}</b>\n\n"
            "<i>На экзамене могут остановить и уточнить — здесь бот сделает то же после ответа.</i>"
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Топик: {topic}",
            item_index=idx + 1,
        )

    if task_type == TaskKind.TERMS:
        if len(pool.terms) < 2:
            raise ValueError("Недостаточно терминов в пуле")
        pair = rng.sample(pool.terms, 2)
        prompt = (
            "<b>Два термина</b>\n\n"
            f"Дайте определения:\n"
            f"1. <b>{pair[0]}</b>\n"
            f"2. <b>{pair[1]}</b>"
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Определите термины: {pair[0]}; {pair[1]}",
            terms=pair,
        )

    if task_type == TaskKind.ESSAY:
        idx = rng.randrange(len(pool.essays))
        essay = pool.essays[idx]
        prompt = f"<b>Рассуждение</b>\n\n{essay}"
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=essay,
            item_index=idx + 1,
        )

    if task_type == TaskKind.DATE:
        idx = rng.randrange(len(pool.dates))
        item = pool.dates[idx]
        prompt = (
            f"<b>📅 Дата</b>\n\n"
            f"<b>{item}</b>\n\n"
            "Назовите дату (или хронологические рамки) и кратко: "
            "<i>что это за событие и почему оно важно</i>."
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Дата/событие: {item}",
            item_index=idx + 1,
        )

    if task_type == TaskKind.PERSONALITY:
        idx = rng.randrange(len(pool.personalities))
        item = pool.personalities[idx]
        prompt = (
            f"<b>👤 Персоналия</b>\n\n"
            f"<b>{item}</b>\n\n"
            "Расскажите: <i>кто это, чем известен(а), к какому периоду относится</i>."
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Персоналия: {item}",
            item_index=idx + 1,
        )

    if task_type == TaskKind.TERM:
        idx = rng.randrange(len(pool.terms))
        item = pool.terms[idx]
        prompt = (
            f"<b>📖 Термин</b>\n\n"
            f"<b>{item}</b>\n\n"
            "Дайте <i>определение и исторический контекст</i>."
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Термин: {item}",
            item_index=idx + 1,
        )

    if task_type == TaskKind.PERIOD:
        idx = rng.randrange(len(pool.periods))
        item = pool.periods[idx]
        prompt = (
            f"<b>🗓 Период</b>\n\n"
            f"<b>{item}</b>\n\n"
            "Укажите <i>хронологические рамки</i> и <i>ключевые процессы</i> этого этапа."
        )
        return ExamQuestion(
            task_type=task_type,
            prompt=prompt + _VOICE_HINT,
            block=block,
            context_text=f"Исторический период: {item}",
            item_index=idx + 1,
        )

    raise ValueError(f"Неизвестный тип задания: {task_type}")
