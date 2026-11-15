#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ Запустите скрипт от имени root: sudo bash deploy.sh"
  exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOT_USER="botuser"
SERVICE_NAME="duelbot"

echo "🚀 Начинаем развертывание Дуэльного Бота на Ubuntu 22.04..."

echo "📦 1/6. Установка Python 3.12 и системных утилит..."
apt-get update -y
apt-get install -y software-properties-common ufw curl
if ! command -v python3.12 &> /dev/null; then
  add-apt-repository ppa:deadsnakes/ppa -y
  apt-get update -y
  apt-get install -y python3.12 python3.12-venv
fi

echo "🛡 2/6. Настройка фаервола (оставляем только SSH)..."
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

echo "👤 3/6. Настройка безопасного пользователя $BOT_USER..."
if ! id "$BOT_USER" &>/dev/null; then
  useradd -m -s /bin/bash "$BOT_USER"
fi

TARGET_DIR="/home/$BOT_USER/duel_bot"
if [ "$APP_DIR" != "$TARGET_DIR" ]; then
  mkdir -p "$TARGET_DIR"
  cp -r "$APP_DIR"/. "$TARGET_DIR"/
fi

chown -R "$BOT_USER:$BOT_USER" "$TARGET_DIR"
chmod 700 "$TARGET_DIR"

echo "🐍 4/6. Создание виртуального окружения и установка зависимостей..."
su - "$BOT_USER" -c "
  cd '$TARGET_DIR'
  python3.12 -m venv venv
  ./venv/bin/pip install --upgrade pip -q
  ./venv/bin/pip install -r requirements.txt -q
"

echo "🔑 5/6. Настройка переменных окружения (.env)..."
su - "$BOT_USER" -c "cd '$TARGET_DIR' && ./venv/bin/python setup_env.py"

echo "⚙️ 6/6. Настройка автозапуска 24/7 через systemd..."
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
ExecStart=$TARGET_DIR/venv/bin/python $TARGET_DIR/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo ""
echo "✅ Готово! Бот успешно установлен и запущен в фоновом режиме."
echo "📊 Проверить статус: systemctl status $SERVICE_NAME"
echo "📜 Смотреть логи:    journalctl -u $SERVICE_NAME -f"
