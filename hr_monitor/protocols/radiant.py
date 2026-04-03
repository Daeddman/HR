"""
Radiant Capital v2 protocol handler (Arbitrum).

Radiant is a fork of Aave v2 / RDNT lending market.
Pool address: 0xF4B1486DD74D07706052A33d31d7c0AAFD0659E1
getUserAccountData() returns the same tuple structure as Aave v2.
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
            {"name": "totalCollateralETH", "type": "uint256"},
            {"name": "totalDebtETH", "type": "uint256"},
            {"name": "availableBorrowsETH", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
    },
]

# Chainlink ETH/USD feed on Arbitrum — returns answer scaled by 1e8
CHAINLINK_ETH_USD_ADDRESS = "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"
CHAINLINK_ABI = [
    {
        "name": "latestRoundData",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
    }
]

# Borrow(address indexed reserve, address user, address indexed onBehalfOf,
#        uint256 amount, uint8 interestRateMode, uint256 borrowRate, uint16 referralCode)
BORROW_EVENT_SIG = "0xc6a898309e823ee50bac64e45ca8adba6690e99e7841c45d754e2a38e9019d9b"

POOL_ADDRESS = "0xF4B1486DD74D07706052A33d31d7c0AAFD0659E1"
CHAIN = "Arbitrum"
EXPLORER = "https://arbiscan.io/address/{}"
LIQUIDATION_BONUS = 5.0

WEI = 10 ** 18
ETH_UNIT = 10 ** 18

# Refresh the ETH price every this many position-check calls (reduces oracle RPC traffic).
_ETH_PRICE_REFRESH_EVERY = 50


class RadiantProtocol(BaseProtocol):
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.pool_address = AsyncWeb3.to_checksum_address(POOL_ADDRESS)
        self.pool = self.w3.eth.contract(address=self.pool_address, abi=POOL_ABI)
        self._chainlink = self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(CHAINLINK_ETH_USD_ADDRESS),
            abi=CHAINLINK_ABI,
        )
        self._borrowers: List[str] = []
        self._eth_price_usd: float = 0.0
        self._price_call_count: int = 0

    async def _get_eth_price(self) -> float:
        """Fetch ETH/USD price from Chainlink; fall back to cached value on error."""
        self._price_call_count += 1
        if self._eth_price_usd > 0 and self._price_call_count % _ETH_PRICE_REFRESH_EVERY != 0:
            return self._eth_price_usd
        try:
            data = await self._chainlink.functions.latestRoundData().call()
            # answer is int256 scaled by 1e8
            price = data[1] / 10 ** 8
            if price > 0:
                self._eth_price_usd = price
                logger.debug("Radiant: ETH/USD price updated to %.2f", price)
        except Exception as exc:
            logger.warning("Radiant: failed to fetch ETH price from Chainlink: %s", exc)
        return self._eth_price_usd if self._eth_price_usd > 0 else 3000.0

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
            logger.info("Radiant %s: found %d unique borrowers", CHAIN, len(self._borrowers))
        except Exception as exc:
            logger.error("Radiant get_borrowers error: %s", exc)
        return self._borrowers

    async def _check_position(self, address: str) -> LiquidatablePosition | None:
        try:
            data = await self.pool.functions.getUserAccountData(address).call()
            total_collateral_eth, total_debt_eth, _, _, _, health_factor_raw = data

            if total_debt_eth == 0:
                return None

            eth_price = await self._get_eth_price()
            debt_usd = (total_debt_eth / ETH_UNIT) * eth_price
            collateral_usd = (total_collateral_eth / ETH_UNIT) * eth_price

            if debt_usd < config.MIN_POSITION_USD:
                return None

            hf = health_factor_raw / WEI
            if hf < config.HF_ALERT_THRESHOLD:
                shortfall_usd = max(0.0, debt_usd - collateral_usd)
                return LiquidatablePosition(
                    protocol="Radiant Capital",
                    chain=CHAIN,
                    address=address,
                    health_factor=hf,
                    collateral_usd=collateral_usd,
                    debt_usd=debt_usd,
                    shortfall_usd=shortfall_usd,
                    liquidation_bonus=LIQUIDATION_BONUS,
                )
        except Exception as exc:
            logger.debug("Radiant check_position %s: %s", address, exc)
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
            "Radiant: %d liquidatable out of %d borrowers", len(results), len(self._borrowers)
        )
        return results

