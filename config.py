import os
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass(frozen=True)
class Config:
    bot_token: str
    default_hp: int = 5
    top_limit: int = 10
    db_path: str = "duels.db"
    log_file: str = "bot.log"

def load_config() -> Config:
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN не найден в файле .env")

    return Config(
        bot_token=token,
        default_hp=int(os.getenv("DEFAULT_HP", 5)),
        top_limit=int(os.getenv("TOP_LIMIT", 10)),
        db_path=os.getenv("DB_PATH", "duels.db"),
        log_file=os.getenv("LOG_FILE", "bot.log"),
    )
