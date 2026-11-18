import html
import math

from bot.domain.models import DuelInvite, DuelSession, OutcomeKind, Player, PlayerStats
from bot.services.duel_service import FinishedDuel

FALLBACK_NICK = "Дуэлянт"


def format_nick(username: str | None, first_name: str | None) -> str:
    """Отображаемый ник (@username или имя), экранированный для HTML."""
    raw = f"@{username}" if username else (first_name or FALLBACK_NICK)
    return html.escape(raw)


def escape_nick(user) -> str:
    return format_nick(user.username, user.first_name)


def render_invite(p1_name: str, p2_name: str) -> str:
    return f"🔥 <b>{p1_name}</b> вызывает <b>{p2_name}</b> на дуэль! ⚔️"


INVITE_EXPIRED = "⌛ Вызов на дуэль истёк — соперник не ответил вовремя."
_PAUSE_NOTE = "\n\n⏸ <i>Бот перезапускается — {what} продолжится сразу после запуска, время на паузе.</i>"


def render_paused(session: DuelSession) -> str:
    return render_battle(session) + _PAUSE_NOTE.format(what="бой")


def render_invite_paused(invite: DuelInvite) -> str:
    return render_invite(invite.challenger_name, invite.target_name) + _PAUSE_NOTE.format(what="вызов")


def render_cancelled(session: DuelSession, canceler_name: str) -> str:
    return (
        f"🚫 <b>ДУЭЛЬ ОТМЕНЕНА</b>\n\n<i>Бой между {session.p1.name} и {session.p2.name} "
        f"аннулирован игроком {canceler_name} (без изменения рейтинга).</i>"
    )


def render_interrupted(session: DuelSession) -> str:
    return (
        f"⚠️ <b>БОЙ ПРЕРВАН</b>\n\n<i>Бой между {session.p1.name} и {session.p2.name} "
        "прерван перезапуском бота, рейтинг не изменён.</i>"
    )


def render_invite_interrupted(invite: DuelInvite) -> str:
    return (
        f"⚠️ Вызов {invite.challenger_name} → {invite.target_name} снят: бот перезапускался. Брось вызов заново: /duel"
    )


def render_battle(session: DuelSession) -> str:
    def hp_bar(hp: int) -> str:
        return "❤️" * hp + "🖤" * (session.max_hp - hp)

    log_block = f"<i>{session.last_log}</i>\n\n" if session.last_log else ""

    att_icon = "✅" if session.attacker.choice else "🔴"
    def_icon = "✅" if session.defender.choice else "🟢"

    return (
        f"⚔️ <b>ДУЭЛЬ</b> ⚔️\n\n"
        f"{session.p1.name}: {hp_bar(session.p1.hp)}\n"
        f"{session.p2.name}: {hp_bar(session.p2.hp)}\n\n"
        f"{log_block}"
        f"{att_icon} Атакует: <b>{session.attacker.name}</b>\n"
        f"{def_icon} Защищается: <b>{session.defender.name}</b>"
    )


def render_top(stats: list[PlayerStats], limit: int = 10) -> str:
    if not stats:
        return "🏆 В этом чате еще не было дуэлей. Станьте первыми!"
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 <b>ТОП-{limit} ДУЭЛЯНТОВ ЧАТА</b> 🏆\n"]
    for i, s in enumerate(stats, 1):
        prefix = medals[i - 1] if i <= 3 else f"<b>{i}.</b>"
        lines.append(f"{prefix} {s.nickname} — <b>{s.wins}</b> побед / {s.losses} пор. ({s.winrate}%)")
    return "\n".join(lines)


def render_victory(
    duel_id: int, winner: Player, reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🏆"
) -> str:
    return (
        f"{icon} <b>ДУЭЛЬ #{duel_id} ОКОНЧЕНА!</b>\n\n"
        f"<i>{reason}</i>\n\n"
        f"Победитель: <b>{winner.name}</b> (+1 в рейтинг)\n\n"
        f"{render_top(stats, limit)}"
    )


def render_draw(reason: str, stats: list[PlayerStats], limit: int = 10, icon: str = "🤝") -> str:
    return (
        f"{icon} <b>ДУЭЛЬ ОКОНЧЕНА ВНИЧЬЮ!</b>\n\n"
        f"<i>{reason}</i>\n\n"
        f"Рейтинг игроков не изменился.\n\n"
        f"{render_top(stats, limit)}"
    )


def render_outcome(finished: FinishedDuel, limit: int) -> str:
    """Итоговая карточка для любого исхода боя."""
    outcome = finished.outcome
    winner, loser = outcome.winner, outcome.loser
    if outcome.kind is OutcomeKind.DRAW:
        reason = f"⏰ Время дуэли вышло! У обоих бойцов осталось по {outcome.session.p1.hp} ❤️."
        return render_draw(reason, finished.top, limit, icon="⏰")
    if outcome.kind is OutcomeKind.SURRENDER:
        reason, icon = f"{loser.name} выбросил белый флаг и сдался!", "🏳️"
    elif outcome.kind is OutcomeKind.TIMEOUT:
        reason = f"⏰ Время дуэли вышло! {winner.name} ({winner.hp} ❤️) оказался сильнее {loser.name} ({loser.hp} ❤️)."
        icon = "⏰"
    else:
        reason, icon = outcome.session.last_log, "🏆"
    return render_victory(finished.duel_id, winner, reason, finished.top, limit, icon=icon)


ERROR_NOTICE = "⚠️ Что-то сломалось на нашей стороне. Попробуй ещё раз; если бой завис — /cancel"


def render_slow_choice(wait_sec: float) -> str:
    """Ответ на ход, когда карточка обновится не сразу: у чата пауза после 429 или длинная очередь."""
    return (
        "⏳ Выбор принят! Telegram притормаживает бота — слишком много боёв сразу, "
        f"карточка обновится через ~{math.ceil(wait_sec)} с"
    )


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: plural(1, "жизнь", "жизни", "жизней") -> "жизнь"."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def format_duration(seconds: float) -> str:
    """300 -> "5 минут", 90 -> "90 секунд"."""
    total = int(seconds)
    if total >= 60 and total % 60 == 0:
        minutes = total // 60
        return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"
    return f"{total} {plural(total, 'секунда', 'секунды', 'секунд')}"


def render_help(default_hp: int, top_limit: int, duel_timeout_sec: float) -> str:
    return (
        "⚔️ <b>ДУЭЛЬНЫЙ БОТ — ПРАВИЛА И КОМАНДЫ</b>\n\n"
        "<b>Как начать бой:</b>\n"
        "• Напишите <code>/duel @ник</code> (выбрав соперника из списка)\n"
        "• Или ответьте (Reply) на любое сообщение соперника командой /duel\n\n"
        "<b>Правила дуэли:</b>\n"
        f"• У каждого бойца по <b>{default_hp} {plural(default_hp, 'жизни', 'жизни', 'жизней')}</b>.\n"
        "• Игроки ходят по очереди: один атакует (сверху или снизу), второй защищается.\n"
        "• Выбор делается вслепую через кнопки под сообщением.\n"
        "• Если блок совпал с ударом — урон не проходит. Если нет — защитник теряет 1 жизнь.\n"
        f"• На весь бой отводится <b>{format_duration(duel_timeout_sec)}</b>. "
        "Если время выйдет, победит боец с большим запасом жизней.\n\n"
        "<b>Команды чата:</b>\n"
        "/duel @ник — вызвать на бой\n"
        "/cancel — отменить свой текущий бой (если соперник пропал)\n"
        f"/top — топ-{top_limit} дуэлянтов чата\n"
        "/help — показать эту инструкцию"
    )
