"""Формат C: «Кто хочет стать факихом» — 14 вопросов с выбором из 4."""
from __future__ import annotations

import random

from .log import enrich_answer_log
from .models import (
    AnswerCheck,
    ControlMode,
    ControlPool,
    ControlSession,
    MillionaireState,
    PendingQuestion,
    Question,
    QuestionOption,
)


def init_millionaire(
    session: ControlSession, pool: ControlPool, *, roll_seed: int
) -> str | None:
    if not pool.millionaire_steps:
        return (
            "Режим «Факих» пока без ступеней — добавьте millionaire.steps в control_pool.json."
        )
    session.mode = ControlMode.MILLIONAIRE
    session.millionaire = MillionaireState(roll_seed=roll_seed)
    session.phase = "playing"
    return None


def current_step(pool: ControlPool, state: MillionaireState):
    if state.step_index >= len(pool.millionaire_steps):
        return None
    return pool.millionaire_steps[state.step_index]


def _rng_for_step(state: MillionaireState, step_key: str) -> random.Random:
    h = hash(step_key) & 0xFFFFFFFF
    return random.Random((state.roll_seed ^ h) & 0xFFFFFFFF)


def _pick_question_id(pool: ControlPool, session: ControlSession) -> str | None:
    step = current_step(pool, session.millionaire)
    if not step:
        return None
    key = f"step{step.step}"
    picks = session.millionaire.picks
    if key in picks:
        return picks[key]
    pool_ids = [
        qid
        for qid in step.question_pool()
        if qid and pool.questions.get(qid, None)
        and pool.questions[qid].type.value == "choice"
    ]
    used = set(session.millionaire.picks.values())
    fresh = [qid for qid in pool_ids if qid not in used]
    if fresh:
        pool_ids = fresh
    if not pool_ids:
        return step.question_id
    qid = _rng_for_step(session.millionaire, key).choice(pool_ids)
    picks[key] = qid
    return qid


def format_millionaire_hud(pool: ControlPool, session: ControlSession) -> str:
    m = session.millionaire
    step = current_step(pool, m)
    total = len(pool.millionaire_steps)
    step_num = m.step_index + 1
    lines = [
        "📚 <b>Кто хочет стать факихом</b>",
        f"Вопрос {step_num}/{total}",
        f"✅ Верно: {m.correct_count}/{m.step_index}",
    ]
    if step:
        lines.append(f"Семинар {step.seminar}")
    lifelines = []
    if m.lifeline_5050:
        lifelines.append("50:50")
    if m.lifeline_ulema:
        lifelines.append("улем")
    if m.lifeline_retry:
        lifelines.append("повтор")
    lines.append("Подсказки: " + (", ".join(lifelines) if lifelines else "нет"))
    full = pool.millionaire_full_score
    lines.append(f"Максимум оценки при {full}–{total} верных ответах")
    return "\n".join(lines)


def begin_millionaire_question(
    pool: ControlPool, session: ControlSession
) -> tuple[str, Question | None]:
    step = current_step(pool, session.millionaire)
    if not step:
        return _finish_millionaire(pool, session), None

    qid = _pick_question_id(pool, session)
    question = pool.questions.get(qid) if qid else None
    if not question:
        session.millionaire.step_index += 1
        if session.millionaire.step_index >= len(pool.millionaire_steps):
            return _finish_millionaire(pool, session), None
        return begin_millionaire_question(pool, session)

    session.pending = PendingQuestion(
        question_id=question.id,
        context_label=f"millionaire:step{step.step}",
        allow_retry=session.millionaire.lifeline_retry,
        hidden_option_ids=list(session.millionaire.hidden_option_ids),
    )
    session.phase = "playing"

    text = format_millionaire_hud(pool, session) + f"\n\n❓ {question.prompt}"
    return text, question


def visible_options(question: Question, hidden_ids: list[str]) -> list[QuestionOption]:
    hidden = set(hidden_ids)
    return [o for o in question.options if o.id not in hidden]


def apply_5050(session: ControlSession, question: Question) -> str | None:
    m = session.millionaire
    if not m.lifeline_5050:
        return "Подсказка 50:50 уже использована."
    if question.type.value != "choice" or len(question.options) < 4:
        return "50:50 только для вопросов с 4 вариантами."
    wrong = [o for o in question.options if not o.correct]
    if len(wrong) < 2:
        return "Нельзя применить 50:50 к этому вопросу."
    to_hide = random.sample(wrong, 2)
    m.hidden_option_ids = [o.id for o in to_hide]
    m.lifeline_5050 = False
    if session.pending:
        session.pending.hidden_option_ids = list(m.hidden_option_ids)
    return "50:50: убраны два неверных варианта."


def apply_ulema_hint(session: ControlSession, question: Question) -> str | None:
    m = session.millionaire
    if not m.lifeline_ulema:
        return "Подсказка «улем» уже использована."
    m.lifeline_ulema = False
    hint = question.hint.strip() or f"Тема связана с семинаром {question.seminar}."
    return f"📖 Намёк улема: {hint}"


def apply_millionaire_answer(
    pool: ControlPool,
    session: ControlSession,
    check: AnswerCheck,
    *,
    student_answer: str = "",
) -> str:
    step = current_step(pool, session.millionaire)
    qid = session.pending.question_id if session.pending else None
    question = pool.questions.get(qid) if qid else None
    session.log.append(
        enrich_answer_log(
            {
                "mode": "millionaire",
                "step": step.step if step else None,
                "question_id": qid,
                "picked": session.millionaire.picks.get(f"step{step.step}") if step else None,
                "retry": bool(session.pending and session.pending.retry_used),
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
        session.millionaire.correct_count += 1
        session.millionaire.step_index += 1
        session.millionaire.hidden_option_ids = []
        session.pending = None
        if session.millionaire.step_index >= len(pool.millionaire_steps):
            return _finish_millionaire(pool, session)
        next_num = session.millionaire.step_index + 1
        return (
            f"✅ Верно! {check.feedback}\n"
            f"Счёт: {session.millionaire.correct_count}/{session.millionaire.step_index}. "
            f"Вопрос {next_num}.\n\nНажмите «Далее»."
        )

    pending = session.pending
    if (
        pending
        and pending.allow_retry
        and not pending.retry_used
        and session.millionaire.lifeline_retry
    ):
        pending.retry_used = True
        session.millionaire.lifeline_retry = False
        return (
            f"❌ {check.feedback}\n"
            "🔄 Вторая попытка (подсказка «повтор») — ответьте ещё раз."
        )

    session.millionaire.step_index += 1
    session.millionaire.hidden_option_ids = []
    session.pending = None
    answered = session.millionaire.step_index
    total = len(pool.millionaire_steps)
    msg = f"❌ {check.feedback}\nСчёт: {session.millionaire.correct_count}/{answered}."
    if session.millionaire.step_index >= total:
        return msg + "\n\n" + _finish_millionaire(pool, session)
    return msg + f"\n\nВопрос {answered + 1}/{total}. Нажмите «Далее»."


def _finish_millionaire(pool: ControlPool, session: ControlSession) -> str:
    session.phase = "finished"
    m = session.millionaire
    total = len(pool.millionaire_steps)
    correct = m.correct_count
    session.passed = True
    session.final_grade = correct
    session.final_grade_max = total
    session.summary = f"Оценка: {correct}/{total}"
    full_note = ""
    if correct >= pool.millionaire_full_score:
        full_note = "\n🏆 Полный балл!"
    elif correct >= total - 1:
        full_note = "\nПочти максимум — отличный результат."
    return (
        f"🏁 <b>Финиш!</b>\n"
        f"<b>{session.summary}</b>{full_note}\n"
        "✅ ЗАЧЁТ\n\n/control — новая попытка"
    )
