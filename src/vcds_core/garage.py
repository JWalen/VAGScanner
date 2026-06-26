"""A simple multi-vehicle "garage" — vehicles keyed by VIN, with session history.

Persisted as JSON. Dependency-free; the GUI provides the file path (typically in
the logs folder).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class Vehicle:
    vin: str
    nickname: str = ""
    make: Optional[str] = None
    year: Optional[int] = None
    brand_profile: str = "generic"
    mass_kg: Optional[float] = None
    notes: str = ""
    sessions: List[str] = field(default_factory=list)
    chat: List[dict] = field(default_factory=list)  # per-vehicle AI conversation
    odometer: Optional[float] = None
    maintenance: List[dict] = field(default_factory=list)  # service records
    fuel: List[dict] = field(default_factory=list)         # fill-up log
    calibration_ids: List[str] = field(default_factory=list)
    ecu_name: Optional[str] = None
    fuel_type: Optional[str] = None

    @property
    def label(self) -> str:
        if self.nickname:
            return self.nickname
        bits = [str(b) for b in (self.year, self.make) if b]
        return " ".join(bits) or self.vin


def load_garage(path: str) -> List[Vehicle]:
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return [Vehicle(**d) for d in data]
    except Exception:  # noqa: BLE001
        return []


def save_garage(path: str, vehicles: List[Vehicle]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(v) for v in vehicles], fh, indent=2)


def find(vehicles: List[Vehicle], vin: str) -> Optional[Vehicle]:
    vin = (vin or "").strip().upper()
    for v in vehicles:
        if v.vin.upper() == vin:
            return v
    return None


def add_or_update(vehicles: List[Vehicle], vehicle: Vehicle) -> Vehicle:
    """Insert ``vehicle``, or merge into an existing record with the same VIN."""
    existing = find(vehicles, vehicle.vin)
    if existing is None:
        vehicles.append(vehicle)
        return vehicle
    # fill in any newly-known fields without clobbering user edits
    existing.make = existing.make or vehicle.make
    existing.year = existing.year or vehicle.year
    existing.ecu_name = existing.ecu_name or vehicle.ecu_name
    existing.fuel_type = existing.fuel_type or vehicle.fuel_type
    if vehicle.calibration_ids and not existing.calibration_ids:
        existing.calibration_ids = list(vehicle.calibration_ids)
    if vehicle.brand_profile and vehicle.brand_profile != "generic":
        existing.brand_profile = vehicle.brand_profile
    return existing


def add_session(vehicles: List[Vehicle], vin: str, filename: str) -> bool:
    v = find(vehicles, vin)
    if v is None:
        return False
    if filename not in v.sessions:
        v.sessions.append(filename)
    return True


def _safe_dirname(name: str) -> str:
    out = "".join(ch if (ch.isalnum() or ch in " -_") else "_" for ch in name)
    return "_".join(out.split()).strip("._") or "vehicle"


def log_folder_name(vehicle: "Vehicle") -> str:
    """A filesystem-safe per-vehicle log folder name derived from the VIN.

    e.g. a 2011 Audi (VIN …BA123456) -> ``2011_Audi_123456``; a nickname wins.
    """
    if vehicle.nickname:
        base = vehicle.nickname
    else:
        bits = [str(b) for b in (vehicle.year, vehicle.make) if b]
        base = " ".join(bits) or "vehicle"
    tail = (vehicle.vin or "").strip()[-6:]
    return _safe_dirname(f"{base} {tail}" if tail else base)


def add_maintenance(vehicles: List[Vehicle], vin: str, record: dict) -> bool:
    """Append a service record: {type, mileage, date, interval_miles, cost, notes}."""
    v = find(vehicles, vin)
    if v is None:
        return False
    v.maintenance.append(record)
    if record.get("mileage") is not None:
        v.odometer = max(v.odometer or 0, float(record["mileage"]))
    return True


def add_fuel(vehicles: List[Vehicle], vin: str, entry: dict) -> bool:
    """Append a fill-up: {mileage, volume, cost, date}."""
    v = find(vehicles, vin)
    if v is None:
        return False
    v.fuel.append(entry)
    if entry.get("mileage") is not None:
        v.odometer = max(v.odometer or 0, float(entry["mileage"]))
    return True


def maintenance_due(vehicle: "Vehicle", current_odo: Optional[float] = None) -> List[dict]:
    """For each service record with an interval, how far until it's due again."""
    odo = current_odo if current_odo is not None else vehicle.odometer
    out = []
    for r in vehicle.maintenance:
        interval = r.get("interval_miles")
        at = r.get("mileage")
        if not interval or at is None:
            continue
        next_due = float(at) + float(interval)
        remaining = (next_due - odo) if odo is not None else None
        out.append({"type": r.get("type", "service"), "next_due": next_due,
                    "remaining": remaining,
                    "overdue": (remaining is not None and remaining < 0)})
    return out


def fuel_stats(vehicle: "Vehicle") -> Optional[dict]:
    """Aggregate the fill-up log into distance / volume / cost (unit-neutral)."""
    fills = [f for f in vehicle.fuel if f.get("mileage") is not None]
    if not vehicle.fuel:
        return None
    total_cost = sum(float(f.get("cost") or 0) for f in vehicle.fuel)
    total_vol = sum(float(f.get("volume") or 0) for f in vehicle.fuel)
    stats = {"fills": len(vehicle.fuel), "total_cost": total_cost, "total_volume": total_vol,
             "distance": None, "vol_per_100": None, "cost_per_dist": None}
    if len(fills) >= 2:
        miles = sorted(float(f["mileage"]) for f in fills)
        dist = miles[-1] - miles[0]
        # fuel burned over that distance = all volume except the first (baseline) fill
        ordered = sorted(fills, key=lambda f: float(f["mileage"]))
        vol_since_first = sum(float(f.get("volume") or 0) for f in ordered[1:])
        if dist > 0:
            stats["distance"] = dist
            if vol_since_first > 0:
                stats["vol_per_100"] = vol_since_first / dist * 100.0
            stats["cost_per_dist"] = total_cost / dist
    return stats


def get_chat(vehicles: List[Vehicle], vin: str) -> List[dict]:
    v = find(vehicles, vin)
    return list(v.chat) if v else []


def set_chat(vehicles: List[Vehicle], vin: str, chat: List[dict]) -> bool:
    v = find(vehicles, vin)
    if v is None:
        return False
    v.chat = list(chat)
    return True
