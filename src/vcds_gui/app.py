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
from typing import Dict, List, Optional, Tuple

from vcds_core import parse
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
            self.lbl_info = QtWidgets.QLabel("No file loaded.")
            for w in (self.btn_open, self.btn_scan, self.chk_norm, self.btn_export):
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
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(self, "Parse error", str(exc))
                return
            self.plot.clear()
            self.chan_list.blockSignals(True)
            self.chan_list.clear()
            for ch in self.mlog.channels:
                rs = self.mlog.raw_series[ch.name]
                color = self.plot.add_channel(ch.name, rs["time"], rs["value"], ch.unit)
                item = QtWidgets.QListWidgetItem(f"{ch.name}  [{ch.unit}]")
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked)
                item.setData(QtCore.Qt.UserRole, ch.name)
                item.setForeground(QtGui.QColor(color))
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
                    node.addChild(child)
                self.scan_tree.addTopLevelItem(node)
                node.setExpanded(True)

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
            lv.addWidget(QtWidgets.QLabel("<b>Supported PIDs</b>"))
            self.pid_list = QtWidgets.QListWidget()
            lv.addWidget(self.pid_list, 2)

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
            lv.addWidget(trig_box)

            dtc_box = QtWidgets.QGroupBox("Stored DTCs")
            dv = QtWidgets.QVBoxLayout(dtc_box)
            self.dtc_list = QtWidgets.QListWidget()
            dv.addWidget(self.dtc_list)
            dbar = QtWidgets.QHBoxLayout()
            self.btn_read_dtc = QtWidgets.QPushButton("Read DTCs")
            self.btn_clear_dtc = QtWidgets.QPushButton("Clear DTCs…")
            dbar.addWidget(self.btn_read_dtc)
            dbar.addWidget(self.btn_clear_dtc)
            dv.addLayout(dbar)
            lv.addWidget(dtc_box)

            cap_box = QtWidgets.QGroupBox("Captured events (double-click to analyze)")
            cv = QtWidgets.QVBoxLayout(cap_box)
            self.capture_list = QtWidgets.QListWidget()
            cv.addWidget(self.capture_list)
            lv.addWidget(cap_box)

            split.addWidget(left)
            self.plot = PlotPanel()
            split.addWidget(self.plot)
            split.setSizes([340, 760])

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
            self.channels = live.build_channels(supported)
            self.pid_list.clear()
            for ch in self.channels:
                item = QtWidgets.QListWidgetItem(f"{ch.name}  [{ch.unit}]")
                item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.Checked)
                item.setData(QtCore.Qt.UserRole, ch.name)
                self.pid_list.addItem(item)
            self.conn_status.setText(
                f"<span style='color:#38A169'>Connected</span> — {self.conn.protocol()} "
                f"({len(supported)} PIDs)"
            )
            self._set_connected(True)

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
            self.dtc_list.clear()
            try:
                dtcs = live.read_dtcs(self.conn)
            except Exception as exc:  # noqa: BLE001
                self.dtc_list.addItem(f"Error: {exc}")
                return
            if not dtcs:
                self.dtc_list.addItem("No stored DTCs.")
            for code, desc in dtcs:
                self.dtc_list.addItem(f"{code} — {desc}")

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
    # Main window
    # --------------------------------------------------------------------- #
    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self):
            super().__init__()
            from vcds_core import __version__ as _ver

            self.setWindowTitle(f"VCDS Toolkit v{_ver}")
            icon = _find_app_icon()
            if icon:
                self.setWindowIcon(QtGui.QIcon(icon))
            self.resize(1280, 800)
            self.tabs = QtWidgets.QTabWidget()
            self.setCentralWidget(self.tabs)
            self.analyzer = FileAnalyzerTab()
            self.live_tab = LiveTab(self)
            self.tabs.addTab(self.analyzer, "File Analyzer")
            self.tabs.addTab(self.live_tab, "Live (OBD-II)")
            self.statusBar().showMessage(f"Logs dir: {DEFAULT_LOGS_DIR}")

        def open_in_analyzer(self, path: str):
            self.analyzer.load_csv(path)
            self.tabs.setCurrentWidget(self.analyzer)


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
