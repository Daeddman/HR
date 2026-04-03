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
    CORE_RPC: str = os.getenv("CORE_RPC", "https://rpc.coredao.org/")

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

    # CoLend (Core DAO) uses ~3-second blocks, so 50k blocks ≈ 41 h.
    # A larger window is needed to find borrowers who opened positions earlier.
    # Default: 2_000_000 blocks ≈ 70 days at 3 s/block.
    COLEND_MAX_BLOCKS_SCAN: int = int(os.getenv("COLEND_MAX_BLOCKS_SCAN", "2000000"))

    # CoLend runs on Core DAO where typical positions are smaller than on Ethereum L1/L2.
    # Use a lower minimum to avoid filtering out legitimate positions.
    COLEND_MIN_POSITION_USD: float = float(os.getenv("COLEND_MIN_POSITION_USD", "100"))

    # How many scan cycles to keep the borrower list before refreshing it from on-chain events.
    # A refresh re-scans Borrow events so newly opened positions are detected.
    # Lower values = fresher borrower list but more RPC calls.
    BORROWER_REFRESH_CYCLES: int = int(os.getenv("BORROWER_REFRESH_CYCLES", "10"))

    # Maximum number of concurrent RPC eth_call requests per protocol.
    # Prevents overwhelming public RPC endpoints when checking hundreds of borrowers.
    RPC_SEMAPHORE_SIZE: int = int(os.getenv("RPC_SEMAPHORE_SIZE", "20"))

    # Path to JSON file used to persist cooldown state and runtime threshold overrides
    # across restarts. An empty string disables persistence.
    COOLDOWN_PERSIST_PATH: str = os.getenv("COOLDOWN_PERSIST_PATH", "cooldown_state.json")

    # Pre-filter for near-liquidation checks in Compound v2 forks (Venus / Moonwell).
    # Positions whose USD liquidity buffer exceeds this value are skipped during the
    # deep HF computation (avoids expensive per-market oracle calls for clearly safe positions).
    NEAR_LIQ_MAX_BUFFER_USD: float = float(os.getenv("NEAR_LIQ_MAX_BUFFER_USD", "100000"))

    # Maximum block range per single eth_getLogs request (prevents 413 errors on public RPCs)
    LOG_CHUNK_SIZE: int = int(os.getenv("LOG_CHUNK_SIZE", "2000"))

    # Seconds to sleep between consecutive eth_getLogs chunks (reduces 429 on public RPCs)
    LOG_CHUNK_DELAY: float = float(os.getenv("LOG_CHUNK_DELAY", "0.2"))

    # Retry settings for eth_getLogs when the RPC returns 429 Too Many Requests
    LOG_MAX_RETRIES: int = int(os.getenv("LOG_MAX_RETRIES", "5"))
    LOG_RETRY_BASE_DELAY: float = float(os.getenv("LOG_RETRY_BASE_DELAY", "2.0"))


config = Config()
