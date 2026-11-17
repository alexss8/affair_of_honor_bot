"""Жизненный цикл бота: бои и вызовы переживают перезапуск.

При остановке активные бои и вызовы сохраняются в БД (таймеры замораживаются), карточки встают на паузу.
При старте они восстанавливаются, карточки возвращаются с кнопками. Если снимок сохранить не удалось,
карточки помечаются прерванными — рейтинг при этом не меняется.
"""

import asyncio
import logging
import time

from aiogram import Bot, Router

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelInvite, DuelPhase, DuelSession
from bot.services.duel_service import DuelService
from bot.storage.base import BaseRepository
from bot.telegram.duel_cards import announce_outcome, card_message, refresh_card, show_final_notice
from bot.telegram.duel_timer import start_duel_timer
from bot.telegram.safe_api import safe_edit_text, safe_edit_text_by_id
from bot.telegram.ui import keyboards, texts

router = Router(name="lifecycle")

# Остановка не должна зависнуть из-за лимитов Telegram (429) при множестве активных боёв.
SHUTDOWN_NOTIFY_TIMEOUT_SEC = 10


@router.shutdown()
async def on_shutdown(bot: Bot, engine: DuelEngine, repo: BaseRepository, logger: logging.Logger) -> None:
    sessions, invites = engine.interrupt_all()
    if not sessions and not invites:
        return
    try:
        repo.save_suspended(sessions, invites, time.time())
        render_session, render_invite = texts.render_paused, texts.render_invite_paused
    except Exception:
        logger.exception("Не удалось сохранить активные бои: карточки будут помечены прерванными")
        render_session, render_invite = texts.render_interrupted, texts.render_invite_interrupted

    edits = [show_final_notice(bot, s, render_session(s)) for s in sessions] + [
        safe_edit_text_by_id(bot, i.chat_id, i.message_id, render_invite(i)) for i in invites
    ]
    try:
        await asyncio.wait_for(asyncio.gather(*edits), timeout=SHUTDOWN_NOTIFY_TIMEOUT_SEC)
    except TimeoutError:
        logger.warning("Не все карточки успели обновить до остановки")


@router.startup()
async def on_startup(
    bot: Bot,
    engine: DuelEngine,
    repo: BaseRepository,
    service: DuelService,
    config: Config,
    logger: logging.Logger,
) -> None:
    try:
        sessions, invites = repo.pop_suspended(time.time())
    except Exception:
        logger.exception("Не удалось загрузить бои, сохранённые перед перезапуском")
        return
    now = time.time()
    live = [i for i in invites if not i.is_expired(now)]
    expired = [i for i in invites if i.is_expired(now)]
    engine.restore(sessions, live)
    await asyncio.gather(
        *(_resume_duel(bot, s, engine, service, config.duel_timeout_sec) for s in sessions),
        *(_resume_invite(bot, i, engine) for i in live),
        *(safe_edit_text(card_message(bot, i.chat_id, i.message_id), texts.INVITE_EXPIRED) for i in expired),
    )


async def _resume_duel(
    bot: Bot, session: DuelSession, engine: DuelEngine, service: DuelService, full_timeout_sec: float
) -> None:
    message = card_message(bot, session.chat_id, session.message_id)
    if session.phase is DuelPhase.ROLLING:  # бот остановился во время броска — досчитываем раунд
        outcome = engine.resolve_round(session)
        if outcome is not None:
            return await announce_outcome(message, outcome, service)
    if not await refresh_card(message, session):
        engine.drop_session(session.chat_id, session.message_id)
        return
    remaining = full_timeout_sec if session.deadline is None else max(session.deadline - time.time(), 0)
    start_duel_timer(session, message, engine, service, remaining)


async def _resume_invite(bot: Bot, invite: DuelInvite, engine: DuelEngine) -> None:
    message = card_message(bot, invite.chat_id, invite.message_id)
    text = texts.render_invite(invite.challenger_name, invite.target_name)
    if not await safe_edit_text(message, text, reply_markup=keyboards.invite_keyboard()):
        engine.close_invite(invite)
