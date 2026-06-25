"""Tests for log comparison."""

from __future__ import annotations

from vcds_core import compare, parse


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return parse.parse_measuring_log(str(p))


def test_compare_union_and_deltas(tmp_path):
    a = _write(tmp_path, "a.csv",
               "TIME,Boost,Coolant Temp\ns,mbar,°C\n0,1000,80\n1,1100,82\n2,1050,84\n")
    b = _write(tmp_path, "b.csv",
               "TIME,Boost,Intake Air Temp\ns,mbar,°C\n0,1200,30\n1,1300,31\n2,1250,32\n")
    comp = compare.compare_logs(a, b, a_name="A", b_name="B")
    by = {c.name: c for c in comp.channels}

    assert set(by) == {"Boost", "Coolant Temp", "Intake Air Temp"}
    boost = by["Boost"]
    assert boost.in_a and boost.in_b
    assert boost.d_mean is not None and boost.d_mean > 0  # B richer boost
    assert boost.d_max is not None and boost.d_max > 0

    assert by["Coolant Temp"].in_a and not by["Coolant Temp"].in_b
    assert by["Coolant Temp"].d_mean is None
    assert by["Intake Air Temp"].in_b and not by["Intake Air Temp"].in_a


def test_compare_preserves_a_order(tmp_path):
    a = _write(tmp_path, "a.csv", "TIME,Z,Y,X\ns,a,b,c\n0,1,2,3\n1,1,2,3\n2,1,2,3\n")
    b = _write(tmp_path, "b.csv", "TIME,Z\ns,a\n0,1\n1,1\n2,1\n")
    comp = compare.compare_logs(a, b)
    assert [c.name for c in comp.channels] == ["Z", "Y", "X"]
