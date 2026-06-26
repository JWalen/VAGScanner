"""Desktop GUI for the VCDS toolkit (PySide6 + pyqtgraph).

Two tabs share ONE plotting widget:

  * Tab 1 — File Analyzer: open a measuring CSV (+ optional Auto-Scan), toggle
    channels, run event detection, and export a clipped CSV of the current view.
  * Tab 2 — Live (OBD-II): connect to an ELM327, pick supported PIDs, watch a
    live plot, read/clear DTCs, configure event-capture triggers and log a
    session that is immediately analyzable in Tab 1.

Multi-scale UX choice
---------------------
Channels span wildly different scales (RPM vs °C vs mbar). We use **per-channel
min-max normalization** for the on-screen traces (toggleable) so every channel
is legible on one shared axis, while the linked vertical cursor always reads out
each visible channel's REAL value and unit at the cursor time. This keeps a
single clean crosshair instead of a forest of stacked y-axes.
"""

from __future__ import annotations

import bisect
import html as _html
import os
import re
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from vcds_core import compare as compare_mod
from vcds_core import compute, garage as garage_mod
from vcds_core import knowledge, parse, perform, profiles, trip, units
from vcds_core.diagnose import diagnose as run_diagnose
from vcds_core.diagnose import report_to_text
from vcds_core.importers import open_measuring_file
from vcds_core.report import build_html_report
from vcds_gui import ai, updater
from vcds_obd import live

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    import pyqtgraph as pg

    _HAVE_QT = True
    _IMPORT_ERR: Optional[Exception] = None
except Exception as exc:  # noqa: BLE001
    _HAVE_QT = False
    _IMPORT_ERR = exc


def _default_logs_dir() -> str:
    """The app's own data folder (live logs, garage, chat) — cross-platform."""
    env = os.environ.get("VCDS_LOGS_DIR")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), "Documents", "OBD Toolkit", "Logs")


DEFAULT_LOGS_DIR = _default_logs_dir()
# Where Ross-Tech VCDS writes its measuring CSVs / Auto-Scans (Windows only).
LEGACY_LOGS_DIR = r"C:\Ross-Tech\VCDS\Logs"


def _vcds_import_dir() -> str:
    """Default folder for *opening* VCDS files (its log folder if present)."""
    return LEGACY_LOGS_DIR if os.path.isdir(LEGACY_LOGS_DIR) else DEFAULT_LOGS_DIR


def _open_folder(path: str) -> None:
    """Open a folder in the OS file manager (Windows/macOS/Linux)."""
    try:
        os.makedirs(path, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception:  # noqa: BLE001
        pass


def _migrate_legacy_data() -> None:
    """One-time copy of garage/enhanced data out of the old Ross-Tech folder."""
    try:
        os.makedirs(DEFAULT_LOGS_DIR, exist_ok=True)
        if os.path.normcase(DEFAULT_LOGS_DIR) == os.path.normcase(LEGACY_LOGS_DIR):
            return
        import shutil
        for name in ("garage.json", "enhanced_pids.json"):
            src = os.path.join(LEGACY_LOGS_DIR, name)
            dst = os.path.join(DEFAULT_LOGS_DIR, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
    except Exception:  # noqa: BLE001
        pass

# Distinct trace colours cycled across channels.
_PALETTE = [
    "#0066CC", "#E53E3E", "#38A169", "#DD6B20", "#00C9A7", "#805AD5",
    "#D69E2E", "#3182CE", "#DD2C8B", "#2C7A7B", "#9F7AEA", "#718096",
]


if _HAVE_QT:
    pg.setConfigOptions(antialias=True, background="w", foreground="k")

    # Carbon "motorsport" palette — the default look.
    CARBON = {
        "bg": "#15171C", "base": "#0F1115", "surface": "#1E2228", "line": "#262A31",
        "text": "#E8EAED", "muted": "#A8AEB8", "accent": "#FF6A00", "accent2": "#FF7E26",
        "red": "#E10600",
    }

    _CARBON_QSS = """
    QWidget {{ background:{bg}; color:{text}; }}
    QMainWindow, QDialog {{ background:{bg}; }}
    QFrame#Sidebar {{ background:{base}; border-right:1px solid {line}; }}
    QLabel#Brand {{ color:{accent}; font-size:13pt; font-weight:bold; }}
    QToolButton#Nav {{ color:{muted}; border:none; padding:10px 12px; text-align:left;
        border-radius:8px; font-size:10.5pt; }}
    QToolButton#Nav:hover {{ background:{surface}; color:{text}; }}
    QToolButton#Nav:checked {{ background:{surface}; color:{accent};
        border-left:3px solid {accent}; }}
    QFrame#Card {{ background:{surface}; border:1px solid {line}; border-radius:12px; }}
    QLabel#H1 {{ font-size:18pt; font-weight:bold; color:{text}; }}
    QLabel#Muted {{ color:{muted}; }}
    QPushButton {{ background:{surface}; color:{text}; border:1px solid {line};
        border-radius:8px; padding:7px 14px; }}
    QPushButton:hover {{ background:#2E333B; }}
    QPushButton:disabled {{ color:#5A616B; background:#1A1D23; border-color:#22262E; }}
    QPushButton#Accent {{ background:{accent}; color:#15171C; border:none; font-weight:bold; }}
    QPushButton#Accent:hover {{ background:{accent2}; }}
    QTabWidget::pane {{ border:1px solid {line}; }}
    QTabBar::tab {{ background:#1A1D23; color:{muted}; padding:7px 14px; border:1px solid {line};
        border-bottom:none; }}
    QTabBar::tab:selected {{ background:{surface}; color:{accent}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QListWidget, QTableWidget, QTreeWidget, QTextBrowser {{
        background:{base}; color:{text}; border:1px solid #2A2F37; border-radius:6px;
        selection-background-color:{accent}; selection-color:#15171C; }}
    QHeaderView::section {{ background:#1A1D23; color:{muted}; border:none; padding:4px; }}
    QMenuBar {{ background:{base}; color:{text}; }}
    QMenuBar::item:selected {{ background:{surface}; }}
    QMenu {{ background:#1A1D23; color:{text}; border:1px solid #2A2F37; }}
    QMenu::item:selected {{ background:{accent}; color:#15171C; }}
    QStatusBar {{ background:{base}; color:{muted}; }}
    QToolTip {{ color:{text}; background:{surface}; border:1px solid {line}; }}
    QScrollBar:vertical {{ background:{bg}; width:12px; margin:0; }}
    QScrollBar::handle:vertical {{ background:#2E333B; border-radius:6px; min-height:24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height:0; }}
    QCheckBox, QRadioButton {{ color:{text}; }}
    QGroupBox {{ border:1px solid {line}; border-radius:8px; margin-top:8px; }}
    QGroupBox::title {{ subcontrol-origin:margin; left:10px; color:{muted}; }}
    """

    _LIGHT_QSS = """
    QFrame#Sidebar { background:#EEF1F5; border-right:1px solid #DBE0E6; }
    QLabel#Brand { color:#0066CC; font-size:13pt; font-weight:bold; }
    QToolButton#Nav { color:#4A5568; border:none; padding:10px 12px; text-align:left;
        border-radius:8px; font-size:10.5pt; }
    QToolButton#Nav:hover { background:#E2E8F0; }
    QToolButton#Nav:checked { background:#FFFFFF; color:#0066CC; border-left:3px solid #0066CC; }
    QFrame#Card { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:12px; }
    QLabel#H1 { font-size:18pt; font-weight:bold; color:#1A202C; }
    QLabel#Muted { color:#718096; }
    QPushButton#Accent { background:#0066CC; color:#FFFFFF; border:none; font-weight:bold;
        border-radius:8px; padding:7px 14px; }
    QPushButton#Accent:hover { background:#0a72db; }
    """

    def apply_theme(dark: bool):
        """Apply the carbon (dark) or light theme to the whole application."""
        app = QtWidgets.QApplication.instance()
        if app is None:
            return
        app.setStyle("Fusion")
        if not dark:
            app.setPalette(app.style().standardPalette())
            app.setStyleSheet(_LIGHT_QSS)
            return
        c = QtGui.QColor
        p = QtGui.QPalette()
        p.setColor(QtGui.QPalette.Window, c(CARBON["bg"]))
        p.setColor(QtGui.QPalette.WindowText, c(CARBON["text"]))
        p.setColor(QtGui.QPalette.Base, c(CARBON["base"]))
        p.setColor(QtGui.QPalette.AlternateBase, c(CARBON["surface"]))
        p.setColor(QtGui.QPalette.Text, c(CARBON["text"]))
        p.setColor(QtGui.QPalette.Button, c(CARBON["surface"]))
        p.setColor(QtGui.QPalette.ButtonText, c(CARBON["text"]))
        p.setColor(QtGui.QPalette.Highlight, c(CARBON["accent"]))
        p.setColor(QtGui.QPalette.HighlightedText, c(CARBON["bg"]))
        p.setColor(QtGui.QPalette.ToolTipBase, c(CARBON["surface"]))
        p.setColor(QtGui.QPalette.ToolTipText, c(CARBON["text"]))
        p.setColor(QtGui.QPalette.PlaceholderText, c(CARBON["muted"]))
        p.setColor(QtGui.QPalette.Link, c(CARBON["accent2"]))
        for role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
            p.setColor(QtGui.QPalette.Disabled, role, c("#5A616B"))
        app.setPalette(p)
        app.setStyleSheet(_CARBON_QSS.format(**CARBON))

    class FlowLayout(QtWidgets.QLayout):
        """A layout that lays widgets out left-to-right and wraps to the next row
        when the window is too narrow — so button bars fit any window size."""

        def __init__(self, parent=None, spacing=6):
            super().__init__(parent)
            self._items = []
            self.setSpacing(spacing)
            self.setContentsMargins(0, 0, 0, 0)

        def addItem(self, item):
            self._items.append(item)

        def count(self):
            return len(self._items)

        def itemAt(self, i):
            return self._items[i] if 0 <= i < len(self._items) else None

        def takeAt(self, i):
            return self._items.pop(i) if 0 <= i < len(self._items) else None

        def expandingDirections(self):
            return QtCore.Qt.Orientation(0)

        def hasHeightForWidth(self):
            return True

        def heightForWidth(self, width):
            return self._do(QtCore.QRect(0, 0, width, 0), test=True)

        def setGeometry(self, rect):
            super().setGeometry(rect)
            self._do(rect, test=False)

        def sizeHint(self):
            return self.minimumSize()

        def minimumSize(self):
            size = QtCore.QSize()
            for it in self._items:
                size = size.expandedTo(it.minimumSize())
            return size

        def _do(self, rect, test):
            x, y, line_h = rect.x(), rect.y(), 0
            sp = self.spacing()
            for it in self._items:
                w = it.sizeHint().width()
                h = it.sizeHint().height()
                if x + w > rect.right() and line_h > 0:
                    x = rect.x()
                    y += line_h + sp
                    line_h = 0
                if not test:
                    it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), it.sizeHint()))
                x += w + sp
                line_h = max(line_h, h)
            return y + line_h - rect.y()

    # --------------------------------------------------------------------- #
    # Shared plotting widget
    # --------------------------------------------------------------------- #
    class PlotPanel(QtWidgets.QWidget):
        """A pyqtgraph plot with normalization and a value-reading crosshair."""

        cursorMoved = QtCore.Signal(float)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.normalize = True
            self._unit_system = units.AS_LOGGED
            # name -> dict(curve, t[list], v[list], unit, color, visible, vmin, vmax)
            self.channels: Dict[str, dict] = {}
            self._color_idx = 0

            self.plot = pg.PlotWidget()
            self.pi = self.plot.getPlotItem()
            self.pi.showGrid(x=True, y=True, alpha=0.3)
            self.pi.setLabel("bottom", "Time", units="s")
            self.pi.addLegend(offset=(10, 10))

            self.vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#888", width=1))
            self.pi.addItem(self.vline, ignoreBounds=True)
            # Second (measurement) cursor — placed by clicking in measure mode.
            self.vline_b = pg.InfiniteLine(angle=90, movable=False,
                                           pen=pg.mkPen("#00C9A7", width=1, style=QtCore.Qt.DashLine))
            self.vline_b.hide()
            self.pi.addItem(self.vline_b, ignoreBounds=True)
            self.measure = False
            self._cursor_b = None

            self.readout = QtWidgets.QLabel("Move cursor over the plot…")
            self.readout.setStyleSheet(
                "font-family: Consolas, monospace; font-size: 12px; padding: 6px;"
            )
            self.readout.setAlignment(QtCore.Qt.AlignTop)
            self.readout.setMinimumWidth(230)
            self.readout.setWordWrap(True)

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.plot, 1)
            layout.addWidget(self.readout, 0)

            self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
            self.plot.scene().sigMouseClicked.connect(self._mouse_clicked)

        # -- channel management -------------------------------------------- #
        def clear(self):
            for entry in self.channels.values():
                self.pi.removeItem(entry["curve"])
            self.channels.clear()
            self._color_idx = 0
            self.readout.setText("Move cursor over the plot…")

        def _next_color(self) -> str:
            c = _PALETTE[self._color_idx % len(_PALETTE)]
            self._color_idx += 1
            return c

        def add_channel(self, name: str, times: List[float], values: List[float], unit: str,
                        visible: bool = True, color: Optional[str] = None) -> str:
            if name in self.channels:
                self.pi.removeItem(self.channels[name]["curve"])
            color = color or self._next_color()
            curve = self.pi.plot([], [], pen=pg.mkPen(color, width=2), name=name)
            entry = {
                "curve": curve,
                "t": list(times),
                "v": list(values),
                "unit": unit,
                "color": color,
                "visible": visible,
                "vmin": min(values) if values else 0.0,
                "vmax": max(values) if values else 1.0,
            }
            self.channels[name] = entry
            self._replot(name)
            return color

        def set_visible(self, name: str, visible: bool):
            if name in self.channels:
                self.channels[name]["visible"] = visible
                self._replot(name)

        def set_normalize(self, on: bool):
            self.normalize = on
            self.pi.setLabel("left", "Normalized (0–1)" if on else "Value")
            for name in self.channels:
                self._replot(name)

        def set_theme(self, dark: bool):
            bg = "#15151f" if dark else "w"
            fg = "#e6edf6" if dark else "k"
            self.plot.setBackground(bg)
            for axis in ("left", "bottom"):
                ax = self.pi.getAxis(axis)
                ax.setPen(fg)
                ax.setTextPen(fg)
            self.readout.setStyleSheet(
                f"font-family: Consolas, monospace; font-size: 12px; padding: 6px; color: {fg};"
            )

        def set_measure(self, on: bool):
            """Toggle two-cursor measurement mode (click sets the second cursor)."""
            self.measure = on
            if not on:
                self._cursor_b = None
                self.vline_b.hide()

        def _conv(self, value, unit):
            return units.convert(value, unit, self._unit_system)[0]

        def _scaled(self, entry: dict) -> List[float]:
            unit = entry["unit"]
            v = [self._conv(x, unit) for x in entry["v"]]
            if not self.normalize:
                return v
            lo = self._conv(entry["vmin"], unit)
            hi = self._conv(entry["vmax"], unit)
            span = (hi - lo) or 1.0
            return [(x - lo) / span for x in v]

        def set_unit_system(self, system: str):
            self._unit_system = system
            for name in self.channels:
                self._replot(name)

        def auto_fit(self):
            """Auto-range the view to fit all visible data."""
            self.pi.enableAutoRange()
            self.pi.autoRange()

        def _replot(self, name: str):
            entry = self.channels[name]
            if entry["visible"] and entry["t"]:
                entry["curve"].setData(entry["t"], self._scaled(entry))
            else:
                entry["curve"].setData([], [])

        # -- live append ---------------------------------------------------- #
        def append_sample(self, t: float, values: Dict[str, Optional[float]]):
            for name, val in values.items():
                if val is None or name not in self.channels:
                    continue
                entry = self.channels[name]
                entry["t"].append(t)
                entry["v"].append(val)
                entry["vmin"] = min(entry["vmin"], val)
                entry["vmax"] = max(entry["vmax"], val)
                self._replot(name)

        # -- crosshair ------------------------------------------------------ #
        def _mouse_moved(self, pos):
            if not self.pi.sceneBoundingRect().contains(pos):
                return
            x = self.pi.vb.mapSceneToView(pos).x()
            self.set_cursor(x)

        def _mouse_clicked(self, ev):
            if not self.measure:
                return
            pos = ev.scenePos()
            if not self.pi.sceneBoundingRect().contains(pos):
                return
            self._cursor_b = self.pi.vb.mapSceneToView(pos).x()
            self.vline_b.setPos(self._cursor_b)
            self.vline_b.show()

        def set_cursor(self, x: float):
            self.vline.setPos(x)
            lines = [f"<b>t = {x:.3f} s</b>"]
            measuring = self.measure and self._cursor_b is not None
            if measuring:
                lines[0] += (f" &nbsp; <span style='color:#00C9A7'>B = {self._cursor_b:.3f} s "
                             f"(Δt {x - self._cursor_b:+.3f} s)</span>")
            for name, entry in self.channels.items():
                if not entry["visible"] or not entry["t"]:
                    continue
                raw = self._value_at(entry, x)
                if raw is None:
                    continue
                val, dunit = units.convert(raw, entry["unit"], self._unit_system)
                unit = f" {dunit}" if dunit else ""
                line = (f"<span style='color:{entry['color']}'>&#9632;</span> "
                        f"{name}: <b>{val:g}</b>{unit}")
                if measuring:
                    raw_b = self._value_at(entry, self._cursor_b)
                    if raw_b is not None:
                        val_b = units.convert(raw_b, entry["unit"], self._unit_system)[0]
                        line += f" &nbsp;<span style='color:#00C9A7'>Δ {val - val_b:+g}</span>"
                lines.append(line)
            self.readout.setText("<br>".join(lines))
            self.cursorMoved.emit(x)

        @staticmethod
        def _value_at(entry: dict, x: float) -> Optional[float]:
            t = entry["t"]
            if not t:
                return None
            i = bisect.bisect_left(t, x)
            if i <= 0:
                i = 0
            elif i >= len(t):
                i = len(t) - 1
            elif abs(t[i - 1] - x) <= abs(t[i] - x):
                i = i - 1
            return entry["v"][i]

    # --------------------------------------------------------------------- #
    # Tab 1 — File Analyzer
    # --------------------------------------------------------------------- #
    class FileAnalyzerTab(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.mlog: Optional[parse.MeasuringLog] = None
            self.scan = None
            self.rules: List[dict] = []
            self._build()

        def _build(self):
            outer = QtWidgets.QVBoxLayout(self)

            # toolbar (wraps on narrow windows)
            bar = FlowLayout()
            self.btn_open = QtWidgets.QPushButton("Open Measuring CSV…")
            self.btn_scan = QtWidgets.QPushButton("Open Auto-Scan…")
            self.chk_norm = QtWidgets.QCheckBox("Normalize")
            self.chk_norm.setChecked(True)
            self.chk_measure = QtWidgets.QCheckBox("Measure")
            self.chk_measure.setToolTip("Two-cursor mode: click to drop a second cursor and read Δ")
            self.btn_fit = QtWidgets.QPushButton("⤢ Fit")
            self.btn_fit.setToolTip("Auto-fit the graph to all visible data")
            self.btn_export = QtWidgets.QPushButton("Export View…")
            self.view_combo = QtWidgets.QComboBox()
            self.view_combo.addItems(["📈 Graph", "▦ Data"])
            self.view_combo.setToolTip("Switch between the line graph and the raw data table")
            self.btn_diagnose = QtWidgets.QPushButton("🔍 Diagnose")
            self.btn_diagnose.setToolTip("Analyze the loaded log and/or Auto-Scan for likely faults")
            self.btn_perf = QtWidgets.QPushButton("📈 Performance")
            self.btn_perf.setToolTip("Acceleration runs, pulls and an estimated power figure")
            self.btn_compare = QtWidgets.QPushButton("⇄ Compare…")
            self.btn_compare.setToolTip("Open a second log and compare it (before/after)")
            self.lbl_info = QtWidgets.QLabel("No file loaded.")
            for w in (self.btn_open, self.btn_scan, self.btn_diagnose, self.btn_perf,
                      self.btn_compare, self.chk_norm, self.chk_measure, self.btn_fit,
                      self.btn_export, self.view_combo):
                bar.addWidget(w)
            bar.addWidget(self.lbl_info)
            outer.addLayout(bar)

            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            outer.addWidget(split, 1)

            # left: channels + events + rules
            left = QtWidgets.QWidget()
            lv = QtWidgets.QVBoxLayout(left)
            lv.addWidget(QtWidgets.QLabel("<b>Channels</b>"))
            self.chan_list = QtWidgets.QListWidget()
            lv.addWidget(self.chan_list, 2)

            lv.addWidget(QtWidgets.QLabel("<b>Events</b>"))
            self.event_list = QtWidgets.QListWidget()
            lv.addWidget(self.event_list, 2)

            ev_bar = QtWidgets.QHBoxLayout()
            self.btn_find = QtWidgets.QPushButton("Find Events")
            self.btn_apply_rules = QtWidgets.QPushButton("Apply Rules")
            ev_bar.addWidget(self.btn_find)
            ev_bar.addWidget(self.btn_apply_rules)
            lv.addLayout(ev_bar)

            rule_box = QtWidgets.QGroupBox("Add threshold rule")
            rb = QtWidgets.QHBoxLayout(rule_box)
            self.rule_chan = QtWidgets.QLineEdit()
            self.rule_chan.setPlaceholderText("channel")
            self.rule_op = QtWidgets.QComboBox()
            self.rule_op.addItems([">", "<", ">=", "<=", "=="])
            self.rule_val = QtWidgets.QLineEdit()
            self.rule_val.setPlaceholderText("value")
            self.rule_val.setMaximumWidth(80)
            self.btn_add_rule = QtWidgets.QPushButton("Add")
            self.btn_clear_rules = QtWidgets.QPushButton("Clear")
            for w in (self.rule_chan, self.rule_op, self.rule_val, self.btn_add_rule, self.btn_clear_rules):
                rb.addWidget(w)
            lv.addWidget(rule_box)
            self.rules_label = QtWidgets.QLabel("No rules.")
            lv.addWidget(self.rules_label)

            split.addWidget(left)

            # center: graph / data table, switchable
            self.plot = PlotPanel()
            self.data_table = QtWidgets.QTableWidget()
            self.data_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
            self.center_stack = QtWidgets.QStackedWidget()
            self.center_stack.addWidget(self.plot)       # index 0 — graph
            self.center_stack.addWidget(self.data_table)  # index 1 — data
            split.addWidget(self.center_stack)

            # right: autoscan
            right = QtWidgets.QWidget()
            rv = QtWidgets.QVBoxLayout(right)
            rv.addWidget(QtWidgets.QLabel("<b>Auto-Scan</b>"))
            self.scan_info = QtWidgets.QLabel("No scan loaded.")
            self.scan_info.setWordWrap(True)
            rv.addWidget(self.scan_info)
            self.scan_tree = QtWidgets.QTreeWidget()
            self.scan_tree.setHeaderLabels(["Module / Fault", "Detail"])
            rv.addWidget(self.scan_tree, 1)
            split.addWidget(right)

            split.setSizes([280, 700, 320])

            # signals
            self.btn_open.clicked.connect(self.open_csv_dialog)
            self.btn_scan.clicked.connect(self.open_scan_dialog)
            self.chk_norm.toggled.connect(self.plot.set_normalize)
            self.chk_measure.toggled.connect(self.plot.set_measure)
            self.btn_fit.clicked.connect(self.plot.auto_fit)
            self.btn_export.clicked.connect(self.export_view)
            self.view_combo.currentIndexChanged.connect(self._set_view)
            self.btn_diagnose.clicked.connect(self.run_diagnosis)
            self.btn_perf.clicked.connect(self.run_performance)
            self.btn_compare.clicked.connect(self.open_compare)
            self.chan_list.itemChanged.connect(self._chan_toggled)
            self.btn_find.clicked.connect(lambda: self.run_events(use_rules=False))
            self.btn_apply_rules.clicked.connect(lambda: self.run_events(use_rules=True))
            self.btn_add_rule.clicked.connect(self._add_rule)
            self.btn_clear_rules.clicked.connect(self._clear_rules)
            self.event_list.itemClicked.connect(self._event_clicked)

        # -- graph / data view ---------------------------------------------- #
        def _set_view(self, idx):
            self.center_stack.setCurrentIndex(idx)
            if idx == 1:
                self._populate_data_table()

        def _refresh_data_view(self):
            if self.center_stack.currentIndex() == 1:
                self._populate_data_table()

        def _populate_data_table(self):
            log = self.mlog
            self.data_table.clearContents()
            if log is None:
                self.data_table.setRowCount(0)
                self.data_table.setColumnCount(1)
                self.data_table.setHorizontalHeaderLabels(["No file loaded"])
                return
            names = [c.name for c in log.channels]
            units_by = {c.name: c.unit for c in log.channels}
            series = log.raw_series
            master = []
            for n in names:
                t = series.get(n, {}).get("time", [])
                if len(t) > len(master):
                    master = t
            cols = ["Time (s)"] + [f"{n} [{units_by.get(n)}]" if units_by.get(n) else n
                                   for n in names]
            self.data_table.setUpdatesEnabled(False)
            self.data_table.setColumnCount(len(cols))
            self.data_table.setHorizontalHeaderLabels(cols)
            self.data_table.setRowCount(len(master))
            Item = QtWidgets.QTableWidgetItem
            for i, tv in enumerate(master):
                self.data_table.setItem(i, 0, Item(f"{tv:g}"))
                for c, n in enumerate(names, start=1):
                    vals = series.get(n, {}).get("value", [])
                    v = vals[i] if i < len(vals) else None
                    self.data_table.setItem(i, c, Item("" if v is None else f"{v:g}"))
            self.data_table.setUpdatesEnabled(True)
            self.data_table.resizeColumnsToContents()

        # -- loading -------------------------------------------------------- #
        def open_csv_dialog(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open VCDS Measuring CSV", _vcds_import_dir(), "CSV files (*.csv *.CSV);;All files (*)"
            )
            if path:
                self.load_csv(path)

        def load_csv(self, path: str):
            try:
                # Auto-detect: VCDS layout first, then generic (Torque/OBD Fusion/FORScan).
                self.mlog = open_measuring_file(path)
                compute.add_computed_channels(self.mlog)  # adds Fuel Trim Total, AFR, …
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Parse error", str(exc))
                return
            self.plot.clear()
            self.chan_list.blockSignals(True)
            self.chan_list.clear()
            for ch in self.mlog.channels:
                rs = self.mlog.raw_series[ch.name]
                color = self.plot.add_channel(ch.name, rs["time"], rs["value"], ch.unit)
                computed = ch.group == "(computed)"
                label = f"{ch.name}  [{ch.unit}]" + ("  ✚" if computed else "")
                item = QtWidgets.QListWidgetItem(label)
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                # Computed channels start hidden to avoid cluttering the plot.
                item.setCheckState(QtCore.Qt.Unchecked if computed else QtCore.Qt.Checked)
                self.plot.set_visible(ch.name, not computed)
                item.setData(QtCore.Qt.UserRole, ch.name)
                item.setForeground(QtGui.QColor(color))
                if computed:
                    item.setToolTip("Computed (derived) channel")
                self.chan_list.addItem(item)
            self.chan_list.blockSignals(False)
            self.plot.pi.enableAutoRange()
            self.lbl_info.setText(
                f"{os.path.basename(path)} — {self.mlog.format_guess}, "
                f"delim={self.mlog.delimiter}, {self.mlog.sample_count} samples, "
                f"{len(self.mlog.channels)} channels"
            )
            self.event_list.clear()
            self._refresh_data_view()

        def open_scan_dialog(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open VCDS Auto-Scan", _vcds_import_dir(), "Text files (*.txt *.TXT);;All files (*)"
            )
            if path:
                self.load_scan(path)

        def load_scan(self, path: str):
            try:
                scan = parse.parse_autoscan(path)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Parse error", str(exc))
                return
            self.scan = scan
            self.scan_info.setText(
                f"<b>VIN:</b> {scan.vin or '?'}<br><b>Mileage:</b> {scan.mileage or '?'}"
                f"<br><b>Total faults:</b> {scan.total_faults}"
            )
            self.scan_tree.clear()
            for m in scan.modules:
                node = QtWidgets.QTreeWidgetItem([f"Addr {m.address}: {m.name}", f"{len(m.faults)} fault(s)"])
                if m.faults:
                    node.setForeground(0, QtGui.QColor("#E53E3E"))
                for f in m.faults:
                    child = QtWidgets.QTreeWidgetItem([f"{f.code} — {f.description}", f.status_detail or ""])
                    # Enrich with likely causes from the knowledge base.
                    k = knowledge.lookup(f.code)
                    if not k.known and f.status_detail:
                        import re as _re

                        mm = _re.search(r"\b([PUBC][0-9]{4})\b", f.status_detail)
                        if mm:
                            k = knowledge.lookup(mm.group(1))
                    if k.causes:
                        child.setToolTip(0, "Likely causes: " + "; ".join(k.causes))
                        cause_node = QtWidgets.QTreeWidgetItem(["Likely: " + "; ".join(k.causes[:3]), ""])
                        cause_node.setForeground(0, QtGui.QColor("#718096"))
                        child.addChild(cause_node)
                    node.addChild(child)
                self.scan_tree.addTopLevelItem(node)
                node.setExpanded(True)

        def run_diagnosis(self):
            if self.mlog is None and self.scan is None:
                QtWidgets.QMessageBox.information(
                    self, "Diagnose", "Open a measuring CSV and/or an Auto-Scan first."
                )
                return
            prof = QtCore.QSettings("DeltaModTech", "VCDS Toolkit").value(
                "ui/profile", profiles.DEFAULT_PROFILE, type=str)
            report = run_diagnose(scan=self.scan, log=self.mlog, profile=prof)
            plot_png = None
            if self.mlog is not None:
                try:
                    plot_png = _grab_png(self.plot.plot)
                except Exception:  # noqa: BLE001
                    plot_png = None
            DiagnosisDialog(report, self.mlog, self.scan, plot_png, self).exec()

        def run_performance(self):
            if self.mlog is None:
                QtWidgets.QMessageBox.information(self, "Performance", "Open a measuring CSV first.")
                return
            PerformanceDialog(self.mlog, self).exec()

        def open_compare(self):
            if self.mlog is None:
                QtWidgets.QMessageBox.information(
                    self, "Compare", "Open a measuring CSV first — that becomes log A.")
                return
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open second CSV to compare (log B)", DEFAULT_LOGS_DIR,
                "CSV files (*.csv *.CSV);;All files (*)")
            if not path:
                return
            try:
                other = parse.parse_measuring_log(path)
                compute.add_computed_channels(other)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Parse error", str(exc))
                return
            comparison = compare_mod.compare_logs(
                self.mlog, other,
                a_name=os.path.basename(self.mlog.file), b_name=os.path.basename(path))
            CompareDialog(comparison, self).exec()

        # -- channel toggling ---------------------------------------------- #
        def _chan_toggled(self, item: "QtWidgets.QListWidgetItem"):
            name = item.data(QtCore.Qt.UserRole)
            self.plot.set_visible(name, item.checkState() == QtCore.Qt.Checked)

        # -- events --------------------------------------------------------- #
        def _add_rule(self):
            chan = self.rule_chan.text().strip()
            try:
                val = float(self.rule_val.text().strip())
            except ValueError:
                QtWidgets.QMessageBox.warning(self, "Bad rule", "Value must be numeric.")
                return
            if not chan:
                QtWidgets.QMessageBox.warning(self, "Bad rule", "Channel is required.")
                return
            self.rules.append({"channel": chan, "op": self.rule_op.currentText(), "value": val})
            self._refresh_rules_label()

        def _clear_rules(self):
            self.rules = []
            self._refresh_rules_label()

        def _refresh_rules_label(self):
            if not self.rules:
                self.rules_label.setText("No rules.")
            else:
                self.rules_label.setText(
                    "; ".join(f"{r['channel']} {r['op']} {r['value']:g}" for r in self.rules)
                )

        def run_events(self, use_rules: bool):
            if self.mlog is None:
                return
            rules = self.rules if (use_rules and self.rules) else None
            events = parse.find_events(self.mlog, rules=rules)
            self.event_list.clear()
            for ev in events:
                t = f"{ev.time:.2f}s" if ev.time is not None else "—"
                item = QtWidgets.QListWidgetItem(f"[{t}] {ev.kind}: {ev.message}")
                item.setData(QtCore.Qt.UserRole, ev.time)
                self.event_list.addItem(item)
            if not events:
                self.event_list.addItem("(no events)")

        def _event_clicked(self, item: "QtWidgets.QListWidgetItem"):
            t = item.data(QtCore.Qt.UserRole)
            if t is not None:
                self.plot.set_cursor(float(t))

        # -- export --------------------------------------------------------- #
        def export_view(self):
            if self.mlog is None:
                return
            xmin, xmax = self.plot.pi.vb.viewRange()[0]
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export clipped CSV", DEFAULT_LOGS_DIR, "CSV files (*.csv *.CSV)"
            )
            if not path:
                return
            n = _export_clip(self.mlog, path, xmin, xmax)
            QtWidgets.QMessageBox.information(
                self, "Exported", f"Wrote {n} samples in t=[{xmin:.2f}, {xmax:.2f}]s to\n{path}"
            )

    # --------------------------------------------------------------------- #
    # Live capture worker (runs the blocking logger off the UI thread)
    # --------------------------------------------------------------------- #
    class LiveWorker(QtCore.QObject):
        sample = QtCore.Signal(float, dict, str)
        finished = QtCore.Signal(object)
        failed = QtCore.Signal(str)

        def __init__(self, logger: "live.LiveLogger", duration_s: float, trigger, session_name=None):
            super().__init__()
            self.logger = logger
            self.duration_s = duration_s
            self.trigger = trigger
            self.session_name = session_name

        @QtCore.Slot()
        def run(self):
            try:
                result = self.logger.run(
                    self.duration_s,
                    trigger=self.trigger,
                    session_name=self.session_name,
                    on_sample=lambda t, vals, marker: self.sample.emit(t, dict(vals), marker),
                )
                self.finished.emit(result)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

    class IdentifyWorker(QtCore.QObject):
        """Reads vehicle identification (VIN, cal-IDs, protocol…) off the thread."""

        done = QtCore.Signal(dict)

        def __init__(self, conn):
            super().__init__()
            self.conn = conn

        @QtCore.Slot()
        def run(self):
            try:
                info = self.conn.identify() if hasattr(self.conn, "identify") else {}
            except Exception as exc:  # noqa: BLE001
                info = {"error": str(exc)}
            self.done.emit(info or {})

    class LiveDataPoller(QtCore.QObject):
        """Free-running poller: snapshots the adapter on an interval (own thread)."""

        values = QtCore.Signal(dict)
        failed = QtCore.Signal(str)

        def __init__(self, conn, channels, interval_ms=0):
            super().__init__()
            self.conn = conn
            self.channels = channels
            self.interval_ms = interval_ms  # 0 = as fast as the adapter allows
            self._stop = False

        def stop(self):
            self._stop = True

        @QtCore.Slot()
        def run(self):
            while not self._stop:
                try:
                    snap = live.snapshot(self.conn, self.channels)
                except Exception as exc:  # noqa: BLE001
                    self.failed.emit(str(exc))
                    return
                if self._stop:
                    return
                self.values.emit(dict(snap))
                if self.interval_ms:
                    QtCore.QThread.msleep(int(self.interval_ms))

    class LiveDataWindow(QtWidgets.QWidget):
        """Always-on Live Data table: current value, unit, min/max and trend per PID."""

        COLS = ["PID", "Value", "Unit", "Min", "Max", "Trend"]

        def __init__(self, channels, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Live Data")
            self.resize(560, 640)
            self._channels = list(channels)
            self._rows = {}      # channel name -> row index
            self._stats = {}     # name -> [min, max, last]
            self._thread = None
            self._poller = None
            self._poll_conn = None
            self._interval_ms = 0
            self._rate_ema = None
            self._last_mono = None

            v = QtWidgets.QVBoxLayout(self)
            self.status = QtWidgets.QLabel("Streaming…")
            self.status.setObjectName("Muted")
            v.addWidget(self.status)
            self.table = QtWidgets.QTableWidget(len(self._channels), len(self.COLS))
            self.table.setHorizontalHeaderLabels(self.COLS)
            self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            for r, ch in enumerate(self._channels):
                self._rows[ch.name] = r
                self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(ch.name))
                self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(ch.unit or ""))
                for c in (1, 3, 4, 5):
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem("—"))
            self.table.resizeColumnsToContents()
            v.addWidget(self.table, 1)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Refresh:"))
            self.rate_combo = QtWidgets.QComboBox()
            self._rate_options = [("As fast as possible", 0), ("10 Hz", 100), ("5 Hz", 200),
                                  ("2 Hz", 500), ("1 Hz", 1000)]
            for label, _ms in self._rate_options:
                self.rate_combo.addItem(label)
            self.rate_combo.currentIndexChanged.connect(self._rate_changed)
            row.addWidget(self.rate_combo)
            row.addStretch(1)
            btn_reset = QtWidgets.QPushButton("Reset min/max")
            btn_reset.clicked.connect(self._reset_stats)
            row.addWidget(btn_reset)
            v.addLayout(row)

        def _rate_changed(self, idx):
            self._interval_ms = self._rate_options[idx][1]
            if self._poller is not None and self._poll_conn is not None:
                self.start_poll(self._poll_conn)  # restart at the new interval

        def _reset_stats(self):
            self._stats.clear()
            for r in range(self.table.rowCount()):
                for c in (3, 4, 5):
                    self.table.item(r, c).setText("—")

        @QtCore.Slot(dict)
        def update_values(self, values):
            now = time.perf_counter()
            if self._last_mono is not None:
                dt = now - self._last_mono
                if dt > 0:
                    inst = 1.0 / dt
                    self._rate_ema = inst if self._rate_ema is None \
                        else 0.7 * self._rate_ema + 0.3 * inst
                    self.status.setText(
                        f"Streaming live · ~{self._rate_ema:.1f} updates/s · "
                        f"{len(self._channels)} PIDs")
            self._last_mono = now
            for name, val in values.items():
                r = self._rows.get(name)
                if r is None or val is None:
                    continue
                st = self._stats.get(name)
                if st is None:
                    st = [val, val, val]
                    self._stats[name] = st
                arrow = "▲" if val > st[2] + 1e-9 else ("▼" if val < st[2] - 1e-9 else "•")
                st[0] = min(st[0], val)
                st[1] = max(st[1], val)
                st[2] = val
                self.table.item(r, 1).setText(f"{val:g}")
                self.table.item(r, 3).setText(f"{st[0]:g}")
                self.table.item(r, 4).setText(f"{st[1]:g}")
                self.table.item(r, 5).setText(arrow)

        def start_poll(self, conn):
            self.stop_poll()
            self._poll_conn = conn
            self._thread = QtCore.QThread()
            self._poller = LiveDataPoller(conn, self._channels, self._interval_ms)
            self._poller.moveToThread(self._thread)
            self._thread.started.connect(self._poller.run)
            self._poller.values.connect(self.update_values)
            self._poller.failed.connect(lambda m: self.status.setText(f"Stopped: {m}"))
            self._thread.start()
            self.status.setText("Streaming live…")

        def stop_poll(self):
            if self._poller is not None:
                self._poller.stop()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(1500)
            self._poller = self._thread = None

        def closeEvent(self, event):
            self.stop_poll()
            super().closeEvent(event)

    # --------------------------------------------------------------------- #
    # Tab 2 — Live (OBD-II)
    # --------------------------------------------------------------------- #
    class LiveTab(QtWidgets.QWidget):
        def __init__(self, main_window: "MainWindow", parent=None):
            super().__init__(parent)
            self.main = main_window
            self.conn = None
            self.channels: List[live.LiveChannel] = []
            self.thread: Optional[QtCore.QThread] = None
            self.worker: Optional[LiveWorker] = None
            self.logger: Optional[live.LiveLogger] = None
            self.trigger_rules: List[dict] = []
            self._gauges = None
            self._livedata = None
            self.vehicle_header: List[str] = []
            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            self._presets: dict = {}
            self._build()
            self._load_presets()

        def _build(self):
            outer = QtWidgets.QVBoxLayout(self)

            # connection bar
            conn_box = QtWidgets.QGroupBox("Adapter")
            cb = FlowLayout(conn_box)
            self.port_combo = QtWidgets.QComboBox()
            self.port_combo.setEditable(True)
            self.port_combo.setMinimumWidth(200)
            self.port_combo.setToolTip(
                "USB/Bluetooth adapters appear as a COM port (pair Bluetooth first).\n"
                "Wi-Fi adapters use socket://HOST:PORT — use the Wi-Fi button.")
            self.btn_refresh = QtWidgets.QPushButton("Scan Ports")
            self.btn_wifi = QtWidgets.QPushButton("📶 Wi-Fi…")
            self.btn_wifi.setToolTip("Connect to a Wi-Fi ELM327 adapter (e.g. 192.168.0.10:35000)")
            self.baud_combo = QtWidgets.QComboBox()
            self.baud_combo.addItems(["Auto", "38400", "9600", "115200"])
            self.chk_async = QtWidgets.QCheckBox("⚡ Smooth")
            self.chk_async.setToolTip("Async mode: the adapter polls in the background and reads "
                                      "come from a live cache — much smoother high-rate streaming")
            self.btn_connect = QtWidgets.QPushButton("Connect")
            self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
            self.btn_disconnect.setEnabled(False)
            self.conn_status = QtWidgets.QLabel("Not connected.")
            for w in (QtWidgets.QLabel("Port:"), self.port_combo, self.btn_refresh, self.btn_wifi,
                      QtWidgets.QLabel("Baud:"), self.baud_combo, self.chk_async,
                      self.btn_connect, self.btn_disconnect):
                cb.addWidget(w)
            cb.addWidget(self.conn_status)
            outer.addWidget(conn_box)

            # Live alert HUD — flashes red when a threshold rule is breached.
            self.alert_banner = QtWidgets.QLabel("")
            self.alert_banner.setAlignment(QtCore.Qt.AlignCenter)
            self.alert_banner.setStyleSheet(
                "background:#E10600; color:white; font-weight:bold; font-size:12pt;"
                " border-radius:6px; padding:6px;")
            self.alert_banner.hide()
            outer.addWidget(self.alert_banner)
            self._alert_active = set()
            self._alert_flash = False
            self._alert_timer = QtCore.QTimer(self)
            self._alert_timer.setInterval(450)
            self._alert_timer.timeout.connect(self._flash_alert)

            split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
            outer.addWidget(split, 1)

            left = QtWidgets.QWidget()
            lv = QtWidgets.QVBoxLayout(left)
            lv.setContentsMargins(0, 0, 0, 0)
            left_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
            lv.addWidget(left_split)

            # --- PID picker: searchable + resizable -------------------------- #
            pid_box = QtWidgets.QWidget()
            pv = QtWidgets.QVBoxLayout(pid_box)
            pv.setContentsMargins(0, 0, 0, 0)
            pv.addWidget(QtWidgets.QLabel("<b>Supported PIDs</b>"))
            self.pid_search = QtWidgets.QLineEdit()
            self.pid_search.setPlaceholderText("Search PIDs…  (e.g. boost, temp, fuel, O2)")
            self.pid_search.setClearButtonEnabled(True)
            self.pid_search.textChanged.connect(self._filter_pids)
            pv.addWidget(self.pid_search)
            self.pid_list = QtWidgets.QListWidget()
            self.pid_list.setMinimumHeight(240)
            self.pid_list.itemChanged.connect(self._update_pid_count)
            pv.addWidget(self.pid_list, 1)
            pid_btns = QtWidgets.QHBoxLayout()
            self.btn_pid_all = QtWidgets.QPushButton("Select shown")
            self.btn_pid_none = QtWidgets.QPushButton("Clear all")
            self.btn_pid_all.clicked.connect(lambda: self._set_pids_checked(True, only_visible=True))
            self.btn_pid_none.clicked.connect(lambda: self._set_pids_checked(False, only_visible=False))
            self.lbl_pid_count = QtWidgets.QLabel("")
            pid_btns.addWidget(self.btn_pid_all)
            pid_btns.addWidget(self.btn_pid_none)
            pid_btns.addWidget(self.lbl_pid_count, 1)
            pv.addLayout(pid_btns)

            preset_row = QtWidgets.QHBoxLayout()
            preset_row.addWidget(QtWidgets.QLabel("Preset:"))
            self.preset_combo = QtWidgets.QComboBox()
            self.preset_combo.setMinimumWidth(120)
            self.btn_preset_save = QtWidgets.QPushButton("Save…")
            self.btn_preset_del = QtWidgets.QPushButton("Delete")
            preset_row.addWidget(self.preset_combo, 1)
            preset_row.addWidget(self.btn_preset_save)
            preset_row.addWidget(self.btn_preset_del)
            pv.addLayout(preset_row)
            self.preset_combo.currentTextChanged.connect(self._apply_preset)
            self.btn_preset_save.clicked.connect(self._save_preset)
            self.btn_preset_del.clicked.connect(self._delete_preset)

            left_split.addWidget(pid_box)

            trig_box = QtWidgets.QGroupBox("Event-capture trigger")
            tv = QtWidgets.QVBoxLayout(trig_box)
            self.chk_dtc = QtWidgets.QCheckBox("Trigger on any new DTC")
            tv.addWidget(self.chk_dtc)
            trow = QtWidgets.QHBoxLayout()
            self.trig_chan = QtWidgets.QLineEdit()
            self.trig_chan.setPlaceholderText("channel")
            self.trig_op = QtWidgets.QComboBox()
            self.trig_op.addItems([">", "<", ">=", "<=", "=="])
            self.trig_val = QtWidgets.QLineEdit()
            self.trig_val.setPlaceholderText("value")
            self.trig_val.setMaximumWidth(70)
            self.btn_add_trig = QtWidgets.QPushButton("Add")
            for w in (self.trig_chan, self.trig_op, self.trig_val, self.btn_add_trig):
                trow.addWidget(w)
            tv.addLayout(trow)
            self.trig_label = QtWidgets.QLabel("No threshold rules.")
            tv.addWidget(self.trig_label)
            left_split.addWidget(trig_box)

            dtc_box = QtWidgets.QGroupBox("Stored DTCs")
            dv = QtWidgets.QVBoxLayout(dtc_box)
            self.dtc_tree = QtWidgets.QTreeWidget()
            self.dtc_tree.setHeaderLabels(["Stored DTC / likely cause", "Severity"])
            self.dtc_tree.setColumnWidth(0, 250)
            dv.addWidget(self.dtc_tree)
            dbar = FlowLayout()
            self.btn_read_dtc = QtWidgets.QPushButton("Read DTCs")
            self.btn_clear_dtc = QtWidgets.QPushButton("Clear DTCs…")
            self.btn_vehinfo = QtWidgets.QPushButton("ⓘ Vehicle Info")
            self.btn_vehinfo.setToolTip("VIN, calibration IDs, emissions readiness, permanent DTCs")
            self.btn_mode06 = QtWidgets.QPushButton("Mode 06")
            self.btn_mode06.setToolTip("On-board monitoring test results (catalyst, O2, EVAP…)")
            dbar.addWidget(self.btn_read_dtc)
            dbar.addWidget(self.btn_clear_dtc)
            dbar.addWidget(self.btn_vehinfo)
            dbar.addWidget(self.btn_mode06)
            dv.addLayout(dbar)
            left_split.addWidget(dtc_box)

            cap_box = QtWidgets.QGroupBox("Captured events (double-click to analyze)")
            cv = QtWidgets.QVBoxLayout(cap_box)
            self.capture_list = QtWidgets.QListWidget()
            cv.addWidget(self.capture_list)
            left_split.addWidget(cap_box)
            # Give the PID list the lion's share; the rest stays compact.
            left_split.setSizes([460, 130, 170, 120])

            split.addWidget(left)
            self.plot = PlotPanel()
            split.addWidget(self.plot)
            split.setSizes([400, 720])

            # logging controls
            run_bar = FlowLayout()
            run_bar.addWidget(QtWidgets.QLabel("Duration (s):"))
            self.dur_spin = QtWidgets.QSpinBox()
            self.dur_spin.setRange(1, live.MAX_SESSION_SECONDS)
            self.dur_spin.setValue(60)
            run_bar.addWidget(self.dur_spin)
            run_bar.addWidget(QtWidgets.QLabel("Rate (Hz):"))
            self.rate_spin = QtWidgets.QDoubleSpinBox()
            self.rate_spin.setRange(0.5, 20.0)
            self.rate_spin.setValue(5.0)
            run_bar.addWidget(self.rate_spin)
            run_bar.addWidget(QtWidgets.QLabel("Name:"))
            self.name_edit = QtWidgets.QLineEdit()
            self.name_edit.setPlaceholderText("session name (optional)")
            self.name_edit.setMaximumWidth(180)
            run_bar.addWidget(self.name_edit)
            self.btn_gauges = QtWidgets.QPushButton("📊 Gauges")
            self.btn_gauges.setToolTip("Open a live gauge dashboard for the selected PIDs")
            run_bar.addWidget(self.btn_gauges)
            self.btn_livedata = QtWidgets.QPushButton("📋 Live Data")
            self.btn_livedata.setToolTip("Open an always-on live data table for the selected PIDs")
            run_bar.addWidget(self.btn_livedata)
            self.chk_alert = QtWidgets.QCheckBox("🔔 Alerts")
            self.chk_alert.setChecked(True)
            self.chk_alert.setToolTip("Flash and beep when a threshold rule is breached live")
            run_bar.addWidget(self.chk_alert)
            self.btn_start = QtWidgets.QPushButton("Start Logging")
            self.btn_stop = QtWidgets.QPushButton("Stop")
            self.btn_stop.setEnabled(False)
            run_bar.addWidget(self.btn_start)
            run_bar.addWidget(self.btn_stop)
            self.run_status = QtWidgets.QLabel("")
            run_bar.addWidget(self.run_status)
            outer.addLayout(run_bar)

            # signals
            self.btn_refresh.clicked.connect(self.scan_ports)
            self.btn_wifi.clicked.connect(self.setup_wifi)
            self.btn_connect.clicked.connect(self.connect_adapter)
            self.btn_disconnect.clicked.connect(self.disconnect_adapter)
            self.btn_add_trig.clicked.connect(self._add_trigger_rule)
            self.btn_read_dtc.clicked.connect(self.read_dtcs)
            self.btn_clear_dtc.clicked.connect(self.clear_dtcs)
            self.btn_vehinfo.clicked.connect(self.show_vehicle_info)
            self.btn_mode06.clicked.connect(self.show_onboard_tests)
            self.btn_start.clicked.connect(self.start_logging)
            self.btn_stop.clicked.connect(self.stop_logging)
            self.btn_gauges.clicked.connect(self.open_gauges)
            self.btn_livedata.clicked.connect(self.open_live_data)
            self.capture_list.itemDoubleClicked.connect(self._open_capture)

            self.scan_ports()
            self._set_connected(False)

        # -- ports / connection -------------------------------------------- #
        def scan_ports(self):
            self.port_combo.clear()
            self.port_combo.addItems(live.scan_ports())
            saved = self.settings.value("live/wifi", "", type=str)
            if saved:
                self.port_combo.addItem(f"socket://{saved}")

        def setup_wifi(self):
            saved = self.settings.value("live/wifi", "192.168.0.10:35000", type=str)
            text, ok = QtWidgets.QInputDialog.getText(
                self, "Wi-Fi adapter",
                "Wi-Fi ELM327 address (HOST:PORT):\n"
                "Common defaults: 192.168.0.10:35000 or 192.168.4.1:35000",
                QtWidgets.QLineEdit.Normal, saved)
            text = (text or "").strip()
            if not ok or not text:
                return
            text = text.replace("socket://", "")
            self.settings.setValue("live/wifi", text)
            self.port_combo.setEditText(f"socket://{text}")
            self.conn_status.setText("Wi-Fi address set — press Connect.")

        def _baud(self) -> Optional[int]:
            txt = self.baud_combo.currentText()
            return None if txt == "Auto" else int(txt)

        def connect_adapter(self):
            port = self.port_combo.currentText().strip() or None
            self.conn_status.setText("Connecting…")
            QtWidgets.QApplication.processEvents()
            try:
                self.conn = live.connect(port=port, baud=self._baud(),
                                         prefer_async=self.chk_async.isChecked())
            except Exception as exc:  # noqa: BLE001
                self.conn = None
                self.conn_status.setText(f"<span style='color:#E53E3E'>Failed: {exc}</span>")
                return
            supported = self.conn.supported()
            # Offer EVERY supported PID; default-check only the curated set so a
            # session doesn't log 100+ channels unless the user opts in.
            self.channels = live.build_channels(supported, include_all=True)
            self.pid_list.clear()
            for ch in self.channels:
                unit = f"  [{ch.unit}]" if ch.unit else ""
                item = QtWidgets.QListWidgetItem(f"{ch.name}{unit}")
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                is_default = (
                    ch.command_name in live.DEFAULT_CHANNELS_BY_CMD
                    or ch.name in live.DEFAULT_CHANNELS_BY_NAME
                )
                item.setCheckState(QtCore.Qt.Checked if is_default else QtCore.Qt.Unchecked)
                item.setData(QtCore.Qt.UserRole, ch.name)
                self.pid_list.addItem(item)
            self.pid_search.clear()
            self._filter_pids("")
            self._update_pid_count()
            mode = " · ⚡ smooth" if getattr(self.conn, "is_async", False) else ""
            self.conn_status.setText(
                f"<span style='color:#38A169'>Connected</span> — {self.conn.protocol()} "
                f"({len(supported)} PIDs){mode} · identifying vehicle…"
            )
            self._set_connected(True)
            self._start_identify()

        def _start_identify(self):
            if self.conn is None or not hasattr(self.conn, "identify"):
                return
            self._id_thread = QtCore.QThread()
            self._id_worker = IdentifyWorker(self.conn)
            self._id_worker.moveToThread(self._id_thread)
            self._id_thread.started.connect(self._id_worker.run)
            self._id_worker.done.connect(self._on_identified)
            self._id_worker.done.connect(self._id_thread.quit)
            self._id_thread.start()

        @QtCore.Slot(dict)
        def _on_identified(self, info):
            from vcds_core import vin as vinmod

            vin = info.get("vin")
            dec = vinmod.decode_vin(vin) if vin else None

            # Build the vehicle-info header embedded at the top of saved logs.
            lines = []
            if vin:
                lines.append(f"VIN: {vin}")
            if dec and dec.make:
                lines.append(f"Vehicle: {dec.year or ''} {dec.make}".strip())
            if info.get("ecu_name"):
                lines.append(f"ECU: {info['ecu_name']}")
            if info.get("fuel_type"):
                lines.append(f"Fuel type: {info['fuel_type']}")
            if info.get("calibration_ids"):
                lines.append("Calibration IDs: " + ", ".join(info["calibration_ids"]))
            if info.get("protocol"):
                lines.append(f"Protocol: {info['protocol']}")
            lines.append(f"Logged by OBD Toolkit {getattr(self.main, '_version', '')}".strip())
            self.vehicle_header = lines

            created = False
            if vin:
                path = os.path.join(DEFAULT_LOGS_DIR, "garage.json")
                vehicles = garage_mod.load_garage(path)
                created = garage_mod.find(vehicles, vin) is None
                garage_mod.add_or_update(vehicles, garage_mod.Vehicle(
                    vin=vin, make=(dec.make if dec else None),
                    year=(dec.year if dec else None),
                    brand_profile=(dec.brand_profile if dec else "generic"),
                    calibration_ids=info.get("calibration_ids") or [],
                    ecu_name=info.get("ecu_name"), fuel_type=info.get("fuel_type")))
                garage_mod.save_garage(path, vehicles)
                self.main.settings.setValue("garage/active_vin", vin)
                if dec and dec.brand_profile != "generic":
                    self.main._set_profile(dec.brand_profile)

            label = f"{dec.year} {dec.make}" if (dec and dec.make) else (f"VIN {vin}" if vin else "")
            base = self.conn_status.text().split(" · identifying")[0]
            if label:
                base += f" · <b>{label}</b>"
                if created:
                    self.run_status.setText(
                        f"🚗 Added {label} to your Garage — logs save under it automatically.")
            self.conn_status.setText(base)

        # -- PID picker helpers -------------------------------------------- #
        def _filter_pids(self, text: str):
            needle = text.strip().lower()
            for i in range(self.pid_list.count()):
                it = self.pid_list.item(i)
                it.setHidden(needle not in it.text().lower())
            self._update_pid_count()

        def _set_pids_checked(self, checked: bool, only_visible: bool = True):
            state = QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked
            for i in range(self.pid_list.count()):
                it = self.pid_list.item(i)
                if only_visible and it.isHidden():
                    continue
                it.setCheckState(state)

        def _update_pid_count(self, *_):
            total = self.pid_list.count()
            checked = sum(
                1 for i in range(total)
                if self.pid_list.item(i).checkState() == QtCore.Qt.Checked
            )
            self.lbl_pid_count.setText(f"{checked} selected / {total}")

        # -- PID presets ---------------------------------------------------- #
        def _load_presets(self):
            import json

            raw = self.settings.value("live/presets", "", type=str)
            try:
                self._presets = json.loads(raw) if raw else {}
            except ValueError:
                self._presets = {}
            self.preset_combo.blockSignals(True)
            self.preset_combo.clear()
            self.preset_combo.addItem("—")
            for name in sorted(self._presets):
                self.preset_combo.addItem(name)
            self.preset_combo.blockSignals(False)

        def _save_preset(self):
            import json

            chosen = [self.pid_list.item(i).data(QtCore.Qt.UserRole)
                      for i in range(self.pid_list.count())
                      if self.pid_list.item(i).checkState() == QtCore.Qt.Checked]
            if not chosen:
                QtWidgets.QMessageBox.information(self, "Preset", "Check some PIDs first.")
                return
            name, ok = QtWidgets.QInputDialog.getText(self, "Save preset", "Preset name:")
            if not ok or not name.strip():
                return
            self._presets[name.strip()] = chosen
            self.settings.setValue("live/presets", json.dumps(self._presets))
            self._load_presets()
            self.preset_combo.setCurrentText(name.strip())

        def _apply_preset(self, name):
            if name not in self._presets:
                return
            wanted = set(self._presets[name])
            for i in range(self.pid_list.count()):
                it = self.pid_list.item(i)
                it.setCheckState(
                    QtCore.Qt.Checked if it.data(QtCore.Qt.UserRole) in wanted
                    else QtCore.Qt.Unchecked
                )

        def _delete_preset(self):
            import json

            name = self.preset_combo.currentText()
            if name in self._presets:
                del self._presets[name]
                self.settings.setValue("live/presets", json.dumps(self._presets))
                self._load_presets()

        def disconnect_adapter(self):
            if self._livedata is not None:
                self._livedata.stop_poll()
                self._livedata.close()
                self._livedata = None
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:  # noqa: BLE001
                    pass
            self.conn = None
            self.conn_status.setText("Not connected.")
            self._set_connected(False)

        def _set_connected(self, on: bool):
            self.btn_connect.setEnabled(not on)
            self.btn_disconnect.setEnabled(on)
            for w in (self.btn_start, self.btn_read_dtc, self.btn_clear_dtc,
                      self.btn_vehinfo, self.btn_mode06, self.btn_livedata):
                w.setEnabled(on)

        def show_onboard_tests(self):
            if self.conn is None:
                return
            tests = self.conn.read_monitor_tests() if hasattr(self.conn, "read_monitor_tests") else []
            OnboardTestsDialog(tests, self).exec()

        def show_vehicle_info(self):
            if self.conn is None:
                QtWidgets.QMessageBox.information(self, "Vehicle Info", "Connect to an adapter first.")
                return
            from vcds_core import vin as vinmod

            conn = self.conn
            vstr = conn.read_vin() if hasattr(conn, "read_vin") else None
            cals = conn.read_calibration_ids() if hasattr(conn, "read_calibration_ids") else []
            readiness = conn.read_readiness() if hasattr(conn, "read_readiness") else None
            try:
                perm = conn.read_permanent_dtcs() if hasattr(conn, "read_permanent_dtcs") else []
            except Exception:  # noqa: BLE001
                perm = []
            info = vinmod.decode_vin(vstr) if vstr else None
            VehicleInfoDialog(vstr, info, cals, readiness, perm, self).exec()
            # Auto-select the brand profile from the VIN, and add to the garage.
            if info and info.brand_profile != "generic":
                self.main._set_profile(info.brand_profile)
            if vstr and info:
                path = os.path.join(DEFAULT_LOGS_DIR, "garage.json")
                vehicles = garage_mod.load_garage(path)
                garage_mod.add_or_update(vehicles, garage_mod.Vehicle(
                    vin=info.vin, make=info.make, year=info.year,
                    brand_profile=info.brand_profile))
                garage_mod.save_garage(path, vehicles)
                self.main.settings.setValue("garage/active_vin", info.vin)

        # -- triggers ------------------------------------------------------- #
        def _add_trigger_rule(self):
            chan = self.trig_chan.text().strip()
            try:
                val = float(self.trig_val.text().strip())
            except ValueError:
                return
            if not chan:
                return
            self.trigger_rules.append({"channel": chan, "op": self.trig_op.currentText(), "value": val})
            self.trig_label.setText(
                "; ".join(f"{r['channel']} {r['op']} {r['value']:g}" for r in self.trigger_rules)
            )

        def _build_trigger(self):
            if not self.trigger_rules and not self.chk_dtc.isChecked():
                return None
            return live.Trigger(thresholds=list(self.trigger_rules), on_new_dtc=self.chk_dtc.isChecked())

        # -- DTCs ----------------------------------------------------------- #
        def read_dtcs(self):
            if self.conn is None:
                return
            self.dtc_tree.clear()
            try:
                dtcs = live.read_dtcs(self.conn)
            except Exception as exc:  # noqa: BLE001
                self.dtc_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem([f"Error: {exc}", ""]))
                return
            if not dtcs:
                self.dtc_tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(["No stored DTCs.", ""]))
                return
            for code, desc in dtcs:
                k = knowledge.lookup(code)
                node = QtWidgets.QTreeWidgetItem([f"{code} — {desc or k.description}", k.severity.upper()])
                node.setForeground(1, QtGui.QColor(_SEVERITY_COLORS.get(k.severity, "#000000")))
                font = node.font(0)
                font.setBold(True)
                node.setFont(0, font)
                if k.notes:
                    node.addChild(QtWidgets.QTreeWidgetItem([k.notes, ""]))
                if k.causes:
                    causes = QtWidgets.QTreeWidgetItem(["Likely causes (most likely first):", ""])
                    for c in k.causes:
                        causes.addChild(QtWidgets.QTreeWidgetItem([f"•  {c}", ""]))
                    node.addChild(causes)
                    causes.setExpanded(True)
                self.dtc_tree.addTopLevelItem(node)
                node.setExpanded(True)

        def clear_dtcs(self):
            if self.conn is None:
                return
            ok = QtWidgets.QMessageBox.question(
                self,
                "Clear DTCs",
                "This will CLEAR all stored trouble codes and freeze-frame data "
                "from the ECU. This cannot be undone. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ok != QtWidgets.QMessageBox.Yes:
                return
            try:
                done = self.conn.clear_dtcs()
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Clear failed", str(exc))
                return
            QtWidgets.QMessageBox.information(
                self, "Clear DTCs", "Cleared." if done else "ECU did not confirm the clear."
            )
            self.read_dtcs()

        # -- logging -------------------------------------------------------- #
        def _selected_channels(self) -> List[live.LiveChannel]:
            wanted = set()
            for i in range(self.pid_list.count()):
                it = self.pid_list.item(i)
                if it.checkState() == QtCore.Qt.Checked:
                    wanted.add(it.data(QtCore.Qt.UserRole))
            return [c for c in self.channels if c.name in wanted] or self.channels

        def _session_dir(self) -> str:
            """Live logs go into a per-vehicle subfolder named from the VIN."""
            vin = self.main.settings.value("garage/active_vin", "", type=str)
            if not vin:
                return DEFAULT_LOGS_DIR
            veh = garage_mod.find(
                garage_mod.load_garage(os.path.join(DEFAULT_LOGS_DIR, "garage.json")), vin)
            sub = (garage_mod.log_folder_name(veh) if veh
                   else garage_mod._safe_dirname(vin[-12:]))
            return os.path.join(DEFAULT_LOGS_DIR, sub)

        def start_logging(self):
            if self.conn is None:
                return
            # The logger owns the connection during a session — pause the
            # free-running Live Data poller; it'll update from the sample stream.
            if self._livedata is not None and self._livedata.isVisible():
                self._livedata.stop_poll()
                self._livedata.status.setText("Streaming from the active recording…")
            channels = self._selected_channels()
            if getattr(self.conn, "is_async", False):
                self.conn.rewatch([ch.command_name for ch in channels if ch.command_name])
            self.plot.clear()
            for ch in channels:
                self.plot.add_channel(ch.name, [], [], ch.unit)
            logs_dir = self._session_dir()
            self.logger = live.LiveLogger(self.conn, channels, logs_dir, sample_rate_hz=self.rate_spin.value())
            self.logger.header_lines = list(self.vehicle_header)  # embed vehicle ID in the log

            session_name = _safe_session_name(self.name_edit.text())
            self.thread = QtCore.QThread()
            self.worker = LiveWorker(self.logger, float(self.dur_spin.value()),
                                     self._build_trigger(), session_name)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.sample.connect(self._on_sample)
            self.worker.finished.connect(self._on_finished)
            self.worker.failed.connect(self._on_failed)
            self.worker.finished.connect(self.thread.quit)
            self.worker.failed.connect(self.thread.quit)

            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.run_status.setText("Logging…")
            self.thread.start()

        def stop_logging(self):
            if self.logger is not None:
                self.logger.stop()

        def open_gauges(self):
            channels = self._selected_channels()
            if not channels:
                QtWidgets.QMessageBox.information(
                    self, "Gauges", "Connect and select at least one PID first.")
                return
            system = self.settings.value("ui/units", units.AS_LOGGED, type=str)
            self._gauges = GaugeWindow(channels, system, self)
            self._gauges.set_thresholds(self.trigger_rules)
            self._gauges.show()

        def open_live_data(self):
            if self.conn is None:
                QtWidgets.QMessageBox.information(
                    self, "Live Data", "Connect to an adapter first.")
                return
            channels = self._selected_channels()
            if not channels:
                QtWidgets.QMessageBox.information(
                    self, "Live Data", "Select at least one PID to stream.")
                return
            if getattr(self.conn, "is_async", False):
                self.conn.rewatch([ch.command_name for ch in channels if ch.command_name])
            self._livedata = LiveDataWindow(channels, self)
            # Free-run its own poller only when not recording (shared connection).
            if self.logger is None or not self.btn_stop.isEnabled():
                self._livedata.start_poll(self.conn)
            else:
                self._livedata.status.setText("Streaming from the active recording…")
            self._livedata.show()

        _ALERT_OPS = {
            ">": lambda a, b: a > b, "<": lambda a, b: a < b,
            ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
            "==": lambda a, b: a == b,
        }

        def _eval_alerts(self, values):
            tripped = []
            for r in self.trigger_rules:
                v = values.get(r["channel"])
                op = self._ALERT_OPS.get(r["op"])
                if v is not None and op and op(v, r["value"]):
                    tripped.append(f"{r['channel']} {r['op']} {r['value']:g} (now {v:g})")
            return tripped

        def _flash_alert(self):
            self._alert_flash = not self._alert_flash
            shade = "#E10600" if self._alert_flash else "#8A0400"
            self.alert_banner.setStyleSheet(
                f"background:{shade}; color:white; font-weight:bold; font-size:12pt;"
                " border-radius:6px; padding:6px;")

        def _update_alerts(self, values):
            if not self.chk_alert.isChecked():
                if self._alert_active:
                    self._clear_alerts()
                return
            tripped = self._eval_alerts(values)
            keys = {t.split(" (now")[0] for t in tripped}
            if tripped:
                self.alert_banner.setText("⚠  " + "    ·    ".join(tripped))
                self.alert_banner.show()
                if not self._alert_timer.isActive():
                    self._alert_timer.start()
                if keys - self._alert_active:  # a newly-tripped rule → beep
                    QtWidgets.QApplication.beep()
                self._alert_active = keys
            elif self._alert_active:
                self._clear_alerts()

        def _clear_alerts(self):
            self._alert_active = set()
            self._alert_timer.stop()
            self.alert_banner.hide()

        @QtCore.Slot(float, dict, str)
        def _on_sample(self, t, values, marker):
            self.plot.append_sample(t, values)
            if self._gauges is not None and self._gauges.isVisible():
                self._gauges.update_values(values)
            if self._livedata is not None and self._livedata.isVisible():
                self._livedata.update_values(values)
            self._update_alerts(values)
            if marker:
                self.run_status.setText(f"Logging… (event at t={t:.1f}s)")

        def _resume_livedata(self):
            if (self._livedata is not None and self._livedata.isVisible()
                    and self.conn is not None):
                self._livedata.start_poll(self.conn)

        @QtCore.Slot(object)
        def _on_finished(self, result):
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._clear_alerts()
            self._resume_livedata()
            self.run_status.setText(
                f"Saved {os.path.basename(result.session_file)} ({result.sample_count} samples)."
            )
            # Record the session under the active garage vehicle, if any.
            active = self.main.settings.value("garage/active_vin", "", type=str)
            if active:
                path = os.path.join(DEFAULT_LOGS_DIR, "garage.json")
                vehicles = garage_mod.load_garage(path)
                try:
                    rel = os.path.relpath(result.session_file, DEFAULT_LOGS_DIR)
                except ValueError:
                    rel = os.path.basename(result.session_file)
                if garage_mod.add_session(vehicles, active, rel):
                    garage_mod.save_garage(path, vehicles)
            for cap in result.captures:
                item = QtWidgets.QListWidgetItem(
                    f"{os.path.basename(cap.file)} — {cap.trigger_kind} @ {cap.trigger_time:.1f}s"
                )
                item.setData(QtCore.Qt.UserRole, cap.file)
                self.capture_list.addItem(item)
            # offer the session itself for analysis
            sess = QtWidgets.QListWidgetItem(f"[session] {os.path.basename(result.session_file)}")
            sess.setData(QtCore.Qt.UserRole, result.session_file)
            self.capture_list.addItem(sess)

        @QtCore.Slot(str)
        def _on_failed(self, msg):
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._clear_alerts()
            self._resume_livedata()
            self.run_status.setText(f"Error: {msg}")

        def _open_capture(self, item: "QtWidgets.QListWidgetItem"):
            path = item.data(QtCore.Qt.UserRole)
            if path and os.path.isfile(path):
                self.main.open_in_analyzer(path)

    # --------------------------------------------------------------------- #
    # Help
    # --------------------------------------------------------------------- #
    VCDS_LOG_HTML = """
    <p>This app reads the <code>.CSV</code> files VCDS writes — it can't drive
    VCDS itself. To create one in the Ross-Tech VCDS application:</p>
    <ul>
      <li><b>Advanced Measuring Values</b> (newer cars, recommended): Select
          Control Module (e.g. 01&nbsp;-&nbsp;Engine) &rarr;
          <b>[Adv.&nbsp;Measuring&nbsp;Values]</b> &rarr; tick the measurements
          you want (include the <i>Specified</i> <b>and</b> <i>Actual</i> pairs
          for boost/lambda so divergence detection works) &rarr; <b>[Log]</b>
          &rarr; do your drive/pull &rarr; <b>[Stop]</b>, then
          <b>[Done,&nbsp;Go&nbsp;Back]</b>.</li>
      <li><b>Measuring Blocks</b> (older modules): Select Control Module &rarr;
          <b>[Measuring&nbsp;Blocks&nbsp;-&nbsp;08]</b> &rarr; enter the group
          number(s) &rarr; <b>[Log]</b> &rarr; choose the fields &rarr;
          <b>[Done,&nbsp;Close]</b>.</li>
    </ul>
    <p>VCDS saves logs to <code>C:\\Ross-Tech\\VCDS\\Logs</code> — the folder this
    app reads (shown in the status bar; override with the
    <code>VCDS_LOGS_DIR</code> environment variable). Then use
    <b>Open&nbsp;Measuring&nbsp;CSV…</b> and <b>🔍&nbsp;Diagnose</b>.</p>
    <p class="muted">Tip: a steady wide-open-throttle pull makes boost/power
    issues easiest to spot.</p>
    """

    HELP_HTML = """
    <h2>VCDS Toolkit — User Guide</h2>
    <p>Analyze VCDS logs and capture live OBD-II data from a VAG/Audi car.
    There are two tabs that share one plot.</p>

    <h3>What it does &mdash; and does not</h3>
    <ul>
      <li><b>Reads the files VCDS writes</b> (measuring <code>.CSV</code> logs and
          Auto-Scan <code>.TXT</code> reports). It does <b>not</b> control the VCDS
          application or the HEX-V2/HEX-NET cable &mdash; Ross-Tech exposes no API
          for that.</li>
      <li><b>Live data comes from a generic ELM327</b> adapter only. A generic
          ELM327 exposes the standard OBD-II PIDs and is <b>blind to the
          VAG-specific channels</b> VCDS reads. An <b>OBDeleven</b> dongle is
          locked to its own app and cannot be used here.</li>
    </ul>

    <h3>Getting a log file out of VCDS</h3>
    """ + VCDS_LOG_HTML + """
    <h3>Tab 1 &mdash; File Analyzer</h3>
    <ol>
      <li><b>Open Measuring CSV…</b> loads a log; its channels appear in the left
          list. Tick/untick a channel to show or hide its trace (the colour
          matches the plot).</li>
      <li><b>Open Auto-Scan…</b> (optional) shows faults grouped by module on the
          right, with each fault's status detail.</li>
      <li><b>Find Events</b> runs the built-in VAG heuristics (specified-vs-actual
          divergence, rising counters, per-channel extremes). Click any event to
          jump the cursor to its time.</li>
      <li><b>Add threshold rule</b> (channel / operator / value) then
          <b>Apply Rules</b> to find threshold crossings, e.g.
          <code>Boost &lt; 1700</code>.</li>
      <li><b>Export View…</b> writes the samples currently in view to a new CSV.</li>
    </ol>

    <h3>Tab 2 &mdash; Live (OBD-II)</h3>
    <ol>
      <li><b>Scan Ports</b>, pick your adapter's port (or type it), choose a baud
          (Auto, or 38400 / 9600 / 115200 for clones), then <b>Connect</b>.
          USB and <b>Bluetooth</b> adapters show up as a COM port (pair Bluetooth
          first). For a <b>Wi-Fi</b> adapter, click <b>📶 Wi-Fi…</b> and enter its
          address (commonly <code>192.168.0.10:35000</code>).</li>
      <li>The supported PIDs appear on the left &mdash; tick the ones to log.</li>
      <li><b>Read DTCs</b> shows stored trouble codes. <b>Clear DTCs…</b> erases
          them and is always behind a confirmation &mdash; it is never automatic.</li>
      <li>Set an <b>event-capture trigger</b>: threshold rules and/or
          &ldquo;trigger on any new DTC&rdquo;. When it fires during logging, a
          clipped capture (with context from <i>before</i> the trigger) is saved.</li>
      <li><b>Start Logging</b> / <b>Stop</b>. On stop, the session CSV is saved to
          your logs folder. Captured events appear at the lower left &mdash;
          double-click one to open it in the File Analyzer tab.</li>
    </ol>

    <h3>The plot</h3>
    <ul>
      <li>Channels have wildly different scales, so traces are drawn
          <b>normalized</b> (toggle the <b>Normalize</b> checkbox to see raw
          values). The vertical cursor always reads out each visible channel's
          <b>real</b> value and unit at the cursor time.</li>
      <li>Scroll to zoom, drag to pan, right-click for more options.</li>
    </ul>

    <h3>Tips</h3>
    <ul>
      <li>Prefer a <b>USB</b> ELM327; Bluetooth clones drop samples.</li>
      <li>Logs and live sessions are read from / written to the folder in
          <code>VCDS_LOGS_DIR</code> (shown in the status bar).</li>
      <li>The derived <b>Boost (derived)</b> channel = MAP &minus; barometric
          pressure.</li>
    </ul>

    <p>Project &amp; docs:
    <a href="https://github.com/JWalen/VAGScanner">github.com/JWalen/VAGScanner</a></p>
    """

    class HelpDialog(QtWidgets.QDialog):
        def __init__(self, version: str, parent=None):
            super().__init__(parent)
            self.setWindowTitle("VCDS Toolkit — User Guide")
            self.resize(760, 680)
            layout = QtWidgets.QVBoxLayout(self)
            browser = QtWidgets.QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(f"<p style='color:#718096'>Version {version}</p>" + HELP_HTML)
            layout.addWidget(browser)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            layout.addWidget(buttons)

    _SEVERITY_COLORS = {
        "critical": "#E53E3E", "high": "#DD6B20", "medium": "#D69E2E",
        "low": "#3182CE", "info": "#718096",
    }

    class CompareDialog(QtWidgets.QDialog):
        """Before/after stats table for two logs (A = current, B = opened)."""

        def __init__(self, comparison, parent=None):
            super().__init__(parent)
            self.setWindowTitle(f"Compare — {comparison.a_name}  vs  {comparison.b_name}")
            self.resize(860, 600)
            v = QtWidgets.QVBoxLayout(self)
            v.addWidget(QtWidgets.QLabel(
                f"<b>A:</b> {comparison.a_name} &nbsp;&nbsp; <b>B:</b> {comparison.b_name} "
                "&nbsp;&nbsp;<span style='color:#718096'>(Δ = B − A)</span>"))

            cols = ["Channel", "Unit", "A mean", "B mean", "Δ mean", "A max", "B max", "Δ max"]
            table = QtWidgets.QTableWidget(len(comparison.channels), len(cols))
            table.setHorizontalHeaderLabels(cols)
            table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
            for r, d in enumerate(comparison.channels):
                table.setItem(r, 0, QtWidgets.QTableWidgetItem(d.name))
                table.setItem(r, 1, QtWidgets.QTableWidgetItem(d.unit))
                table.setItem(r, 2, self._num(d.a_mean))
                table.setItem(r, 3, self._num(d.b_mean))
                table.setItem(r, 4, self._delta(d.d_mean))
                table.setItem(r, 5, self._num(d.a_max))
                table.setItem(r, 6, self._num(d.b_max))
                table.setItem(r, 7, self._delta(d.d_max))
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
            v.addWidget(table, 1)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        @staticmethod
        def _num(x):
            it = QtWidgets.QTableWidgetItem("—" if x is None else f"{x:.2f}")
            it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return it

        @staticmethod
        def _delta(x):
            it = QtWidgets.QTableWidgetItem("—" if x is None else f"{x:+.2f}")
            it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            if x is not None and abs(x) > 1e-9:
                it.setForeground(QtGui.QColor("#38A169" if x > 0 else "#E53E3E"))
            return it

    class PerformanceDialog(QtWidgets.QDialog):
        """Acceleration runs, WOT pulls and an estimated power/torque figure."""

        def __init__(self, log, parent=None):
            super().__init__(parent)
            self._log = log
            self.setWindowTitle("Performance Analysis")
            self.resize(640, 540)
            v = QtWidgets.QVBoxLayout(self)

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Vehicle mass (kg):"))
            self.mass_spin = QtWidgets.QSpinBox()
            self.mass_spin.setRange(500, 4000)
            self.mass_spin.setSingleStep(25)
            self.mass_spin.setValue(1850)
            row.addWidget(self.mass_spin)
            self.btn_go = QtWidgets.QPushButton("Analyze")
            self.btn_go.clicked.connect(self._analyze)
            row.addWidget(self.btn_go)
            row.addStretch(1)
            v.addLayout(row)

            self.out = QtWidgets.QTextBrowser()
            v.addWidget(self.out, 1)
            self.dyno_plot = pg.PlotWidget()
            self.dyno_plot.setLabel("bottom", "Engine RPM")
            self.dyno_plot.setLabel("left", "HP (amber)  ·  N·m (red)")
            self.dyno_plot.addLegend()
            self.dyno_plot.hide()
            v.addWidget(self.dyno_plot, 1)
            self._curve = None

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            self.btn_export_dyno = buttons.addButton("Export dyno CSV…",
                                                     QtWidgets.QDialogButtonBox.ActionRole)
            self.btn_export_dyno.clicked.connect(self._export_dyno)
            self.btn_export_dyno.setEnabled(False)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)
            self._analyze()

        def _export_dyno(self):
            if not self._curve:
                return
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Export dyno CSV", os.path.join(DEFAULT_LOGS_DIR, "dyno.csv"),
                "CSV (*.csv)")
            if not path:
                return
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write("rpm,hp,torque_nm\n")
                    for pt in self._curve.points:
                        fh.write(f"{pt.rpm:.0f},{pt.hp:.1f},{pt.torque_nm:.1f}\n")
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Export failed", str(exc))
                return
            QtWidgets.QMessageBox.information(self, "Saved", f"Saved to\n{path}")

        def _analyze(self):
            log = self._log
            runs = perform.standard_accel_runs(log)
            pulls = perform.detect_pulls(log)
            est = perform.estimate_power(log, self.mass_spin.value())

            p = ["<h3>Acceleration</h3>"]
            if runs:
                p.append("<ul>")
                for r in runs:
                    p.append(f"<li>{r.from_speed:g}–{r.to_speed:g} {r.unit}: "
                             f"<b>{r.elapsed_s:.2f}s</b> (at t={r.start_time:.1f}s)</li>")
                p.append("</ul>")
            else:
                p.append("<p class='muted'>No qualifying acceleration runs found "
                         "(need a speed channel and a clean pull).</p>")

            p.append("<h3>Detected pulls</h3>")
            if pulls:
                p.append("<ul>")
                for pl in pulls:
                    extra = []
                    if pl.peak_boost is not None:
                        extra.append(f"peak boost {pl.peak_boost:g}")
                    if pl.peak_speed is not None:
                        extra.append(f"peak speed {pl.peak_speed:g}")
                    tail = (" — " + ", ".join(extra)) if extra else ""
                    p.append(f"<li>t={pl.start_time:.1f}–{pl.end_time:.1f}s: "
                             f"{pl.rpm_start:.0f}→{pl.rpm_end:.0f} rpm{tail}</li>")
                p.append("</ul>")
            else:
                p.append("<p class='muted'>No sustained RPM pulls detected.</p>")

            p.append("<h3>Estimated power (crank)</h3>")
            if est:
                p.append(f"<p><b>~{est.peak_hp:.0f} hp</b> peak at t={est.peak_hp_time:.1f}s")
                if est.peak_torque_nm:
                    p.append(f", <b>~{est.peak_torque_nm:.0f} N·m</b> "
                             f"(~{est.peak_torque_nm * 0.7376:.0f} lb-ft) "
                             f"at {est.peak_torque_rpm:.0f} rpm")
                p.append(f" — assuming {est.mass_kg} kg.</p>")
                p.append("<p class='muted'>Rough estimate from the speed trace and mass/drag "
                         "assumptions — useful for before/after comparison, not a calibrated "
                         "dyno number.</p>")
            else:
                p.append("<p class='muted'>Need a speed channel and an acceleration event to "
                         "estimate power.</p>")

            drag = perform.dragstrip(log)
            if drag:
                spd = "mph" if "mph" in drag.speed_unit.lower() else "km/h"
                p.append("<h3>Drag strip</h3><ul>")
                if drag.zero_to_s:
                    p.append(f"<li>{drag.zero_to_label}: <b>{drag.zero_to_s:.2f}s</b></li>")
                if drag.quarter_mile_s:
                    p.append(f"<li>¼ mile: <b>{drag.quarter_mile_s:.2f}s</b> "
                             f"@ {drag.trap_speed:.0f} {spd} trap</li>")
                else:
                    p.append("<li class='muted'>¼ mile: distance not reached in this log</li>")
                p.append("</ul>")

            curve = perform.dyno_curve(log, self.mass_spin.value())
            self._curve = curve
            self.dyno_plot.clear()
            if curve:
                rpm = [pt.rpm for pt in curve.points]
                self.dyno_plot.plot(rpm, [pt.hp for pt in curve.points],
                                    pen=pg.mkPen("#FF6A00", width=2), name="HP")
                self.dyno_plot.plot(rpm, [pt.torque_nm for pt in curve.points],
                                    pen=pg.mkPen("#E10600", width=2), name="Torque N·m")
                self.dyno_plot.show()
                self.btn_export_dyno.setEnabled(True)
                p.append("<h3>Virtual dyno</h3><p class='muted'>Estimated crank HP/torque "
                         "vs RPM (envelope from the pull) shown below — relative/tuning aid, "
                         "not a calibrated dyno.</p>")
            else:
                self.dyno_plot.hide()
                self.btn_export_dyno.setEnabled(False)

            econ = trip.fuel_economy(log)
            if econ:
                p.append("<h3>Trip & economy</h3><ul>")
                p.append(f"<li>Distance: {econ.distance_km:.2f} km "
                         f"({econ.distance_km * 0.621371:.2f} mi), "
                         f"avg speed {econ.avg_speed_kmh:.0f} km/h</li>")
                if econ.l_per_100km:
                    p.append(f"<li>Economy (est., from {econ.source}): "
                             f"<b>{econ.l_per_100km:.1f} L/100km</b> "
                             f"(~{econ.mpg_us:.0f} US mpg)</li>")
                p.append(f"<li>Fuel used: {econ.fuel_l:.2f} L, "
                         f"idle {econ.idle_fraction * 100:.0f}% of the time</li></ul>")

            bat = trip.battery_analysis(log)
            if bat:
                p.append("<h3>Battery / charging</h3><ul>")
                p.append(f"<li>Voltage min/avg/max: {bat.min_v:.1f} / {bat.avg_v:.1f} / "
                         f"{bat.max_v:.1f} V</li>")
                if bat.charging_v:
                    p.append(f"<li>Charging voltage (running): ~{bat.charging_v:.1f} V"
                             + ("" if bat.charging_v >= 13.5 else
                                " <span style='color:#DD6B20'>(low — check alternator)</span>")
                             + "</li>")
                if bat.cranking_v < 10.0:
                    p.append(f"<li><span style='color:#E53E3E'>Cranking dip to "
                             f"{bat.cranking_v:.1f} V — weak battery/starter possible</span></li>")
                p.append("</ul>")
            self.out.setHtml("".join(p))

    class DiagnosisDialog(QtWidgets.QDialog):
        """Shows a DiagnosticReport: prioritized findings with causes."""

        def __init__(self, report, log=None, scan=None, plot_png=None, parent=None):
            super().__init__(parent)
            self._report = report
            self._log = log
            self._scan = scan
            self._plot_png = plot_png
            self.setWindowTitle("Diagnosis")
            self.resize(740, 620)
            v = QtWidgets.QVBoxLayout(self)

            head = f"<b>{report.headline}</b>"
            if report.vin:
                head += (f"<br><span style='color:#718096'>VIN {report.vin}"
                         f"{('  ·  ' + report.mileage) if report.mileage else ''}</span>")
            head_label = QtWidgets.QLabel(head)
            head_label.setWordWrap(True)
            v.addWidget(head_label)

            summary = report.summary
            chips = "   ".join(
                f"<span style='color:{_SEVERITY_COLORS.get(k, '#000')}'>● {k}: {summary[k]}</span>"
                for k in ("critical", "high", "medium", "low", "info") if summary.get(k)
            )
            if chips:
                v.addWidget(QtWidgets.QLabel(chips))

            tree = QtWidgets.QTreeWidget()
            tree.setHeaderLabels(["Finding", "Severity"])
            tree.setColumnWidth(0, 540)
            for f in report.findings:
                node = QtWidgets.QTreeWidgetItem([f.title, f.severity.upper()])
                color = QtGui.QColor(_SEVERITY_COLORS.get(f.severity, "#000000"))
                node.setForeground(1, color)
                font = node.font(0)
                font.setBold(True)
                node.setFont(0, font)
                node.addChild(QtWidgets.QTreeWidgetItem([f.detail, ""]))
                if f.causes:
                    causes = QtWidgets.QTreeWidgetItem(["Likely causes (most likely first):", ""])
                    for c in f.causes:
                        causes.addChild(QtWidgets.QTreeWidgetItem([f"•  {c}", ""]))
                    node.addChild(causes)
                    causes.setExpanded(True)
                tree.addTopLevelItem(node)
                node.setExpanded(True)
            if not report.findings:
                tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(["No faults or abnormal readings.", ""]))
            v.addWidget(tree, 1)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            self.btn_save = buttons.addButton("Save Report…", QtWidgets.QDialogButtonBox.ActionRole)
            self.btn_save.clicked.connect(self._save_report)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _save_report(self):
            from vcds_core import __version__ as _ver

            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Diagnostic Report", DEFAULT_LOGS_DIR,
                "PDF document (*.pdf);;HTML page (*.html)",
            )
            if not path:
                return
            html = build_html_report(
                self._report, log=self._log, scan=self._scan, plot_png=self._plot_png,
                generated=time.strftime("%Y-%m-%d %H:%M"), version=_ver,
            )
            try:
                if path.lower().endswith(".pdf"):
                    doc = QtGui.QTextDocument()
                    doc.setHtml(html)
                    writer = QtGui.QPdfWriter(path)
                    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
                    doc.print_(writer)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(html)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
                return
            QtWidgets.QMessageBox.information(self, "Report saved", f"Saved to\n{path}")

    # First-run quick tour pages: (title, html body).
    TOUR_PAGES = [
        (
            "Welcome to VCDS Toolkit 👋",
            "<p>Analyze VCDS logs and capture live OBD-II data from your VAG/Audi.</p>"
            "<p>Two things to know up front:</p>"
            "<ul>"
            "<li>It <b>reads the files VCDS writes</b> — it does not control VCDS "
            "or the HEX cable.</li>"
            "<li>Live data comes from a <b>generic ELM327</b> only, which sees the "
            "standard OBD-II PIDs (not VAG-specific channels).</li>"
            "</ul>"
            "<p>This quick tour takes 20 seconds. You can reopen it any time from "
            "<b>Help → Quick Tour</b>.</p>",
        ),
        (
            "Tab 1 — File Analyzer 📈",
            "<p>Open a measuring <b>CSV</b> (and optionally an <b>Auto-Scan</b>).</p>"
            "<ul>"
            "<li>Tick channels on the left to show/hide their traces.</li>"
            "<li><b>Find Events</b> highlights divergence, rising counters and "
            "extremes; click an event to jump the cursor there.</li>"
            "<li>Add <b>threshold rules</b> (e.g. <code>Boost &lt; 1700</code>) and "
            "<b>Export View…</b> to clip the current window to a new CSV.</li>"
            "</ul>",
        ),
        (
            "Tab 2 — Live (OBD-II) 🔌",
            "<p>Plug in a generic ELM327 (USB preferred), <b>Scan Ports</b>, "
            "<b>Connect</b>, then tick the PIDs to log.</p>"
            "<ul>"
            "<li><b>Read / Clear DTCs</b> — clearing always asks first.</li>"
            "<li>Set a <b>trigger</b> (threshold or any new DTC) to auto-save a "
            "clipped capture around an intermittent fault.</li>"
            "<li><b>Start / Stop Logging</b>; the session CSV is immediately "
            "analyzable back in Tab 1.</li>"
            "</ul>",
        ),
        (
            "Tips & help 💡",
            "<ul>"
            "<li>Traces are <b>normalized</b> so different scales fit one axis; the "
            "cursor still reads each channel's real value.</li>"
            "<li>Files live in the folder shown in the status bar "
            "(<code>VCDS_LOGS_DIR</code>).</li>"
            "<li>Press <b>F1</b> any time for the full User Guide.</li>"
            "</ul>"
            "<p>Have fun — and drive safely. 🏎️</p>",
        ),
    ]

    class QuickTourDialog(QtWidgets.QDialog):
        def __init__(self, settings, show_startup_default: bool, parent=None):
            super().__init__(parent)
            self.settings = settings
            self.setWindowTitle("Welcome to VCDS Toolkit")
            self.resize(580, 440)
            v = QtWidgets.QVBoxLayout(self)

            self.stack = QtWidgets.QStackedWidget()
            v.addWidget(self.stack, 1)
            for title, body in TOUR_PAGES:
                label = QtWidgets.QLabel(f"<h2>{title}</h2>{body}")
                label.setTextFormat(QtCore.Qt.RichText)
                label.setWordWrap(True)
                label.setAlignment(QtCore.Qt.AlignTop)
                label.setOpenExternalLinks(True)
                area = QtWidgets.QScrollArea()
                area.setWidgetResizable(True)
                area.setWidget(label)
                self.stack.addWidget(area)

            nav = QtWidgets.QHBoxLayout()
            self.chk = QtWidgets.QCheckBox("Show this tour at startup")
            self.chk.setChecked(show_startup_default)
            self.lbl_step = QtWidgets.QLabel()
            self.btn_back = QtWidgets.QPushButton("Back")
            self.btn_next = QtWidgets.QPushButton("Next")
            nav.addWidget(self.chk)
            nav.addStretch(1)
            nav.addWidget(self.lbl_step)
            nav.addWidget(self.btn_back)
            nav.addWidget(self.btn_next)
            v.addLayout(nav)

            self.btn_back.clicked.connect(self._back)
            self.btn_next.clicked.connect(self._next)
            self.stack.currentChanged.connect(self._update)
            self._update()

        def _update(self):
            i, n = self.stack.currentIndex(), self.stack.count()
            self.btn_back.setEnabled(i > 0)
            self.btn_next.setText("Finish" if i == n - 1 else "Next")
            self.lbl_step.setText(f"{i + 1} / {n}")

        def _back(self):
            self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))

        def _next(self):
            i = self.stack.currentIndex()
            if i >= self.stack.count() - 1:
                self.accept()
            else:
                self.stack.setCurrentIndex(i + 1)

        def _persist(self):
            self.settings.setValue("ui/show_tour", self.chk.isChecked())

        def accept(self):
            self._persist()
            super().accept()

        def reject(self):
            self._persist()
            super().reject()

    # --------------------------------------------------------------------- #
    # Auto-update (checks GitHub Releases on a background thread)
    # --------------------------------------------------------------------- #
    class UpdateCheckWorker(QtCore.QObject):
        found = QtCore.Signal(object)
        none = QtCore.Signal()
        failed = QtCore.Signal(str)

        def __init__(self, current_version: str):
            super().__init__()
            self.current_version = current_version

        @QtCore.Slot()
        def run(self):
            try:
                info = updater.check_for_update(self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))
                return
            if info:
                self.found.emit(info)
            else:
                self.none.emit()

    class UpdateDownloadWorker(QtCore.QObject):
        progress = QtCore.Signal(int, int)
        done = QtCore.Signal(str)
        failed = QtCore.Signal(str)

        def __init__(self, info, dest_dir: str):
            super().__init__()
            self.info = info
            self.dest_dir = dest_dir
            self._cancel = False

        def cancel(self):
            self._cancel = True

        @QtCore.Slot()
        def run(self):
            try:
                path = updater.download_installer(
                    self.info,
                    self.dest_dir,
                    progress=lambda d, t: self.progress.emit(d, t),
                    is_cancelled=lambda: self._cancel,
                )
                self.done.emit(path)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

    class UpdateBanner(QtWidgets.QFrame):
        install = QtCore.Signal()
        notes = QtCore.Signal()
        dismiss = QtCore.Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setStyleSheet(
                "UpdateBanner { background: #0066CC; }"
                " QLabel { color: white; }"
                " QPushButton { padding: 3px 12px; }"
            )
            h = QtWidgets.QHBoxLayout(self)
            h.setContentsMargins(12, 7, 12, 7)
            self.label = QtWidgets.QLabel()
            font = self.label.font()
            font.setBold(True)
            self.label.setFont(font)
            h.addWidget(self.label, 1)
            b_notes = QtWidgets.QPushButton("Release Notes")
            b_install = QtWidgets.QPushButton("Download && Install")
            b_dismiss = QtWidgets.QPushButton("Dismiss")
            for b in (b_notes, b_install, b_dismiss):
                h.addWidget(b)
            b_notes.clicked.connect(self.notes)
            b_install.clicked.connect(self.install)
            b_dismiss.clicked.connect(self.dismiss)

        def show_update(self, text: str):
            self.label.setText(text)
            self.show()

    class McpInstallDialog(QtWidgets.QDialog):
        """One-click registration of the MCP server with Claude Desktop / Code."""

        def __init__(self, default_logs_dir: str, parent=None):
            super().__init__(parent)
            from vcds_mcp import install as mcp_install

            self._install = mcp_install
            self.setWindowTitle("Install MCP Server for Claude")
            self.resize(620, 500)
            v = QtWidgets.QVBoxLayout(self)

            info = QtWidgets.QLabel(
                "Register this app as an <b>MCP server</b> so Claude can read your "
                "VCDS logs and run diagnostics for you.<br><br>"
                "A local stdio server attaches to <b>Claude Desktop</b> and "
                "<b>Claude Code</b> (not the claude.ai web app). Claude Desktop "
                "must be restarted afterwards."
            )
            info.setWordWrap(True)
            info.setTextFormat(QtCore.Qt.RichText)
            v.addWidget(info)

            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Logs folder:"))
            self.logs_edit = QtWidgets.QLineEdit(default_logs_dir)
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(self._browse)
            row.addWidget(self.logs_edit, 1)
            row.addWidget(browse)
            v.addLayout(row)

            self.chk_desktop = QtWidgets.QCheckBox("Claude Desktop")
            self.chk_desktop.setChecked(True)
            self.chk_code = QtWidgets.QCheckBox("Claude Code (CLI)")
            code_ok = mcp_install.claude_code_available()
            self.chk_code.setChecked(code_ok)
            self.chk_code.setEnabled(code_ok)
            if not code_ok:
                self.chk_code.setText("Claude Code (CLI) — not found on PATH")
            v.addWidget(self.chk_desktop)
            v.addWidget(self.chk_code)

            self.btn_install = QtWidgets.QPushButton("Install")
            self.btn_install.clicked.connect(self._do_install)
            v.addWidget(self.btn_install)

            self.results = QtWidgets.QTextEdit()
            self.results.setReadOnly(True)
            v.addWidget(self.results, 1)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _browse(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select VCDS Logs folder", self.logs_edit.text()
            )
            if d:
                self.logs_edit.setText(d)

        def _do_install(self):
            logs = self.logs_edit.text().strip() or DEFAULT_LOGS_DIR
            lines = []
            if self.chk_desktop.isChecked():
                ok, msg = self._install.install_claude_desktop(logs)
                lines.append(("✅ " if ok else "❌ ") + "Claude Desktop: " + msg)
            if self.chk_code.isChecked():
                ok, msg = self._install.install_claude_code(logs)
                lines.append(("✅ " if ok else "❌ ") + "Claude Code: " + msg)
            if not lines:
                lines = ["Select at least one target above."]
            self.results.setPlainText("\n\n".join(lines))

    # --------------------------------------------------------------------- #
    # Live gauge dashboard
    # --------------------------------------------------------------------- #
    def _auto_gauge(name: str, unit: str):
        """Pick a sensible gauge kind + range from a channel's name/unit."""
        n, u = name.lower(), unit.lower()
        if "rpm" in u or "rpm" in n or "/min" in u or "engine speed" in n:
            return "needle", 0.0, 8000.0
        if "speed" in n:
            return "needle", 0.0, 260.0
        if "°c" in u or "°f" in u or "temp" in n:
            return "bar", -40.0, 150.0
        if u == "%" or "load" in n or "throttle" in n or "trim" in n or "pedal" in n:
            return "bar", 0.0, 100.0
        if ("kpa" in u or "mbar" in u or "bar" in u or "psi" in u
                or "boost" in n or "map" in n or "pressure" in n):
            return "bar", 0.0, 300.0
        return "numeric", 0.0, 100.0

    class Gauge(QtWidgets.QFrame):
        """A single live gauge: needle dial, bar, or big numeric — customizable."""

        changed = QtCore.Signal(str)  # channel name, when the user customizes it

        def __init__(self, name, unit, kind, vmin, vmax, system=units.AS_LOGGED, parent=None):
            super().__init__(parent)
            self.name = name
            self.unit = unit
            self.kind = kind
            self.vmin = vmin
            self.vmax = vmax
            self.system = system
            self.value = None
            self.warn = None
            self.crit = None
            self.setMinimumSize(190, 150)
            self.setFrameShape(QtWidgets.QFrame.StyledPanel)
            self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            self.customContextMenuRequested.connect(self._menu)

        def set_value(self, value):
            self.value = value
            self.update()

        def _color(self):
            if self.value is None:
                return QtGui.QColor("#0066CC")
            if self.crit is not None and self.value >= self.crit:
                return QtGui.QColor("#E53E3E")
            if self.warn is not None and self.value >= self.warn:
                return QtGui.QColor("#DD6B20")
            return QtGui.QColor("#0066CC")

        def _frac(self):
            if self.value is None or self.vmax == self.vmin:
                return None
            return max(0.0, min(1.0, (self.value - self.vmin) / (self.vmax - self.vmin)))

        # -- customization -------------------------------------------------- #
        def _menu(self, pos):
            m = QtWidgets.QMenu(self)
            for label, kind in (("Needle", "needle"), ("Bar", "bar"), ("Numeric", "numeric")):
                act = m.addAction(label)
                act.setCheckable(True)
                act.setChecked(self.kind == kind)
                act.triggered.connect(lambda _c, k=kind: self._set_kind(k))
            m.addSeparator()
            m.addAction("Set range…").triggered.connect(self._set_range)
            m.exec(self.mapToGlobal(pos))

        def _set_kind(self, kind):
            self.kind = kind
            self.update()
            self.changed.emit(self.name)

        def _set_range(self):
            lo, ok = QtWidgets.QInputDialog.getDouble(self, "Range", "Minimum:", self.vmin, -1e6, 1e6, 1)
            if not ok:
                return
            hi, ok = QtWidgets.QInputDialog.getDouble(self, "Range", "Maximum:", self.vmax, -1e6, 1e6, 1)
            if not ok or hi <= lo:
                return
            self.vmin, self.vmax = lo, hi
            self.update()
            self.changed.emit(self.name)

        # -- painting ------------------------------------------------------- #
        def paintEvent(self, _ev):
            import math

            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            fg = self.palette().color(QtGui.QPalette.WindowText)
            muted = QtGui.QColor("#8a93a6")
            r = self.rect().adjusted(8, 8, -8, -8)

            p.setPen(muted)
            font = p.font()
            font.setPointSize(9)
            p.setFont(font)
            p.drawText(QtCore.QRectF(r.left(), r.top(), r.width(), 16),
                       QtCore.Qt.AlignLeft, self.name)

            disp, dunit = (None, self.unit)
            if self.value is not None:
                disp, dunit = units.convert(self.value, self.unit, self.system)
            vtxt = "—" if disp is None else f"{disp:g} {dunit}".strip()

            if self.kind == "needle":
                cx = r.center().x()
                cy = r.center().y() + r.height() * 0.12
                rad = min(r.width(), r.height()) * 0.40
                arc = QtCore.QRectF(cx - rad, cy - rad, 2 * rad, 2 * rad)
                p.setPen(QtGui.QPen(muted, 3))
                p.drawArc(arc, 225 * 16, -270 * 16)
                fr = self._frac()
                if fr is not None:
                    ang = math.radians(225 - 270 * fr)
                    nx = cx + rad * 0.82 * math.cos(ang)
                    ny = cy - rad * 0.82 * math.sin(ang)
                    p.setPen(QtGui.QPen(self._color(), 3))
                    p.drawLine(QtCore.QPointF(cx, cy), QtCore.QPointF(nx, ny))
                p.setPen(fg)
                font.setPointSize(13)
                font.setBold(True)
                p.setFont(font)
                p.drawText(QtCore.QRectF(r.left(), cy + rad * 0.5, r.width(), 24),
                           QtCore.Qt.AlignHCenter, vtxt)
            elif self.kind == "bar":
                bar = QtCore.QRectF(r.left(), r.center().y() - 14, r.width(), 28)
                p.setPen(QtGui.QPen(muted, 1))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawRoundedRect(bar, 5, 5)
                fr = self._frac()
                if fr is not None:
                    fill = QtCore.QRectF(bar.left() + 1, bar.top() + 1,
                                         (bar.width() - 2) * fr, bar.height() - 2)
                    p.setBrush(self._color())
                    p.setPen(QtCore.Qt.NoPen)
                    p.drawRoundedRect(fill, 4, 4)
                p.setPen(fg)
                font.setPointSize(13)
                font.setBold(True)
                p.setFont(font)
                p.drawText(bar, QtCore.Qt.AlignCenter, vtxt)
            else:  # numeric
                p.setPen(self._color() if (self.crit or self.warn) else fg)
                font.setPointSize(22)
                font.setBold(True)
                p.setFont(font)
                p.drawText(r, QtCore.Qt.AlignCenter, vtxt)
            p.end()

    class GaugeWindow(QtWidgets.QWidget):
        def __init__(self, channels, system=units.AS_LOGGED, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Live Gauges")
            self.setWindowFlag(QtCore.Qt.Window)
            self.resize(720, 420)
            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            outer = QtWidgets.QVBoxLayout(self)
            outer.addWidget(QtWidgets.QLabel(
                "<span style='color:#718096'>Right-click a gauge to change its type "
                "(needle / bar / numeric) or range.</span>"))
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QtWidgets.QWidget()
            grid = QtWidgets.QGridLayout(inner)
            scroll.setWidget(inner)
            outer.addWidget(scroll, 1)

            self.gauges = {}
            cols = 4
            for i, ch in enumerate(channels):
                kind, lo, hi = self._config_for(ch)
                g = Gauge(ch.name, ch.unit, kind, lo, hi, system)
                g.changed.connect(self._save_config)
                self.gauges[ch.name] = g
                grid.addWidget(g, i // cols, i % cols)

        def _config_for(self, ch):
            kind, lo, hi = _auto_gauge(ch.name, ch.unit)
            raw = self.settings.value(f"gauge/{ch.name}", "", type=str)
            if raw:
                try:
                    import json
                    d = json.loads(raw)
                    return d.get("kind", kind), float(d.get("min", lo)), float(d.get("max", hi))
                except Exception:  # noqa: BLE001
                    pass
            return kind, lo, hi

        def _save_config(self, name):
            import json
            g = self.gauges.get(name)
            if g is None:
                return
            self.settings.setValue(
                f"gauge/{name}", json.dumps({"kind": g.kind, "min": g.vmin, "max": g.vmax}))

        def set_thresholds(self, rules):
            for name, g in self.gauges.items():
                g.warn = None
                g.crit = None
                for r in rules:
                    chan = str(r.get("channel", "")).lower()
                    if chan and chan in name.lower() and r.get("op") in (">", ">="):
                        g.crit = float(r.get("value"))

        def update_values(self, values):
            for name, g in self.gauges.items():
                if name in values:
                    g.set_value(values[name])

    # --------------------------------------------------------------------- #
    # Tab 3 — AI Assistant
    # --------------------------------------------------------------------- #
    class ChatInput(QtWidgets.QPlainTextEdit):
        """Multiline input that sends on Enter (Shift+Enter inserts a newline)."""

        send_requested = QtCore.Signal()

        def keyPressEvent(self, ev):
            if (ev.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter)
                    and not (ev.modifiers() & QtCore.Qt.ShiftModifier)):
                self.send_requested.emit()
                return
            super().keyPressEvent(ev)

    class AiChatWorker(QtCore.QObject):
        done = QtCore.Signal(str)
        failed = QtCore.Signal(str)
        tool = QtCore.Signal(str)
        delta = QtCore.Signal(str)

        def __init__(self, provider, key, model, system, messages, tools=None, executor=None):
            super().__init__()
            self.provider = provider
            self.key = key
            self.model = model
            self.system = system
            self.messages = messages
            self.tools = tools
            self.executor = executor

        @QtCore.Slot()
        def run(self):
            ex = self.executor

            def wrapped(name, args):
                self.tool.emit(name)
                return ex(name, args) if ex else {}

            try:
                reply = ai.chat(self.provider, self.key, self.model, self.system, self.messages,
                                tools=self.tools, tool_executor=(wrapped if ex else None),
                                on_delta=self.delta.emit)
                self.done.emit(reply)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

    class AiSettingsDialog(QtWidgets.QDialog):
        """Provider / model / API-key configuration (kept off the chat page)."""

        def __init__(self, settings, parent=None):
            super().__init__(parent)
            self.settings = settings
            self.setWindowTitle("AI Settings")
            self.resize(540, 240)
            form = QtWidgets.QFormLayout(self)
            self.provider_combo = QtWidgets.QComboBox()
            for pid, prov in ai.PROVIDERS.items():
                self.provider_combo.addItem(prov.label, pid)
            form.addRow("Provider:", self.provider_combo)
            self.model_combo = QtWidgets.QComboBox()
            self.model_combo.setEditable(True)
            form.addRow("Model:", self.model_combo)
            self.key_edit = QtWidgets.QLineEdit()
            self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
            form.addRow("API key:", self.key_edit)
            self.key_link = QtWidgets.QLabel()
            self.key_link.setOpenExternalLinks(True)
            form.addRow("", self.key_link)
            note = QtWidgets.QLabel("Keys are stored locally in your user settings (not encrypted).")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            form.addRow("", note)
            bb = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Close)
            bb.button(QtWidgets.QDialogButtonBox.Save).clicked.connect(self._save)
            bb.rejected.connect(self.reject)
            form.addRow(bb)

            self.provider_combo.currentIndexChanged.connect(self._provider_changed)
            pid = settings.value("ai/provider", "anthropic", type=str)
            self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(pid)))
            self._provider_changed()

        def _provider_changed(self):
            pid = self.provider_combo.currentData()
            prov = ai.PROVIDERS[pid]
            self.model_combo.clear()
            self.model_combo.addItems(prov.models)
            self.model_combo.setCurrentText(
                self.settings.value(f"ai/model/{pid}", prov.default_model, type=str))
            self.key_edit.setText(self.settings.value(f"ai/key/{pid}", "", type=str))
            self.key_link.setText(f"<a href='{prov.key_url}'>Get an API key</a>")

        def _save(self):
            pid = self.provider_combo.currentData()
            self.settings.setValue("ai/provider", pid)
            self.settings.setValue(f"ai/model/{pid}", self.model_combo.currentText().strip())
            self.settings.setValue(f"ai/key/{pid}", self.key_edit.text().strip())
            self.accept()

    class AiAssistantTab(QtWidgets.QWidget):
        CHATS_FILE = "ai_chats.json"

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            self.chats: list = []
            self.current = None
            self.history: list = []
            self._thread = None
            self._worker = None
            self._pending = None
            self._error = None
            self._stream_text = ""
            self._build()
            self._load_chats()
            self._update_model_label()

        def _build(self):
            root = QtWidgets.QHBoxLayout(self)

            # left: conversation list
            left = QtWidgets.QVBoxLayout()
            self.btn_new = QtWidgets.QPushButton("＋  New chat")
            self.btn_new.setObjectName("Accent")
            left.addWidget(self.btn_new)
            self.chat_list = QtWidgets.QListWidget()
            left.addWidget(self.chat_list, 1)
            self.btn_delete = QtWidgets.QPushButton("🗑  Delete")
            left.addWidget(self.btn_delete)
            left_w = QtWidgets.QWidget()
            left_w.setLayout(left)
            left_w.setFixedWidth(200)
            root.addWidget(left_w)

            # right: header + conversation + input
            right = QtWidgets.QVBoxLayout()
            header = FlowLayout()
            self.model_label = QtWidgets.QLabel("")
            self.model_label.setObjectName("Muted")
            self.btn_settings = QtWidgets.QPushButton("⚙ AI Settings")
            self.chk_context = QtWidgets.QCheckBox("Use scan/log as context")
            self.chk_context.setChecked(True)
            self.chk_tools = QtWidgets.QCheckBox("Let the AI use tools")
            self.chk_tools.setChecked(True)
            self.chk_tools.setToolTip("Browse stored logs and read the live car to investigate")
            self.btn_save_chat = QtWidgets.QPushButton("Export…")
            for w in (self.model_label, self.btn_settings, self.chk_context, self.chk_tools,
                      self.btn_save_chat):
                header.addWidget(w)
            right.addLayout(header)

            self.conversation = QtWidgets.QTextBrowser()
            self.conversation.setOpenExternalLinks(True)
            right.addWidget(self.conversation, 1)

            entry = QtWidgets.QHBoxLayout()
            self.input = ChatInput()
            self.input.setPlaceholderText("Message…  (Enter to send, Shift+Enter for a new line)")
            self.input.setMaximumHeight(90)
            entry.addWidget(self.input, 1)
            self.btn_send = QtWidgets.QPushButton("Send")
            entry.addWidget(self.btn_send)
            right.addLayout(entry)
            root.addLayout(right, 1)

            self.btn_new.clicked.connect(self._new_chat)
            self.btn_delete.clicked.connect(self._delete_chat)
            self.chat_list.currentRowChanged.connect(self._select_chat)
            self.btn_settings.clicked.connect(self.open_settings)
            self.btn_save_chat.clicked.connect(self._save_chat)
            self.btn_send.clicked.connect(self.send)
            self.input.send_requested.connect(self.send)

        # -- settings ------------------------------------------------------- #
        def open_settings(self):
            if AiSettingsDialog(self.settings, self).exec():
                self._update_model_label()

        def _update_model_label(self):
            pid = self.settings.value("ai/provider", "anthropic", type=str)
            prov = ai.PROVIDERS.get(pid)
            model = self.settings.value(
                f"ai/model/{pid}", prov.default_model if prov else "", type=str)
            has_key = bool(self.settings.value(f"ai/key/{pid}", "", type=str))
            name = prov.label if prov else pid
            tail = "" if has_key else "  —  no API key (open ⚙ AI Settings)"
            self.model_label.setText(f"🤖 {name} · {model}{tail}")

        def refresh_vehicle(self):
            """Called when the tab is shown — just refresh the model header."""
            self._update_model_label()

        # -- chat store ----------------------------------------------------- #
        def _chats_path(self):
            return os.path.join(DEFAULT_LOGS_DIR, self.CHATS_FILE)

        def _garage_path(self):
            return os.path.join(DEFAULT_LOGS_DIR, "garage.json")

        def _mk_id(self, n):
            return time.strftime("%Y%m%d%H%M%S") + str(n)

        def _blank_chat(self):
            vin = self.settings.value("garage/active_vin", "", type=str)
            return {"id": self._mk_id(len(self.chats)), "title": "New chat",
                    "ts": time.time(), "messages": [], "vin": vin or None}

        def _migrate_garage_chats(self):
            out = []
            for v in garage_mod.load_garage(self._garage_path()):
                if getattr(v, "chat", None):
                    out.append({"id": self._mk_id(len(out)), "title": v.label,
                                "ts": 0, "messages": list(v.chat), "vin": v.vin})
            return out

        def _load_chats(self):
            import json
            chats = []
            try:
                with open(self._chats_path(), encoding="utf-8") as fh:
                    chats = json.load(fh)
            except Exception:  # noqa: BLE001
                chats = []
            if not chats:
                chats = self._migrate_garage_chats()
            self.chats = chats if isinstance(chats, list) else []
            if not self.chats:
                self.chats = [self._blank_chat()]
            self._refresh_chat_list()
            self.chat_list.setCurrentRow(0)
            if self.current is None:
                self._select_chat(0)

        def _save_chats(self):
            import json
            try:
                os.makedirs(DEFAULT_LOGS_DIR, exist_ok=True)
                with open(self._chats_path(), "w", encoding="utf-8") as fh:
                    json.dump(self.chats, fh, indent=2)
            except Exception:  # noqa: BLE001
                pass

        def _refresh_chat_list(self):
            self.chat_list.blockSignals(True)
            self.chat_list.clear()
            for c in self.chats:
                it = QtWidgets.QListWidgetItem(c.get("title") or "New chat")
                if c.get("vin"):
                    it.setToolTip(f"Vehicle: {c['vin']}")
                self.chat_list.addItem(it)
            self.chat_list.blockSignals(False)

        def _refresh_titles(self):
            for i, c in enumerate(self.chats):
                it = self.chat_list.item(i)
                if it:
                    it.setText(c.get("title") or "New chat")

        def _select_chat(self, row):
            if row < 0 or row >= len(self.chats):
                return
            self.current = self.chats[row]
            self.history = self.current["messages"]
            self._stream_text = ""
            self._pending = None
            self._error = None
            self._render()

        def _new_chat(self):
            self.chats.insert(0, self._blank_chat())
            self._save_chats()
            self._refresh_chat_list()
            self.chat_list.setCurrentRow(0)

        def _delete_chat(self):
            row = self.chat_list.currentRow()
            if row < 0 or not self.chats:
                return
            del self.chats[row]
            if not self.chats:
                self.chats = [self._blank_chat()]
            self._save_chats()
            self._refresh_chat_list()
            self.chat_list.setCurrentRow(0)

        def _save_chat(self):
            if not self.history:
                QtWidgets.QMessageBox.information(self, "Save chat", "The chat is empty.")
                return
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save chat transcript", os.path.join(DEFAULT_LOGS_DIR, "chat.md"),
                "Markdown (*.md);;Text (*.txt);;HTML (*.html)")
            if not path:
                return
            try:
                if path.lower().endswith(".html"):
                    parts = ["<html><head><meta charset='utf-8'></head><body>"]
                    for m in self.history:
                        who = "You" if m["role"] == "user" else "Assistant"
                        body = _esc_br(m["content"]) if m["role"] == "user" else _md_to_html(m["content"])
                        parts.append(f"<h3>{who}</h3><div>{body}</div>")
                    parts.append("</body></html>")
                    data = "".join(parts)
                else:
                    data = "\n\n".join(
                        f"### {'You' if m['role'] == 'user' else 'Assistant'}\n\n{m['content']}"
                        for m in self.history)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(data)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
                return
            QtWidgets.QMessageBox.information(self, "Chat saved", f"Saved to\n{path}")

        def _render(self):
            if (not self.history and not self._pending and not self._error
                    and not self._stream_text):
                self.conversation.setHtml(
                    "<div style='color:#718096'>Ask the assistant to help diagnose your car. "
                    "It uses the scan/log open in the File Analyzer tab, can browse your stored "
                    "logs, and — when the car is connected in the Live tab — read live DTCs, a "
                    "PID snapshot, VIN and readiness.<br><br>Set your provider and API key in "
                    "<b>⚙ AI Settings</b>, then type a message below. Use <b>＋ New chat</b> to "
                    "start a fresh conversation.</div>")
                return
            blocks = []
            for m in self.history:
                if m["role"] == "user":
                    blocks.append(
                        "<div style='border-left:3px solid #0066CC;padding-left:8px;margin:10px 0'>"
                        f"<b style='color:#0066CC'>You</b><br>{_esc_br(m['content'])}</div>")
                else:
                    blocks.append(
                        "<div style='border-left:3px solid #00897B;padding-left:8px;margin:10px 0'>"
                        f"<b style='color:#00897B'>Assistant</b><br>{_md_to_html(m['content'])}</div>")
            if self._stream_text:
                # live (streaming) reply — plain text for speed; formatted on completion
                blocks.append(
                    "<div style='border-left:3px solid #00897B;padding-left:8px;margin:10px 0'>"
                    f"<b style='color:#00897B'>Assistant</b><br>{_esc_br(self._stream_text)}</div>")
            if self._pending and not self._stream_text:
                blocks.append(f"<div style='color:#718096;margin:10px 0'><i>{self._pending}</i></div>")
            if self._error:
                blocks.append(f"<div style='color:#E53E3E;margin:10px 0'><b>Error:</b> "
                              f"{_html.escape(self._error)}</div>")
            self.conversation.setHtml("".join(blocks))
            sb = self.conversation.verticalScrollBar()
            sb.setValue(sb.maximum())

        def _build_context(self) -> str:
            if not self.chk_context.isChecked():
                return ""
            analyzer = self.main.analyzer
            scan = getattr(analyzer, "scan", None)
            log = getattr(analyzer, "mlog", None)
            if scan is None and log is None:
                return ""
            report = run_diagnose(scan=scan, log=log)
            return report_to_text(report, log=log)

        def send(self):
            text = self.input.toPlainText().strip()
            if not text:
                return
            pid = self.settings.value("ai/provider", "anthropic", type=str)
            key = self.settings.value(f"ai/key/{pid}", "", type=str)
            if not key:
                r = QtWidgets.QMessageBox.question(
                    self, "No API key",
                    "No API key is set for this provider. Open AI Settings now?",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
                if r == QtWidgets.QMessageBox.Yes:
                    self.open_settings()
                return
            prov = ai.PROVIDERS.get(pid)
            model = self.settings.value(
                f"ai/model/{pid}", prov.default_model if prov else "", type=str)
            if self.current is None:
                self._new_chat()
            self.history.append({"role": "user", "content": text})
            if self.current.get("title") in (None, "", "New chat"):
                self.current["title"] = text[:40] + ("…" if len(text) > 40 else "")
                self._refresh_titles()
            self.input.clear()
            self.btn_send.setEnabled(False)
            self._error = None
            self._stream_text = ""
            self._pending = "Assistant is typing…"
            self._render()
            self._save_chats()

            prof = profiles.get_profile(
                self.settings.value("ui/profile", profiles.DEFAULT_PROFILE, type=str))
            system = ai.vehicle_system_prompt(self._build_context(), persona=prof.ai_persona)

            tools = executor = None
            if self.chk_tools.isChecked():
                from vcds_gui import log_tools
                tools = log_tools.TOOL_SPECS
                executor = log_tools.make_executor(
                    DEFAULT_LOGS_DIR, prof.id,
                    conn_getter=lambda: getattr(self.main.live_tab, "conn", None))
                system += (
                    "\n\nYou have tools to investigate. FILE tools read the user's stored logs "
                    f"(folder: {DEFAULT_LOGS_DIR}): list_logs, read_log, read_autoscan, "
                    "diagnose_log, find_events, performance, lookup_dtc. LIVE tools read the "
                    "connected car when an adapter is plugged in (Live tab): obd_status, "
                    "read_live_dtcs, snapshot_pids, vehicle_info, readiness. Proactively use "
                    "these tools to gather data before concluding, and tell the user what you "
                    "find and the most likely fix.")

            self._thread = QtCore.QThread()
            self._worker = AiChatWorker(pid, key, model, system, list(self.history), tools, executor)
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.done.connect(self._on_reply)
            self._worker.failed.connect(self._on_error)
            self._worker.tool.connect(self._on_tool)
            self._worker.delta.connect(self._on_delta)
            self._worker.done.connect(self._thread.quit)
            self._worker.failed.connect(self._thread.quit)
            self._thread.start()

        @QtCore.Slot(str)
        def _on_delta(self, chunk):
            self._pending = None
            self._stream_text += chunk
            self._render()

        @QtCore.Slot(str)
        def _on_tool(self, name):
            pretty = name.replace("_", " ")
            self._pending = f"🔧 {pretty}…"
            self._render()

        @QtCore.Slot(str)
        def _on_reply(self, reply):
            text = reply or self._stream_text
            self.history.append({"role": "assistant", "content": text})
            self._stream_text = ""
            self._pending = None
            self.btn_send.setEnabled(True)
            self._save_chats()
            self._render()

        @QtCore.Slot(str)
        def _on_error(self, msg):
            self._pending = None
            self._stream_text = ""
            self._error = msg
            self.btn_send.setEnabled(True)
            self._render()

    class ResetsDialog(QtWidgets.QDialog):
        """Safe, standardized OBD-II write actions (and an honest note on the rest)."""

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            self.setWindowTitle("Resets & Service")
            self.resize(580, 400)
            v = QtWidgets.QVBoxLayout(self)
            head = QtWidgets.QLabel("<b>Safe standardized actions</b> (standard OBD-II only):")
            v.addWidget(head)

            self.btn_clear = QtWidgets.QPushButton("Clear DTCs & reset readiness monitors…")
            self.btn_clear.clicked.connect(self._clear)
            v.addWidget(self.btn_clear)
            note = QtWidgets.QLabel(
                "<span style='color:#718096'>Mode 04: erases stored trouble codes and "
                "freeze-frame data and resets the I/M readiness monitors. The car must then "
                "complete a drive cycle before it's emissions-ready again.</span>")
            note.setWordWrap(True)
            v.addWidget(note)

            honest = QtWidgets.QLabel(
                "<hr><b>Oil/service reset, coding &amp; adaptations, ECU tuning</b><br>"
                "<span style='color:#718096'>These are <b>not</b> standard OBD-II functions. "
                "They're manufacturer-specific (VCDS / OBDeleven on VAG, FORScan on Ford) and "
                "usually require security access a generic ELM327 doesn't have — so this app "
                "intentionally doesn't perform them, to avoid bricking a module. Engine "
                "tuning/flashing also isn't possible over a generic ELM327; it needs a proper "
                "flashing tool and a real calibration file.</span>")
            honest.setWordWrap(True)
            v.addWidget(honest)
            v.addStretch(1)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _clear(self):
            conn = getattr(self.main.live_tab, "conn", None)
            if conn is None:
                QtWidgets.QMessageBox.information(self, "Resets", "Connect to an adapter first.")
                return
            ok = QtWidgets.QMessageBox.question(
                self, "Clear DTCs & reset readiness",
                "This erases stored trouble codes and freeze-frame data and resets the readiness "
                "monitors. This cannot be undone. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if ok != QtWidgets.QMessageBox.Yes:
                return
            try:
                done = conn.clear_dtcs()
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Failed", str(exc))
                return
            QtWidgets.QMessageBox.information(
                self, "Resets", "Cleared." if done else "The ECU did not confirm the reset.")

    class OnboardTestsDialog(QtWidgets.QDialog):
        """Mode-06 on-board monitoring test results."""

        def __init__(self, tests, parent=None):
            super().__init__(parent)
            self.setWindowTitle("On-board Tests (Mode 06)")
            self.resize(720, 460)
            v = QtWidgets.QVBoxLayout(self)
            if not tests:
                v.addWidget(QtWidgets.QLabel(
                    "No mode-06 results available (the ECU reported none, or the adapter "
                    "doesn't expose them)."))
            else:
                cols = ["Monitor", "Test", "Value", "Min", "Max", "Result"]
                table = QtWidgets.QTableWidget(len(tests), len(cols))
                table.setHorizontalHeaderLabels(cols)
                table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
                for r, t in enumerate(tests):
                    vals = [t.get("command", ""), t.get("name", ""),
                            _fmt_num(t.get("value")), _fmt_num(t.get("min")), _fmt_num(t.get("max"))]
                    for c, val in enumerate(vals):
                        table.setItem(r, c, QtWidgets.QTableWidgetItem(str(val)))
                    res = QtWidgets.QTableWidgetItem("PASS" if t.get("passed", True) else "FAIL")
                    res.setForeground(QtGui.QColor("#38A169" if t.get("passed", True) else "#E53E3E"))
                    table.setItem(r, 5, res)
                table.resizeColumnsToContents()
                v.addWidget(table, 1)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

    class GarageDialog(QtWidgets.QDialog):
        """Manage saved vehicles (per VIN) and pick the active one."""

        GARAGE_PATH = os.path.join(DEFAULT_LOGS_DIR, "garage.json")

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            self.vehicles = garage_mod.load_garage(self.GARAGE_PATH)
            self.setWindowTitle("Garage")
            self.resize(720, 440)
            v = QtWidgets.QVBoxLayout(self)
            self.list = QtWidgets.QListWidget()
            v.addWidget(self.list, 1)
            self._fill()

            bar = QtWidgets.QHBoxLayout()
            for label, slot in (("Add by VIN…", self._add), ("Set Active", self._set_active),
                                ("Edit…", self._edit), ("Remove", self._remove)):
                b = QtWidgets.QPushButton(label)
                b.clicked.connect(slot)
                bar.addWidget(b)
            bar.addStretch(1)
            v.addLayout(bar)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _fill(self):
            self.list.clear()
            active = self.main.settings.value("garage/active_vin", "", type=str)
            for veh in self.vehicles:
                star = "★ " if veh.vin.upper() == active.upper() else ""
                it = QtWidgets.QListWidgetItem(
                    f"{star}{veh.label}  —  {veh.vin}  ({veh.brand_profile})  "
                    f"[{len(veh.sessions)} sessions]")
                it.setData(QtCore.Qt.UserRole, veh.vin)
                self.list.addItem(it)

        def _selected(self):
            it = self.list.currentItem()
            return garage_mod.find(self.vehicles, it.data(QtCore.Qt.UserRole)) if it else None

        def _save(self):
            garage_mod.save_garage(self.GARAGE_PATH, self.vehicles)

        def _add(self):
            from vcds_core import vin as vinmod
            text, ok = QtWidgets.QInputDialog.getText(self, "Add vehicle", "VIN:")
            if not ok or not text.strip():
                return
            info = vinmod.decode_vin(text.strip())
            garage_mod.add_or_update(self.vehicles, garage_mod.Vehicle(
                vin=info.vin, make=info.make, year=info.year, brand_profile=info.brand_profile))
            self._save()
            self._fill()

        def _set_active(self):
            veh = self._selected()
            if not veh:
                return
            self.main.settings.setValue("garage/active_vin", veh.vin)
            if veh.brand_profile != "generic":
                self.main._set_profile(veh.brand_profile)
            self.main.statusBar().showMessage(f"Active vehicle: {veh.label}", 4000)
            self._fill()

        def _edit(self):
            veh = self._selected()
            if not veh:
                return
            nick, ok = QtWidgets.QInputDialog.getText(
                self, "Nickname", f"Nickname for {veh.vin}:", text=veh.nickname)
            if ok:
                veh.nickname = nick.strip()
            mass, ok = QtWidgets.QInputDialog.getDouble(
                self, "Mass", "Vehicle mass (kg, 0 = unknown):", veh.mass_kg or 0.0, 0, 5000, 0)
            if ok:
                veh.mass_kg = mass or None
            self._save()
            self._fill()

        def _remove(self):
            veh = self._selected()
            if not veh:
                return
            self.vehicles = [x for x in self.vehicles if x.vin != veh.vin]
            self._save()
            self._fill()

    class VehicleInfoDialog(QtWidgets.QDialog):
        """Shows VIN/decode, calibration IDs, I/M readiness and permanent DTCs."""

        def __init__(self, vin_str, info, cals, readiness, perm, parent=None):
            super().__init__(parent)
            self._vin, self._info, self._readiness, self._perm = vin_str, info, readiness, perm
            self.setWindowTitle("Vehicle Info & Readiness")
            self.resize(580, 620)
            v = QtWidgets.QVBoxLayout(self)
            br = QtWidgets.QTextBrowser()
            h = ["<h3>Vehicle</h3>", f"<b>VIN:</b> {vin_str or 'n/a'}<br>"]
            if info:
                h.append(f"<b>Make:</b> {info.make or '?'} &nbsp;&nbsp; "
                         f"<b>Year:</b> {info.year or '?'} &nbsp;&nbsp; "
                         f"<b>Profile:</b> {info.brand_profile}<br>")
            if cals:
                h.append("<b>Calibration IDs:</b> " + ", ".join(cals) + "<br>")

            h.append("<h3>Emissions readiness</h3>")
            if readiness:
                mil = "<span style='color:#E53E3E'>ON ⚠</span>" if readiness["mil"] else "off"
                h.append(f"<b>MIL (check-engine):</b> {mil} &nbsp;&nbsp; "
                         f"<b>Stored DTCs:</b> {readiness['dtc_count']}<br>")
                from vcds_core import report as _report
                h.append("<table cellpadding=3>")
                incomplete = []
                for name, st in readiness["monitors"].items():
                    tip = ""
                    if not st["available"]:
                        status = "<span style='color:#718096'>n/a</span>"
                    elif st["complete"]:
                        status = "<span style='color:#38A169'>ready</span>"
                    else:
                        status = "<span style='color:#E53E3E'>NOT ready</span>"
                        incomplete.append(name)
                        tip = f"<span style='color:#718096'>{_report.drive_cycle_tip(name)}</span>"
                    h.append(f"<tr><td>{name.replace('_', ' ')}</td><td>{status}</td>"
                             f"<td>{tip}</td></tr>")
                h.append("</table>")
                ready = (not readiness["mil"]) and not incomplete
                verdict = ("<span style='color:#38A169'>likely ready to pass</span>" if ready
                           else "<span style='color:#DD6B20'>not ready</span>")
                h.append(f"<p><b>Emissions test:</b> {verdict}</p>")
            else:
                h.append("<p style='color:#718096'>Readiness status unavailable.</p>")

            if perm:
                h.append("<h3>Permanent DTCs (mode 0A)</h3><ul>")
                for code, _ in perm:
                    h.append(f"<li>{code} — {knowledge.lookup(code).description}</li>")
                h.append("</ul>")
            br.setHtml("".join(h))
            v.addWidget(br, 1)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            self.btn_smog = buttons.addButton("Save Smog Report…",
                                              QtWidgets.QDialogButtonBox.ActionRole)
            self.btn_smog.clicked.connect(self._save_smog)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _save_smog(self):
            from vcds_core import __version__ as _ver
            from vcds_core import report as _report

            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Smog Report", DEFAULT_LOGS_DIR, "PDF document (*.pdf);;HTML page (*.html)")
            if not path:
                return
            html = _report.build_smog_html(
                self._vin, self._info, self._readiness, self._perm,
                generated=time.strftime("%Y-%m-%d %H:%M"), version=_ver)
            try:
                if path.lower().endswith(".pdf"):
                    doc = QtGui.QTextDocument()
                    doc.setHtml(html)
                    writer = QtGui.QPdfWriter(path)
                    writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.A4))
                    doc.print_(writer)
                else:
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(html)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
                return
            QtWidgets.QMessageBox.information(self, "Report saved", f"Saved to\n{path}")

    class EnhancedPidsDialog(QtWidgets.QDialog):
        """View / read experimental manufacturer (mode 22) enhanced PIDs."""

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            from vcds_obd import enhanced

            self.main = main_window
            self.enh = enhanced
            self.path = os.path.join(DEFAULT_LOGS_DIR, "enhanced_pids.json")
            self.pids = enhanced.load_library(self.path)
            self.setWindowTitle("Enhanced PIDs (experimental)")
            self.resize(840, 520)
            v = QtWidgets.QVBoxLayout(self)

            warn = QtWidgets.QLabel(
                "⚠ <b>Experimental.</b> Manufacturer (mode 22) PIDs and their formulas are "
                "vehicle-specific and are <b>not validated here</b>. Edit the library file with "
                "values for your vehicle (e.g. from FORScan community PID lists) before trusting "
                "readings. Reads are safe — service $22 is read-only.")
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#DD6B20;")
            v.addWidget(warn)

            cols = ["Brand", "Name", "DID", "Unit", "Formula (a,b,…=bytes)", "Value"]
            self.table = QtWidgets.QTableWidget(len(self.pids), len(cols))
            self.table.setHorizontalHeaderLabels(cols)
            self.table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
            for r, p in enumerate(self.pids):
                for c, val in enumerate([p.brand, p.name, p.did, p.unit, p.formula, ""]):
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem(val))
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setStretchLastSection(True)
            v.addWidget(self.table, 1)

            bar = QtWidgets.QHBoxLayout()
            self.btn_read = QtWidgets.QPushButton("Read with connected adapter")
            self.btn_save = QtWidgets.QPushButton("Save library file…")
            bar.addWidget(self.btn_read)
            bar.addWidget(self.btn_save)
            bar.addStretch(1)
            bar.addWidget(QtWidgets.QLabel(f"<span style='color:#718096'>{self.path}</span>"))
            v.addLayout(bar)
            self.btn_read.clicked.connect(self._read)
            self.btn_save.clicked.connect(self._save)

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)

        def _read(self):
            conn = getattr(self.main.live_tab, "conn", None)
            if conn is None:
                QtWidgets.QMessageBox.information(
                    self, "Enhanced PIDs", "Connect to an adapter in the Live tab first.")
                return
            for r, p in enumerate(self.pids):
                val = self.enh.query_enhanced(conn, p)
                self.table.setItem(
                    r, 5, QtWidgets.QTableWidgetItem("—" if val is None else f"{val:g} {p.unit}"))

        def _save(self):
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.enh.save_library(self.path, self.pids)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
                return
            QtWidgets.QMessageBox.information(
                self, "Saved",
                f"Wrote {self.path}\n\nEdit this JSON to add enhanced PIDs for your vehicle "
                "(name, 16-bit DID, unit, and a formula over data bytes a,b,c…), then reopen "
                "this dialog.")

    # --------------------------------------------------------------------- #
    # Main window
    # --------------------------------------------------------------------- #
    class MaintenanceDialog(QtWidgets.QDialog):
        """Per-vehicle service log, mileage reminders and a fuel/cost log."""

        SERVICE_TYPES = ["Oil change", "Oil filter", "Air filter", "Brake fluid", "Coolant",
                         "Spark plugs", "Trans fluid", "Timing belt", "Tires", "Inspection",
                         "Other"]

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            self.setWindowTitle("Maintenance & Reminders")
            self.resize(660, 640)
            self.path = os.path.join(DEFAULT_LOGS_DIR, "garage.json")
            self.vin = main_window.settings.value("garage/active_vin", "", type=str)
            self.vehicles = garage_mod.load_garage(self.path)
            self.veh = garage_mod.find(self.vehicles, self.vin) if self.vin else None

            v = QtWidgets.QVBoxLayout(self)
            if self.veh is None:
                v.addWidget(QtWidgets.QLabel(
                    "No active vehicle. Open the Garage and Set Active, or read Vehicle Info "
                    "(Live tab) to add your car first."))
                bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
                bb.rejected.connect(self.reject)
                v.addWidget(bb)
                return

            v.addWidget(QtWidgets.QLabel(f"<b>{self.veh.label}</b> &nbsp; · &nbsp; VIN {self.veh.vin}"))
            orow = QtWidgets.QHBoxLayout()
            orow.addWidget(QtWidgets.QLabel("Odometer:"))
            self.odo = QtWidgets.QDoubleSpinBox()
            self.odo.setRange(0, 2_000_000)
            self.odo.setDecimals(0)
            self.odo.setMaximumWidth(150)
            self.odo.setValue(self.veh.odometer or 0)
            orow.addWidget(self.odo)
            bsave = QtWidgets.QPushButton("Save")
            bsave.clicked.connect(self._save_odo)
            orow.addWidget(bsave)
            orow.addStretch(1)
            v.addLayout(orow)

            v.addWidget(QtWidgets.QLabel("<b>Service log &amp; reminders</b>"))
            self.svc_list = QtWidgets.QListWidget()
            v.addWidget(self.svc_list, 1)
            srow = QtWidgets.QHBoxLayout()
            self.svc_type = QtWidgets.QComboBox()
            self.svc_type.addItems(self.SERVICE_TYPES)
            self.svc_type.setEditable(True)
            self.svc_mi = QtWidgets.QDoubleSpinBox()
            self.svc_mi.setRange(0, 2_000_000)
            self.svc_mi.setDecimals(0)
            self.svc_mi.setPrefix("at ")
            self.svc_int = QtWidgets.QDoubleSpinBox()
            self.svc_int.setRange(0, 200_000)
            self.svc_int.setDecimals(0)
            self.svc_int.setPrefix("every ")
            self.svc_cost = QtWidgets.QDoubleSpinBox()
            self.svc_cost.setRange(0, 100_000)
            self.svc_cost.setPrefix("$")
            badd = QtWidgets.QPushButton("Add")
            badd.clicked.connect(self._add_service)
            for w in (self.svc_type, self.svc_mi, self.svc_int, self.svc_cost, badd):
                srow.addWidget(w)
            v.addLayout(srow)

            v.addWidget(QtWidgets.QLabel("<b>Fuel log</b>"))
            self.fuel_stats = QtWidgets.QLabel("")
            self.fuel_stats.setObjectName("Muted")
            v.addWidget(self.fuel_stats)
            frow = QtWidgets.QHBoxLayout()
            self.fuel_mi = QtWidgets.QDoubleSpinBox()
            self.fuel_mi.setRange(0, 2_000_000)
            self.fuel_mi.setDecimals(0)
            self.fuel_mi.setPrefix("at ")
            self.fuel_vol = QtWidgets.QDoubleSpinBox()
            self.fuel_vol.setRange(0, 1000)
            self.fuel_vol.setPrefix("vol ")
            self.fuel_cost = QtWidgets.QDoubleSpinBox()
            self.fuel_cost.setRange(0, 100_000)
            self.fuel_cost.setPrefix("$")
            fadd = QtWidgets.QPushButton("Add fill-up")
            fadd.clicked.connect(self._add_fuel)
            for w in (self.fuel_mi, self.fuel_vol, self.fuel_cost, fadd):
                frow.addWidget(w)
            v.addLayout(frow)

            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            bb.rejected.connect(self.reject)
            bb.accepted.connect(self.accept)
            v.addWidget(bb)
            self._refresh()

        def _save(self):
            garage_mod.save_garage(self.path, self.vehicles)

        def _save_odo(self):
            self.veh.odometer = self.odo.value()
            self._save()
            self._refresh()

        def _add_service(self):
            self.veh.maintenance.append({
                "type": self.svc_type.currentText().strip() or "Service",
                "mileage": self.svc_mi.value(),
                "interval_miles": self.svc_int.value() or None,
                "cost": self.svc_cost.value() or None,
                "date": time.strftime("%Y-%m-%d")})
            self.veh.odometer = max(self.veh.odometer or 0, self.svc_mi.value())
            self.odo.setValue(self.veh.odometer)
            self._save()
            self._refresh()

        def _add_fuel(self):
            self.veh.fuel.append({
                "mileage": self.fuel_mi.value(), "volume": self.fuel_vol.value(),
                "cost": self.fuel_cost.value() or None, "date": time.strftime("%Y-%m-%d")})
            self.veh.odometer = max(self.veh.odometer or 0, self.fuel_mi.value())
            self.odo.setValue(self.veh.odometer)
            self._save()
            self._refresh()

        def _refresh(self):
            self.svc_list.clear()
            for r in self.veh.maintenance:
                line = f"{r.get('date', '')}   {r.get('type')}   at {float(r.get('mileage') or 0):,.0f}"
                if r.get("cost"):
                    line += f"   ${float(r['cost']):,.0f}"
                self.svc_list.addItem(line)
            for d in garage_mod.maintenance_due(self.veh):
                if d["remaining"] is None:
                    continue
                if d["overdue"]:
                    it = QtWidgets.QListWidgetItem(
                        f"⚠  {d['type']} OVERDUE by {abs(d['remaining']):,.0f}")
                    it.setForeground(QtGui.QColor("#E10600"))
                else:
                    it = QtWidgets.QListWidgetItem(
                        f"•  {d['type']} due in {d['remaining']:,.0f}  (at {d['next_due']:,.0f})")
                    it.setForeground(QtGui.QColor("#DD6B20") if d["remaining"] < 1000
                                     else QtGui.QColor("#38A169"))
                self.svc_list.addItem(it)
            st = garage_mod.fuel_stats(self.veh)
            if st:
                txt = (f"{st['fills']} fill-ups · ${st['total_cost']:,.0f} total · "
                       f"{st['total_volume']:,.0f} vol")
                if st.get("vol_per_100"):
                    txt += f" · {st['vol_per_100']:.1f} vol / 100 dist"
                if st.get("cost_per_dist"):
                    txt += f" · ${st['cost_per_dist']:.2f}/dist"
                self.fuel_stats.setText(txt)
            else:
                self.fuel_stats.setText("No fill-ups logged yet.")

    class DashboardPage(QtWidgets.QWidget):
        """Landing page: quick actions, vehicle status and recent logs."""

        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(28, 24, 28, 24)
            outer.setSpacing(16)

            title = QtWidgets.QLabel("Dashboard")
            title.setObjectName("H1")
            outer.addWidget(title)
            sub = QtWidgets.QLabel("Connect to your car or open a log to get started.")
            sub.setObjectName("Muted")
            outer.addWidget(sub)

            row = QtWidgets.QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(self._action_card(
                "🔌  Connect", "Live OBD-II over an ELM327 adapter", "Connect", self._connect, True))
            row.addWidget(self._action_card(
                "📂  Open log", "VCDS / Torque / OBD Fusion CSV", "Open…", self._open_csv, False))
            row.addWidget(self._action_card(
                "📋  Auto-Scan", "Open a VCDS Auto-Scan .TXT", "Open…", self._open_scan, False))
            outer.addLayout(row)

            low = QtWidgets.QHBoxLayout()
            low.setSpacing(14)
            self.veh_card, vbody = self._panel("Active vehicle")
            self.veh_body = QtWidgets.QLabel("—")
            self.veh_body.setWordWrap(True)
            vbody.addWidget(self.veh_body)
            vbody.addStretch(1)
            low.addWidget(self.veh_card, 1)

            rc, rbody = self._panel("Recent logs")
            self.recent_list = QtWidgets.QListWidget()
            self.recent_list.itemActivated.connect(self._open_recent)
            self.recent_list.itemDoubleClicked.connect(self._open_recent)
            rbody.addWidget(self.recent_list)
            low.addWidget(rc, 1)
            outer.addLayout(low, 1)

        def _action_card(self, title, subtitle, btn_text, slot, accent):
            card = QtWidgets.QFrame()
            card.setObjectName("Card")
            card.setMinimumHeight(150)
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(18, 16, 18, 16)
            t = QtWidgets.QLabel(title)
            t.setStyleSheet("font-size:13pt; font-weight:bold;")
            s = QtWidgets.QLabel(subtitle)
            s.setObjectName("Muted")
            s.setWordWrap(True)
            btn = QtWidgets.QPushButton(btn_text)
            if accent:
                btn.setObjectName("Accent")
            btn.clicked.connect(slot)
            v.addWidget(t)
            v.addWidget(s)
            v.addStretch(1)
            v.addWidget(btn, 0, QtCore.Qt.AlignLeft)
            return card

        def _panel(self, heading):
            card = QtWidgets.QFrame()
            card.setObjectName("Card")
            v = QtWidgets.QVBoxLayout(card)
            v.setContentsMargins(18, 14, 18, 14)
            h = QtWidgets.QLabel(heading)
            h.setStyleSheet("font-weight:bold;")
            v.addWidget(h)
            return card, v

        # -- actions -------------------------------------------------------- #
        def _connect(self):
            self.main.show_page("live")
            self.main.live_tab.connect_adapter()

        def _open_csv(self):
            self.main.show_page("files")
            self.main.analyzer.open_csv_dialog()

        def _open_scan(self):
            self.main.show_page("files")
            self.main.analyzer.open_scan_dialog()

        def _open_recent(self, item):
            path = item.data(QtCore.Qt.UserRole)
            if not path:
                return
            self.main.show_page("files")
            if path.lower().endswith((".txt",)):
                self.main.analyzer.load_scan(path)
            else:
                self.main.analyzer.load_csv(path)

        def refresh(self):
            # active vehicle
            vin = self.main.settings.value("garage/active_vin", "", type=str)
            if vin:
                veh = garage_mod.find(
                    garage_mod.load_garage(os.path.join(DEFAULT_LOGS_DIR, "garage.json")), vin)
                self.veh_body.setText(
                    f"{veh.label}\nVIN {veh.vin}" if veh else f"VIN {vin}")
            else:
                self.veh_body.setText("No active vehicle.\nRead Vehicle Info or open the Garage.")
            # recent logs (incl. per-vehicle subfolders)
            self.recent_list.clear()
            base = DEFAULT_LOGS_DIR
            files = []
            try:
                for root, _dirs, names in os.walk(base):
                    for n in names:
                        if n.lower().endswith((".csv", ".txt")):
                            full = os.path.join(root, n)
                            files.append((os.path.getmtime(full), os.path.relpath(full, base), full))
                files.sort(reverse=True)
            except OSError:
                files = []
            if not files:
                self.recent_list.addItem("No logs yet.")
            for _mt, rel, full in files[:12]:
                it = QtWidgets.QListWidgetItem(rel.replace(os.sep, " / "))
                it.setData(QtCore.Qt.UserRole, full)
                self.recent_list.addItem(it)

    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            from vcds_core import __version__ as _ver

            _migrate_legacy_data()
            self._version = _ver
            self.setWindowTitle(f"OBD Toolkit v{_ver}")
            icon = _find_app_icon()
            if icon:
                self.setWindowIcon(QtGui.QIcon(icon))
            self.resize(1280, 800)
            self.setMinimumSize(760, 520)  # usable on small/laptop screens
            self._update_info = None

            central = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(central)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            self.setCentralWidget(central)

            # --- left navigation rail --------------------------------------- #
            self.sidebar = QtWidgets.QFrame()
            self.sidebar.setObjectName("Sidebar")
            self.sidebar.setFixedWidth(186)
            sv = QtWidgets.QVBoxLayout(self.sidebar)
            sv.setContentsMargins(12, 16, 12, 16)
            sv.setSpacing(4)
            brand = QtWidgets.QLabel("⛽ OBD Toolkit")
            brand.setObjectName("Brand")
            sv.addWidget(brand)
            sv.addSpacing(10)
            self._nav = {}
            for key, label in (("dashboard", "▦  Dashboard"), ("files", "📈  Files"),
                               ("live", "⏱  Live"), ("ai", "🤖  AI Assistant")):
                b = QtWidgets.QToolButton()
                b.setObjectName("Nav")
                b.setText(label)
                b.setCheckable(True)
                b.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
                b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
                b.setMinimumHeight(40)
                b.clicked.connect(lambda _c=False, k=key: self.show_page(k))
                sv.addWidget(b)
                self._nav[key] = b
            sv.addStretch(1)
            garage_btn = QtWidgets.QToolButton()
            garage_btn.setObjectName("Nav")
            garage_btn.setText("🚗  Garage")
            garage_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            garage_btn.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            garage_btn.setMinimumHeight(40)
            garage_btn.clicked.connect(self.show_garage)
            sv.addWidget(garage_btn)
            h.addWidget(self.sidebar)

            # --- main content area (banner + stacked pages) ----------------- #
            right = QtWidgets.QWidget()
            cv = QtWidgets.QVBoxLayout(right)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            self.update_banner = UpdateBanner()
            self.update_banner.hide()
            self.update_banner.install.connect(self._install_update)
            self.update_banner.notes.connect(self._open_release_notes)
            self.update_banner.dismiss.connect(self.update_banner.hide)
            cv.addWidget(self.update_banner)
            self.stack = QtWidgets.QStackedWidget()
            cv.addWidget(self.stack, 1)
            h.addWidget(right, 1)

            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            self.dashboard = DashboardPage(self)
            self.analyzer = FileAnalyzerTab()
            self.live_tab = LiveTab(self)
            self.ai_tab = AiAssistantTab(self)
            self._page_index = {}
            for key, widget in (("dashboard", self.dashboard), ("files", self.analyzer),
                                ("live", self.live_tab), ("ai", self.ai_tab)):
                self._page_index[key] = self.stack.addWidget(widget)

            self._build_menu()
            self.show_page("dashboard")
            # carbon (dark) is the default look
            self._apply_theme(self.settings.value("ui/dark", True, type=bool))
            self._apply_units(self.settings.value("ui/units", units.AS_LOGGED, type=str))
            self.statusBar().showMessage(
                f"Logs dir: {DEFAULT_LOGS_DIR}   ·   Press F1 for help"
            )
            # Offer the quick tour on first run, then check for updates — both
            # after the window is shown, and both skipped in headless runs.
            QtCore.QTimer.singleShot(400, self._maybe_first_run_tour)
            QtCore.QTimer.singleShot(1500, self._maybe_startup_update_check)

        def _build_menu(self):
            view_menu = self.menuBar().addMenu("&View")
            self.act_dark = QtGui.QAction("&Dark mode", self)
            self.act_dark.setCheckable(True)
            self.act_dark.setChecked(self.settings.value("ui/dark", True, type=bool))
            self.act_dark.toggled.connect(self._toggle_theme)
            view_menu.addAction(self.act_dark)

            prof_menu = view_menu.addMenu("Vehicle &profile")
            self._profile_group = QtGui.QActionGroup(self)
            current = self.settings.value("ui/profile", profiles.DEFAULT_PROFILE, type=str)
            for pid, prof in profiles.PROFILES.items():
                act = QtGui.QAction(prof.label, self)
                act.setCheckable(True)
                act.setChecked(pid == current)
                act.setData(pid)
                act.triggered.connect(lambda _checked, p=pid: self._set_profile(p))
                self._profile_group.addAction(act)
                prof_menu.addAction(act)

            units_menu = view_menu.addMenu("&Units")
            self._units_group = QtGui.QActionGroup(self)
            cur_units = self.settings.value("ui/units", units.AS_LOGGED, type=str)
            for uid, label in ((units.AS_LOGGED, "As logged"),
                               (units.METRIC, "Metric"), (units.IMPERIAL, "Imperial")):
                a = QtGui.QAction(label, self)
                a.setCheckable(True)
                a.setChecked(uid == cur_units)
                a.setData(uid)
                a.triggered.connect(lambda _c, u=uid: self._set_units(u))
                self._units_group.addAction(a)
                units_menu.addAction(a)

            tools_menu = self.menuBar().addMenu("&Tools")
            mcp_action = QtGui.QAction("Install &MCP Server (for Claude)…", self)
            mcp_action.triggered.connect(self.show_mcp_install)
            tools_menu.addAction(mcp_action)
            enh_action = QtGui.QAction("&Enhanced PIDs (experimental)…", self)
            enh_action.triggered.connect(self.show_enhanced_pids)
            tools_menu.addAction(enh_action)
            garage_action = QtGui.QAction("&Garage…", self)
            garage_action.triggered.connect(self.show_garage)
            tools_menu.addAction(garage_action)
            maint_action = QtGui.QAction("&Maintenance & Reminders…", self)
            maint_action.triggered.connect(self.show_maintenance)
            tools_menu.addAction(maint_action)
            resets_action = QtGui.QAction("&Resets / Service…", self)
            resets_action.triggered.connect(self.show_resets)
            tools_menu.addAction(resets_action)
            ai_settings_action = QtGui.QAction("&AI Settings…", self)
            ai_settings_action.triggered.connect(lambda: self.ai_tab.open_settings())
            tools_menu.addAction(ai_settings_action)
            tools_menu.addSeparator()
            logs_action = QtGui.QAction("Open &logs folder", self)
            logs_action.triggered.connect(lambda: _open_folder(DEFAULT_LOGS_DIR))
            tools_menu.addAction(logs_action)

            help_menu = self.menuBar().addMenu("&Help")
            tour = QtGui.QAction("&Quick Tour", self)
            tour.triggered.connect(lambda: self.show_tour(force=True))
            help_menu.addAction(tour)
            vcds_log = QtGui.QAction("How to &Log in VCDS…", self)
            vcds_log.triggered.connect(self.show_vcds_logging)
            help_menu.addAction(vcds_log)
            guide = QtGui.QAction("User &Guide", self)
            guide.setShortcut(QtGui.QKeySequence.HelpContents)  # F1
            guide.triggered.connect(self.show_help)
            help_menu.addAction(guide)
            help_menu.addSeparator()

            upd = QtGui.QAction("Check for &Updates…", self)
            upd.triggered.connect(lambda: self.check_for_updates(manual=True))
            help_menu.addAction(upd)
            self.act_update_startup = QtGui.QAction("Check for Updates at Startup", self)
            self.act_update_startup.setCheckable(True)
            self.act_update_startup.setChecked(
                self.settings.value("ui/check_updates", True, type=bool)
            )
            self.act_update_startup.toggled.connect(
                lambda v: self.settings.setValue("ui/check_updates", v)
            )
            help_menu.addAction(self.act_update_startup)
            help_menu.addSeparator()

            about = QtGui.QAction("&About", self)
            about.triggered.connect(self.show_about)
            help_menu.addAction(about)

        def _maybe_first_run_tour(self):
            # Never auto-pop a modal in headless/offscreen runs (tests, CI).
            if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
                return
            if self.settings.value("ui/show_tour", True, type=bool):
                self.show_tour()

        def show_tour(self, force: bool = False):
            show_default = self.settings.value("ui/show_tour", True, type=bool)
            QuickTourDialog(self.settings, show_default, self).exec()

        def show_page(self, key: str):
            idx = self._page_index.get(key)
            if idx is None:
                return
            self.stack.setCurrentIndex(idx)
            for k, b in self._nav.items():
                b.setChecked(k == key)
            if key == "ai":
                self.ai_tab.refresh_vehicle()
            elif key == "dashboard":
                self.dashboard.refresh()

        def _apply_theme(self, dark: bool):
            apply_theme(dark)
            self.analyzer.plot.set_theme(dark)
            self.live_tab.plot.set_theme(dark)

        def _toggle_theme(self, on: bool):
            self.settings.setValue("ui/dark", on)
            self._apply_theme(on)

        def _set_profile(self, pid: str):
            self.settings.setValue("ui/profile", pid)
            for a in self._profile_group.actions():
                if a.data() == pid:
                    a.setChecked(True)
            self.statusBar().showMessage(
                f"Vehicle profile: {profiles.get_profile(pid).label}", 4000)

        def _set_units(self, system: str):
            self.settings.setValue("ui/units", system)
            self._apply_units(system)

        def _apply_units(self, system: str):
            self.analyzer.plot.set_unit_system(system)
            self.live_tab.plot.set_unit_system(system)

        def current_profile(self) -> str:
            return self.settings.value("ui/profile", profiles.DEFAULT_PROFILE, type=str)

        def show_mcp_install(self):
            McpInstallDialog(DEFAULT_LOGS_DIR, self).exec()

        def show_enhanced_pids(self):
            EnhancedPidsDialog(self, self).exec()

        def show_garage(self):
            GarageDialog(self, self).exec()

        def show_maintenance(self):
            MaintenanceDialog(self, self).exec()

        def show_resets(self):
            ResetsDialog(self, self).exec()

        def show_help(self):
            HelpDialog(self._version, self).exec()

        def show_vcds_logging(self):
            dlg = QtWidgets.QDialog(self)
            dlg.setWindowTitle("How to Log in VCDS")
            dlg.resize(580, 470)
            lay = QtWidgets.QVBoxLayout(dlg)
            browser = QtWidgets.QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml("<h2>Getting a log file out of VCDS</h2>" + VCDS_LOG_HTML)
            lay.addWidget(browser)
            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(dlg.reject)
            buttons.accepted.connect(dlg.accept)
            lay.addWidget(buttons)
            dlg.exec()

        def show_about(self):
            QtWidgets.QMessageBox.about(
                self,
                "About OBD Toolkit",
                f"<b>OBD Toolkit</b> v{self._version}<br>"
                "Analyze VCDS logs &amp; Auto-Scans and capture live ELM327 "
                "OBD-II data from any OBD-II vehicle.<br><br>"
                "Brand-specific diagnosis is selected via "
                "<b>View → Vehicle profile</b>. Live data is from a generic "
                "ELM327; it does not control VCDS or the HEX cable.<br><br>"
                '<a href="https://github.com/JWalen/VAGScanner">'
                "github.com/JWalen/VAGScanner</a>",
            )

        # -- updates -------------------------------------------------------- #
        def _maybe_startup_update_check(self):
            if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
                return
            if self.settings.value("ui/check_updates", True, type=bool):
                self.check_for_updates(manual=False)

        def check_for_updates(self, manual: bool = False):
            self._update_manual = manual
            self._chk_thread = QtCore.QThread()
            self._chk_worker = UpdateCheckWorker(self._version)
            self._chk_worker.moveToThread(self._chk_thread)
            self._chk_thread.started.connect(self._chk_worker.run)
            self._chk_worker.found.connect(self._on_update_found)
            self._chk_worker.none.connect(self._on_update_none)
            self._chk_worker.failed.connect(self._on_update_failed)
            for sig in (self._chk_worker.found, self._chk_worker.none, self._chk_worker.failed):
                sig.connect(self._chk_thread.quit)
            self._chk_thread.start()

        @QtCore.Slot(object)
        def _on_update_found(self, info):
            self._update_info = info
            self.update_banner.show_update(
                f"Update available — {info.name} (you have v{self._version})."
            )

        @QtCore.Slot()
        def _on_update_none(self):
            if getattr(self, "_update_manual", False):
                QtWidgets.QMessageBox.information(
                    self, "Up to date", f"You're running the latest version (v{self._version})."
                )

        @QtCore.Slot(str)
        def _on_update_failed(self, msg):
            if getattr(self, "_update_manual", False):
                QtWidgets.QMessageBox.warning(
                    self, "Update check failed", f"Could not check for updates:\n{msg}"
                )

        def _open_release_notes(self):
            if self._update_info and self._update_info.html_url:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl(self._update_info.html_url))

        def _install_update(self):
            info = self._update_info
            if not info:
                return
            if not info.installer_url:
                QtWidgets.QMessageBox.information(
                    self, "Update",
                    "This release has no installer asset. Opening the releases page.",
                )
                self._open_release_notes()
                return
            import tempfile

            dest = os.path.join(tempfile.gettempdir(), "vcds_toolkit_update")
            self._dl_dialog = QtWidgets.QProgressDialog(
                "Downloading update…", "Cancel", 0, 100, self
            )
            self._dl_dialog.setWindowTitle("Updating")
            self._dl_dialog.setWindowModality(QtCore.Qt.WindowModal)
            self._dl_dialog.setMinimumDuration(0)
            self._dl_dialog.setAutoClose(False)
            self._dl_dialog.setAutoReset(False)

            self._dl_thread = QtCore.QThread()
            self._dl_worker = UpdateDownloadWorker(info, dest)
            self._dl_worker.moveToThread(self._dl_thread)
            self._dl_thread.started.connect(self._dl_worker.run)
            self._dl_worker.progress.connect(self._on_dl_progress)
            self._dl_worker.done.connect(self._on_dl_done)
            self._dl_worker.failed.connect(self._on_dl_failed)
            self._dl_worker.done.connect(self._dl_thread.quit)
            self._dl_worker.failed.connect(self._dl_thread.quit)
            self._dl_dialog.canceled.connect(self._dl_worker.cancel)
            self._dl_thread.start()

        @QtCore.Slot(int, int)
        def _on_dl_progress(self, done, total):
            if total > 0:
                self._dl_dialog.setValue(int(done * 100 / total))
            else:
                self._dl_dialog.setLabelText(f"Downloading update… {done // 1024} KB")

        @QtCore.Slot(str)
        def _on_dl_done(self, path):
            self._dl_dialog.close()
            ok = QtWidgets.QMessageBox.question(
                self,
                "Install update",
                "The update has downloaded. OBD Toolkit will close, update "
                "automatically in the background, and reopen.\n\nInstall now?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if ok != QtWidgets.QMessageBox.Yes:
                return
            relaunch = sys.executable if getattr(sys, "frozen", False) else None
            try:
                updater.launch_installer(path, silent=True, relaunch=relaunch)
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Launch failed", str(exc))
                return
            QtWidgets.QApplication.quit()

        @QtCore.Slot(str)
        def _on_dl_failed(self, msg):
            self._dl_dialog.close()
            if "cancel" in msg.lower():
                return
            QtWidgets.QMessageBox.warning(self, "Download failed", msg)

        def open_in_analyzer(self, path: str):
            self.analyzer.load_csv(path)
            self.show_page("files")


def _fmt_num(x):
    if x is None:
        return "—"
    return f"{x:g}" if isinstance(x, float) else str(x)


def _esc_br(s: str) -> str:
    return _html.escape(s or "").replace("\n", "<br>")


def _md_inline(s: str) -> str:
    s = _html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code style='font-family:Consolas,monospace'>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__([^_]+)__", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\*\w])\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<a href='\2'>\1</a>", s)
    return s


def _md_to_html(text: str) -> str:
    """Minimal Markdown -> HTML for rendering AI replies (headings, lists, code, etc.)."""
    out, code, in_code, list_type = [], [], False, None

    def close_list():
        nonlocal list_type
        if list_type:
            out.append(f"</{list_type}>")
            list_type = None

    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre style='font-family:Consolas,monospace;border:1px solid #8888;"
                           "padding:6px'>" + _html.escape("\n".join(code)) + "</pre>")
                code, in_code = [], False
            else:
                close_list()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        st = line.strip()
        if not st:
            close_list()
            out.append("<div style='height:6px'></div>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", st)
        if m:
            close_list()
            out.append(f"<div><b>{_md_inline(m.group(2))}</b></div>")
            continue
        m = re.match(r"^[-*+]\s+(.*)$", st)
        if m:
            if list_type != "ul":
                close_list()
                out.append("<ul style='margin:2px 0 2px 18px'>")
                list_type = "ul"
            out.append(f"<li>{_md_inline(m.group(1))}</li>")
            continue
        m = re.match(r"^\d+\.\s+(.*)$", st)
        if m:
            if list_type != "ol":
                close_list()
                out.append("<ol style='margin:2px 0 2px 18px'>")
                list_type = "ol"
            out.append(f"<li>{_md_inline(m.group(1))}</li>")
            continue
        close_list()
        out.append(f"<div>{_md_inline(line)}</div>")
    if in_code and code:
        out.append("<pre>" + _html.escape("\n".join(code)) + "</pre>")
    close_list()
    return "".join(out)


def _safe_session_name(name: str):
    """Sanitize a user-entered session name to a safe file stem, or None (auto)."""
    name = (name or "").strip()
    if not name:
        return None
    if name.lower().endswith(".csv"):
        name = name[:-4]
    import re

    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" ._")
    return name or None


def _grab_png(widget) -> bytes:
    """Render a Qt widget to PNG bytes (for embedding the plot in a report)."""
    pixmap = widget.grab()
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def _find_app_icon() -> Optional[str]:
    """Locate app.ico in the PyInstaller bundle or the source tree, else None."""
    candidates = []
    base = getattr(sys, "_MEIPASS", None)  # set when frozen by PyInstaller
    if base:
        candidates.append(os.path.join(base, "app.ico"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "..", "installer", "app.ico"))
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _export_clip(mlog: "parse.MeasuringLog", path: str, xmin: float, xmax: float) -> int:
    """Write the samples whose time falls in [xmin, xmax] to a flat-layout CSV.

    Channels keep their own values; rows are indexed against the longest series'
    time axis (the common case of a single shared time axis exports exactly).
    """
    # choose the channel with the most samples as the time reference
    ref_name = max(mlog.raw_series, key=lambda n: len(mlog.raw_series[n]["time"]))
    ref_t = mlog.raw_series[ref_name]["time"]
    channels = [live.LiveChannel(c.name, c.unit) for c in mlog.channels]
    rows: List[Tuple[str, float, Dict[str, Optional[float]]]] = []
    for i, t in enumerate(ref_t):
        if t < xmin or t > xmax:
            continue
        values: Dict[str, Optional[float]] = {}
        for c in mlog.channels:
            vals = mlog.raw_series[c.name]["value"]
            values[c.name] = vals[i] if i < len(vals) else None
        rows.append(("", t, values))
    live.write_measuring_csv(path, channels, rows)
    return len(rows)


def main() -> int:
    """Console entry point: launch the desktop GUI."""
    if not _HAVE_QT:
        sys.stderr.write(
            "The GUI needs PySide6 and pyqtgraph. Install with:\n"
            "    pip install 'vcds-toolkit[gui]'\n"
            f"(import error: {_IMPORT_ERR})\n"
        )
        return 1
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
