"""
CoLend protocol handler (Core DAO).

CoLend is a fork of Aave v3 deployed on Core DAO (chain ID 1116).
Pool: 0x0CEa9F0F49F30d376390e480ba32f903B43B19C5
"""

import asyncio
from typing import List

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

# Borrow event (same as Aave v3)
BORROW_EVENT_SIG = "0xb3d084820fb1a9decffb176436bd02b9a4cb29dd38bf53e8f7bb4bf9863e1b6a"

POOL_ADDRESS = "0x0CEa9F0F49F30d376390e480ba32f903B43B19C5"
CHAIN = "Core"
EXPLORER = "https://scan.coredao.org/address/{}"
LIQUIDATION_BONUS = 5.0

WEI = 10 ** 18
BASE_CURRENCY_UNIT = 10 ** 8  # Aave v3 style 8-decimal USD


class ColendProtocol(BaseProtocol):
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.pool_address = AsyncWeb3.to_checksum_address(POOL_ADDRESS)
        self.pool = self.w3.eth.contract(address=self.pool_address, abi=POOL_ABI)
        self._borrowers: List[str] = []

    async def get_borrowers(self) -> List[str]:
        try:
            latest = await self.w3.eth.block_number
            from_block = max(0, latest - config.MAX_BLOCKS_SCAN)
            logs = await get_logs_chunked(
                self.w3,
                {
                    "address": self.pool_address,
                    "topics": [BORROW_EVENT_SIG],
                    "fromBlock": from_block,
                    "toBlock": latest,
                },
            )
            borrowers: set = set()
            for log in logs:
                if len(log["topics"]) >= 3:
                    addr = "0x" + log["topics"][2].hex()[-40:]
                    borrowers.add(AsyncWeb3.to_checksum_address(addr))
            self._borrowers = list(borrowers)
            logger.info("CoLend Core: found %d unique borrowers", len(self._borrowers))
        except Exception as exc:
            logger.error("CoLend get_borrowers error: %s", exc)
        return self._borrowers

    async def _check_position(self, address: str) -> LiquidatablePosition | None:
        try:
            data = await self.pool.functions.getUserAccountData(address).call()
            total_collateral_base, total_debt_base, _, _, _, health_factor_raw = data

            if total_debt_base == 0:
                return None

            debt_usd = total_debt_base / BASE_CURRENCY_UNIT
            collateral_usd = total_collateral_base / BASE_CURRENCY_UNIT

            if debt_usd < config.MIN_POSITION_USD:
                return None

            hf = health_factor_raw / WEI
            if hf < config.HF_ALERT_THRESHOLD:
                shortfall_usd = max(0.0, debt_usd - collateral_usd)
                return LiquidatablePosition(
                    protocol="CoLend",
                    chain=CHAIN,
                    address=address,
                    health_factor=hf,
                    collateral_usd=collateral_usd,
                    debt_usd=debt_usd,
                    shortfall_usd=shortfall_usd,
                    liquidation_bonus=LIQUIDATION_BONUS,
                )
        except Exception as exc:
            logger.debug("CoLend check_position %s: %s", address, exc)
        return None

    async def get_liquidatable_positions(self) -> List[LiquidatablePosition]:
        if not self._borrowers:
            await self.get_borrowers()

        results: List[LiquidatablePosition] = []
        tasks = [self._check_position(addr) for addr in self._borrowers]
        checked = await asyncio.gather(*tasks, return_exceptions=True)
        for item in checked:
            if isinstance(item, LiquidatablePosition):
                results.append(item)
        logger.info(
            "CoLend: %d liquidatable out of %d borrowers",
            len(results),
            len(self._borrowers),
        )
        return results
