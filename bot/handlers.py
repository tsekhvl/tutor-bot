"""Обработчики Telegram бота."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ai import check_student_answer
from config import (
    GOOGLE_CREDENTIALS_PATH,
    GOOGLE_LOCATION,
    GOOGLE_PROJECT_ID,
    SHEET_BLOCK_1_ID,
    SHEET_BLOCK_3_ID,
    TUTOR_OWNER_TELEGRAM_ID,
    TUTOR_SQLITE_ENABLED,
    TUTOR_SQLITE_PATH,
)
from sheets import append_accepted_record, get_client
from storage import export_submissions_snapshot, insert_submission, submissions_stats

logger = logging.getLogger(__name__)


# Состояния диалога
STATE_BLOCK = "block"
STATE_FIO = "fio"  # Фамилия Имя Отчество одним сообщением
STATE_TYPE = "type"
STATE_SEMINAR = "seminar"
STATE_ANSWER = "answer"


ASSIGNMENTS_PATH = Path(__file__).parent.parent / "assignments.json"

def _normalize_num(value: str) -> str:
    """Возвращает числовую часть строки (например, 'блок 1' -> '1')."""
    digits = "".join(c for c in str(value) if c.isdigit())
    return digits or str(value).strip()


def get_state(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get("state")


def set_state(context: ContextTypes.DEFAULT_TYPE, state: str | None) -> None:
    context.user_data["state"] = state


def clear_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ["state", "block", "fio", "type", "seminar", "assignment_key"]:
        context.user_data.pop(key, None)


def load_assignment(block: str, seminar: str, assignment_key: str) -> str | None:
    """Загружает текст задания из assignments.json."""
    if not ASSIGNMENTS_PATH.exists():
        return None
    with open(ASSIGNMENTS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    block_key = _normalize_num(block)
    seminar_key = _normalize_num(seminar)
    block_data = data.get(block_key)
    if not block_data:
        return None
    seminar_data = block_data.get(str(seminar_key))
    if not seminar_data:
        return None
    return seminar_data.get(assignment_key)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начало: сбрасываем и спрашиваем блок ИРС."""
    from control.session import clear_session

    clear_session(context)
    clear_state(context)
    set_state(context, STATE_BLOCK)
    await update.message.reply_text("Блок ИРС?")


async def _owner_gate(msg, user) -> bool:
    if not TUTOR_SQLITE_ENABLED:
        await msg.reply_text("SQLite выключена — команда недоступна.")
        return False
    owner_id = int(TUTOR_OWNER_TELEGRAM_ID or 0)
    if owner_id <= 0:
        await msg.reply_text("В .env не задан TUTOR_OWNER_TELEGRAM_ID.")
        return False
    if user.id != owner_id:
        await msg.reply_text("Команда только для владельца бота.")
        return False
    return True


async def _log_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    fio: str,
    block: str,
    assignment_type: str,
    seminar: str,
    assignment_key: str,
    student_answer: str,
    accepted: bool,
    bot_message: str | None,
    sheet_written: bool,
    error_message: str | None = None,
) -> None:
    if not TUTOR_SQLITE_ENABLED:
        return
    u = update.effective_user
    try:
        await insert_submission(
            TUTOR_SQLITE_PATH,
            telegram_user_id=u.id if u else None,
            telegram_username=u.username if u else None,
            telegram_first_name=u.first_name if u else None,
            fio=fio,
            block=block,
            assignment_type=assignment_type,
            seminar=seminar,
            assignment_key=assignment_key,
            accepted=accepted,
            student_answer=student_answer,
            bot_message=bot_message,
            sheet_written=sheet_written,
            error_message=error_message,
        )
    except Exception:
        logger.exception("insert_submission")


async def cmd_submissions_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await _owner_gate(msg, user):
        return
    try:
        data = await submissions_stats(TUTOR_SQLITE_PATH)
    except Exception as e:
        logger.exception("submissions_stats")
        await msg.reply_text(f"Ошибка чтения базы: {type(e).__name__}: {e}")
        return
    if data.get("error") == "not_found":
        await msg.reply_text(f"База пока не создана: {data.get('path')}")
        return
    await msg.reply_text(
        "📊 Журнал проверок (SQLite)\n"
        f"Всего записей: {data.get('total', 0)}\n"
        f"Принято: {data.get('accepted', 0)}\n"
        f"Отклонено: {data.get('rejected', 0)}\n"
        f"Ошибки проверки: {data.get('check_errors', 0)}\n"
        f"Файл: {data.get('path')}\n"
        f"Размер: {int(data.get('db_size_bytes', 0)) / (1024 * 1024):.2f} МиБ"
    )


async def cmd_export_submissions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    if not await _owner_gate(msg, user):
        return
    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    try:
        payload = await export_submissions_snapshot(TUTOR_SQLITE_PATH)
    except Exception as e:
        logger.exception("export_submissions_snapshot")
        await msg.reply_text(f"Ошибка экспорта: {type(e).__name__}: {e}")
        return
    if payload.get("error") == "not_found":
        await msg.reply_text(f"База не найдена: {payload.get('path')}")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw = json.dumps(payload, ensure_ascii=False, indent=2)
    fname = f"tutor_submissions_{stamp}.json"
    await msg.reply_document(
        InputFile(raw.encode("utf-8"), filename=fname),
        caption="Журнал проверок (последние записи, полные тексты ответов).",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка ответов по шагам."""
    from control.handlers import control_handle_text
    from control.session import is_control_active
    from exam_train.handlers import exam_handle_text
    from exam_train.session import is_exam_active

    if is_exam_active(context):
        if await exam_handle_text(update, context):
            return

    if is_control_active(context):
        if await control_handle_text(update, context):
            return

    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Отправь текст.")
        return

    state = get_state(context)

    if state == STATE_BLOCK:
        context.user_data["block"] = text
        set_state(context, STATE_FIO)
        await update.message.reply_text("Фамилия Имя Отчество?")

    elif state == STATE_FIO:
        context.user_data["fio"] = text
        set_state(context, STATE_TYPE)
        await update.message.reply_text(
            "Дополнительное задание или отработка?\n\n"
            "Напиши: <b>доп</b> или <b>отработка</b>",
            parse_mode="HTML",
        )

    elif state == STATE_TYPE:
        t = text.lower().strip()
        if "доп" in t or "дополнительное" in t:
            context.user_data["type"] = "дополнительное задание"
            context.user_data["assignment_key"] = "dop"
        elif "отработ" in t:
            context.user_data["type"] = "отработка"
            context.user_data["assignment_key"] = "main"
        else:
            await update.message.reply_text("Напиши: доп или отработка")
            return
        set_state(context, STATE_SEMINAR)
        await update.message.reply_text("Номер семинара?")

    elif state == STATE_SEMINAR:
        context.user_data["seminar"] = text
        set_state(context, STATE_ANSWER)
        await update.message.reply_text("Отправь свой ответ на задание:")

    elif state == STATE_ANSWER:
        student_answer = text
        block = context.user_data.get("block", "")
        seminar = context.user_data.get("seminar", "")
        assignment_key = context.user_data.get("assignment_key", "main")

        assignment = load_assignment(block, seminar, assignment_key)
        if not assignment:
            await update.message.reply_text(
                f"❌ Задание не найдено для блок {block}, семинар {seminar}.\n"
                "Проверь данные и напиши /start чтобы начать заново."
            )
            clear_state(context)
            return

        msg = await update.message.reply_text("⏳ Проверяю...")

        sheet_written = False
        result = None
        check_error: str | None = None
        try:
            result = check_student_answer(
                assignment=assignment,
                student_answer=student_answer,
                project_id=GOOGLE_PROJECT_ID,
                location=GOOGLE_LOCATION,
                credentials_path=GOOGLE_CREDENTIALS_PATH,
            )
        except Exception as e:
            check_error = f"{type(e).__name__}: {e}"
            await msg.edit_text(f"❌ Ошибка проверки: {check_error}")
            await _log_submission(
                update,
                context,
                fio=str(context.user_data.get("fio", "—")),
                block=str(block),
                assignment_type=str(context.user_data.get("type", "—")),
                seminar=str(seminar),
                assignment_key=assignment_key,
                student_answer=student_answer,
                accepted=False,
                bot_message=None,
                sheet_written=False,
                error_message=check_error,
            )
            clear_state(context)
            return

        status = "✅ ПРИНЯТО" if result.accepted else "❌ НЕ ПРИНЯТО"
        reply = f"{status}\n\n{result.message}"

        await msg.edit_text(reply)

        # Записываем в Google Sheets, если принято
        block_num = "".join(c for c in str(block) if c.isdigit()) or "1"
        sheet_id = SHEET_BLOCK_3_ID if block_num == "3" else SHEET_BLOCK_1_ID
        if result.accepted and sheet_id and GOOGLE_CREDENTIALS_PATH:
            try:
                client = get_client(GOOGLE_CREDENTIALS_PATH)
                append_accepted_record(
                    client=client,
                    spreadsheet_id=sheet_id,
                    fio=context.user_data.get("fio", "—"),
                    block=block_num,
                    assignment_type=context.user_data.get("type", "—"),
                    seminar=seminar,
                    score=result.score,
                )
                sheet_written = True
            except Exception as e:
                await update.message.reply_text(
                    f"⚠️ Балл не записан в таблицу:\n{type(e).__name__}: {e}"
                )

        await _log_submission(
            update,
            context,
            fio=str(context.user_data.get("fio", "—")),
            block=str(block),
            assignment_type=str(context.user_data.get("type", "—")),
            seminar=str(seminar),
            assignment_key=assignment_key,
            student_answer=student_answer,
            accepted=result.accepted,
            bot_message=result.message,
            sheet_written=sheet_written,
        )

        clear_state(context)
        await update.message.reply_text("Напиши /start чтобы сдать ещё одно задание.")

    else:
        await update.message.reply_text("Напиши /start чтобы начать.")
