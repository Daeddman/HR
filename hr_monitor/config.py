import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # RPC endpoints
    ARB_RPC: str = os.getenv("ARB_RPC", "https://arb1.arbitrum.io/rpc")
    BSC_RPC: str = os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org/")
    BASE_RPC: str = os.getenv("BASE_RPC", "https://mainnet.base.org")

    # Health Factor thresholds
    HF_ALERT_THRESHOLD: float = float(os.getenv("HF_ALERT_THRESHOLD", "1.05"))
    HF_CRITICAL_THRESHOLD: float = float(os.getenv("HF_CRITICAL_THRESHOLD", "1.01"))

    # Scan interval in seconds
    SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL", "60"))

    # Minimum position in USD to avoid dust
    MIN_POSITION_USD: float = float(os.getenv("MIN_POSITION_USD", "1000"))

    # Alert cooldown in minutes — do not re-alert same address within this period
    ALERT_COOLDOWN_MINUTES: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "10"))

    # Maximum number of past blocks to scan for borrower events on startup
    MAX_BLOCKS_SCAN: int = int(os.getenv("MAX_BLOCKS_SCAN", "50000"))


config = Config()
