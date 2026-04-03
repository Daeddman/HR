"""
Main monitoring manager.

Runs all protocol scanners in parallel via asyncio.gather() and deduplicates
alerts using an in-memory cooldown dictionary.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List

from hr_monitor.chains.rpc import get_arb_w3, get_base_w3, get_bsc_w3, get_core_w3
from hr_monitor.config import config
from hr_monitor.protocols.aave_v3 import AaveV3Protocol
from hr_monitor.protocols.base_protocol import LiquidatablePosition
from hr_monitor.protocols.colend import ColendProtocol
from hr_monitor.protocols.compound_v3 import CompoundV3Protocol
from hr_monitor.protocols.moonwell import MoonwellProtocol
from hr_monitor.protocols.radiant import RadiantProtocol
from hr_monitor.protocols.seamless import SeamlessProtocol
from hr_monitor.protocols.venus import VenusProtocol
from hr_monitor.utils.logger import setup_logger

logger = setup_logger(__name__)


class MonitoringManager:
    def __init__(self):
        # Web3 connections
        self._arb_w3 = get_arb_w3()
        self._bsc_w3 = get_bsc_w3()
        self._base_w3 = get_base_w3()
        self._core_w3 = get_core_w3()

        # Protocol instances
        self._protocols = [
            AaveV3Protocol(self._arb_w3, "Arbitrum"),
            AaveV3Protocol(self._base_w3, "Base"),
            RadiantProtocol(self._arb_w3),
            CompoundV3Protocol(self._arb_w3, "Arbitrum"),
            CompoundV3Protocol(self._base_w3, "Base"),
            VenusProtocol(self._bsc_w3),
            SeamlessProtocol(self._base_w3),
            MoonwellProtocol(self._base_w3),
            ColendProtocol(self._core_w3),
        ]

        # Deduplication: "address:protocol:chain" -> last alert datetime (UTC)
        self._last_alerted: Dict[str, datetime] = {}

        # Stats
        self.total_found: int = 0
        self.last_scan_time: datetime | None = None
        self.active_protocols: int = len(self._protocols)
        self._scan_cycle: int = 0

    def _cooldown_key(self, pos: LiquidatablePosition) -> str:
        return f"{pos.address}:{pos.protocol}:{pos.chain}"

    def _is_cooldown_active(self, pos: LiquidatablePosition) -> bool:
        last = self._last_alerted.get(self._cooldown_key(pos))
        if last is None:
            return False
        delta = (datetime.now(timezone.utc) - last).total_seconds()
        return delta < config.ALERT_COOLDOWN_MINUTES * 60

    def _record_alert(self, pos: LiquidatablePosition) -> None:
        self._last_alerted[self._cooldown_key(pos)] = datetime.now(timezone.utc)

    async def _scan_protocol(self, protocol) -> List[LiquidatablePosition]:
        try:
            positions = await protocol.get_liquidatable_positions()
            return positions
        except Exception as exc:
            logger.error("Protocol scan error (%s): %s", type(protocol).__name__, exc)
            return []

    async def scan_all(self) -> List[LiquidatablePosition]:
        """Scan all protocols in parallel and return new (non-cooldown) liquidatable positions."""
        self._scan_cycle += 1
        # Periodically reset the cached borrower list so new borrowers are picked up.
        if self._scan_cycle % config.BORROWER_REFRESH_CYCLES == 0:
            logger.info(
                "Cycle %d: refreshing borrower lists for all protocols", self._scan_cycle
            )
            for p in self._protocols:
                p.reset_borrowers()

        logger.info("Starting scan of %d protocols...", len(self._protocols))
        tasks = [self._scan_protocol(p) for p in self._protocols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        new_positions: List[LiquidatablePosition] = []
        for batch in results:
            if isinstance(batch, Exception):
                continue
            for pos in batch:
                if not self._is_cooldown_active(pos):
                    new_positions.append(pos)
                    self._record_alert(pos)

        self.total_found += len(new_positions)
        self.last_scan_time = datetime.now(timezone.utc)

        logger.info("Scan complete: %d new liquidatable positions found", len(new_positions))
        return new_positions
