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
    assert config["version"] == "0.5.0"
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
    state_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "STATE_PATH", state_path)

    addon = server.Addon()

    assert (tmp_path / "state.corrupt.json").read_text(encoding="utf-8") == "{broken"
    assert json.loads(state_path.read_text(encoding="utf-8"))["version"] == 4
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "state.corrupt.json").stat().st_mode) == 0o600
    assert addon._state["people"] == {}


def test_ocr_assets_have_reproducible_timestamps(monkeypatch, tmp_path) -> None:
    wheel = tmp_path / "ddddocr.whl"
    destination = tmp_path / "assets"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("ddddocr/common_old.onnx", b"model")
        archive.writestr(
            "ddddocr/models/charset_manager.py",
            "def _get_old_charset():\n    return ['', 'A', '7']\n",
        )

    monkeypatch.setattr(sys, "argv", ["extract", str(wheel), str(destination)])
    extract_ocr_assets.main()

    assert (destination / "common_old.onnx").read_bytes() == b"model"
    assert json.loads((destination / "charset.json").read_text()) == ["", "A", "7"]
    assert (destination / "common_old.onnx").stat().st_mtime == 0
    assert (destination / "charset.json").stat().st_mtime == 0
    assert destination.stat().st_mtime == 0
