"""
Compound v3 (Comet) protocol handler.

Supports Arbitrum (USDC Comet) and Base (USDC Comet).
Uses isLiquidatable() and getBorrowBalanceOf() to find liquidatable accounts.
Borrower list is built from Supply and Withdraw events (Transfer events on Comet).
"""

import asyncio
from typing import List

from web3 import AsyncWeb3

from hr_monitor.config import config
from hr_monitor.protocols.base_protocol import BaseProtocol, LiquidatablePosition
from hr_monitor.utils.logger import setup_logger
from hr_monitor.utils.logs import get_logs_chunked

logger = setup_logger(__name__)

COMET_ABI = [
    {
        "name": "isLiquidatable",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "getBorrowBalanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getCollateralReserves",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "quoteCollateral",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "baseAmount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "userBasic",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [
            {"name": "principal", "type": "int104"},
            {"name": "baseTrackingIndex", "type": "uint64"},
            {"name": "baseTrackingAccrued", "type": "uint64"},
            {"name": "assetsIn", "type": "uint16"},
            {"name": "reserved", "type": "uint8"},
        ],
    },
    {
        "name": "decimals",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

# keccak256("Supply(address,address,uint256)")
SUPPLY_EVENT_SIG = "0xd1cf3d156d5f8f0d50f6c122ed609cec09d35c9b9fb3fff6ea0959134dae424e"
# keccak256("Withdraw(address,address,uint256)")
WITHDRAW_EVENT_SIG = "0x9b1bfa7fa9ee420a16e124f794c35ac9f90472acc99140eb2f6447c714cad8eb"
# keccak256("AbsorbCollateral(address,address,address,uint256,uint256)")
ABSORB_EVENT_SIG = "0xe52a667f71ec761b9b381c7b76ca9b852adf7e8905da0e0ad49986a0a6871815"

DEPLOYMENTS = {
    "Arbitrum": {
        # Compound v3 USDC Comet on Arbitrum
        "comet": "0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf",
        "chain": "Arbitrum",
        "explorer": "https://arbiscan.io/address/{}",
        "base_token_decimals": 6,  # USDC
        "liquidation_bonus": 8.0,
    },
    "Base": {
        # Compound v3 USDbC Comet on Base — verify address on https://basescan.org
        "comet": "0xb125E6687d4313864e53df431d5425969c15Eb2c",
        "chain": "Base",
        "explorer": "https://basescan.org/address/{}",
        "base_token_decimals": 6,  # USDC
        "liquidation_bonus": 8.0,
    },
}


class CompoundV3Protocol(BaseProtocol):
    def __init__(self, w3: AsyncWeb3, chain_name: str):
        self.w3 = w3
        self.chain_name = chain_name
        deployment = DEPLOYMENTS[chain_name]
        self.comet_address = AsyncWeb3.to_checksum_address(deployment["comet"])
        self.explorer_url = deployment["explorer"]
        self.base_token_decimals = deployment["base_token_decimals"]
        self.liquidation_bonus = deployment["liquidation_bonus"]
        self.comet = self.w3.eth.contract(address=self.comet_address, abi=COMET_ABI)
        self._borrowers: List[str] = []

    async def get_borrowers(self) -> List[str]:
        try:
            latest = await self.w3.eth.block_number
            from_block = max(0, latest - config.MAX_BLOCKS_SCAN)
            borrowers: set = set()

            # Collect addresses from both Supply and Withdraw events so we catch
            # accounts that may have gone underwater via collateral price drops
            # without generating a new Supply event.
            for event_sig in (SUPPLY_EVENT_SIG, WITHDRAW_EVENT_SIG):
                try:
                    logs = await get_logs_chunked(
                        self.w3,
                        {
                            "address": self.comet_address,
                            "topics": [event_sig],
                            "fromBlock": from_block,
                            "toBlock": latest,
                        },
                    )
                    for log in logs:
                        if len(log["topics"]) >= 3:
                            addr = "0x" + log["topics"][2].hex()[-40:]
                            borrowers.add(AsyncWeb3.to_checksum_address(addr))
                except Exception as exc:
                    logger.warning(
                        "Compound v3 %s get_borrowers (sig %s) error: %s",
                        self.chain_name,
                        event_sig,
                        exc,
                    )

            self._borrowers = list(borrowers)
            logger.info(
                "Compound v3 %s: found %d unique accounts",
                self.chain_name,
                len(self._borrowers),
            )
        except Exception as exc:
            logger.error("Compound v3 %s get_borrowers error: %s", self.chain_name, exc)
        return self._borrowers

    async def _check_position(self, address: str) -> LiquidatablePosition | None:
        try:
            borrow_balance = await self.comet.functions.getBorrowBalanceOf(address).call()
            if borrow_balance == 0:
                return None

            scale = 10 ** self.base_token_decimals
            debt_usd = borrow_balance / scale

            if debt_usd < config.MIN_POSITION_USD:
                return None

            liquidatable = await self.comet.functions.isLiquidatable(address).call()
            if liquidatable:
                # For Compound-style, HF is represented as 999.0 sentinel
                # and shortfall is the key metric
                return LiquidatablePosition(
                    protocol="Compound v3",
                    chain=self.chain_name,
                    address=address,
                    health_factor=999.0,  # Compound does not expose HF directly
                    collateral_usd=0.0,   # not easily available without extra calls
                    debt_usd=debt_usd,
                    shortfall_usd=debt_usd,
                    liquidation_bonus=self.liquidation_bonus,
                )
        except Exception as exc:
            logger.debug(
                "Compound v3 %s check_position %s: %s", self.chain_name, address, exc
            )
        return None

    async def get_liquidatable_positions(self) -> List[LiquidatablePosition]:
        if not self._borrowers:
            await self.get_borrowers()

        sem = asyncio.Semaphore(config.RPC_SEMAPHORE_SIZE)

        async def _limited(addr: str):
            async with sem:
                return await self._check_position(addr)

        results: List[LiquidatablePosition] = []
        checked = await asyncio.gather(*[_limited(addr) for addr in self._borrowers], return_exceptions=True)
        for item in checked:
            if isinstance(item, LiquidatablePosition):
                results.append(item)
        logger.info(
            "Compound v3 %s: %d liquidatable out of %d accounts",
            self.chain_name,
            len(results),
            len(self._borrowers),
        )
        return results
