"""
Telegram bot using aiogram 3.x.

Provides:
  - Automatic liquidation alerts sent to TELEGRAM_CHAT_ID
  - /start  — welcome message
  - /status — monitoring status
  - /threshold — current HF thresholds
  - /setthreshold <value> — update HF_ALERT_THRESHOLD at runtime
  - /stats   — session statistics
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
}

router = Router()


def _short_address(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}"


def _format_alert(pos: LiquidatablePosition) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    explorer_template = EXPLORER_URLS.get(pos.chain, "https://etherscan.io/address/{}")
    explorer = explorer_template.format(pos.address)

    if pos.health_factor < config.HF_CRITICAL_THRESHOLD:
        header = "🚨 КРИТИЧНО — HF < {:.2f}\n".format(config.HF_CRITICAL_THRESHOLD)
    else:
        header = "🔴 ЛИКВИДАЦИЯ ДОСТУПНА\n"

    if pos.health_factor >= 999.0:
        hf_line = "❤️ Health Factor: N/A (shortfall > 0)"
    else:
        hf_line = f"❤️ Health Factor: {pos.health_factor:.3f}"

    lines = [
        header,
        f"📋 Протокол: {pos.protocol}",
        f"🌐 Сеть: {pos.chain}",
        f"👤 Адрес: {_short_address(pos.address)}",
        hf_line,
        f"💰 Залог: ${pos.collateral_usd:,.2f}" if pos.collateral_usd > 0 else "💰 Залог: N/A",
        f"💸 Долг: ${pos.debt_usd:,.2f}",
        f"💥 Shortfall: ${pos.shortfall_usd:,.2f}",
        f"🎁 Бонус ликвидатора: {pos.liquidation_bonus:.1f}%",
        "",
        f"🔗 Explorer: {explorer}",
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
        "• Moonwell (Base)\n\n"
        "<b>Команды:</b>\n"
        "/status — статус мониторинга\n"
        "/threshold — текущие пороги HF\n"
        "/setthreshold &lt;value&gt; — изменить порог\n"
        "/stats — статистика сессии\n"
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
    text = (
        f"📊 <b>Статус мониторинга</b>\n\n"
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


@router.message(Command("stats"))
async def cmd_stats(message: Message, monitor=None) -> None:
    if monitor is None:
        await message.answer("⚠️ Monitor недоступен.")
        return
    text = (
        f"📈 <b>Статистика сессии</b>\n\n"
        f"💥 Найдено ликвидируемых позиций: {monitor.total_found}\n"
    )
    await message.answer(text, parse_mode="HTML")


class TelegramBot:
    def __init__(self, monitor):
        self.bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        self.dp = Dispatcher()
        self.monitor = monitor
        # Pass monitor via middleware/closure trick
        self.dp.include_router(router)
        # Inject monitor into handler context
        self.dp["monitor"] = monitor

    async def send_alert(self, pos: LiquidatablePosition) -> None:
        try:
            text = _format_alert(pos)
            await self.bot.send_message(
                chat_id=config.TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=None,
            )
            logger.info("Alert sent for %s (%s / %s)", pos.address, pos.protocol, pos.chain)
        except Exception as exc:
            logger.error("Failed to send Telegram alert: %s", exc)

    async def start_polling(self) -> None:
        logger.info("Starting Telegram bot polling...")
        await self.dp.start_polling(self.bot)

    async def close(self) -> None:
        await self.bot.session.close()
