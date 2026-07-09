"""Запись результатов в Google Sheets."""
import math

import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_client(credentials_path: str):
    """Создаёт клиент Google Sheets."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _normalize_fio(s: str) -> str:
    """Нормализация ФИО для поиска: нижний регистр, лишние пробелы убраны."""
    if not s or not isinstance(s, str):
        return ""
    return " ".join(str(s).strip().lower().split())


def get_seminar_column(seminar: int) -> int:
    """Номер колонки для семинара (1-based). Семинар 1 = C = 3, семинар 2 = D = 4, ..."""
    return 2 + int(seminar)


# Блок 1, 1-й курс: итоговая оценка интерактивной контрольной (столбец U)
BLOCK_1_CONTROL_GRADE_COLUMN = 21
CONTROL_SHEET_GRADE_MAX = 7


def normalize_control_grade_for_sheet(grade: int, max_grade: int | None = None) -> int:
    """Приводит итог контрольной к шкале 0–7 для записи в таблицу."""
    g = max(0, int(grade))
    if max_grade is None or max_grade <= CONTROL_SHEET_GRADE_MAX:
        return min(CONTROL_SHEET_GRADE_MAX, g)
    return min(
        CONTROL_SHEET_GRADE_MAX,
        max(0, math.ceil(g * CONTROL_SHEET_GRADE_MAX / max_grade)),
    )


def _parse_score_cell(value) -> int:
    """Читает балл из ячейки: 6, «6», «6/7» → 6."""
    if value is None:
        return 0
    s = str(value).strip()
    if not s:
        return 0
    if "/" in s:
        s = s.split("/", 1)[0].strip()
    try:
        return int(float(s.replace(",", ".")))
    except (ValueError, TypeError):
        return 0


def find_student_row(worksheet, fio: str) -> int:
    """Ищет строку студента по ФИО (1-based). Бросает ValueError, если не найден."""
    all_rows = worksheet.get_all_values()
    fio_normalized = _normalize_fio(fio)
    if not fio_normalized:
        raise ValueError("ФИО не указано.")

    for i, row in enumerate(all_rows):
        if not row:
            continue
        cell_fio = row[0] if row else ""
        cell_norm = _normalize_fio(cell_fio)
        if cell_norm in ("", "фио студента", "фио"):
            continue
        if cell_norm == fio_normalized:
            return i + 1
        user_words = set(fio_normalized.split())
        table_words = set(cell_norm.split())
        if user_words and user_words <= table_words:
            return i + 1
        if table_words and table_words <= user_words:
            return i + 1

    raise ValueError(
        f"Студент «{fio}» не найден в таблице. "
        "ФИО должно совпадать с таблицей (например: Иванов Иван Иванович)."
    )


# Лимиты
MAX_EXTRA_BALLS = 10
# Блок 1: семинар макс 2, +2 за отработку
# Блок 3: семинар макс 1, +1 за отработку

BLOCK_CONFIG = {
    "1": {"otrabotka_add": 2, "otrabotka_max": 2, "seminars": 15},
    "3": {"otrabotka_add": 1, "otrabotka_max": 1, "seminars": 9},
}


def write_score_to_existing_sheet(
    client: gspread.Client,
    spreadsheet_id: str,
    fio: str,
    assignment_type: str,
    seminar: str,
    block: str = "1",
    sheet_name: str = None,
) -> None:
    """
    Записывает балл в существующую таблицу ВШЭ-ЮФУ.
    Доп: +1 в Экстра баллы (макс 10).
    Отработка: блок 1 — +2 (макс 2), блок 3 — +1 (макс 1).
    """
    sh = client.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1 if sheet_name is None else sh.worksheet(sheet_name)

    row_idx = find_student_row(worksheet, fio)

    cfg = BLOCK_CONFIG.get(block, BLOCK_CONFIG["1"])
    max_seminars = cfg["seminars"]

    if assignment_type == "дополнительное задание":
        col = 2  # B — Экстра баллы, +1 балл
        add_score = 1
        max_score = MAX_EXTRA_BALLS
    else:
        # отработка — колонка семинара
        try:
            sem_num = int(seminar)
            if sem_num < 1 or sem_num > max_seminars:
                raise ValueError(f"Номер семинара должен быть от 1 до {max_seminars}")
            col = get_seminar_column(sem_num)
        except ValueError:
            raise ValueError(f"Некорректный номер семинара: {seminar}")
        add_score = cfg["otrabotka_add"]
        max_score = cfg["otrabotka_max"]

    cell = worksheet.cell(row_idx, col)
    current = cell.value
    try:
        current_val = int(current) if current else 0
    except (ValueError, TypeError):
        current_val = 0

    new_val = min(current_val + add_score, max_score)
    worksheet.update_cell(row_idx, col, new_val)


def write_control_grade(
    client: gspread.Client,
    spreadsheet_id: str,
    fio: str,
    grade: int,
    *,
    column: int = BLOCK_1_CONTROL_GRADE_COLUMN,
    sheet_name: str = None,
) -> tuple[int, bool]:
    """
    Записывает итог контрольной в столбец U (блок 1).
    Возвращает (итоговое значение в ячейке, было_ли_обновление).
    Новый балл не понижает уже записанный.
    """
    sh = client.open_by_key(spreadsheet_id)
    worksheet = sh.sheet1 if sheet_name is None else sh.worksheet(sheet_name)
    row_idx = find_student_row(worksheet, fio)

    cell = worksheet.cell(row_idx, column)
    current_val = _parse_score_cell(cell.value)
    new_val = max(current_val, min(CONTROL_SHEET_GRADE_MAX, int(grade)))
    updated = new_val != current_val
    if updated:
        worksheet.update_cell(row_idx, column, new_val)
    return new_val, updated


def append_accepted_record(
    client: gspread.Client,
    spreadsheet_id: str,
    fio: str,
    block: str,
    assignment_type: str,
    seminar: str,
    score: int = 0,  # не используется
    sheet_name: str = None,
) -> None:
    """
    Записывает балл в существующую таблицу.
    Блок 1: доп +1, отработка +2 (макс 2). Блок 3: доп +1, отработка +1 (макс 1).
    """
    write_score_to_existing_sheet(
        client=client,
        spreadsheet_id=spreadsheet_id,
        fio=fio,
        assignment_type=assignment_type,
        seminar=seminar,
        block=block,
        sheet_name=sheet_name,
    )
