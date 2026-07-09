"""Точка входа: Telegram-бот для приёма и анализа заданий."""
import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot import cmd_export_submissions, cmd_submissions_stats, handle_text, start
from control import cmd_control, control_handle_callback
from exam_train import cmd_exam, exam_handle_callback, exam_handle_voice
from config import TELEGRAM_BOT_TOKEN, TUTOR_SQLITE_ENABLED, TUTOR_SQLITE_PATH
from storage import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("Укажите TELEGRAM_BOT_TOKEN в .env")

    async def _post_init(app) -> None:
        if TUTOR_SQLITE_ENABLED:
            await init_db(TUTOR_SQLITE_PATH)
            logger.info("SQLite журнал: %s", TUTOR_SQLITE_PATH)

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("control", cmd_control))
    app.add_handler(CommandHandler("exam", cmd_exam))
    app.add_handler(CallbackQueryHandler(control_handle_callback, pattern=r"^ctl:"))
    app.add_handler(CallbackQueryHandler(exam_handle_callback, pattern=r"^exm:"))
    app.add_handler(MessageHandler(filters.VOICE, exam_handle_voice))
    app.add_handler(CommandHandler(["submissions_stats", "stats"], cmd_submissions_stats))
    app.add_handler(
        CommandHandler(["export_submissions", "export_db"], cmd_export_submissions)
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
