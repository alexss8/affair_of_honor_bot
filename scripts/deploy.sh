#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ Запустите скрипт от имени root: sudo bash scripts/deploy.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE}")/.." && pwd)"  # корень проекта (скрипт лежит в scripts/)
BOT_USER="botuser"
SERVICE_NAME="duelbot"

echo "🚀 Начинаем развертывание Дуэльного Бота на Ubuntu 22.04..."

echo "📦 1/7. Установка Python 3.12, Git и системных утилит..."
apt-get update -y
apt-get install -y software-properties-common ufw curl git rsync
if ! command -v python3.12 &> /dev/null; then
  add-apt-repository ppa:deadsnakes/ppa -y
  apt-get update -y
  apt-get install -y python3.12 python3.12-venv
fi

echo "🛡 2/7. Настройка фаервола (оставляем только SSH)..."
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

echo "👤 3/7. Настройка безопасного пользователя $BOT_USER..."
if ! id "$BOT_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$BOT_USER"
fi

TARGET_DIR="/home/$BOT_USER/duel_bot"
if [ "$APP_DIR" != "$TARGET_DIR" ]; then
  mkdir -p "$TARGET_DIR"
  # Синхронизируем код (включая .git), но никогда не трогаем данные прошлой установки:
  # .env, базу (duels.db, -wal, -shm), логи и venv. Исключённые файлы rsync не удаляет даже с --delete.
  rsync -a --delete \
    --exclude '.env' \
    --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
    --exclude '*.log' --exclude '*.log.*' \
    --exclude 'venv/' \
    "$APP_DIR"/ "$TARGET_DIR"/
fi

chown -R "$BOT_USER:$BOT_USER" "$TARGET_DIR"
chmod 700 "$TARGET_DIR"
su - "$BOT_USER" -c "git config --global --add safe.directory '$TARGET_DIR'"

echo "🐍 4/7. Создание виртуального окружения и установка зависимостей..."
su - "$BOT_USER" -c "
  cd '$TARGET_DIR'
  python3.12 -m venv venv
  ./venv/bin/pip install --upgrade pip -q
  ./venv/bin/pip install -r requirements.txt -q
"

echo "🔑 5/7. Настройка переменных окружения (.env)..."
if [ ! -f "$TARGET_DIR/.env" ]; then
  su - "$BOT_USER" -c "cd '$TARGET_DIR' && ./venv/bin/python scripts/setup_env.py"
else
  echo "ℹ️  Файл .env уже существует, пропускаем настройку."
fi

echo "⚙️ 6/7. Настройка автозапуска 24/7 через systemd и лимита логов..."
mkdir -p /etc/systemd/journald.conf.d
cat <<EOF > /etc/systemd/journald.conf.d/size-limit.conf
[Journal]
SystemMaxUse=50M
MaxRetentionSec=7day
EOF
systemctl restart systemd-journald || true

cat <<EOF > /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=Telegram Duel Bot (Affair of Honor)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$BOT_USER
Group=$BOT_USER
WorkingDirectory=$TARGET_DIR
ExecStart=$TARGET_DIR/venv/bin/python -m bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "💾 7/7. Ежедневный бэкап базы (cron, 7 последних копий)..."
# Бэкапы лежат вне папки кода, чтобы rsync --delete при повторном деплое их не задел.
BACKUP_DIR="/home/$BOT_USER/backups"
install -d -m 700 -o "$BOT_USER" -g "$BOT_USER" "$BACKUP_DIR"
cat <<EOF > /etc/cron.d/${SERVICE_NAME}-backup
# Ежедневный бэкап базы дуэльного бота. Лог: journalctl -t ${SERVICE_NAME}-backup
SHELL=/bin/bash
30 4 * * * $BOT_USER cd $TARGET_DIR && ./venv/bin/python scripts/backup_db.py --dir $BACKUP_DIR --keep 7 2>&1 | systemd-cat -t ${SERVICE_NAME}-backup
EOF
chmod 644 /etc/cron.d/${SERVICE_NAME}-backup

echo ""
echo "✅ Готово! Бот успешно установлен и запущен в фоновом режиме."
echo "📊 Проверить статус: systemctl status $SERVICE_NAME"
echo "📜 Смотреть логи:    journalctl -u $SERVICE_NAME -f"
echo "💾 Бэкапы базы:     ls -lh $BACKUP_DIR"
