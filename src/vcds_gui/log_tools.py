"""Function-calling tools for the AI assistant.

Two groups, so the assistant can actually troubleshoot:
  * FILE tools (always available) — browse/read/diagnose stored logs, look up
    DTCs, find events, performance — confined to the logs folder.
  * LIVE tools (available when an ELM327 is connected in the Live tab) — adapter
    status, live DTCs, a PID snapshot, VIN/cal-IDs and emissions readiness.

The executor is built with the logs folder, the active brand profile and a
``conn_getter`` that returns the current live connection (or None).
"""

from __future__ import annotations

import os
from typing import Optional

from vcds_core import compute, knowledge, parse, perform, vin
from vcds_core.diagnose import diagnose

_FILE_TOOLS = [
    {"name": "list_logs",
     "description": "List the stored log files (measuring CSVs and Auto-Scan TXTs), newest first.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "read_log",
     "description": "Read a measuring CSV: format, channels and per-channel min/max/mean.",
     "parameters": {"type": "object",
                    "properties": {"filename": {"type": "string"}}, "required": ["filename"]}},
    {"name": "read_autoscan",
     "description": "Read a VCDS Auto-Scan TXT: VIN, modules and faults.",
     "parameters": {"type": "object",
                    "properties": {"filename": {"type": "string"}}, "required": ["filename"]}},
    {"name": "diagnose_log",
     "description": "Diagnose a measuring log and/or Auto-Scan into prioritized findings with causes.",
     "parameters": {"type": "object",
                    "properties": {"filename": {"type": "string"}, "autoscan": {"type": "string"}},
                    "required": []}},
    {"name": "find_events",
     "description": "Find events in a measuring log (heuristics, or threshold rules like "
                    "[{channel, op, value}]).",
     "parameters": {"type": "object",
                    "properties": {"filename": {"type": "string"},
                                   "rules": {"type": "array", "items": {"type": "object"}}},
                    "required": ["filename"]}},
    {"name": "performance",
     "description": "Acceleration runs, pulls and an estimated power figure for a measuring log.",
     "parameters": {"type": "object",
                    "properties": {"filename": {"type": "string"},
                                   "mass_kg": {"type": "number"}}, "required": ["filename"]}},
    {"name": "lookup_dtc",
     "description": "Look up a diagnostic trouble code: description, severity, likely causes.",
     "parameters": {"type": "object",
                    "properties": {"code": {"type": "string"}}, "required": ["code"]}},
]

_LIVE_TOOLS = [
    {"name": "obd_status",
     "description": "Live adapter status: connected protocol and supported PIDs (needs the car connected).",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "read_live_dtcs",
     "description": "Read current stored DTCs from the connected car, with descriptions and causes.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "snapshot_pids",
     "description": "One-shot read of current live PID values from the connected car.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "vehicle_info",
     "description": "Read VIN + calibration IDs from the connected car (decodes make/year).",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "readiness",
     "description": "Read emissions-readiness monitors and permanent DTCs from the connected car.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]

TOOL_SPECS = _FILE_TOOLS + _LIVE_TOOLS


def make_executor(logs_dir: str, profile: str = "generic", conn_getter=None):
    """Return ``execute(name, args) -> dict`` for the assistant's tools."""

    def _safe(filename: str) -> str:
        if (not filename or os.path.isabs(filename)
                or ".." in filename.replace("\\", "/").split("/")):
            raise ValueError(f"Illegal filename: {filename!r}")
        base = os.path.realpath(logs_dir)  # resolve symlinks so they can't escape
        full = os.path.realpath(os.path.join(base, filename))
        try:
            contained = os.path.commonpath(
                [os.path.normcase(full), os.path.normcase(base)]) == os.path.normcase(base)
        except ValueError:
            contained = False
        if not contained:
            raise ValueError("Path escapes the logs folder.")
        if not os.path.isfile(full):
            raise ValueError(f"File not found: {filename!r}")
        return full

    def _conn():
        return conn_getter() if conn_getter else None

    # --- file tools -------------------------------------------------------- #
    def _list():
        base = os.path.abspath(logs_dir)
        rows = []
        if os.path.isdir(base):
            for root, _dirs, names in os.walk(base):
                for name in names:
                    if not name.lower().endswith((".csv", ".txt")):
                        continue
                    full = os.path.join(root, name)
                    st = os.stat(full)
                    rows.append({"filename": os.path.relpath(full, base),
                                 "kind": parse.classify_file(full),
                                 "size": st.st_size, "mtime": st.st_mtime})
        rows.sort(key=lambda r: r["mtime"], reverse=True)
        return {"logs_dir": base, "count": len(rows), "files": rows}

    def _read_log(fn):
        log = parse.parse_measuring_log(_safe(fn))
        return {"file": os.path.basename(log.file), "format": log.format_guess,
                "duration_s": log.duration_s, "sample_count": log.sample_count,
                "channels": [{"name": c.name, "unit": c.unit, "min": c.min, "max": c.max,
                              "mean": c.mean} for c in log.channels]}

    def _read_scan(fn):
        scan = parse.parse_autoscan(_safe(fn))
        return {"vin": scan.vin, "mileage": scan.mileage, "total_faults": scan.total_faults,
                "modules": [{"address": m.address, "name": m.name,
                             "faults": [{"code": f.code, "description": f.description,
                                         "status_detail": f.status_detail} for f in m.faults]}
                            for m in scan.modules]}

    def _diagnose(fn, autoscan):
        log = scan = None
        if autoscan:
            scan = parse.parse_autoscan(_safe(autoscan))
        if fn:
            log = parse.parse_measuring_log(_safe(fn))
            compute.add_computed_channels(log)
        if log is None and scan is None:
            return {"error": "Provide filename and/or autoscan."}
        r = diagnose(scan=scan, log=log, profile=profile)
        return {"headline": r.headline, "summary": r.summary,
                "findings": [{"severity": f.severity, "title": f.title, "detail": f.detail,
                              "causes": f.causes} for f in r.findings]}

    def _events(fn, rules):
        log = parse.parse_measuring_log(_safe(fn))
        evs = parse.find_events(log, rules=rules)
        return {"count": len(evs),
                "events": [{"time": e.time, "channel": e.channel, "kind": e.kind,
                            "message": e.message} for e in evs]}

    def _perf(fn, mass_kg):
        log = parse.parse_measuring_log(_safe(fn))
        runs = perform.standard_accel_runs(log)
        pulls = perform.detect_pulls(log)
        est = perform.estimate_power(log, mass_kg or 1850)
        return {"acceleration": [{"from": r.from_speed, "to": r.to_speed, "unit": r.unit,
                                  "seconds": r.elapsed_s} for r in runs],
                "pulls": len(pulls),
                "power": {"peak_hp": est.peak_hp, "peak_torque_nm": est.peak_torque_nm}
                if est else None}

    def _dtc(code):
        k = knowledge.lookup(code, brand=(None if profile == "generic" else profile))
        return {"code": k.code, "description": k.description, "severity": k.severity,
                "system": k.system, "causes": k.causes, "notes": k.notes, "known": k.known}

    # --- live tools -------------------------------------------------------- #
    def _need_conn():
        c = _conn()
        if c is None:
            raise RuntimeError("No adapter connected. Ask the user to connect in the Live tab.")
        return c

    def _obd_status():
        from vcds_obd import live
        c = _need_conn()
        return {"protocol": c.protocol() if hasattr(c, "protocol") else None,
                "status": c.status() if hasattr(c, "status") else None,
                "supported_pids": sorted(c.supported()) if hasattr(c, "supported") else [],
                "log_channels": [ch.name for ch in live.build_channels(c.supported())]}

    def _live_dtcs():
        c = _need_conn()
        out = []
        for code, desc in c.get_dtcs():
            k = knowledge.lookup(code, brand=(None if profile == "generic" else profile))
            out.append({"code": code, "description": desc or k.description,
                        "severity": k.severity, "likely_causes": k.causes})
        return {"count": len(out), "dtcs": out}

    def _snapshot():
        from vcds_obd import live
        c = _need_conn()
        snap = live.snapshot(c, live.build_channels(c.supported()))
        return {"values": snap}

    def _vehicle_info():
        c = _need_conn()
        v = c.read_vin() if hasattr(c, "read_vin") else None
        cals = c.read_calibration_ids() if hasattr(c, "read_calibration_ids") else []
        info = vin.decode_vin(v) if v else None
        return {"vin": v, "make": info.make if info else None,
                "model_year": info.year if info else None,
                "brand_profile": info.brand_profile if info else None, "calibration_ids": cals}

    def _readiness():
        c = _need_conn()
        r = c.read_readiness() if hasattr(c, "read_readiness") else None
        perm = c.read_permanent_dtcs() if hasattr(c, "read_permanent_dtcs") else []
        if r is None:
            return {"error": "Readiness unavailable."}
        incomplete = [m for m, s in r["monitors"].items() if s["available"] and not s["complete"]]
        return {"mil_on": r["mil"], "dtc_count": r["dtc_count"], "incomplete_monitors": incomplete,
                "ready_for_emissions": (not r["mil"]) and not incomplete,
                "permanent_dtcs": [c for c, _ in perm]}

    def execute(name, args):
        try:
            a = args or {}
            if name == "list_logs":
                return _list()
            if name == "read_log":
                return _read_log(a["filename"])
            if name == "read_autoscan":
                return _read_scan(a["filename"])
            if name == "diagnose_log":
                return _diagnose(a.get("filename"), a.get("autoscan"))
            if name == "find_events":
                return _events(a["filename"], a.get("rules"))
            if name == "performance":
                return _perf(a["filename"], a.get("mass_kg"))
            if name == "lookup_dtc":
                return _dtc(a["code"])
            if name == "obd_status":
                return _obd_status()
            if name == "read_live_dtcs":
                return _live_dtcs()
            if name == "snapshot_pids":
                return _snapshot()
            if name == "vehicle_info":
                return _vehicle_info()
            if name == "readiness":
                return _readiness()
            return {"error": f"unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    return execute
