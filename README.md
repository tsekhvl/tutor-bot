# Tutor Bot

Telegram-бот для курса по истории Ближнего Востока / исламоведению: **сдача отработок**, **интерактивная контрольная** и **тренажёр к устному экзамену**. Проверка через Vertex Gemini; опционально — запись баллов в Google Sheets и журнал в SQLite.

> Portfolio snapshot with **demo content only**. The full course pack is not published.

## Highlights (what to look at)

### 1. Interactive control — `/control`

Three game modes over the same question pool (`data/control_pool.json`):

| Mode | Mechanic | Scoring idea |
|------|----------|--------------|
| **Caravan** | Open answers at city stops; easier fallback on fail | Seals / stops → 0–7 |
| **Bosses** | Debate: refute an opponent thesis; Gemini judges | Defeated bosses → 0–7 |
| **Faqih** | Multiple choice with 50/50, hint, one redo | Correct count scaled to 0–7 |

Best grade wins on retake when Sheets is configured.

### 2. Oral exam trainer — `/exam`

- **Block 1** — topic (~5 min), two terms, short essay  
- **Block 3** — dates, personalities, terms, periods (modern Arab world showcase)  
- Text or **voice**; Gemini asks a follow-up, then gives strengths / gaps / how to improve  

Demo pools: `data/exam_train_block1.json`, `data/exam_train_block3.json`.

### 3. Homework / make-up flow — `/start`

Student picks block → assignment type → seminar → answer. Gemini checks against criteria in `assignments.json`. Accepted work can be written to Sheets (block 1 / block 3 spreadsheets).

### 4. Ops

- SQLite submission log; owner commands `/submissions_stats`, `/export_submissions`  
- Deploy scripts + systemd unit (see `DEPLOY.md`)

## Stack

| Layer | Tech |
|-------|------|
| Bot | Python, `python-telegram-bot` |
| AI | Vertex Gemini (`google-cloud-aiplatform` / genai) |
| Grades | Google Sheets (`gspread`) |
| Journal | SQLite |
| Content | JSON pools (`assignments.json`, `control_pool.json`, `exam_train_*.json`) |

```mermaid
flowchart TD
  student[Student in Telegram]
  student --> hw["/start homework"]
  student --> ctl["/control"]
  student --> exam["/exam"]
  hw --> gemini[Gemini check]
  ctl --> engines[Caravan / Bosses / Faqih]
  engines --> gemini
  exam --> coach[Exam coach + follow-up]
  coach --> gemini
  gemini --> sheets[Google Sheets optional]
  gemini --> sqlite[SQLite journal]
```

## Quick start

```bash
cd tutor
python -m venv venv
# Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# set TELEGRAM_BOT_TOKEN, GOOGLE_PROJECT_ID, GOOGLE_APPLICATION_CREDENTIALS
python main.py
```

Sheets IDs are optional for trying `/control` and `/exam` locally (grades simply will not sync).

## Demo content note

This repo ships a **short demo** question set so `/control` and `/exam` run out of the box. Replace JSON files with your own course materials for production. Do not commit real student databases or spreadsheet IDs with live grades.

## Deploy

See [DEPLOY.md](DEPLOY.md). Use `YOUR_VPS_HOST` and your own secrets; never commit `.env` or service-account keys.
