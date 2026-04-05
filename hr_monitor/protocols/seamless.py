"""
Seamless Protocol handler (Base).

Seamless is a fork of Aave v3 deployed on Base.
Pool: 0x8F44Fd754285aa6A2b8B9B97739B79746e0475a7
Delegates all logic to AaveV3ForkProtocol.
"""

from web3 import AsyncWeb3

from hr_monitor.protocols.aave_v3_fork import AaveV3ForkProtocol

POOL_ADDRESS = "0x8F44Fd754285aa6A2b8B9B97739B79746e0475a7"
LIQUIDATION_BONUS = 5.0


class SeamlessProtocol(AaveV3ForkProtocol):
    def __init__(self, w3: AsyncWeb3) -> None:
        super().__init__(
            w3=w3,
            pool_address=POOL_ADDRESS,
            chain_name="Base",
            protocol_name="Seamless Protocol",
            liquidation_bonus=LIQUIDATION_BONUS,
        )
