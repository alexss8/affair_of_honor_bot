"""Единое место завершения дуэли: запись результата в БД, лог и свежий топ для итоговой карточки."""

import logging
from dataclasses import dataclass

from bot.domain.models import DuelOutcome, PlayerStats
from bot.storage.base import BaseRepository


@dataclass(frozen=True)
class FinishedDuel:
    outcome: DuelOutcome
    duel_id: int | None  # None для ничьей — в рейтинг не пишется
    top: list[PlayerStats]


class DuelService:
    def __init__(self, repo: BaseRepository, logger: logging.Logger, top_limit: int):
        self.repo = repo
        self.logger = logger
        self.top_limit = top_limit

    def finish(self, outcome: DuelOutcome) -> FinishedDuel:
        session = outcome.session
        duel_id = None
        if outcome.is_rated:
            winner, loser = outcome.winner, outcome.loser
            duel_id = self.repo.record_duel(session.chat_id, winner, loser)
            self.logger.info(
                f"Дуэль #{duel_id} завершена ({outcome.kind}, чат {session.chat_id}). "
                f"Победитель: {winner.name} ({winner.hp} HP), проигравший: {loser.name} ({loser.hp} HP)"
            )
        else:
            self.logger.info(f"Дуэль в чате {session.chat_id} (msg {session.message_id}) завершилась ничьей.")
        top = self.repo.get_top(session.chat_id, self.top_limit)
        return FinishedDuel(outcome=outcome, duel_id=duel_id, top=top)
