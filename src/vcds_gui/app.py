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
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from vcds_core import compare as compare_mod
from vcds_core import compute, knowledge, parse, perform
from vcds_core.diagnose import diagnose as run_diagnose
from vcds_core.diagnose import report_to_text
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


DEFAULT_LOGS_DIR = os.environ.get("VCDS_LOGS_DIR", r"C:\Ross-Tech\VCDS\Logs")

# Distinct trace colours cycled across channels.
_PALETTE = [
    "#0066CC", "#E53E3E", "#38A169", "#DD6B20", "#00C9A7", "#805AD5",
    "#D69E2E", "#3182CE", "#DD2C8B", "#2C7A7B", "#9F7AEA", "#718096",
]


if _HAVE_QT:
    pg.setConfigOptions(antialias=True, background="w", foreground="k")

    # --------------------------------------------------------------------- #
    # Shared plotting widget
    # --------------------------------------------------------------------- #
    class PlotPanel(QtWidgets.QWidget):
        """A pyqtgraph plot with normalization and a value-reading crosshair."""

        cursorMoved = QtCore.Signal(float)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.normalize = True
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

        def _scaled(self, entry: dict) -> List[float]:
            v = entry["v"]
            if not self.normalize:
                return v
            lo, hi = entry["vmin"], entry["vmax"]
            span = (hi - lo) or 1.0
            return [(x - lo) / span for x in v]

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

        def set_cursor(self, x: float):
            self.vline.setPos(x)
            lines = [f"<b>t = {x:.3f} s</b>"]
            for name, entry in self.channels.items():
                if not entry["visible"] or not entry["t"]:
                    continue
                val = self._value_at(entry, x)
                if val is None:
                    continue
                unit = f" {entry['unit']}" if entry["unit"] else ""
                lines.append(
                    f"<span style='color:{entry['color']}'>&#9632;</span> "
                    f"{name}: <b>{val:g}</b>{unit}"
                )
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

            # toolbar
            bar = QtWidgets.QHBoxLayout()
            self.btn_open = QtWidgets.QPushButton("Open Measuring CSV…")
            self.btn_scan = QtWidgets.QPushButton("Open Auto-Scan…")
            self.chk_norm = QtWidgets.QCheckBox("Normalize")
            self.chk_norm.setChecked(True)
            self.btn_export = QtWidgets.QPushButton("Export View…")
            self.btn_diagnose = QtWidgets.QPushButton("🔍 Diagnose")
            self.btn_diagnose.setToolTip("Analyze the loaded log and/or Auto-Scan for likely faults")
            self.btn_perf = QtWidgets.QPushButton("📈 Performance")
            self.btn_perf.setToolTip("Acceleration runs, pulls and an estimated power figure")
            self.btn_compare = QtWidgets.QPushButton("⇄ Compare…")
            self.btn_compare.setToolTip("Open a second log and compare it (before/after)")
            self.lbl_info = QtWidgets.QLabel("No file loaded.")
            for w in (self.btn_open, self.btn_scan, self.btn_diagnose, self.btn_perf,
                      self.btn_compare, self.chk_norm, self.btn_export):
                bar.addWidget(w)
            bar.addWidget(self.lbl_info, 1)
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

            # center: shared plot
            self.plot = PlotPanel()
            split.addWidget(self.plot)

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
            self.btn_export.clicked.connect(self.export_view)
            self.btn_diagnose.clicked.connect(self.run_diagnosis)
            self.btn_perf.clicked.connect(self.run_performance)
            self.btn_compare.clicked.connect(self.open_compare)
            self.chan_list.itemChanged.connect(self._chan_toggled)
            self.btn_find.clicked.connect(lambda: self.run_events(use_rules=False))
            self.btn_apply_rules.clicked.connect(lambda: self.run_events(use_rules=True))
            self.btn_add_rule.clicked.connect(self._add_rule)
            self.btn_clear_rules.clicked.connect(self._clear_rules)
            self.event_list.itemClicked.connect(self._event_clicked)

        # -- loading -------------------------------------------------------- #
        def open_csv_dialog(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open VCDS Measuring CSV", DEFAULT_LOGS_DIR, "CSV files (*.csv *.CSV);;All files (*)"
            )
            if path:
                self.load_csv(path)

        def load_csv(self, path: str):
            try:
                self.mlog = parse.parse_measuring_log(path)
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

        def open_scan_dialog(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Open VCDS Auto-Scan", DEFAULT_LOGS_DIR, "Text files (*.txt *.TXT);;All files (*)"
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
            report = run_diagnose(scan=self.scan, log=self.mlog)
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

        def __init__(self, logger: "live.LiveLogger", duration_s: float, trigger):
            super().__init__()
            self.logger = logger
            self.duration_s = duration_s
            self.trigger = trigger

        @QtCore.Slot()
        def run(self):
            try:
                result = self.logger.run(
                    self.duration_s,
                    trigger=self.trigger,
                    on_sample=lambda t, vals, marker: self.sample.emit(t, dict(vals), marker),
                )
                self.finished.emit(result)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

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
            self._build()

        def _build(self):
            outer = QtWidgets.QVBoxLayout(self)

            # connection bar
            conn_box = QtWidgets.QGroupBox("Adapter")
            cb = QtWidgets.QHBoxLayout(conn_box)
            self.port_combo = QtWidgets.QComboBox()
            self.port_combo.setEditable(True)
            self.port_combo.setMinimumWidth(160)
            self.btn_refresh = QtWidgets.QPushButton("Scan Ports")
            self.baud_combo = QtWidgets.QComboBox()
            self.baud_combo.addItems(["Auto", "38400", "9600", "115200"])
            self.btn_connect = QtWidgets.QPushButton("Connect")
            self.btn_disconnect = QtWidgets.QPushButton("Disconnect")
            self.btn_disconnect.setEnabled(False)
            self.conn_status = QtWidgets.QLabel("Not connected.")
            for w in (QtWidgets.QLabel("Port:"), self.port_combo, self.btn_refresh,
                      QtWidgets.QLabel("Baud:"), self.baud_combo,
                      self.btn_connect, self.btn_disconnect):
                cb.addWidget(w)
            cb.addWidget(self.conn_status, 1)
            outer.addWidget(conn_box)

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
            dbar = QtWidgets.QHBoxLayout()
            self.btn_read_dtc = QtWidgets.QPushButton("Read DTCs")
            self.btn_clear_dtc = QtWidgets.QPushButton("Clear DTCs…")
            dbar.addWidget(self.btn_read_dtc)
            dbar.addWidget(self.btn_clear_dtc)
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
            run_bar = QtWidgets.QHBoxLayout()
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
            self.btn_start = QtWidgets.QPushButton("Start Logging")
            self.btn_stop = QtWidgets.QPushButton("Stop")
            self.btn_stop.setEnabled(False)
            run_bar.addWidget(self.btn_start)
            run_bar.addWidget(self.btn_stop)
            self.run_status = QtWidgets.QLabel("")
            run_bar.addWidget(self.run_status, 1)
            outer.addLayout(run_bar)

            # signals
            self.btn_refresh.clicked.connect(self.scan_ports)
            self.btn_connect.clicked.connect(self.connect_adapter)
            self.btn_disconnect.clicked.connect(self.disconnect_adapter)
            self.btn_add_trig.clicked.connect(self._add_trigger_rule)
            self.btn_read_dtc.clicked.connect(self.read_dtcs)
            self.btn_clear_dtc.clicked.connect(self.clear_dtcs)
            self.btn_start.clicked.connect(self.start_logging)
            self.btn_stop.clicked.connect(self.stop_logging)
            self.capture_list.itemDoubleClicked.connect(self._open_capture)

            self.scan_ports()
            self._set_connected(False)

        # -- ports / connection -------------------------------------------- #
        def scan_ports(self):
            self.port_combo.clear()
            self.port_combo.addItems(live.scan_ports())

        def _baud(self) -> Optional[int]:
            txt = self.baud_combo.currentText()
            return None if txt == "Auto" else int(txt)

        def connect_adapter(self):
            port = self.port_combo.currentText().strip() or None
            self.conn_status.setText("Connecting…")
            QtWidgets.QApplication.processEvents()
            try:
                self.conn = live.connect(port=port, baud=self._baud())
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
            self.conn_status.setText(
                f"<span style='color:#38A169'>Connected</span> — {self.conn.protocol()} "
                f"({len(supported)} PIDs)"
            )
            self._set_connected(True)

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

        def disconnect_adapter(self):
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
            for w in (self.btn_start, self.btn_read_dtc, self.btn_clear_dtc):
                w.setEnabled(on)

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

        def start_logging(self):
            if self.conn is None:
                return
            channels = self._selected_channels()
            self.plot.clear()
            for ch in channels:
                self.plot.add_channel(ch.name, [], [], ch.unit)
            logs_dir = DEFAULT_LOGS_DIR
            self.logger = live.LiveLogger(self.conn, channels, logs_dir, sample_rate_hz=self.rate_spin.value())

            self.thread = QtCore.QThread()
            self.worker = LiveWorker(self.logger, float(self.dur_spin.value()), self._build_trigger())
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

        @QtCore.Slot(float, dict, str)
        def _on_sample(self, t, values, marker):
            self.plot.append_sample(t, values)
            if marker:
                self.run_status.setText(f"Logging… (event at t={t:.1f}s)")

        @QtCore.Slot(object)
        def _on_finished(self, result):
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.run_status.setText(
                f"Saved {os.path.basename(result.session_file)} ({result.sample_count} samples)."
            )
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
          (Auto, or 38400 / 9600 / 115200 for clones), then <b>Connect</b>.</li>
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

            buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
            buttons.rejected.connect(self.reject)
            buttons.accepted.connect(self.accept)
            v.addWidget(buttons)
            self._analyze()

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
    # Tab 3 — AI Assistant
    # --------------------------------------------------------------------- #
    class AiChatWorker(QtCore.QObject):
        done = QtCore.Signal(str)
        failed = QtCore.Signal(str)

        def __init__(self, provider, key, model, system, messages):
            super().__init__()
            self.provider = provider
            self.key = key
            self.model = model
            self.system = system
            self.messages = messages

        @QtCore.Slot()
        def run(self):
            try:
                reply = ai.chat(self.provider, self.key, self.model, self.system, self.messages)
                self.done.emit(reply)
            except Exception as exc:  # noqa: BLE001
                self.failed.emit(str(exc))

    class AiAssistantTab(QtWidgets.QWidget):
        def __init__(self, main_window, parent=None):
            super().__init__(parent)
            self.main = main_window
            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            self.history: list = []
            self._thread = None
            self._worker = None
            self._build()
            self._load_provider_settings()

        def _build(self):
            v = QtWidgets.QVBoxLayout(self)

            cfg = QtWidgets.QHBoxLayout()
            cfg.addWidget(QtWidgets.QLabel("Provider:"))
            self.provider_combo = QtWidgets.QComboBox()
            for pid, prov in ai.PROVIDERS.items():
                self.provider_combo.addItem(prov.label, pid)
            cfg.addWidget(self.provider_combo)
            cfg.addWidget(QtWidgets.QLabel("Model:"))
            self.model_combo = QtWidgets.QComboBox()
            self.model_combo.setEditable(True)
            self.model_combo.setMinimumWidth(190)
            cfg.addWidget(self.model_combo)
            cfg.addWidget(QtWidgets.QLabel("API key:"))
            self.key_edit = QtWidgets.QLineEdit()
            self.key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
            self.key_edit.setMinimumWidth(220)
            cfg.addWidget(self.key_edit, 1)
            self.btn_save_key = QtWidgets.QPushButton("Save")
            cfg.addWidget(self.btn_save_key)
            self.key_link = QtWidgets.QLabel()
            self.key_link.setOpenExternalLinks(True)
            cfg.addWidget(self.key_link)
            v.addLayout(cfg)

            opts = QtWidgets.QHBoxLayout()
            self.chk_context = QtWidgets.QCheckBox("Include current scan/log data as context")
            self.chk_context.setChecked(True)
            opts.addWidget(self.chk_context)
            opts.addStretch(1)
            self.btn_clear_chat = QtWidgets.QPushButton("Clear chat")
            opts.addWidget(self.btn_clear_chat)
            v.addLayout(opts)

            self.conversation = QtWidgets.QTextBrowser()
            self.conversation.setOpenExternalLinks(True)
            v.addWidget(self.conversation, 1)

            entry = QtWidgets.QHBoxLayout()
            self.input = QtWidgets.QPlainTextEdit()
            self.input.setPlaceholderText("Ask about the vehicle…  (Ctrl+Enter to send)")
            self.input.setMaximumHeight(90)
            entry.addWidget(self.input, 1)
            self.btn_send = QtWidgets.QPushButton("Send")
            entry.addWidget(self.btn_send)
            v.addLayout(entry)

            self.provider_combo.currentIndexChanged.connect(self._provider_changed)
            self.btn_save_key.clicked.connect(self._save_key)
            self.btn_send.clicked.connect(self.send)
            self.btn_clear_chat.clicked.connect(self._clear_chat)
            send_sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self.input)
            send_sc.activated.connect(self.send)

            self._render_intro()

        # -- settings ------------------------------------------------------- #
        def _provider_changed(self):
            pid = self.provider_combo.currentData()
            prov = ai.PROVIDERS[pid]
            self.model_combo.clear()
            self.model_combo.addItems(prov.models)
            saved_model = self.settings.value(f"ai/model/{pid}", prov.default_model, type=str)
            self.model_combo.setCurrentText(saved_model)
            self.key_edit.setText(self.settings.value(f"ai/key/{pid}", "", type=str))
            self.key_link.setText(f"<a href='{prov.key_url}'>Get a key</a>")
            self.settings.setValue("ai/provider", pid)

        def _load_provider_settings(self):
            pid = self.settings.value("ai/provider", "anthropic", type=str)
            idx = self.provider_combo.findData(pid)
            self.provider_combo.setCurrentIndex(max(0, idx))
            self._provider_changed()

        def _save_key(self):
            pid = self.provider_combo.currentData()
            self.settings.setValue(f"ai/key/{pid}", self.key_edit.text().strip())
            self.settings.setValue(f"ai/model/{pid}", self.model_combo.currentText().strip())
            QtWidgets.QMessageBox.information(
                self, "Saved",
                "API key saved for this provider.\n\nNote: it is stored locally in your "
                "user settings (not encrypted).",
            )

        # -- chat ----------------------------------------------------------- #
        def _render_intro(self):
            self.conversation.setHtml(
                "<p style='color:#718096'>Ask the assistant to help diagnose your car. "
                "It can use the scan/log currently open in the File Analyzer tab as "
                "context. Pick a provider, paste an API key, and Save.</p>"
            )

        def _clear_chat(self):
            self.history = []
            self._render_intro()

        def _append(self, who: str, text: str, color: str):
            safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    .replace("\n", "<br>"))
            self.conversation.append(
                f"<p><b style='color:{color}'>{who}:</b><br>{safe}</p>"
            )

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
            pid = self.provider_combo.currentData()
            key = self.key_edit.text().strip()
            if not key:
                QtWidgets.QMessageBox.warning(self, "No API key", "Enter and Save an API key first.")
                return
            model = self.model_combo.currentText().strip()
            self.history.append({"role": "user", "content": text})
            self._append("You", text, "#0066CC")
            self.input.clear()
            self.btn_send.setEnabled(False)
            self.conversation.append("<p style='color:#718096'><i>Thinking…</i></p>")

            system = ai.vehicle_system_prompt(self._build_context())
            self._thread = QtCore.QThread()
            self._worker = AiChatWorker(pid, key, model, system, list(self.history))
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.done.connect(self._on_reply)
            self._worker.failed.connect(self._on_error)
            self._worker.done.connect(self._thread.quit)
            self._worker.failed.connect(self._thread.quit)
            self._thread.start()

        @QtCore.Slot(str)
        def _on_reply(self, reply):
            self.history.append({"role": "assistant", "content": reply})
            self._append("Assistant", reply, "#00897B")
            self.btn_send.setEnabled(True)

        @QtCore.Slot(str)
        def _on_error(self, msg):
            self.conversation.append(
                f"<p style='color:#E53E3E'><b>Error:</b> {msg}</p>"
            )
            self.btn_send.setEnabled(True)

    # --------------------------------------------------------------------- #
    # Main window
    # --------------------------------------------------------------------- #
    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            from vcds_core import __version__ as _ver

            self._version = _ver
            self.setWindowTitle(f"VCDS Toolkit v{_ver}")
            icon = _find_app_icon()
            if icon:
                self.setWindowIcon(QtGui.QIcon(icon))
            self.resize(1280, 800)
            self._update_info = None

            central = QtWidgets.QWidget()
            cv = QtWidgets.QVBoxLayout(central)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            self.update_banner = UpdateBanner()
            self.update_banner.hide()
            self.update_banner.install.connect(self._install_update)
            self.update_banner.notes.connect(self._open_release_notes)
            self.update_banner.dismiss.connect(self.update_banner.hide)
            cv.addWidget(self.update_banner)
            self.tabs = QtWidgets.QTabWidget()
            cv.addWidget(self.tabs, 1)
            self.setCentralWidget(central)

            self.analyzer = FileAnalyzerTab()
            self.live_tab = LiveTab(self)
            self.ai_tab = AiAssistantTab(self)
            self.tabs.addTab(self.analyzer, "File Analyzer")
            self.tabs.addTab(self.live_tab, "Live (OBD-II)")
            self.tabs.addTab(self.ai_tab, "AI Assistant")

            self.settings = QtCore.QSettings("DeltaModTech", "VCDS Toolkit")
            self._build_menu()
            self.statusBar().showMessage(
                f"Logs dir: {DEFAULT_LOGS_DIR}   ·   Press F1 for help"
            )
            # Offer the quick tour on first run, then check for updates — both
            # after the window is shown, and both skipped in headless runs.
            QtCore.QTimer.singleShot(400, self._maybe_first_run_tour)
            QtCore.QTimer.singleShot(1500, self._maybe_startup_update_check)

        def _build_menu(self):
            tools_menu = self.menuBar().addMenu("&Tools")
            mcp_action = QtGui.QAction("Install &MCP Server (for Claude)…", self)
            mcp_action.triggered.connect(self.show_mcp_install)
            tools_menu.addAction(mcp_action)

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

        def show_mcp_install(self):
            McpInstallDialog(DEFAULT_LOGS_DIR, self).exec()

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
                "About VCDS Toolkit",
                f"<b>VCDS Toolkit</b> v{self._version}<br>"
                "Analyze VCDS logs &amp; Auto-Scans and capture live ELM327 "
                "OBD-II data.<br><br>"
                "Reads the files VCDS writes; it does not control VCDS or the "
                "HEX cable. Live data is from a generic ELM327 only.<br><br>"
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
                "The update has downloaded. VCDS Toolkit will close and the "
                "installer will run.\n\nInstall now?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if ok != QtWidgets.QMessageBox.Yes:
                return
            try:
                updater.launch_installer(path)
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
            self.tabs.setCurrentWidget(self.analyzer)


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
