"""FastMCP stdio server for the VCDS toolkit.

Exposes the dependency-free :mod:`vcds_core` parsers plus live ELM327 OBD-II
tools over the Model Context Protocol (stdio transport).

Operational rules baked in:

  * The logs folder comes from the ``VCDS_LOGS_DIR`` environment variable and
    defaults to ``C:\\Ross-Tech\\VCDS\\Logs``.
  * ALL file access is confined to that folder; path-traversal attempts are
    rejected.
  * We NEVER write to stdout — that stream carries the JSON-RPC protocol and any
    stray byte corrupts it. All diagnostics go to stderr via :mod:`logging`.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import asdict
from typing import List, Optional

# vcds_core is dependency-free and importable as a sibling package.
from vcds_core import compute, knowledge, parse, perform, trip
from vcds_core.diagnose import diagnose as run_diagnose

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - exercised only without the dep
    raise SystemExit(
        "The 'mcp' package is required to run vcds-mcp. Install with: pip install mcp"
    ) from exc

# --------------------------------------------------------------------------- #
# Logging — stderr ONLY.
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=os.environ.get("VCDS_LOG_LEVEL", "INFO"),
    stream=sys.stderr,
    format="%(asctime)s vcds-mcp %(levelname)s %(message)s",
)
log = logging.getLogger("vcds_mcp")

DEFAULT_LOGS_DIR = os.environ.get(
    "VCDS_LOGS_DIR", os.path.join(os.path.expanduser("~"), "Documents", "OBD Toolkit", "Logs"))


def logs_dir() -> str:
    """Resolved absolute path of the configured VCDS logs folder."""
    return os.path.abspath(os.environ.get("VCDS_LOGS_DIR", DEFAULT_LOGS_DIR))


def _safe_path(filename: str) -> str:
    """Resolve ``filename`` inside the logs folder, rejecting traversal.

    Raises:
        ValueError: if the resulting path escapes the logs directory or the
            file does not exist.
    """
    if not filename or os.path.isabs(filename) or ".." in filename.replace("\\", "/").split("/"):
        raise ValueError(f"Illegal filename: {filename!r}")
    # realpath (not abspath) so a symlink/junction inside the folder can't point out.
    base = os.path.realpath(logs_dir())
    full = os.path.realpath(os.path.join(base, filename))
    # Containment check that also handles case-insensitive Windows paths.
    try:
        contained = os.path.commonpath([os.path.normcase(full), os.path.normcase(base)]) \
            == os.path.normcase(base)
    except ValueError:  # different drives -> commonpath raises
        contained = False
    if not contained:
        raise ValueError(f"Path escapes logs directory: {filename!r}")
    if not os.path.isfile(full):
        raise ValueError(f"File not found in logs directory: {filename!r}")
    return full


mcp = FastMCP("vcds")


# --------------------------------------------------------------------------- #
# Serialization helpers
# --------------------------------------------------------------------------- #


def _channel_dict(ch: parse.Channel) -> dict:
    return {
        "name": ch.name,
        "unit": ch.unit,
        "group": ch.group,
        "count": ch.count,
        "min": ch.min,
        "max": ch.max,
        "mean": ch.mean,
        "first": ch.first,
        "last": ch.last,
    }


def _log_summary(mlog: parse.MeasuringLog) -> dict:
    return {
        "file": os.path.basename(mlog.file),
        "delimiter": mlog.delimiter,
        "format_guess": mlog.format_guess,
        "header_rows": mlog.header_rows,
        "data_start_row": mlog.data_start_row,
        "duration_s": mlog.duration_s,
        "sample_count": mlog.sample_count,
        "channels": [_channel_dict(c) for c in mlog.channels],
        "parse_notes": mlog.parse_notes,
    }


def _event_dict(ev: parse.Event) -> dict:
    return asdict(ev)


# --------------------------------------------------------------------------- #
# File tools
# --------------------------------------------------------------------------- #


@mcp.tool()
def list_logs(kind: str = "all", limit: int = 50) -> dict:
    """List VCDS log files in the configured logs folder, newest first.

    Args:
        kind: Filter by type: "measuring_log", "autoscan", or "all".
        limit: Maximum number of files to return.

    Returns:
        A dict with the logs directory and a list of files, each classified as
        a measuring_log or autoscan with its size and modification time.
    """
    base = logs_dir()
    if not os.path.isdir(base):
        return {"logs_dir": base, "error": "Logs directory does not exist.", "files": []}
    rows = []
    for name in os.listdir(base):
        full = os.path.join(base, name)
        if not os.path.isfile(full):
            continue
        if not name.lower().endswith((".csv", ".txt")):
            continue
        cls = parse.classify_file(full)
        if kind != "all" and cls != kind:
            continue
        st = os.stat(full)
        rows.append({"filename": name, "kind": cls, "size": st.st_size, "mtime": st.st_mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return {"logs_dir": base, "count": len(rows[:limit]), "files": rows[:limit]}


@mcp.tool()
def read_autoscan(filename: str) -> dict:
    """Parse a VCDS Auto-Scan report (.TXT) into VIN, mileage, modules and faults.

    Args:
        filename: File name (not a path) inside the logs folder.

    Returns:
        VIN, mileage, a list of modules (address, name, faults with status
        detail) and any count-reconciliation notes.
    """
    path = _safe_path(filename)
    scan = parse.parse_autoscan(path)
    return {
        "file": os.path.basename(scan.file),
        "vin": scan.vin,
        "mileage": scan.mileage,
        "total_faults": scan.total_faults,
        "modules": [
            {
                "address": m.address,
                "name": m.name,
                "reported_fault_count": m.reported_fault_count,
                "faults": [asdict(f) for f in m.faults],
            }
            for m in scan.modules
        ],
        "parse_notes": scan.parse_notes,
    }


@mcp.tool()
def read_measuring_log(
    filename: str,
    max_points: int = 500,
    include_series: bool = False,
    channels: Optional[List[str]] = None,
    include_computed: bool = False,
) -> dict:
    """Parse a VCDS measuring-value CSV: detected structure, channels and stats.

    Args:
        filename: File name (not a path) inside the logs folder.
        max_points: Down-sample each returned series to roughly this many points.
        include_series: If true, include the (down-sampled) time/value arrays.
        channels: Optional list of channel-name substrings to restrict the
            returned series to (only used when include_series is true).

    Returns:
        The detected format, delimiter, echoed header rows, per-channel stats,
        and optionally the down-sampled series.
    """
    path = _safe_path(filename)
    mlog = parse.parse_measuring_log(path, max_points=max_points)
    if include_computed:
        compute.add_computed_channels(mlog, max_points=max_points)
    out = _log_summary(mlog)
    if include_series:
        wanted = [c.lower() for c in channels] if channels else None
        series = {}
        for name, s in mlog.series.items():
            if wanted and not any(w in name.lower() for w in wanted):
                continue
            series[name] = s
        out["series"] = series
    return out


@mcp.tool()
def channel_stats(filename: str, channel: str) -> dict:
    """Return statistics for one channel in a measuring log.

    Args:
        filename: File name (not a path) inside the logs folder.
        channel: Channel name or case-insensitive substring to match.

    Returns:
        The matched channel's stats, or an error if no channel matches.
    """
    path = _safe_path(filename)
    mlog = parse.parse_measuring_log(path)
    ch = mlog.channel(channel)
    if ch is None:
        return {
            "error": f"No channel matching {channel!r}.",
            "available": [c.name for c in mlog.channels],
        }
    return _channel_dict(ch)


@mcp.tool()
def find_log_events(
    filename: str,
    rules: Optional[List[dict]] = None,
    max_points: int = 2000,
) -> dict:
    """Detect events in a measuring log: heuristics by default, or rule-based.

    Args:
        filename: File name (not a path) inside the logs folder.
        rules: Optional threshold rules, e.g.
            [{"channel": "Boost", "op": "<", "value": 1700}]. op is one of
            > < >= <= ==; channel is a case-insensitive substring match.
            When omitted, VAG heuristics run (spec-vs-actual divergence,
            rising counters, per-channel extremes).
        max_points: Resolution cap when parsing the log.

    Returns:
        A time-sorted list of events.
    """
    path = _safe_path(filename)
    mlog = parse.parse_measuring_log(path, max_points=max_points)
    events = parse.find_events(mlog, rules=rules)
    return {"file": os.path.basename(mlog.file), "count": len(events), "events": [_event_dict(e) for e in events]}


@mcp.tool()
def lookup_dtc(code: str, profile: str = "generic") -> dict:
    """Look up a diagnostic trouble code's meaning, severity and likely causes.

    Args:
        code: A DTC such as "P0299" (leading apostrophes / lower-case tolerated).
        profile: Vehicle brand for manufacturer-specific (P1xxx) codes —
            "vag", "ford" or "generic".

    Returns:
        Description, severity, affected system, likely causes (most-likely
        first), any brand note, and whether it was an exact match.
    """
    brand = None if profile == "generic" else profile
    k = knowledge.lookup(code, brand=brand)
    return {
        "code": k.code,
        "description": k.description,
        "severity": k.severity,
        "system": k.system,
        "causes": k.causes,
        "notes": k.notes,
        "known": k.known,
    }


def _finding_dict(f) -> dict:
    return {
        "severity": f.severity,
        "title": f.title,
        "detail": f.detail,
        "category": f.category,
        "causes": f.causes,
        "evidence": f.evidence,
        "code": f.code,
    }


@mcp.tool()
def diagnose_file(filename: Optional[str] = None, autoscan: Optional[str] = None) -> dict:
    """Diagnose a measuring log and/or an Auto-Scan into prioritized findings.

    Combines the fault-code knowledge base with data-driven symptom detection
    (lean/rich fuel trims, overheating, boost shortfall, rising misfire
    counters, heat soak) and returns findings sorted most-severe first, each
    with likely causes.

    Args:
        filename: Measuring-log CSV file name in the logs folder (optional).
        autoscan: Auto-Scan TXT file name in the logs folder (optional).
            At least one of filename/autoscan is required.

    Returns:
        VIN/mileage (when available), a one-line headline, a severity summary
        and the list of findings.
    """
    scan = log = None
    if autoscan:
        scan = parse.parse_autoscan(_safe_path(autoscan))
    if filename:
        log = parse.parse_measuring_log(_safe_path(filename))
        compute.add_computed_channels(log)
    if scan is None and log is None:
        return {"error": "Provide a measuring-log filename and/or an autoscan filename."}
    report = run_diagnose(scan=scan, log=log)
    return {
        "vin": report.vin,
        "mileage": report.mileage,
        "headline": report.headline,
        "summary": report.summary,
        "findings": [_finding_dict(f) for f in report.findings],
        "notes": report.notes,
    }


@mcp.tool()
def analyze_performance(filename: str, mass_kg: float = 1850) -> dict:
    """Acceleration runs, WOT pulls and an estimated peak power/torque figure.

    Args:
        filename: Measuring-log CSV file name in the logs folder.
        mass_kg: Vehicle mass used for the (rough) power estimate.

    Returns:
        Acceleration times (e.g. 0–100 km/h), detected pulls, and an estimated
        crank power/torque figure (approximate — depends on mass/drag).
    """
    path = _safe_path(filename)
    mlog = parse.parse_measuring_log(path)
    runs = perform.standard_accel_runs(mlog)
    pulls = perform.detect_pulls(mlog)
    est = perform.estimate_power(mlog, mass_kg)
    econ = trip.fuel_economy(mlog)
    bat = trip.battery_analysis(mlog)
    return {
        "file": os.path.basename(mlog.file),
        "acceleration_runs": [
            {"from": r.from_speed, "to": r.to_speed, "unit": r.unit, "seconds": r.elapsed_s}
            for r in runs
        ],
        "pulls": [
            {"start_s": p.start_time, "end_s": p.end_time, "rpm_start": p.rpm_start,
             "rpm_end": p.rpm_end, "peak_boost": p.peak_boost, "peak_speed": p.peak_speed}
            for p in pulls
        ],
        "power_estimate": (
            {"peak_hp": est.peak_hp, "peak_torque_nm": est.peak_torque_nm,
             "peak_torque_rpm": est.peak_torque_rpm, "mass_kg": est.mass_kg}
            if est else None
        ),
        "economy": (
            {"l_per_100km": econ.l_per_100km, "mpg_us": econ.mpg_us,
             "distance_km": econ.distance_km, "fuel_l": econ.fuel_l,
             "idle_fraction": econ.idle_fraction, "source": econ.source}
            if econ else None
        ),
        "battery": (
            {"min_v": bat.min_v, "avg_v": bat.avg_v, "max_v": bat.max_v,
             "cranking_v": bat.cranking_v, "charging_v": bat.charging_v}
            if bat else None
        ),
    }


# --------------------------------------------------------------------------- #
# Live OBD-II tools are registered by vcds_obd (added at a later build stage).
# --------------------------------------------------------------------------- #
try:
    from vcds_obd.mcp_tools import register_obd_tools

    register_obd_tools(mcp, logs_dir)
    log.info("Live OBD-II tools registered.")
except Exception as exc:  # noqa: BLE001 - live tools are optional
    log.info("Live OBD-II tools unavailable (%s).", exc)


def main() -> None:
    """Console-script entry point: run the FastMCP server over stdio."""
    log.info("Starting vcds-mcp; logs dir = %s", logs_dir())
    mcp.run()


if __name__ == "__main__":
    main()
