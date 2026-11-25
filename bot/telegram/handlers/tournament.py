"""Турниры со своим Elo-рейтингом: /tstart и /tend (админы группы и владелец бота), /ttop для всех."""

import datetime
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.storage.base import BaseRepository
from bot.telegram.callbacks import ConfirmAction, TournamentEndCb
from bot.telegram.permissions import is_chat_admin
from bot.telegram.safe_api import safe_answer, safe_answer_callback, safe_edit_text, safe_reply
from bot.telegram.ui import keyboards, texts

router = Router(name="tournament")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))


@router.message(Command("tstart"))
async def cmd_tournament_start(
    message: Message, command: CommandObject, bot: Bot, repo: BaseRepository, config: Config, logger: logging.Logger
):
    chat_id = message.chat.id
    if not await is_chat_admin(bot, chat_id, message.from_user.id, config):
        await safe_reply(message, texts.TOURNAMENT_DENIED)
        return
    title = " ".join((command.args or "").split())[: texts.TOURNAMENT_TITLE_MAX].strip()
    title = title or f"Турнир {datetime.date.today():%d.%m.%Y}"
    tournament = repo.start_tournament(chat_id, title)
    if tournament is None:
        active = repo.get_active_tournament(chat_id)
        await safe_reply(message, texts.render_tournament_already_open(active.title if active else title))
        return
    logger.info("Открыт турнир #%s «%s» админом %s", tournament.tournament_id, tournament.title, message.from_user.id)
    await safe_answer(message, texts.render_tournament_started(tournament.title, texts.user_nick(message.from_user)))


@router.message(Command("tend"))
async def cmd_tournament_end(message: Message, bot: Bot, repo: BaseRepository, config: Config):
    admin_id = message.from_user.id
    if not await is_chat_admin(bot, message.chat.id, admin_id, config):
        await safe_reply(message, texts.TOURNAMENT_DENIED)
        return
    tournament = repo.get_active_tournament(message.chat.id)
    if tournament is None:
        await safe_reply(message, texts.NO_TOURNAMENT)
        return
    await safe_answer(
        message,
        texts.render_tournament_end_confirm(tournament.title),
        reply_markup=keyboards.tournament_end_keyboard(admin_id, tournament.tournament_id),
    )


@router.callback_query(TournamentEndCb.filter())
async def on_tournament_end(
    cb: CallbackQuery,
    callback_data: TournamentEndCb,
    bot: Bot,
    repo: BaseRepository,
    config: Config,
    logger: logging.Logger,
):
    if cb.from_user.id != callback_data.admin_id:
        await safe_answer_callback(cb, texts.TOP_RESET_NOT_YOURS, show_alert=True)
        return
    if callback_data.action == ConfirmAction.CANCEL:
        await safe_answer_callback(cb)
        await safe_edit_text(cb.message, texts.TOURNAMENT_END_CANCELLED)
        return
    chat_id = cb.message.chat.id
    # Rights are checked again: the admin may have been demoted since the command.
    if not await is_chat_admin(bot, chat_id, cb.from_user.id, config):
        await safe_answer_callback(cb, texts.TOURNAMENT_DENIED, show_alert=True)
        return
    active = repo.get_active_tournament(chat_id)
    # No await between the check and the close: a stale card cannot close a tournament opened after it.
    if active is None or active.tournament_id != callback_data.tournament_id:
        await safe_answer_callback(cb)
        await safe_edit_text(cb.message, texts.TOURNAMENT_ALREADY_CLOSED)
        return
    tournament = repo.end_tournament(chat_id)
    await safe_answer_callback(cb)
    logger.info("Закрыт турнир #%s «%s» админом %s", tournament.tournament_id, tournament.title, cb.from_user.id)
    standings = repo.get_tournament_standings(tournament.tournament_id, config.top_limit)
    await safe_edit_text(cb.message, texts.render_tournament_table(tournament.title, standings, final=True))


@router.message(Command("ttop"))
async def cmd_tournament_top(message: Message, repo: BaseRepository, config: Config):
    tournament = repo.get_active_tournament(message.chat.id)
    if tournament is None:
        await safe_reply(message, texts.NO_TOURNAMENT)
        return
    standings = repo.get_tournament_standings(tournament.tournament_id, config.top_limit)
    await safe_answer(message, texts.render_tournament_table(tournament.title, standings))
