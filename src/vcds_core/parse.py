"""Parsers and event detection for VCDS measuring logs, Auto-Scans, and any CSV
written in the same flat layout (including live ELM327 sessions).

Standard library ONLY. No third-party imports may ever appear in this module.

VCDS has shipped several CSV layouts over the years and the field delimiter and
decimal separator both vary with the Windows locale of the machine that wrote
the file. Nothing here hard-codes a single format: structure is *inferred* and
echoed back so callers can see what was detected.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class Channel:
    """A single measured value column."""

    name: str
    unit: str
    column_index: int
    time_column_index: Optional[int]
    group: Optional[str] = None
    count: int = 0
    min: Optional[float] = None
    max: Optional[float] = None
    mean: Optional[float] = None
    first: Optional[float] = None
    last: Optional[float] = None


@dataclass
class MeasuringLog:
    """Result of parsing a measuring-value CSV."""

    file: str
    delimiter: str
    format_guess: str
    header_rows: List[str]
    data_start_row: int
    duration_s: Optional[float]
    sample_count: int
    channels: List[Channel] = field(default_factory=list)
    # Down-sampled series returned to callers: name -> {"time": [...], "value": [...], "unit": str}
    series: Dict[str, Dict[str, object]] = field(default_factory=dict)
    parse_notes: List[str] = field(default_factory=list)
    # Full-resolution series kept for analysis (find_events); not meant for transport.
    raw_series: Dict[str, Dict[str, List[float]]] = field(default_factory=dict, repr=False)

    def channel(self, name: str) -> Optional[Channel]:
        """Return the channel whose name matches ``name`` (case-insensitive substring)."""
        low = name.lower()
        for ch in self.channels:
            if ch.name.lower() == low:
                return ch
        for ch in self.channels:
            if low in ch.name.lower():
                return ch
        return None


@dataclass
class Fault:
    code: str
    description: str
    status_detail: Optional[str] = None


@dataclass
class Module:
    address: str
    name: str
    status: Optional[str] = None
    faults: List[Fault] = field(default_factory=list)
    reported_fault_count: Optional[int] = None


@dataclass
class AutoScan:
    file: str
    vin: Optional[str]
    mileage: Optional[str]
    modules: List[Module] = field(default_factory=list)
    parse_notes: List[str] = field(default_factory=list)

    @property
    def total_faults(self) -> int:
        return sum(len(m.faults) for m in self.modules)


@dataclass
class Event:
    time: Optional[float]
    channel: str
    kind: str
    message: str
    value: Optional[float] = None
    detail: Optional[str] = None


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #

_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# Tokens that are structural metadata, never channel names.
_META_TOKENS = {"time", "marker", "stamp", "marker/stamp", "te"}

_GROUP_LABEL_RE = re.compile(r"(?i)^\s*group\b")
_GROUP_NUMBER_RE = re.compile(r"^'?\s*\d{1,3}\s*$")  # e.g. '115, '020, 002
_SEPARATOR_RE = re.compile(r"^[\s\-=_*~]+$")


def _decode(raw: bytes) -> str:
    """Decode bytes trying a sequence of encodings VCDS is known to emit."""
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 never raises, but be defensive.
    return raw.decode("latin-1", errors="replace")


def _detect_delimiter(lines: Sequence[str]) -> str:
    """Pick the densest of comma / semicolon / tab over the sample lines.

    Counting raw occurrences works even when comma doubles as a decimal mark,
    because a real field separator appears far more consistently per line than
    an occasional decimal comma.
    """
    candidates = [";", "\t", ","]  # tie-break order favours unambiguous separators
    best = ","
    best_score = -1.0
    for cand in candidates:
        counts = [ln.count(cand) for ln in lines if ln.strip()]
        if not counts:
            continue
        # Reward total volume but also consistency (lines that actually contain it).
        present = sum(1 for c in counts if c > 0)
        total = sum(counts)
        score = total + present * 0.5
        if score > best_score:
            best_score = score
            best = cand
    return best


def _to_float(cell: str) -> Optional[float]:
    """Parse a numeric cell accepting both ``1.5`` and locale ``1,5``.

    Handles thousands separators when both ``.`` and ``,`` are present by
    treating the right-most symbol as the decimal mark.
    """
    if cell is None:
        return None
    s = cell.strip().strip('"').strip()
    if not s:
        return None
    # Drop a trailing unit letter sometimes glued on (rare); keep sign/exponent.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return val


def _split_rows(text: str, delimiter: str) -> List[List[str]]:
    """Split decoded text into rows of cells, honouring quoted fields."""
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [row for row in reader]


def _is_numeric_row(cells: Sequence[str]) -> bool:
    non_empty = [c for c in cells if c.strip()]
    if len(non_empty) < 2:
        return False
    numeric = sum(1 for c in non_empty if _to_float(c) is not None)
    return numeric >= 2 and (numeric / len(non_empty)) >= 0.6


def _cell_wordiness(cell: str) -> int:
    s = cell.strip()
    letters = sum(1 for ch in s if ch.isalpha())
    spaces = s.count(" ")
    return letters + 2 * spaces


def _clean_name(cell: str) -> str:
    """Strip quoting and reject structural metadata tokens."""
    s = cell.strip().strip('"').strip().strip("'").strip()
    if not s:
        return ""
    if s.lower() in _META_TOKENS:
        return ""
    if _GROUP_LABEL_RE.match(s) or _GROUP_NUMBER_RE.match(s):
        return ""
    return s


def _is_group_meta_cell(cell: str) -> bool:
    s = cell.strip().strip('"').strip()
    return bool(_GROUP_LABEL_RE.match(s) or _GROUP_NUMBER_RE.match(s))


# --------------------------------------------------------------------------- #
# Measuring-log CSV parser
# --------------------------------------------------------------------------- #


MAX_FILE_BYTES = 64 * 1024 * 1024  # cap a single log/scan file at 64 MB


def _read_capped(path: str, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    """Read a file but refuse pathologically large ones (DoS guard)."""
    with open(path, "rb") as fh:
        data = fh.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"File too large (>{max_bytes // (1024 * 1024)} MB): {path}")
    return data


def parse_measuring_log(path: str, max_points: int = 2000) -> MeasuringLog:
    """Parse a VCDS measuring-value CSV (or any file in the same flat layout).

    Args:
        path: Path to the .CSV file.
        max_points: Down-sample each returned series to roughly this many points.
    """
    text = _decode(_read_capped(path))

    sample_lines = text.splitlines()[:25]
    delimiter = _detect_delimiter(sample_lines)
    rows = _split_rows(text, delimiter)

    notes: List[str] = []

    # --- locate the data region: first run of >=2 consecutive numeric rows --- #
    numeric_flags = [_is_numeric_row(r) for r in rows]
    data_start = None
    for i in range(len(rows) - 1):
        if numeric_flags[i] and numeric_flags[i + 1]:
            data_start = i
            break
    if data_start is None:
        # fall back to any single numeric row
        for i, flag in enumerate(numeric_flags):
            if flag:
                data_start = i
                break
    if data_start is None:
        raise ValueError("No numeric data region found in measuring log.")

    def _blank(r):
        return not any(c.strip() for c in r)

    # Extend across the numeric data region. VCDS and many OBD tools emit blank
    # separator rows mid-capture — bridge those (when numeric data resumes after
    # the gap) instead of truncating the whole rest of the log at the first blank.
    data_end = data_start
    bridged = False
    while data_end < len(rows):
        if numeric_flags[data_end] or _is_numeric_row(rows[data_end]):
            data_end += 1
            continue
        if _blank(rows[data_end]):
            j = data_end + 1
            while j < len(rows) and _blank(rows[j]):
                j += 1
            if j < len(rows) and (numeric_flags[j] or _is_numeric_row(rows[j])):
                data_end = j  # skip the blank gap; data continues
                bridged = True
                continue
        break  # a genuine non-numeric, non-blank row ends the data region
    if bridged:
        notes.append("Bridged blank separator row(s) within the data region.")
    # Drop blank rows so the column series build cleanly.
    data_rows = [r for r in rows[data_start:data_end] if not _blank(r)]

    # --- header rows: the CONTIGUOUS non-empty rows just above the data
    #     region (a blank line separates them from any date banner above). --- #
    header_indices: List[int] = []
    i = data_start - 1
    while i >= 0 and any(c.strip() for c in rows[i]):
        header_indices.append(i)
        i -= 1
    header_indices.reverse()
    header_cell_rows = [rows[i] for i in header_indices]
    header_rows_echo = [
        delimiter.join(rows[i]) if rows[i] else "" for i in header_indices
    ]

    n_cols = max((len(r) for r in data_rows), default=0)

    # --- collect numeric columns from the data region ----------------------- #
    columns: List[List[Optional[float]]] = [[] for _ in range(n_cols)]
    for r in data_rows:
        for j in range(n_cols):
            cell = r[j] if j < len(r) else ""
            columns[j].append(_to_float(cell))

    # --- identify header sub-rows: group-meta, names, units ----------------- #
    group_meta_row: Optional[List[str]] = None
    naming_rows: List[List[str]] = []
    for hr in header_cell_rows:
        if any(_is_group_meta_cell(c) for c in hr) and sum(
            1 for c in hr if _clean_name(c)
        ) <= sum(1 for c in hr if _is_group_meta_cell(c)) + 1:
            group_meta_row = hr
        else:
            naming_rows.append(hr)

    def _row_wordiness(hr: Sequence[str]) -> int:
        return sum(_cell_wordiness(c) for c in hr if _clean_name(c))

    # VCDS always lays channel NAMES above UNITS, so row order is the most
    # reliable signal for the common 1- or 2-row header. Wordiness is only a
    # fallback when an unusual file has 3+ header rows.
    names_row: Optional[List[str]] = None
    units_row: Optional[List[str]] = None
    if len(naming_rows) == 1:
        names_row = naming_rows[0]
    elif len(naming_rows) == 2:
        names_row, units_row = naming_rows[0], naming_rows[1]
    elif len(naming_rows) >= 3:
        names_row = max(naming_rows, key=_row_wordiness)
        others = [r for r in naming_rows if r is not names_row]
        units_row = min(others, key=_row_wordiness) if others else None

    # group labels carried forward across columns (classic group logs)
    group_for_col: List[Optional[str]] = [None] * n_cols
    if group_meta_row is not None:
        current = None
        for j in range(n_cols):
            cell = group_meta_row[j].strip() if j < len(group_meta_row) else ""
            if _GROUP_LABEL_RE.match(cell):
                current = cell.rstrip(":").strip()
            group_for_col[j] = current

    # --- classify time columns ---------------------------------------------- #
    def _header_label(j: int) -> str:
        if names_row and j < len(names_row):
            return names_row[j].strip().strip('"').lower()
        return ""

    time_cols: List[int] = []
    for j in range(n_cols):
        vals = [v for v in columns[j] if v is not None]
        if not vals:
            continue
        label = _header_label(j)
        name = _clean_name(names_row[j]) if (names_row and j < len(names_row)) else ""
        if label == "time" and len(vals) >= 2:
            time_cols.append(j)
            continue
        if name:
            # A column with a real channel name is never an inferred time axis;
            # only explicit "TIME" headers (handled above) qualify.
            continue
        if _looks_like_time(columns[j]):
            time_cols.append(j)
    time_cols.sort()

    if not time_cols:
        notes.append("No time column detected; using sample index as time.")

    def _time_for(col: int) -> Optional[int]:
        left = [t for t in time_cols if t < col]
        if left:
            return left[-1]
        return time_cols[0] if time_cols else None

    # --- build channels ----------------------------------------------------- #
    channels: List[Channel] = []
    raw_series: Dict[str, Dict[str, List[float]]] = {}

    # synthetic time (index) if needed
    synthetic_time = [float(i) for i in range(len(data_rows))]

    def _time_axis(col: Optional[int]) -> List[float]:
        if col is None:
            return synthetic_time
        return [v if v is not None else float(i) for i, v in enumerate(columns[col])]

    used_names = set()
    for j in range(n_cols):
        if j in time_cols:
            continue
        vals = columns[j]
        if all(v is None for v in vals):
            continue  # blank column (e.g. Marker)
        name = _clean_name(names_row[j]) if (names_row and j < len(names_row)) else ""
        unit = ""
        if units_row and j < len(units_row):
            u = units_row[j].strip().strip('"').strip()
            if u and not _is_group_meta_cell(u) and u.lower() not in _META_TOKENS:
                unit = u
        if not name:
            # nothing usable — skip non-channel numeric column
            continue
        # de-duplicate names
        base = name
        k = 2
        while name in used_names:
            name = f"{base} ({k})"
            k += 1
        used_names.add(name)

        tcol = _time_for(j)
        taxis = _time_axis(tcol)
        # pair time/value, dropping rows where value is missing
        tv = [(taxis[i], vals[i]) for i in range(len(vals)) if vals[i] is not None]
        times = [t for t, _ in tv]
        series_vals = [v for _, v in tv]

        ch = Channel(
            name=name,
            unit=unit,
            column_index=j,
            time_column_index=tcol,
            group=group_for_col[j] if j < len(group_for_col) else None,
        )
        _fill_stats(ch, series_vals)
        channels.append(ch)
        raw_series[name] = {"time": times, "value": series_vals}

    # --- duration & format guess ------------------------------------------- #
    duration_s: Optional[float] = None
    spans = []
    for tc in time_cols:
        tv = [v for v in columns[tc] if v is not None]
        if len(tv) >= 2:
            spans.append(tv[-1] - tv[0])
    if spans:
        duration_s = max(spans)

    if group_meta_row is not None:
        format_guess = "classic_group"
    elif len(time_cols) > 1:
        format_guess = "multi_group"
    elif units_row is not None:
        format_guess = "advanced_uds"
    else:
        format_guess = "flat"

    log = MeasuringLog(
        file=path,
        delimiter=_delim_name(delimiter),
        format_guess=format_guess,
        header_rows=header_rows_echo,
        data_start_row=data_start,
        duration_s=duration_s,
        sample_count=len(data_rows),
        channels=channels,
        parse_notes=notes,
        raw_series=raw_series,
    )

    # --- down-sampled transport series -------------------------------------- #
    for ch in channels:
        rs = raw_series[ch.name]
        t_ds, v_ds = _downsample(rs["time"], rs["value"], max_points)
        log.series[ch.name] = {"time": t_ds, "value": v_ds, "unit": ch.unit}

    return log


def _looks_like_time(col: Sequence[Optional[float]]) -> bool:
    """Heuristic: numeric, non-decreasing, starts near zero, mostly increasing."""
    vals = [v for v in col if v is not None]
    if len(vals) < 3:
        return False
    if abs(vals[0]) > 1.0:
        return False
    if vals[-1] <= vals[0]:
        return False
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    if any(d < -1e-6 for d in diffs):  # must be non-decreasing
        return False
    increasing = sum(1 for d in diffs if d > 1e-9)
    return (increasing / len(diffs)) >= 0.6


def _fill_stats(ch: Channel, vals: Sequence[float]) -> None:
    if not vals:
        return
    ch.count = len(vals)
    ch.min = min(vals)
    ch.max = max(vals)
    ch.mean = sum(vals) / len(vals)
    ch.first = vals[0]
    ch.last = vals[-1]


def _downsample(times: Sequence[float], vals: Sequence[float], max_points: int):
    n = len(vals)
    if max_points <= 0 or n <= max_points:
        return list(times), list(vals)
    stride = math.ceil(n / max_points)
    t_out = list(times[::stride])
    v_out = list(vals[::stride])
    if (n - 1) % stride != 0:  # always keep the final sample
        t_out.append(times[-1])
        v_out.append(vals[-1])
    return t_out, v_out


def _delim_name(delim: str) -> str:
    return {",": "comma", ";": "semicolon", "\t": "tab"}.get(delim, repr(delim))


# --------------------------------------------------------------------------- #
# Auto-Scan .TXT parser (indentation-aware)
# --------------------------------------------------------------------------- #

_ADDRESS_RE = re.compile(r"^\s*Address\s+([0-9A-Fa-f]{2})\s*:\s*(.+?)\s*$")
_VIN_RE = re.compile(r"VIN:\s*([A-HJ-NPR-Z0-9]{11,17})", re.IGNORECASE)
_MILEAGE_RE = re.compile(r"Mileage:\s*([0-9]+\s*km[^\s]*|[0-9]+\s*km|[0-9]+km[^ ]*)", re.IGNORECASE)
# A fault-code line: "P2196 - ...", "U0101 - ...", "01314 - ..." etc.
_FAULT_RE = re.compile(r"^([PUBC]?[0-9A-F]{4,6})\s*-\s*(.+?)\s*$")
_FAULTS_FOUND_RE = re.compile(r"(\d+)\s+Faults?\s+Found", re.IGNORECASE)
_NO_FAULT_RE = re.compile(r"No\s+fault\s+code\s+found", re.IGNORECASE)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def parse_autoscan(path: str) -> AutoScan:
    """Parse a VCDS Auto-Scan report (.TXT).

    The format is stable, but indentation — not regex alone — is what separates
    a fault line from the status-detail line indented beneath it.
    """
    text = _decode(_read_capped(path))
    lines = text.splitlines()

    vin: Optional[str] = None
    mileage: Optional[str] = None
    for ln in lines[:40]:
        if vin is None:
            m = _VIN_RE.search(ln)
            if m:
                vin = m.group(1)
        if mileage is None:
            m = _MILEAGE_RE.search(ln)
            if m:
                mileage = m.group(1).strip()

    modules: List[Module] = []
    notes: List[str] = []
    current: Optional[Module] = None
    current_fault: Optional[Fault] = None
    current_fault_indent = 0

    for ln in lines:
        if not ln.strip():
            continue
        if _SEPARATOR_RE.match(ln):
            continue

        addr = _ADDRESS_RE.match(ln)
        if addr:
            current = Module(address=addr.group(1), name=addr.group(2).strip())
            modules.append(current)
            current_fault = None
            continue

        if current is None:
            continue  # header / banner lines before the first module

        if _NO_FAULT_RE.search(ln):
            current.reported_fault_count = 0
            current_fault = None
            continue

        ff = _FAULTS_FOUND_RE.search(ln)
        if ff:
            current.reported_fault_count = int(ff.group(1))
            current_fault = None
            continue

        stripped = ln.strip()
        fault = _FAULT_RE.match(stripped)
        indent = _indent(ln)

        if fault:
            # An indented match beneath an existing fault is its status detail,
            # NOT a new fault.
            if current_fault is not None and indent > current_fault_indent:
                detail = stripped if current_fault.status_detail is None else (
                    current_fault.status_detail + " | " + stripped
                )
                current_fault.status_detail = detail
            else:
                current_fault = Fault(code=fault.group(1), description=fault.group(2).strip())
                current_fault.status_detail = None
                current.faults.append(current_fault)
                current_fault_indent = indent
            continue

        # Non-code line more indented than the current fault → extra status text.
        if current_fault is not None and indent > current_fault_indent:
            extra = stripped
            current_fault.status_detail = (
                extra if current_fault.status_detail is None
                else current_fault.status_detail + " | " + extra
            )

    # reconcile reported vs detected counts
    for m in modules:
        if m.reported_fault_count is not None and m.reported_fault_count != len(m.faults):
            notes.append(
                f"Address {m.address} ({m.name}): reported "
                f"{m.reported_fault_count} fault(s) but parsed {len(m.faults)}."
            )

    return AutoScan(file=path, vin=vin, mileage=mileage, modules=modules, parse_notes=notes)


# --------------------------------------------------------------------------- #
# Event detection
# --------------------------------------------------------------------------- #

_SPEC_KEYWORDS = ("specified", "requested", "target", "desired", "spec")
_ACTUAL_KEYWORDS = ("actual", "real", "current")
_COUNTER_KEYWORDS = ("misfire", "counter", "count", "fault")

_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def _base_name(name: str) -> str:
    out = name.lower()
    for kw in _SPEC_KEYWORDS + _ACTUAL_KEYWORDS:
        out = out.replace(kw, " ")
    out = re.sub(r"[()\[\]]", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def find_events(log: MeasuringLog, rules: Optional[List[dict]] = None) -> List[Event]:
    """Detect notable events in a measuring log.

    Args:
        log: A parsed :class:`MeasuringLog`.
        rules: Optional threshold rules, e.g.
            ``[{"channel": "Boost", "op": "<", "value": 1700}]``. ``op`` is one of
            ``> < >= <= ==`` and ``channel`` is a case-insensitive substring match.
            When omitted, VAG heuristics are applied instead.

    Returns:
        Events sorted by time.
    """
    events: List[Event] = []
    series = log.raw_series or {
        name: {"time": list(s["time"]), "value": list(s["value"])}  # type: ignore[index]
        for name, s in log.series.items()
    }

    if rules:
        events.extend(_threshold_events(log, series, rules))
    else:
        events.extend(_spec_actual_events(log, series))
        events.extend(_counter_events(log, series))
        events.extend(_extreme_events(log, series))

    events.sort(key=lambda e: (e.time is None, e.time if e.time is not None else 0.0))
    return events


def _threshold_events(log, series, rules) -> List[Event]:
    out: List[Event] = []
    for rule in rules:
        chan_q = str(rule.get("channel", "")).lower()
        op = rule.get("op", ">")
        fn = _OPS.get(op)
        if fn is None:
            continue
        try:
            thr = float(rule.get("value"))
        except (TypeError, ValueError):
            continue  # skip a malformed rule rather than abort the whole call
        for ch in log.channels:
            if chan_q and chan_q not in ch.name.lower():
                continue
            s = series.get(ch.name)
            if not s:
                continue
            times, vals = s["time"], s["value"]
            prev = False
            for t, v in zip(times, vals):
                now = fn(v, thr)
                if now and not prev:  # transition into the condition
                    out.append(
                        Event(
                            time=t,
                            channel=ch.name,
                            kind="threshold",
                            message=f"{ch.name} {op} {thr} (={_fmt(v)}{_u(ch)})",
                            value=v,
                        )
                    )
                prev = now
    return out


def _spec_actual_events(log, series) -> List[Event]:
    out: List[Event] = []
    spec_chans = [c for c in log.channels if any(k in c.name.lower() for k in _SPEC_KEYWORDS)]
    actual_chans = [c for c in log.channels if any(k in c.name.lower() for k in _ACTUAL_KEYWORDS)]
    for spec in spec_chans:
        base = _base_name(spec.name)
        match = None
        for act in actual_chans:
            if _base_name(act.name) == base:
                match = act
                break
        if match is None:
            continue
        s1, s2 = series.get(spec.name), series.get(match.name)
        if not s1 or not s2:
            continue
        n = min(len(s1["value"]), len(s2["value"]))
        best_div = -1.0
        best_t = None
        best_pair = (0.0, 0.0)
        for i in range(n):
            div = abs(s1["value"][i] - s2["value"][i])
            if div > best_div:
                best_div = div
                best_t = s1["time"][i]
                best_pair = (s1["value"][i], s2["value"][i])
        if best_t is not None:
            out.append(
                Event(
                    time=best_t,
                    channel=f"{spec.name} vs {match.name}",
                    kind="divergence",
                    message=(
                        f"Max divergence {_fmt(best_div)}{_u(spec)} "
                        f"(spec={_fmt(best_pair[0])}, actual={_fmt(best_pair[1])})"
                    ),
                    value=best_div,
                )
            )
    return out


def _counter_events(log, series) -> List[Event]:
    out: List[Event] = []
    for ch in log.channels:
        low = ch.name.lower()
        if not any(k in low for k in _COUNTER_KEYWORDS):
            continue
        s = series.get(ch.name)
        if not s or len(s["value"]) < 2:
            continue
        vals, times = s["value"], s["time"]
        non_decreasing = all(vals[i + 1] >= vals[i] - 1e-9 for i in range(len(vals) - 1))
        if non_decreasing and vals[-1] > vals[0]:
            # time of first increase
            t_rise = times[-1]
            for i in range(len(vals) - 1):
                if vals[i + 1] > vals[i] + 1e-9:
                    t_rise = times[i + 1]
                    break
            out.append(
                Event(
                    time=t_rise,
                    channel=ch.name,
                    kind="rising_counter",
                    message=f"{ch.name} rising {_fmt(vals[0])} -> {_fmt(vals[-1])}{_u(ch)}",
                    value=vals[-1],
                )
            )
    return out


def _extreme_events(log, series) -> List[Event]:
    out: List[Event] = []
    for ch in log.channels:
        s = series.get(ch.name)
        if not s or not s["value"]:
            continue
        vals, times = s["value"], s["time"]
        i_max = max(range(len(vals)), key=lambda i: vals[i])
        i_min = min(range(len(vals)), key=lambda i: vals[i])
        out.append(
            Event(
                time=times[i_max],
                channel=ch.name,
                kind="extreme_max",
                message=f"{ch.name} max {_fmt(vals[i_max])}{_u(ch)}",
                value=vals[i_max],
            )
        )
        out.append(
            Event(
                time=times[i_min],
                channel=ch.name,
                kind="extreme_min",
                message=f"{ch.name} min {_fmt(vals[i_min])}{_u(ch)}",
                value=vals[i_min],
            )
        )
    return out


def _fmt(v: float) -> str:
    if v is None:
        return "?"
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return f"{v:.3g}"


def _u(ch: Channel) -> str:
    return f" {ch.unit}" if ch.unit else ""


# --------------------------------------------------------------------------- #
# Lightweight classification used by the MCP layer
# --------------------------------------------------------------------------- #


def classify_file(path: str) -> str:
    """Best-effort guess: ``'autoscan'``, ``'measuring_log'`` or ``'unknown'``.

    Reads only the first chunk of the file, so it is cheap to call when listing
    a directory of logs.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return "unknown"
    text = _decode(head)
    low = text.lower()
    if path.lower().endswith(".txt"):
        if "address " in low or "vcds" in low or "fault" in low:
            return "autoscan"
    if "address " in low and ("vin" in low or "fault" in low):
        return "autoscan"
    if path.lower().endswith(".csv"):
        return "measuring_log"
    # crude: many delimiters + numbers -> measuring log
    if any(d in text for d in (";", ",", "\t")):
        return "measuring_log"
    return "unknown"
