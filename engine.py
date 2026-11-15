import logging
from typing import Optional
from models import DuelSession, Player
from phrases import get_round_phrase
from repository import BaseRepository

class DuelEngine:
    def __init__(self, repo: BaseRepository, logger: logging.Logger, default_hp: int = 5):
        self.repo = repo
        self.logger = logger
        self.default_hp = default_hp
        self.sessions: dict[tuple[int, int], DuelSession] = {}

    def is_user_busy(self, chat_id: int, user_id: int) -> bool:
        return any(s.chat_id == chat_id and s.get_player(user_id) is not None for s in self.sessions.values())

    def get_session(self, chat_id: int, msg_id: int) -> Optional[DuelSession]:
        return self.sessions.get((chat_id, msg_id))

    def start_duel(self, chat_id: int, msg_id: int, p1_id: int, p1_name: str, p2_id: int, p2_name: str) -> DuelSession:
        p1 = Player(id=p1_id, name=p1_name, hp=self.default_hp)
        p2 = Player(id=p2_id, name=p2_name, hp=self.default_hp)
        session = DuelSession(
            chat_id=chat_id, message_id=msg_id,
            p1=p1, p2=p2, attacker_id=p1.id, max_hp=self.default_hp
        )
        self.sessions[(chat_id, msg_id)] = session
        self.logger.info(f"Начата дуэль в чате {chat_id}: {p1.name} ({p1.id}) vs {p2.name} ({p2.id})")
        return session

    def cancel_user_duel(self, chat_id: int, user_id: int) -> Optional[DuelSession]:
        """Находит активную дуэль игрока в чате и аннулирует её без записи в рейтинг."""
        for key, session in list(self.sessions.items()):
            if session.chat_id == chat_id and session.get_player(user_id) is not None:
                del self.sessions[key]
                self.logger.info(f"Дуэль в чате {chat_id} (msg {session.message_id}) отменена игроком {user_id}")
                return session
        return None

    def finish_duel(self, session: DuelSession, winner: Player, loser: Player) -> int:
        self.sessions.pop((session.chat_id, session.message_id), None)
        duel_id = self.repo.record_duel(session.chat_id, winner, loser)
        self.logger.info(f"Дуэль #{duel_id} завершена (чат {session.chat_id}). Победитель: {winner.name}, Проигравший: {loser.name}")
        return duel_id

    def resolve_round(self, session: DuelSession) -> tuple[str, bool]:
        att, dfn = session.attacker, session.defender
        is_blocked = (att.choice == dfn.choice)

        if not is_blocked:
            dfn.hp -= 1

        log = get_round_phrase(att.choice, is_blocked, att.name, dfn.name)

        if dfn.hp <= 0:
            return log, True

        session.attacker_id = dfn.id
        session.p1.choice = session.p2.choice = None
        return log, False
