"""Tests for the timing chain / belt stretch check."""

from __future__ import annotations

from vcds_core import knowledge, parse
from vcds_core.diagnose import diagnose
from vcds_core.parse import AutoScan, Fault, Module


def _scan_with(code, detail=""):
    return AutoScan(file="x", vin="WAUZZZ", mileage=None, modules=[
        Module(address="01", name="Engine", faults=[
            Fault(code=code, description="", status_detail=detail)])])


def test_correlation_code_in_knowledge():
    k = knowledge.lookup("P0016")
    assert k.known and "Correlation" in k.description
    assert any("timing chain" in c.lower() for c in k.causes)


def test_timing_finding_from_correlation_dtc():
    report = diagnose(scan=_scan_with("P0016"), profile="generic")
    titles = [f.title for f in report.findings]
    assert "Possible timing chain / belt stretch" in titles
    f = next(f for f in report.findings if f.title == "Possible timing chain / belt stretch")
    assert f.severity == "high"
    assert any("tensioner" in c.lower() or "chain" in c.lower() for c in f.causes)


def test_timing_finding_when_code_is_in_status_detail():
    # VCDS lists the standard code in the status detail
    report = diagnose(scan=_scan_with("16394", detail="P0016 - Correlation Bank1"),
                      profile="vag")
    assert any(f.title == "Possible timing chain / belt stretch" for f in report.findings)


def test_timing_finding_includes_brand_note():
    # Ford 3.5 EcoBoost note is appended for the ford profile, not for generic
    ford = diagnose(scan=_scan_with("P0017"), profile="ford")
    f = next(f for f in ford.findings if f.title == "Possible timing chain / belt stretch")
    assert "EcoBoost" in f.detail
    gen = diagnose(scan=_scan_with("P0017"), profile="generic")
    fg = next(f for f in gen.findings if f.title == "Possible timing chain / belt stretch")
    assert "EcoBoost" not in fg.detail


def test_no_timing_finding_without_correlation_code():
    report = diagnose(scan=_scan_with("P0420"), profile="generic")
    assert not any("timing chain" in f.title.lower() for f in report.findings)


def test_camshaft_deviation_log_heuristic(tmp_path):
    path = tmp_path / "cam.csv"
    # a camshaft-deviation channel that climbs past the 6 degree threshold
    rows = ["TIME,Camshaft Deviation Bank 1", "s,°"]
    for k in range(6):
        rows.append(f"{k * 0.5:.1f},{k * 1.8:.1f}")  # 0 -> 9.0 deg
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(path))
    report = diagnose(log=log, profile="generic")
    assert any(f.title == "Camshaft timing deviation high" for f in report.findings)


def test_camshaft_spec_actual_pair(tmp_path):
    # VAG-style logging: specified vs actual camshaft timing diverge (chain stretch)
    path = tmp_path / "campair.csv"
    rows = ["TIME,Camshaft Timing Specified Bank 1,Camshaft Timing Actual Bank 1", "s,°,°"]
    for k in range(6):
        rows.append(f"{k * 0.5:.1f},10.0,{10.0 - k * 1.6:.1f}")  # diverges to 8.0 deg
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log = parse.parse_measuring_log(str(path))
    report = diagnose(log=log, profile="vag")
    titles = [f.title for f in report.findings]
    assert "Camshaft timing deviation high" in titles
    # and it must NOT be mislabeled as a boost shortfall
    assert "Actual value falls short of target" not in titles
    f = next(f for f in report.findings if f.title == "Camshaft timing deviation high")
    assert any("tensioner" in c.lower() for c in f.causes)
