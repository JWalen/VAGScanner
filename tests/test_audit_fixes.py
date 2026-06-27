"""Regression tests for issues found in the multi-agent audit."""

from __future__ import annotations

from vcds_core import parse, trip
from vcds_core.diagnose import diagnose


def test_parse_bridges_blank_separator_rows(tmp_path):
    # A blank row mid-capture must NOT truncate the rest of the log.
    rows = ["TIME,Engine RPM", "s,/min"]
    for k in range(3):
        rows.append(f"{k * 0.5:.1f},{1000 + k * 100}")
    rows.append("")  # blank separator (VCDS emits these)
    for k in range(3, 6):
        rows.append(f"{k * 0.5:.1f},{1000 + k * 100}")
    p = tmp_path / "blank.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    rpm = log.channel("Engine RPM")
    assert rpm.max >= 1500          # all 6 samples kept, not truncated at 1200
    assert log.sample_count == 6


def test_fuel_economy_is_unit_aware(tmp_path):
    # 60 mph constant for 10 s = 0.268 km; the km/h assumption gave 0.167 km.
    rows = ["TIME,Vehicle Speed,Fuel Rate", "s,mph,L/h"]
    for k in range(11):
        rows.append(f"{k:.1f},60,6")
    p = tmp_path / "mph.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    econ = trip.fuel_economy(log)
    assert econ is not None
    assert abs(econ.distance_km - 0.268) < 0.03
    assert abs(econ.avg_speed_kmh - 96.6) < 3      # 60 mph -> ~96.6 km/h


def test_threshold_hit_survives_malformed_rule():
    from vcds_obd.live import Trigger, _threshold_hit
    trig = Trigger(thresholds=[{"channel": "Boost", "op": ">"}])  # missing 'value'
    assert _threshold_hit(trig, {"Boost": 1500.0}) is None         # no crash
    trig2 = Trigger(thresholds=[{"channel": "Boost", "op": ">", "value": "x"}])
    assert _threshold_hit(trig2, {"Boost": 1500.0}) is None         # non-numeric ok


def test_divergence_does_not_pair_unrelated_channels(tmp_path):
    # "Specified torque" vs "Actual intake pressure" must NOT trip a boost finding.
    rows = ["TIME,Specified Torque,Actual Coolant Temp", "s,Nm,°C"]
    for k in range(6):
        rows.append(f"{k * 0.5:.1f},{300 - k * 40},90")
    p = tmp_path / "div.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    report = diagnose(log=log, profile="vag")
    assert not any(f.title == "Actual value falls short of target" for f in report.findings)


def test_divergence_still_fires_on_matching_boost(tmp_path):
    rows = ["TIME,Specified Boost,Actual Boost", "s,kPa,kPa"]
    for k in range(6):
        rows.append(f"{k * 0.5:.1f},180,{180 - k * 25}")  # actual falls well short
    p = tmp_path / "boost.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    report = diagnose(log=log, profile="vag")
    assert any(f.title == "Actual value falls short of target" for f in report.findings)


def test_find_events_survives_malformed_rule(tmp_path):
    rows = ["TIME,Engine RPM", "s,/min"]
    for k in range(5):
        rows.append(f"{k * 0.5:.1f},{1000 + k * 300}")
    p = tmp_path / "ev.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    # A rule missing 'value' (or non-numeric) must not crash find_events.
    assert parse.find_events(log, [{"channel": "RPM", "op": ">"}]) == []
    assert parse.find_events(log, [{"channel": "RPM", "op": ">", "value": "x"}]) == []
    good = parse.find_events(log, [{"channel": "RPM", "op": ">", "value": 1500}])
    assert isinstance(good, list)


def test_fuel_economy_handles_misaligned_fuel_series(tmp_path):
    # Speed has a gap (blank fuel cell) so speed & fuel rows differ in index;
    # time-based lookup must still integrate fuel correctly (no IndexError/zero).
    rows = ["TIME,Vehicle Speed,Fuel Rate", "s,km/h,L/h"]
    for k in range(11):
        fr = "" if k == 5 else "6"        # one missing fuel sample
        rows.append(f"{k:.1f},100,{fr}")
    p = tmp_path / "gap.csv"
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(p))
    econ = trip.fuel_economy(log)
    assert econ is not None and econ.fuel_l > 0 and econ.l_per_100km is not None


def test_openai_token_param_for_o_series():
    from vcds_gui.ai import _openai_token_param
    assert "max_completion_tokens" in _openai_token_param("o4-mini", 100)
    assert "max_completion_tokens" in _openai_token_param("o1", 100)
    assert "max_tokens" in _openai_token_param("gpt-4o", 100)


class _Resp:
    def __init__(self, null):
        self._null = null

    def is_null(self):
        return self._null


class _Cmd:
    def __init__(self, name):
        self.name = name


class _Cmds:
    CLEAR_DTC = _Cmd("CLEAR_DTC")


class _Obd:
    commands = _Cmds()

    class OBD:
        @staticmethod
        def query(conn, cmd, force=False):
            return conn.query(cmd, force=force)


class _Conn:
    def __init__(self, null):
        self._null = null

    def query(self, cmd, force=False):
        return _Resp(self._null)

    def close(self):
        pass


def test_clear_dtcs_null_reply_is_failure():
    from vcds_obd import live
    assert live.PyOBDConnection(conn=_Conn(True), obd_module=_Obd, is_async=False).clear_dtcs() is False
    assert live.PyOBDConnection(conn=_Conn(False), obd_module=_Obd, is_async=False).clear_dtcs() is True
