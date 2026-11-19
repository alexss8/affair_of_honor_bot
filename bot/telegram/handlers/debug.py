"""Отладочный стенд для тестового бота (DEBUG_TOOLS=1, см. TEST_STAND.md): команда /debug только для ADMIN_ID.

Роутер подключается раньше корневого (`include_handlers`): ход за бота-соперника делается до обычного
`handle_move`, после чего нажатие уходит дальше (SkipHandler) — бой идёт по настоящему коду, а не по копии.
"""

import contextlib
import html
import random
from typing import Any

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import Command
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import MoveKind, Zone
from bot.services.duel_service import DuelService
from bot.telegram.callbacks import DebugAction, DebugCb, MoveCb
from bot.telegram.duel_timer import open_battle
from bot.telegram.middlewares import log_context_middleware
from bot.telegram.rate_limiter import RateLimiter
from bot.telegram.safe_api import safe_answer, safe_answer_callback
from bot.telegram.ui import keyboards, texts

# Настоящие Telegram ID положительны — с живым игроком бот-соперник не совпадёт
BOT_OPPONENT_ID = -1
BOT_OPPONENT_NAME = "🤖 Бот-дуэлянт"
EVIL_NAME = html.escape("<b>Tom & Jerry</b> 🤖 " + "Я" * 40)
FLOOD_RETRY_SEC = 30


def _is_admin(event: TelegramObject, config: Config) -> bool:
    user = getattr(event, "from_user", None)
    return user is not None and user.id == config.admin_id


router = Router(name="debug")
router.message.filter(_is_admin)
router.callback_query.filter(_is_admin)
router.message.outer_middleware(log_context_middleware)
router.callback_query.outer_middleware(log_context_middleware)


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    await safe_answer(message, texts.DEBUG_MENU, reply_markup=keyboards.debug_keyboard())


@router.callback_query(DebugCb.filter(F.action.in_({DebugAction.BOT_DUEL, DebugAction.EVIL_NAME_DUEL})))
async def handle_bot_duel(
    cb: CallbackQuery, callback_data: DebugCb, engine: DuelEngine, service: DuelService, config: Config
):
    chat_id = cb.message.chat.id
    if engine.is_user_busy(chat_id, cb.from_user.id):
        return await safe_answer_callback(cb, texts.DEBUG_BUSY, show_alert=True)
    await safe_answer_callback(cb)
    card = await safe_answer(cb.message, texts.DEBUG_CARD_PLACEHOLDER)
    if card is None:
        return
    opponent_name = EVIL_NAME if callback_data.action is DebugAction.EVIL_NAME_DUEL else BOT_OPPONENT_NAME
    session = engine.start_duel(
        chat_id, card.message_id, cb.from_user.id, texts.escape_nick(cb.from_user), BOT_OPPONENT_ID, opponent_name
    )
    await open_battle(card, session, engine, service, config.duel_timeout_sec)


@router.callback_query(MoveCb.filter())
async def bot_opponent_moves_first(cb: CallbackQuery, engine: DuelEngine):
    """Бот выбирает ход раньше игрока, поэтому ход игрока сразу рассчитывает раунд в обычном handle_move."""
    session = engine.get_session(cb.message.chat.id, cb.message.message_id)
    opponent = session.get_player(BOT_OPPONENT_ID) if session else None
    if opponent is not None and opponent.choice is None and session.get_player(cb.from_user.id) is not None:
        kind = MoveKind.ATTACK if opponent.id == session.attacker_id else MoveKind.DEFEND
        engine.choose(session, opponent, kind, random.choice(list(Zone)))
    raise SkipHandler


async def _answer_flood(bot: Bot, method: SendMessage) -> Any:
    raise TelegramRetryAfter(method=method, message="debug: simulated flood", retry_after=FLOOD_RETRY_SEC)


@router.callback_query(DebugCb.filter(F.action == DebugAction.FLOOD))
async def handle_flood(cb: CallbackQuery, bot: Bot, limiter: RateLimiter):
    """Прогоняет через лимитер запрос, на который «Telegram» ответил 429 — срабатывает настоящая реакция лимитера."""
    chat_id = cb.message.chat.id
    with contextlib.suppress(TelegramRetryAfter):  # 429 пробрасывается лимитером дальше, как и настоящий
        await limiter(_answer_flood, bot, SendMessage(chat_id=chat_id, text="debug"))
    await safe_answer_callback(cb, texts.render_debug_flood(FLOOD_RETRY_SEC), show_alert=True)
