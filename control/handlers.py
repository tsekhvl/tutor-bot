"""Telegram: /control и интерактивные три формата."""
from __future__ import annotations

import json
import logging
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from config import (
    GOOGLE_CREDENTIALS_PATH,
    SHEET_BLOCK_1_ID,
    TUTOR_SQLITE_ENABLED,
    TUTOR_SQLITE_PATH,
)
from sheets import get_client, normalize_control_grade_for_sheet, write_control_grade
from storage import insert_submission

from .checker import check_answer
from .engine_bosses import apply_boss_answer, begin_boss_question, init_bosses
from .engine_caravan import apply_caravan_answer, begin_stop_question, init_caravan
from .engine_millionaire import (
    apply_5050,
    apply_millionaire_answer,
    apply_ulema_hint,
    begin_millionaire_question,
    init_millionaire,
    visible_options,
)
from .models import ControlMode, ControlSession, Question, QuestionType
from .pool import get_control_pool
from .session import clear_session, get_session, set_session

logger = logging.getLogger(__name__)

# callback prefixes
CB_MODE = "ctl:m:"
CB_CHOICE = "ctl:ch:"
CB_NEXT = "ctl:next"
CB_ML = "ctl:ml:"


def _clear_homework_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ["state", "block", "fio", "type", "seminar", "assignment_key"]:
        context.user_data.pop(key, None)


def _control_intro_text() -> str:
    return (
        "🎮 <b>Интерактивная контрольная</b>\n\n"
        "Демо-пул (публичный репозиторий): укороченный набор вопросов "
        "по истории Ближнего Востока. В проде подключается полный курс.\n"
        "Оценка <b>0–7</b> может писаться в Google Sheets (если заданы ID таблиц). "
        "Пересдача сохраняет <b>лучший</b> результат.\n\n"
        "Три формата:\n\n"
        "🏜 <b>Караван</b> — открытые ответы, остановки-города, fallback-вопрос\n"
        "⚔️ <b>Боссы</b> — дебаты: опровергнуть тезис оппонента (Gemini)\n"
        "📚 <b>Факих</b> — тесты с подсказками 50:50 / улем / повтор\n\n"
        "Напишите <b>ФИО</b> одним сообщением (для журнала / таблицы):"
    )


def _control_mode_picker_text() -> str:
    return (
        "Как будете сдавать?\n\n"
        "🏜 <b>Караван</b> — ответы текстом, 7 остановок\n"
        "⚔️ <b>Боссы</b> — спор: опровергнуть чужое утверждение\n"
        "📚 <b>Факих</b> — 14 вопросов с кнопками и подсказками"
    )


def _mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏜 Караван", callback_data=f"{CB_MODE}caravan")],
            [InlineKeyboardButton("⚔️ Боссы", callback_data=f"{CB_MODE}bosses")],
            [
                InlineKeyboardButton(
                    "📚 Кто хочет стать факихом", callback_data=f"{CB_MODE}millionaire"
                )
            ],
        ]
    )


def _millionaire_extras_keyboard(pool, session: ControlSession) -> list[list[InlineKeyboardButton]]:
    m = session.millionaire
    row1 = []
    if m.lifeline_5050:
        row1.append(InlineKeyboardButton("50:50", callback_data=f"{CB_ML}50"))
    if m.lifeline_ulema:
        row1.append(InlineKeyboardButton("📖 Улем", callback_data=f"{CB_ML}ulema"))
    rows: list[list[InlineKeyboardButton]] = []
    if row1:
        rows.append(row1)
    return rows


def _playing_keyboard(
    pool, session: ControlSession, question: Question | None
) -> InlineKeyboardMarkup | None:
    if question and question.type == QuestionType.CHOICE:
        hidden = []
        if session.pending:
            hidden = session.pending.hidden_option_ids
        rows = [
            [
                InlineKeyboardButton(
                    o.text[:64], callback_data=f"{CB_CHOICE}{question.id}:{o.id}"
                )
            ]
            for o in visible_options(question, hidden)
        ]
        if session.mode == ControlMode.MILLIONAIRE:
            rows.extend(_millionaire_extras_keyboard(pool, session))
        rows.append([InlineKeyboardButton("➡️ Далее", callback_data=CB_NEXT)])
        return InlineKeyboardMarkup(rows)
    if session.mode == ControlMode.MILLIONAIRE:
        extra = _millionaire_extras_keyboard(pool, session)
        if extra:
            extra.append([InlineKeyboardButton("➡️ Далее", callback_data=CB_NEXT)])
            return InlineKeyboardMarkup(extra)
    return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Далее", callback_data=CB_NEXT)]])


async def cmd_control(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    _clear_homework_state(context)
    stash_user_context(context, update)
    session = ControlSession(phase="await_fio")
    set_session(context, session)
    await msg.reply_text(_control_intro_text(), parse_mode="HTML")


async def control_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Обработка текста в сессии control. True если съели сообщение."""
    session = get_session(context)
    if not session or session.phase == "finished":
        return False

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Отправьте текст.")
        return True

    if session.phase == "await_fio":
        stash_user_context(context, update)
        session.fio = text
        session.phase = "await_mode"
        set_session(context, session)
        await update.message.reply_text(
            _control_mode_picker_text(),
            parse_mode="HTML",
            reply_markup=_mode_keyboard(),
        )
        return True

    if session.phase == "await_open" and session.pending:
        pool = get_control_pool()
        question = pool.questions.get(session.pending.question_id)
        if not question:
            await update.message.reply_text("Вопрос не найден в пуле.")
            return True
        await update.message.reply_chat_action(ChatAction.TYPING)
        try:
            check = await check_answer(question, text)
        except Exception as e:
            logger.exception("control check open")
            await update.message.reply_text(f"Ошибка проверки: {type(e).__name__}: {e}")
            return True
        await _apply_check_and_reply(update, context, session, pool, check, text)
        return True

    if session.phase in ("playing", "await_mode"):
        await update.message.reply_text("Используйте кнопки или /control для перезапуска.")
        return True

    return False


async def control_handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    stash_user_context(context, update)

    session = get_session(context)
    if not session:
        await query.answer()
        await query.edit_message_text("Сессия истекла. /control")
        return

    data = query.data
    pool = get_control_pool()

    if data.startswith(CB_MODE):
        await query.answer()
        mode_key = data[len(CB_MODE) :]
        err = _start_mode(session, pool, mode_key, context)
        if err:
            await query.edit_message_text(err)
            return
        text, question = _begin_current_question(pool, session)
        set_session(context, session)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=_playing_keyboard(pool, session, question),
        )
        return

    if data.startswith(CB_CHOICE):
        payload = data[len(CB_CHOICE) :]
        if ":" not in payload:
            await query.answer()
            return
        qid, oid = payload.split(":", 1)
        question = pool.questions.get(qid)
        if not question:
            await query.answer("Вопрос не найден", show_alert=True)
            return
        await query.answer()
        await query.message.reply_chat_action(ChatAction.TYPING)
        check = await check_answer(question, oid)
        await _apply_check_callback(query, context, session, pool, check, oid)
        return

    if data == CB_NEXT:
        await query.answer()
        text, question = _begin_current_question(pool, session)
        if session.phase == "finished":
            await query.message.reply_text(text, parse_mode="HTML")
        else:
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=_playing_keyboard(pool, session, question),
            )
        set_session(context, session)
        return

    if data.startswith(CB_ML) and session.mode == ControlMode.MILLIONAIRE:
        code = data[len(CB_ML) :]
        question = None
        if session.pending:
            question = pool.questions.get(session.pending.question_id)
        if not question:
            await query.answer("Сначала начните вопрос", show_alert=True)
            return
        if code == "50":
            msg = apply_5050(session, question)
        elif code == "ulema":
            msg = apply_ulema_hint(session, question)
        else:
            msg = "Неизвестная подсказка"
        set_session(context, session)
        if code == "ulema" and msg and not msg.startswith("Подсказка"):
            await query.answer("Подсказка улема")
            await query.message.reply_text(msg, parse_mode="HTML")
        else:
            await query.answer(msg or "Готово", show_alert=True)
        if code in ("50", "ulema") and msg and not msg.startswith(
            ("Нельзя", "Подсказка", "50:50 только")
        ):
            try:
                await query.message.edit_reply_markup(
                    reply_markup=_playing_keyboard(pool, session, question)
                )
            except Exception:
                logger.debug("edit_reply_markup after lifeline", exc_info=True)
        return

def _control_roll_seed(context: ContextTypes.DEFAULT_TYPE) -> int:
    """Случайный набор вопросов на каждую новую попытку."""
    _ = context
    return secrets.randbits(63)


def _start_mode(
    session: ControlSession, pool, mode_key: str, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    try:
        mode = ControlMode(mode_key)
    except ValueError:
        return "Неизвестный формат."
    session.mode = mode
    if mode == ControlMode.CARAVAN:
        return init_caravan(session, pool, roll_seed=_control_roll_seed(context))
    if mode == ControlMode.BOSSES:
        return init_bosses(session, pool, roll_seed=_control_roll_seed(context))
    return init_millionaire(session, pool, roll_seed=_control_roll_seed(context))


def _begin_current_question(pool, session: ControlSession) -> tuple[str, Question | None]:
    if session.mode == ControlMode.CARAVAN:
        return begin_stop_question(pool, session)
    if session.mode == ControlMode.BOSSES:
        return begin_boss_question(pool, session)
    return begin_millionaire_question(pool, session)


async def _apply_check_and_reply(
    update, context, session: ControlSession, pool, check, student_answer: str
) -> None:
    msg = _dispatch_apply(pool, session, check, student_answer)
    set_session(context, session)
    if session.phase == "finished":
        await update.message.reply_text(msg, parse_mode="HTML")
        await _finalize_control_message(update, context, session)
        return
    await update.message.reply_text(msg, parse_mode="HTML")
    await _post_answer_followup(update.message, context, session, pool)


async def _post_answer_followup(message, context, session: ControlSession, pool) -> None:
    if session.phase == "finished":
        return
    if session.mode == ControlMode.CARAVAN and session.caravan.awaiting_fallback:
        text, question = _begin_current_question(pool, session)
        set_session(context, session)
        await message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=_playing_keyboard(pool, session, question),
        )
        return
    if session.pending:
        q = pool.questions.get(session.pending.question_id)
        if q and q.type == QuestionType.CHOICE:
            await message.reply_text(
                "Выберите вариант:",
                reply_markup=_playing_keyboard(pool, session, q),
            )


async def _apply_check_callback(
    query, context, session, pool, check, student_answer: str
) -> None:
    msg = _dispatch_apply(pool, session, check, student_answer)
    set_session(context, session)
    await query.message.reply_text(msg, parse_mode="HTML")
    if session.phase == "finished":
        await _finalize_control(query, context, session)
    else:
        await _post_answer_followup(query.message, context, session, pool)


def _dispatch_apply(pool, session: ControlSession, check, student_answer: str = ""):
    if session.mode == ControlMode.CARAVAN:
        return apply_caravan_answer(pool, session, check, student_answer=student_answer)
    if session.mode == ControlMode.BOSSES:
        return apply_boss_answer(pool, session, check, student_answer=student_answer)
    return apply_millionaire_answer(pool, session, check, student_answer=student_answer)


async def _finalize_control(query, context, session: ControlSession) -> None:
    note = await _log_control_result(context, session)
    if note and query.message:
        await query.message.reply_text(note)


async def _finalize_control_message(update, context, session: ControlSession) -> None:
    stash_user_context(context, update)
    note = await _log_control_result(context, session)
    if note and update.effective_message:
        await update.effective_message.reply_text(note)


def _write_control_grade_to_sheet(session: ControlSession) -> tuple[bool, str | None]:
    """Запись в столбец U. Возвращает (успех, сообщение для студента)."""
    if session.final_grade is None:
        return False, None
    if not SHEET_BLOCK_1_ID or not GOOGLE_CREDENTIALS_PATH:
        return False, None
    sheet_grade = normalize_control_grade_for_sheet(
        session.final_grade, session.final_grade_max
    )
    try:
        client = get_client(GOOGLE_CREDENTIALS_PATH)
        stored, updated = write_control_grade(
            client=client,
            spreadsheet_id=SHEET_BLOCK_1_ID,
            fio=session.fio or "—",
            grade=sheet_grade,
        )
    except Exception as e:
        logger.exception("control write_control_grade")
        return False, f"⚠️ Оценка не записана в таблицу:\n{type(e).__name__}: {e}"

    attempt_note = str(sheet_grade)
    if (
        session.final_grade_max
        and session.final_grade_max > 7
        and session.final_grade is not None
    ):
        attempt_note = (
            f"{session.final_grade}/{session.final_grade_max} → {sheet_grade}/7"
        )
    elif session.final_grade_max:
        attempt_note = f"{session.final_grade}/{session.final_grade_max}"

    if not updated:
        return True, (
            f"📊 В таблице уже стоит {stored}/7 (попытка: {attempt_note}) — "
            "более высокий балл не перезаписываем."
        )
    return True, f"📊 Оценка {stored}/7 записана в таблицу (столбец U)."


async def _log_control_result(context, session: ControlSession) -> str | None:
    if session.passed is None:
        return None

    sheet_written = False
    sheet_note: str | None = None
    if session.final_grade is not None:
        sheet_written, sheet_note = _write_control_grade_to_sheet(session)

    if TUTOR_SQLITE_ENABLED:
        tid = context.user_data.get("_control_telegram_id")
        tuser = context.user_data.get("_control_username")
        tname = context.user_data.get("_control_first_name")
        if tid is not None:
            try:
                await insert_submission(
                    TUTOR_SQLITE_PATH,
                    telegram_user_id=tid,
                    telegram_username=tuser,
                    telegram_first_name=tname,
                    fio=session.fio or "—",
                    block="1",
                    assignment_type="контрольная",
                    seminar="9-15",
                    assignment_key=session.mode.value if session.mode else "control",
                    accepted=bool(session.passed),
                    student_answer=json.dumps(
                        {
                            "mode": session.mode.value if session.mode else None,
                            "final_grade": session.final_grade,
                            "final_grade_max": session.final_grade_max,
                            "answers": session.log,
                        },
                        ensure_ascii=False,
                    ),
                    bot_message=session.summary,
                    sheet_written=sheet_written,
                )
            except Exception:
                logger.exception("control insert_submission")

    return sheet_note


def stash_user_context(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    u = update.effective_user
    if u:
        context.user_data["_control_telegram_id"] = u.id
        context.user_data["_control_username"] = u.username
        context.user_data["_control_first_name"] = u.first_name
