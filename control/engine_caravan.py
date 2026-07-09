"""Формат A: караван с печатями."""
from __future__ import annotations

import random

from .log import enrich_answer_log
from .models import (
    AnswerCheck,
    CaravanState,
    CaravanStop,
    ControlMode,
    ControlPool,
    ControlSession,
    PendingQuestion,
    Question,
    QuestionType,
)


def init_caravan(session: ControlSession, pool: ControlPool, *, roll_seed: int) -> str | None:
    """Старт режима. None если ок, иначе текст ошибки."""
    if not pool.caravan_stops:
        return "Режим «Караван» пока без остановок — добавьте stops в data/control_pool.json."
    session.mode = ControlMode.CARAVAN
    session.caravan = CaravanState(roll_seed=roll_seed)
    session.phase = "playing"
    return None


def _rng_for_stop(state: CaravanState, stop_id: str) -> random.Random:
    h = hash(stop_id) & 0xFFFFFFFF
    return random.Random((state.roll_seed ^ h) & 0xFFFFFFFF)


def _open_ids(pool: ControlPool, ids: list[str]) -> list[str]:
    return [
        qid
        for qid in ids
        if (q := pool.questions.get(qid)) and q.type == QuestionType.OPEN
    ]


def _ensure_stop_pick(
    pool: ControlPool, session: ControlSession, stop: CaravanStop
) -> None:
    if stop.id in session.caravan.picks:
        return
    rng = _rng_for_stop(session.caravan, stop.id)
    main_pool = _open_ids(pool, stop.main_pool())
    fb_pool = _open_ids(pool, stop.fallback_pool())
    main_id = rng.choice(main_pool) if main_pool else None
    fb_id = rng.choice(fb_pool) if fb_pool else None
    if fb_id == main_id and len(fb_pool) > 1:
        alt = [x for x in fb_pool if x != main_id]
        fb_id = rng.choice(alt)
    session.caravan.picks[stop.id] = {"main": main_id, "fallback": fb_id}


def _resolved_qid(
    pool: ControlPool, session: ControlSession, stop: CaravanStop
) -> str | None:
    _ensure_stop_pick(pool, session, stop)
    pick = session.caravan.picks.get(stop.id, {})
    if session.caravan.awaiting_fallback:
        return pick.get("fallback")
    return pick.get("main")


def _has_fallback(
    pool: ControlPool, stop: CaravanStop, session: ControlSession
) -> bool:
    _ensure_stop_pick(pool, session, stop)
    return bool(session.caravan.picks.get(stop.id, {}).get("fallback"))


def _resolve_question(pool: ControlPool, qid: str | None) -> Question | None:
    if not qid:
        return None
    return pool.questions.get(qid)


def current_stop(pool: ControlPool, state: CaravanState):
    if state.stop_index >= len(pool.caravan_stops):
        return None
    return pool.caravan_stops[state.stop_index]


def format_stop_intro(pool: ControlPool, session: ControlSession) -> str:
    stop = current_stop(pool, session.caravan)
    if not stop:
        return ""
    lines = [
        f"🏜 <b>Караван</b> — остановка {session.caravan.stop_index + 1}/{len(pool.caravan_stops)}",
        f"📍 {stop.city} (семинар {stop.seminar})",
        f"📊 Баллы: {session.caravan.seals}/{len(pool.caravan_stops)}",
    ]
    if stop.event_text:
        lines.append("")
        lines.append(stop.event_text)
    return "\n".join(lines)


def begin_stop_question(
    pool: ControlPool, session: ControlSession
) -> tuple[str, Question | None]:
    """Назначить вопрос текущей остановки. Возвращает (сообщение, вопрос)."""
    stop = current_stop(pool, session.caravan)
    if not stop:
        return _finish_caravan(pool, session), None

    qid = _resolved_qid(pool, session, stop)
    question = _resolve_question(pool, qid)
    if not question:
        session.caravan.awaiting_fallback = False
        session.caravan.stop_index += 1
        if session.caravan.stop_index >= len(pool.caravan_stops):
            return _finish_caravan(pool, session), None
        return begin_stop_question(pool, session)

    label = "запасной вопрос" if session.caravan.awaiting_fallback else "остановка"
    session.pending = PendingQuestion(
        question_id=question.id,
        context_label=f"caravan:{stop.id}:{label}",
    )
    if question.type.value == "open":
        session.phase = "await_open"
    else:
        session.phase = "playing"

    header = format_stop_intro(pool, session)
    body = f"\n\n❓ {question.prompt}"
    if question.type.value == "open":
        body += "\n\n<i>Напишите ответ текстом.</i>"
    return header + body, question


def apply_caravan_answer(
    pool: ControlPool,
    session: ControlSession,
    check: AnswerCheck,
    *,
    student_answer: str = "",
) -> str:
    """Обработать результат ответа, вернуть сообщение для пользователя."""
    stop = current_stop(pool, session.caravan)
    if not stop:
        return _finish_caravan(pool, session)

    qid = session.pending.question_id if session.pending else None
    question = pool.questions.get(qid) if qid else None
    session.log.append(
        enrich_answer_log(
            {
                "mode": "caravan",
                "stop": stop.id,
                "question_id": qid,
                "picked": session.caravan.picks.get(stop.id),
                "fallback": session.caravan.awaiting_fallback,
                "correct": check.correct,
                "score": check.score,
                "method": check.method.value,
            },
            question=question,
            student_answer=student_answer,
            check=check,
        )
    )

    if check.correct:
        via_fallback = session.caravan.awaiting_fallback
        session.caravan.seals += 1
        if not via_fallback:
            session.caravan.seals_opportunities += 1
        session.caravan.awaiting_fallback = False
        session.caravan.stop_index += 1
        session.pending = None
        msg = f"✅ {check.feedback}"
        if via_fallback:
            msg += "\n+1 балл (упрощённый вопрос — дозаработали за остановку)."
        else:
            msg += "\n+1 балл."
        if session.caravan.stop_index >= len(pool.caravan_stops):
            return msg + "\n\n" + _finish_caravan(pool, session)
        return msg + "\n\nНажмите «Далее» для следующей остановки."
    # неверно
    if not session.caravan.awaiting_fallback and _has_fallback(pool, stop, session):
        session.caravan.awaiting_fallback = True
        session.pending = None
        return (
            f"❌ {check.feedback}\n\n"
            "⏳ Задержка в пустыне — упрощённый вопрос. "
            "За правильный ответ можно получить балл за эту остановку."
        )
    session.caravan.awaiting_fallback = False
    session.caravan.stop_index += 1
    session.pending = None
    msg = f"❌ {check.feedback}\nБалл за остановку не получен."
    if session.caravan.stop_index >= len(pool.caravan_stops):
        return msg + "\n\n" + _finish_caravan(pool, session)
    return msg + "\n\nНажмите «Далее»."


def _finish_caravan(pool: ControlPool, session: ControlSession) -> str:
    session.phase = "finished"
    max_grade = len(pool.caravan_stops)
    grade = session.caravan.seals
    session.passed = True
    session.final_grade = grade
    session.final_grade_max = max_grade
    session.summary = f"Оценка: {grade}/{max_grade}"
    return (
        f"🏁 <b>Караван завершён</b>\n"
        f"<b>{session.summary}</b>\n"
        "✅ ЗАЧЁТ\n\n"
        "Новая попытка: /control"
    )
