"""
Telegram bot using aiogram 3.x.

Provides:
  - Automatic liquidation alerts sent to TELEGRAM_CHAT_ID
  - /start        — welcome message
  - /status       — monitoring status
  - /threshold    — current HF thresholds
  - /setthreshold <value>  — update HF_ALERT_THRESHOLD at runtime (owner only)
  - /setcritical  <value>  — update HF_CRITICAL_THRESHOLD at runtime (owner only)
  - /stats        — session statistics (scan count, errors, avg position size)
  - /protocols    — per-protocol borrower count and error info
  - /pause        — pause monitoring (owner only)
  - /resume       — resume monitoring (owner only)
"""

from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message

from hr_monitor.config import config
from hr_monitor.protocols.base_protocol import LiquidatablePosition
from hr_monitor.utils.logger import setup_logger

logger = setup_logger(__name__)

EXPLORER_URLS = {
    "Arbitrum": "https://arbiscan.io/address/{}",
    "BSC": "https://bscscan.com/address/{}",
    "Base": "https://basescan.org/address/{}",
    "Core": "https://scan.coredao.org/address/{}",
}

router = Router()


def _short_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def _is_authorized(message: Message) -> bool:
    """Return True only for the configured owner chat."""
    return str(message.chat.id) == config.TELEGRAM_CHAT_ID


def _format_alert(pos: LiquidatablePosition) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    explorer_template = EXPLORER_URLS.get(pos.chain, "https://etherscan.io/address/{}")
    explorer_url = explorer_template.format(pos.address)

    if pos.health_factor < config.HF_CRITICAL_THRESHOLD:
        header = "🚨 <b>КРИТИЧНО — HF &lt; {:.2f}</b>".format(config.HF_CRITICAL_THRESHOLD)
    else:
        header = "🔴 <b>ЛИКВИДАЦИЯ ДОСТУПНА</b>"

    if pos.health_factor >= 999.0:
        hf_line = "❤️ Health Factor: N/A (shortfall &gt; 0)"
    else:
        hf_line = f"❤️ Health Factor: {pos.health_factor:.3f}"

    # Shortfall line: only show a dollar amount when there actually is one
    if pos.shortfall_usd > 0:
        shortfall_line = f"💥 Shortfall: ${pos.shortfall_usd:,.2f}"
    elif pos.health_factor >= 999.0:
        shortfall_line = "💥 Shortfall: вычисляется"
    else:
        shortfall_line = "💥 Near-liquidation (shortfall = 0)"

    address_link = f'<a href="{explorer_url}">{_short_address(pos.address)}</a>'

    lines = [
        header,
        "",
        f"📋 Протокол: {pos.protocol}",
        f"🌐 Сеть: {pos.chain}",
        f"👤 Адрес: {address_link}",
        hf_line,
        f"💰 Залог: ${pos.collateral_usd:,.2f}" if pos.collateral_usd > 0 else "💰 Залог: N/A",
        f"💸 Долг: ${pos.debt_usd:,.2f}",
        shortfall_line,
        f"🎁 Бонус ликвидатора: {pos.liquidation_bonus:.1f}%",
        "",
        f"⏰ {now_utc}",
    ]
    return "\n".join(lines)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    text = (
        "👋 <b>HR Monitor Bot</b>\n\n"
        "Я отслеживаю позиции в lending протоколах и уведомляю о ликвидируемых позициях.\n\n"
        "<b>Поддерживаемые протоколы:</b>\n"
        "• Aave v3 (Arbitrum, Base)\n"
        "• Radiant Capital (Arbitrum)\n"
        "• Compound v3 (Arbitrum, Base)\n"
        "• Venus (BSC)\n"
        "• Seamless Protocol (Base)\n"
        "• Moonwell (Base)\n"
        "• CoLend (Core DAO)\n\n"
        "<b>Команды:</b>\n"
        "/status — статус мониторинга\n"
        "/threshold — текущие пороги HF\n"
        "/setthreshold &lt;value&gt; — изменить порог предупреждения\n"
        "/setcritical &lt;value&gt; — изменить критический порог\n"
        "/stats — статистика сессии\n"
        "/protocols — список протоколов\n"
        "/pause — приостановить мониторинг\n"
        "/resume — возобновить мониторинг\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("status"))
async def cmd_status(message: Message, monitor=None) -> None:
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    last_scan = (
        monitor.last_scan_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        if monitor.last_scan_time
        else "ещё не запускался"
    )
    paused_str = "⏸ Мониторинг приостановлен\n" if monitor.is_paused else ""
    text = (
        f"📊 <b>Статус мониторинга</b>\n\n"
        f"{paused_str}"
        f"✅ Активных протоколов: {monitor.active_protocols}\n"
        f"⏱ Последний скан: {last_scan}\n"
        f"🔁 Интервал скана: {config.SCAN_INTERVAL} сек\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("threshold"))
async def cmd_threshold(message: Message) -> None:
    text = (
        f"⚙️ <b>Пороги Health Factor</b>\n\n"
        f"🟡 Порог предупреждения: {config.HF_ALERT_THRESHOLD}\n"
        f"🔴 Критический порог: {config.HF_CRITICAL_THRESHOLD}\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("setthreshold"))
async def cmd_setthreshold(message: Message) -> None:
    if not _is_authorized(message):
        await message.answer("⛔ Недостаточно прав.")
        return
    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        await message.answer("❌ Использование: /setthreshold <значение>\nПример: /setthreshold 1.1")
        return
    try:
        new_val = float(parts[1])
        if new_val <= 0 or new_val > 10:
            raise ValueError("Значение вне диапазона")
        config.HF_ALERT_THRESHOLD = new_val
        await message.answer(f"✅ Новый порог HF_ALERT_THRESHOLD: {new_val}")
    except ValueError:
        await message.answer("❌ Некорректное значение. Введите число, например: 1.05")


@router.message(Command("setcritical"))
async def cmd_setcritical(message: Message) -> None:
    if not _is_authorized(message):
        await message.answer("⛔ Недостаточно прав.")
        return
    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        await message.answer(
            "❌ Использование: /setcritical <значение>\nПример: /setcritical 1.01"
        )
        return
    try:
        new_val = float(parts[1])
        if new_val <= 0 or new_val > 10:
            raise ValueError("Значение вне диапазона")
        config.HF_CRITICAL_THRESHOLD = new_val
        await message.answer(f"✅ Новый порог HF_CRITICAL_THRESHOLD: {new_val}")
    except ValueError:
        await message.answer("❌ Некорректное значение. Введите число, например: 1.01")


@router.message(Command("stats"))
async def cmd_stats(message: Message, monitor=None) -> None:
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    total_errors = sum(monitor._error_counts.values())
    avg_usd = monitor.avg_position_usd
    avg_str = f"${avg_usd:,.2f}" if avg_usd > 0 else "N/A"
    text = (
        f"📈 <b>Статистика сессии</b>\n\n"
        f"🔁 Сканов выполнено: {monitor.total_scans}\n"
        f"💥 Найдено позиций: {monitor.total_found}\n"
        f"⚠️ Ошибок протоколов: {total_errors}\n"
        f"📊 Средний размер позиции: {avg_str}\n"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("protocols"))
async def cmd_protocols(message: Message, monitor=None) -> None:
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    info = monitor.get_protocol_info()
    lines = ["📡 <b>Протоколы</b>\n"]
    for p in info:
        err_str = f" | ⚠️ {p['errors']} ош." if p["errors"] else ""
        last_err = f"\n  ↳ {p['last_error'][:80]}" if p["last_error"] else ""
        lines.append(f"• <b>{p['name']}</b>: {p['borrowers']} borrowers{err_str}{last_err}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("pause"))
async def cmd_pause(message: Message, monitor=None) -> None:
    if not _is_authorized(message):
        await message.answer("⛔ Недостаточно прав.")
        return
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    monitor.pause()
    await message.answer("⏸ Мониторинг приостановлен.")


@router.message(Command("resume"))
async def cmd_resume(message: Message, monitor=None) -> None:
    if not _is_authorized(message):
        await message.answer("⛔ Недостаточно прав.")
        return
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    monitor.resume()
    await message.answer("▶️ Мониторинг возобновлён.")


class TelegramBot:
    def __init__(self, monitor):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher()
        self.monitor = monitor
        self.dp.include_router(router)
        # Inject monitor into handler context
        self.dp["monitor"] = monitor

    async def send_alert(self, pos: LiquidatablePosition) -> None:
        try:
            text = _format_alert(pos)
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            logger.info("Alert sent for %s (%s / %s)", pos.address, pos.protocol, pos.chain)
        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)

    async def start_polling(self) -> None:
        logger.info("Starting Telegram bot polling...")
        await self.dp.start_polling(self.bot)

    async def close(self) -> None:
        await self.bot.session.close()
