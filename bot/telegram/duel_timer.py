"""Таймер дуэли: по истечении времени завершает бой по очкам или ничьей."""

import asyncio

from aiogram.types import Message

from bot.domain.engine import DuelEngine
from bot.domain.models import DuelSession
from bot.services.duel_service import DuelService
from bot.telegram.duel_cards import announce_outcome


def start_duel_timer(
    session: DuelSession, message: Message, engine: DuelEngine, service: DuelService, timeout_sec: float
) -> None:
    session.timeout_task = asyncio.create_task(_duel_timeout_watcher(session, message, engine, service, timeout_sec))


async def _duel_timeout_watcher(
    session: DuelSession, message: Message, engine: DuelEngine, service: DuelService, timeout_sec: float
) -> None:
    try:
        await asyncio.sleep(timeout_sec)
        outcome = engine.resolve_timeout(session)
        if outcome is not None:
            await announce_outcome(message, outcome, service)
    except asyncio.CancelledError:
        pass
