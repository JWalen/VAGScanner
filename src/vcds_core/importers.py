"""Importers for non-VCDS log formats (Torque, OBD Fusion, FORScan, generic CSV).

These phone/PC apps export flat CSVs with their own conventions — channel names
that carry units in parentheses (``"Engine RPM(rpm)"``), a timestamp column
instead of a seconds column, and leading GPS/metadata columns. This module reads
them into the same :class:`MeasuringLog` the rest of the toolkit consumes, so
every analysis tool works on them too.

Standard-library only; reuses the inference helpers from :mod:`vcds_core.parse`.
"""

from __future__ import annotations

import datetime
import re
from typing import List, Optional

from .parse import (
    Channel,
    MeasuringLog,
    _decode,
    _delim_name,
    _detect_delimiter,
    _downsample,
    _fill_stats,
    _is_numeric_row,
    _read_capped,
    _split_rows,
    _to_float,
)

_UNIT_PAREN = re.compile(r"^(.*?)\s*[\(\[]([^)\]]+)[\)\]]\s*$")
_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S",
    "%H:%M:%S.%f", "%H:%M:%S",
)


def _name_unit(cell: str):
    s = (cell or "").strip().strip('"')
    m = _UNIT_PAREN.match(s)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


def _parse_ts(s: str) -> Optional[float]:
    s = (s or "").strip().strip('"')
    if not s:
        return None
    for fmt in _TS_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return _to_float(s)


def import_generic_csv(path: str, max_points: int = 2000) -> MeasuringLog:
    """Import a generic OBD app CSV (Torque / OBD Fusion / FORScan / similar)."""
    text = _decode(_read_capped(path))
    rows = _split_rows(text, _detect_delimiter(text.splitlines()[:25]))
    delim = _detect_delimiter(text.splitlines()[:25])

    flags = [_is_numeric_row(r) for r in rows]
    data_start = None
    for i in range(len(rows) - 1):
        if flags[i] and flags[i + 1]:
            data_start = i
            break
    if data_start is None:
        for i, f in enumerate(flags):
            if f:
                data_start = i
                break
    if data_start is None:
        raise ValueError("No numeric data region found.")

    hidx: List[int] = []
    i = data_start - 1
    while i >= 0 and any(c.strip() for c in rows[i]):
        hidx.append(i)
        i -= 1
    hidx.reverse()
    header = rows[hidx[-1]] if hidx else []

    data_end = data_start
    while data_end < len(rows) and (flags[data_end] or _is_numeric_row(rows[data_end])):
        data_end += 1
    data = rows[data_start:data_end]
    n_cols = max((len(r) for r in data), default=0)

    cols = [[_to_float(r[j] if j < len(r) else "") for r in data] for j in range(n_cols)]
    names_units = [_name_unit(header[j]) if j < len(header) else ("", "") for j in range(n_cols)]

    notes: List[str] = []
    time_col = None
    taxis = None
    # Prefer a column NAMED like time/seconds (numeric seconds or a timestamp).
    # A blind numeric heuristic is avoided: a speed channel that starts at 0 and
    # rises would be mistaken for a time axis.
    for j in range(n_cols):
        nm = names_units[j][0].lower()
        if "time" not in nm and nm not in ("seconds", "sec", "secs"):
            continue
        col = cols[j]
        nums = [v for v in col if v is not None]
        if (len(nums) >= 2 and nums[-1] > nums[0]
                and all(nums[k + 1] >= nums[k] - 1e-6 for k in range(len(nums) - 1))):
            base = col[0] if col[0] is not None else 0.0
            taxis = [(v - base) if v is not None else float(i) for i, v in enumerate(col)]
            time_col = j
            break
        ts = [_parse_ts(r[j] if j < len(r) else "") for r in data]
        ok = sum(1 for x in ts if x is not None)
        if ok >= 0.8 * len(ts) and ok >= 2:
            b = next(x for x in ts if x is not None)
            taxis = [(x - b) if x is not None else float(i) for i, x in enumerate(ts)]
            notes.append(f"Derived time (s) from '{names_units[j][0]}'.")
            time_col = j
            break
    if taxis is None:
        taxis = [float(i) for i in range(len(data))]
        notes.append("No time column found; using sample index as seconds.")

    channels: List[Channel] = []
    raw_series = {}
    series = {}
    used = set()
    for j in range(n_cols):
        if j == time_col:
            continue
        vals = cols[j]
        if all(v is None for v in vals):
            continue
        name, unit = names_units[j]
        if not name:
            name = f"col{j}"
        base, k = name, 2
        while name in used:
            name = f"{base} ({k})"
            k += 1
        used.add(name)

        tv = [(taxis[i], vals[i]) for i in range(len(vals)) if vals[i] is not None]
        if not tv:
            continue
        times = [t for t, _ in tv]
        svals = [v for _, v in tv]
        ch = Channel(name=name, unit=unit, column_index=j, time_column_index=time_col)
        _fill_stats(ch, svals)
        channels.append(ch)
        raw_series[name] = {"time": times, "value": svals}
        t_ds, v_ds = _downsample(times, svals, max_points)
        series[name] = {"time": t_ds, "value": v_ds, "unit": unit}

    duration = (taxis[-1] - taxis[0]) if len(taxis) >= 2 else None
    return MeasuringLog(
        file=path,
        delimiter=_delim_name(delim),
        format_guess="generic_csv",
        header_rows=[delim.join(rows[i]) for i in hidx],
        data_start_row=data_start,
        duration_s=duration,
        sample_count=len(data),
        channels=channels,
        series=series,
        parse_notes=notes,
        raw_series=raw_series,
    )


def open_measuring_file(path: str, max_points: int = 2000) -> MeasuringLog:
    """Open a measuring log, trying the VCDS parser then the generic importer."""
    from .parse import parse_measuring_log

    try:
        log = parse_measuring_log(path, max_points=max_points)
        if log.channels:
            return log
    except Exception:  # noqa: BLE001
        pass
    return import_generic_csv(path, max_points=max_points)
