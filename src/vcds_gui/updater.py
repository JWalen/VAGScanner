"""In-app update check against GitHub Releases.

Qt-free and dependency-free (stdlib ``urllib`` only) so it can be unit-tested
without a network or a display. The GUI wires this onto a background thread.

Flow: query the repo's *latest* release, compare its tag to the running
version, and if newer, hand back the installer asset's URL (and SHA-256 when
GitHub provides an asset ``digest``). The GUI then downloads, verifies and runs
the existing Inno Setup installer, which upgrades in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

REPO = "JWalen/OBD-Toolkit"
_API = "https://api.github.com/repos/{repo}/releases/latest"

# Injectable opener: callable(request, timeout) -> context-manager response.
Opener = Callable[..., object]

_SSL_CTX: Optional[ssl.SSLContext] = None


def _ssl_context() -> Optional[ssl.SSLContext]:
    """Build an SSL context with a working CA bundle.

    A PyInstaller-frozen app has no system CA store, so HTTPS verification fails
    with CERTIFICATE_VERIFY_FAILED. Prefer certifi's bundled CA file (PyInstaller
    ships it via its hook); fall back to the platform default, then — only as a
    last resort — to the OS trust store via ``truststore`` if present.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    try:
        return ssl.create_default_context()
    except Exception:  # noqa: BLE001
        return None


def _urlopen(req, timeout: float = 15):
    global _SSL_CTX
    if _SSL_CTX is None:
        _SSL_CTX = _ssl_context()
    return urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)


@dataclass
class UpdateInfo:
    version: str
    tag: str
    name: str
    notes: str
    html_url: str
    installer_url: Optional[str]
    installer_name: Optional[str]
    installer_size: int
    sha256: Optional[str]


def version_tuple(v: str) -> tuple:
    """Parse ``"v1.2.3"`` / ``"1.2.3"`` into ``(1, 2, 3)`` (lenient)."""
    v = (v or "").strip().lstrip("vV")
    parts = []
    for chunk in v.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break  # stop at a pre-release suffix like "0rc1"
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def is_newer(latest: str, current: str) -> bool:
    """True if semantic version ``latest`` is strictly newer than ``current``."""
    a, b = version_tuple(latest), version_tuple(current)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return a > b


def _request(url: str) -> "urllib.request.Request":
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "vcds-toolkit-updater",
        },
    )


def fetch_latest(repo: str = REPO, opener: Opener = _urlopen, timeout: float = 15) -> Optional[UpdateInfo]:
    """Fetch the latest release metadata, or None on any failure."""
    with opener(_request(_API.format(repo=repo)), timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tag = data.get("tag_name") or ""
    ext = ".dmg" if sys.platform == "darwin" else ".exe"
    installer = None
    for asset in data.get("assets") or []:
        if str(asset.get("name", "")).lower().endswith(ext):
            installer = asset
            break

    sha256 = None
    digest = (installer or {}).get("digest") or ""
    if isinstance(digest, str) and digest.startswith("sha256:"):
        sha256 = digest.split(":", 1)[1]

    return UpdateInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        name=data.get("name") or tag,
        notes=data.get("body") or "",
        html_url=data.get("html_url") or "",
        installer_url=(installer or {}).get("browser_download_url"),
        installer_name=(installer or {}).get("name"),
        installer_size=int((installer or {}).get("size") or 0),
        sha256=sha256,
    )


def check_for_update(
    current_version: str, repo: str = REPO, opener: Opener = _urlopen
) -> Optional[UpdateInfo]:
    """Return UpdateInfo if a newer release exists, else None."""
    info = fetch_latest(repo, opener=opener)
    if info and info.version and is_newer(info.version, current_version):
        return info
    return None


def download_installer(
    info: UpdateInfo,
    dest_dir: str,
    progress: Optional[Callable[[int, int], None]] = None,
    opener: Opener = _urlopen,
    chunk: int = 65536,
    timeout: float = 60,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> str:
    """Download the installer asset, verifying its SHA-256 when known.

    Args:
        info: The update to download.
        dest_dir: Directory to write the installer into (created if needed).
        progress: Optional callback(downloaded_bytes, total_bytes).
        is_cancelled: Optional predicate; if it returns True mid-download the
            partial file is removed and ``InterruptedError`` is raised.

    Returns:
        Path to the downloaded installer.
    """
    if not info.installer_url:
        raise ValueError("This release has no installer (.exe) asset.")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, info.installer_name or "VCDS-Toolkit-Setup.exe")

    digest = hashlib.sha256()
    total = info.installer_size or 0
    done = 0
    with opener(_request(info.installer_url), timeout=timeout) as resp, open(dest, "wb") as fh:
        if not total:
            try:
                total = int(resp.headers.get("Content-Length") or 0)
            except Exception:  # noqa: BLE001
                total = 0
        while True:
            if is_cancelled is not None and is_cancelled():
                fh.close()
                _safe_remove(dest)
                raise InterruptedError("Download cancelled.")
            block = resp.read(chunk)
            if not block:
                break
            fh.write(block)
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, total)

    if info.sha256 and digest.hexdigest().lower() != info.sha256.lower():
        _safe_remove(dest)
        raise ValueError("Downloaded installer failed SHA-256 verification.")
    return dest


def launch_installer(path: str, silent: bool = False, relaunch: Optional[str] = None) -> None:
    """Launch the downloaded installer; the caller should then quit the app.

    Args:
        path: The downloaded Setup.exe.
        silent: Run the Inno Setup installer unattended (no wizard).
        relaunch: When silent, an executable to start after the update completes
            (typically the app's own exe, so it reopens automatically).

    With ``silent``, a small detached helper batch waits for the app to exit,
    runs the installer with ``/VERYSILENT``, then relaunches — giving a hands-off
    update.
    """
    import subprocess

    if sys.platform == "darwin":
        # Open the .dmg in Finder; the user drags the app to Applications.
        subprocess.Popen(["open", path])
        return
    if not sys.platform.startswith("win"):
        subprocess.Popen([path])
        return
    if not silent:
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - intended
        return

    import tempfile

    exe_name = os.path.basename(relaunch) if relaunch else ""
    tmp = tempfile.gettempdir()
    log = os.path.join(tmp, "obd_toolkit_update.log")
    inno_log = os.path.join(tmp, "obd_toolkit_update_inno.log")
    lines = [
        "@echo off",
        f'echo Update run >"{log}"',
        "ping 127.0.0.1 -n 3 >nul",  # give the app a moment to start exiting
    ]
    if exe_name:
        # make sure the running app is fully gone so its files aren't locked
        lines.append(f'taskkill /im "{exe_name}" /f >>"{log}" 2>&1')
        lines.append("ping 127.0.0.1 -n 2 >nul")
    lines.append(
        f'"{path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /LOG="{inno_log}"')
    lines.append(f'echo Installer exit code: %ERRORLEVEL% >>"{log}"')
    if relaunch:
        lines.append(f'start "" "{relaunch}"')
    bat = os.path.join(tmp, "obd_toolkit_update.bat")
    with open(bat, "w", encoding="ascii") as fh:
        fh.write("\r\n".join(lines) + "\r\n")
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    flags = 0x00000008 | 0x00000200 | 0x08000000
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True)


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
