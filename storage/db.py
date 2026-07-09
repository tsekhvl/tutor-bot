"""SQLite: журнал проверок заданий tutor_bot."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = __import__("logging").getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(path: str) -> sqlite3.Connection:
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db_sync(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                telegram_user_id INTEGER,
                telegram_username TEXT,
                telegram_first_name TEXT,
                fio TEXT NOT NULL,
                block TEXT NOT NULL,
                assignment_type TEXT NOT NULL,
                seminar TEXT NOT NULL,
                assignment_key TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                student_answer TEXT NOT NULL,
                bot_message TEXT,
                sheet_written INTEGER NOT NULL DEFAULT 0,
                error_message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_submissions_created
                ON submissions (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_submissions_user
                ON submissions (telegram_user_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_submissions_fio
                ON submissions (fio, created_at DESC);
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_submission_sync(
    db_path: str,
    *,
    telegram_user_id: int | None,
    telegram_username: str | None,
    telegram_first_name: str | None,
    fio: str,
    block: str,
    assignment_type: str,
    seminar: str,
    assignment_key: str,
    accepted: bool,
    student_answer: str,
    bot_message: str | None,
    sheet_written: bool,
    error_message: str | None = None,
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO submissions (
                created_at, telegram_user_id, telegram_username, telegram_first_name,
                fio, block, assignment_type, seminar, assignment_key,
                accepted, student_answer, bot_message, sheet_written, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_now(),
                telegram_user_id,
                telegram_username,
                telegram_first_name,
                fio.strip() or "—",
                block.strip() or "—",
                assignment_type.strip() or "—",
                seminar.strip() or "—",
                assignment_key.strip() or "main",
                1 if accepted else 0,
                student_answer,
                bot_message,
                1 if sheet_written else 0,
                error_message,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def submissions_stats_sync(db_path: str) -> dict[str, object]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return {"error": "not_found", "path": str(path)}
    conn = _connect(str(path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
        accepted = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE accepted = 1"
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE accepted = 0 AND error_message IS NULL"
        ).fetchone()[0]
        errors = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE error_message IS NOT NULL"
        ).fetchone()[0]
        return {
            "path": str(path),
            "db_size_bytes": int(path.stat().st_size),
            "total": int(total),
            "accepted": int(accepted),
            "rejected": int(rejected),
            "check_errors": int(errors),
        }
    finally:
        conn.close()


def export_submissions_snapshot_sync(db_path: str, *, limit: int = 5000) -> dict[str, object]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        return {"error": "not_found", "path": str(path)}
    limit = max(1, min(int(limit), 50_000))
    conn = _connect(str(path))
    try:
        rows = conn.execute(
            """
            SELECT
                id, created_at, telegram_user_id, telegram_username, telegram_first_name,
                fio, block, assignment_type, seminar, assignment_key,
                accepted, student_answer, bot_message, sheet_written, error_message
            FROM submissions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        items: list[dict[str, object]] = []
        for r in rows:
            items.append(
                {
                    "id": int(r["id"]),
                    "created_at": r["created_at"],
                    "telegram_user_id": r["telegram_user_id"],
                    "telegram_username": r["telegram_username"],
                    "telegram_first_name": r["telegram_first_name"],
                    "fio": r["fio"],
                    "block": r["block"],
                    "assignment_type": r["assignment_type"],
                    "seminar": r["seminar"],
                    "assignment_key": r["assignment_key"],
                    "accepted": bool(r["accepted"]),
                    "student_answer": r["student_answer"],
                    "bot_message": r["bot_message"],
                    "sheet_written": bool(r["sheet_written"]),
                    "error_message": r["error_message"],
                }
            )
        stats = submissions_stats_sync(str(path))
        return {
            "exported_at": _utc_now(),
            "stats": stats,
            "submissions": items,
            "limit": limit,
        }
    finally:
        conn.close()


async def init_db(db_path: str) -> None:
    await asyncio.to_thread(init_db_sync, db_path)


async def insert_submission(db_path: str, **kwargs) -> int:
    return await asyncio.to_thread(insert_submission_sync, db_path, **kwargs)


async def submissions_stats(db_path: str) -> dict[str, object]:
    return await asyncio.to_thread(submissions_stats_sync, db_path)


async def export_submissions_snapshot(db_path: str, *, limit: int = 5000) -> dict[str, object]:
    return await asyncio.to_thread(
        export_submissions_snapshot_sync, db_path, limit=limit
    )
