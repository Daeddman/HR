"""
Utility for fetching event logs in chunks to avoid 413 Payload Too Large
errors on public RPC endpoints that limit eth_getLogs response size.

Also handles 429 Too Many Requests with exponential backoff retries and
an optional inter-chunk delay to stay under public-RPC rate limits.
"""

import asyncio
import logging
from typing import Any, Dict, List

from web3 import AsyncWeb3

from hr_monitor.config import config

logger = logging.getLogger(__name__)


async def get_logs_chunked(
    w3: AsyncWeb3,
    filter_params: Dict[str, Any],
) -> List[Any]:
    """
    Call eth_getLogs in chunks of config.LOG_CHUNK_SIZE blocks.

    filter_params must contain 'fromBlock' and 'toBlock' as integers.
    All other filter keys (address, topics, …) are forwarded unchanged.

    On 429 responses each chunk is retried up to config.LOG_MAX_RETRIES times
    with exponential backoff (base delay config.LOG_RETRY_BASE_DELAY seconds).
    A small sleep of config.LOG_CHUNK_DELAY seconds is inserted between chunks
    to reduce pressure on rate-limited public RPC endpoints.
    """
    from_block: int = filter_params["fromBlock"]
    to_block: int = filter_params["toBlock"]
    chunk_size: int = config.LOG_CHUNK_SIZE
    chunk_delay: float = config.LOG_CHUNK_DELAY
    max_retries: int = config.LOG_MAX_RETRIES
    retry_base: float = config.LOG_RETRY_BASE_DELAY

    all_logs: List[Any] = []
    start = from_block
    first_chunk = True
    while start <= to_block:
        if not first_chunk and chunk_delay > 0:
            await asyncio.sleep(chunk_delay)
        first_chunk = False

        end = min(start + chunk_size - 1, to_block)
        chunk_params = {**filter_params, "fromBlock": start, "toBlock": end}

        for attempt in range(max_retries + 1):
            try:
                logs = await w3.eth.get_logs(chunk_params)
                all_logs.extend(logs)
                break
            except Exception as exc:
                err_str = str(exc)
                is_429 = "429" in err_str or "Too Many Requests" in err_str
                if is_429 and attempt < max_retries:
                    wait = retry_base * (2 ** attempt)
                    logger.warning(
                        "eth_getLogs 429 on blocks %d-%d, retry %d/%d in %.1fs",
                        start, end, attempt + 1, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    raise

        start = end + 1

    return all_logs
