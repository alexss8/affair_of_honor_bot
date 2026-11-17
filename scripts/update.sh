#!/usr/bin/env bash
# Обновление бота на сервере без переустановки: бэкап базы → git pull → зависимости (если менялись) →
# перезапуск службы → проверка, что бот поднялся. .env, база и venv не затрагиваются.
#
# Запуск (от root):  bash /home/botuser/duel_bot/scripts/update.sh
#
# Системные настройки (служба systemd, cron, фаервол) этот скрипт не меняет — для них нужен scripts/deploy.sh;
# если он изменился в обновлении, скрипт об этом напомнит.
set -euo pipefail

BOT_USER="botuser"
SERVICE_NAME="duelbot"
START_CHECK_SEC=5

# Выполнить команду в папке бота от имени botuser.
as_bot() {
  su - "$BOT_USER" -c "cd '$APP_DIR' && $1"
}

changed() {
  [ -n "$(as_bot "git diff --name-only $OLD $NEW -- '$1'")" ]
}

# Всё тело — в функции: bash разбирает её целиком до запуска, поэтому git pull,
# переписывающий этот файл на ходу, не сломает выполнение.
main() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Запустите скрипт от имени root: bash $0"
    exit 1
  fi

  APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  local backup_dir
  backup_dir="$(dirname "$APP_DIR")/backups"
  OLD="$(as_bot "git rev-parse HEAD")"

  echo "💾 1/4. Бэкап базы перед обновлением..."
  if ! as_bot "./venv/bin/python scripts/backup_db.py --dir '$backup_dir'"; then
    echo "❌ Бэкап не удался — обновление остановлено, бот не тронут."
    exit 1
  fi

  echo "⬇️ 2/4. Забираем код (git pull)..."
  as_bot "git pull --ff-only -q"
  NEW="$(as_bot "git rev-parse HEAD")"
  if [ "$OLD" = "$NEW" ]; then
    echo "✅ Обновлений нет — бот не перезапускался."
    return 0
  fi
  as_bot "git log --oneline $OLD..$NEW"

  echo "📦 3/4. Зависимости..."
  if changed requirements.txt; then
    as_bot "./venv/bin/pip install -r requirements.txt -q"
  else
    echo "ℹ️  requirements.txt не менялся, пропускаем."
  fi

  echo "🔄 4/4. Перезапуск бота..."
  systemctl restart "$SERVICE_NAME" || true
  sleep "$START_CHECK_SEC"
  if ! systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "❌ Бот не запустился после обновления. Последние строки лога:"
    journalctl -u "$SERVICE_NAME" -n 30 --no-pager || true
    echo ""
    echo "↩️  Откат на прежнюю версию:"
    echo "   su - $BOT_USER -c \"cd ~/duel_bot && git checkout $OLD\" && systemctl restart $SERVICE_NAME"
    echo "   (базу при необходимости вернуть из $backup_dir; вернуться на ветку потом: git checkout main)"
    exit 1
  fi

  if changed scripts/deploy.sh; then
    echo "⚠️  Изменился scripts/deploy.sh — примените системные настройки: bash $APP_DIR/scripts/deploy.sh"
  fi
  echo "✅ Обновлено: ${OLD:0:7} → ${NEW:0:7}. Статус: systemctl status $SERVICE_NAME"
}

main "$@"
