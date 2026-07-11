"""Owner-only: проверка footnotes в .docx на сомнительные цитаты."""
from __future__ import annotations

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

from telegram import InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from config import TUTOR_OWNER_TELEGRAM_ID

from .annotate_docx import annotate_docx_with_comments
from .checker import build_report_docx, check_citations, format_telegram_summary
from .docx_footnotes import parse_docx_footnotes

logger = logging.getLogger(__name__)

AWAIT_DOCX = "citation_await_docx"
_MAX_BYTES = 15 * 1024 * 1024


async def _owner_only(update: Update) -> bool:
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return False
    owner_id = int(TUTOR_OWNER_TELEGRAM_ID or 0)
    if owner_id <= 0:
        await msg.reply_text("В .env не задан TUTOR_OWNER_TELEGRAM_ID.")
        return False
    if user.id != owner_id:
        await msg.reply_text("Команда только для владельца бота.")
        return False
    return True


async def cmd_check_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ /check_ai — пришлите .docx со сносками (footnotes). """
    msg = update.effective_message
    if not msg:
        return
    if not await _owner_only(update):
        return

    for key in ["state", "block", "fio", "type", "seminar", "assignment_key"]:
        context.user_data.pop(key, None)
    try:
        from control.session import clear_session as clear_control

        clear_control(context)
    except Exception:
        pass
    try:
        from exam_train.session import set_session as set_exam_session

        set_exam_session(context, None)
    except Exception:
        context.user_data.pop("exam", None)

    context.user_data[AWAIT_DOCX] = True
    await msg.reply_text(
        "🔎 Проверка сносок (footnotes)\n\n"
        "Пришлите файл <b>.docx</b> с классическими сносками Word.\n\n"
        "Бот:\n"
        "• откроет ссылки из сносок (HTTP prefetch) и проверит через Gemini + поиск;\n"
        "• вернёт копию с <b>примечаниями Word</b> и короткую таблицу на проверку.\n\n"
        "<i>Пока только footnotes. Доступ только владельцу.</i>",
        parse_mode="HTML",
    )


def is_citation_awaiting(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get(AWAIT_DOCX))


def _safe_stem(filename: str) -> str:
    stem = re.sub(r"\.docx$", "", filename, flags=re.I)
    stem = re.sub(r"[^\w\-а-яА-ЯёЁ ]+", "", stem, flags=re.U).strip()
    return (stem[:60] or "document").replace(" ", "_")


async def citation_handle_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    msg = update.effective_message
    if not msg or not msg.document:
        return
    if not await _owner_only(update):
        return
    if not context.user_data.get(AWAIT_DOCX):
        await msg.reply_text("Сначала напишите /check_ai, затем пришлите .docx.")
        return

    doc = msg.document
    name = (doc.file_name or "document.docx").strip()
    if not name.lower().endswith(".docx"):
        await msg.reply_text("Нужен файл .docx (не .doc и не PDF).")
        return
    if doc.file_size and doc.file_size > _MAX_BYTES:
        await msg.reply_text("Файл слишком большой (лимит ~15 МБ).")
        return

    status = await msg.reply_text("⏳ Скачиваю и разбираю сноски…")
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)

    try:
        tg_file = await doc.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        logger.exception("download docx")
        await status.edit_text(f"❌ Не удалось скачать файл: {type(e).__name__}: {e}")
        return

    try:
        parsed = parse_docx_footnotes(data)
    except ValueError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        logger.exception("parse docx")
        await status.edit_text(f"❌ Ошибка разбора DOCX: {type(e).__name__}: {e}")
        return

    if not parsed.citations:
        warn = "\n".join(f"• {w}" for w in parsed.warnings[:10]) or "—"
        await status.edit_text(
            f"Сносок footnotes не найдено.\n\nПредупреждения:\n{warn}\n\n"
            "Убедитесь, что в Word это именно Insert → Footnote, не endnote."
        )
        context.user_data.pop(AWAIT_DOCX, None)
        return

    n = len(parsed.citations)
    await status.edit_text(
        f"Найдено сносок: <b>{n}</b>.\n"
        "Проверяю источники (Gemini + Google Search)…\n"
        "<i>Прогресс будет обновляться по батчам.</i>",
        parse_mode="HTML",
    )
    await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)

    loop = asyncio.get_running_loop()
    progress_lines: list[str] = []

    def _on_progress(line: str) -> None:
        progress_lines.append(line)
        tail = progress_lines[-6:]
        body = (
            f"🔎 Проверка сносок: <b>{n}</b>\n\n"
            + "\n".join(f"• {_escape_html(x)}" for x in tail)
        )
        if len(body) > 3500:
            body = body[-3500:]
        fut = asyncio.run_coroutine_threadsafe(
            _safe_edit_status(status, body),
            loop,
        )
        try:
            fut.result(timeout=15)
        except Exception:
            logger.exception("citation progress edit")

    try:
        report = await asyncio.to_thread(
            check_citations,
            parsed.citations,
            parse_warnings=parsed.warnings,
            progress=_on_progress,
        )
    except Exception as e:
        logger.exception("check_citations")
        await status.edit_text(f"❌ Ошибка проверки: {type(e).__name__}: {e}")
        context.user_data.pop(AWAIT_DOCX, None)
        return

    context.user_data.pop(AWAIT_DOCX, None)

    summary = format_telegram_summary(report, filename=name)
    await status.edit_text(summary, parse_mode="HTML")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    stem = _safe_stem(name)

    # 1) аннотированный оригинал с Word-комментариями
    try:
        annotated = await asyncio.to_thread(
            annotate_docx_with_comments,
            data,
            report.flagged,
        )
        await msg.reply_document(
            document=InputFile(
                io.BytesIO(annotated),
                filename=f"{stem}_checked_{stamp}.docx",
            ),
            caption=(
                f"Оригинал с примечаниями Word: "
                f"{len(report.flagged)} из {report.total}."
            ),
        )
    except Exception as e:
        logger.exception("annotate_docx_with_comments")
        await msg.reply_text(
            f"Не удалось добавить примечания в оригинал: {type(e).__name__}: {e}"
        )

    # 2) короткая таблица
    try:
        docx_bytes = await asyncio.to_thread(
            build_report_docx,
            report,
            source_filename=name,
        )
        await msg.reply_document(
            document=InputFile(
                io.BytesIO(docx_bytes),
                filename=f"{stem}_report_{stamp}.docx",
            ),
            caption=(
                f"Таблица: на проверку {len(report.flagged)}, ок {report.ok_count}."
            ),
        )
    except Exception as e:
        logger.exception("build_report_docx")
        await msg.reply_text(
            f"Таблица не собралась: {type(e).__name__}: {e}"
        )


async def _safe_edit_status(status_message, text: str) -> None:
    try:
        await status_message.edit_text(text, parse_mode="HTML")
    except Exception as e:
        low = str(e).lower()
        if "not modified" in low:
            return
        logger.warning("status edit: %s", e)


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
