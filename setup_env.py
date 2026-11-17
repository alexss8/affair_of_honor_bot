import os
import stat
from getpass import getpass

ENV_FILE = ".env"

def prompt_value(label: str, default: str = "", is_secret: bool = False, required: bool = True) -> str:
    prompt_str = f"{label} [{default}]: " if default else f"{label}: "
    while True:
        raw = getpass(prompt_str) if is_secret else input(prompt_str)
        value = raw.strip() or default
        if value or not required:
            return value
        print("❌ Это поле обязательно для заполнения.")

def main() -> None:
    print("⚙️  Настройка окружения для Дуэльного Бота\n")

    if os.path.exists(ENV_FILE):
        overwrite = input(f"⚠️  Файл {ENV_FILE} уже существует. Перезаписать? (y/N): ").strip().lower()
        if overwrite != "y":
            print("Отменено. Текущий файл .env сохранен без изменений.")
            return

    token = prompt_value("Введите BOT_TOKEN от @BotFather (ввод скрыт)", is_secret=True)
    admin_id = prompt_value("Ваш Telegram ID для /status (можно оставить пустым)", default="", required=False)
    default_hp = prompt_value("Количество жизней у бойцов (DEFAULT_HP)", default="5")
    top_limit = prompt_value("Количество мест в топе (TOP_LIMIT)", default="10")
    duel_timeout = prompt_value("Длительность дуэли в секундах (DUEL_TIMEOUT_SEC)", default="300")
    db_path = prompt_value("Имя файла базы данных (DB_PATH)", default="duels.db")
    log_file = prompt_value("Имя файла логов (LOG_FILE)", default="bot.log")

    content = (
        f"BOT_TOKEN={token}\n"
        f"ADMIN_ID={admin_id}\n"
        f"DEFAULT_HP={default_hp}\n"
        f"TOP_LIMIT={top_limit}\n"
        f"DB_PATH={db_path}\n"
        f"LOG_FILE={log_file}\n"
    )

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write(content)

    try:
        os.chmod(ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(f"\n✅ Файл {ENV_FILE} успешно создан и защищен правами доступа (600)!")

if __name__ == "__main__":
    main()
