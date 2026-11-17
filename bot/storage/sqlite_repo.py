import os
import sqlite3

from bot.domain.models import Player, PlayerStats


class SQLiteRepository:
    def __init__(self, db_path: str = "duels.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        return self._conn

    def init(self) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS stats ("
                "chat_id INTEGER, user_id INTEGER, nickname TEXT, "
                "wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, "
                "PRIMARY KEY (chat_id, user_id))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS duels_history ("
                "duel_id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, winner_id INTEGER, loser_id INTEGER, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS chat_users ("
                "chat_id INTEGER, user_id INTEGER, username TEXT, first_name TEXT, "
                "PRIMARY KEY (chat_id, user_id))"
            )
        if os.path.exists(self.db_path):
            os.chmod(self.db_path, 0o600)

    def save_user(self, chat_id: int, user_id: int, username: str | None, first_name: str) -> None:
        self.save_users_batch([(chat_id, user_id, username, first_name)])

    def save_users_batch(self, users: list[tuple[int, int, str | None, str]]) -> None:
        if not users:
            return
        rows = [(cid, uid, uname.lower() if uname else None, fname) for cid, uid, uname, fname in users]
        conn = self._get_conn()
        with conn:
            conn.executemany(
                "INSERT INTO chat_users (chat_id, user_id, username, first_name) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(chat_id, user_id) DO UPDATE SET "
                "username = excluded.username, first_name = excluded.first_name",
                rows,
            )

    def get_user_by_username(self, chat_id: int, username: str) -> tuple[int, str] | None:
        clean_username = username.lstrip("@").lower()
        cur = self._get_conn().cursor()
        cur.execute(
            "SELECT user_id, username FROM chat_users WHERE chat_id = ? AND username = ?", (chat_id, clean_username)
        )
        row = cur.fetchone()
        if row:
            user_id, uname = row
            return user_id, f"@{uname}"
        return None

    def get_user_names(self, chat_id: int, user_id: int) -> tuple[str | None, str] | None:
        """Возвращает (username, first_name) из кэша участников чата или None."""
        cur = self._get_conn().cursor()
        cur.execute("SELECT username, first_name FROM chat_users WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

    def record_duel(self, chat_id: int, winner: Player, loser: Player) -> int:
        conn = self._get_conn()
        with conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO duels_history (chat_id, winner_id, loser_id) VALUES (?, ?, ?)",
                (chat_id, winner.id, loser.id),
            )
            duel_id = cur.lastrowid
            for player, w_inc, l_inc in ((winner, 1, 0), (loser, 0, 1)):
                cur.execute(
                    "INSERT INTO stats (chat_id, user_id, nickname, wins, losses) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(chat_id, user_id) DO UPDATE SET "
                    "wins = wins + ?, losses = losses + ?, nickname = excluded.nickname",
                    (chat_id, player.id, player.name, w_inc, l_inc, w_inc, l_inc),
                )
            return duel_id

    def get_top(self, chat_id: int, limit: int = 10) -> list[PlayerStats]:
        cur = self._get_conn().cursor()
        cur.execute(
            "SELECT nickname, wins, losses FROM stats WHERE chat_id = ? ORDER BY wins DESC, losses ASC LIMIT ?",
            (chat_id, limit),
        )
        return [PlayerStats(nick, wins, losses) for nick, wins, losses in cur.fetchall()]

    def get_health_stats(self) -> dict:
        cur = self._get_conn().cursor()
        cur.execute("SELECT COUNT(*) FROM duels_history")
        total_duels = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM duels_history WHERE date(created_at) = date('now')")
        today_duels = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM chat_users")
        cached_users = cur.fetchone()[0]
        db_size_kb = round(os.path.getsize(self.db_path) / 1024, 1) if os.path.exists(self.db_path) else 0.0
        return {
            "total_duels": total_duels,
            "today_duels": today_duels,
            "cached_users": cached_users,
            "db_size_kb": db_size_kb,
            "queue_size": 0,
        }
