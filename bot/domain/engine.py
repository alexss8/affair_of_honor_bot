"""Игровая логика: приглашения, сессии и переходы фаз боя. Без aiogram и без БД.

Все переходы состояния — синхронные методы без await, поэтому в asyncio они атомарны:
проверка условия и изменение состояния не могут быть прерваны другим обработчиком.
"""

import asyncio
import logging
import time

from bot.domain.models import (
    ChoiceResult,
    DuelInvite,
    DuelOutcome,
    DuelPhase,
    DuelSession,
    MoveKind,
    OutcomeKind,
    Player,
    Zone,
)
from bot.domain.phrases import get_round_phrase

START_LOG = "Бой начался! Выбирайте действия."


class DuelEngine:
    def __init__(self, logger: logging.Logger, default_hp: int = 5, invite_ttl_sec: float = 300):
        self.logger = logger
        self.default_hp = default_hp
        self.invite_ttl_sec = invite_ttl_sec
        self.sessions: dict[tuple[int, int], DuelSession] = {}
        self.invites: dict[tuple[int, int], DuelInvite] = {}

    # ---------- Приглашения ----------

    def create_invite(
        self,
        chat_id: int,
        message_id: int,
        challenger_id: int,
        challenger_name: str,
        target_id: int,
        target_name: str,
        now: float | None = None,
    ) -> DuelInvite:
        now = time.time() if now is None else now
        self._purge_expired_invites(now)
        invite = DuelInvite(
            chat_id=chat_id,
            message_id=message_id,
            challenger_id=challenger_id,
            challenger_name=challenger_name,
            target_id=target_id,
            target_name=target_name,
            expires_at=now + self.invite_ttl_sec,
        )
        self.invites[(chat_id, message_id)] = invite
        return invite

    def get_invite(self, chat_id: int, message_id: int) -> DuelInvite | None:
        return self.invites.get((chat_id, message_id))

    def close_invite(self, invite: DuelInvite) -> bool:
        """Снимает приглашение (отказ, отзыв, истечение). False, если его уже кто-то закрыл."""
        return self.invites.pop((invite.chat_id, invite.message_id), None) is not None

    def accept_invite(self, invite: DuelInvite) -> DuelSession | None:
        """Принимает приглашение и начинает бой в том же сообщении.

        Returns:
            Сессию или None, если приглашение уже закрыто или один из игроков занят.
        """
        if self.get_invite(invite.chat_id, invite.message_id) is not invite:
            return None
        if self.is_user_busy(invite.chat_id, invite.challenger_id) or self.is_user_busy(
            invite.chat_id, invite.target_id
        ):
            return None
        self.close_invite(invite)
        return self.start_duel(
            invite.chat_id,
            invite.message_id,
            invite.challenger_id,
            invite.challenger_name,
            invite.target_id,
            invite.target_name,
        )

    def _purge_expired_invites(self, now: float) -> None:
        for key in [key for key, inv in self.invites.items() if inv.is_expired(now)]:
            del self.invites[key]

    # ---------- Сессии ----------

    def is_user_busy(self, chat_id: int, user_id: int) -> bool:
        return any(s.chat_id == chat_id and s.get_player(user_id) is not None for s in self.sessions.values())

    def get_session(self, chat_id: int, msg_id: int) -> DuelSession | None:
        return self.sessions.get((chat_id, msg_id))

    def start_duel(self, chat_id: int, msg_id: int, p1_id: int, p1_name: str, p2_id: int, p2_name: str) -> DuelSession:
        p1 = Player(id=p1_id, name=p1_name, hp=self.default_hp)
        p2 = Player(id=p2_id, name=p2_name, hp=self.default_hp)
        session = DuelSession(
            chat_id=chat_id,
            message_id=msg_id,
            p1=p1,
            p2=p2,
            attacker_id=p1.id,
            max_hp=self.default_hp,
            last_log=START_LOG,
        )
        self.sessions[(chat_id, msg_id)] = session
        self.logger.info(f"Начата дуэль в чате {chat_id}: {p1.name} ({p1.id}) vs {p2.name} ({p2.id})")
        return session

    def drop_session(self, chat_id: int, msg_id: int) -> DuelSession | None:
        """Удаляет сессию без записи в рейтинг (например, при удалении сообщения)."""
        session = self.sessions.get((chat_id, msg_id))
        if session:
            self._finish(session)
        return session

    def cancel_user_duel(self, chat_id: int, user_id: int) -> DuelSession | None:
        """Находит активную дуэль игрока в чате и аннулирует её без записи в рейтинг."""
        for session in list(self.sessions.values()):
            if session.chat_id == chat_id and session.get_player(user_id) is not None:
                self._finish(session)
                self.logger.info(f"Дуэль в чате {chat_id} (msg {session.message_id}) отменена игроком {user_id}")
                return session
        return None

    def interrupt_all(self) -> tuple[list[DuelSession], list[DuelInvite]]:
        """Снимает все бои и приглашения без записи в рейтинг (остановка бота).

        Returns:
            Снятые сессии и приглашения — их сохраняют для восстановления и помечают карточки.
        """
        sessions = list(self.sessions.values())
        invites = list(self.invites.values())
        for session in sessions:
            self._finish(session)
        self.invites.clear()
        if sessions or invites:
            self.logger.info(f"Прервано при остановке: боёв {len(sessions)}, вызовов {len(invites)}")
        return sessions, invites

    def restore(self, sessions: list[DuelSession], invites: list[DuelInvite]) -> None:
        """Возвращает в игру бои и вызовы, сохранённые при остановке бота.

        Бой, где оба игрока уже выбрали ход, продолжается с фазы ROLLING — раунд нужно досчитать.
        """
        for session in sessions:
            both_chose = session.p1.choice is not None and session.p2.choice is not None
            session.phase = DuelPhase.ROLLING if both_chose else DuelPhase.CHOOSING
            self.sessions[(session.chat_id, session.message_id)] = session
        for invite in invites:
            self.invites[(invite.chat_id, invite.message_id)] = invite
        if sessions or invites:
            self.logger.info(f"Восстановлено после рестарта: боёв {len(sessions)}, вызовов {len(invites)}")

    def _finish(self, session: DuelSession) -> None:
        session.phase = DuelPhase.FINISHED
        self.sessions.pop((session.chat_id, session.message_id), None)
        task = session.timeout_task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()

    # ---------- Ходы ----------

    def choose(self, session: DuelSession, player: Player, kind: MoveKind, zone: Zone) -> ChoiceResult:
        if session.phase is not DuelPhase.CHOOSING:
            return ChoiceResult.ROUND_IN_PROGRESS
        is_attacker = player.id == session.attacker_id
        if is_attacker and kind is not MoveKind.ATTACK:
            return ChoiceResult.MUST_ATTACK
        if not is_attacker and kind is not MoveKind.DEFEND:
            return ChoiceResult.MUST_DEFEND
        if player.choice == zone and session.surrender_confirm_id is None:
            return ChoiceResult.ALREADY_CHOSEN
        player.choice = zone
        session.surrender_confirm_id = None
        if session.p1.choice and session.p2.choice:
            session.phase = DuelPhase.ROLLING
            return ChoiceResult.ROUND_READY
        return ChoiceResult.ACCEPTED

    def resolve_round(self, session: DuelSession) -> DuelOutcome | None:
        """Рассчитывает раунд в фазе ROLLING. Возвращает итог, если бой окончен нокаутом."""
        if session.phase is not DuelPhase.ROLLING:
            raise ValueError(f"resolve_round вызван в фазе {session.phase}")
        att, dfn = session.attacker, session.defender
        is_blocked = att.choice == dfn.choice
        if not is_blocked:
            dfn.hp -= 1
        session.last_log = get_round_phrase(att.choice, is_blocked, att.name, dfn.name)

        if dfn.hp <= 0:
            self._finish(session)
            return DuelOutcome(OutcomeKind.KNOCKOUT, session, winner=att, loser=dfn)

        session.attacker_id = dfn.id
        session.p1.choice = session.p2.choice = None
        session.phase = DuelPhase.CHOOSING
        return None

    # ---------- Сдача и таймаут ----------

    def request_surrender(self, session: DuelSession, player: Player) -> bool:
        """Первое нажатие «Сдаться» просит подтверждения. True — сдача подтверждена вторым нажатием."""
        if session.surrender_confirm_id == player.id:
            return True
        session.surrender_confirm_id = player.id
        return False

    def surrender(self, session: DuelSession, loser: Player) -> DuelOutcome:
        self._finish(session)
        return DuelOutcome(OutcomeKind.SURRENDER, session, winner=session.get_opponent(loser.id), loser=loser)

    def resolve_timeout(self, session: DuelSession) -> DuelOutcome | None:
        """Завершает бой по времени. None — бой уже был завершён иначе (ничья — это OutcomeKind.DRAW)."""
        if self.get_session(session.chat_id, session.message_id) is not session:
            return None
        self._finish(session)
        p1, p2 = session.p1, session.p2
        if p1.hp == p2.hp:
            return DuelOutcome(OutcomeKind.DRAW, session)
        winner, loser = (p1, p2) if p1.hp > p2.hp else (p2, p1)
        return DuelOutcome(OutcomeKind.TIMEOUT, session, winner=winner, loser=loser)
