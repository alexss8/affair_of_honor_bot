import os
import re
import sqlite3

from bot.domain.models import DuelInvite, DuelSession, Player, PlayerStats, Zone
from bot.storage.migrations import migrate

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _text(value: str | None) -> str | None:
    """Текст, который можно записать в базу: одиночные суррогаты (их нельзя закодировать в UTF-8) → «�».

    Любые нормальные символы (кириллица, эмодзи, RTL и т.д.) не меняются.
    """
    return None if value is None else _LONE_SURROGATE.sub("\ufffd", value)


class SQLiteRepository:
    def __init__(self, db_path: str = "duels.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        return self._conn

    def init(self) -> int:
        """Настраивает соединение и применяет миграции схемы. Возвращает версию схемы."""
        conn = self._get_conn()
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        version = migrate(conn)
        if os.path.exists(self.db_path):
            os.chmod(self.db_path, 0o600)
        return version

    def save_user(self, chat_id: int, user_id: int, username: str | None, first_name: str) -> None:
        self.save_users_batch([(chat_id, user_id, username, first_name)])

    def save_users_batch(self, users: list[tuple[int, int, str | None, str]]) -> None:
        if not users:
            return
        rows = [(cid, uid, _text(uname.lower()) if uname else None, _text(fname)) for cid, uid, uname, fname in users]
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
                    (chat_id, player.id, _text(player.name), w_inc, l_inc, w_inc, l_inc),
                )
            return duel_id

    def get_top(self, chat_id: int, limit: int = 10) -> list[PlayerStats]:
        cur = self._get_conn().cursor()
        cur.execute(
            "SELECT nickname, wins, losses FROM stats WHERE chat_id = ? ORDER BY wins DESC, losses ASC LIMIT ?",
            (chat_id, limit),
        )
        return [PlayerStats(nick, wins, losses) for nick, wins, losses in cur.fetchall()]

    def save_suspended(self, sessions: list[DuelSession], invites: list[DuelInvite], now: float) -> None:
        """Сохраняет бои и вызовы при остановке; таймеры замораживаются (храним остаток времени)."""
        duel_rows = [
            (
                s.chat_id,
                s.message_id,
                s.p1.id,
                _text(s.p1.name),
                s.p1.hp,
                s.p1.choice,
                s.p2.id,
                _text(s.p2.name),
                s.p2.hp,
                s.p2.choice,
                s.attacker_id,
                s.max_hp,
                _text(s.last_log),
                None if s.deadline is None else s.deadline - now,
            )
            for s in sessions
        ]
        invite_rows = [
            (
                i.chat_id,
                i.message_id,
                i.challenger_id,
                _text(i.challenger_name),
                i.target_id,
                _text(i.target_name),
                i.expires_at - now,
            )
            for i in invites
        ]
        conn = self._get_conn()
        with conn:
            conn.executemany(f"INSERT OR REPLACE INTO suspended_duels VALUES ({', '.join('?' * 14)})", duel_rows)
            conn.executemany("INSERT OR REPLACE INTO suspended_invites VALUES (?, ?, ?, ?, ?, ?, ?)", invite_rows)

    def pop_suspended(self, now: float) -> tuple[list[DuelSession], list[DuelInvite]]:
        """Забирает сохранённые бои и вызовы (и очищает снимок); время отсчитывается от now."""
        conn = self._get_conn()
        with conn:
            duel_rows = conn.execute("SELECT * FROM suspended_duels").fetchall()
            invite_rows = conn.execute("SELECT * FROM suspended_invites").fetchall()
            conn.execute("DELETE FROM suspended_duels")
            conn.execute("DELETE FROM suspended_invites")
        sessions = [
            DuelSession(
                chat_id=row[0],
                message_id=row[1],
                p1=_player(*row[2:6]),
                p2=_player(*row[6:10]),
                attacker_id=row[10],
                max_hp=row[11],
                last_log=row[12],
                deadline=None if row[13] is None else now + row[13],
            )
            for row in duel_rows
        ]
        invites = [
            DuelInvite(chat_id, message_id, ch_id, ch_name, t_id, t_name, expires_at=now + remaining)
            for chat_id, message_id, ch_id, ch_name, t_id, t_name, remaining in invite_rows
        ]
        return sessions, invites

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


def _player(player_id: int, name: str, hp: int, choice: str | None) -> Player:
    return Player(id=player_id, name=name, hp=hp, choice=Zone(choice) if choice else None)
