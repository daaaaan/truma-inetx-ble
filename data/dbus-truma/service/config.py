"""Persistent configuration stored as JSON."""
import json
import logging
from pathlib import Path

log = logging.getLogger("truma.config")

CONFIG_FILE = "/data/dbus-truma/config.json"

DEFAULTS = {
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_enabled": False,
    "ble_adapter": "/org/bluez/hci1",
}


def load() -> dict:
    """Load config from disk, merging with defaults."""
    config = dict(DEFAULTS)
    path = Path(CONFIG_FILE)
    if path.exists():
        try:
            with open(path) as f:
                saved = json.load(f)
            config.update(saved)
        except Exception as e:
            log.warning("Failed to load config: %s", e)
    return config


def save(config: dict):
    """Save config to disk."""
    path = Path(CONFIG_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(config, f, indent=2)
        log.info("Config saved")
    except Exception as e:
        log.warning("Failed to save config: %s", e)


def update(updates: dict) -> dict:
    """Load, merge updates, save, return new config."""
    config = load()
    config.update(updates)
    save(config)
    return config
