"""Unit tests for the GitHub-Releases updater (no network)."""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("PySide6")  # updater ships in the gui package
from vcds_gui import updater  # noqa: E402


class FakeResp:
    """Minimal urlopen-response stand-in supporting `with` + chunked read()."""

    def __init__(self, data: bytes, headers=None):
        self._data = data
        self.headers = headers or {}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out, self._data = self._data, b""
            return out
        out, self._data = self._data[:n], self._data[n:]
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _release_json(tag="v0.2.0", installer_bytes=b"FAKE-INSTALLER", with_digest=True):
    sha = hashlib.sha256(installer_bytes).hexdigest()
    asset = {
        "name": f"VCDS-Toolkit-Setup-{tag.lstrip('v')}.exe",
        "browser_download_url": "https://example.test/setup.exe",
        "size": len(installer_bytes),
    }
    if with_digest:
        asset["digest"] = f"sha256:{sha}"
    return {
        "tag_name": tag,
        "name": tag,
        "body": "Shiny new release.",
        "html_url": f"https://github.com/JWalen/OBD-Toolkit/releases/tag/{tag}",
        "assets": [
            {"name": "vcds_toolkit-0.2.0-py3-none-any.whl", "browser_download_url": "x", "size": 1},
            asset,
        ],
    }


def _opener_for(payload: dict):
    def opener(req, timeout=15):
        return FakeResp(json.dumps(payload).encode("utf-8"))

    return opener


def test_fetch_latest_picks_platform_installer():
    import sys

    payload = {
        "tag_name": "v2.0.0", "name": "v2.0.0", "body": "", "html_url": "x",
        "assets": [
            {"name": "OBD-Toolkit-2.0.0.dmg", "browser_download_url": "x", "size": 1},
            {"name": "VCDS-Toolkit-Setup-2.0.0.exe", "browser_download_url": "y", "size": 2},
        ],
    }
    info = updater.fetch_latest(opener=_opener_for(payload))
    expected = ".dmg" if sys.platform == "darwin" else ".exe"
    assert info.installer_name.endswith(expected)


# --------------------------------------------------------------------------- #
# Version comparison
# --------------------------------------------------------------------------- #


def test_launch_installer_silent_writes_helper(monkeypatch, tmp_path):
    import os
    import sys

    if not sys.platform.startswith("win"):
        pytest.skip("silent helper is Windows-only")

    import subprocess

    calls = {}

    def fake_popen(cmd, **kw):
        calls["cmd"] = cmd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    setup = str(tmp_path / "Setup.exe")
    app = str(tmp_path / "App.exe")
    updater.launch_installer(setup, silent=True, relaunch=app)

    # it should have launched a cmd helper batch
    assert calls["cmd"][0] == "cmd" and calls["cmd"][1] == "/c"
    bat = calls["cmd"][2]
    content = open(bat, encoding="ascii").read()
    assert "/VERYSILENT" in content
    assert 'taskkill /im "App.exe"' in content  # closes the running app first
    assert "App.exe" in content  # relaunch line
    os.remove(bat)


def test_ssl_context_builds_without_error():
    import ssl

    ctx = updater._ssl_context()
    assert ctx is None or isinstance(ctx, ssl.SSLContext)


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        ("0.1.10", "0.1.9", True),
        ("v1.0", "0.9", True),
        ("1.0.0", "1.0", False),
        ("1.2.0rc1", "1.1.0", True),
    ],
)
def test_is_newer(latest, current, expected):
    assert updater.is_newer(latest, current) is expected


# --------------------------------------------------------------------------- #
# Fetch / check
# --------------------------------------------------------------------------- #


def test_fetch_latest_parses_installer_asset():
    info = updater.fetch_latest(opener=_opener_for(_release_json()))
    assert info.version == "0.2.0"
    assert info.tag == "v0.2.0"
    assert info.installer_name.endswith(".exe")
    assert info.installer_url == "https://example.test/setup.exe"
    assert info.sha256 and len(info.sha256) == 64
    assert "Shiny" in info.notes


def test_check_for_update_when_newer():
    info = updater.check_for_update("0.1.0", opener=_opener_for(_release_json("v0.2.0")))
    assert info is not None and info.version == "0.2.0"


def test_check_for_update_when_current_is_latest():
    assert updater.check_for_update("0.2.0", opener=_opener_for(_release_json("v0.2.0"))) is None


# --------------------------------------------------------------------------- #
# Download + verify
# --------------------------------------------------------------------------- #


def test_download_installer_verifies_sha256(tmp_path):
    payload = b"FAKE-INSTALLER-BYTES"
    info = updater.fetch_latest(opener=_opener_for(_release_json(installer_bytes=payload)))

    seen = []

    def opener(req, timeout=60):
        return FakeResp(payload)

    path = updater.download_installer(
        info, str(tmp_path), progress=lambda d, t: seen.append((d, t)), opener=opener
    )
    assert path.endswith(".exe")
    with open(path, "rb") as fh:
        assert fh.read() == payload
    assert seen and seen[-1][0] == len(payload)  # progress reached 100%


def test_download_installer_rejects_tampered_file(tmp_path):
    info = updater.fetch_latest(opener=_opener_for(_release_json(installer_bytes=b"ORIGINAL")))

    def tampered_opener(req, timeout=60):
        return FakeResp(b"TAMPERED")  # different bytes -> sha mismatch

    import os

    with pytest.raises(ValueError, match="SHA-256"):
        updater.download_installer(info, str(tmp_path), opener=tampered_opener)
    # the bad file must not be left behind
    assert not any(f.endswith(".exe") for f in os.listdir(tmp_path))
