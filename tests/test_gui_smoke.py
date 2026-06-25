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
    assert win.tabs.count() == 3  # File Analyzer, Live, AI Assistant
    win.close()


def test_ai_tab_builds(qapp):
    win = gui_app.MainWindow()
    tab = win.ai_tab
    assert tab.provider_combo.count() == 3
    tab.provider_combo.setCurrentIndex(0)
    assert tab.model_combo.count() >= 1  # provider change populated models
    assert "Get a key" in tab.key_link.text()
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
        html_url="https://github.com/JWalen/VAGScanner/releases/tag/v9.9.9",
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
