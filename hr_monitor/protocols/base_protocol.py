from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class LiquidatablePosition:
    protocol: str
    chain: str
    address: str
    health_factor: float          # 999.0 for Compound-style (use shortfall instead)
    collateral_usd: float
    debt_usd: float
    shortfall_usd: float          # for Compound-style protocols
    liquidation_bonus: float      # liquidator bonus percentage (e.g. 5.0 = 5%)


class BaseProtocol(ABC):
    @abstractmethod
    async def get_liquidatable_positions(self) -> List[LiquidatablePosition]:
        """Return all positions that can currently be liquidated."""

    @abstractmethod
    async def get_borrowers(self) -> List[str]:
        """Return list of known borrower addresses for this protocol."""
