"""Бэкап базы бота: снимок duels.db в папку бэкапов, хранятся только последние N копий.

Работает при запущенном боте: онлайн-бэкап SQLite (то же, что `sqlite3 duels.db ".backup ..."`)
даёт согласованную копию вместе с содержимым WAL. Запускается по cron (ставит scripts/deploy.sh):

    cd ~/duel_bot && ./venv/bin/python scripts/backup_db.py --dir ~/backups
"""

import argparse
import datetime
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKUP_PREFIX = "duels_"
BACKUP_GLOB = f"{BACKUP_PREFIX}????-??-??.db"


def backup(db_path: Path, backup_dir: Path, keep: int = 7, today: datetime.date | None = None) -> Path:
    """Снимает копию базы и удаляет старые копии сверх keep.

    Returns:
        Путь к свежей копии.

    Raises:
        FileNotFoundError: базы нет (не создаём пустую вместо неё).
        sqlite3.DatabaseError: копия не прошла проверку целостности; старые копии не тронуты.
    """
    db_path, backup_dir = Path(db_path), Path(backup_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f"База не найдена: {db_path}")
    today = today or datetime.date.today()
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{BACKUP_PREFIX}{today.isoformat()}.db"
    tmp = target.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
        status = dst.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dst.close()
        src.close()
    if status != "ok":
        tmp.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(f"Копия базы повреждена: {status}")

    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    for old in sorted(backup_dir.glob(BACKUP_GLOB))[:-keep]:
        old.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Бэкап базы дуэльного бота")
    parser.add_argument("--db", default=os.getenv("DB_PATH", "duels.db"), help="путь к базе (по умолчанию DB_PATH)")
    parser.add_argument("--dir", required=True, help="папка для бэкапов")
    parser.add_argument("--keep", type=int, default=7, help="сколько последних копий хранить")
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep должен быть не меньше 1")

    target = backup(Path(args.db), Path(args.dir), keep=args.keep)
    print(f"Бэкап готов: {target} ({target.stat().st_size // 1024} КБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
