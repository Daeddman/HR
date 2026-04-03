"""
Unit tests for HR Monitor.

Tests are intentionally free of network calls — all on-chain interaction
is either mocked or tested through pure-Python logic.
"""

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from hr_monitor.bot import _format_alert, _is_authorized, _short_address
from hr_monitor.config import Config
from hr_monitor.monitor import MonitoringManager
from hr_monitor.protocols.base_protocol import LiquidatablePosition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pos(**kwargs) -> LiquidatablePosition:
    defaults = dict(
        protocol="Aave v3",
        chain="Arbitrum",
        address="0xDeadBeefDeadBeefDeadBeefDeadBeefDeadBeef",
        health_factor=0.98,
        collateral_usd=10000.0,
        debt_usd=11000.0,
        shortfall_usd=1000.0,
        liquidation_bonus=5.0,
    )
    defaults.update(kwargs)
    return LiquidatablePosition(**defaults)


# ---------------------------------------------------------------------------
# _short_address
# ---------------------------------------------------------------------------

def test_short_address():
    addr = "0xDeadBeefDeadBeefDeadBeefDeadBeefDeadBeef"
    result = _short_address(addr)
    assert result.startswith("0xDead")
    assert result.endswith("Beef")
    assert "..." in result


# ---------------------------------------------------------------------------
# _format_alert — basic structure
# ---------------------------------------------------------------------------

def test_format_alert_contains_protocol_and_chain():
    pos = _make_pos()
    text = _format_alert(pos)
    assert "Aave v3" in text
    assert "Arbitrum" in text


def test_format_alert_hf_shown():
    pos = _make_pos(health_factor=0.95)
    text = _format_alert(pos)
    assert "0.950" in text


def test_format_alert_critical_header():
    """HF below HF_CRITICAL_THRESHOLD triggers the critical header."""
    cfg = Config()
    with patch("hr_monitor.bot.config", cfg):
        cfg.HF_CRITICAL_THRESHOLD = 1.01
        pos = _make_pos(health_factor=0.99)
        text = _format_alert(pos)
    assert "КРИТИЧНО" in text


def test_format_alert_normal_header():
    cfg = Config()
    with patch("hr_monitor.bot.config", cfg):
        cfg.HF_CRITICAL_THRESHOLD = 1.01
        cfg.HF_ALERT_THRESHOLD = 1.05
        pos = _make_pos(health_factor=1.03)
        text = _format_alert(pos)
    assert "ЛИКВИДАЦИЯ ДОСТУПНА" in text
    assert "КРИТИЧНО" not in text


def test_format_alert_compound_style_hf():
    """health_factor >= 999 should show N/A."""
    pos = _make_pos(health_factor=999.0, shortfall_usd=500.0)
    text = _format_alert(pos)
    assert "N/A" in text


def test_format_alert_zero_shortfall_near_liq():
    """shortfall_usd == 0 should show near-liquidation text, not '$0.00'."""
    pos = _make_pos(health_factor=1.03, shortfall_usd=0.0)
    text = _format_alert(pos)
    assert "$0.00" not in text
    assert "shortfall = 0" in text


def test_format_alert_address_is_hyperlink():
    pos = _make_pos()
    text = _format_alert(pos)
    assert "<a href=" in text
    assert pos.address in text


def test_format_alert_no_collateral():
    pos = _make_pos(collateral_usd=0.0)
    text = _format_alert(pos)
    assert "Залог: N/A" in text


# ---------------------------------------------------------------------------
# _is_authorized
# ---------------------------------------------------------------------------

def test_is_authorized_match():
    cfg = Config()
    cfg.TELEGRAM_CHAT_ID = "12345"
    msg = MagicMock()
    msg.chat.id = 12345
    with patch("hr_monitor.bot.config", cfg):
        assert _is_authorized(msg) is True


def test_is_authorized_mismatch():
    cfg = Config()
    cfg.TELEGRAM_CHAT_ID = "12345"
    msg = MagicMock()
    msg.chat.id = 99999
    with patch("hr_monitor.bot.config", cfg):
        assert _is_authorized(msg) is False


# ---------------------------------------------------------------------------
# MonitoringManager — cooldown key
# ---------------------------------------------------------------------------

def _make_manager_no_init() -> MonitoringManager:
    """Create a MonitoringManager while skipping network-dependent __init__."""
    mgr = object.__new__(MonitoringManager)
    mgr._last_alerted = {}
    mgr._paused = False
    mgr.total_found = 0
    mgr.total_scans = 0
    mgr.last_scan_time = None
    mgr.active_protocols = 0
    mgr._scan_cycle = 0
    mgr._error_counts = {}
    mgr._last_errors = {}
    mgr._position_usd_sum = 0.0
    mgr._position_usd_count = 0
    mgr._protocols = []
    return mgr


def test_cooldown_key_format():
    mgr = _make_manager_no_init()
    pos = _make_pos(address="0xABC", protocol="Aave v3", chain="Arbitrum")
    key = mgr._cooldown_key(pos)
    assert key == "0xABC:Aave v3:Arbitrum"


def test_cooldown_key_different_protocols_different_keys():
    """Same address in two different protocols must produce different keys."""
    mgr = _make_manager_no_init()
    pos1 = _make_pos(address="0xABC", protocol="Aave v3", chain="Arbitrum")
    pos2 = _make_pos(address="0xABC", protocol="Radiant Capital", chain="Arbitrum")
    assert mgr._cooldown_key(pos1) != mgr._cooldown_key(pos2)


def test_cooldown_active_within_window():
    mgr = _make_manager_no_init()
    pos = _make_pos()
    mgr._record_alert(pos)
    with patch("hr_monitor.monitor.config") as cfg:
        cfg.ALERT_COOLDOWN_MINUTES = 10
        assert mgr._is_cooldown_active(pos) is True


def test_cooldown_inactive_after_expiry():
    mgr = _make_manager_no_init()
    pos = _make_pos()
    # Manually set a past timestamp (11 minutes ago)
    key = mgr._cooldown_key(pos)
    mgr._last_alerted[key] = datetime.now(timezone.utc) - timedelta(minutes=11)
    with patch("hr_monitor.monitor.config") as cfg:
        cfg.ALERT_COOLDOWN_MINUTES = 10
        assert mgr._is_cooldown_active(pos) is False


# ---------------------------------------------------------------------------
# MonitoringManager — pause / resume
# ---------------------------------------------------------------------------

def test_pause_resume():
    mgr = _make_manager_no_init()
    assert mgr.is_paused is False
    mgr.pause()
    assert mgr.is_paused is True
    mgr.resume()
    assert mgr.is_paused is False


def test_scan_all_skips_when_paused():
    mgr = _make_manager_no_init()
    mgr.pause()
    result = asyncio.get_event_loop().run_until_complete(mgr.scan_all())
    assert result == []


# ---------------------------------------------------------------------------
# MonitoringManager — avg_position_usd
# ---------------------------------------------------------------------------

def test_avg_position_no_positions():
    mgr = _make_manager_no_init()
    assert mgr.avg_position_usd == 0.0


def test_avg_position_with_data():
    mgr = _make_manager_no_init()
    mgr._position_usd_sum = 3000.0
    mgr._position_usd_count = 3
    assert mgr.avg_position_usd == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# BaseProtocol — reset_borrowers
# ---------------------------------------------------------------------------

def test_base_reset_borrowers_clears_list():
    from hr_monitor.protocols.base_protocol import BaseProtocol

    class _Dummy(BaseProtocol):
        async def get_liquidatable_positions(self):
            return []

        async def get_borrowers(self):
            return []

    dummy = _Dummy()
    dummy._borrowers = ["0xAAA", "0xBBB"]
    dummy.reset_borrowers()
    assert dummy._borrowers == []


def test_venus_reset_clears_markets_and_oracle():
    from hr_monitor.protocols.venus import VenusProtocol

    v = object.__new__(VenusProtocol)
    v._borrowers = ["0xAAA"]
    v._markets = ["0xMKT"]
    v._oracle_contract = object()
    v.reset_borrowers()
    assert v._borrowers == []
    assert v._markets == []
    assert v._oracle_contract is None


def test_moonwell_reset_clears_markets_and_oracle():
    from hr_monitor.protocols.moonwell import MoonwellProtocol

    m = object.__new__(MoonwellProtocol)
    m._borrowers = ["0xAAA"]
    m._markets = ["0xMKT"]
    m._oracle_contract = object()
    m.reset_borrowers()
    assert m._borrowers == []
    assert m._markets == []
    assert m._oracle_contract is None


# ---------------------------------------------------------------------------
# persist — save and load round-trip
# ---------------------------------------------------------------------------

def test_persist_round_trip():
    from hr_monitor.utils.persist import load_state, save_state

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        path = tmp.name

    try:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        cooldowns = {"0xABC:Aave v3:Arbitrum": now}
        with patch("hr_monitor.utils.persist._path", return_value=path):
            save_state(cooldowns, 1.08, 1.02)
            loaded_cd, loaded_alert, loaded_crit = load_state()

        assert "0xABC:Aave v3:Arbitrum" in loaded_cd
        assert loaded_cd["0xABC:Aave v3:Arbitrum"].isoformat() == now.isoformat()
        assert loaded_alert == pytest.approx(1.08)
        assert loaded_crit == pytest.approx(1.02)
    finally:
        os.unlink(path)


def test_persist_load_missing_file():
    from hr_monitor.utils.persist import load_state

    with patch("hr_monitor.utils.persist._path", return_value="/nonexistent/path/state.json"):
        cd, alert, crit = load_state()
    assert cd == {}
    assert alert is None
    assert crit is None


def test_persist_disabled_when_path_empty():
    from hr_monitor.utils.persist import load_state, save_state

    with patch("hr_monitor.utils.persist._path", return_value=""):
        # Should not raise and should return empty defaults
        cd, alert, crit = load_state()
        assert cd == {}
        # save_state should no-op without error
        save_state({}, 1.05, 1.01)
