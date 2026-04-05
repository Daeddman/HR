"""
Persistence helpers for cooldown state and runtime threshold overrides.

State is stored as a JSON file at the path configured by COOLDOWN_PERSIST_PATH.
The file schema is:

    {
        "cooldowns": {
            "<address>:<protocol>:<chain>": "<ISO-8601 UTC datetime>"
        },
        "thresholds": {
            "HF_ALERT_THRESHOLD": 1.05,
            "HF_CRITICAL_THRESHOLD": 1.01
        }
    }

If the path is empty or the file cannot be read/written the helpers silently
no-op so the bot keeps running without persistence.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def _path() -> str:
    from hr_monitor.config import config
    return config.COOLDOWN_PERSIST_PATH


def load_state() -> Tuple[Dict[str, datetime], Optional[float], Optional[float]]:
    """
    Load persisted state from disk.

    Returns
    -------
    cooldowns       dict mapping cooldown key → aware UTC datetime
    hf_alert        stored HF_ALERT_THRESHOLD or None if not persisted
    hf_critical     stored HF_CRITICAL_THRESHOLD or None if not persisted
    """
    path = _path()
    if not path:
        return {}, None, None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cooldowns: Dict[str, datetime] = {}
        for key, ts in data.get("cooldowns", {}).items():
            try:
                dt = datetime.fromisoformat(ts)
                # Ensure the loaded datetime is always timezone-aware (UTC).
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                cooldowns[key] = dt
            except ValueError:
                pass
        thresholds = data.get("thresholds", {})
        hf_alert = thresholds.get("HF_ALERT_THRESHOLD")
        hf_critical = thresholds.get("HF_CRITICAL_THRESHOLD")
        logger.info("Loaded %d cooldown entries from %s", len(cooldowns), path)
        return cooldowns, hf_alert, hf_critical
    except FileNotFoundError:
        return {}, None, None
    except Exception as exc:
        logger.warning("Failed to load cooldown state from %s: %s", path, exc)
        return {}, None, None


def save_state(
    cooldowns: Dict[str, datetime],
    hf_alert: float,
    hf_critical: float,
) -> None:
    """Persist cooldown state and current threshold values to disk."""
    path = _path()
    if not path:
        return
    try:
        data = {
            "cooldowns": {k: v.isoformat() for k, v in cooldowns.items()},
            "thresholds": {
                "HF_ALERT_THRESHOLD": hf_alert,
                "HF_CRITICAL_THRESHOLD": hf_critical,
            },
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception as exc:
        logger.warning("Failed to save cooldown state to %s: %s", path, exc)
