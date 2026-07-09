"""Формат B: спор с семью оппонентами (дебаты)."""
from __future__ import annotations

import random

from .log import enrich_answer_log
from .models import (
    AnswerCheck,
    BossesState,
    ControlMode,
    ControlPool,
    ControlSession,
    PendingQuestion,
    Question,
)


def init_bosses(session: ControlSession, pool: ControlPool, *, roll_seed: int) -> str | None:
    if not pool.bosses:
        return "Режим «Боссы» пока без оппонентов — добавьте bosses в data/control_pool.json."
    session.mode = ControlMode.BOSSES
    session.bosses = BossesState(roll_seed=roll_seed)
    session.phase = "playing"
    return None


def current_boss(pool: ControlPool, state: BossesState):
    if state.boss_index >= len(pool.bosses):
        return None
    return pool.bosses[state.boss_index]


def _rng_for_boss(state: BossesState, boss_id: str) -> random.Random:
    h = hash(boss_id) & 0xFFFFFFFF
    return random.Random((state.roll_seed ^ h) & 0xFFFFFFFF)


def _pick_debate_id(pool: ControlPool, session: ControlSession) -> str | None:
    boss = current_boss(pool, session.bosses)
    if not boss:
        return None
    debate_ids = [qid for qid in boss.debate_ids if qid]
    if not debate_ids:
        return None
    picks = session.bosses.picks
    if boss.id in picks:
        return picks[boss.id]
    qid = _rng_for_boss(session.bosses, boss.id).choice(debate_ids)
    picks[boss.id] = qid
    return qid


def format_boss_hud(pool: ControlPool, session: ControlSession) -> str:
    b = session.bosses
    boss = current_boss(pool, b)
    total = len(pool.bosses)
    lines = [
        "⚔️ <b>Спор с оппонентами</b>",
        f"📊 Оценка: {b.defeated}/{total}",
        f"🗣 Оппонент {b.boss_index + 1}/{total}",
    ]
    if boss:
        lines.append(f"<b>{boss.name}</b> (сем. {boss.seminar})")
        if boss.opponent:
            lines.append(f"Роль: {boss.opponent}")
    return "\n".join(lines)


def begin_boss_question(
    pool: ControlPool, session: ControlSession
) -> tuple[str, Question | None]:
    boss = current_boss(pool, session.bosses)
    if not boss:
        return _finish_bosses(pool, session), None

    qid = _pick_debate_id(pool, session)
    question = pool.questions.get(qid) if qid else None
    if not question:
        session.bosses.boss_index += 1
        if session.bosses.boss_index >= len(pool.bosses):
            return _finish_bosses(pool, session), None
        return begin_boss_question(pool, session)

    session.pending = PendingQuestion(
        question_id=question.id,
        context_label=f"boss:{boss.id}:debate",
    )
    session.phase = "await_open"

    thesis = (question.thesis or question.prompt).strip()
    speaker = boss.opponent or boss.name
    text = (
        format_boss_hud(pool, session)
        + f"\n\n🗣 <b>{speaker}</b> утверждает:\n"
        f"«{thesis}»\n\n"
        "✍️ <i>Парировать тезис: напишите контраргумент (2–5 предложений).</i>"
    )
    return text, question


def apply_boss_answer(
    pool: ControlPool,
    session: ControlSession,
    check: AnswerCheck,
    *,
    student_answer: str = "",
) -> str:
    boss = current_boss(pool, session.bosses)
    qid = session.pending.question_id if session.pending else None
    question = pool.questions.get(qid) if qid else None
    session.log.append(
        enrich_answer_log(
            {
                "mode": "bosses",
                "boss": boss.id if boss else None,
                "question_id": qid,
                "picked": session.bosses.picks.get(boss.id) if boss else None,
                "correct": check.correct,
                "score": check.score,
                "method": check.method.value,
            },
            question=question,
            student_answer=student_answer,
            check=check,
        )
    )

    session.pending = None

    if check.correct:
        session.bosses.defeated += 1
        session.bosses.boss_index += 1
        msg = (
            f"✅ Оппонент отступил! {check.feedback}\n"
            f"💀 +1 к оценке ({session.bosses.defeated}/{len(pool.bosses)})."
        )
    else:
        session.bosses.boss_index += 1
        msg = f"❌ Тезис не опровергнут. {check.feedback}"

    if session.bosses.boss_index >= len(pool.bosses):
        return msg + "\n\n" + _finish_bosses(pool, session)
    return msg + "\n\nНажмите «Далее» для следующего оппонента."


def _finish_bosses(pool: ControlPool, session: ControlSession) -> str:
    session.phase = "finished"
    b = session.bosses
    max_grade = len(pool.bosses)
    grade = b.defeated
    session.passed = True
    session.final_grade = grade
    session.final_grade_max = max_grade
    session.summary = f"Оценка: {grade}/{max_grade}"
    return (
        f"🏁 <b>Диспут завершён</b>\n"
        f"<b>{session.summary}</b>\n"
        "✅ ЗАЧЁТ\n\n"
        "/control — новая попытка"
    )
