#!/bin/bash
# Первоначальная настройка сервера. Запустить один раз: bash setup-server.sh

set -e

echo "=== Установка Python и зависимостей ==="
apt update && apt install -y python3 python3-pip python3-venv git

echo "=== Создание каталога data (SQLite) ==="
mkdir -p /opt/tutor_bot/data

echo "=== Создание venv ==="
cd /opt/tutor_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "=== Установка systemd сервиса ==="
cp /opt/tutor_bot/tutor-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tutor-bot

echo "=== Готово! ==="
echo "1. Создайте .env (nano .env) с TELEGRAM_BOT_TOKEN и др."
echo "2. Загрузите credentials.json в /opt/tutor_bot/"
echo "3. Запустите: systemctl start tutor-bot"
