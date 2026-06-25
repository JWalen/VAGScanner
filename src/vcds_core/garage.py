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
