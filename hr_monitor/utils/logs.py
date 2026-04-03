"""
Utility for fetching event logs in chunks to avoid 413 Payload Too Large
errors on public RPC endpoints that limit eth_getLogs response size.
"""

from typing import Any, Dict, List

from web3 import AsyncWeb3

from hr_monitor.config import config


async def get_logs_chunked(
    w3: AsyncWeb3,
    filter_params: Dict[str, Any],
) -> List[Any]:
    """
    Call eth_getLogs in chunks of config.LOG_CHUNK_SIZE blocks.

    filter_params must contain 'fromBlock' and 'toBlock' as integers.
    All other filter keys (address, topics, …) are forwarded unchanged.
    """
    from_block: int = filter_params["fromBlock"]
    to_block: int = filter_params["toBlock"]
    chunk_size: int = config.LOG_CHUNK_SIZE

    all_logs: List[Any] = []
    start = from_block
    while start <= to_block:
        end = min(start + chunk_size - 1, to_block)
        chunk_params = {**filter_params, "fromBlock": start, "toBlock": end}
        logs = await w3.eth.get_logs(chunk_params)
        all_logs.extend(logs)
        start = end + 1

    return all_logs
