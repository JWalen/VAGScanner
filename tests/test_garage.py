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


def test_chat_roundtrip(tmp_path):
    path = str(tmp_path / "g.json")
    vehicles = [garage.Vehicle(vin="V1")]
    assert garage.set_chat(vehicles, "V1", [{"role": "user", "content": "hi"}])
    garage.save_garage(path, vehicles)
    loaded = garage.load_garage(path)
    assert garage.get_chat(loaded, "V1") == [{"role": "user", "content": "hi"}]
    assert not garage.set_chat(loaded, "NOPE", [])


def test_maintenance_due_and_overdue():
    vehicles = [garage.Vehicle(vin="V1", odometer=53000)]
    garage.add_maintenance(vehicles, "V1",
                           {"type": "Oil change", "mileage": 45000, "interval_miles": 7500})
    garage.add_maintenance(vehicles, "V1",
                           {"type": "Brake fluid", "mileage": 40000, "interval_miles": 20000})
    due = {d["type"]: d for d in garage.maintenance_due(vehicles[0])}
    # oil: next due 52,500 -> overdue at 53,000
    assert due["Oil change"]["overdue"] and due["Oil change"]["remaining"] < 0
    # brake fluid: next due 60,000 -> 7,000 to go
    assert not due["Brake fluid"]["overdue"]
    assert abs(due["Brake fluid"]["remaining"] - 7000) < 1


def test_fuel_stats():
    vehicles = [garage.Vehicle(vin="V1")]
    garage.add_fuel(vehicles, "V1", {"mileage": 1000, "volume": 10, "cost": 40})
    garage.add_fuel(vehicles, "V1", {"mileage": 1300, "volume": 12, "cost": 48})
    st = garage.fuel_stats(vehicles[0])
    assert st["fills"] == 2 and st["total_cost"] == 88
    assert st["distance"] == 300
    # 12 vol over 300 dist -> 4.0 vol/100
    assert abs(st["vol_per_100"] - 4.0) < 1e-6
    assert vehicles[0].odometer == 1300  # odometer advanced


def test_log_folder_name():
    v = garage.Vehicle(vin="WAUZZZ8K9BA123456", make="Audi", year=2011)
    assert garage.log_folder_name(v) == "2011_Audi_123456"
    nick = garage.Vehicle(vin="ABC999", nickname="Track Car")
    assert garage.log_folder_name(nick).startswith("Track_Car")
    bare = garage.Vehicle(vin="")
    assert garage.log_folder_name(bare)  # never empty


def test_identify_fields_merge():
    vehicles = [garage.Vehicle(vin="V1")]
    garage.add_or_update(vehicles, garage.Vehicle(
        vin="V1", calibration_ids=["CAL1"], ecu_name="ECM-1", fuel_type="Gasoline"))
    v = vehicles[0]
    assert v.calibration_ids == ["CAL1"] and v.ecu_name == "ECM-1" and v.fuel_type == "Gasoline"


def test_chat_preserved_on_merge():
    vehicles = [garage.Vehicle(vin="V1", chat=[{"role": "user", "content": "keep"}])]
    garage.add_or_update(vehicles, garage.Vehicle(vin="V1", make="Audi"))
    assert garage.get_chat(vehicles, "V1") == [{"role": "user", "content": "keep"}]
