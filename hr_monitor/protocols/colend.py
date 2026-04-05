"""
CoLend protocol handler (Core DAO).

CoLend is a fork of Aave v3 deployed on Core DAO (chain ID 1116).
Pool: 0x0CEa9F0F49F30d376390e480ba32f903B43B19C5
Delegates all logic to AaveV3ForkProtocol.
"""

from hr_monitor.config import config
from hr_monitor.protocols.aave_v3_fork import AaveV3ForkProtocol
from web3 import AsyncWeb3

POOL_ADDRESS = "0x0CEa9F0F49F30d376390e480ba32f903B43B19C5"
LIQUIDATION_BONUS = 5.0


class ColendProtocol(AaveV3ForkProtocol):
    def __init__(self, w3: AsyncWeb3) -> None:
        super().__init__(
            w3=w3,
            pool_address=POOL_ADDRESS,
            chain_name="Core",
            protocol_name="CoLend",
            liquidation_bonus=LIQUIDATION_BONUS,
            min_position_usd=config.COLEND_MIN_POSITION_USD,
            max_blocks_scan=config.COLEND_MAX_BLOCKS_SCAN,
        )
