"""
Main monitoring manager.

Runs all protocol scanners in parallel via asyncio.gather() and deduplicates
alerts using an in-memory cooldown dictionary (optionally persisted to disk).
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
from hr_monitor.utils.persist import load_state, save_state

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
        # Loaded from disk on startup so cooldowns survive restarts.
        cooldowns, hf_alert, hf_critical = load_state()
        self._last_alerted: Dict[str, datetime] = cooldowns

        # Apply persisted threshold overrides (if any).
        # NOTE: this intentionally mutates the shared config singleton so that all
        # modules that read config.HF_ALERT_THRESHOLD at call-time pick up the
        # overridden values immediately, preserving the same runtime-override
        # semantics as the /setthreshold bot command.
        if hf_alert is not None:
            config.HF_ALERT_THRESHOLD = hf_alert
        if hf_critical is not None:
            config.HF_CRITICAL_THRESHOLD = hf_critical

        # Stats
        self.total_found: int = 0
        self.total_scans: int = 0
        self.last_scan_time: datetime | None = None
        self.active_protocols: int = len(self._protocols)
        self._scan_cycle: int = 0

        # Per-protocol error tracking {protocol_class_name: error_count}
        self._error_counts: Dict[str, int] = {}
        # Last error message per protocol
        self._last_errors: Dict[str, str] = {}

        # Position size accumulator for average
        self._position_usd_sum: float = 0.0
        self._position_usd_count: int = 0

        # Pause flag — when True, scan_all() skips actual scanning
        self._paused: bool = False

    # ------------------------------------------------------------------
    # Pause / resume
    # ------------------------------------------------------------------

    def pause(self) -> None:
        self._paused = True
        logger.info("Monitoring paused.")

    def resume(self) -> None:
        self._paused = False
        logger.info("Monitoring resumed.")

    @property
    def is_paused(self) -> bool:
        return self._paused

    # ------------------------------------------------------------------
    # Cooldown helpers
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Protocol stats helpers
    # ------------------------------------------------------------------

    def get_protocol_info(self) -> List[dict]:
        """Return a list of dicts with per-protocol runtime info."""
        info = []
        for p in self._protocols:
            name = type(p).__name__
            borrowers = len(getattr(p, "_borrowers", []))
            info.append(
                {
                    "name": name,
                    "borrowers": borrowers,
                    "errors": self._error_counts.get(name, 0),
                    "last_error": self._last_errors.get(name, ""),
                }
            )
        return info

    @property
    def avg_position_usd(self) -> float:
        if self._position_usd_count == 0:
            return 0.0
        return self._position_usd_sum / self._position_usd_count

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    async def _scan_protocol(self, protocol) -> List[LiquidatablePosition]:
        try:
            positions = await protocol.get_liquidatable_positions()
            return positions
        except Exception as exc:
            name = type(protocol).__name__
            self._error_counts[name] = self._error_counts.get(name, 0) + 1
            self._last_errors[name] = str(exc)
            logger.error("Protocol scan error (%s): %s", name, exc)
            return []

    async def scan_all(self) -> List[LiquidatablePosition]:
        """Scan all protocols in parallel and return new (non-cooldown) liquidatable positions."""
        if self._paused:
            logger.debug("Monitoring is paused — skipping scan.")
            return []

        self._scan_cycle += 1
        self.total_scans += 1

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
                    self._position_usd_sum += pos.debt_usd
                    self._position_usd_count += 1

        self.total_found += len(new_positions)
        self.last_scan_time = datetime.now(timezone.utc)

        # Persist cooldowns + current thresholds after every scan
        save_state(self._last_alerted, config.HF_ALERT_THRESHOLD, config.HF_CRITICAL_THRESHOLD)

        logger.info("Scan complete: %d new liquidatable positions found", len(new_positions))
        return new_positions

    def save_state_now(self) -> None:
        """Explicitly persist current state (called on graceful shutdown)."""
        save_state(self._last_alerted, config.HF_ALERT_THRESHOLD, config.HF_CRITICAL_THRESHOLD)
