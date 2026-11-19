"""Общие хелперы тестов: сборка Dispatcher, апдейты и callback_data, подменённые часы лимитера."""

import asyncio
import datetime
import logging
import time

from aiogram import Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update, User

from bot.config import Config
from bot.domain.engine import DuelEngine
from bot.domain.models import DuelInvite, MoveKind, Zone
from bot.services.alert_throttle import AlertThrottle
from bot.services.duel_service import DuelService
from bot.telegram import invite_timer
from bot.telegram.callbacks import InviteAction, InviteCb, MoveCb, SurrenderCb
from bot.telegram.handlers import include_handlers
from bot.telegram.handlers.errors import ERROR_NOTICE_COOLDOWN_SEC
from bot.telegram.rate_limiter import RateLimiter

TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
CHAT_ID = -1001
ALICE = User(id=10, is_bot=False, first_name="Alice", username="alice")
BOB = User(id=20, is_bot=False, first_name="Bob", username="bob")
TROLL = User(id=30, is_bot=False, first_name="Troll")

ACCEPT = InviteCb(action=InviteAction.ACCEPT).pack()
DECLINE = InviteCb(action=InviteAction.DECLINE).pack()
REVOKE = InviteCb(action=InviteAction.REVOKE).pack()
SURRENDER = SurrenderCb().pack()


def move(kind: MoveKind, zone: Zone) -> str:
    return MoveCb(kind=kind, zone=zone).pack()


ATTACK_TOP = move(MoveKind.ATTACK, Zone.TOP)
ATTACK_BOT = move(MoveKind.ATTACK, Zone.BOT)
DEFEND_TOP = move(MoveKind.DEFEND, Zone.TOP)
DEFEND_BOT = move(MoveKind.DEFEND, Zone.BOT)


class FakeClock:
    """Часы и sleep для лимитера: sleep мгновенно сдвигает время вперёд.

    Лимитер считает паузу от текущего времени перед каждым sleep, поэтому одновременные паузы не складываются.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps += 1
        self.now += delay
        await asyncio.sleep(0)


def max_in_window(times: list[float], period: float) -> int:
    """Наибольшее число отправок в любом полуоткрытом окне [t, t + period)."""
    return max(sum(1 for u in times if t <= u < t + period) for t in times)


def build_dispatcher(
    config: Config, repo, engine: DuelEngine, logger: logging.Logger, limiter: RateLimiter | None = None
) -> Dispatcher:
    """Dispatcher с теми же зависимостями, что и в bot/__main__.py.

    После теста обязательно сбросить router._parent_router = None (см. .claude/rules/tests.md).
    """
    dp = Dispatcher()
    dp["config"] = config
    dp["repo"] = repo
    dp["engine"] = engine
    dp["service"] = DuelService(repo=repo, logger=logger, top_limit=config.top_limit)
    dp["logger"] = logger
    dp["alerts"] = AlertThrottle()
    dp["error_notices"] = AlertThrottle(cooldown_sec=ERROR_NOTICE_COOLDOWN_SEC)
    dp["started_at"] = time.time()
    dp["limiter"] = limiter or RateLimiter()
    include_handlers(dp, config)
    return dp


def cancel_timers(engine: DuelEngine) -> None:
    for session in list(engine.sessions.values()):
        if session.timeout_task and not session.timeout_task.done():
            session.timeout_task.cancel()
    for task in list(invite_timer._tasks):
        task.cancel()


def make_invite(
    engine: DuelEngine,
    msg_id: int = 500,
    chat_id: int = CHAT_ID,
    challenger: tuple[int, str] = (10, "@alice"),
    target: tuple[int, str] = (20, "@bob"),
    now: float | None = None,
) -> DuelInvite:
    return engine.create_invite(chat_id, msg_id, challenger[0], challenger[1], target[0], target[1], now=now)


def make_callback_update(update_id: int, user: User, data: str, msg_id: int = 500, chat_id: int = CHAT_ID) -> Update:
    chat = Chat(id=chat_id, type="supergroup", title="Test Group")
    msg = Message(message_id=msg_id, date=datetime.datetime.now(), chat=chat, text="Battle")
    cb = CallbackQuery(id=str(update_id), from_user=user, chat_instance="123", message=msg, data=data)
    return Update(update_id=update_id, callback_query=cb)


def make_msg_update(
    update_id: int,
    user: User,
    text: str,
    entities: list[MessageEntity] | None = None,
    reply_to: Message | None = None,
    chat_id: int = CHAT_ID,
) -> Update:
    chat = Chat(id=chat_id, type="supergroup", title="Test Group")
    msg = Message(
        message_id=100 + update_id,
        date=datetime.datetime.now(),
        chat=chat,
        from_user=user,
        text=text,
        entities=entities,
        reply_to_message=reply_to,
    )
    return Update(update_id=update_id, message=msg)


def callback_answers(bot) -> list[AnswerCallbackQuery]:
    """Ответы на нажатия кнопок, отправленные подменённым ботом (AsyncMock(spec=Bot))."""
    return [c.args[0] for c in bot.call_args_list if c.args and isinstance(c.args[0], AnswerCallbackQuery)]


def sent_messages(bot) -> list[SendMessage]:
    """Сообщения, отправленные подменённым ботом через bot(SendMessage(...))."""
    return [c.args[0] for c in bot.call_args_list if c.args and isinstance(c.args[0], SendMessage)]
