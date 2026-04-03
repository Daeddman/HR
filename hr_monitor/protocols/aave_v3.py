"""
Aave v3 protocol handler.

Supports both Arbitrum and Base deployments.
Uses getUserAccountData() to check positions and filters Borrow events
to build the borrower list.
"""

import asyncio
from typing import List

from web3 import AsyncWeb3

from hr_monitor.config import config
from hr_monitor.protocols.base_protocol import BaseProtocol, LiquidatablePosition
from hr_monitor.utils.logger import setup_logger

logger = setup_logger(__name__)

# Minimal ABI — only the functions we need
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

DATA_PROVIDER_ABI = [
    {
        "name": "getAllReservesTokens",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {
                "name": "",
                "type": "tuple[]",
                "components": [
                    {"name": "symbol", "type": "string"},
                    {"name": "tokenAddress", "type": "address"},
                ],
            }
        ],
    },
]

# Borrow(address indexed reserve, address user, address indexed onBehalfOf,
#        uint256 amount, uint8 interestRateMode, uint256 borrowRate,
#        uint16 indexed referralCode)
BORROW_EVENT_SIG = "0xb3d084820fb1a9decffb176436bd02b9a4cb29dd38bf53e8f7bb4bf9863e1b6a"

# Aave v3 deployments
DEPLOYMENTS = {
    "Arbitrum": {
        "pool": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "data_provider": "0x69FA688f1Dc47d4B5d8029D5a35FB7a548310654",
        "liquidation_bonus": 5.0,
        "explorer": "https://arbiscan.io/address/{}",
    },
    "Base": {
        "pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        "data_provider": None,
        "liquidation_bonus": 5.0,
        "explorer": "https://basescan.org/address/{}",
    },
}

WEI = 10 ** 18
BASE_CURRENCY_UNIT = 10 ** 8  # Aave v3 uses 8 decimals for USD values


class AaveV3Protocol(BaseProtocol):
    """Aave v3 monitor for a single chain deployment."""

    def __init__(self, w3: AsyncWeb3, chain_name: str):
        self.w3 = w3
        self.chain_name = chain_name
        deployment = DEPLOYMENTS[chain_name]
        self.pool_address = AsyncWeb3.to_checksum_address(deployment["pool"])
        self.liquidation_bonus = deployment["liquidation_bonus"]
        self.explorer_url = deployment["explorer"]
        self.pool = self.w3.eth.contract(address=self.pool_address, abi=POOL_ABI)
        self._borrowers: List[str] = []

    async def get_borrowers(self) -> List[str]:
        """Fetch unique borrowers from recent Borrow events."""
        try:
            latest = await self.w3.eth.block_number
            from_block = max(0, latest - config.MAX_BLOCKS_SCAN)
            logs = await self.w3.eth.get_logs(
                {
                    "address": self.pool_address,
                    "topics": [BORROW_EVENT_SIG],
                    "fromBlock": from_block,
                    "toBlock": latest,
                }
            )
            # topic[2] = onBehalfOf (indexed), topic[1] = reserve, data contains user
            borrowers: set = set()
            for log in logs:
                # onBehalfOf is the third indexed topic
                if len(log["topics"]) >= 3:
                    addr = "0x" + log["topics"][2].hex()[-40:]
                    borrowers.add(AsyncWeb3.to_checksum_address(addr))
            self._borrowers = list(borrowers)
            logger.info(
                "Aave v3 %s: found %d unique borrowers", self.chain_name, len(self._borrowers)
            )
        except Exception as exc:
            logger.error("Aave v3 %s get_borrowers error: %s", self.chain_name, exc)
        return self._borrowers

    async def _check_position(self, address: str) -> LiquidatablePosition | None:
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

            debt_usd = total_debt_base / BASE_CURRENCY_UNIT
            collateral_usd = total_collateral_base / BASE_CURRENCY_UNIT

            if debt_usd < config.MIN_POSITION_USD:
                return None

            hf = health_factor_raw / WEI

            if hf < config.HF_ALERT_THRESHOLD:
                shortfall_usd = max(0.0, debt_usd - collateral_usd)
                return LiquidatablePosition(
                    protocol="Aave v3",
                    chain=self.chain_name,
                    address=address,
                    health_factor=hf,
                    collateral_usd=collateral_usd,
                    debt_usd=debt_usd,
                    shortfall_usd=shortfall_usd,
                    liquidation_bonus=self.liquidation_bonus,
                )
        except Exception as exc:
            logger.debug("Aave v3 %s check_position %s: %s", self.chain_name, address, exc)
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
            "Aave v3 %s: %d liquidatable out of %d borrowers",
            self.chain_name,
            len(results),
            len(self._borrowers),
        )
        return results
