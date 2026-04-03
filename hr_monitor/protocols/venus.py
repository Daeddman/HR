"""
Venus protocol handler (BNB Smart Chain).

Venus is a Compound v2 fork on BSC.
Comptroller: 0xfD36E2c2a6789Db23113685031d7F16329158384
Uses Venus Lens to fetch account balances and calculates Health Factor.
"""

import asyncio
from typing import List

from web3 import AsyncWeb3

from hr_monitor.config import config
from hr_monitor.protocols.base_protocol import BaseProtocol, LiquidatablePosition
from hr_monitor.utils.logger import setup_logger
from hr_monitor.utils.logs import get_logs_chunked

logger = setup_logger(__name__)

COMPTROLLER_ADDRESS = "0xfD36E2c2a6789Db23113685031d7F16329158384"
CHAIN = "BSC"
EXPLORER = "https://bscscan.com/address/{}"
LIQUIDATION_BONUS = 10.0  # Venus default ~10%

# Borrow(address borrower, uint borrowAmount, uint accountBorrows, uint totalBorrows)
BORROW_EVENT_SIG = "0x13ed6866d4e1ee6da46f845c46d7e54120883d75c5ea9a2dacc1c4ca8984ab80"

COMPTROLLER_ABI = [
    {
        "name": "getAccountLiquidity",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [
            {"name": "error", "type": "uint256"},
            {"name": "liquidity", "type": "uint256"},
            {"name": "shortfall", "type": "uint256"},
        ],
    },
    {
        "name": "getAllMarkets",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address[]"}],
    },
    {
        "name": "getAssetsIn",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "address[]"}],
    },
    {
        "name": "oracle",
        "type": "function",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
]

# Minimal ABI for any vToken — borrowBalanceStored returns balance in underlying base units.
VTOKEN_ABI = [
    {
        "name": "borrowBalanceStored",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "getAccountSnapshot",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [
            {"name": "error", "type": "uint256"},
            {"name": "vTokenBalance", "type": "uint256"},
            {"name": "borrowBalance", "type": "uint256"},
            {"name": "exchangeRateMantissa", "type": "uint256"},
        ],
    },
]

# Oracle ABI — getUnderlyingPrice returns price * 10^(36 - underlyingDecimals)
ORACLE_ABI = [
    {
        "name": "getUnderlyingPrice",
        "type": "function",
        "stateMutability": "view",
        "inputs": [{"name": "cToken", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]

WEI = 10 ** 18


class VenusProtocol(BaseProtocol):
    def __init__(self, w3: AsyncWeb3):
        self.w3 = w3
        self.comptroller_address = AsyncWeb3.to_checksum_address(COMPTROLLER_ADDRESS)
        self.comptroller = self.w3.eth.contract(
            address=self.comptroller_address, abi=COMPTROLLER_ABI
        )
        self._borrowers: List[str] = []
        self._markets: List[str] = []
        self._oracle_contract = None

    async def _get_markets(self) -> List[str]:
        if self._markets:
            return self._markets
        try:
            self._markets = await self.comptroller.functions.getAllMarkets().call()
        except Exception as exc:
            logger.error("Venus get_markets error: %s", exc)
        return self._markets

    async def _get_oracle_contract(self):
        if self._oracle_contract is None:
            try:
                addr = await self.comptroller.functions.oracle().call()
                self._oracle_contract = self.w3.eth.contract(
                    address=AsyncWeb3.to_checksum_address(addr), abi=ORACLE_ABI
                )
            except Exception as exc:
                logger.error("Venus: failed to fetch oracle address: %s", exc)
        return self._oracle_contract

    async def _compute_borrow_sum(self, address: str) -> int:
        """
        Return total borrow in USD * 1e18 units using Compound v2 oracle math:
            sumBorrows = Σ (borrowBalanceStored_i * oraclePrice_i / 1e18)
        where oraclePrice = getUnderlyingPrice(market) = price * 10^(36 - underlyingDecimals),
        and borrowBalance is in underlying base units, giving a result in USD * 1e18.
        """
        markets = await self.comptroller.functions.getAssetsIn(address).call()
        if not markets:
            return 0
        oracle = await self._get_oracle_contract()
        if oracle is None:
            return 0

        borrow_balances = await asyncio.gather(
            *[
                self.w3.eth.contract(
                    address=AsyncWeb3.to_checksum_address(m), abi=VTOKEN_ABI
                ).functions.borrowBalanceStored(address).call()
                for m in markets
            ],
            return_exceptions=True,
        )

        active = [
            (m, bb)
            for m, bb in zip(markets, borrow_balances)
            if not isinstance(bb, Exception) and bb > 0
        ]
        if not active:
            return 0

        prices = await asyncio.gather(
            *[
                oracle.functions.getUnderlyingPrice(
                    AsyncWeb3.to_checksum_address(m)
                ).call()
                for m, _ in active
            ],
            return_exceptions=True,
        )

        borrow_sum = 0
        for (_, bb), price in zip(active, prices):
            if isinstance(price, Exception) or price == 0:
                continue
            borrow_sum += price * bb // WEI
        return borrow_sum

    async def get_borrowers(self) -> List[str]:
        try:
            markets = await self._get_markets()
            latest = await self.w3.eth.block_number
            from_block = max(0, latest - config.MAX_BLOCKS_SCAN)
            borrowers: set = set()
            for market_addr in markets:
                try:
                    logs = await get_logs_chunked(
                        self.w3,
                        {
                            "address": AsyncWeb3.to_checksum_address(market_addr),
                            "topics": [BORROW_EVENT_SIG],
                            "fromBlock": from_block,
                            "toBlock": latest,
                        },
                    )
                    for log in logs:
                        # Non-indexed borrower address is first topic after event sig,
                        # stored in data as first 32 bytes
                        if log.get("data") and len(log["data"]) >= 66:
                            raw = log["data"]
                            addr_hex = raw[2:66] if isinstance(raw, str) else raw.hex()[0:64]
                            addr = "0x" + addr_hex[-40:]
                            borrowers.add(AsyncWeb3.to_checksum_address(addr))
                except Exception:
                    pass

            self._borrowers = list(borrowers)
            logger.info("Venus BSC: found %d unique borrowers", len(self._borrowers))
        except Exception as exc:
            logger.error("Venus get_borrowers error: %s", exc)
        return self._borrowers

    async def _check_position(self, address: str) -> LiquidatablePosition | None:
        try:
            result = await self.comptroller.functions.getAccountLiquidity(address).call()
            error, liquidity, shortfall = result

            if error != 0:
                return None

            if shortfall > 0:
                shortfall_usd = shortfall / WEI
                if shortfall_usd < config.MIN_POSITION_USD:
                    return None
                return LiquidatablePosition(
                    protocol="Venus",
                    chain=CHAIN,
                    address=address,
                    health_factor=999.0,  # shortfall > 0 means liquidatable
                    collateral_usd=0.0,
                    debt_usd=shortfall_usd,
                    shortfall_usd=shortfall_usd,
                    liquidation_bonus=LIQUIDATION_BONUS,
                )

            # Near-liquidation check: shortfall == 0 but HF may still be < alert threshold.
            # Skip positions with a large safety buffer to avoid expensive oracle calls.
            if liquidity / WEI > config.NEAR_LIQ_MAX_BUFFER_USD:
                return None
            try:
                borrow_sum = await self._compute_borrow_sum(address)
            except Exception as exc:
                logger.debug("Venus near_liq borrow_sum %s: %s", address, exc)
                return None
            if borrow_sum == 0:
                return None
            hf = 1.0 + liquidity / borrow_sum
            if hf >= config.HF_ALERT_THRESHOLD:
                return None
            debt_usd = borrow_sum / WEI
            if debt_usd < config.MIN_POSITION_USD:
                return None
            collateral_usd = (borrow_sum + liquidity) / WEI
            return LiquidatablePosition(
                protocol="Venus",
                chain=CHAIN,
                address=address,
                health_factor=round(hf, 6),
                collateral_usd=collateral_usd,
                debt_usd=debt_usd,
                shortfall_usd=0.0,
                liquidation_bonus=LIQUIDATION_BONUS,
            )
        except Exception as exc:
            logger.debug("Venus check_position %s: %s", address, exc)
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
            "Venus: %d liquidatable out of %d borrowers", len(results), len(self._borrowers)
        )
        return results

