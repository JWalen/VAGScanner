"""Mocked live-capture tests — NO hardware.

A fake Connection supplies canned supported_commands, a scripted PID stream and
a DTC list. We assert that:
  * a logged session CSV round-trips through vcds_core.parse and yields the
    expected channels, including the derived boost channel;
  * a threshold trigger fires and writes a clipped capture;
  * a new-DTC trigger fires;
  * read_dtcs surfaces the mocked codes.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from vcds_core import parse
from vcds_obd import live


class FakeClock:
    """Deterministic monotonic clock advancing by ``dt`` on every call."""

    def __init__(self, dt: float) -> None:
        self.dt = dt
        self.t = -dt

    def __call__(self) -> float:
        self.t += self.dt
        return self.t


class FakeOBD:
    """Scripted ELM327 stand-in implementing the live.Connection protocol."""

    def __init__(self, dtc_at: Optional[int] = None) -> None:
        self.i = -1  # advances once per sampled row (on the first channel read)
        self.dtc_at = dtc_at

    def supported(self):
        return set(live.DEFAULT_CHANNELS_BY_CMD.keys())

    def query_value(self, command_name: str) -> Optional[float]:
        if command_name == "RPM":
            self.i += 1
        i = max(0, self.i)
        if command_name == "BAROMETRIC_PRESSURE":
            return 100.0
        if command_name == "INTAKE_PRESSURE":  # MAP ramps 100 -> 180 kPa
            return min(180.0, 100.0 + 2.0 * i)
        if command_name == "RPM":
            return 800.0 + 10.0 * i
        if command_name == "COOLANT_TEMP":
            return 60.0 + 0.5 * i
        if command_name == "SPEED":
            return float(i)
        if command_name == "ENGINE_LOAD":
            return 30.0 + (i % 5)
        return 0.0

    def get_dtcs(self) -> List[Tuple[str, str]]:
        if self.dtc_at is not None and self.i >= self.dtc_at:
            return [("P0301", "Cylinder 1 Misfire Detected")]
        return []

    def status(self) -> str:
        return "Car Connected"

    def protocol(self) -> str:
        return "ISO 15765-4 (CAN 11/500)"


def _logger(conn, tmp_path, **kw):
    return live.LiveLogger(
        conn,
        live.build_channels(conn.supported()),
        str(tmp_path),
        sample_rate_hz=5.0,
        clock=FakeClock(0.2),
        sleep=lambda _s: None,
        **kw,
    )


def test_session_roundtrips_through_core(tmp_path):
    conn = FakeOBD()
    channels = live.build_channels(conn.supported())
    names = {c.name for c in channels}
    assert "Boost (derived)" in names  # derived channel offered

    logger = _logger(conn, tmp_path)
    result = logger.run(duration_s=8.0, session_name="sess")
    assert result.sample_count > 20

    # The session file must parse back through the dependency-free core.
    mlog = parse.parse_measuring_log(result.session_file)
    parsed_names = {c.name for c in mlog.channels}
    assert "Engine RPM" in parsed_names
    assert "Boost (derived)" in parsed_names
    assert "Marker" not in parsed_names  # marker column is not a channel

    boost = mlog.channel("Boost (derived)")
    assert boost.unit == "kPa"
    # Boost = MAP(100..180) - Baro(100) -> climbs from 0 to ~80.
    assert boost.max is not None and boost.max > 50
    assert boost.min is not None and boost.min <= 1.0


def test_vehicle_header_embedded_and_ignored_by_parser(tmp_path):
    conn = FakeOBD()
    logger = _logger(conn, tmp_path)
    logger.header_lines = ["VIN: WAUZZZ8K9BA123456", "Vehicle: 2011 Audi", "Protocol: CAN"]
    result = logger.run(duration_s=4.0, session_name="sess")

    text = open(result.session_file, encoding="utf-8").read()
    assert text.startswith("# VIN: WAUZZZ8K9BA123456")     # embedded at the top
    assert "# Vehicle: 2011 Audi" in text
    # ...and the parser ignores the comment block (blank-line separated)
    mlog = parse.parse_measuring_log(result.session_file)
    assert "Engine RPM" in {c.name for c in mlog.channels}
    assert mlog.sample_count > 5


def test_threshold_trigger_writes_capture(tmp_path):
    conn = FakeOBD()
    logger = _logger(conn, tmp_path, buffer_before_s=2.0, buffer_after_s=2.0)
    trigger = live.Trigger(thresholds=[{"channel": "Boost (derived)", "op": ">", "value": 50}])
    result = logger.run(duration_s=12.0, trigger=trigger, session_name="sess")

    assert result.captures, "threshold should have produced a capture"
    cap = result.captures[0]
    assert cap.trigger_kind == "threshold"

    import os

    assert os.path.isfile(cap.file)
    assert "EVENT" in os.path.basename(cap.file)

    # The capture round-trips and includes pre-trigger context (buffer_before).
    cmlog = parse.parse_measuring_log(cap.file)
    boost = cmlog.channel("Boost (derived)")
    assert boost is not None
    t0 = cmlog.raw_series["Boost (derived)"]["time"][0]
    assert t0 < cap.trigger_time  # context from BEFORE the trigger is present


def test_new_dtc_trigger_fires(tmp_path):
    conn = FakeOBD(dtc_at=10)
    logger = _logger(conn, tmp_path, buffer_before_s=1.0, buffer_after_s=1.0, dtc_poll_s=1.0)
    trigger = live.Trigger(on_new_dtc=True)
    result = logger.run(duration_s=12.0, trigger=trigger, session_name="sess")

    assert any(c.trigger_kind == "dtc" for c in result.captures)
    assert ("P0301", "Cylinder 1 Misfire Detected") in result.dtcs
    # The DTC capture records the engine conditions at the trigger instant.
    dtc_cap = next(c for c in result.captures if c.trigger_kind == "dtc")
    assert dtc_cap.conditions and "Engine RPM" in dtc_cap.conditions
    # Conditions are embedded in the capture file as a comment header.
    text = open(dtc_cap.file, encoding="utf-8").read()
    assert "Conditions at trigger:" in text


def test_clean_vin_normalizes_messy_values():
    assert live._clean_vin("WAUZZZ8K9BA123456") == "WAUZZZ8K9BA123456"
    assert live._clean_vin(bytearray(b"WAUZZZ8K9BA123456")) == "WAUZZZ8K9BA123456"
    assert live._clean_vin(b"WAUZZZ8K9BA123456") == "WAUZZZ8K9BA123456"
    assert live._clean_vin(["WAUZZZ8K9BA123456"]) == "WAUZZZ8K9BA123456"
    assert live._clean_vin("WAUZ ZZ8K9-BA123456\x00") == "WAUZZZ8K9BA123456"
    assert live._clean_vin("") is None


def test_read_dtcs_surfaces_codes(tmp_path):
    conn = FakeOBD(dtc_at=-1)  # report immediately
    dtcs = live.read_dtcs(conn)
    assert dtcs == [("P0301", "Cylinder 1 Misfire Detected")]


def test_read_dtcs_detailed_falls_back_to_stored():
    # A connection without get_dtcs_detailed (like FakeOBD) tags everything stored.
    conn = FakeOBD(dtc_at=-1)
    detailed = live.read_dtcs_detailed(conn)
    assert detailed == [("P0301", "Cylinder 1 Misfire Detected", "stored")]


def test_pyobd_splits_stored_and_pending():
    # mode 03 (GET_DTC) -> stored; mode 07 (GET_CURRENT_DTC) -> pending.
    class _Resp:
        def __init__(self, value):
            self.value = value

        def is_null(self):
            return not self.value

    class _Cmd:
        def __init__(self, name):
            self.name = name

    class _Cmds:
        GET_DTC = _Cmd("GET_DTC")
        GET_CURRENT_DTC = _Cmd("GET_CURRENT_DTC")

    class _Conn:
        def query(self, cmd, force=False):
            if cmd.name == "GET_DTC":
                return _Resp([("P0420", "Catalyst")])
            return _Resp([("P0171", "System Too Lean")])

        def close(self):
            pass

    class _Obd:
        commands = _Cmds()

        class OBD:
            @staticmethod
            def query(conn, cmd, force=False):
                return conn.query(cmd, force=force)

    conn = live.PyOBDConnection(conn=_Conn(), obd_module=_Obd, is_async=False)
    detailed = conn.get_dtcs_detailed()
    assert ("P0420", "Catalyst", "stored") in detailed
    assert ("P0171", "System Too Lean", "pending") in detailed


def test_snapshot_returns_supported_values(tmp_path):
    conn = FakeOBD()
    snap = live.snapshot(conn)
    assert "Engine RPM" in snap
    assert "Boost (derived)" in snap
    assert snap["Barometric Pressure"] == 100.0


def test_prettify_names():
    assert live._prettify("FUEL_LEVEL") == "Fuel Level"
    assert live._prettify("O2_B1S1") == "O2 B1S1"
    assert live._prettify("CONTROL_MODULE_VOLTAGE") == "Control Module Voltage"


def test_build_channels_include_all_offers_extra_pids():
    supported = set(live.DEFAULT_CHANNELS_BY_CMD) | {"FUEL_LEVEL", "CONTROL_MODULE_VOLTAGE", "O2_B1S1"}
    curated = live.build_channels(supported)
    full = live.build_channels(supported, include_all=True)
    assert len(full) > len(curated)
    names = {c.name for c in full}
    assert "Fuel Level" in names and "O2 B1S1" in names
    assert any(c.name == "Engine RPM" for c in full)  # curated still present
    fl = next(c for c in full if c.name == "Fuel Level")
    assert fl.command_name == "FUEL_LEVEL" and fl.unit == "%"


def test_build_channels_selected_filter_with_all():
    supported = set(live.DEFAULT_CHANNELS_BY_CMD) | {"FUEL_LEVEL"}
    sel = live.build_channels(supported, selected=["FUEL_LEVEL"], include_all=True)
    assert [c.name for c in sel] == ["Fuel Level"]
