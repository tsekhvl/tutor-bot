"""Конфигурация приложения."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Google Cloud (Vertex AI + Sheets)
GOOGLE_PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID", "")
GOOGLE_LOCATION = os.getenv("GOOGLE_LOCATION", "global")
# Vertex Gemini. Avoid gemini-2.0-flash in us-central1 (often 404).
TUTOR_GEMINI_MODEL = os.getenv("TUTOR_GEMINI_MODEL", "gemini-3.5-flash")
# Thinking for /check_ai (google-genai): low | medium | high | off
TUTOR_GEMINI_THINKING_LEVEL = os.getenv(
    "TUTOR_GEMINI_THINKING_LEVEL", "low"
).strip().lower()
_DEFAULT_CREDENTIALS = Path(__file__).parent / "my-project-key.json"
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or (
    str(_DEFAULT_CREDENTIALS) if _DEFAULT_CREDENTIALS.exists() else ""
)

# Google Sheets — spreadsheet IDs from env only (no production defaults in public repo)
SHEET_BLOCK_1_ID = os.getenv("SHEET_BLOCK_1_ID", "")
SHEET_BLOCK_3_ID = os.getenv("SHEET_BLOCK_3_ID", "")

# SQLite — журнал всех проверок (принятые и отклонённые)
_DEFAULT_DATA_DIR = Path(__file__).resolve().parent / "data"
TUTOR_SQLITE_PATH = (
    os.getenv("TUTOR_SQLITE_PATH", "").strip()
    or str(_DEFAULT_DATA_DIR / "tutor.db")
)
_raw_sqlite_en = os.getenv("TUTOR_SQLITE_ENABLED", "1").strip().lower()
TUTOR_SQLITE_ENABLED = _raw_sqlite_en not in {"0", "false", "no", "off", ""}

# Telegram user id владельца: /export_submissions и /submissions_stats
def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


TUTOR_OWNER_TELEGRAM_ID = _int_env("TUTOR_OWNER_TELEGRAM_ID", 0)

# Критерии оценивания (можно настроить)
MAX_SCORE = 10  # Максимальный балл за задание

# Контрольная: порог Gemini для открытых ответов (0–100)
TUTOR_OPEN_ANSWER_PASS_SCORE = _int_env("TUTOR_OPEN_ANSWER_PASS_SCORE", 60)
