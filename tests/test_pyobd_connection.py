"""Regression tests for PyOBDConnection DTC read/clear (the force=True bug).

Uses an injected fake python-OBD connection + module, so no hardware or the
real ``obd`` package is required.
"""

from __future__ import annotations

from vcds_obd import live


class _Resp:
    def __init__(self, value=None, null=False):
        self.value = value
        self._null = null

    def is_null(self):
        return self._null


class _Cmd:
    def __init__(self, name):
        self.name = name


class _Commands:
    GET_DTC = _Cmd("GET_DTC")
    GET_CURRENT_DTC = _Cmd("GET_CURRENT_DTC")
    CLEAR_DTC = _Cmd("CLEAR_DTC")
    RPM = _Cmd("RPM")
    VIN = _Cmd("VIN")


class _FakeObd:
    commands = _Commands()

    class OBD:
        @staticmethod
        def query(conn, cmd, force=False):
            return conn.query(cmd, force=force)


class _FakeConn:
    def __init__(self, stored=None, pending=None):
        self.calls = []
        self.cleared = False
        self.supported_commands = {_Commands.RPM}
        self._stored = stored if stored is not None else [("P0299", "Turbo/Super Underboost")]
        self._pending = pending if pending is not None else [("P0171", "System Too Lean")]

    def query(self, cmd, force=False):
        self.calls.append((cmd.name, force))
        if cmd.name == "GET_DTC":
            return _Resp(list(self._stored))
        if cmd.name == "GET_CURRENT_DTC":
            return _Resp(list(self._pending))
        if cmd.name == "CLEAR_DTC":
            self.cleared = True
            return _Resp("OK")
        if cmd.name == "RPM":
            return _Resp(1500.0)
        if cmd.name == "VIN":
            return _Resp("WAUZZZ8K9BA123456")
        return _Resp(None, null=True)

    def status(self):
        return "Car Connected"

    def close(self):
        pass


class _FakeAsyncConn(_FakeConn):
    def __init__(self):
        super().__init__()
        self.watched = []
        self.started = self.stopped = self.unwatched = 0

    def watch(self, cmd):
        self.watched.append(cmd.name)

    def unwatch_all(self):
        self.unwatched += 1
        self.watched = []

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def _conn(fake=None):
    return live.PyOBDConnection(conn=fake or _FakeConn(), obd_module=_FakeObd, is_async=False)


def test_rewatch_async_replaces_watchlist():
    fake = _FakeAsyncConn()
    c = live.PyOBDConnection(conn=fake, obd_module=_FakeObd, is_async=True)
    assert c.is_async
    c.rewatch(["RPM"])
    assert fake.watched == ["RPM"]
    assert fake.started >= 1 and fake.stopped >= 1 and fake.unwatched >= 1


def test_identify_gathers_vehicle_info():
    info = _conn().identify()
    assert info["vin"] == "WAUZZZ8K9BA123456"
    assert info["supported_count"] == 1          # only RPM in the fake
    assert set(info) >= {"vin", "calibration_ids", "protocol", "supported_count"}


def test_rewatch_blocking_is_noop():
    fake = _FakeConn()
    live.PyOBDConnection(conn=fake, obd_module=_FakeObd, is_async=False).rewatch(["RPM"])
    assert not hasattr(fake, "watched")  # blocking connection untouched


def test_get_dtcs_forces_command_and_merges_pending():
    c = _conn()
    dtcs = c.get_dtcs()
    assert ("P0299", "Turbo/Super Underboost") in dtcs
    assert ("P0171", "System Too Lean") in dtcs
    # The original bug: GET_DTC was queried WITHOUT force=True and returned nothing.
    assert ("GET_DTC", True) in c._conn.calls
    assert ("GET_CURRENT_DTC", True) in c._conn.calls


def test_get_dtcs_deduplicates():
    fake = _FakeConn(stored=[("P0299", "Underboost")], pending=[("P0299", "Underboost")])
    dtcs = _conn(fake).get_dtcs()
    assert dtcs == [("P0299", "Underboost")]


def test_get_dtcs_empty():
    fake = _FakeConn(stored=[], pending=[])
    assert _conn(fake).get_dtcs() == []


def test_clear_dtcs_uses_force():
    c = _conn()
    assert c.clear_dtcs() is True
    assert c._conn.cleared
    assert ("CLEAR_DTC", True) in c._conn.calls


def test_query_value_reads_blocking():
    assert _conn().query_value("RPM") == 1500.0


def test_connect_default_is_blocking():
    # The default must be a blocking connection so one-shot DTC reads work.
    import inspect

    sig = inspect.signature(live.connect)
    assert sig.parameters["prefer_async"].default is False
