"""Единое место завершения дуэли: запись результата в БД, лог и свежий топ для итоговой карточки."""

import logging
from dataclasses import dataclass

from bot.domain.models import DuelOutcome, PlayerStats, RatingChange
from bot.storage.base import BaseRepository


@dataclass(frozen=True)
class FinishedDuel:
    outcome: DuelOutcome
    duel_id: int | None  # None для ничьей — в рейтинг не пишется
    top: list[PlayerStats]
    rating_change: RatingChange | None = None  # None — в чате нет открытого турнира или ничья


class DuelService:
    def __init__(self, repo: BaseRepository, logger: logging.Logger, top_limit: int):
        self.repo = repo
        self.logger = logger
        self.top_limit = top_limit

    def finish(self, outcome: DuelOutcome) -> FinishedDuel:
        session = outcome.session
        duel_id = None
        rating_change = None
        if outcome.is_rated:
            winner, loser = outcome.winner, outcome.loser
            duel_id = self.repo.record_duel(session.chat_id, winner, loser)
            self.logger.info(
                "Дуэль #%s завершена (%s, чат %s). Победитель: %s (%s HP), проигравший: %s (%s HP)",
                duel_id,
                outcome.kind,
                session.chat_id,
                winner.name,
                winner.hp,
                loser.name,
                loser.hp,
            )
            rating_change = self.repo.record_tournament_duel(session.chat_id, winner, loser)
            if rating_change is not None:
                self.logger.info(
                    "Турнир «%s»: %s %s (+%s), %s %s (-%s)",
                    rating_change.title,
                    winner.name,
                    rating_change.winner_rating,
                    rating_change.delta,
                    loser.name,
                    rating_change.loser_rating,
                    rating_change.delta,
                )
        else:
            self.logger.info("Дуэль в чате %s (msg %s) завершилась ничьей.", session.chat_id, session.message_id)
        top = self.repo.get_top(session.chat_id, self.top_limit)
        return FinishedDuel(outcome=outcome, duel_id=duel_id, top=top, rating_change=rating_change)
