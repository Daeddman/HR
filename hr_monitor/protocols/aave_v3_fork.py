"""
Base class for Aave v3 protocol forks (Aave v3, Seamless, CoLend, …).

All these forks share the same:
  - Pool ABI (getUserAccountData)
  - Borrow event signature
  - HF computation (healthFactor / 1e18)
  - USD values expressed in 8-decimal base currency units

Subclasses pass deployment-specific parameters at construction time.
"""

import asyncio
from typing import List, Optional

from web3 import AsyncWeb3

from hr_monitor.config import config
from hr_monitor.protocols.base_protocol import BaseProtocol, LiquidatablePosition
from hr_monitor.utils.logger import setup_logger
from hr_monitor.utils.logs import get_logs_chunked

logger = setup_logger(__name__)

POOL_ABI = [
    {
        "name": "getUserAccountData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
    },
]

# Aave v3 Borrow event (same across all v3 forks)
DEFAULT_BORROW_EVENT_SIG = "0xb3d084820fb1a9decffb176436bd02b9a4cb29dd38bf53e8f7bb4bf9863e1b6a"

WEI = 10 ** 18
DEFAULT_CURRENCY_UNIT = 10 ** 8  # Aave v3 uses 8 decimals for USD values


class AaveV3ForkProtocol(BaseProtocol):
    """
    Generic handler for any Aave v3 fork.

    Parameters
    ----------
    w3                  AsyncWeb3 instance connected to the target chain
    pool_address        Lending pool contract address
    chain_name          Human-readable chain name (used in alerts and logs)
    protocol_name       Human-readable protocol name (used in alerts and logs)
    liquidation_bonus   Liquidator bonus percentage, e.g. 5.0 for 5 %
    currency_unit       Divisor for USD amounts returned by getUserAccountData
                        (10^8 for standard Aave v3 forks)
    borrow_event_sig    keccak256 topic0 of the Borrow event; defaults to
                        the standard Aave v3 signature
    min_position_usd    Minimum USD debt to report; None → config.MIN_POSITION_USD
    max_blocks_scan     Block window for borrower event scan; None → config.MAX_BLOCKS_SCAN
    """

    def __init__(
        self,
        w3: AsyncWeb3,
        pool_address: str,
        chain_name: str,
        protocol_name: str,
        liquidation_bonus: float,
        currency_unit: int = DEFAULT_CURRENCY_UNIT,
        borrow_event_sig: Optional[str] = None,
        min_position_usd: Optional[float] = None,
        max_blocks_scan: Optional[int] = None,
    ) -> None:
        self.w3 = w3
        self.pool_address = AsyncWeb3.to_checksum_address(pool_address)
        self.chain_name = chain_name
        self.protocol_name = protocol_name
        self.liquidation_bonus = liquidation_bonus
        self.currency_unit = currency_unit
        self.borrow_event_sig = borrow_event_sig or DEFAULT_BORROW_EVENT_SIG
        self._min_position_usd = min_position_usd
        self._max_blocks_scan = max_blocks_scan
        self.pool = w3.eth.contract(address=self.pool_address, abi=POOL_ABI)
        self._borrowers: List[str] = []

    @property
    def _effective_min_position_usd(self) -> float:
        return self._min_position_usd if self._min_position_usd is not None else config.MIN_POSITION_USD

    @property
    def _effective_max_blocks_scan(self) -> int:
        return self._max_blocks_scan if self._max_blocks_scan is not None else config.MAX_BLOCKS_SCAN

    async def get_borrowers(self) -> List[str]:
        """Fetch unique borrowers from recent Borrow events."""
        try:
            latest = await self.w3.eth.block_number
            from_block = max(0, latest - self._effective_max_blocks_scan)
            logs = await get_logs_chunked(
                self.w3,
                {
                    "address": self.pool_address,
                    "topics": [self.borrow_event_sig],
                    "fromBlock": from_block,
                    "toBlock": latest,
                },
            )
            borrowers: set = set()
            for log in logs:
                # onBehalfOf is the third indexed topic (topic[2])
                if len(log["topics"]) >= 3:
                    addr = "0x" + log["topics"][2].hex()[-40:]
                    borrowers.add(AsyncWeb3.to_checksum_address(addr))
            self._borrowers = list(borrowers)
            logger.info(
                "%s %s: found %d unique borrowers",
                self.protocol_name,
                self.chain_name,
                len(self._borrowers),
            )
        except Exception as exc:
            logger.error("%s %s get_borrowers error: %s", self.protocol_name, self.chain_name, exc)
        return self._borrowers

    async def _check_position(self, address: str) -> Optional[LiquidatablePosition]:
        try:
            data = await self.pool.functions.getUserAccountData(address).call()
            (
                total_collateral_base,
                total_debt_base,
                _available_borrows,
                _liquidation_threshold,
                _ltv,
                health_factor_raw,
            ) = data

            if total_debt_base == 0:
                return None

            debt_usd = total_debt_base / self.currency_unit
            collateral_usd = total_collateral_base / self.currency_unit

            if debt_usd < self._effective_min_position_usd:
                return None

            hf = health_factor_raw / WEI
            if hf < config.HF_ALERT_THRESHOLD:
                shortfall_usd = max(0.0, debt_usd - collateral_usd)
                return LiquidatablePosition(
                    protocol=self.protocol_name,
                    chain=self.chain_name,
                    address=address,
                    health_factor=hf,
                    collateral_usd=collateral_usd,
                    debt_usd=debt_usd,
                    shortfall_usd=shortfall_usd,
                    liquidation_bonus=self.liquidation_bonus,
                )
        except Exception as exc:
            logger.debug(
                "%s %s check_position %s: %s",
                self.protocol_name,
                self.chain_name,
                address,
                exc,
            )
        return None

    async def get_liquidatable_positions(self) -> List[LiquidatablePosition]:
        if not self._borrowers:
            await self.get_borrowers()

        sem = asyncio.Semaphore(config.RPC_SEMAPHORE_SIZE)

        async def _limited(addr: str) -> Optional[LiquidatablePosition]:
            async with sem:
                return await self._check_position(addr)

        results: List[LiquidatablePosition] = []
        checked = await asyncio.gather(
            *[_limited(addr) for addr in self._borrowers], return_exceptions=True
        )
        for item in checked:
            if isinstance(item, LiquidatablePosition):
                results.append(item)
        logger.info(
            "%s %s: %d liquidatable out of %d borrowers",
            self.protocol_name,
            self.chain_name,
            len(results),
            len(self._borrowers),
        )
        return results
