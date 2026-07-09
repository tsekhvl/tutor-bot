"""Telegram: /exam — тренажёр к устному экзамену."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from config import TUTOR_SQLITE_ENABLED, TUTOR_SQLITE_PATH
from storage import insert_submission

from .coach import generate_followup_sync, generate_review_sync, transcribe_audio_sync
from .models import ExamTrainSession, TaskKind
from .pool import load_block_pool, pick_question
from .session import get_session, set_session

logger = logging.getLogger(__name__)

CB_BLK = "exm:blk:"
CB_TYPE = "exm:type:"
CB_AGAIN = "exm:again"
CB_MENU = "exm:menu"


def _intro_text() -> str:
    return (
        "🎓 <b>Тренажёр к экзамену</b>\n\n"
        "Потренируйтесь отвечать как на устном экзамене по ИРС.\n\n"
        "После вашего ответа бот задаст <b>уточняющий вопрос</b>, как на комиссии, "
        "а затем пришлёт <b>разбор</b>: что хорошо, что упущено, что подтянуть.\n\n"
        "Отвечать можно <b>текстом</b> или <b>голосовым</b> — как на экзамене с микрофоном.\n\n"
        "Выберите блок:"
    )


_TASK_LABELS: dict[TaskKind, tuple[str, str]] = {
    TaskKind.TOPIC: ("📖 Топик", "topic"),
    TaskKind.TERMS: ("📚 Термины (2 шт.)", "terms"),
    TaskKind.ESSAY: ("💭 Рассуждение", "essay"),
    TaskKind.DATE: ("📅 Даты", "date"),
    TaskKind.PERSONALITY: ("👤 Персоналии", "personality"),
    TaskKind.TERM: ("📖 Термин", "term"),
    TaskKind.PERIOD: ("🗓 Период", "period"),
}


def _task_type_text(pool) -> str:
    lines = [f"<b>{pool.title}</b>\n", "Что тренируем?\n"]
    hints = {
        TaskKind.TOPIC: "— рассказ по теме (~5 минут)",
        TaskKind.TERMS: "— определить два термина",
        TaskKind.ESSAY: "— ответ с аргументацией",
        TaskKind.DATE: "— когда, что случилось и почему важно",
        TaskKind.PERSONALITY: "— кто такой, чем известен, период",
        TaskKind.TERM: "— определение и исторический контекст",
        TaskKind.PERIOD: "— хронология и ключевые процессы",
    }
    for kind in pool.available_task_kinds():
        label, _ = _TASK_LABELS[kind]
        lines.append(f"{label} {hints.get(kind, '')}")
    if pool.student_hint:
        lines.append(f"\n<i>{pool.student_hint}</i>")
    elif pool.block == "1":
        lines.append(
            "\n<i>На экзамене в билете будут и доисламская, и исламская часть.</i>"
        )
    return "\n".join(lines)


def _block_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("1 блок", callback_data=f"{CB_BLK}1")],
            [
                InlineKeyboardButton(
                    "3 блок — арабская часть", callback_data=f"{CB_BLK}3"
                )
            ],
        ]
    )


def _task_type_keyboard(pool) -> InlineKeyboardMarkup:
    rows = []
    for kind in pool.available_task_kinds():
        label, key = _TASK_LABELS[kind]
        rows.append([InlineKeyboardButton(label, callback_data=f"{CB_TYPE}{key}")])
    return InlineKeyboardMarkup(rows)


def _after_review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔁 Ещё задание", callback_data=CB_AGAIN)],
            [InlineKeyboardButton("📋 Другой тип", callback_data=CB_MENU)],
        ]
    )


def _clear_other_flows(context: ContextTypes.DEFAULT_TYPE) -> None:
    from control.session import clear_session

    clear_session(context)
    for key in ["state", "block", "fio", "type", "seminar", "assignment_key"]:
        context.user_data.pop(key, None)


def _stash_user(context: ContextTypes.DEFAULT_TYPE, update: Update) -> None:
    u = update.effective_user
    if u:
        context.user_data["_exam_telegram_id"] = u.id
        context.user_data["_exam_username"] = u.username
        context.user_data["_exam_first_name"] = u.first_name


async def cmd_exam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    _clear_other_flows(context)
    _stash_user(context, update)
    session = ExamTrainSession(phase="await_block")
    set_session(context, session)
    await msg.reply_text(_intro_text(), parse_mode="HTML", reply_markup=_block_keyboard())


async def exam_handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    session = get_session(context)
    if not session or session.phase in ("await_block", "await_task_type", "finished"):
        if session and session.phase in ("await_block", "await_task_type"):
            await update.message.reply_text("Выберите вариант кнопкой ниже.")
            return True
        return False

    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Отправьте текст или голосовое.")
        return True

    if session.phase == "await_main_answer":
        await _accept_main_answer(update, context, session, text, source="text")
        return True
    if session.phase == "await_followup_answer":
        await _accept_followup_answer(update, context, session, text, source="text")
        return True
    return False


async def exam_handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    session = get_session(context)
    if not session or session.phase not in ("await_main_answer", "await_followup_answer"):
        return False

    voice = update.message.voice if update.message else None
    if not voice:
        return False

    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = bytes(await file.download_as_bytearray())
        text = await asyncio.to_thread(transcribe_audio_sync, audio_bytes)
    except Exception as e:
        logger.exception("exam transcribe")
        await update.message.reply_text(
            f"Не удалось распознать голос: {type(e).__name__}. Попробуйте текстом."
        )
        return True

    if len(text) < 5:
        await update.message.reply_text(
            "Речь не распознана или слишком короткая. Повторите голосом или напишите текстом."
        )
        return True

    preview = text if len(text) <= 500 else text[:500] + "…"
    await update.message.reply_text(f"🎤 Распознано:\n<i>{preview}</i>", parse_mode="HTML")

    if session.phase == "await_main_answer":
        await _accept_main_answer(update, context, session, text, source="voice")
    else:
        await _accept_followup_answer(update, context, session, text, source="voice")
    return True


async def exam_handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    _stash_user(context, update)
    session = get_session(context)
    if not session:
        await query.answer()
        await query.edit_message_text("Сессия истекла. /exam")
        return

    data = query.data

    if data.startswith(CB_BLK):
        await query.answer()
        block = data[len(CB_BLK) :]
        pool = load_block_pool(block)
        if not pool:
            await query.edit_message_text("Пул заданий для этого блока не найден.")
            return
        session.block = block
        session.phase = "await_task_type"
        set_session(context, session)
        await query.edit_message_text(
            _task_type_text(pool),
            parse_mode="HTML",
            reply_markup=_task_type_keyboard(pool),
        )
        return

    if data.startswith(CB_TYPE):
        await query.answer()
        type_key = data[len(CB_TYPE) :]
        try:
            task_type = TaskKind(type_key)
        except ValueError:
            return
        pool = load_block_pool(session.block)
        if not pool:
            await query.message.reply_text("Пул не найден. /exam")
            return
        rng = random.Random(secrets.randbits(63))
        try:
            question = pick_question(pool, task_type, rng=rng)
        except ValueError as e:
            await query.message.reply_text(str(e))
            return
        session.task_type = task_type
        session.question = question
        session.main_answer = ""
        session.followup_question = ""
        session.followup_answer = ""
        session.phase = "await_main_answer"
        set_session(context, session)
        await query.message.reply_text(question.prompt, parse_mode="HTML")
        return

    if data == CB_MENU:
        await query.answer()
        pool = load_block_pool(session.block)
        if not pool:
            return
        session.phase = "await_task_type"
        session.question = None
        set_session(context, session)
        await query.message.reply_text(
            _task_type_text(pool),
            parse_mode="HTML",
            reply_markup=_task_type_keyboard(pool),
        )
        return

    if data == CB_AGAIN and session.block:
        await query.answer()
        pool = load_block_pool(session.block)
        if not pool:
            return
        if not session.task_type:
            session.phase = "await_task_type"
            set_session(context, session)
            await query.message.reply_text(
                _task_type_text(pool),
                parse_mode="HTML",
                reply_markup=_task_type_keyboard(pool),
            )
            return
        rng = random.Random(secrets.randbits(63))
        question = pick_question(pool, session.task_type, rng=rng)
        session.question = question
        session.main_answer = ""
        session.followup_question = ""
        session.followup_answer = ""
        session.phase = "await_main_answer"
        set_session(context, session)
        await query.message.reply_text(question.prompt, parse_mode="HTML")
        return


async def _accept_main_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: ExamTrainSession,
    text: str,
    *,
    source: str,
) -> None:
    if not session.question:
        return
    session.main_answer = text
    session.main_answer_source = source
    set_session(context, session)
    pool = load_block_pool(session.block)
    hint = pool.student_hint if pool else ""
    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        followup = await asyncio.to_thread(
            generate_followup_sync,
            session.question,
            text,
            student_hint=hint,
        )
    except Exception as e:
        logger.exception("exam followup")
        await update.message.reply_text(
            f"Не удалось сформировать уточняющий вопрос: {type(e).__name__}. Попробуйте /exam"
        )
        return
    session.followup_question = followup
    session.phase = "await_followup_answer"
    set_session(context, session)
    await update.message.reply_text(
        f"🎤 <b>Уточняющий вопрос комиссии:</b>\n{followup}\n\n"
        "Ответьте текстом или голосовым.",
        parse_mode="HTML",
    )


async def _accept_followup_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    session: ExamTrainSession,
    text: str,
    *,
    source: str,
) -> None:
    if not session.question:
        return
    session.followup_answer = text
    session.followup_answer_source = source
    set_session(context, session)
    pool = load_block_pool(session.block)
    hint = pool.student_hint if pool else ""
    await update.message.reply_text("⏳ Готовлю разбор ответа…")
    await update.message.reply_chat_action(ChatAction.TYPING)
    try:
        review = await asyncio.to_thread(
            generate_review_sync,
            session.question,
            session.main_answer,
            session.followup_question,
            text,
            student_hint=hint,
        )
    except Exception as e:
        logger.exception("exam review")
        await update.message.reply_text(
            f"Не удалось сделать разбор: {type(e).__name__}. Попробуйте ещё раз /exam"
        )
        return

    session.log.append(
        {
            "block": session.block,
            "task_type": session.task_type.value if session.task_type else None,
            "context": session.question.context_text,
            "main_answer": session.main_answer,
            "main_source": session.main_answer_source,
            "followup_question": session.followup_question,
            "followup_answer": session.followup_answer,
            "followup_source": session.followup_answer_source,
            "review": review,
        }
    )
    session.phase = "finished"
    set_session(context, session)
    await _log_exam_attempt(context, session)
    await update.message.reply_text(review, parse_mode="HTML")
    await update.message.reply_text(
        "Готово. Хотите ещё одно задание?",
        reply_markup=_after_review_keyboard(),
    )


async def _log_exam_attempt(context: ContextTypes.DEFAULT_TYPE, session: ExamTrainSession) -> None:
    if not TUTOR_SQLITE_ENABLED:
        return
    tid = context.user_data.get("_exam_telegram_id")
    if tid is None:
        return
    try:
        await insert_submission(
            TUTOR_SQLITE_PATH,
            telegram_user_id=tid,
            telegram_username=context.user_data.get("_exam_username"),
            telegram_first_name=context.user_data.get("_exam_first_name"),
            fio="—",
            block=session.block or "1",
            assignment_type="тренажёр экзамена",
            seminar="экзамен",
            assignment_key=session.task_type.value if session.task_type else "exam",
            accepted=True,
            student_answer=json.dumps(session.log[-1] if session.log else {}, ensure_ascii=False),
            bot_message=session.log[-1].get("review", "")[:500] if session.log else None,
            sheet_written=False,
        )
    except Exception:
        logger.exception("exam insert_submission")
