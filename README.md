# vcds-toolkit

Analyze and capture data from a VAG/Audi car on Windows. Three data sources feed
one shared, dependency-free parsing/analysis core, surfaced through two
front-ends — an **MCP server** (for Claude Desktop / Claude Code) and a
**desktop GUI**.

```
 VCDS .CSV measuring logs ┐
 VCDS .TXT Auto-Scans     ├─►  vcds_core  ─►  MCP server (vcds-mcp)
 Live ELM327 OBD-II       ┘   (stdlib only)    Desktop GUI (vcds-gui)
```

---

## What it does — and what it deliberately does NOT do

**It does:**

- Parse VCDS **measuring-value logs** (`.CSV`) across VCDS's several layouts and
  Windows locales (comma/semicolon/tab delimiters, `1.5` *and* `1,5` decimals),
  inferring the structure and echoing back what it detected.
- Parse VCDS **Auto-Scan** fault reports (`.TXT`) into modules and faults, using
  indentation to attach each fault's status detail correctly.
- Detect **events** — specified-vs-actual divergence, rising misfire/fault
  counters, per-channel extremes, and custom threshold rules.
- Capture **live OBD-II** data from a generic ELM327 and write it in the *exact
  same flat CSV layout* the core parses, so a live session round-trips straight
  back through every analysis tool.

**It does NOT:**

- ⛔ **Control VCDS or the HEX-V2 / HEX-NET cable.** Ross-Tech exposes no API for
  that. This toolkit only **reads the files VCDS writes**. Point it at your VCDS
  `Logs` folder; it never drives the VCDS application or the cable.
- ⛔ Talk to an **OBDeleven** dongle. OBDeleven is locked to its own app and
  cannot be used as a generic serial adapter.

### Generic OBD-II vs VCDS — know the limit

A generic ELM327 exposes only the **standard OBD-II PIDs** (RPM, speed, coolant,
MAP, fuel trims, etc.). It is **blind to the VAG-specific measuring blocks** that
VCDS reads (e.g. individual cylinder misfire counters, adaptation channels,
charge-pressure actuator duty). Use VCDS logs for VAG-specific channels; use the
live ELM327 path for standard PIDs and a usable derived boost figure.

The live logger adds a derived channel **`Boost (derived)` = MAP − Barometric
pressure** (the 3.0T is supercharged, so manifold pressure minus ambient is the
usable boost figure). Derived channels are clearly labelled.

### USB vs Bluetooth ELM327

**Prefer a USB ELM327.** Bluetooth clones that pair as an outgoing COM port do
work, but they routinely drop samples at higher rates. If you must use Bluetooth,
lower the sample rate. Clones also vary in baud — try `38400`, then `9600`, then
`115200` if auto-detect fails.

---

## Install

Requires **Python 3.10+** on Windows.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1

# Core only (parsing/analysis, no third-party deps):
pip install -e .

# Add the pieces you need:
pip install -e ".[mcp]"   # MCP server
pip install -e ".[obd]"   # live ELM327 logging
pip install -e ".[gui]"   # desktop GUI
pip install -e ".[dev]"   # everything + test tooling
```

Try it without a car — ready-made example files are committed in
[`examples/`](examples/) (a classic group log, an Advanced/UDS log and an
Auto-Scan). Parse them all:

```powershell
python scripts/smoke_logs.py examples
```

Or regenerate fresh synthetic files anywhere:

```powershell
python scripts/make_samples.py samples
```

Run the tests (no hardware needed — the live path is mocked):

```powershell
pip install -e ".[dev]"
pytest
```

---

## Point it at your VCDS Logs folder

All file tools are confined to one folder, set by the `VCDS_LOGS_DIR`
environment variable (path-traversal outside it is rejected). Default:
`C:\Ross-Tech\VCDS\Logs`.

```powershell
$env:VCDS_LOGS_DIR = "C:\Ross-Tech\VCDS\Logs"
```

In VCDS, this is the folder where measuring-block logs and saved Auto-Scans are
written (VCDS → Options shows the path).

---

## Console scripts

```powershell
vcds-mcp            # run the MCP stdio server (used by Claude Desktop / Code)
vcds-gui            # launch the desktop GUI
vcds-obd-log --duration 30 --rate 5      # log a quick live session to VCDS_LOGS_DIR
vcds-obd-log --list-ports                # list candidate ELM327 ports
```

---

## Launching the GUI

```powershell
vcds-gui                # or:  python -m vcds_gui
```

**No-Python install for end users:** a self-contained Windows installer can be
built with [`installer/build_installer.ps1`](installer/) (PyInstaller + Inno
Setup) — see [`installer/README.md`](installer/README.md). Tagged releases ship
the `VCDS-Toolkit-Setup-<version>.exe` automatically.

- **File Analyzer tab** — open a measuring CSV (and optionally an Auto-Scan),
  toggle channels on/off, run event detection, click an event to jump the cursor
  to its timestamp, and export a clipped CSV of the current view. Channels are
  drawn with per-channel normalization (toggleable) so RPM, °C and mbar are all
  legible on one axis, while the linked vertical cursor reads out every visible
  channel's **real** value and unit at the cursor time.
- **Live (OBD-II) tab** — connect to an ELM327 (port dropdown + manual entry +
  baud), pick from the PIDs the ECU actually supports, watch a live plot, read or
  (behind a confirm dialog) clear DTCs, set event-capture triggers, and Start /
  Stop logging. On stop the session CSV is immediately analyzable in Tab 1.

---

## Add the MCP server to Claude Desktop

A local **stdio** MCP server attaches to **Claude Desktop / Claude Code** — not
the claude.ai web app.

Edit `claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit
Config). Use the **venv Python** so the `vcds_mcp` package is importable, and
**escape backslashes** in JSON:

```json
{
  "mcpServers": {
    "vcds": {
      "command": "D:\\Code\\VAGScanner\\.venv\\Scripts\\python.exe",
      "args": ["-m", "vcds_mcp.server"],
      "env": {
        "VCDS_LOGS_DIR": "C:\\Ross-Tech\\VCDS\\Logs"
      }
    }
  }
}
```

Restart Claude Desktop. The `vcds` tools should appear.

## Add the MCP server to Claude Code

```powershell
claude mcp add --transport stdio --env VCDS_LOGS_DIR=C:\Ross-Tech\VCDS\Logs vcds -- D:\Code\VAGScanner\.venv\Scripts\python.exe -m vcds_mcp.server
```

(`claude mcp list` to confirm it registered.)

### Tools exposed

File tools: `list_logs`, `read_autoscan`, `read_measuring_log`, `channel_stats`,
`find_log_events`.

Live OBD tools (require an ELM327 on the machine running the server; they degrade
gracefully when none is connected): `list_serial_ports`, `obd_status`,
`read_live_dtcs`, `snapshot_pids`, `run_obd_session`.

---

## Example prompts (Claude Desktop / Code)

- *"List the newest measuring logs, then read the most recent one and summarize
  which channels diverge most."*
- *"Read the latest Auto-Scan and group the faults by module — which are
  intermittent?"*
- *"Find events in `boost_pull.CSV` where Boost (actual) drops below 1700 mbar."*
- *"Connect to the ELM327 on COM5, show me the supported PIDs and any stored
  DTCs."*
- *"Run a 60-second OBD session that triggers a capture if MAP exceeds 180 kPa or
  any new DTC appears, then analyze the captured event."*

---

## Versioning & releases

This project follows [Semantic Versioning](https://semver.org/) and keeps a
[`CHANGELOG.md`](CHANGELOG.md) in the [Keep a Changelog](https://keepachangelog.com/)
format. The version lives in **one place** — `pyproject.toml` — and is surfaced
at runtime as `vcds_core.__version__` (and in the GUI title bar).

Releases are cut by GitHub Actions (`.github/workflows/release.yml`). On a
version-tag push it runs the full test suite, builds the wheel + sdist **and the
Windows installer**, and publishes a GitHub Release with all artifacts and
auto-generated notes.

To cut a release:

```powershell
# 1. bump `version` in pyproject.toml
# 2. move the CHANGELOG "Unreleased" items under a new version heading + date
# 3. commit, then tag and push:
git tag v0.2.0
git push origin v0.2.0
```

The tag must match the `pyproject.toml` version (the workflow fails fast if they
disagree). You can also trigger it manually from the **Actions → Release** tab.

## Project layout

```
vcds-toolkit/
  pyproject.toml          console scripts: vcds-mcp, vcds-gui, vcds-obd-log
  src/vcds_core/parse.py  parsers + event detection — stdlib ONLY, no deps
  src/vcds_obd/live.py    live ELM327 logging (deps: obd, pyserial)
  src/vcds_mcp/server.py  FastMCP stdio server (dep: mcp)
  src/vcds_gui/app.py     desktop GUI (deps: PySide6, pyqtgraph)
  scripts/make_samples.py generate synthetic test files
  tests/                  pytest (no hardware; live path mocked)
```

`vcds_core` is intentionally dependency-free so every front-end can rely on it.

## License

MIT
