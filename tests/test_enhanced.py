"""Tests for the experimental mode-22 enhanced-PID framework (no hardware)."""

from __future__ import annotations

from vcds_obd import enhanced, live


class FakeSerial:
    def __init__(self, responses):
        self.responses = responses
        self._last = ""

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self._last = data.decode("ascii").strip().upper()

    def read_until(self, expected=b">"):
        return (self.responses.get(self._last, "NO DATA") + " \r\r>").encode("ascii")

    def close(self):
        pass


def test_evaluate_formula():
    pid = enhanced.EnhancedPid("Trans Temp", "°C", "ford", "1E1C", "(a*256 + b)/16 - 40")
    # bytes 0x05,0x00 -> 1280/16 - 40 = 40
    assert enhanced.evaluate(pid, [0x05, 0x00]) == 40.0


def test_query_enhanced_with_fake_conn():
    pid = enhanced.EnhancedPid("Oil", "°C", "ford", "1446", "a - 40")

    class Conn:
        def query_raw(self, req):
            assert req == "221446"
            return [0x6E]  # 110 -> 70°C

    assert enhanced.query_enhanced(Conn(), pid) == 70.0


def test_raw_query_raw_strips_service_and_ident():
    fake = FakeSerial({"221E1C": "62 1E 1C 05 00"})
    conn = live.RawELM327Connection(serial_obj=fake)
    assert conn.query_raw("221E1C") == [0x05, 0x00]


def test_library_roundtrip(tmp_path):
    path = str(tmp_path / "lib.json")
    pids = [enhanced.EnhancedPid("A", "u", "ford", "1234", "a*2")]
    enhanced.save_library(path, pids)
    loaded = enhanced.load_library(path)
    assert loaded[0].name == "A" and loaded[0].did == "1234" and loaded[0].formula == "a*2"


def test_default_library_when_missing(tmp_path):
    loaded = enhanced.load_library(str(tmp_path / "nope.json"))
    assert loaded and all(isinstance(p, enhanced.EnhancedPid) for p in loaded)
    assert enhanced.for_brand(loaded, "ford")  # bundled examples are Ford


def test_safe_formula_rejects_code():
    pid = enhanced.EnhancedPid("X", "u", "ford", "1234", "__import__('os')")
    assert enhanced.evaluate(pid, [1, 2]) is None  # bad formula -> None, no crash
