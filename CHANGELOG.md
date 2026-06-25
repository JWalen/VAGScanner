# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/JWalen/VAGScanner/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.2.0
[0.1.0]: https://github.com/JWalen/VAGScanner/releases/tag/v0.1.0
