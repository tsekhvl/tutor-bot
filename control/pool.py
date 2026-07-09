"""Загрузка пула вопросов и сценариев контрольной."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .models import (
    BossDef,
    CaravanStop,
    ControlPool,
    MillionaireStep,
    Question,
    QuestionOption,
    QuestionType,
)

logger = logging.getLogger(__name__)

DEFAULT_POOL_PATH = Path(__file__).resolve().parent.parent / "data" / "control_pool.json"


def _parse_question(raw: dict) -> Question:
    qtype = QuestionType(str(raw.get("type", "choice")).lower())
    options = [
        QuestionOption(
            id=str(o["id"]),
            text=str(o.get("text", "")),
            correct=bool(o.get("correct")),
        )
        for o in (raw.get("options") or [])
    ]
    return Question(
        id=str(raw["id"]),
        seminar=str(raw.get("seminar", "")),
        type=qtype,
        prompt=str(raw.get("prompt", "")),
        options=options,
        rubric=str(raw.get("rubric", "")),
        hint=str(raw.get("hint", "")),
        thesis=str(raw.get("thesis", "")),
        difficulty=int(raw.get("difficulty", 1)),
        tags=[str(t) for t in (raw.get("tags") or [])],
    )


def load_control_pool(path: Path | None = None) -> ControlPool:
    pool_path = path or DEFAULT_POOL_PATH
    if not pool_path.is_file():
        logger.warning("control_pool не найден: %s", pool_path)
        return ControlPool(
            questions={},
            caravan_stops=[],
            caravan_pass_min=0,
            bosses=[],
            boss_hp_start=0,
            boss_pass_defeated=0,
            millionaire_steps=[],
            millionaire_full_score=13,
        )

    with open(pool_path, encoding="utf-8") as f:
        data = json.load(f)

    questions = {
        str(q["id"]): _parse_question(q) for q in (data.get("questions") or [])
    }

    caravan_raw = data.get("caravan") or {}
    caravan_stops = []
    for s in caravan_raw.get("stops") or []:
        caravan_stops.append(
            CaravanStop(
                id=str(s["id"]),
                city=str(s.get("city", "")),
                seminar=str(s.get("seminar", "")),
                question_id=s.get("question_id"),
                event_text=str(s.get("event_text", "")),
                fallback_question_id=s.get("fallback_question_id"),
                question_ids=[str(x) for x in (s.get("question_ids") or [])],
                fallback_question_ids=[
                    str(x) for x in (s.get("fallback_question_ids") or [])
                ],
            )
        )

    bosses_raw = data.get("bosses_config") or {}
    boss_list = data.get("bosses") or []
    if isinstance(boss_list, dict):
        bosses_raw = boss_list
        boss_list = bosses_raw.get("items") or []

    bosses = [
        BossDef(
            id=str(b["id"]),
            seminar=str(b.get("seminar", "")),
            name=str(b.get("name", "")),
            opponent=str(b.get("opponent", "")),
            debate_ids=[str(x) for x in (b.get("debate_ids") or [])],
        )
        for b in boss_list
    ]

    millionaire_raw = data.get("millionaire") or {}
    millionaire_steps = [
        MillionaireStep(
            step=int(s.get("step", 0)),
            seminar=str(s.get("seminar", "")),
            question_id=s.get("question_id"),
            question_ids=[str(x) for x in (s.get("question_ids") or [])],
        )
        for s in (millionaire_raw.get("steps") or [])
    ]

    return ControlPool(
        questions=questions,
        caravan_stops=caravan_stops,
        caravan_pass_min=int(
            caravan_raw.get("pass_min", caravan_raw.get("pass_seals", 4))
        ),
        bosses=bosses,
        boss_hp_start=int(bosses_raw.get("hp_start", 5)),
        boss_pass_defeated=int(bosses_raw.get("pass_defeated", 4)),
        millionaire_steps=millionaire_steps,
        millionaire_full_score=int(millionaire_raw.get("full_score_at", 13)),
    )


_pool_cache: ControlPool | None = None


def get_control_pool(reload: bool = False) -> ControlPool:
    global _pool_cache
    if _pool_cache is None or reload:
        _pool_cache = load_control_pool()
    return _pool_cache
