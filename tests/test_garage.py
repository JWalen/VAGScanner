"""Tests for the multi-vehicle garage."""

from __future__ import annotations

from vcds_core import garage


def test_add_find_and_roundtrip(tmp_path):
    path = str(tmp_path / "garage.json")
    vehicles = []
    garage.add_or_update(vehicles, garage.Vehicle(
        vin="WAUZZZ8K9BA123456", make="Audi", year=2011, brand_profile="vag"))
    garage.save_garage(path, vehicles)

    loaded = garage.load_garage(path)
    assert len(loaded) == 1
    v = garage.find(loaded, "wauzzz8k9ba123456")  # case-insensitive
    assert v is not None and v.make == "Audi" and v.brand_profile == "vag"
    assert v.label == "2011 Audi"


def test_add_or_update_merges(tmp_path):
    vehicles = [garage.Vehicle(vin="V1", nickname="Daily", brand_profile="generic")]
    garage.add_or_update(vehicles, garage.Vehicle(vin="V1", make="Ford", year=2015, brand_profile="ford"))
    assert len(vehicles) == 1
    v = vehicles[0]
    assert v.nickname == "Daily"      # user edit preserved
    assert v.make == "Ford" and v.brand_profile == "ford"  # new info filled in


def test_add_session(tmp_path):
    vehicles = [garage.Vehicle(vin="V1")]
    assert garage.add_session(vehicles, "V1", "OBD_1.CSV")
    assert garage.add_session(vehicles, "V1", "OBD_1.CSV")  # dedup
    assert vehicles[0].sessions == ["OBD_1.CSV"]
    assert not garage.add_session(vehicles, "NOPE", "x.CSV")


def test_load_missing_returns_empty(tmp_path):
    assert garage.load_garage(str(tmp_path / "none.json")) == []
