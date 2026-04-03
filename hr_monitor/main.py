"""
Entry point for the HR Monitor bot.

Starts two concurrent tasks:
  1. Telegram bot polling (aiogram)
  2. Main monitoring loop that scans protocols every SCAN_INTERVAL seconds
"""

import asyncio

from hr_monitor.bot import TelegramBot
from hr_monitor.config import config
from hr_monitor.monitor import MonitoringManager
from hr_monitor.utils.logger import setup_logger

logger = setup_logger(__name__)


async def monitoring_loop(bot: TelegramBot, monitor: MonitoringManager) -> None:
    """Periodically scan all protocols and send alerts for new liquidatable positions."""
    logger.info(
        "Monitoring started. Scan interval: %d sec, HF threshold: %s",
        config.SCAN_INTERVAL,
        config.HF_ALERT_THRESHOLD,
    )
    while True:
        try:
            positions = await monitor.scan_all()
            for pos in positions:
                await bot.send_alert(pos)
        except Exception as exc:
            logger.error("Unexpected error in monitoring loop: %s", exc)
        await asyncio.sleep(config.SCAN_INTERVAL)


async def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Please configure .env file.")
        return
    if not config.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID is not set. Please configure .env file.")
        return

    monitor = MonitoringManager()
    tg_bot = TelegramBot(monitor)

    try:
        await asyncio.gather(
            monitoring_loop(tg_bot, monitor),
            tg_bot.start_polling(),
        )
    finally:
        await tg_bot.close()


if __name__ == "__main__":
    asyncio.run(main())
