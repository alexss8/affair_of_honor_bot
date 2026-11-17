"""Таймер дуэли: по истечении времени завершает бой по очкам или ничьей."""

import asyncio

from aiogram.types import Message

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelSession
from bot.storage.base import BaseRepository
from bot.telegram.safe_api import safe_edit_text
from bot.telegram.ui import texts


def start_duel_timer(
    session: DuelSession,
    message: Message,
    engine: DuelEngine,
    repo: BaseRepository,
    config: Config,
) -> None:
    session.timeout_task = asyncio.create_task(_duel_timeout_watcher(session, message, engine, repo, config))


async def _duel_timeout_watcher(
    session: DuelSession,
    message: Message,
    engine: DuelEngine,
    repo: BaseRepository,
    config: Config,
) -> None:
    try:
        await asyncio.sleep(config.duel_timeout_sec)
        if engine.get_session(session.chat_id, session.message_id) is not session:
            return

        outcome = engine.resolve_timeout(session)
        stats = repo.get_top(session.chat_id, config.top_limit)
        if outcome is not None:
            winner, loser = outcome
            duel_id = repo.record_duel(session.chat_id, winner, loser)
            engine.logger.info(
                f"Дуэль #{duel_id} завершена по таймауту (чат {session.chat_id}). "
                f"Победитель: {winner.name} ({winner.hp} HP), Проигравший: {loser.name} ({loser.hp} HP)"
            )
            reason = (
                f"⏰ Время дуэли вышло! {winner.name} ({winner.hp} ❤️) оказался сильнее {loser.name} ({loser.hp} ❤️)."
            )
            text = texts.render_victory(duel_id, winner, reason, stats, config.top_limit, icon="⏰")
        else:
            reason = f"⏰ Время дуэли вышло! У обоих бойцов осталось по {session.p1.hp} ❤️."
            text = texts.render_draw(reason, stats, config.top_limit, icon="⏰")

        await safe_edit_text(message, text)
    except asyncio.CancelledError:
        pass
