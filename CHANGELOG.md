# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.31.0] - 2026-06-27

### Fixed (audit round 6 — UX, accessibility & onboarding)
- **Onboarding rewritten for the current UI.** The first-run tour, the F1 User
  Guide and the in-app text described an old "Tab 1 / Tab 2" layout that no longer
  exists — they now describe the **sidebar** (Dashboard / Files / Live / AI
  Assistant / Garage / Settings) and the brand name is consistent ("OBD Toolkit").
- **Gauges read correctly in Metric/Imperial.** The needle/bar fill was computed
  from the raw value against a raw-unit range while the number was converted, so
  the dial disagreed with its own readout — now both use the same converted system.
- **Gauges honor low-side alarms.** Rules like "oil pressure < 1.0" or "voltage <
  11.5" now turn the gauge red (they were silently ignored), matching the alert
  banner.
- **Clearer connect feedback.** When the adapter opens but there's no OBD-II link
  (ignition off / wrong protocol), you get an actionable message instead of a green
  "Connected (0 PIDs)".
- **Capture-trigger input is validated** with a message (empty channel / non-numeric
  value) instead of silently doing nothing.

### Added
- **Keyboard shortcuts** Ctrl+1–4 to switch between Dashboard / Files / Live / AI
  (shown in the sidebar tooltips) — improves keyboard accessibility.

## [1.30.0] - 2026-06-27

### Security / CI (audit round 5 — pipeline & supply chain)
- **Hardened the release workflow** against shell injection — the manual
  `workflow_dispatch` tag input is now passed via `env:` (never interpolated into a
  script body) and validated to look like `v1.2.3` before a signing job uses it.
- **Fixed macOS manual releases** — the tag was resolved with a `:-` default that
  kept the branch name on `workflow_dispatch`, so the `.dmg` was uploaded to a
  non-existent release; now resolved by event, with a tag/version guard like Windows.

### Fixed
- **MCP input validation:** `run_obd_session` rejects non-finite / non-positive
  durations gracefully; `read_measuring_log`/`find_log_events` clamp `max_points`
  and `list_logs` clamps `limit` (no unbounded dumps).
- **Claude Desktop config is written atomically** (temp + replace) so an interrupted
  install can't corrupt your shared MCP config.
- **CLI `--logs-dir` default** now matches the app/MCP (`~/Documents/OBD Toolkit/Logs`)
  instead of the legacy `C:\Ross-Tech\VCDS\Logs`.
- Removed a stale `pint` entry from `THIRD_PARTY_NOTICES.md` (not a dependency).

## [1.29.0] - 2026-06-27

### Fixed (audit round 4 — failure modes & error handling)
- **Garage data-loss prevention.** Saving the garage is now **atomic** (write-temp
  + replace), so a full disk / crash mid-save can't truncate it; loading now
  tolerates an unknown/newer field per record and **moves a corrupt file aside**
  instead of silently treating the garage as empty (and overwriting it).
- **CSV export aligns by time**, not row index — exported clips no longer pair the
  time with the wrong sample of channels that have gaps.
- **A recording that ends early now says so.** A mid-session adapter drop is shown
  as "ended early — partial log saved" instead of a plain "Saved".
- **Saving a recording is crash-safe** — if the logs folder is unwritable, the
  recording is salvaged to a temp file rather than lost.

### Security
- **Updater won't silently install an unverified download.** If no checksum was
  published, the installer runs **visibly** (human gate) instead of `/VERYSILENT`.
- **AI consent is now per-provider** — switching providers re-prompts before any
  car data goes to a different company.
- **Tightened the AI/MCP file sandbox** to resolve symlinks (`realpath`) so a link
  inside the logs folder can't read outside it; added a **file-size cap** on log
  parsing to prevent a huge-file denial of service.

## [1.28.0] - 2026-06-27

### Fixed (audit round 3 — concurrency & performance)
- **Serial-port data race (the big one).** The single ELM327 connection was read
  from up to five threads (live poller, recorder, identify, AI tools, GUI one-shot
  reads) with no locking, which could corrupt/cross-wire ELM327 responses. Every
  adapter operation now goes through a per-connection re-entrant lock, so
  transactions never interleave; closing the port also waits for any in-flight
  read. A recording also waits for identify to finish before it starts.
- **Live plot stays smooth on long sessions** — enabled downsampling + clip-to-view
  (was re-drawing the entire history every sample).
- **Live Data poller no longer pegs a CPU core** in "as fast as possible" mode
  (added a small sleep floor + backpressure).
- **Data table shows the right value per time** — it aligned channels by row index,
  which mismatched any channel with dropped samples; now aligned by time.
- **Update threads**: guarded against being relaunched while running and cleaned up
  after finishing (no leaks / no "destroyed while running" crash).

## [1.27.0] - 2026-06-27

### Fixed (audit round 2)
- **Live alert banner now fires on partial channel names.** It used an exact key
  lookup, so a rule like `Boost > 100` never alerted (the value key is
  "Boost (derived)") even though event-capture and the gauges did — now all three
  use the same substring match.
- **Time-aligned cross-channel math.** Boost target-vs-actual, cam-timing
  deviation, and fuel economy now align the two channels by **time** instead of by
  list index (each channel drops missing rows independently, so indices didn't line
  up) — fixing silently wrong numbers on logs with gaps.
- **`find_log_events` no longer crashes** on a malformed threshold rule (the other
  threshold path was missing the guard).
- **AI chat fixes:** a reply can no longer land in the wrong conversation if you
  switch chats mid-answer; a second send while one is in flight is ignored;
  streaming returns a clear "(stopped…)" notice instead of blank on tool-round
  exhaustion; mid-stream provider errors (rate limit/overload) are now surfaced.
- **Clean shutdown:** the AI-chat and update-check/download worker threads are now
  stopped on exit (no "QThread destroyed while running" crash).
- **Auto-identify no longer overwrites your default profile.** A VIN-derived brand
  profile now applies for the session only; your saved default is untouched.

## [1.26.0] - 2026-06-27

### Fixed (from a multi-agent functionality/security/UI-UX audit)
- **Critical: recording could be lost.** A malformed trigger rule or an adapter
  drop mid-session threw inside the capture loop, and the CSV was only written
  afterward — losing the whole recording. The loop is now crash-safe (writes what
  it has on any error), bad trigger rules are skipped, and the MCP
  `run_obd_session` returns a clean error instead of raising.
- **Log parser truncated at blank rows.** VCDS emits blank separators mid-capture;
  the parser stopped at the first one and discarded the rest. It now bridges blank
  gaps (with a parse note) so the full log is kept.
- **Fuel economy ignored the speed unit** — mph logs were ~61% off. Now
  unit-aware (km/h / mph / m/s).
- **AI replies were hard-capped at ~1024 tokens** (silently truncated); raised the
  default. **`o4-mini`** now works (uses `max_completion_tokens`). OpenAI
  streaming tool-calls fire even when a proxy reports `finish_reason: stop`.
- **Diagnosis no longer pairs unrelated channels** (e.g. "Specified torque" vs
  "Actual intake pressure") into a bogus boost-leak finding.
- **`Clear DTCs` no longer reports success** when the ECU never acked (NULL reply).
- **Security:** the updater sanitizes the downloaded asset name to a basename and
  refuses to write outside the download folder.

## [1.25.2] - 2026-06-27

### Fixed
- **The Live Data window couldn't be closed** — it opened as a frameless child
  widget with no title bar; it's now a proper top-level window.
- **Thread/window lifecycle hardening** (from a full GUI review):
  - the app now **cleans up live worker threads on exit** (`MainWindow.closeEvent`
    → `LiveTab._shutdown`), preventing a possible "QThread destroyed while still
    running" crash when quitting mid-session;
  - **reconnecting no longer reassigns a still-running identify thread**;
  - **Disconnect** now stops a running recording before closing the connection;
  - opening **Gauges / Live Data** again closes the previous window (no leaked
    windows or duplicate pollers on one adapter).

## [1.25.1] - 2026-06-26

### Added
- **Project license: GPL-2.0-or-later** (`LICENSE`). Chosen so the app can bundle
  GPL-2.0 python-OBD for the live ELM327 path and be distributed legitimately;
  `pyproject` metadata and README updated to match.

## [1.25.0] - 2026-06-26

### Changed
- **Repo renamed** to `OBD-Toolkit` (old links auto-redirect); updater, About and
  docs point at the new URL.

### Added
- **Crash/error logging.** A rotating log (`obd_toolkit.log` in the logs folder)
  plus a global exception handler — unhandled errors are logged and shown in a
  dialog instead of dying silently.
- **`THIRD_PARTY_NOTICES.md`** documenting bundled components and their licenses,
  including the important **python-OBD GPL-2.0** consideration for distribution.

## [1.24.0] - 2026-06-26

### Added
- **AI privacy consent.** Before the assistant first sends anything, a one-time
  prompt makes clear that your messages and included context (diagnosis, stored
  logs, live VIN/DTCs) go to the selected provider over the internet — and lets
  you decline. AI Settings now states this too.

## [1.23.0] - 2026-06-26

### Added
- **Basic / Advanced mode.** A clean default view for everyday use; an **Advanced
  mode** toggle (Settings) reveals power-user controls — event-capture threshold
  rules, ⚡ async streaming, live alerts, Enhanced PIDs and Resets. New installs
  start in Basic so the app isn't overwhelming.

## [1.22.0] - 2026-06-26

### Changed
- **UI polish.** Replaced emoji navigation glyphs with crisp, theme-tinted **SVG
  line icons** (lucide, ISC-licensed), added the **app logo** to the sidebar, and
  **aligned the sidebar consistently** (logo lines up with the nav icons).
- **Unified Settings page** (sidebar gear / Tools → Settings…): theme, units,
  default vehicle profile, startup update check, AI provider & key, and the logs
  folder — all in one place instead of scattered across menus.

## [1.21.1] - 2026-06-26

### Added
- The timing chain/belt stretch finding now appends a **brand-specific note** when
  one applies — e.g. the **Ford 3.5 EcoBoost** cam-phaser / chain-stretch tell, and
  the VAG tensioner note — so the finding speaks to the actual engine.

## [1.21.0] - 2026-06-26

### Changed
- **Timing-stretch check now reads VAG's specified-vs-actual camshaft logging.**
  In addition to a dedicated deviation channel, it pairs a *specified* and
  *actual* camshaft-timing channel and flags a large divergence — the way chain
  stretch shows up when logging a **2.0 TSI / 2.0 FSI / 3.0T** (and any cam-phased
  engine). The generic boost target-vs-actual check no longer mis-labels cam
  channels.

## [1.20.0] - 2026-06-26

### Added
- **Timing chain / belt stretch check.** Diagnosis now flags the classic
  **cam-to-crank correlation** codes (P0008/P0009/P0016–P0019, incl. when VCDS
  lists them in the status detail) as a clear **"Possible timing chain / belt
  stretch"** finding with the right causes (worn chain/guides/tensioner, jumped
  belt, VVT actuator, cam/crank sensor). It also flags a **large camshaft-timing
  deviation** from a measuring log. Surfaces in Diagnose, live DTC reads and the
  AI assistant.

## [1.19.0] - 2026-06-26

### Added
- **Brand profiles for all common manufacturers.** Added **GM, Toyota, Honda,
  Nissan, Mazda, Subaru, Hyundai/Kia, Chrysler/Dodge/Jeep/Ram, BMW/Mini and
  Mercedes-Benz** — each with a brand-aware AI persona, known-issue notes, and
  (for most) a pack of common manufacturer-specific DTCs (e.g. Hyundai KSDS
  `P1326`, Nissan CVT `P17F0`, GM Passlock `P1626`, Honda VTEC `P1259`). VINs now
  decode many more makes and auto-select the matching profile. Mazda is now its
  own profile (split from Ford).

## [1.18.0] - 2026-06-26

### Added
- **Chat management**: rename a conversation (double-click), a **search box** to
  filter chats, and a confirmation before deleting.

### Changed
- README rewritten to reflect the full feature set (now "OBD Toolkit").

## [1.17.0] - 2026-06-26

### Changed
- **Modern AI chat UX.** The AI Assistant now works like a normal AI chat:
  - a **conversation list** on the left with **＋ New chat** and **Delete**;
  - **multiple saved chats** (persisted to `ai_chats.json`), auto-titled from your
    first message; existing per-vehicle chats are migrated in;
  - **provider / model / API-key moved to an ⚙ AI Settings** dialog (Tools → AI
    Settings…) instead of cluttering the chat page; the chat header just shows the
    active model.

## [1.16.0] - 2026-06-26

### Added
- **Auto-identify on connect.** Every time you connect an adapter, the app now
  reads the car's **VIN, calibration / ECU IDs, fuel type, protocol** and PID
  count in the background. From the VIN it decodes **make / model-year** and:
  - **creates the vehicle in your Garage the first time it's seen** (and just
    re-activates it on later connects), so its logs always save under that
    profile's folder;
  - selects the matching **brand profile**;
  - **embeds the vehicle ID at the top of every saved log** (as `#` comment
    lines the parser ignores), so a log always says which car it came from.

## [1.15.0] - 2026-06-26

### Changed
- **Responsive UI.** Button toolbars now **wrap to fit any window width** (a new
  flow layout) instead of being clipped, and the window can shrink to a small
  minimum — usable on small/laptop screens and odd window sizes.

## [1.14.0] - 2026-06-26

### Added
- **Graph / Data view toggle** in the File Analyzer: switch the centre panel
  between the line graph and a **raw data table** (Time + every channel, with
  units) for the loaded log.

## [1.13.0] - 2026-06-26

### Added
- **Smooth (Async) live mode.** A new **⚡ Smooth** checkbox on the Adapter bar
  connects with `obd.Async`: the adapter polls the watched PIDs in the background
  and the app reads from a continuously-updated cache, so Live Data, gauges and
  recording stream much more smoothly at high rates. The watch-list follows
  whatever PIDs you're streaming (`PyOBDConnection.rewatch`); one-shot reads
  (DTCs, VIN, resets) still hit the bus correctly. Status shows "⚡ smooth".

## [1.12.0] - 2026-06-26

### Changed
- **Smoother Live Data.** Removed the artificial poll delay (it now streams as
  fast as the adapter allows), added a **Refresh-rate selector** (as-fast-as-
  possible / 10 / 5 / 2 / 1 Hz), and the window now shows the **actual measured
  update rate** and PID count. Fewer selected PIDs = a faster refresh, since an
  ELM327 reads one PID per request.

## [1.11.0] - 2026-06-26

### Added
- **Always-on Live Data screen** (Live tab → 📋 Live Data). A continuously
  streaming table of the selected PIDs — current value, unit, session **min/max**
  and a **trend arrow** — that free-runs on its own poller (no need to start a
  recording). While you *are* recording it updates from the live sample stream,
  and resumes free-running when the recording stops. Includes a Reset min/max.

## [1.10.1] - 2026-06-26

### Fixed
- **macOS `.dmg` build** could intermittently fail with `hdiutil: Resource busy`
  (Spotlight indexing the fresh `.app`); the packaging step now retries. Also made
  the GitHub-release step idempotent for re-runs.

## [1.10.0] - 2026-06-26

### Added
- **Maintenance & Reminders** (Tools → Maintenance & Reminders…): per-vehicle
  **odometer**, a **service log** with **mileage-based reminders** (oil, belts,
  fluids — overdue flagged in red, upcoming in amber), and a **fuel/cost log**
  with running economy and total spend. Stored per vehicle in the garage.

### Fixed
- Release CI now judges the test run by its JUnit results rather than the process
  exit code, so the harmless offscreen-Qt shutdown segfault no longer fails an
  all-green build. (This had blocked the 1.9.0 release; its performance pack ships
  here.)

## [1.9.0] - 2026-06-26

### Added
- **Performance pack.** The Performance dialog now reports **drag-strip** figures
  (0–60 mph / 0–100 km/h, plus quarter-mile time + trap speed when the run is long
  enough) and draws a **virtual dyno** — estimated crank HP & torque vs RPM — with
  **Export dyno CSV**. (`perform.dyno_curve`, `perform.dragstrip`.)

## [1.8.0] - 2026-06-26

### Added
- **Live alert HUD.** During logging, the Live tab now **flashes a red banner and
  beeps** the moment a threshold rule is breached (e.g. coolant > 110, boost >
  limit, AFR too lean), and clears when values return to normal. Toggle with the
  **🔔 Alerts** checkbox.

## [1.7.0] - 2026-06-26

### Added
- **Wi-Fi & Bluetooth adapters.** A new **📶 Wi-Fi…** button connects to Wi-Fi
  ELM327 dongles (e.g. `192.168.0.10:35000`) over `socket://`. Bluetooth adapters
  work via their paired COM port. Broadens hardware support well beyond USB.

## [1.6.0] - 2026-06-26

### Added
- **Per-vehicle log folders.** When a vehicle is active (set from its VIN), live
  sessions are saved into a subfolder named from the car —
  e.g. `2011_Audi_123456` — instead of all logs landing in one pile. The
  Dashboard's recent-logs list and the AI's `list_logs`/`read_log` now look into
  those subfolders too.

## [1.5.1] - 2026-06-25

### Changed
- **Re-themed app icon** to match the carbon look — a graphite tile with an amber
  boost gauge, redline sweep and racing-red needle. Ships as a Windows `.ico`,
  a macOS `.icns` (regenerated natively in CI) and a `.png`, so the **macOS app
  is now branded**.

## [1.5.0] - 2026-06-25

### Added
- **Carbon dashboard redesign.** A motorsport-styled dark theme is now the
  default, with a **left sidebar** (Dashboard · Files · Live · AI · Garage) and a
  **Dashboard home** offering quick Connect / Open-log actions, the active
  vehicle, and recent logs. The flow now starts from one clear place. (A light
  theme is still available via View → Dark mode.)
- **macOS support.** CI now also builds an `OBD Toolkit.app` packaged as a
  **`.dmg`**, and the in-app updater downloads the right file per OS. On macOS the
  app covers live OBD-II + file analysis (VCDS itself remains Windows-only).
- **Dedicated logs folder.** The app's own logs, garage and chat now live in
  **`~/Documents/OBD Toolkit/Logs`** instead of the Ross-Tech folder (existing
  `garage.json` is migrated over). Open dialogs still default to the Ross-Tech
  folder for importing VCDS files. New **Tools → Open logs folder**.

## [1.4.2] - 2026-06-25

### Fixed
- **App reported an old version (e.g. 1.0.1) after updating.** The real cause:
  each update left its `vcds_toolkit-<ver>.dist-info` behind in the install
  folder, and the app read the version by scanning those — returning the oldest.
  Now the build embeds a literal `_version.py` that is authoritative, and the
  installer **deletes stale `*.dist-info` folders** before installing. (To pick
  this up you must update once; existing piled-up folders are cleaned by the new
  installer — or delete the old `_internal\vcds_toolkit-*.dist-info` folders by
  hand.)

## [1.4.1] - 2026-06-25

### Fixed
- **Silent auto-update sometimes didn't apply** (app reopened on the old
  version). The update helper now force-closes the running app before installing
  (so its files aren't locked), writes an install log to the temp folder for
  diagnosis, and then relaunches. Note: to receive this fix you must update once
  via the installer (the old updater ships inside the running app).

## [1.4.0] - 2026-06-25

### Added
- **Save chat transcript** — export the conversation to Markdown, plain text or
  HTML ("Save chat…" in the AI tab).
- **Per-vehicle chat memory** — the assistant's conversation is now tied to the
  **active garage vehicle**: switching the active vehicle loads that car's saved
  chat, and replies are remembered per car across sessions (stored in the
  garage). A label shows whose chat you're in.

## [1.3.0] - 2026-06-25

### Added
- **Streaming AI responses** — replies now appear word-by-word as the model
  types (SSE), for Claude, GPT and Gemini, including while it uses tools.
- **The assistant can act to troubleshoot.** Its toolbox grew well beyond log
  browsing: `find_events`, `performance`, `lookup_dtc`, and — when the car is
  connected in the Live tab — **live tools**: `obd_status`, `read_live_dtcs`,
  `snapshot_pids`, `vehicle_info`, `readiness`. It's prompted to gather data
  with these before concluding, so you get an end-to-end diagnosis.

## [1.2.0] - 2026-06-25

### Changed
- **AI chat now feels like a normal chat**: assistant replies render **Markdown**
  (headings, bold/italic, bullet & numbered lists, code), messages show as clean
  **You / Assistant** blocks with auto-scroll, **Enter sends** (Shift+Enter for a
  newline), a live **typing indicator**, and a **"🔧 reading logs…"** activity line
  while the assistant is browsing your logs.

## [1.1.0] - 2026-06-25

### Added
- **The AI assistant can now browse your stored logs.** With "Let the AI browse
  stored logs" enabled (default), the assistant gets function-calling tools —
  `list_logs`, `read_log`, `read_autoscan`, `diagnose_log` — confined to your
  logs folder, so you can ask things like *"which of my saved logs shows the
  worst boost drop?"* or *"diagnose the newest Auto-Scan."* Works with Claude,
  GPT and Gemini.

## [1.0.1] - 2026-06-25

### Fixed
- **Installer could leave a second copy** alongside an older version (per-user vs
  per-machine), so the launched app sometimes still showed the old version. The
  installer now always installs **per-user** and **removes any previous install
  first**, so updates replace the old app cleanly.

## [1.0.0] - 2026-06-25

First stable release. A full-featured, multi-brand OBD-II / VAG desktop app with
an MCP server and an AI assistant.

### Added
- **Unattended (silent) updates**: when you accept an update it now closes,
  installs in the background with no wizard, and **reopens automatically**.

### Summary of what's in 1.0.0
- **Analyze**: VCDS measuring logs & Auto-Scans plus generic importers
  (Torque / OBD Fusion / FORScan); diagnostic engine + fault-code knowledge base;
  computed channels; performance, trip/economy, battery and A/B comparison;
  PDF/HTML reports.
- **Live OBD-II**: all supported PIDs, redesigned needle/bar gauges, event-capture,
  DTCs with fixes, vehicle info, emissions readiness + smog report, Mode 06, and
  safe resets.
- **Brands**: VAG / Ford / Generic profiles (auto-selected from VIN), Ford code
  pack, multi-vehicle garage, experimental enhanced (mode-22) PIDs.
- **Integrations**: MCP server (~18 tools) with one-click install, and an in-app
  AI assistant (Claude / GPT / Gemini).
- **App**: dark mode, units selector, auto-fit, presets, help/tour, auto-update,
  and a signed-capable Windows installer.

## [0.21.0] - 2026-06-25

### Added
- **Resets / Service** (Tools → Resets / Service…): a safe, standardized
  **Clear DTCs & reset readiness monitors** action (mode 04) behind a
  confirmation — plus an honest note that oil/service reset, coding/adaptations
  and ECU tuning are manufacturer-specific (VCDS/OBDeleven/FORScan), often need
  security access a generic ELM327 lacks, and are intentionally not performed
  here to avoid bricking a module.

## [0.20.0] - 2026-06-25

### Added
- **Mode 06 on-board test results** (Live tab → "Mode 06"): reads the ECU's
  on-board monitoring tests (catalyst, O2 sensors, EVAP…) with value / min / max
  and a pass-fail result, and an MCP `onboard_tests` tool.

## [0.19.0] - 2026-06-25

### Added
- **Drive-cycle helper + smog readiness report**: the Vehicle Info dialog now
  shows a **drive-cycle tip** next to each incomplete monitor, and a **Save Smog
  Report…** button produces a one-page PDF/HTML emissions-readiness report
  (READY / NOT-ready verdict, monitor table with tips, MIL, permanent DTCs) via
  `vcds_core.report.build_smog_html`.

## [0.18.0] - 2026-06-25

### Added
- **Garage** (Tools → Garage…): save multiple vehicles by **VIN** with make /
  year / brand profile, a nickname and mass, and a per-vehicle **session
  history**. Set an **active vehicle** (which applies its brand profile). Reading
  **Vehicle Info** auto-adds the car and makes it active; finished live sessions
  are recorded under it (`vcds_core.garage`).

## [0.17.0] - 2026-06-25

### Added
- **Trip / fuel-economy + battery analysis** in the Performance dialog and the
  MCP `analyze_performance` tool: estimated economy (L/100km & US mpg from a Fuel
  Rate channel, or from MAF), distance, fuel used, idle %, plus battery
  min/avg/max with cranking-dip and charging-voltage checks (`vcds_core.trip`).

## [0.16.0] - 2026-06-25

### Added
- **Vehicle Info & emissions readiness** (Live tab → "ⓘ Vehicle Info"): reads the
  **VIN** (with make / model-year decode), ECU **calibration IDs**, **I/M
  readiness monitors** (ready / not-ready / n-a), **MIL** state, and **permanent
  DTCs** (mode 0A), with an overall "ready to pass emissions" verdict. The brand
  **profile is auto-selected from the VIN**.
- `vcds_core.vin` decoder (make / model year / brand profile from WMI + year code).
- MCP tools: **`vehicle_info`** and **`readiness_monitors`** so Claude can read
  VIN/cal-IDs and emissions readiness.

## [0.15.0] - 2026-06-25

### Changed
- **Live gauge dashboard redesigned**: now **scrollable**, with **needle dials**
  for RPM/speed, **bar gauges** for temperatures/load/pressure, and numeric
  tiles — auto-selected per channel. **Right-click any gauge** to change its
  type (needle / bar / numeric) or its range; your choices are remembered.
  Gauges still colour amber/red on threshold-trigger rules and honor the units
  selector.

## [0.14.1] - 2026-06-25

### Added
- Optional **session name** field for live OBD logging — name the saved CSV
  instead of the timestamped default (sanitized to a safe filename).

### Fixed
- **"Install MCP Server → Claude Code"** failed with *missing required argument
  'commandOrUrl'* — the variadic `--env` flag was consuming the server name. Now
  uses the documented `claude mcp add <name> … -- <command>` order.
- Clearer message when the **Claude Desktop config is invalid JSON** — it now
  names the file (and line/column) to fix, and never overwrites your other
  servers.

## [0.14.0] - 2026-06-25

### Added
- **Units selector** (View → Units: *As logged* / *Metric* / *Imperial*):
  converts displayed values in the plot cursor readout and the live gauges
  (°C↔°F, km/h↔mph, kPa/mbar↔psi, km↔mi, L↔gal, N·m↔lb-ft). Bidirectional, so an
  imperial-sourced log can be shown metric and vice-versa; unknown units pass
  through unchanged. Remembered between sessions.
- **"⤢ Fit" button** in the File Analyzer to auto-fit the graph to all visible
  data.

## [0.13.0] - 2026-06-25

### Added
- **Enhanced PIDs (experimental)** — a framework for manufacturer-specific
  UDS service-$22 PIDs (`vcds_obd.enhanced`): a user-editable library of PIDs
  (name, 16-bit DID, unit, and a **safe formula** over the response data bytes),
  a query path over the ELM327 (`Connection.query_raw`), and a Tools → "Enhanced
  PIDs (experimental)" dialog to read them against a connected adapter and save
  the library to JSON.
  - ⚠ DIDs/formulas are vehicle-specific and **not validated** — the bundled
    entries are clearly-marked examples; edit the JSON with values for your
    vehicle (e.g. FORScan community lists) before trusting readings. Reads are
    read-only and safe.

## [0.12.0] - 2026-06-25

### Added
- **Generic-app CSV importer** (`vcds_core.importers`): open logs from **Torque**,
  **OBD Fusion**, **FORScan** and similar apps (any brand). Handles
  `Name(unit)` headers, timestamp **or** seconds time columns, and leading
  GPS/metadata columns. The File Analyzer auto-detects VCDS vs generic format on
  open, so file analysis is no longer VCDS-only.
- **Ford fault-code pack**: common Ford/Lincoln P1xxx codes (P1131/P1151 lean,
  P1260 PATS, P1289 CHT, EVAP, …) consulted when the Ford profile is active;
  exposed via `knowledge.lookup(code, brand=...)` and the MCP `lookup_dtc`
  (`profile` arg).

## [0.11.0] - 2026-06-25

### Added
- **Vehicle/brand profiles** (`vcds_core.profiles`): **VAG**, **Ford** and
  **Generic OBD-II**. Choose one via **View → Vehicle profile**. The profile
  selects brand-specific known-issue notes and the AI assistant's persona, while
  the standard fault codes and data heuristics stay universal — so the live
  side, gauges, performance and generic-code diagnosis already work on any
  OBD-II vehicle. (First step toward full multi-brand support.)

### Changed
- The app's display name is now the brand-neutral **"OBD Toolkit"** (window
  title / About). Internal package, repo and installer identifiers are unchanged
  so auto-update and the MCP integration keep working.
- Diagnosis (`diagnose(..., profile=...)`) and the AI assistant are now
  brand-aware; VAG-flavored per-code notes only appear under the VAG profile.

## [0.10.0] - 2026-06-24

### Added
- **PID presets** in the Live tab: save the currently-checked PIDs under a name
  and re-apply them later (e.g. a "Boost diagnosis" or "Fueling" set), stored in
  local settings.

## [0.9.0] - 2026-06-24

### Added
- **Dark mode** (View → Dark mode, remembered) for the whole app, plots included.
- **Live gauge dashboard** (Live tab → "📊 Gauges"): large value tiles for the
  selected PIDs that update live and turn amber/red when a threshold-trigger
  rule is exceeded.
- **Two-cursor measurement** on the plot ("Measure" toggle in the File
  Analyzer): click to drop a second cursor and read Δtime plus each channel's
  Δvalue between the two cursors.

## [0.8.0] - 2026-06-24

### Added
- **Session comparison** (`vcds_core.compare`): open a second log and compare it
  to the current one channel-by-channel — A/B min/max/mean with colour-coded
  deltas (Δ = B − A). Surfaced via a "⇄ Compare…" button in the File Analyzer.
  Great for before/after a repair or tune, or pull-vs-pull.

## [0.7.0] - 2026-06-24

### Added
- **Performance analysis** (`vcds_core.perform`): acceleration-run timing
  (0–100 km/h / 0–60 mph and the next band), wide-open-throttle **pull
  detection**, and an estimated **crank power & torque** figure derived from the
  speed trace + vehicle mass. Surfaced via a "📈 Performance" button in the File
  Analyzer and the MCP `analyze_performance` tool. (Estimate is approximate —
  great for before/after comparison, not a calibrated dyno number.)

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

[Unreleased]: https://github.com/JWalen/OBD-Toolkit/compare/v1.31.0...HEAD
[1.31.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.31.0
[1.30.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.30.0
[1.29.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.29.0
[1.28.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.28.0
[1.27.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.27.0
[1.26.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.26.0
[1.25.2]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.25.2
[1.25.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.25.1
[1.25.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.25.0
[1.24.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.24.0
[1.23.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.23.0
[1.22.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.22.0
[1.21.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.21.1
[1.21.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.21.0
[1.20.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.20.0
[1.19.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.19.0
[1.18.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.18.0
[1.17.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.17.0
[1.16.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.16.0
[1.15.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.15.0
[1.14.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.14.0
[1.13.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.13.0
[1.12.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.12.0
[1.11.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.11.0
[1.10.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.10.1
[1.10.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.10.0
[1.9.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.9.0
[1.8.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.8.0
[1.7.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.7.0
[1.6.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.6.0
[1.5.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.5.1
[1.5.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.5.0
[1.4.2]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.4.2
[1.4.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.4.1
[1.4.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.4.0
[1.3.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.3.0
[1.2.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.2.0
[1.1.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.1.0
[1.0.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.0.1
[1.0.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v1.0.0
[0.21.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.21.0
[0.20.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.20.0
[0.19.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.19.0
[0.18.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.18.0
[0.17.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.17.0
[0.16.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.16.0
[0.15.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.15.0
[0.14.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.14.1
[0.14.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.14.0
[0.13.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.13.0
[0.12.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.12.0
[0.11.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.11.0
[0.10.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.10.0
[0.9.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.9.0
[0.8.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.8.0
[0.7.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.7.0
[0.6.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.6.0
[0.5.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.5.0
[0.4.1]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.4.1
[0.4.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.4.0
[0.3.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.3.0
[0.2.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.2.0
[0.1.0]: https://github.com/JWalen/OBD-Toolkit/releases/tag/v0.1.0
