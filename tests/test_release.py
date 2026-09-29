"""Release metadata, persistence, and reproducible-build tests."""


from __future__ import annotations


import json
import stat
import sys
import zipfile
from pathlib import Path


import server
import yaml
from tools import extract_ocr_assets


ROOT = Path(__file__).parent.parent




def test_addon_metadata_uses_supported_private_defaults() -> None:
    config = yaml.safe_load(
        (ROOT / "mvdis_penalty_backend" / "config.yaml").read_text(encoding="utf-8")
    )
    assert config["version"] == "0.6.3"
    assert config["options"]["max_retries"] == 1
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["startup"] == "application"
    assert config["stage"] == "stable"
    assert config["watchdog"].endswith("/health")
    assert config["ingress"] is True
    assert config["panel_admin"] is True
    assert config["homeassistant_api"] is True
    assert "ports" not in config
    assert "host_network" not in config




def test_state_is_private_and_corruption_is_preserved(monkeypatch, tmp_path) -> None:
    state_path = tmp_path / "state.json"
