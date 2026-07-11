# Деплой Tutor Bot на VPS (Нидерланды)

Сервер: **`root@YOUR_VPS_HOST`**, каталог **`/opt/tutor_bot`**, сервис **`tutor-bot`**, пользователь **`root`** (без WireGuard / botuser).

На том же VPS может работать **Хроноскоп** (`/opt/bots/chronoscope_bot`) — у tutor **свой токен**, конфликта нет. Если tutor раньше крутился на **старом** IP — там **`stop` + `disable`**, иначе `Conflict: getUpdates`.

---

## Шаг 1. SSH

```powershell
ssh root@YOUR_VPS_HOST
```

На чистой Ubuntu, если нет `venv`:

```bash
apt update
apt install -y python3 python3-venv python3-pip
```

---

## Шаг 2. Каталог на сервере

```bash
mkdir -p /opt/tutor_bot/data
```

---

## Шаг 3. Загрузка кода с Windows

Из **`d:\path\to\tutor`**:

**Вариант А — скрипт:**

```powershell
cd d:\path\to\tutor
.\deploy.ps1
```

**Вариант Б — вручную:**

```powershell
cd d:\path\to\tutor
ssh root@YOUR_VPS_HOST "mkdir -p /opt/tutor_bot/data"
scp -r main.py config.py requirements.txt assignments.json bot ai sheets storage control exam_train citation data tutor-bot.service setup-server.sh .env.example root@YOUR_VPS_HOST:/opt/tutor_bot/
```

Ключ Google (отдельно, не в git):

```powershell
scp d:\path\to\tutor\my-project-key.json root@YOUR_VPS_HOST:/opt/tutor_bot/my-project-key.json
```

**Не копируй** `.env` в git/скрипт без необходимости — на сервере создай вручную (шаг 5).

---

## Шаг 4. venv и зависимости

На сервере:

```bash
cd /opt/tutor_bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Или один раз: `bash setup-server.sh` (нужен уже залитый код в `/opt/tutor_bot`).

---

## Шаг 5. `.env`

```bash
cd /opt/tutor_bot
nano .env
```

Пример (подставь свой токен и `TUTOR_OWNER_TELEGRAM_ID`):

```env
TELEGRAM_BOT_TOKEN=...
GOOGLE_APPLICATION_CREDENTIALS=my-project-key.json
GOOGLE_PROJECT_ID=your_gcp_project_id
GOOGLE_LOCATION=global
TUTOR_GEMINI_MODEL=gemini-3-flash-preview
SHEET_BLOCK_1_ID=
SHEET_BLOCK_3_ID=
TUTOR_SQLITE_ENABLED=1
TUTOR_SQLITE_PATH=./data/tutor.db
TUTOR_OWNER_TELEGRAM_ID=0
```

```bash
chmod 600 /opt/tutor_bot/.env /opt/tutor_bot/my-project-key.json
```

---

## Шаг 6. systemd

```bash
cp /opt/tutor_bot/tutor-bot.service /etc/systemd/system/tutor-bot.service
systemctl daemon-reload
systemctl enable tutor-bot
systemctl start tutor-bot
systemctl status tutor-bot -l --no-pager
```

В логе при старте: **`SQLite журнал: .../data/tutor.db`**.

---

## Шаг 7. Проверка

- Студент: `/start` → сдача задания; `/control`; `/exam`  
- Владелец: **`/stats`**, **`/export_submissions`**, **`/check_ai`** (docx со сносками)

Логи:

```bash
journalctl -u tutor-bot -f
```

---

## Обновление кода

```powershell
cd d:\path\to\tutor
.\deploy.ps1
```

Или точечно:

```powershell
scp d:\path\to\tutor\main.py root@YOUR_VPS_HOST:/opt/tutor_bot/
scp -r d:\path\to\tutor\bot d:\path\to\tutor\storage root@YOUR_VPS_HOST:/opt/tutor_bot/
ssh root@YOUR_VPS_HOST "systemctl restart tutor-bot"
```

---

## Старый сервер (если tutor там был)

```bash
ssh root@72.56.246.52 "systemctl stop tutor-bot; systemctl disable tutor-bot"
```

---

## Команды управления

| Задача | Команда |
|--------|---------|
| Статус | `systemctl status tutor-bot -l --no-pager` |
| Перезапуск | `systemctl restart tutor-bot` |
| Логи | `journalctl -u tutor-bot -f` |
| БД на диске | `/opt/tutor_bot/data/tutor.db` |

---

## Чеклист

- [ ] `/opt/tutor_bot` и `data/` созданы  
- [ ] `venv` + `pip install -r requirements.txt`  
- [ ] `.env` и `my-project-key.json`, `chmod 600`  
- [ ] `tutor-bot.service` в `/etc/systemd/system/`, сервис **enabled**  
- [ ] Старый tutor на другом VPS **остановлен**  
- [ ] `/stats` и тестовая сдача работают  
