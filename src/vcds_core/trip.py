"""Trip / fuel-economy and battery analysis from a measuring log.

Dependency-free. Fuel economy is estimated from a Fuel Rate channel when present,
otherwise from MAF (mass air flow ÷ stoichiometric AFR ÷ fuel density) — a rough
figure, fine for relative comparison.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Optional

from .parse import MeasuringLog

_AFR = 14.7
_FUEL_DENSITY_G_PER_L = 745.0  # gasoline
_MPG_US_PER_L100 = 235.215


@dataclass
class FuelEconomy:
    l_per_100km: Optional[float]
    mpg_us: Optional[float]
    distance_km: float
    fuel_l: float
    duration_s: float
    idle_fraction: float
    avg_speed_kmh: float
    source: str  # "fuel_rate" or "maf"


@dataclass
class Battery:
    min_v: float
    max_v: float
    avg_v: float
    cranking_v: float
    charging_v: Optional[float]


def _at(series, when):
    """Value at the sample whose time is nearest `when` (O(log n); times sorted)."""
    times, values = series["time"], series["value"]
    if not times:
        return None
    j = bisect.bisect_left(times, when)
    best = bd = None
    for k in (j - 1, j):
        if 0 <= k < len(times) and values[k] is not None:
            d = abs(times[k] - when)
            if bd is None or d < bd:
                bd, best = d, values[k]
    return best


def _find(log: MeasuringLog, *names):
    for n in names:
        ch = log.channel(n)
        if ch is not None:
            return ch
    return None


def fuel_economy(log: MeasuringLog) -> Optional[FuelEconomy]:
    speed = _find(log, "Vehicle Speed", "Speed")
    if speed is None:
        return None
    s = log.raw_series.get(speed.name)
    if not s or len(s["value"]) < 2:
        return None
    t, v = s["time"], s["value"]

    # Convert speed to km/h based on the channel's unit (VCDS logs km/h; generic
    # OBD/Torque logs are often mph). Without this, mph logs were ~61% off.
    unit = (speed.unit or "").lower()
    if "mph" in unit:
        to_kmh = 1.609344
    elif "m/s" in unit:
        to_kmh = 3.6
    else:
        to_kmh = 1.0

    rate = _find(log, "Fuel Rate")
    maf = _find(log, "MAF")
    rser = log.raw_series.get(rate.name) if rate else None
    mser = log.raw_series.get(maf.name) if maf else None
    source = "fuel_rate" if rser else ("maf" if mser else None)
    if source is None:
        return None

    fuel_l = dist_km = idle = total = 0.0
    n = len(v)
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        if dt <= 0 or dt > 5:
            continue
        if v[i] is None:
            continue
        spd = v[i] * to_kmh  # km/h
        total += dt
        dist_km += spd / 3600.0 * dt
        if spd < 2:
            idle += dt
        # Resolve fuel-rate/MAF at the speed sample's TIME — the fuel and speed
        # channels drop None rows independently, so their list indices don't line
        # up in general.
        if rser is not None:
            r = _at(rser, t[i])
            if r is not None:
                fuel_l += r / 3600.0 * dt  # L/h -> L
        else:
            m = _at(mser, t[i])
            if m is not None:
                fuel_l += (m / _AFR / _FUEL_DENSITY_G_PER_L) * dt

    if total <= 0:
        return None
    l100 = (fuel_l / dist_km * 100.0) if dist_km > 0 else None
    mpg = (_MPG_US_PER_L100 / l100) if l100 else None
    return FuelEconomy(
        l_per_100km=l100, mpg_us=mpg, distance_km=dist_km, fuel_l=fuel_l,
        duration_s=total, idle_fraction=idle / total,
        avg_speed_kmh=dist_km / (total / 3600.0) if total else 0.0, source=source,
    )


def battery_analysis(log: MeasuringLog) -> Optional[Battery]:
    ch = _find(log, "Control Module Voltage", "Battery Voltage", "Battery", "Module Voltage")
    if ch is None:
        return None
    s = log.raw_series.get(ch.name)
    if not s:
        return None
    vals = [x for x in s["value"] if x is not None]
    if not vals:
        return None
    charging = [x for x in vals if x > 13.0]
    return Battery(
        min_v=min(vals), max_v=max(vals), avg_v=sum(vals) / len(vals),
        cranking_v=min(vals),
        charging_v=(sum(charging) / len(charging)) if charging else None,
    )
