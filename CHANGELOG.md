# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-06-24

### Added
- **In-app AI Assistant** (new tab): chat with **Anthropic (Claude)**,
  **OpenAI (GPT)** or **Google (Gemini)** to help diagnose the car. Enter and
  save an API key per provider, choose a model, and the assistant automatically
  includes your current scan/log diagnosis as context. Uses thin REST clients
  (no provider SDKs); API keys are stored in local user settings.

## [0.5.0] - 2026-06-24

### Added
- **One-click "Install MCP Server (for Claude)"** (Tools menu): registers the
  server with Claude Desktop (writes the config, backing up any existing one)
  and/or Claude Code (via the `claude` CLI). The installer now ships a dedicated
  console **`vcds-mcp.exe`** alongside the GUI, so no separate Python is needed.
- **Searchable, resizable PID picker** in the Live tab — a search box, "Select
  shown" / "Clear all", a selected/total count, and a draggable splitter so the
  list can be made as large as you like (handy with 100+ supported PIDs).
- **Stored DTCs now show descriptions, severity and likely causes/fixes** in the
  Live tab (a color-coded tree), and the MCP `read_live_dtcs` tool returns the
  same enriched data for Claude.

## [0.4.1] - 2026-06-24

### Added
- **Live OBD-II offers every PID the ECU supports**, not just the curated set.
  The Live tab now lists all supported PIDs (the curated defaults are checked,
  the rest are available to tick); `build_channels` gains an `include_all`
  option and dynamically names/units unknown PIDs.

### Fixed
- **Update check failed with `SSL: CERTIFICATE_VERIFY_FAILED`** in the installed
  app — the PyInstaller bundle had no CA store. Now ships `certifi` and uses it
  for HTTPS verification.
- **App showed the wrong version (0.1.0)** when installed — the frozen bundle
  lacked package metadata so `__version__` fell back to a hard-coded default.
  The dist metadata is now bundled, so the real version shows and the updater
  compares correctly.

## [0.4.0] - 2026-06-24

### Added
- **Diagnostic report export** (PDF or HTML) from the Diagnosis dialog —
  prioritized findings with causes, channel-statistics table, scan faults and
  the current plot embedded. Built by the dependency-free
  `vcds_core.report.build_html_report`; the GUI renders PDF via Qt.
- **In-app VCDS logging help**: a "How to Log in VCDS…" Help-menu item and a
  matching section in the User Guide, explaining how to produce a `.CSV` with
  Advanced Measuring Values / Measuring Blocks and where it's saved.

## [0.3.0] - 2026-06-24

### Added
- **Fault-code knowledge base** (`vcds_core.knowledge`): descriptions, severity
  and most-likely causes for common VAG/Audi + generic OBD-II codes, plus
  VAG known-issue notes (PCV, carbon build-up, diverter valve, HPFP follower,
  timing chain). Structural decoding gives even unknown codes a sensible
  category. Enriches Auto-Scans and gives raw ELM327 DTCs real meaning.
- **Diagnostic engine** (`vcds_core.diagnose`): turns a scan and/or a measuring
  log into prioritized findings with likely causes, combining fault codes with
  data symptoms — lean/rich fuel trims, overheating, target-vs-actual boost
  shortfall, rising misfire counters and intake heat-soak.
- **Computed channels** (`vcds_core.compute`): Fuel Trim Total, AFR (estimated)
  and derived boost via a safe (sandboxed) expression evaluator.
- **GUI**: a "🔍 Diagnose" button and color-coded Diagnosis dialog; Auto-Scan
  faults now list likely causes; computed channels are added on load.
- **MCP**: `lookup_dtc` and `diagnose_file` tools; `read_measuring_log` gains an
  `include_computed` option.

### Fixed
- **Live DTC read/clear now work.** `get_dtcs()` queried mode 03 without
  `force=True` (so python-OBD silently returned nothing), and the default
  `obd.Async` connection served cached watch values rather than live reads.
  Now defaults to a blocking connection, forces the DTC commands, reads pending
  codes (mode 07) too, and de-duplicates.

## [0.2.0] - 2026-06-24

### Added
- In-app help: a **Help** menu with a scrollable **User Guide** (F1) and an
  **About** box, plus a **Quick Tour** shown on first start-up (with a "show at
  startup" toggle, re-openable from Help → Quick Tour).
- In-app updates: the GUI checks the GitHub Releases API on startup (and via
  Help → Check for Updates…), shows an update banner, and can download the
  release installer (verifying its SHA-256), run it and relaunch. Startup
  checking is toggleable.

## [0.1.0] - 2026-06-24

First public release.

### Added

**Parsing core (`vcds_core`, standard-library only)**
- Measuring-log CSV parser that *infers* structure instead of hard-coding it:
  tries utf-8-sig / utf-8 / cp1252 / latin-1; detects comma/semicolon/tab
  delimiters; parses both `1.5` and locale `1,5` decimals; finds the data region
  as the first run of mostly-numeric rows and echoes the detected header rows.
- Handles both the classic group layout (multiple `TIME` columns, `Group A:` /
  `'115` metadata stripped) and the Advanced/UDS layout; maps each value column
  to the nearest time column on its left.
- Per-channel stats over full data with down-sampled series for transport.
- Auto-Scan `.TXT` parser using indentation to attach each fault's status detail
  (not mistake it for a new fault); reconciles `N Faults Found` counts.
- `find_events`: specified-vs-actual divergence, rising counters, per-channel
  extremes, and custom threshold rules.

**Live capture (`vcds_obd`)**
- ELM327 logging via python-OBD (`obd.Async` preferred, blocking fallback) plus a
  raw pyserial AT-command driver as a last resort.
- Derived `Boost (derived)` = MAP − barometric pressure.
- Sessions written in the same flat CSV layout `vcds_core` parses, so they
  round-trip straight back through every analysis tool.
- Event-triggered capture with a rolling ring buffer (new DTC or threshold),
  read stored DTCs, and explicit-only `CLEAR_DTC`.

**MCP server (`vcds_mcp`)**
- FastMCP stdio server; file access confined to `VCDS_LOGS_DIR` (path-traversal
  rejected); logs to stderr only.
- File tools: `list_logs`, `read_autoscan`, `read_measuring_log`,
  `channel_stats`, `find_log_events`.
- Live tools: `list_serial_ports`, `obd_status`, `read_live_dtcs`,
  `snapshot_pids`, `run_obd_session` (300 s hard cap).

**Desktop GUI (`vcds_gui`)**
- PySide6 + pyqtgraph app with a File Analyzer tab and a Live (OBD-II) tab
  sharing one plotting widget with a value-reading linked cursor and per-channel
  normalization.

**Tooling & packaging**
- `scripts/make_samples.py` and committed `examples/`; `scripts/smoke_logs.py`.
- Windows installer (PyInstaller one-folder bundle + Inno Setup) with a branded
  boost-gauge application icon (`scripts/make_icon.py`).
- GitHub Actions release workflow: tests, builds the wheel/sdist and the
  installer, and publishes a GitHub Release on each `v*` tag.
- 54-test pytest suite (no hardware; the live path is mocked).

[Unreleased]: https://github.com/JWalen/VAGScanner/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.6.0
[0.5.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.5.0
[0.4.1]: https://github.com/JWalen/VAGScanner/releases/tag/v0.4.1
[0.4.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.4.0
[0.3.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.3.0
[0.2.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.2.0
[0.1.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.1.0
