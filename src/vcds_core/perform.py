"""Performance analysis: acceleration runs, WOT pulls and power/torque estimates.

Dependency-free. Estimates are physics-based and clearly approximate (they depend
on vehicle mass and drag assumptions), intended as a relative/tuning aid rather
than a calibrated dyno figure.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import List, Optional

from .parse import MeasuringLog

_G = 9.80665  # m/s^2
_RHO = 1.20  # air density kg/m^3
_W_PER_HP = 745.7


@dataclass
class AccelRun:
    from_speed: float
    to_speed: float
    unit: str
    elapsed_s: float
    start_time: float
    end_time: float


@dataclass
class Pull:
    start_time: float
    end_time: float
    duration_s: float
    rpm_start: Optional[float]
    rpm_end: Optional[float]
    peak_speed: Optional[float]
    peak_boost: Optional[float]


@dataclass
class PowerEstimate:
    peak_hp: float
    peak_hp_time: float
    peak_torque_nm: Optional[float]
    peak_torque_rpm: Optional[float]
    mass_kg: float
    method: str = "acceleration (crank, est.)"


def _find(log: MeasuringLog, *needles):
    for n in needles:
        ch = log.channel(n)
        if ch is not None:
            return ch
    return None


def _speed_channel(log: MeasuringLog):
    return _find(log, "Vehicle Speed", "Speed")


def _to_ms(value: float, unit: str) -> float:
    u = (unit or "").lower()
    if "mph" in u:
        return value * 0.44704
    if "m/s" in u:
        return value
    return value / 3.6  # default km/h


def find_acceleration_runs(log: MeasuringLog, from_speed: float, to_speed: float) -> List[AccelRun]:
    """Find runs where speed climbs from ``from_speed`` to ``to_speed`` (channel units).

    Returns runs sorted fastest-first.
    """
    ch = _speed_channel(log)
    if ch is None:
        return []
    s = log.raw_series.get(ch.name)
    if not s:
        return []
    t, v = s["time"], s["value"]
    n = len(v)
    drop_floor = from_speed - max(1.0, (to_speed - from_speed) * 0.1)
    runs: List[AccelRun] = []
    i = 0
    while i < n - 1:
        if v[i] <= from_speed <= v[i + 1]:
            t_from = _crossing_time(t, v, i, from_speed)
            j = i + 1
            ok = True
            while j < n and v[j] < to_speed:
                if v[j] < drop_floor:
                    ok = False
                    break
                j += 1
            if ok and j < n:
                t_to = _crossing_time(t, v, j - 1, to_speed)
                runs.append(AccelRun(from_speed, to_speed, ch.unit, t_to - t_from, t_from, t_to))
                i = j
                continue
        i += 1
    runs.sort(key=lambda r: r.elapsed_s)
    return runs


def _crossing_time(t, v, i, target) -> float:
    """Linear-interpolate the time at which v crosses ``target`` between i and i+1."""
    if i + 1 >= len(v):
        return t[i]
    v0, v1 = v[i], v[i + 1]
    if v1 == v0:
        return t[i]
    frac = (target - v0) / (v1 - v0)
    frac = min(1.0, max(0.0, frac))
    return t[i] + frac * (t[i + 1] - t[i])


def detect_pulls(log: MeasuringLog, min_rpm_rise: float = 1500.0, min_duration: float = 1.5) -> List[Pull]:
    """Detect wide-open-throttle style pulls: sustained RPM climbs."""
    rpm = _find(log, "Engine RPM", "Engine Speed", "RPM")
    if rpm is None:
        return []
    s = log.raw_series.get(rpm.name)
    if not s:
        return []
    t, v = s["time"], s["value"]
    boost = _find(log, "Boost (derived)", "Boost", "Charge", "Intake MAP", "MAP")
    speed = _speed_channel(log)
    bser = log.raw_series.get(boost.name) if boost else None
    sser = log.raw_series.get(speed.name) if speed else None

    pulls: List[Pull] = []
    n = len(v)
    i = 0
    while i < n - 1:
        if v[i + 1] > v[i]:  # rising
            j = i
            while j < n - 1 and v[j + 1] >= v[j] - 50:  # allow small dips
                j += 1
            rise = v[j] - v[i]
            dur = t[j] - t[i]
            if rise >= min_rpm_rise and dur >= min_duration:
                pulls.append(Pull(
                    start_time=t[i], end_time=t[j], duration_s=dur,
                    rpm_start=v[i], rpm_end=v[j],
                    peak_speed=_window_max(sser, t[i], t[j]) if sser else None,
                    peak_boost=_window_max(bser, t[i], t[j]) if bser else None,
                ))
            i = j + 1
        else:
            i += 1
    return pulls


def _window_max(series, t0, t1) -> Optional[float]:
    if not series:
        return None
    vals = [series["value"][k] for k in range(len(series["time"]))
            if t0 <= series["time"][k] <= t1 and series["value"][k] is not None]
    return max(vals) if vals else None


def estimate_power(
    log: MeasuringLog,
    mass_kg: float,
    cd: float = 0.32,
    frontal_area: float = 2.2,
    crr: float = 0.015,
    drivetrain_loss: float = 0.15,
) -> Optional[PowerEstimate]:
    """Estimate peak crank power/torque from the speed trace during acceleration.

    P_wheel = (m·a + ½·ρ·Cd·A·v² + Crr·m·g)·v, taken only while accelerating;
    crank power = P_wheel / (1 − drivetrain_loss). Rough — depends on mass/drag.
    """
    ch = _speed_channel(log)
    if ch is None or mass_kg <= 0:
        return None
    s = log.raw_series.get(ch.name)
    if not s or len(s["value"]) < 3:
        return None
    t = s["time"]
    v = [_to_ms(x, ch.unit) for x in s["value"]]

    rpm_ch = _find(log, "Engine RPM", "Engine Speed", "RPM")
    rpm_ser = log.raw_series.get(rpm_ch.name) if rpm_ch else None

    peak_w = 0.0
    peak_t = 0.0
    for i in range(1, len(v)):
        dt = t[i] - t[i - 1]
        if dt <= 0:
            continue
        a = (v[i] - v[i - 1]) / dt
        if a <= 0:
            continue
        speed = v[i]
        f_inertia = mass_kg * a
        f_drag = 0.5 * _RHO * cd * frontal_area * speed * speed
        f_roll = crr * mass_kg * _G
        p_wheel = (f_inertia + f_drag + f_roll) * speed
        if p_wheel > peak_w:
            peak_w = p_wheel
            peak_t = t[i]
    if peak_w <= 0:
        return None

    crank_w = peak_w / (1.0 - drivetrain_loss)
    peak_hp = crank_w / _W_PER_HP

    peak_torque = peak_rpm = None
    if rpm_ser:
        rpm_at = _value_at(rpm_ser, peak_t)
        if rpm_at and rpm_at > 0:
            peak_torque = crank_w / (2 * 3.141592653589793 * rpm_at / 60.0)
            peak_rpm = rpm_at

    return PowerEstimate(
        peak_hp=peak_hp, peak_hp_time=peak_t,
        peak_torque_nm=peak_torque, peak_torque_rpm=peak_rpm, mass_kg=mass_kg,
    )


def _value_at(series, when: float) -> Optional[float]:
    t = series["time"]
    if not t:
        return None
    v = series["value"]
    j = bisect.bisect_left(t, when)  # O(log n); time axis is monotonic
    best = bd = None
    for k in (j - 1, j):
        if 0 <= k < len(t):
            dt = abs(t[k] - when)
            if bd is None or dt < bd:
                bd, best = dt, v[k]
    return best


@dataclass
class DynoPoint:
    rpm: float
    hp: float
    torque_nm: float


@dataclass
class DynoCurve:
    points: List[DynoPoint]
    peak_hp: float
    peak_hp_rpm: Optional[float]
    peak_torque_nm: float
    peak_torque_rpm: Optional[float]
    mass_kg: float


def dyno_curve(
    log: MeasuringLog,
    mass_kg: float,
    cd: float = 0.32,
    frontal_area: float = 2.2,
    crr: float = 0.015,
    drivetrain_loss: float = 0.15,
    bin_rpm: float = 250.0,
) -> Optional[DynoCurve]:
    """Estimate a crank power/torque-vs-RPM curve from a WOT pull.

    Same physics as :func:`estimate_power`, but kept per-RPM-bin (max HP per bin)
    to form an envelope curve. Approximate — depends on mass/drag assumptions.
    """
    ch = _speed_channel(log)
    rpm_ch = _find(log, "Engine RPM", "Engine Speed", "RPM")
    if ch is None or rpm_ch is None or mass_kg <= 0:
        return None
    s = log.raw_series.get(ch.name)
    rpm_ser = log.raw_series.get(rpm_ch.name)
    if not s or not rpm_ser or len(s["value"]) < 3:
        return None
    t = s["time"]
    v = [_to_ms(x, ch.unit) for x in s["value"]]

    bins: dict = {}
    for i in range(1, len(v)):
        dt = t[i] - t[i - 1]
        if dt <= 0 or v[i] is None or v[i - 1] is None:
            continue
        a = (v[i] - v[i - 1]) / dt
        if a <= 0:
            continue
        speed = v[i]
        p_wheel = (mass_kg * a + 0.5 * _RHO * cd * frontal_area * speed * speed
                   + crr * mass_kg * _G) * speed
        crank_w = p_wheel / (1.0 - drivetrain_loss)
        rpm = _value_at(rpm_ser, t[i])
        if not rpm or rpm <= 0:
            continue
        hp = crank_w / _W_PER_HP
        torque = crank_w / (2 * 3.141592653589793 * rpm / 60.0)
        b = round(rpm / bin_rpm) * bin_rpm
        cur = bins.get(b)
        if cur is None or hp > cur.hp:
            bins[b] = DynoPoint(rpm=rpm, hp=hp, torque_nm=torque)
    if not bins:
        return None
    points = [bins[b] for b in sorted(bins)]
    pk_hp = max(points, key=lambda p: p.hp)
    pk_tq = max(points, key=lambda p: p.torque_nm)
    return DynoCurve(points=points, peak_hp=pk_hp.hp, peak_hp_rpm=pk_hp.rpm,
                     peak_torque_nm=pk_tq.torque_nm, peak_torque_rpm=pk_tq.rpm, mass_kg=mass_kg)


@dataclass
class DragResult:
    zero_to_label: str
    zero_to_s: Optional[float]
    quarter_mile_s: Optional[float]
    trap_speed: Optional[float]
    speed_unit: str


def dragstrip(log: MeasuringLog) -> Optional[DragResult]:
    """0–60 mph (or 0–100 km/h) and quarter-mile time + trap speed from a standing run."""
    ch = _speed_channel(log)
    if ch is None:
        return None
    unit = (ch.unit or "km/h")
    imperial = "mph" in unit.lower()
    top = 60 if imperial else 100
    label = "0–60 mph" if imperial else "0–100 km/h"
    runs = find_acceleration_runs(log, 0, top)
    if not runs:
        return None
    run = min(runs, key=lambda r: r.elapsed_s)
    zero_to = run.elapsed_s

    s = log.raw_series.get(ch.name)
    t = s["time"]
    v = [_to_ms(x, ch.unit) for x in s["value"]]
    target = 402.336  # metres in a quarter mile
    dist = 0.0
    qtr = trap = None
    started = False
    for i in range(1, len(v)):
        if t[i] < run.start_time or v[i] is None or v[i - 1] is None:
            continue
        started = True
        dt = t[i] - t[i - 1]
        if dt <= 0:
            continue
        seg = (v[i] + v[i - 1]) / 2.0 * dt
        if dist + seg >= target:
            frac = (target - dist) / seg if seg else 0.0
            qtr = (t[i - 1] + frac * dt) - run.start_time
            trap_ms = v[i]
            trap = trap_ms * 2.236936 if imperial else trap_ms * 3.6
            break
        dist += seg
    if not started:
        return None
    return DragResult(zero_to_label=label, zero_to_s=zero_to,
                      quarter_mile_s=qtr, trap_speed=trap, speed_unit=unit)


def standard_accel_runs(log: MeasuringLog) -> List[AccelRun]:
    """0–100 km/h and 100–200 km/h (or 0–60 / 60–130 mph) for the log's units."""
    ch = _speed_channel(log)
    unit = (ch.unit if ch else "km/h").lower()
    runs: List[AccelRun] = []
    if "mph" in unit:
        runs += find_acceleration_runs(log, 0, 60)
        runs += find_acceleration_runs(log, 60, 130)
    else:
        runs += find_acceleration_runs(log, 0, 100)
        runs += find_acceleration_runs(log, 100, 200)
    return runs
