"""
Aave v3 protocol handler.

Supports both Arbitrum and Base deployments.
Delegates all logic to AaveV3ForkProtocol.
"""

from web3 import AsyncWeb3

from hr_monitor.protocols.aave_v3_fork import AaveV3ForkProtocol

# Aave v3 deployments
DEPLOYMENTS = {
    "Arbitrum": {
        "pool": "0x794a61358D6845594F94dc1DB02A252b5b4814aD",
        "liquidation_bonus": 5.0,
    },
    "Base": {
        "pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
        "liquidation_bonus": 5.0,
    },
}


class AaveV3Protocol(AaveV3ForkProtocol):
    """Aave v3 monitor for a single chain deployment."""

    def __init__(self, w3: AsyncWeb3, chain_name: str) -> None:
        deployment = DEPLOYMENTS[chain_name]
        super().__init__(
            w3=w3,
            pool_address=deployment["pool"],
            chain_name=chain_name,
            protocol_name="Aave v3",
            liquidation_bonus=deployment["liquidation_bonus"],
        )
