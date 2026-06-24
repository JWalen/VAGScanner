"""Tests for the dependency-free parsing core against both CSV layouts."""

from __future__ import annotations

from vcds_core import parse


# --------------------------------------------------------------------------- #
# Classic group layout
# --------------------------------------------------------------------------- #


def test_classic_structure(samples_dir):
    log = parse.parse_measuring_log(samples_dir["classic"])
    assert log.delimiter == "semicolon"
    assert log.format_guess == "classic_group"
    names = {c.name for c in log.channels}
    assert names == {"Engine Speed", "Intake Air Temp", "Boost Pressure", "Throttle Angle"}


def test_classic_units_and_groups(samples_dir):
    log = parse.parse_measuring_log(samples_dir["classic"])
    by_name = {c.name: c for c in log.channels}
    assert by_name["Engine Speed"].unit == "/min"
    assert by_name["Intake Air Temp"].unit == "°C"
    assert by_name["Boost Pressure"].unit == "mbar"
    assert by_name["Throttle Angle"].unit == "%"
    # group metadata is captured, not treated as a channel name
    assert by_name["Engine Speed"].group == "Group A"
    assert by_name["Boost Pressure"].group == "Group B"


def test_classic_two_time_columns(samples_dir):
    log = parse.parse_measuring_log(samples_dir["classic"])
    # Each group's value columns map to their own (different) time column.
    a = log.channel("Engine Speed").time_column_index
    b = log.channel("Boost Pressure").time_column_index
    assert a is not None and b is not None
    assert a != b


def test_classic_comma_decimal_stats(samples_dir):
    log = parse.parse_measuring_log(samples_dir["classic"])
    rpm = log.channel("Engine Speed")
    # comma decimals must have parsed as real floats
    assert rpm.count == log.sample_count
    assert 800 < rpm.min < rpm.max < 3000
    assert rpm.mean is not None
    assert log.duration_s is not None and log.duration_s > 10


# --------------------------------------------------------------------------- #
# Advanced / UDS layout
# --------------------------------------------------------------------------- #


def test_advanced_structure(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"])
    assert log.delimiter == "comma"
    names = {c.name for c in log.channels}
    assert "Engine Speed" in names
    assert "Boost Pressure (specified)" in names
    assert "Boost Pressure (actual)" in names
    assert "Coolant Temp" in names
    assert "Misfire Counter Cyl 1" in names
    # The Marker column must NOT appear as a channel.
    assert "Marker" not in names


def test_advanced_units(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"])
    by_name = {c.name: c for c in log.channels}
    assert by_name["Engine Speed"].unit == "/min"
    assert by_name["Boost Pressure (actual)"].unit == "mbar"
    assert by_name["Coolant Temp"].unit == "°C"
    assert by_name["Misfire Counter Cyl 1"].unit == "count"


def test_advanced_single_time_column(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"])
    tcols = {c.time_column_index for c in log.channels}
    assert len(tcols) == 1
    # the misfire counter must not be mistaken for a time column
    assert log.channel("Misfire Counter Cyl 1") is not None


def test_downsampling(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"], max_points=20)
    for name, s in log.series.items():
        assert len(s["value"]) <= 21  # max_points (+1 for the kept final sample)
    # full-resolution kept for analysis
    assert len(log.raw_series["Engine Speed"]["value"]) == log.sample_count


# --------------------------------------------------------------------------- #
# Auto-Scan
# --------------------------------------------------------------------------- #


def test_autoscan_header(samples_dir):
    scan = parse.parse_autoscan(samples_dir["autoscan"])
    assert scan.vin == "WAUZZZ8K9BA123456"
    assert scan.mileage and scan.mileage.startswith("123456")


def test_autoscan_modules(samples_dir):
    scan = parse.parse_autoscan(samples_dir["autoscan"])
    addrs = {m.address: m for m in scan.modules}
    assert set(addrs) == {"01", "03", "17"}
    assert addrs["01"].name.startswith("Engine")
    assert addrs["03"].name.startswith("ABS")


def test_autoscan_faults_with_status_detail(samples_dir):
    scan = parse.parse_autoscan(samples_dir["autoscan"])
    engine = next(m for m in scan.modules if m.address == "01")
    # 2 faults, NOT 4 — the indented P-code lines are status details.
    assert len(engine.faults) == 2
    codes = {f.code for f in engine.faults}
    assert "008598" in codes
    assert "000257" in codes
    boost = next(f for f in engine.faults if f.code == "008598")
    assert boost.status_detail is not None
    assert "P2196" in boost.status_detail
    assert "Intermittent" in boost.status_detail


def test_autoscan_no_fault_module(samples_dir):
    scan = parse.parse_autoscan(samples_dir["autoscan"])
    abs_mod = next(m for m in scan.modules if m.address == "03")
    assert abs_mod.faults == []
    assert abs_mod.reported_fault_count == 0


def test_autoscan_counts_reconcile(samples_dir):
    scan = parse.parse_autoscan(samples_dir["autoscan"])
    # reported counts match parsed counts -> no reconciliation notes
    assert scan.parse_notes == []
    assert scan.total_faults == 3


# --------------------------------------------------------------------------- #
# Event detection
# --------------------------------------------------------------------------- #


def test_find_events_heuristics(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"])
    events = parse.find_events(log)
    kinds = {e.kind for e in events}
    assert "divergence" in kinds  # spec vs actual boost
    assert "rising_counter" in kinds  # misfire counter
    div = next(e for e in events if e.kind == "divergence")
    assert div.value is not None and div.value > 400  # the ~600 mbar shortfall


def test_find_events_rules(samples_dir):
    log = parse.parse_measuring_log(samples_dir["advanced"])
    rules = [{"channel": "actual", "op": "<", "value": 700}]
    events = parse.find_events(log, rules=rules)
    assert events, "threshold rule should produce at least one crossing"
    assert all(e.kind == "threshold" for e in events)
    assert events == sorted(events, key=lambda e: e.time)
