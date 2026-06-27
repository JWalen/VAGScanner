"""Headless smoke test for the GUI (offscreen Qt platform).

Skipped automatically when PySide6 / pyqtgraph are not installed. Verifies the
window constructs and that Tab 1 can load a measuring CSV and an Auto-Scan,
populate channels, run event detection and export a clipped CSV — all without a
display or any hardware.
"""

from __future__ import annotations

import os

import pytest

# Force the non-interactive Qt backend before importing anything Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6 import QtWidgets  # noqa: E402

from vcds_core import parse  # noqa: E402
from vcds_gui import app as gui_app  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_window_constructs(qapp):
    win = gui_app.MainWindow()
    assert win.stack.count() == 4  # Dashboard, Files, Live, AI
    assert set(win._nav) == {"dashboard", "files", "live", "ai"}
    win.close()


def test_navigation_switches_pages(qapp):
    win = gui_app.MainWindow()
    win.show_page("live")
    assert win.stack.currentWidget() is win.live_tab
    assert win._nav["live"].isChecked() and not win._nav["files"].isChecked()
    win.show_page("dashboard")
    assert win.stack.currentWidget() is win.dashboard
    win.close()


def test_dashboard_refresh(qapp):
    win = gui_app.MainWindow()
    win.dashboard.refresh()  # should not raise; populates recents/vehicle
    assert win.dashboard.recent_list.count() >= 1
    win.close()


def test_ai_tab_builds(qapp):
    win = gui_app.MainWindow()
    tab = win.ai_tab
    assert tab.chk_tools.isChecked()  # AI-uses-tools toggle present
    assert isinstance(tab.input, gui_app.ChatInput)
    assert tab.chat_list is not None and tab.btn_new is not None  # multi-chat UI
    assert "🤖" in tab.model_label.text()                          # model header
    win.close()


def test_ai_settings_dialog(qapp):
    settings = gui_app.QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
    dlg = gui_app.AiSettingsDialog(settings)
    assert dlg.provider_combo.count() == 3
    dlg.provider_combo.setCurrentIndex(0)
    assert dlg.model_combo.count() >= 1
    assert "API key" in dlg.key_link.text() or "key" in dlg.key_link.text().lower()
    dlg.key_edit.setText("test-key-123")
    dlg._save()  # persists provider/model/key
    pid = settings.value("ai/provider", "", type=str)
    assert settings.value(f"ai/key/{pid}", "", type=str) == "test-key-123"
    settings.setValue(f"ai/key/{pid}", "")  # cleanup


def test_ai_multi_chat(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(gui_app, "DEFAULT_LOGS_DIR", str(tmp_path))
    win = gui_app.MainWindow()
    tab = win.ai_tab
    monkeypatch.setattr(tab, "_chats_path", lambda: str(tmp_path / "ai_chats.json"))
    tab.chats = []
    tab._new_chat()
    tab.history.append({"role": "user", "content": "first question about boost"})
    tab.current["title"] = "first question about boost"
    tab._save_chats()
    # a second chat
    tab._new_chat()
    assert len(tab.chats) == 2 and tab.chat_list.count() == 2
    # rename + search
    tab.chats[0]["title"] = "renamed top"
    tab._refresh_titles()
    assert tab.chat_list.item(0).text() == "renamed top"
    tab._filter_chats("boost")
    hidden = [tab.chat_list.item(i).isHidden() for i in range(tab.chat_list.count())]
    assert any(hidden) and not all(hidden)  # the boost chat stays visible
    # reload from disk persists
    import json
    data = json.load(open(str(tmp_path / "ai_chats.json"), encoding="utf-8"))
    assert any(c.get("title") == "first question about boost" for c in data)
    win.close()


def test_ai_consent_gate(qapp, monkeypatch):
    win = gui_app.MainWindow()
    tab = win.ai_tab
    pid = tab.settings.value("ai/provider", "anthropic", type=str)
    key = f"ai/consent/{pid}"
    tab.settings.setValue(key, False)
    # Decline -> no consent recorded, send blocked
    monkeypatch.setattr(gui_app.QtWidgets.QMessageBox, "exec",
                        lambda self: gui_app.QtWidgets.QMessageBox.No)
    assert tab._ensure_ai_consent() is False
    assert tab.settings.value(key, False, type=bool) is False
    # Accept -> consent recorded (per provider), and subsequent calls don't re-prompt
    monkeypatch.setattr(gui_app.QtWidgets.QMessageBox, "exec",
                        lambda self: gui_app.QtWidgets.QMessageBox.Yes)
    assert tab._ensure_ai_consent() is True
    assert tab.settings.value(key, False, type=bool) is True
    assert tab._ensure_ai_consent() is True  # no dialog needed now
    # A different provider must re-prompt (consent is per-provider)
    tab.settings.setValue("ai/provider", "openai")
    tab.settings.setValue("ai/consent/openai", False)
    assert tab._ensure_ai_consent() is True  # exec still returns Yes here
    tab.settings.setValue(key, False)  # cleanup
    tab.settings.setValue("ai/consent/openai", False)
    tab.settings.setValue("ai/provider", pid)
    win.close()


def test_ai_export_chat(qapp, tmp_path, monkeypatch):
    win = gui_app.MainWindow()
    tab = win.ai_tab
    tab.history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "# H\n- a"}]
    out = str(tmp_path / "chat.md")
    monkeypatch.setattr(gui_app.QtWidgets.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (out, "Markdown (*.md)"))
    monkeypatch.setattr(gui_app.QtWidgets.QMessageBox, "information", lambda *a, **k: None)
    tab._save_chat()
    text = open(out, encoding="utf-8").read()
    assert "### You" in text and "### Assistant" in text and "# H" in text
    win.close()


def test_crash_handling_installs(tmp_path, monkeypatch):
    import logging
    import logging.handlers
    import sys

    monkeypatch.setattr(gui_app, "DEFAULT_LOGS_DIR", str(tmp_path))
    old_hook = sys.excepthook
    try:
        path = gui_app._install_crash_handling()
        assert path.endswith("obd_toolkit.log")
        assert sys.excepthook is not old_hook  # global handler installed
    finally:
        sys.excepthook = old_hook
        for h in list(logging.getLogger().handlers):
            if isinstance(h, logging.handlers.RotatingFileHandler):
                logging.getLogger().removeHandler(h)
                h.close()


def test_logs_dir_is_not_rosstech(monkeypatch):
    monkeypatch.delenv("VCDS_LOGS_DIR", raising=False)
    d = gui_app._default_logs_dir()
    assert "Ross-Tech" not in d and "OBD Toolkit" in d
    monkeypatch.setenv("VCDS_LOGS_DIR", "X:/custom-logs")
    assert gui_app._default_logs_dir() == "X:/custom-logs"


def test_maintenance_dialog(qapp, tmp_path, monkeypatch):
    from vcds_core import garage
    gpath = str(tmp_path / "garage.json")
    garage.save_garage(gpath, [garage.Vehicle(vin="WAUZZZ8K9BA123456", make="Audi", year=2011)])
    monkeypatch.setattr(gui_app, "DEFAULT_LOGS_DIR", str(tmp_path))

    win = gui_app.MainWindow()
    win.settings.setValue("garage/active_vin", "WAUZZZ8K9BA123456")
    dlg = gui_app.MaintenanceDialog(win)
    assert dlg.veh is not None
    dlg.svc_type.setCurrentText("Oil change")
    dlg.svc_mi.setValue(45000)
    dlg.svc_int.setValue(7500)
    dlg._add_service()
    assert dlg.svc_list.count() >= 1
    reloaded = garage.find(garage.load_garage(gpath), "WAUZZZ8K9BA123456")
    assert reloaded.maintenance and reloaded.odometer == 45000
    win.settings.setValue("garage/active_vin", "")
    win.close()


def test_performance_dialog_dyno(qapp, tmp_path):
    from vcds_core import parse
    rows = ["TIME,Engine RPM,Vehicle Speed,Boost (derived)", "s,/min,km/h,kPa"]
    for k in range(51):
        t = k * 0.1
        rows.append(f"{t:.1f},{1000 + 600 * t:.0f},{20 * t:.2f},80")
    p = tmp_path / "pull.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    dlg = gui_app.PerformanceDialog(log)
    assert dlg._curve is not None and dlg.btn_export_dyno.isEnabled()
    assert not dlg.dyno_plot.isHidden()
    dlg.close()


def test_live_alert_hud(qapp):
    win = gui_app.MainWindow()
    lt = win.live_tab
    lt.trigger_rules = [{"channel": "Coolant Temp", "op": ">", "value": 110.0}]
    lt.chk_alert.setChecked(True)
    lt._update_alerts({"Coolant Temp": 120.0})  # breach
    assert not lt.alert_banner.isHidden() and "Coolant Temp" in lt.alert_banner.text()
    assert lt._alert_active
    lt._update_alerts({"Coolant Temp": 95.0})   # back to normal
    assert lt.alert_banner.isHidden() and not lt._alert_active
    win.close()


def test_live_alert_substring_channel_match(qapp):
    # Regression: the banner used an exact key lookup, so a rule channel of
    # "Boost" never fired against the "Boost (derived)" value key.
    win = gui_app.MainWindow()
    lt = win.live_tab
    lt.trigger_rules = [{"channel": "Boost", "op": ">", "value": 100.0}]
    lt.chk_alert.setChecked(True)
    lt._update_alerts({"Boost (derived)": 150.0})  # substring match must trip
    assert not lt.alert_banner.isHidden() and lt._alert_active
    win.close()


def test_connect_identify_creates_garage_vehicle(qapp, tmp_path, monkeypatch):
    from vcds_core import garage
    monkeypatch.setattr(gui_app, "DEFAULT_LOGS_DIR", str(tmp_path))
    win = gui_app.MainWindow()
    lt = win.live_tab
    lt.conn_status.setText("Connected — CAN (5 PIDs) · identifying vehicle…")
    lt._on_identified({"vin": "WAUZZZ8K9BA123456", "calibration_ids": ["CAL1"],
                       "protocol": "CAN", "ecu_name": "ECM", "fuel_type": "Gasoline",
                       "supported_count": 5})
    veh = garage.find(garage.load_garage(str(tmp_path / "garage.json")), "WAUZZZ8K9BA123456")
    assert veh is not None and veh.make == "Audi" and veh.calibration_ids == ["CAL1"]
    assert win.settings.value("garage/active_vin", "", type=str) == "WAUZZZ8K9BA123456"
    assert any("VIN: WAUZZZ8K9BA123456" in h for h in lt.vehicle_header)
    win.settings.setValue("garage/active_vin", "")
    win.close()


def test_live_data_window(qapp):
    from vcds_obd import live
    chans = live.build_channels({"RPM", "COOLANT_TEMP"})
    assert chans
    win = gui_app.LiveDataWindow(chans)
    name = chans[0].name
    win.update_values({name: 1000.0})
    win.update_values({name: 1500.0})
    r = win._rows[name]
    assert win.table.item(r, 1).text() == "1500"   # current
    assert win.table.item(r, 3).text() == "1000"   # min
    assert win.table.item(r, 4).text() == "1500"   # max
    win.update_values({name: 1200.0})
    assert win.table.item(r, 5).text() == "▼"       # trend down
    assert win.rate_combo.count() == 5              # refresh-rate selector
    assert "updates/s" in win.status.text()         # measured rate shown
    win.rate_combo.setCurrentIndex(2)               # 5 Hz
    assert win._interval_ms == 200
    win.close()


def test_popout_windows_are_closable(qapp):
    # Regression: LiveDataWindow opened as a frameless child with no close button.
    from vcds_obd import live
    chans = live.build_channels({"RPM", "COOLANT_TEMP"})
    win_flag = gui_app.QtCore.Qt.Window
    ld = gui_app.LiveDataWindow(chans)
    assert ld.windowFlags() & win_flag  # real top-level window (title bar + close)
    ld.close()
    g = gui_app.GaugeWindow(chans, gui_app.units.AS_LOGGED)
    assert g.windowFlags() & win_flag
    g.close()


def test_shutdown_cleans_up(qapp):
    win = gui_app.MainWindow()
    lt = win.live_tab
    lt._shutdown()  # safe with nothing running; clears window refs
    assert lt._livedata is None and lt._gauges is None
    win.close()  # MainWindow.closeEvent delegates to _shutdown without crashing


def test_onboarding_uses_sidebar_terms_not_tabs():
    joined = "".join(t + b for t, b in gui_app.TOUR_PAGES) + gui_app.HELP_HTML
    assert "Tab 1" not in joined and "Tab 2" not in joined
    assert "Dashboard" in joined and "AI Assistant" in joined
    assert "OBD Toolkit" in joined and "VCDS Toolkit" not in joined


def test_gauge_low_side_threshold_colors_red(qapp):
    from vcds_obd import live
    chans = live.build_channels({"RPM"})
    g = gui_app.GaugeWindow(chans, gui_app.units.AS_LOGGED)
    name = next(iter(g.gauges))
    g.set_thresholds([{"channel": name, "op": "<", "value": 10.0}])
    gauge = g.gauges[name]
    assert gauge.crit_lo == 10.0          # low-side rule honored (was ignored)
    gauge.set_value(5.0)
    assert gauge._color().name().lower() == "#e53e3e"   # breach -> red
    gauge.set_value(50.0)
    assert gauge._color().name().lower() != "#e53e3e"
    g.close()


def test_live_tab_has_livedata_button(qapp):
    win = gui_app.MainWindow()
    assert win.live_tab.btn_livedata is not None
    assert win.live_tab.chk_async is not None  # smooth/async toggle
    win.close()


def test_wifi_button_sets_socket_port(qapp, monkeypatch):
    win = gui_app.MainWindow()
    lt = win.live_tab
    monkeypatch.setattr(gui_app.QtWidgets.QInputDialog, "getText",
                        lambda *a, **k: ("192.168.4.1:35000", True))
    lt.setup_wifi()
    assert lt.port_combo.currentText() == "socket://192.168.4.1:35000"
    win.close()


def test_live_session_dir_per_vehicle(qapp, tmp_path, monkeypatch):
    from vcds_core import garage
    gpath = str(tmp_path / "garage.json")
    garage.save_garage(gpath, [garage.Vehicle(vin="WAUZZZ8K9BA123456", make="Audi", year=2011)])

    win = gui_app.MainWindow()
    monkeypatch.setattr(gui_app, "DEFAULT_LOGS_DIR", str(tmp_path))
    win.settings.setValue("garage/active_vin", "WAUZZZ8K9BA123456")
    d = win.live_tab._session_dir()
    assert d.endswith("2011_Audi_123456")
    win.settings.setValue("garage/active_vin", "")
    assert win.live_tab._session_dir() == str(tmp_path)
    win.close()


def test_svg_icons_and_nav(qapp):
    icon = gui_app._svg_icon("dashboard", "#FF6A00")
    assert not icon.isNull()
    win = gui_app.MainWindow()
    # sidebar nav buttons carry icons now (not emoji text)
    assert not win._nav["dashboard"].icon().isNull()
    assert not win._nav_action["settings"].icon().isNull()
    win.close()


def test_advanced_mode_toggle(qapp):
    win = gui_app.MainWindow()
    win._apply_advanced(False)  # basic: advanced controls hidden
    assert win.analyzer.rule_box.isHidden()
    assert win.live_tab.trig_box.isHidden() and win.live_tab.chk_async.isHidden()
    assert not win.act_enhanced.isVisible()
    win._apply_advanced(True)   # advanced: revealed
    assert not win.analyzer.rule_box.isHidden()
    assert not win.live_tab.trig_box.isHidden()
    assert win.act_enhanced.isVisible()
    win._apply_advanced(False)
    win.close()


def test_settings_dialog(qapp):
    win = gui_app.MainWindow()
    dlg = gui_app.SettingsDialog(win)
    assert dlg.prof_combo.count() == len(gui_app.profiles.PROFILES)
    dlg.chk_dark.setChecked(False)
    dlg.units_combo.setCurrentIndex(1)  # Metric
    dlg._save()
    assert win.settings.value("ui/dark", type=bool) is False
    win.settings.setValue("ui/dark", True)  # restore
    win.close()


def test_markdown_rendering():
    h = gui_app._md_to_html("# Title\n\n- one\n- two\n\n**bold** and `code`")
    assert "<b>Title</b>" in h
    assert "<li>one</li>" in h and "<li>two</li>" in h
    assert "<b>bold</b>" in h and "<code" in h


def test_chat_render_flow(qapp):
    win = gui_app.MainWindow()
    tab = win.ai_tab
    tab.history = [{"role": "user", "content": "hi there"},
                   {"role": "assistant", "content": "# Hello\n- a\n- b"}]
    tab._render()
    html = tab.conversation.toHtml()
    assert "You" in html and "Assistant" in html
    assert "hi there" in html and "Hello" in html
    tab._on_tool("list_logs")   # tool activity shows in the typing indicator
    assert "list logs" in tab.conversation.toHtml()
    win.close()


def test_analyzer_loads_csv_and_runs_events(qapp, samples_dir):
    win = gui_app.MainWindow()
    tab = win.analyzer
    tab.load_csv(samples_dir["advanced"])
    assert tab.chan_list.count() >= 5
    assert "Boost (derived)" not in {  # sanity: advanced sample has no derived chan
        tab.chan_list.item(i).data(0x0100) for i in range(tab.chan_list.count())
    }
    # plot received the channels
    assert "Engine Speed" in tab.plot.channels

    # heuristic event detection populates the list
    tab.run_events(use_rules=False)
    assert tab.event_list.count() > 0

    # Graph / Data view toggle: switching to Data fills a table
    tab.view_combo.setCurrentIndex(1)
    assert tab.center_stack.currentIndex() == 1
    assert tab.data_table.rowCount() > 0
    assert tab.data_table.columnCount() == len(tab.mlog.channels) + 1  # +Time
    assert tab.data_table.horizontalHeaderItem(0).text() == "Time (s)"
    tab.view_combo.setCurrentIndex(0)
    assert tab.center_stack.currentIndex() == 0

    # cursor readout works at an arbitrary time
    tab.plot.set_cursor(2.0)
    assert "t = 2.000" in tab.plot.readout.text()
    win.close()


def test_help_dialog_builds(qapp):
    win = gui_app.MainWindow()
    dlg = gui_app.HelpDialog(win._version, win)
    assert "User Guide" in dlg.windowTitle()
    win.close()


def test_quick_tour_navigation(qapp, tmp_path):
    from PySide6 import QtCore

    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.IniFormat)
    dlg = gui_app.QuickTourDialog(settings, show_startup_default=True)
    assert dlg.stack.count() == len(gui_app.TOUR_PAGES) == 4
    assert dlg.btn_next.text() == "Next"
    assert not dlg.btn_back.isEnabled()  # first page

    # advance to the final page
    for _ in range(dlg.stack.count() - 1):
        dlg._next()
    assert dlg.btn_next.text() == "Finish"
    assert dlg.btn_back.isEnabled()

    # unticking "show at startup" must persist
    dlg.chk.setChecked(False)
    dlg._persist()
    assert settings.value("ui/show_tour", True, type=bool) is False


def test_update_banner_shows_on_found(qapp):
    from vcds_gui.updater import UpdateInfo

    win = gui_app.MainWindow()
    assert win.update_banner.isHidden()  # nothing yet
    info = UpdateInfo(
        version="9.9.9", tag="v9.9.9", name="v9.9.9", notes="notes",
        html_url="https://github.com/JWalen/OBD-Toolkit/releases/tag/v9.9.9",
        installer_url="https://example.test/setup.exe", installer_name="setup.exe",
        installer_size=10, sha256=None,
    )
    win._on_update_found(info)
    assert not win.update_banner.isHidden()
    assert "9.9.9" in win.update_banner.label.text()
    # a "no update" result with a background (non-manual) check is silent
    win._update_manual = False
    win._on_update_none()
    win.close()


def test_analyzer_adds_computed_channels(qapp, tmp_path):
    path = tmp_path / "trims.csv"
    path.write_text(
        "TIME,Engine RPM,Short Fuel Trim 1,Long Fuel Trim 1\n"
        "s,/min,%,%\n0,800,2,12\n1,820,3,14\n2,810,2,16\n",
        encoding="utf-8",
    )
    win = gui_app.MainWindow()
    win.analyzer.load_csv(str(path))
    assert "Fuel Trim Total" in win.analyzer.plot.channels
    assert "AFR (estimated)" in win.analyzer.plot.channels
    win.close()


def test_performance_dialog_builds(qapp, tmp_path):
    rows = ["TIME,Engine RPM,Vehicle Speed", "s,/min,km/h"]
    for k in range(51):
        t = k * 0.1
        rows.append(f"{t:.1f},{1000 + 600 * t:.0f},{20 * t:.2f}")
    path = tmp_path / "pull.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    win = gui_app.MainWindow()
    win.analyzer.load_csv(str(path))
    dlg = gui_app.PerformanceDialog(win.analyzer.mlog, win)
    html = dlg.out.toHtml()
    assert "Acceleration" in html and "power" in html.lower()
    win.close()


def test_unit_system_and_autofit(qapp, tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("TIME,Coolant Temp\ns,°C\n0,80\n1,90\n2,100\n", encoding="utf-8")
    win = gui_app.MainWindow()
    win.analyzer.load_csv(str(path))
    win._apply_units("imperial")
    win.analyzer.plot.set_cursor(2.0)
    assert "°F" in win.analyzer.plot.readout.text()  # converted unit shown
    win.analyzer.plot.auto_fit()  # must not raise
    win._apply_units("as_logged")
    win.analyzer.plot.set_cursor(2.0)
    assert "°C" in win.analyzer.plot.readout.text()
    win.close()


def test_dark_mode_toggle(qapp):
    win = gui_app.MainWindow()
    win._apply_theme(True)   # should not raise; plots re-themed
    assert win.analyzer.plot.plot.backgroundBrush().color().name() == "#15151f"
    win._apply_theme(False)
    win.close()


def test_measure_mode_two_cursors(qapp, tmp_path):
    path = tmp_path / "m.csv"
    path.write_text("TIME,Boost\ns,mbar\n0,1000\n1,1200\n2,1400\n3,1100\n", encoding="utf-8")
    win = gui_app.MainWindow()
    win.analyzer.load_csv(str(path))
    plot = win.analyzer.plot
    plot.set_measure(True)
    plot._cursor_b = 1.0   # simulate a placed second cursor
    plot.vline_b.show()
    plot.set_cursor(3.0)
    assert "Δt" in plot.readout.text()
    win.close()


def test_safe_session_name():
    assert gui_app._safe_session_name("  my pull  ") == "my pull"
    assert gui_app._safe_session_name("boost/test.csv") == "boost_test"
    assert gui_app._safe_session_name("") is None
    assert gui_app._safe_session_name('a:b*c?') == "a_b_c"


def test_live_pid_presets(qapp, tmp_path):
    from PySide6 import QtCore, QtWidgets

    win = gui_app.MainWindow()
    tab = win.live_tab
    # isolate settings to a temp file so we don't touch the user's registry
    tab.settings = QtCore.QSettings(str(tmp_path / "s.ini"), QtCore.QSettings.IniFormat)
    for name, checked in [("Engine RPM", True), ("Coolant Temp", False), ("MAF", True)]:
        it = QtWidgets.QListWidgetItem(name)
        it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
        it.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        it.setData(QtCore.Qt.UserRole, name)
        tab.pid_list.addItem(it)

    tab._presets["MyPull"] = ["Engine RPM", "MAF"]
    tab._apply_preset("MyPull")
    states = {tab.pid_list.item(i).data(QtCore.Qt.UserRole):
              tab.pid_list.item(i).checkState() == QtCore.Qt.Checked
              for i in range(tab.pid_list.count())}
    assert states == {"Engine RPM": True, "Coolant Temp": False, "MAF": True}
    win.close()


def test_auto_gauge_types():
    assert gui_app._auto_gauge("Engine RPM", "rpm")[0] == "needle"
    assert gui_app._auto_gauge("Vehicle Speed", "km/h")[0] == "needle"
    assert gui_app._auto_gauge("Coolant Temp", "°C")[0] == "bar"
    assert gui_app._auto_gauge("Engine Load", "%")[0] == "bar"
    assert gui_app._auto_gauge("Intake MAP", "kPa")[0] == "bar"
    assert gui_app._auto_gauge("Some Counter", "count")[0] == "numeric"


def test_live_gauges_window(qapp):
    from vcds_obd.live import LiveChannel

    win = gui_app.MainWindow()
    chans = [LiveChannel("Engine RPM", "rpm", "RPM"),
             LiveChannel("Intake MAP", "kPa", "INTAKE_PRESSURE"),
             LiveChannel("Coolant Temp", "°C", "COOLANT_TEMP")]
    gw = gui_app.GaugeWindow(chans)
    gw.set_thresholds([{"channel": "MAP", "op": ">", "value": 180}])
    gw.update_values({"Engine RPM": 3000, "Intake MAP": 200, "Coolant Temp": 90})
    gw.show()
    qapp.processEvents()  # force a paint pass (catches paintEvent crashes)
    assert gw.gauges["Intake MAP"].value == 200
    assert gw.gauges["Intake MAP"].crit == 180
    assert gw.gauges["Engine RPM"].kind == "needle"
    assert gw.gauges["Intake MAP"].kind == "bar"
    gw.close()
    win.close()


def test_compare_dialog_builds(qapp, tmp_path):
    from PySide6 import QtWidgets
    from vcds_core.compare import compare_logs

    a = tmp_path / "a.csv"
    a.write_text("TIME,Boost\ns,mbar\n0,1000\n1,1100\n2,1050\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text("TIME,Boost\ns,mbar\n0,1200\n1,1300\n2,1250\n", encoding="utf-8")
    win = gui_app.MainWindow()
    win.analyzer.load_csv(str(a))
    comp = compare_logs(win.analyzer.mlog, parse.parse_measuring_log(str(b)))
    dlg = gui_app.CompareDialog(comp)
    table = dlg.findChild(QtWidgets.QTableWidget)
    assert table is not None and table.rowCount() >= 1
    win.close()


def test_diagnosis_dialog_builds(qapp, samples_dir):
    from vcds_core.diagnose import diagnose as run_diagnose

    win = gui_app.MainWindow()
    win.analyzer.load_scan(samples_dir["autoscan"])
    assert win.analyzer.scan is not None
    report = run_diagnose(scan=win.analyzer.scan)
    dlg = gui_app.DiagnosisDialog(report, win)
    tree = dlg.findChild(QtWidgets.QTreeWidget)
    assert tree is not None and tree.topLevelItemCount() == len(report.findings)
    win.close()


def test_vcds_logging_help_present(qapp):
    text = gui_app.VCDS_LOG_HTML
    assert "Measuring" in text and "Log" in text
    assert "VCDS_LOGS_DIR" in text
    assert r"C:\Ross-Tech\VCDS\Logs" in text
    assert "Getting a log file out of VCDS" in gui_app.HELP_HTML


def test_diagnosis_dialog_has_save_button(qapp, samples_dir):
    from vcds_core.diagnose import diagnose as run_diagnose

    win = gui_app.MainWindow()
    win.analyzer.load_scan(samples_dir["autoscan"])
    report = run_diagnose(scan=win.analyzer.scan)
    dlg = gui_app.DiagnosisDialog(report, None, win.analyzer.scan, None, win)
    assert dlg.btn_save is not None
    win.close()


def test_pdf_export_path(qapp, tmp_path, samples_dir):
    from PySide6 import QtGui
    from vcds_core.diagnose import diagnose as run_diagnose
    from vcds_core.report import build_html_report

    scan = parse.parse_autoscan(samples_dir["autoscan"])
    report = run_diagnose(scan=scan)
    html = build_html_report(report, scan=scan, version="0.3.0")
    out = tmp_path / "report.pdf"
    doc = QtGui.QTextDocument()
    doc.setHtml(html)
    writer = QtGui.QPdfWriter(str(out))
    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
    doc.print_(writer)
    assert out.is_file() and out.stat().st_size > 0


def test_garage_dialog_builds(qapp, tmp_path, monkeypatch):
    win = gui_app.MainWindow()
    monkeypatch.setattr(gui_app.GarageDialog, "GARAGE_PATH", str(tmp_path / "garage.json"))
    dlg = gui_app.GarageDialog(win)
    # seed a vehicle and refill
    from vcds_core import garage
    dlg.vehicles.append(garage.Vehicle(vin="WAUZZZ8K9BA123456", make="Audi", year=2011,
                                       brand_profile="vag"))
    dlg._fill()
    assert dlg.list.count() == 1
    win.close()


def test_resets_dialog_builds(qapp):
    win = gui_app.MainWindow()
    dlg = gui_app.ResetsDialog(win)
    assert dlg.btn_clear is not None
    win.close()


def test_onboard_tests_dialog_builds(qapp):
    tests = [{"command": "MONITOR_CATALYST_B1", "name": "Catalyst", "value": 0.8,
              "min": 0.0, "max": 1.0, "passed": False}]
    dlg = gui_app.OnboardTestsDialog(tests)
    table = dlg.findChild(QtWidgets.QTableWidget)
    assert table is not None and table.rowCount() == 1
    assert table.item(0, 5).text() == "FAIL"
    # empty case
    dlg2 = gui_app.OnboardTestsDialog([])
    assert dlg2.findChild(QtWidgets.QTableWidget) is None


def test_vehicle_info_dialog_builds(qapp):
    from vcds_core.vin import decode_vin

    info = decode_vin("WAUZZZ8K9BA123456")
    readiness = {"mil": False, "dtc_count": 0, "monitors": {
        "misfire_monitoring": {"available": True, "complete": True},
        "evap_monitoring": {"available": True, "complete": False}}}
    dlg = gui_app.VehicleInfoDialog("WAUZZZ8K9BA123456", info, ["CAL1"], readiness, [("P0420", "")])
    html = dlg.findChild(QtWidgets.QTextBrowser).toHtml()
    assert "Audi" in html and "readiness" in html.lower()
    assert "NOT ready" in html  # evap incomplete


def test_enhanced_pids_dialog_builds(qapp):
    win = gui_app.MainWindow()
    dlg = gui_app.EnhancedPidsDialog(win)
    assert dlg.table.rowCount() == len(dlg.pids)
    assert dlg.table.rowCount() >= 1  # bundled examples present
    win.close()


def test_mcp_install_dialog_builds(qapp):
    dlg = gui_app.McpInstallDialog(r"C:\Ross-Tech\VCDS\Logs")
    assert dlg.logs_edit.text() == r"C:\Ross-Tech\VCDS\Logs"
    assert dlg.btn_install is not None


def test_pid_search_filter_and_select(qapp):
    from PySide6 import QtCore, QtWidgets

    win = gui_app.MainWindow()
    tab = win.live_tab
    for name in ["Engine RPM", "Coolant Temp", "Boost (derived)", "Fuel Level"]:
        it = QtWidgets.QListWidgetItem(name)
        it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
        it.setCheckState(QtCore.Qt.Unchecked)
        it.setData(QtCore.Qt.UserRole, name)
        tab.pid_list.addItem(it)

    tab._filter_pids("fuel")
    visible = [tab.pid_list.item(i).text() for i in range(tab.pid_list.count())
               if not tab.pid_list.item(i).isHidden()]
    assert visible == ["Fuel Level"]

    tab._set_pids_checked(True, only_visible=True)
    assert tab.pid_list.item(3).checkState() == QtCore.Qt.Checked  # Fuel Level
    assert tab.pid_list.item(0).checkState() == QtCore.Qt.Unchecked  # hidden, untouched
    assert "1 selected" in tab.lbl_pid_count.text()
    win.close()


def test_live_dtc_tree_enriched(qapp):
    win = gui_app.MainWindow()
    tab = win.live_tab

    class FakeConn:
        def get_dtcs(self):
            return [("P0299", "Turbo/Supercharger Underboost")]

    tab.conn = FakeConn()
    tab.read_dtcs()
    root = tab.dtc_tree.topLevelItem(0)
    assert "P0299" in root.text(0)
    assert root.text(1) == "HIGH"  # severity from knowledge base
    assert root.childCount() >= 1  # likely-causes node
    win.close()


def test_analyzer_loads_autoscan(qapp, samples_dir):
    win = gui_app.MainWindow()
    tab = win.analyzer
    tab.load_scan(samples_dir["autoscan"])
    assert tab.scan_tree.topLevelItemCount() == 3
    assert "WAUZZZ8K9BA123456" in tab.scan_info.text()
    win.close()


def test_export_clip_roundtrips(qapp, samples_dir, tmp_path):
    mlog = parse.parse_measuring_log(samples_dir["advanced"])
    out = str(tmp_path / "clip.CSV")
    n = gui_app._export_clip(mlog, out, 2.0, 6.0)
    assert n > 0
    # the exported clip parses straight back through the core
    reparsed = parse.parse_measuring_log(out)
    assert reparsed.channel("Engine Speed") is not None
    assert reparsed.duration_s is not None and reparsed.duration_s <= 4.5
